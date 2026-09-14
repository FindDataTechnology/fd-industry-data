#!/usr/bin/env python3
"""
Shanghai Futures Exchange (SHFE) Spider

Target: https://www.shfe.com.cn (Score: 92)

Extracts:
- Metal futures prices (copper, aluminum, zinc, etc.)
- Settlement prices for all commodities
- Trading volume and open interest
- Inventory data
- Contract specifications
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from scrapling import Fetcher, Selector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("shfe-spider")

# Configuration
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "shfe_settlement.db"
JSON_PATH = OUTPUT_DIR / "settlement_prices.json"

# Target URL - Shanghai Futures Exchange
URLS = [
    {
        "url": "https://www.shfe.com.cn",
        "title": "上海期货交易所",
        "score": 92,
        "category": "metal-futures",
        "description": "Metal futures settlement prices, trading data"
    }
]

# Key statistics pages
STAT_PAGES = [
    "http://www.shfe.com.cn/data/dailydata/",           # 日涨跌行情
    "http://www.shfe.com.cn/data/marketdata/",          # 市场数据
    "http://www.shfe.com.cn/data/weekmonthlydata/",     # 周月统计
    "http://www.shfe.com.cn/data/inventorydata/",       # 库存数据
]


def init_db() -> sqlite3.Connection:
    """Initialize SQLite database with SHFE settlement data schema."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shfe_settlement (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT,
            contract_code TEXT,
            product_name TEXT,
            settlement_price REAL,
            reference_price REAL,
            change REAL,
            change_percent REAL,
            open_price REAL,
            high_price REAL,
            low_price REAL,
            close_price REAL,
            last_price REAL,
            volume INTEGER,
            turnover REAL,
            open_interest INTEGER,
           oi_change INTEGER,
            inventory_quantity INTEGER,
            unit TEXT,
            category TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(trade_date, contract_code, source_url)
        )
    """)
    
    conn.commit()
    return conn


def fetch_page(url: str, timeout: int = 30) -> dict | None:
    """Fetch webpage with error handling."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
        }
        
        fetcher = Fetcher(auto_match=False)
        response = fetcher.get(url, headers=headers, timeout=timeout)
        
        if response.status != 200:
            logger.warning("HTTP %d for %s", response.status, url)
            return None
            
        return {"url": url, "html": response.html, "status": response.status}
    except Exception as e:
        logger.error("Fetch failed for %s: %s", url, e)
        return None


def extract_settlement_prices(html: str, source_url: str) -> list[dict[str, Any]]:
    """Extract daily settlement prices from futures tables."""
    items = []
    sel = Selector(html)
    
    # Find all data tables on the page
    tables = []
    for table in sel.css("table"):
        rows = table.css("tr")
        
        if len(rows) >= 2:
            tables.append(table)
    
    if not tables:
        logger.info("No data tables found on page")
        return items
    
    # Use first substantial table
    table = tables[0]
    rows = table.css("tr")
    
    if len(rows) < 2:
        return items
    
    # Extract headers
    headers = [th.text.strip() for th in rows[0].css("th")]
    
    # Parse data rows
    for row in rows[1:]:
        cells = [td.text.strip() for td in row.css("td")]
        
        if not cells or len(cells) < 5:
            continue
        
        # Check if this looks like futures trading data
        content = ' '.join(cells).lower()
        if any(kw in content for kw in ["contract", "settle", "open", "high", "low"]) or \
           any(re.search(r'\$?\d+\.?\d*|\d{6,}', cell) for cell in cells):
            
            item = parse_shfe_row(cells, source_url)
            if item:
                items.append(item)
    
    return items


def extract_inventory_data(html: str, source_url: str) -> list[dict[str, Any]]:
    """Extract commodity inventory levels."""
    items = []
    sel = Selector(html)
    
    body_text = sel.css("body").first.text
    
    # Inventory patterns (Chinese keywords)
    inv_patterns = [
        r"(?:铜|铝|锌|铅|镍|锡)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+,?\d*)\s*(吨 | 吨位)?",
        r"inventory[\s\S]{0,50}?:(\d+,?\d*)\s*(tons)?",
        r"warehouse.*?receipt[\s\S]{0,50}?(?:(\d+,?\d*)|(\d{4,}))\s*(公吨)?",
    ]
    
    for pattern in inv_patterns:
        matches = re.findall(pattern, body_text, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                match = next(m for m in match if m)
            
            if match and re.match(r'\d+', match):
                inv_value = int(re.sub(r'[^0-9]', '', match))
                
                # Detect commodity type
                commodity = detect_commodity(body_text)
                
                items.append({
                    "trade_date": datetime.now().strftime("%Y-%m-%d"),
                    "contract_code": f"{commodity.upper()}_INV",
                    "product_name": commodity,
                    "settlement_price": None,
                    "reference_price": None,
                    "change": None,
                    "change_percent": None,
                    "open_price": None,
                    "high_price": None,
                    "low_price": None,
                    "close_price": None,
                    "last_price": None,
                    "volume": None,
                    "turnover": None,
                    "open_interest": None,
                    "oi_change": None,
                    "inventory_quantity": inv_value,
                    "unit": "公吨",
                    "category": "inventory",
                    "source_url": source_url,
                    "raw_data": f"Inventory: {match}"
                })
    
    return items


def extract_contract_specs(html: str, source_url: str) -> list[dict[str, Any]]:
    """Extract futures contract specifications."""
    items = []
    sel = Selector(html)
    
    body_text = sel.css("body").first.text
    
    # Look for contract info sections
    sections = sel.css(".section, .detail, .info, div[class*='spec']")
    
    for section in sections:
        text = section.text.lower()
        
        if any(kw in text for kw in ["contract", "规格", "合约", "tick"]):
            # Extract contract details
            contract_name = extract_contract_name(section.text)
            
            if contract_name:
                items.append({
                    "trade_date": datetime.now().strftime("%Y-%m-%d"),
                    "contract_code": contract_name,
                    "product_name": contract_name.split("-")[0] if "-" in contract_name else contract_name,
                    "settlement_price": None,
                    "reference_price": None,
                    "change": None,
                    "change_percent": None,
                    "open_price": None,
                    "high_price": None,
                    "low_price": None,
                    "close_price": None,
                    "last_price": None,
                    "volume": None,
                    "turnover": None,
                    "open_interest": None,
                    "oi_change": None,
                    "inventory_quantity": None,
                    "unit": "元/吨",
                    "category": "contract_spec",
                    "source_url": source_url,
                    "raw_data": section.text[:500]
                })
    
    return items


def detect_commodity(text: str) -> str:
    """Detect commodity type from text context."""
    text_lower = text.lower()
    
    # Chinese names
    commodities = [
        ("铜", "Copper"),
        ("铝", "Aluminum"),
        ("锌", "Zinc"),
        ("铅", "Lead"),
        ("镍", "Nickel"),
        ("锡", "Tin"),
        ("螺纹钢", "Rebar"),
        ("线材", "Wire Rod"),
        ("热轧卷板", "Hot Rolled Coil"),
        ("天然橡胶", "Natural Rubber"),
        ("沥青", "Bitumen"),
        ("玻璃", "Glass"),
        ("棉花", "Cotton"),
        ("白糖", "Sugar"),
        ("橡胶", "Rubber"),
        ("沪铜", "Copper"),
        ("沪铝", "Aluminum"),
        ("沪锌", "Zinc"),
    ]
    
    for cn, en in commodities:
        if cn in text_lower:
            return en
    
    # English fallback
    if "copper" in text_lower:
        return "Copper"
    elif "aluminum" in text_lower:
        return "Aluminum"
    elif "zinc" in text_lower:
        return "Zinc"
    elif "lead" in text_lower:
        return "Lead"
    elif "nickel" in text_lower:
        return "Nickel"
    elif "tin" in text_lower:
        return "Tin"
    elif "rebar" in text_lower:
        return "Rebar"
    elif "rubber" in text_lower:
        return "Natural Rubber"
    
    return "Metals Composite"


def extract_contract_name(text: str) -> str | None:
    """Extract contract name/code from text."""
    # Look for pattern like CU2406 or similar
    match = re.search(r'[A-Z]{2,4}\d{4,6}', text)
    if match:
        return match.group()
    
    # Try to extract from descriptive text
    contracts = re.findall(r'(?:主力合约|合约名称)[:：]?\s*([^\n]+)', text)
    if contracts:
        return contracts[0].strip()
    
    return None


def parse_shfe_row(cells: list[str], source_url: str) -> dict[str, Any] | None:
    """Parse a single row from SHFE trading data table."""
    try:
        def try_float(val: str) -> float | None:
            if val and re.match(r'\d+\.?\d*', val):
                return float(re.sub(r'[^\d.]', '', val))
            return None
        
        def try_int(val: str) -> int | None:
            if val and re.match(r'\d+', val):
                return int(re.sub(r'[^0-9]', '', val))
            return None
        
        # Typical SHFE table columns:
        # 品种名，交割月份，开盘价，最高价，最低价，收盘价，结算价，涨跌，跌幅，成交量，持仓量，仓单
        
        contract_code = cells[0] if len(cells) > 0 else None
        product_name = contract_code.split("-")[0] if contract_code else "Unknown"
        open_price = try_float(cells[1]) if len(cells) > 1 else None
        high_price = try_float(cells[2]) if len(cells) > 2 else None
        low_price = try_float(cells[3]) if len(cells) > 3 else None
        close_price = try_float(cells[4]) if len(cells) > 4 else None
        settle_price = try_float(cells[5]) if len(cells) > 5 else None
        change_val = try_float(cells[6]) if len(cells) > 6 else None
        change_pct = try_float(cells[7]) if len(cells) > 7 and "%" in cells[7] else None
        volume = try_int(cells[8]) if len(cells) > 8 else None
        open_interest = try_int(cells[9]) if len(cells) > 9 else None
        oi_change = try_int(cells[10]) if len(cells) > 10 else None
        
        # Some tables have different column order - adjust if needed
        if settle_price is None and close_price:
            settle_price = close_price
        
        if contract_code and not contract_code.startswith("商品"):
            return {
                "trade_date": datetime.now().strftime("%Y-%m-%d"),
                "contract_code": contract_code,
                "product_name": product_name,
                "settlement_price": settle_price,
                "reference_price": None,
                "change": change_val,
                "change_percent": change_pct,
                "open_price": open_price,
                "high_price": high_price,
                "low_price": low_price,
                "close_price": close_price,
                "last_price": settle_price or close_price,
                "volume": volume,
                "turnover": None,  # Would need additional calculation
                "open_interest": open_interest,
                "oi_change": oi_change,
                "inventory_quantity": None,
                "unit": "元/吨",
                "category": "daily_trading",
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
            
    except Exception as e:
        logger.warning("Failed to parse SHFE row: %s", e)
        return None


def save_to_db(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    """Save SHFE settlement data to database."""
    inserted = 0
    
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO shfe_settlement 
                (trade_date, contract_code, product_name, settlement_price, reference_price,
                 change, change_percent, open_price, high_price, low_price, close_price, last_price,
                 volume, turnover, open_interest, oi_change, inventory_quantity, unit, category,
                 source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("trade_date"),
                item.get("contract_code"),
                item.get("product_name"),
                item.get("settlement_price"),
                item.get("reference_price"),
                item.get("change"),
                item.get("change_percent"),
                item.get("open_price"),
                item.get("high_price"),
                item.get("low_price"),
                item.get("close_price"),
                item.get("last_price"),
                item.get("volume"),
                item.get("turnover"),
                item.get("open_interest"),
                item.get("oi_change"),
                item.get("inventory_quantity"),
                item.get("unit"),
                item.get("category"),
                item.get("source_url"),
                item.get("raw_data", ""),
                datetime.now().isoformat()
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert item: %s", e)
    
    conn.commit()
    return inserted


def main():
    """Main execution function."""
    logger.info("=" * 70)
    logger.info("Starting SHFE (Shanghai Futures Exchange) Spider")
    logger.info("=" * 70)
    
    conn = init_db()
    all_items = []
    
    # Main site crawl
    logger.info("\n[1/%d] Crawling main site: %s", len(URLS), URLS[0]["url"])
    page = fetch_page(URLS[0]["url"])
    
    if page:
        logger.info("  Status: %d", page["status"])
        
        settle_prices = extract_settlement_prices(page["html"], page["url"])
        all_items.extend(settle_prices)
        logger.info("  → Extracted %d settlement price records", len(settle_prices))
        
        inventory = extract_inventory_data(page["html"], page["url"])
        all_items.extend(inventory)
        logger.info("  → Extracted %d inventory records", len(inventory))
        
        specs = extract_contract_specs(page["html"], page["url"])
        all_items.extend(specs)
        logger.info("  → Extracted %d contract specs", len(specs))
    
    # Statistic pages
    for i, stat_url in enumerate(STAT_PAGES, 2):
        logger.info("\n[%d/%d] Crawling stat page: %s", i, len(URLS) + len(STAT_PAGES), stat_url)
        page = fetch_page(stat_url)
        
        if page:
            settles = extract_settlement_prices(page["html"], page["url"])
            all_items.extend(settles)
            logger.info("  → Extracted %d settlement records", len(settles))
            
            invs = extract_inventory_data(page["html"], page["url"])
            all_items.extend(invs)
            logger.info("  → Extracted %d inventory records", len(invs))
        
        time.sleep(2)
    
    # Save results
    if all_items:
        inserted = save_to_db(conn, all_items)
        logger.info("\n✓ Saved %d records to database", inserted)
        
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(all_items, f, ensure_ascii=False, indent=2)
        logger.info("✓ Exported %d records to JSON: %s", len(all_items), JSON_PATH)
    else:
        logger.warning("No data extracted")
    
    conn.close()
    
    print("\n" + "=" * 70)
    print("SHFE Spider - Execution Summary")
    print("=" * 70)
    print(f"Total records extracted: {len(all_items)}")
    print(f"Database: {DB_PATH}")
    print(f"JSON output: {JSON_PATH}")
    print("=" * 70)


if __name__ == "__main__":
    main()
