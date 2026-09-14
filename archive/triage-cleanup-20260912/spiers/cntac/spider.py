#!/usr/bin/env python3
"""
CNTAC (China Textile Industry Federation) Spider
中国纺织工业联合会数据爬虫

Target: http://www.cntac.org.cn/
Data: Textile production data, export statistics, market analysis, industry reports
Focus: Textile output, yarn/fabric production, export volumes, industry indices

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts textile industry indices and export data
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
logger = logging.getLogger("cntac")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "cntac.db"
JSON_PRODUCTION_PATH = OUTPUT_DIR / "cntac_production.json"
JSON_EXPORT_PATH = OUTPUT_DIR / "cntac_export.json"
JSON_NEWS_PATH = OUTPUT_DIR / "cntac_news.json"

TARGET_URLS = {
    "production": [
        "http://www.cntac.org.cn/comm/market/data/",
        "http://www.cntac.org.cn/comm/market/produce/",
    ],
    "export": [
        "http://www.cntac.org.cn/comm/market/export/",
        "http://www.cntac.org.cn/comm/market/trade/",
    ],
    "news": [
        "http://www.cntac.org.cn/comm/news/industry/",
        "http://www.cntac.org.cn/comm/news/policy/",
    ],
    "reports": [
        "http://www.cntac.org.cn/comm/report/analysis/",
        "http://www.cntac.org.cn/comm/report/monthly/",
    ],
}

TEXTILE_CATEGORIES = {
    "纱": "yarn",
    "布": "fabric",
    "棉花": "cotton",
    "化纤": "chemical_fiber",
    "丝绸": "silk",
    "麻": "hemp",
    "服装": "garment",
    "家纺": "home_textile",
    "产业用纺织品": "industrial_textile",
}


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS production_statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            category TEXT,
            category_cn TEXT,
            production_volume REAL,
            production_unit TEXT,
            growth_rate REAL,
            growth_unit TEXT DEFAULT '%',
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, category, region, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS export_statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            category TEXT,
            category_cn TEXT,
            export_volume REAL,
            export_unit TEXT,
            export_value REAL,
            value_unit TEXT DEFAULT '亿美元',
            growth_rate REAL,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, category, source_url)
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


def identify_textile_category(text: str) -> dict | None:
    for cn_name, en_name in TEXTILE_CATEGORIES.items():
        if cn_name in text:
            return {"category": en_name, "category_cn": cn_name}
    return None


def extract_period(text: str) -> str:
    patterns = [
        r"(\d{4}年\d{1,2}月)",
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

        if not any(kw in header_text for kw in ["产量", "生产", "统计", "万吨", "亿米"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            cell_text = " ".join(cells)
            cat_info = identify_textile_category(cell_text)
            if not cat_info:
                continue

            item = {
                "period": extract_period(cell_text),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(cat_info)

            numbers = re.findall(r"[\d,]+\.?\d*", " ".join(cells).replace(",", ""))
            if numbers:
                try:
                    item["production_volume"] = float(numbers[0])
                    item["production_unit"] = "万吨" if "万吨" in cell_text else ("亿米" if "亿米" in cell_text else "万吨")
                except ValueError:
                    continue

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate"] = float(pct[0])
                        item["growth_unit"] = "%"

            if item.get("production_volume"):
                items.append(item)

    return items


def extract_export_stats(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["出口", "贸易", "出口额", "出口量"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            cell_text = " ".join(cells)
            cat_info = identify_textile_category(cell_text)
            if not cat_info:
                continue

            item = {
                "period": extract_period(cell_text),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(cat_info)

            numbers = re.findall(r"[\d,]+\.?\d*", " ".join(cells).replace(",", ""))
            if len(numbers) >= 1:
                try:
                    item["export_volume"] = float(numbers[0])
                    item["export_unit"] = "万吨" if "万吨" in cell_text else "亿米"
                except ValueError:
                    continue
            if len(numbers) >= 2:
                try:
                    item["export_value"] = float(numbers[1])
                    item["value_unit"] = "亿美元"
                except ValueError:
                    pass

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate"] = float(pct[0])

            if item.get("export_volume") or item.get("export_value"):
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
        if "政策" in title or "policy" in url.lower():
            category = "policy"
        elif "出口" in title:
            category = "export"
        elif "市场" in title:
            category = "market"
        elif "报告" in title or "分析" in title:
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
                (period, category, category_cn, production_volume, production_unit,
                 growth_rate, growth_unit, region, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("category"), item.get("category_cn"),
                    item.get("production_volume"), item.get("production_unit"),
                    item.get("growth_rate"), item.get("growth_unit"),
                    item.get("region"), item.get("source_url"), item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_export_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO export_statistics
                (period, category, category_cn, export_volume, export_unit,
                 export_value, value_unit, growth_rate, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("category"), item.get("category_cn"),
                    item.get("export_volume"), item.get("export_unit"),
                    item.get("export_value"), item.get("value_unit"),
                    item.get("growth_rate"), item.get("source_url"), item.get("scraped_at"),
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
                    item.get("date"), item.get("title"), item.get("content"),
                    item.get("category"), item.get("source_url"), item.get("scraped_at"),
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


def get_cntac_data(
    include_production: bool = True,
    include_export: bool = True,
    include_news: bool = True,
) -> dict:
    """Fetch data from China Textile Industry Federation.

    Args:
        include_production: Whether to fetch production statistics.
        include_export: Whether to fetch export statistics.
        include_news: Whether to fetch industry news.

    Returns:
        Dict with keys: 'production', 'export', 'news'.
    """
    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "production": [],
        "export": [],
        "news": [],
    }

    logger.info("Starting CNTAC spider")

    if include_production:
        for url in TARGET_URLS["production"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_production_stats(page["html"], url)
                if items:
                    result["production"].extend(items)
                    logger.info("  Extracted %d production records", len(items))
            time.sleep(2)

    if include_export:
        for url in TARGET_URLS["export"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_export_stats(page["html"], url)
                if items:
                    result["export"].extend(items)
                    logger.info("  Extracted %d export records", len(items))
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

    if result["export"]:
        n = save_export_to_sqlite(result["export"], conn)
        save_to_json(result["export"], JSON_EXPORT_PATH)
        logger.info("Saved %d export records: SQLite=%d, JSON=%s", len(result["export"]), n, JSON_EXPORT_PATH)

    if result["news"]:
        n = save_news_to_sqlite(result["news"], conn)
        save_to_json(result["news"], JSON_NEWS_PATH)
        logger.info("Saved %d news articles: SQLite=%d, JSON=%s", len(result["news"]), n, JSON_NEWS_PATH)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_cntac_data()
    print(f"\n{'=' * 70}")
    print(f"Total production records: {len(results['production'])}")
    print(f"Total export records: {len(results['export'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON (production): {JSON_PRODUCTION_PATH}")
    print(f"JSON (export): {JSON_EXPORT_PATH}")
    print(f"JSON (news): {JSON_NEWS_PATH}")
    print(f"{'=' * 70}")

    if results["production"]:
        print(f"\nProduction Statistics ({len(results['production'])} records):")
        for stat in results["production"][:10]:
            print(f"  {stat.get('period', 'N/A'):>12s} | {stat.get('category_cn', 'N/A'):>8s} | "
                  f"{stat.get('production_volume', 'N/A'):>12} {stat.get('production_unit', '')}")

    if results["export"]:
        print(f"\nExport Statistics ({len(results['export'])} records):")
        for stat in results["export"][:10]:
            print(f"  {stat.get('period', 'N/A'):>12s} | {stat.get('category_cn', 'N/A'):>8s} | "
                  f"{stat.get('export_volume', 'N/A'):>12} {stat.get('export_unit', '')}")

    if results["news"]:
        print(f"\nRecent News ({len(results['news'])} articles):")
        for article in results["news"][:5]:
            print(f"  [{article.get('date', 'N/A')}] {article.get('title', 'N/A')[:60]}")
