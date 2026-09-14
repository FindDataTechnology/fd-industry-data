#!/usr/bin/env python3
"""
China Real Estate Association Spider
中国房地产业协会数据爬虫

Target: http://www.fangchan.com/
Data: Real estate market data, property prices by city, construction statistics, market trends
Focus: Housing sales volume, price indices, land transactions, development investment

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts property price indices and market transaction data
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
logger = logging.getLogger("real-estate")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "real_estate.db"
JSON_PRICES_PATH = OUTPUT_DIR / "real_estate_prices.json"
JSON_MARKET_PATH = OUTPUT_DIR / "real_estate_market.json"
JSON_NEWS_PATH = OUTPUT_DIR / "real_estate_news.json"

TARGET_URLS = {
    "prices": [
        "http://www.fangchan.com/data/price-index/",
        "http://www.fangchan.com/data/city-price/",
    ],
    "market": [
        "http://www.fangchan.com/data/transaction/",
        "http://www.fangchan.com/data/investment/",
        "http://www.fangchan.com/data/construction/",
    ],
    "news": [
        "http://www.fangchan.com/news/industry/",
        "http://www.fangchan.com/news/policy/",
    ],
}

PROPERTY_TYPES = {
    "住宅": "residential",
    "商品房": "commercial_housing",
    "办公楼": "office",
    "商业营业用房": "commercial_premises",
    "别墅": "villa",
    "公寓": "apartment",
    "经济适用房": "affordable_housing",
    "限价房": "price_capped",
}

CITY_TIERS = {
    "一线": "tier_1",
    "二线": "tier_2",
    "三线": "tier_3",
    "北京": "beijing",
    "上海": "shanghai",
    "广州": "guangzhou",
    "深圳": "shenzhen",
}


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            city TEXT,
            city_tier TEXT,
            property_type TEXT,
            property_type_cn TEXT,
            price_index REAL,
            avg_price REAL,
            price_unit TEXT DEFAULT '元/平方米',
            growth_rate_mom REAL,
            growth_rate_yoy REAL,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, city, property_type, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            city TEXT,
            indicator_name TEXT,
            value REAL,
            unit TEXT,
            growth_rate REAL,
            growth_unit TEXT DEFAULT '%',
            property_type TEXT,
            category TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, city, indicator_name, property_type, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS industry_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            title TEXT,
            content TEXT,
            category TEXT,
            city TEXT,
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


def identify_property_type(text: str) -> dict | None:
    for cn_name, en_name in PROPERTY_TYPES.items():
        if cn_name in text:
            return {"property_type": en_name, "property_type_cn": cn_name}
    return None


def identify_city(text: str) -> str | None:
    for cn_name in CITY_TIERS:
        if cn_name in text:
            return cn_name
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


def extract_price_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["价格", "均价", "指数", "房价", "元/㎡", "元/平方米"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            cell_text = " ".join(cells)

            item = {
                "period": extract_period(cell_text),
                "city": identify_city(cell_text),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            prop_info = identify_property_type(cell_text)
            if prop_info:
                item.update(prop_info)

            numbers = re.findall(r"[\d,]+\.?\d*", " ".join(cells).replace(",", ""))
            if numbers:
                try:
                    item["avg_price"] = float(numbers[0])
                    item["price_unit"] = "元/平方米"
                except ValueError:
                    continue

            if len(numbers) >= 2:
                try:
                    item["price_index"] = float(numbers[1])
                except ValueError:
                    pass

            for cell in cells:
                if "环比" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate_mom"] = float(pct[0])
                elif "同比" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate_yoy"] = float(pct[0])

            if item.get("avg_price") or item.get("price_index"):
                items.append(item)

    return items


def extract_market_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["成交", "面积", "金额", "投资", "施工", "新开工", "竣工"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            cell_text = " ".join(cells)

            item = {
                "period": extract_period(cell_text),
                "city": identify_city(cell_text),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            prop_info = identify_property_type(cell_text)
            if prop_info:
                item.update(prop_info)

            numbers = re.findall(r"[\d,]+\.?\d*", " ".join(cells).replace(",", ""))
            if numbers:
                try:
                    item["value"] = float(numbers[0])
                    if "万" in cell_text:
                        item["unit"] = "万平方米" if "面积" in header_text else "万元"
                    elif "亿" in cell_text:
                        item["unit"] = "亿元"
                    else:
                        item["unit"] = "平方米"
                except ValueError:
                    continue

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate"] = float(pct[0])
                        item["growth_unit"] = "%"

            if "成交" in header_text:
                item["category"] = "transaction"
            elif "投资" in header_text:
                item["category"] = "investment"
            elif "施工" in header_text or "开工" in header_text or "竣工" in header_text:
                item["category"] = "construction"

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
        if "政策" in title or "policy" in url.lower():
            category = "policy"
        elif "市场" in title:
            category = "market"
        elif "土地" in title:
            category = "land"
        elif "价格" in title:
            category = "price"

        city = identify_city(title)

        items.append({
            "date": date,
            "title": title,
            "content": content[:500] if content else "",
            "category": category,
            "city": city,
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def save_prices_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO price_index
                (period, city, city_tier, property_type, property_type_cn, price_index,
                 avg_price, price_unit, growth_rate_mom, growth_rate_yoy, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("city"), item.get("city_tier"),
                    item.get("property_type"), item.get("property_type_cn"),
                    item.get("price_index"), item.get("avg_price"), item.get("price_unit"),
                    item.get("growth_rate_mom"), item.get("growth_rate_yoy"),
                    item.get("source_url"), item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_market_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO market_statistics
                (period, city, indicator_name, value, unit, growth_rate, growth_unit,
                 property_type, category, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("city"), item.get("indicator_name"),
                    item.get("value"), item.get("unit"), item.get("growth_rate"),
                    item.get("growth_unit"), item.get("property_type"),
                    item.get("category"), item.get("source_url"), item.get("scraped_at"),
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
                (date, title, content, category, city, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"), item.get("title"), item.get("content"),
                    item.get("category"), item.get("city"),
                    item.get("source_url"), item.get("scraped_at"),
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


def get_real_estate_data(
    include_prices: bool = True,
    include_market: bool = True,
    include_news: bool = True,
) -> dict:
    """Fetch data from China Real Estate Association.

    Args:
        include_prices: Whether to fetch property price data.
        include_market: Whether to fetch market transaction data.
        include_news: Whether to fetch industry news.

    Returns:
        Dict with keys: 'prices', 'market', 'news'.
    """
    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "prices": [],
        "market": [],
        "news": [],
    }

    logger.info("Starting Real Estate spider")

    if include_prices:
        for url in TARGET_URLS["prices"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_price_data(page["html"], url)
                if items:
                    result["prices"].extend(items)
                    logger.info("  Extracted %d price records", len(items))
            time.sleep(2)

    if include_market:
        for url in TARGET_URLS["market"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_market_data(page["html"], url)
                if items:
                    result["market"].extend(items)
                    logger.info("  Extracted %d market records", len(items))
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

    if result["prices"]:
        n = save_prices_to_sqlite(result["prices"], conn)
        save_to_json(result["prices"], JSON_PRICES_PATH)
        logger.info("Saved %d price records: SQLite=%d, JSON=%s", len(result["prices"]), n, JSON_PRICES_PATH)

    if result["market"]:
        n = save_market_to_sqlite(result["market"], conn)
        save_to_json(result["market"], JSON_MARKET_PATH)
        logger.info("Saved %d market records: SQLite=%d, JSON=%s", len(result["market"]), n, JSON_MARKET_PATH)

    if result["news"]:
        n = save_news_to_sqlite(result["news"], conn)
        save_to_json(result["news"], JSON_NEWS_PATH)
        logger.info("Saved %d news articles: SQLite=%d, JSON=%s", len(result["news"]), n, JSON_NEWS_PATH)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_real_estate_data()
    print(f"\n{'=' * 70}")
    print(f"Total price records: {len(results['prices'])}")
    print(f"Total market records: {len(results['market'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON (prices): {JSON_PRICES_PATH}")
    print(f"JSON (market): {JSON_MARKET_PATH}")
    print(f"JSON (news): {JSON_NEWS_PATH}")
    print(f"{'=' * 70}")

    if results["prices"]:
        print(f"\nPrice Data ({len(results['prices'])} records):")
        for stat in results["prices"][:10]:
            print(f"  {stat.get('period', 'N/A'):>12s} | {stat.get('city', 'N/A'):>6s} | "
                  f"{stat.get('avg_price', 'N/A'):>12} {stat.get('price_unit', '')}")

    if results["market"]:
        print(f"\nMarket Statistics ({len(results['market'])} records):")
        for stat in results["market"][:10]:
            print(f"  {stat.get('period', 'N/A'):>12s} | {stat.get('city', 'N/A'):>6s} | "
                  f"{stat.get('value', 'N/A'):>12} {stat.get('unit', '')}")

    if results["news"]:
        print(f"\nRecent News ({len(results['news'])} articles):")
        for article in results["news"][:5]:
            print(f"  [{article.get('date', 'N/A')}] {article.get('title', 'N/A')[:60]}")
