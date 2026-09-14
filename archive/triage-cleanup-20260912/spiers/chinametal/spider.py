#!/usr/bin/env python3
"""
China Metal Market Network Spider - 中国金属市场网数据爬虫

Target: https://www.chinametal.com.cn
Data: Metal pricing, market analysis, industry news, supply/demand data
Metals: Copper, Aluminum, Zinc, Lead, Nickel, Tin, plus steel and minor metals

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts both prices and market commentary
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
logger = logging.getLogger("chinametal")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "chinametal.db"
JSON_PRICES_PATH = OUTPUT_DIR / "chinametal_prices.json"
JSON_NEWS_PATH = OUTPUT_DIR / "chinametal_news.json"

METALS = {
    "copper": {"cn_name": "铜", "symbol": "CU"},
    "aluminum": {"cn_name": "铝", "symbol": "AL"},
    "zinc": {"cn_name": "锌", "symbol": "ZN"},
    "lead": {"cn_name": "铅", "symbol": "PB"},
    "nickel": {"cn_name": "镍", "symbol": "NI"},
    "tin": {"cn_name": "锡", "symbol": "SN"},
    "steel": {"cn_name": "钢", "symbol": "ST"},
}

TARGET_URLS = {
    "prices": [
        "https://www.chinametal.com.cn/price/copper",
        "https://www.chinametal.com.cn/price/aluminum",
        "https://www.chinametal.com.cn/price/zinc",
        "https://www.chinametal.com.cn/price/lead",
        "https://www.chinametal.com.cn/price/nickel",
        "https://www.chinametal.com.cn/price/tin",
    ],
    "news": [
        "https://www.chinametal.com.cn/news/market",
        "https://www.chinametal.com.cn/news/industry",
    ],
    "analysis": [
        "https://www.chinametal.com.cn/analysis/daily",
        "https://www.chinametal.com.cn/analysis/weekly",
    ],
}


def init_db():
    """Initialize SQLite database with metal market schema."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS metal_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            metal TEXT NOT NULL,
            metal_cn TEXT,
            symbol TEXT,
            price_low REAL,
            price_high REAL,
            price_avg REAL,
            price_change REAL,
            price_change_pct REAL,
            unit TEXT,
            currency TEXT DEFAULT 'CNY',
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, metal, region, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            title TEXT,
            content TEXT,
            category TEXT,
            metal TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            title TEXT,
            summary TEXT,
            content TEXT,
            metal TEXT,
            analyst TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def fetch_page(url: str, fetcher: Fetcher) -> dict | None:
    """Fetch a page with browser impersonation."""
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


def extract_price_table(html: str, url: str, metal_key: str) -> list[dict]:
    """Extract price data from HTML tables."""
    sel = Selector(html)
    items = []
    metal_info = METALS.get(metal_key, {})

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers).lower()

        if not any(keyword in header_text for keyword in ["价格", "price", "均价", "涨跌", "市场"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            item = {
                "date": extract_date(cells[0] if cells else ""),
                "metal": metal_key,
                "metal_cn": metal_info.get("cn_name", ""),
                "symbol": metal_info.get("symbol", ""),
                "unit": "元/吨",
                "currency": "CNY",
                "region": "China",
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            price_values = extract_prices(cells)
            item.update(price_values)

            if item.get("price_avg") or item.get("price_low"):
                items.append(item)

    return items


def extract_news(html: str, url: str) -> list[dict]:
    """Extract market news and analysis articles."""
    sel = Selector(html)
    items = []

    for article in sel.css("article, .news-item, .article-item, .post"):
        title_elem = article.css("h1, h2, h3, .title")
        if not title_elem:
            continue

        title = title_elem[0].text.strip() if title_elem else ""
        content_elem = article.css("p, .content, .summary, .excerpt")
        content = content_elem[0].text.strip() if content_elem else ""

        date_elem = article.css("time, .date, .publish-date")
        date = date_elem[0].text.strip() if date_elem else ""
        date = extract_date(date)

        category = "news"
        if "analysis" in url.lower() or "分析" in title:
            category = "analysis"
        elif "market" in url.lower() or "市场" in title:
            category = "market"

        metal = identify_metal_in_text(title + " " + content)

        items.append({
            "date": date,
            "title": title,
            "content": content[:500],
            "category": category,
            "metal": metal,
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def identify_metal_in_text(text: str) -> str:
    """Identify metal mentioned in text."""
    text_lower = text.lower()
    for metal_key, metal_info in METALS.items():
        if metal_key in text_lower or metal_info["cn_name"] in text:
            return metal_key
    return "general"


def extract_date(text: str) -> str:
    """Extract date from text."""
    patterns = [
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{4}年\d{1,2}月\d{1,2}日)",
        r"(\d{2}/\d{2}/\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return datetime.now().strftime("%Y-%m-%d")


def extract_prices(cells: list[str]) -> dict:
    """Extract price values from table cells."""
    prices = {}
    price_pattern = r"[\d,]+\.?\d*"

    for i, cell in enumerate(cells):
        cell_clean = cell.replace(",", "").replace("，", "")
        numbers = re.findall(price_pattern, cell_clean)

        if not numbers:
            continue

        try:
            value = float(numbers[0])
        except (ValueError, IndexError):
            continue

        if "低" in cell or "low" in cell.lower() or "最低" in cell:
            prices["price_low"] = value
        elif "高" in cell or "high" in cell.lower() or "最高" in cell:
            prices["price_high"] = value
        elif "均" in cell or "avg" in cell.lower() or "中间" in cell or "平均" in cell:
            prices["price_avg"] = value
        elif "涨跌" in cell or "变化" in cell or "涨" in cell or "跌" in cell:
            if "%" in cell:
                prices["price_change_pct"] = value
            else:
                prices["price_change"] = value
        elif i == 1 and "price_low" not in prices:
            prices["price_low"] = value
        elif i == 2 and "price_high" not in prices:
            prices["price_high"] = value
        elif i == 3 and "price_avg" not in prices:
            prices["price_avg"] = value

    if "price_avg" not in prices and "price_low" in prices and "price_high" in prices:
        prices["price_avg"] = (prices["price_low"] + prices["price_high"]) / 2

    return prices


def save_prices_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    """Save price items to SQLite database."""
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO metal_prices
                (date, metal, metal_cn, symbol, price_low, price_high, price_avg,
                 price_change, price_change_pct, unit, currency, region, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"),
                    item.get("metal"),
                    item.get("metal_cn"),
                    item.get("symbol"),
                    item.get("price_low"),
                    item.get("price_high"),
                    item.get("price_avg"),
                    item.get("price_change"),
                    item.get("price_change_pct"),
                    item.get("unit"),
                    item.get("currency"),
                    item.get("region"),
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
    """Save news items to SQLite database."""
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO market_news
                (date, title, content, category, metal, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"),
                    item.get("title"),
                    item.get("content"),
                    item.get("category"),
                    item.get("metal"),
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
    """Save items to JSON file."""
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def get_chinametal_data(
    metals: list[str] | None = None,
    include_news: bool = True,
) -> dict:
    """Fetch metal prices and market data from China Metal Market Network.

    Args:
        metals: List of metal keys (default: all metals).
            Available: copper, aluminum, zinc, lead, nickel, tin, steel.
        include_news: Whether to fetch market news and analysis.

    Returns:
        Dict with keys: 'prices', 'news', 'analysis'.
    """
    if metals is None:
        metals = list(METALS.keys())

    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "prices": [],
        "news": [],
        "analysis": [],
    }

    logger.info("Starting China Metal spider for metals: %s", ", ".join(metals))

    for metal in metals:
        metal_urls = [url for url in TARGET_URLS["prices"] if metal in url]
        if not metal_urls:
            continue

        for url in metal_urls:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_price_table(page["html"], url, metal)
                if items:
                    result["prices"].extend(items)
                    logger.info("  Extracted %d price records for %s", len(items), metal)

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

        for url in TARGET_URLS["analysis"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_news(page["html"], url)
                if items:
                    result["analysis"].extend(items)
                    logger.info("  Extracted %d analysis articles", len(items))

            time.sleep(2)

    if result["prices"]:
        n_sqlite = save_prices_to_sqlite(result["prices"], conn)
        save_to_json(result["prices"], JSON_PRICES_PATH)
        logger.info(
            "Saved %d price records: %d to SQLite (%s), JSON (%s)",
            len(result["prices"]),
            n_sqlite,
            DB_PATH,
            JSON_PRICES_PATH,
        )

    if result["news"]:
        save_news_to_sqlite(result["news"], conn)
        save_to_json(result["news"], JSON_NEWS_PATH)
        logger.info("Saved %d news articles to JSON (%s)", len(result["news"]), JSON_NEWS_PATH)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_chinametal_data(["copper", "aluminum", "zinc"], include_news=True)
    print(f"\n{'=' * 70}")
    print(f"Total price records: {len(results['prices'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"Total analysis articles: {len(results['analysis'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON (prices): {JSON_PRICES_PATH}")
    print(f"JSON (news): {JSON_NEWS_PATH}")
    print(f"{'=' * 70}")

    if results["prices"]:
        for metal in ["copper", "aluminum", "zinc"]:
            metal_items = [r for r in results["prices"] if r.get("metal") == metal]
            if metal_items:
                print(f"\n{metal.upper()} ({len(metal_items)} records):")
                for r in sorted(metal_items, key=lambda x: x.get("date", ""), reverse=True)[:5]:
                    avg_price = r.get("price_avg", "N/A")
                    if isinstance(avg_price, (int, float)):
                        avg_price = f"{avg_price:,.2f}"
                    print(f"  {r.get('date', 'N/A'):>12s}  {avg_price:>15s}  {r.get('unit', '')}")

    if results["news"]:
        print(f"\nRecent News ({len(results['news'])} articles):")
        for article in results["news"][:5]:
            print(f"  [{article.get('date', 'N/A')}] {article.get('title', 'N/A')[:60]}")
