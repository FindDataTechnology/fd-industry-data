#!/usr/bin/env python3
"""
LME (London Metal Exchange) Spider - 伦敦金属交易所数据爬虫

Target: https://www.lme.com
Data: Futures settlement prices, LME warehouse stocks, global market data
Metals: Copper, Aluminum, Zinc, Lead, Nickel, Tin, Aluminum Alloy

Architecture:
  - Primary: Direct HTML/API scraping with Scrapling
  - Anti-bot: Cloudflare bypass via browser impersonation
  - Currency: USD/ton (international standard)
  - Output: SQLite DB + JSON export

IMPORTANT: LME data access restrictions
  - Real-time data requires paid subscription
  - Delayed data (15-30 min) available for free
  - Some endpoints require API key registration
  - This spider extracts publicly available delayed prices
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
logger = logging.getLogger("lme_metals")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "lme_metals.db"
JSON_PATH = OUTPUT_DIR / "lme_prices.json"

METALS = {
    "copper": {
        "cn_name": "铜",
        "symbol": "CA",
        "unit": "USD/tonne",
        "lme_code": "CA",
    },
    "aluminum": {
        "cn_name": "铝",
        "symbol": "AH",
        "unit": "USD/tonne",
        "lme_code": "AH",
    },
    "zinc": {
        "cn_name": "锌",
        "symbol": "ZS",
        "unit": "USD/tonne",
        "lme_code": "ZS",
    },
    "lead": {
        "cn_name": "铅",
        "symbol": "PB",
        "unit": "USD/tonne",
        "lme_code": "PB",
    },
    "nickel": {
        "cn_name": "镍",
        "symbol": "NI",
        "unit": "USD/tonne",
        "lme_code": "NI",
    },
    "tin": {
        "cn_name": "锡",
        "symbol": "SN",
        "unit": "USD/tonne",
        "lme_code": "SN",
    },
}

TARGET_URLS = [
    "https://www.lme.com/en/Prices",
    "https://www.lme.com/en/Prices/LME-Prices",
    "https://www.lme.com/en/Markets/Data",
]

EXCHANGE_RATE_URL = "https://api.exchangerate-api.com/v4/latest/USD"


def init_db():
    """Initialize SQLite database with LME price schema."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lme_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            metal TEXT NOT NULL,
            metal_cn TEXT,
            symbol TEXT,
            lme_code TEXT,
            settlement_price REAL,
            bid_price REAL,
            offer_price REAL,
            price_change REAL,
            price_change_pct REAL,
            unit TEXT,
            currency TEXT DEFAULT 'USD',
            contract_month TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, metal, contract_month, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lme_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            metal TEXT NOT NULL,
            warehouse_location TEXT,
            stocks_tonnes REAL,
            change_tonnes REAL,
            source_url TEXT,
            scraped_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS exchange_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            from_currency TEXT,
            to_currency TEXT,
            rate REAL,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, from_currency, to_currency)
        )
    """)
    conn.commit()
    return conn


def fetch_page(url: str, fetcher: Fetcher) -> dict | None:
    """Fetch a page with Cloudflare bypass."""
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


def extract_lme_prices(html: str, url: str) -> list[dict]:
    """Extract LME settlement prices from HTML."""
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers).lower()

        if not any(keyword in header_text for keyword in ["metal", "price", "settlement", "lme"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            item = {
                "date": extract_date(cells[0] if cells else ""),
                "unit": "USD/tonne",
                "currency": "USD",
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            metal_info = identify_metal(cells)
            if metal_info:
                item.update(metal_info)
            else:
                continue

            price_data = extract_lme_price_values(cells)
            item.update(price_data)

            if item.get("settlement_price") or item.get("bid_price"):
                items.append(item)

    return items


def identify_metal(cells: list[str]) -> dict | None:
    """Identify metal from table cells."""
    cell_text = " ".join(cells).lower()

    for metal_key, metal_info in METALS.items():
        if metal_key in cell_text or metal_info["lme_code"].lower() in cell_text:
            return {
                "metal": metal_key,
                "metal_cn": metal_info["cn_name"],
                "symbol": metal_info["symbol"],
                "lme_code": metal_info["lme_code"],
            }
    return None


def extract_lme_price_values(cells: list[str]) -> dict:
    """Extract LME-specific price values."""
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

        if "settlement" in cell.lower() or "收盘" in cell or "结算" in cell:
            prices["settlement_price"] = value
        elif "bid" in cell.lower() or "买" in cell:
            prices["bid_price"] = value
        elif "offer" in cell.lower() or "卖" in cell or "ask" in cell.lower():
            prices["offer_price"] = value
        elif "change" in cell.lower() or "涨跌" in cell:
            if "%" in cell:
                prices["price_change_pct"] = value
            else:
                prices["price_change"] = value
        elif re.search(r"\d{4}-\d{2}", cell):
            prices["contract_month"] = cell

    if "settlement_price" not in prices:
        for i, cell in enumerate(cells):
            if i > 0 and re.search(r"[\d,]+\.?\d*", cell.replace(",", "")):
                try:
                    value = float(cell.replace(",", ""))
                    if value > 1000:
                        prices["settlement_price"] = value
                        break
                except ValueError:
                    continue

    return prices


def extract_date(text: str) -> str:
    """Extract date from text."""
    patterns = [
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{2}/\d{2}/\d{4})",
        r"(\d{2}\s+\w+\s+\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return datetime.now().strftime("%Y-%m-%d")


def fetch_exchange_rate(fetcher: Fetcher) -> dict | None:
    """Fetch USD/CNY exchange rate for currency conversion."""
    try:
        response = fetcher.get(EXCHANGE_RATE_URL, timeout=10)
        if response.status == 200:
            data = json.loads(response.text)
            rate = data.get("rates", {}).get("CNY")
            if rate:
                return {"USD": 1.0, "CNY": rate}
    except Exception as e:
        logger.warning("Exchange rate fetch failed: %s", e)
    return None


def save_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    """Save items to SQLite database."""
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO lme_prices
                (date, metal, metal_cn, symbol, lme_code, settlement_price,
                 bid_price, offer_price, price_change, price_change_pct,
                 unit, currency, contract_month, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"),
                    item.get("metal"),
                    item.get("metal_cn"),
                    item.get("symbol"),
                    item.get("lme_code"),
                    item.get("settlement_price"),
                    item.get("bid_price"),
                    item.get("offer_price"),
                    item.get("price_change"),
                    item.get("price_change_pct"),
                    item.get("unit"),
                    item.get("currency"),
                    item.get("contract_month"),
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


def convert_currency(items: list[dict], exchange_rate: float) -> list[dict]:
    """Convert USD prices to CNY."""
    converted = []
    for item in items:
        item_copy = item.copy()
        if item.get("settlement_price"):
            item_copy["settlement_price_cny"] = item["settlement_price"] * exchange_rate
            item_copy["currency_cny"] = "CNY"
        converted.append(item_copy)
    return converted


def get_lme_prices(metals: list[str] | None = None) -> list[dict]:
    """Fetch LME metal prices.

    Args:
        metals: List of metal keys (default: all metals).
            Available: copper, aluminum, zinc, lead, nickel, tin.

    Returns:
        List of dicts with LME price data (USD/tonne).
    """
    if metals is None:
        metals = list(METALS.keys())

    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()
    all_items = []

    logger.info("Starting LME spider for metals: %s", ", ".join(metals))

    exchange_rate = fetch_exchange_rate(fetcher)
    if exchange_rate:
        logger.info("USD/CNY exchange rate: %.4f", exchange_rate.get("CNY", 0))

    for url in TARGET_URLS:
        page = fetch_page(url, fetcher)
        if page:
            items = extract_lme_prices(page["html"], url)
            if items:
                filtered_items = [
                    item for item in items
                    if item.get("metal") in metals
                ]
                all_items.extend(filtered_items)
                logger.info("  Extracted %d LME price records", len(filtered_items))

        time.sleep(3)

    if exchange_rate and all_items:
        all_items = convert_currency(all_items, exchange_rate.get("CNY", 7.0))

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
        logger.warning("No LME price data extracted")
        logger.info("Note: LME may require special API access or subscription")

    conn.close()
    return all_items


if __name__ == "__main__":
    results = get_lme_prices(["copper", "aluminum", "zinc"])
    print(f"\n{'=' * 70}")
    print(f"Total records: {len(results)}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON: {JSON_PATH}")
    print(f"{'=' * 70}")

    if results:
        for metal in ["copper", "aluminum", "zinc"]:
            metal_items = [r for r in results if r.get("metal") == metal]
            if metal_items:
                print(f"\n{metal.upper()} (LME) - {len(metal_items)} records:")
                for r in sorted(metal_items, key=lambda x: x.get("date", ""), reverse=True)[:5]:
                    settlement = r.get("settlement_price", "N/A")
                    if isinstance(settlement, (int, float)):
                        settlement = f"${settlement:,.2f}"
                    print(f"  {r.get('date', 'N/A'):>12s}  {settlement:>15s}  {r.get('unit', '')}")
    else:
        print("\nNo data extracted. LME may require:")
        print("  1. Paid subscription for real-time data")
        print("  2. API key registration")
        print("  3. Alternative data source (see SMM for Chinese market)")
