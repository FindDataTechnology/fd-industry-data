#!/usr/bin/env python3
"""
SMM (Shanghai Metals Market) Spider - 上海有色金属网数据爬虫

Target: https://www.smm.cn
Data: Daily spot prices, futures settlements, market analysis for non-ferrous metals
Metals: Copper (铜), Aluminum (铝), Zinc (锌), Lead (铅), Nickel (镍), Tin (锡)

Architecture:
  - Primary: Direct HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting, proper headers
  - Output: SQLite DB + JSON export

Note: SMM requires JavaScript rendering for some pages. This spider uses
browser impersonation to bypass basic anti-bot measures.
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
logger = logging.getLogger("smm_metals")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "smm_metals.db"
JSON_PATH = OUTPUT_DIR / "smm_prices.json"

METALS = {
    "copper": {"cn_name": "铜", "symbol": "CU", "unit": "元/吨"},
    "aluminum": {"cn_name": "铝", "symbol": "AL", "unit": "元/吨"},
    "zinc": {"cn_name": "锌", "symbol": "ZN", "unit": "元/吨"},
    "lead": {"cn_name": "铅", "symbol": "PB", "unit": "元/吨"},
    "nickel": {"cn_name": "镍", "symbol": "NI", "unit": "元/吨"},
    "tin": {"cn_name": "锡", "symbol": "SN", "unit": "元/吨"},
}

TARGET_URLS = [
    "https://www.smm.cn/prices/copper",
    "https://www.smm.cn/prices/aluminum",
    "https://www.smm.cn/prices/zinc",
    "https://www.smm.cn/prices/lead",
    "https://www.smm.cn/prices/nickel",
    "https://www.smm.cn/prices/tin",
    "https://www.smm.cn/metal/nonferrous",
]


def init_db():
    """Initialize SQLite database with metal price schema."""
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
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, metal, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_news (
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

        if not any(keyword in header_text for keyword in ["价格", "price", "均价", "涨跌"]):
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
                "unit": metal_info.get("unit", "元/吨"),
                "currency": "CNY",
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            price_values = extract_prices(cells)
            item.update(price_values)

            if item.get("price_avg") or item.get("price_low"):
                items.append(item)

    return items


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

        if "低" in cell or "low" in cell.lower():
            prices["price_low"] = value
        elif "高" in cell or "high" in cell.lower():
            prices["price_high"] = value
        elif "均" in cell or "avg" in cell.lower() or "中间" in cell:
            prices["price_avg"] = value
        elif "涨跌" in cell or "变化" in cell:
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


def save_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    """Save items to SQLite database."""
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO metal_prices
                (date, metal, metal_cn, symbol, price_low, price_high, price_avg,
                 price_change, price_change_pct, unit, currency, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def get_smm_prices(metals: list[str] | None = None) -> list[dict]:
    """Fetch metal prices from SMM.

    Args:
        metals: List of metal keys (default: all metals).
            Available: copper, aluminum, zinc, lead, nickel, tin.

    Returns:
        List of dicts with price data.
    """
    if metals is None:
        metals = list(METALS.keys())

    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()
    all_items = []

    logger.info("Starting SMM spider for metals: %s", ", ".join(metals))

    for metal in metals:
        metal_urls = [url for url in TARGET_URLS if metal in url]
        if not metal_urls:
            metal_urls = [TARGET_URLS[-1]]

        for url in metal_urls:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_price_table(page["html"], url, metal)
                if items:
                    all_items.extend(items)
                    logger.info("  Extracted %d price records for %s", len(items), metal)

            time.sleep(2)

    if all_items:
        n_sqlite = save_to_sqlite(all_items, conn)
        save_to_json(all_items, JSON_PATH)
        logger.info(
            "Saved %d records: %d to SQLite (%s), JSON (%s)",
            len(all_items),
            n_sqlite,
            DB_PATH,
            JSON_PATH,
        )
    else:
        logger.warning("No price data extracted")

    conn.close()
    return all_items


if __name__ == "__main__":
    results = get_smm_prices(["copper", "aluminum", "zinc"])
    print(f"\n{'=' * 70}")
    print(f"Total records: {len(results)}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON: {JSON_PATH}")
    print(f"{'=' * 70}")

    if results:
        for metal in ["copper", "aluminum", "zinc"]:
            metal_items = [r for r in results if r.get("metal") == metal]
            if metal_items:
                print(f"\n{metal.upper()} ({len(metal_items)} records):")
                for r in sorted(metal_items, key=lambda x: x.get("date", ""), reverse=True)[:5]:
                    avg_price = r.get("price_avg", "N/A")
                    if isinstance(avg_price, (int, float)):
                        avg_price = f"{avg_price:,.2f}"
                    print(f"  {r.get('date', 'N/A'):>12s}  {avg_price:>15s}  {r.get('unit', '')}")
