#!/usr/bin/env python3
"""
China Catering Industry Association Spider
中国餐饮行业协会数据爬虫

Target: http://www.chinaca.org/
Data: Restaurant industry data, food service statistics, market trends, consumer behavior
Focus: Catering revenue, restaurant count, delivery data, consumer spending

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts catering revenue and consumer behavior data
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
logger = logging.getLogger("catering")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "catering.db"
JSON_STATS_PATH = OUTPUT_DIR / "catering_statistics.json"
JSON_NEWS_PATH = OUTPUT_DIR / "catering_news.json"

TARGET_URLS = {
    "statistics": [
        "http://www.chinaca.org/data/industry/",
        "http://www.chinaca.org/data/statistics/",
    ],
    "news": [
        "http://www.chinaca.org/news/industry/",
        "http://www.chinaca.org/news/policy/",
    ],
    "reports": [
        "http://www.chinaca.org/report/analysis/",
    ],
}

CATERING_CATEGORIES = {
    "正餐": "full_service",
    "快餐": "fast_food",
    "火锅": "hotpot",
    "烧烤": "barbecue",
    "饮品": "beverages",
    "烘焙": "bakery",
    "团餐": "group_catering",
    "外卖": "delivery",
    "预制菜": "prepared_food",
}


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS catering_statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            category TEXT,
            category_cn TEXT,
            indicator_name TEXT,
            value REAL,
            unit TEXT,
            growth_rate REAL,
            growth_unit TEXT DEFAULT '%',
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, category, indicator_name, region, source_url)
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


def identify_catering_category(text: str) -> dict | None:
    for cn_name, en_name in CATERING_CATEGORIES.items():
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


def extract_statistics(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["餐饮", "收入", "营业", "门店", "增长", "亿元", "万亿"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            cell_text = " ".join(cells)
            cat_info = identify_catering_category(cell_text)
            if not cat_info:
                cat_info = {"category": "general", "category_cn": "综合餐饮"}

            item = {
                "period": extract_period(cell_text),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(cat_info)

            numbers = re.findall(r"[\d,]+\.?\d*", " ".join(cells).replace(",", ""))
            if numbers:
                try:
                    item["value"] = float(numbers[0])
                    if "万亿" in cell_text:
                        item["value"] = item["value"] * 10000
                        item["unit"] = "亿元"
                    elif "亿" in cell_text:
                        item["unit"] = "亿元"
                    elif "万" in cell_text:
                        item["unit"] = "万家"
                    else:
                        item["unit"] = "亿元"
                except ValueError:
                    continue

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate"] = float(pct[0])
                        item["growth_unit"] = "%"

            if "收入" in header_text or "营收" in cell_text:
                item["indicator_name"] = "revenue"
            elif "门店" in header_text or "店" in cell_text:
                item["indicator_name"] = "store_count"
            elif "客流" in header_text or "消费" in cell_text:
                item["indicator_name"] = "consumer_spending"
            else:
                item["indicator_name"] = "general"

            if item.get("value"):
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
        if "政策" in title:
            category = "policy"
        elif "报告" in title or "分析" in title:
            category = "analysis"
        elif "数据" in title:
            category = "data"
        elif "外卖" in title:
            category = "delivery"
        elif "预制菜" in title:
            category = "prepared_food"

        items.append({
            "date": date,
            "title": title,
            "content": content[:500] if content else "",
            "category": category,
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def save_stats_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO catering_statistics
                (period, category, category_cn, indicator_name, value, unit,
                 growth_rate, growth_unit, region, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("category"), item.get("category_cn"),
                    item.get("indicator_name"), item.get("value"), item.get("unit"),
                    item.get("growth_rate"), item.get("growth_unit"),
                    item.get("region"), item.get("source_url"), item.get("scraped_at"),
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


def get_catering_data(
    include_stats: bool = True,
    include_news: bool = True,
) -> dict:
    """Fetch data from China Catering Industry Association.

    Args:
        include_stats: Whether to fetch catering statistics.
        include_news: Whether to fetch industry news.

    Returns:
        Dict with keys: 'statistics', 'news'.
    """
    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "statistics": [],
        "news": [],
    }

    logger.info("Starting Catering spider")

    if include_stats:
        for url in TARGET_URLS["statistics"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_statistics(page["html"], url)
                if items:
                    result["statistics"].extend(items)
                    logger.info("  Extracted %d statistics records", len(items))
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

    if result["statistics"]:
        n = save_stats_to_sqlite(result["statistics"], conn)
        save_to_json(result["statistics"], JSON_STATS_PATH)
        logger.info("Saved %d statistics: SQLite=%d, JSON=%s", len(result["statistics"]), n, JSON_STATS_PATH)

    if result["news"]:
        n = save_news_to_sqlite(result["news"], conn)
        save_to_json(result["news"], JSON_NEWS_PATH)
        logger.info("Saved %d news articles: SQLite=%d, JSON=%s", len(result["news"]), n, JSON_NEWS_PATH)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_catering_data()
    print(f"\n{'=' * 70}")
    print(f"Total statistics records: {len(results['statistics'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON (stats): {JSON_STATS_PATH}")
    print(f"JSON (news): {JSON_NEWS_PATH}")
    print(f"{'=' * 70}")

    if results["statistics"]:
        print(f"\nCatering Statistics ({len(results['statistics'])} records):")
        for stat in results["statistics"][:10]:
            print(f"  {stat.get('period', 'N/A'):>12s} | {stat.get('category_cn', 'N/A'):>8s} | "
                  f"{stat.get('value', 'N/A'):>12} {stat.get('unit', '')}")

    if results["news"]:
        print(f"\nRecent News ({len(results['news'])} articles):")
        for article in results["news"][:5]:
            print(f"  [{article.get('date', 'N/A')}] {article.get('title', 'N/A')[:60]}")
