#!/usr/bin/env python3
"""
CAAM (China Association of Automobile Manufacturers) Spider
中国汽车工业协会数据爬虫

Target: http://www.caam.org.cn/
Data: Automobile production statistics, sales data, export/import data, industry trends
Focus: Vehicle production/sales by category, brand sales rankings, NEV data

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts production/sales statistics tables, brand rankings
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scrapling import Fetcher, Selector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("caam")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "caam.db"
JSON_PRODUCTION_PATH = OUTPUT_DIR / "caam_production.json"
JSON_SALES_PATH = OUTPUT_DIR / "caam_sales.json"
JSON_NEWS_PATH = OUTPUT_DIR / "caam_news.json"

TARGET_URLS = {
    "production": [
        "http://www.caam.org.cn/chn/4/cate_4/con_0/list_1.html",
        "http://www.caam.org.cn/chn/4/cate_4/con_1/list_1.html",
    ],
    "sales": [
        "http://www.caam.org.cn/chn/4/cate_5/con_0/list_1.html",
        "http://www.caam.org.cn/chn/4/cate_5/con_1/list_1.html",
    ],
    "news": [
        "http://www.caam.org.cn/chn/4/cate_1/con_0/list_1.html",
        "http://www.caam.org.cn/chn/4/cate_2/con_0/list_1.html",
        "http://www.caam.org.cn/chn/4/cate_3/con_0/list_1.html",
    ],
    "statistics": [
        "http://www.caam.org.cn/chn/4/cate_6/con_0/list_1.html",
    ],
}

VEHICLE_TYPES = {
    "乘用车": "passenger_vehicle",
    "商用车": "commercial_vehicle",
    "新能源汽车": "nev",
    "轿车": "sedan",
    "SUV": "suv",
    "MPV": "mpv",
    "客车": "bus",
    "货车": "truck",
    "交叉型乘用车": "cross_passenger",
}


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS production_statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            vehicle_type TEXT,
            vehicle_type_cn TEXT,
            production_volume REAL,
            production_unit TEXT DEFAULT '辆',
            growth_rate_yoy REAL,
            growth_rate_mom REAL,
            brand TEXT,
            category TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, vehicle_type, brand, category, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sales_statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            vehicle_type TEXT,
            vehicle_type_cn TEXT,
            sales_volume REAL,
            sales_unit TEXT DEFAULT '辆',
            growth_rate_yoy REAL,
            growth_rate_mom REAL,
            brand TEXT,
            ranking INTEGER,
            category TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, vehicle_type, brand, category, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS industry_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            title TEXT,
            content TEXT,
            category TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def fetch_page(url: str, fetcher: Fetcher) -> dict | None:
    try:
        logger.info("Fetching: %s", url)
        response = fetcher.get(url, timeout=30)
        if response.status != 200:
            logger.warning("HTTP %d for %s", response.status, url)
            return None
        return {"url": url, "html": response.html, "status": response.status}
    except Exception as e:
        logger.error("Fetch failed for %s: %s", url, e)
        return None


def identify_vehicle_type(text: str) -> dict | None:
    for cn_name, en_name in VEHICLE_TYPES.items():
        if cn_name in text:
            return {"vehicle_type": en_name, "vehicle_type_cn": cn_name}
    return None


def extract_period(text: str) -> str:
    patterns = [
        r"(\d{4}年\d{1,2}月)",
        r"(\d{4}年第\d+季度)",
        r"(\d{4}-\d{2})",
        r"(\d{4}年)",
        r"(\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return datetime.now().strftime("%Y-%m")


def extract_production_stats(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["产量", "生产", "统计", "万辆", "汽车"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            cell_text = " ".join(cells)
            vehicle_info = identify_vehicle_type(cell_text)
            if not vehicle_info:
                continue

            item = {
                "period": extract_period(cell_text),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(vehicle_info)

            numbers = re.findall(r"[\d,]+\.?\d*", " ".join(cells).replace(",", ""))
            if numbers:
                try:
                    item["production_volume"] = float(numbers[0])
                    item["production_unit"] = "万辆" if "万" in cell_text else "辆"
                except ValueError:
                    continue

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate_yoy"] = float(pct[0])

            if item.get("production_volume"):
                items.append(item)

    return items


def extract_sales_stats(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["销量", "销售", "统计", "万辆", "汽车", "排名"]):
            continue

        for idx, row in enumerate(rows[1:], start=1):
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            cell_text = " ".join(cells)
            vehicle_info = identify_vehicle_type(cell_text)
            if not vehicle_info:
                continue

            item = {
                "period": extract_period(cell_text),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(vehicle_info)

            numbers = re.findall(r"[\d,]+\.?\d*", " ".join(cells).replace(",", ""))
            if numbers:
                try:
                    item["sales_volume"] = float(numbers[0])
                    item["sales_unit"] = "万辆" if "万" in cell_text else "辆"
                except ValueError:
                    continue

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate_yoy"] = float(pct[0])

            if "排名" in header_text or "品牌" in header_text:
                item["ranking"] = idx

            if item.get("sales_volume"):
                items.append(item)

    return items


def extract_news(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for article in sel.css("article, .news-item, .article-item, .list-item, li"):
        title_elem = article.css("h1, h2, h3, h4, .title, a")
        if not title_elem:
            continue

        title = title_elem[0].text.strip() if title_elem else ""
        if not title or len(title) < 5:
            continue

        content_elem = article.css("p, .content, .summary, .excerpt, .desc")
        content = content_elem[0].text.strip() if content_elem else ""

        date_elem = article.css("time, .date, .publish-date, span")
        date = date_elem[0].text.strip() if date_elem else ""
        date = extract_period(date)

        category = "news"
        if "产销" in title or "生产" in title:
            category = "production"
        elif "销售" in title:
            category = "sales"
        elif "出口" in title or "进口" in title:
            category = "trade"
        elif "新能源" in title:
            category = "nev"
        elif "分析" in title or "预测" in title:
            category = "analysis"

        items.append({
            "date": date,
            "title": title,
            "content": content[:500] if content else "",
            "category": category,
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def save_production_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO production_statistics
                (period, vehicle_type, vehicle_type_cn, production_volume, production_unit,
                 growth_rate_yoy, growth_rate_mom, brand, category, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"),
                    item.get("vehicle_type"),
                    item.get("vehicle_type_cn"),
                    item.get("production_volume"),
                    item.get("production_unit"),
                    item.get("growth_rate_yoy"),
                    item.get("growth_rate_mom"),
                    item.get("brand"),
                    item.get("category"),
                    item.get("source_url"),
                    item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_sales_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO sales_statistics
                (period, vehicle_type, vehicle_type_cn, sales_volume, sales_unit,
                 growth_rate_yoy, growth_rate_mom, brand, ranking, category, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"),
                    item.get("vehicle_type"),
                    item.get("vehicle_type_cn"),
                    item.get("sales_volume"),
                    item.get("sales_unit"),
                    item.get("growth_rate_yoy"),
                    item.get("growth_rate_mom"),
                    item.get("brand"),
                    item.get("ranking"),
                    item.get("category"),
                    item.get("source_url"),
                    item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_news_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO industry_news
                (date, title, content, category, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"),
                    item.get("title"),
                    item.get("content"),
                    item.get("category"),
                    item.get("source_url"),
                    item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_to_json(items: list[dict], json_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def get_caam_data(
    include_production: bool = True,
    include_sales: bool = True,
    include_news: bool = True,
) -> dict:
    """Fetch data from China Association of Automobile Manufacturers.

    Args:
        include_production: Whether to fetch production statistics.
        include_sales: Whether to fetch sales statistics.
        include_news: Whether to fetch industry news.

    Returns:
        Dict with keys: 'production', 'sales', 'news'.
    """
    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "production": [],
        "sales": [],
        "news": [],
    }

    logger.info("Starting CAAM spider")

    if include_production:
        for url in TARGET_URLS["production"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_production_stats(page["html"], url)
                if items:
                    result["production"].extend(items)
                    logger.info("  Extracted %d production records", len(items))
            time.sleep(2)

    if include_sales:
        for url in TARGET_URLS["sales"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_sales_stats(page["html"], url)
                if items:
                    result["sales"].extend(items)
                    logger.info("  Extracted %d sales records", len(items))
            time.sleep(2)

    if include_news:
        for url in TARGET_URLS["news"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_news(page["html"], url)
                if items:
                    result["news"].extend(items)
                    logger.info("  Extracted %d news articles", len(items))
            time.sleep(2)

    if result["production"]:
        n = save_production_to_sqlite(result["production"], conn)
        save_to_json(result["production"], JSON_PRODUCTION_PATH)
        logger.info("Saved %d production records: SQLite=%d, JSON=%s", len(result["production"]), n, JSON_PRODUCTION_PATH)

    if result["sales"]:
        n = save_sales_to_sqlite(result["sales"], conn)
        save_to_json(result["sales"], JSON_SALES_PATH)
        logger.info("Saved %d sales records: SQLite=%d, JSON=%s", len(result["sales"]), n, JSON_SALES_PATH)

    if result["news"]:
        n = save_news_to_sqlite(result["news"], conn)
        save_to_json(result["news"], JSON_NEWS_PATH)
        logger.info("Saved %d news articles: SQLite=%d, JSON=%s", len(result["news"]), n, JSON_NEWS_PATH)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_caam_data()
    print(f"\n{'=' * 70}")
    print(f"Total production records: {len(results['production'])}")
    print(f"Total sales records: {len(results['sales'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON (production): {JSON_PRODUCTION_PATH}")
    print(f"JSON (sales): {JSON_SALES_PATH}")
    print(f"JSON (news): {JSON_NEWS_PATH}")
    print(f"{'=' * 70}")

    if results["production"]:
        print(f"\nProduction Statistics ({len(results['production'])} records):")
        for stat in results["production"][:10]:
            print(f"  {stat.get('period', 'N/A'):>12s} | {stat.get('vehicle_type_cn', 'N/A'):>8s} | "
                  f"{stat.get('production_volume', 'N/A'):>12} {stat.get('production_unit', '')}")

    if results["sales"]:
        print(f"\nSales Statistics ({len(results['sales'])} records):")
        for stat in results["sales"][:10]:
            print(f"  {stat.get('period', 'N/A'):>12s} | {stat.get('vehicle_type_cn', 'N/A'):>8s} | "
                  f"{stat.get('sales_volume', 'N/A'):>12} {stat.get('sales_unit', '')}")

    if results["news"]:
        print(f"\nRecent News ({len(results['news'])} articles):")
        for article in results["news"][:5]:
            print(f"  [{article.get('date', 'N/A')}] {article.get('title', 'N/A')[:60]}")
