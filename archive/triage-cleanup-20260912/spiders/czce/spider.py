#!/usr/bin/env python3
"""
CZCE Spider - Zhengzhou Commodity Exchange Scraper
http://www.czce.com.cn/

Data Coverage:
  - Futures settlement prices (daily)
  - Trading volumes and open interest  
  - Contract specifications
  - Delivery statistics
  - Market notices and announcements

Authentication:
  - Public pages: no login required
  - All market data publicly available

Architecture:
  - Primary: Scrapling Fetcher with Chrome impersonation
  - Anti-bot: Browser impersonation, stealthy headers, rate limiting
  - Output: SQLite DB + JSON export
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from scrapling import Fetcher, Selector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("czce")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "czce.db"

# Contract categories
CONTRACT_CATEGORIES = {
    "agricultural": {"cn_name": "农产品类", "contracts": ["PTA", "白糖", "菜油", "早籼稻"]},
    "energy_chemical": {"cn_name": "能源化工类", "contracts": ["甲醇", "玻璃", "粳米"]},
}

TARGET_URLS = {
    "home": "http://www.czce.com.cn/",
    "settlement_prices": [
        "http://www.czce.com.cn/cn/markettrade/sjjg/index.shtml",
    ],
    "open_interest": [
        "http://www.czce.com.cn/cn/markettrade/kzcj/index.shtml",
    ],
    "news": [
        "http://www.czce.com.cn/cn/xwdt/index.shtml",
    ],
    "announcements": [
        "http://www.czce.com.cn/cn/gongzuitg/gg/index.shtml",
    ],
}


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settlement_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            contract_code TEXT NOT NULL,
            contract_name TEXT,
            category TEXT,
            open_price REAL,
            high_price REAL,
            low_price REAL,
            close_price REAL,
            settle_price REAL,
            pre_settle REAL,
            volume INTEGER,
            turnover REAL,
            open_interest REAL,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(trade_date, contract_code, source_url)
        )
    """)
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_notices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            publish_date TEXT,
            notice_type TEXT,
            summary TEXT,
            url TEXT UNIQUE,
            source_url TEXT,
            scraped_at TEXT NOT NULL
        )
    """)
    
    conn.commit()
    return conn


def fetch_page(url: str) -> dict | None:
    try:
        logger.info("Fetching: %s", url)
        fetcher = Fetcher(auto_match=False, impersonate="chrome")
        response = fetcher.get(url, timeout=30, stealthy_headers=True)
        if response.status != 200:
            logger.warning("HTTP %d for %s", response.status, url)
            return None
        return {"url": url, "html": response.html, "status": response.status}
    except Exception as e:
        logger.error("Fetch failed for %s: %s", url, e)
        return None


def extract_date(text: str) -> str:
    patterns = [
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{4}年\d{1,2}月\d{1,2}日)",
        r"(\d{4}/\d{2}/\d{2})",
        r"(\d{8})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            date_str = match.group(1)
            if len(date_str) == 8 and date_str.isdigit():
                return f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"
            return date_str
    return datetime.now().strftime("%Y-%m-%d")


def extract_number(text: str) -> float | None:
    if not text:
        return None
    text = text.replace(",", "").replace("，", "").replace(" ", "")
    match = re.search(r"[-+]?\d+\.?\d*", text)
    if match:
        try:
            return float(match.group())
        except ValueError:
            return None
    return None


def identify_contract(text: str) -> tuple[str, str, str]:
    for cat_key, cat_info in CONTRACT_CATEGORIES.items():
        for contract in cat_info["contracts"]:
            if contract in text:
                return contract, contract, cat_key
    return "other", text[:20], "other"


def extract_settlement_prices(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []
    
    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue
            
        header_text = " ".join([th.text.strip() for th in rows[0].css("th, td")]).lower()
        
        if any(kw in header_text for kw in ["开盘", "最高", "最低", "收盘", "结算"]):
            for row in rows[1:]:
                cells = [td.text.strip() for td in row.css("td")]
                if len(cells) < 5:
                    continue
                
                row_text = " ".join(cells)
                contract_cn, contract_code, category = identify_contract(row_text)
                
                item = {
                    "contract_code": contract_code,
                    "contract_name": contract_cn,
                    "category": category,
                    "source_url": url,
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                }
                
                for i, cell in enumerate(cells):
                    num_val = extract_number(cell)
                    text_lower = cell.lower()
                    
                    if "日期" in text_lower or re.match(r"\d{4}", cell):
                        item["trade_date"] = extract_date(cell)
                    elif num_val is not None:
                        if "price" not in item and "开盘" in header_text:
                            item["open_price"] = num_val
                        elif "high" not in item and "最高" in header_text:
                            item["high_price"] = num_val
                        elif "low" not in item and "最低" in header_text:
                            item["low_price"] = num_val
                        elif "close" not in item and "收盘" in header_text:
                            item["close_price"] = num_val
                        elif "settle" in header_text or "结算" in header_text:
                            item["settle_price"] = num_val
                        elif "volume" in text_lower and i > 5:
                            try:
                                item["volume"] = int(num_val)
                            except ValueError:
                                pass
                        elif "turnover" in text_lower or "成交额" in header_text:
                            item["turnover"] = num_val
                        elif "interest" in text_lower or "持仓" in header_text:
                            item["open_interest"] = num_val
                
                if "trade_date" in item and "contract_code" in item:
                    items.append(item)
    
    return items


def save_to_sqlite(items: list[dict], conn: sqlite3.Connection, table: str) -> int:
    inserted = 0
    for item in items:
        try:
            if table == "settlement":
                conn.execute("""
                    INSERT OR REPLACE INTO settlement_prices
                    (trade_date, contract_code, contract_name, category, open_price,
                     high_price, low_price, close_price, settle_price, pre_settle,
                     volume, turnover, open_interest, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    item.get("trade_date"),
                    item.get("contract_code"),
                    item.get("contract_name"),
                    item.get("category"),
                    item.get("open_price"),
                    item.get("high_price"),
                    item.get("low_price"),
                    item.get("close_price"),
                    item.get("settle_price"),
                    item.get("pre_settle"),
                    item.get("volume"),
                    item.get("turnover"),
                    item.get("open_interest"),
                    item.get("source_url"),
                    item.get("scraped_at"),
                ))
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def get_czce_data() -> dict:
    conn = init_db()
    result = {"prices": [], "notices": []}

    logger.info("Starting CZCE data fetch")

    for url in TARGET_URLS["settlement_prices"]:
        page = fetch_page(url)
        if page:
            price_items = extract_settlement_prices(page["html"], url)
            if price_items:
                result["prices"].extend(price_items)
                logger.info("  Extracted %d price records from %s", len(price_items), url)
        time.sleep(2)

    # Save results
    if result["prices"]:
        n = save_to_sqlite(result["prices"], conn, "settlement")
        logger.info("Saved %d price records (%d to SQLite)", len(result["prices"]), n)

    conn.close()
    print(f"CZCE Data Complete - {len(result['prices'])} price records")
    return result


def run_czce(limit: int | None = None) -> list[dict]:
    """Entry point for fd-open-data-protocol dispatch."""
    result = get_czce_data()
    all_records = []
    all_records.extend(result.get("prices", []))
    all_records.extend(result.get("notices", []))
    if limit is not None:
        all_records = all_records[:limit]
    return all_records


if __name__ == "__main__":
    run_czce()
