#!/usr/bin/env python3
"""
CME Group Agriculture Futures Spider

Target: https://www.cmegroup.com/markets/agricultural.html (Score: 90)

Extracts:
- Agricultural futures prices (corn, wheat, soybeans, etc.)
- Open interest data
- Settlement prices
- Trading volume statistics
- Historical price data
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
logger = logging.getLogger("cmegroup-ag-spider")

# Configuration
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "cme_ag_futures.db"
JSON_PATH = OUTPUT_DIR / "agricultural_futures.json"

# Target URLs - CME Agriculture
URLS = [
    {
        "url": "https://www.cmegroup.com/markets/agricultural.html",
        "title": "CME Group Agriculture Futures",
        "score": 90,
        "category": "agricultural-futures",
        "description": "Corn, wheat, soybeans, livestock futures prices and data"
    }
]

# Specific agricultural products pages
AG_PAGES = [
    "https://www.cmegroup.com/markets/agricultural/corn.html",
    "https://www.cmegroup.com/markets/agricultural/wheat.html",
    "https://www.cmegroup.com/markets/agricultural/soybeans.html",
    "https://www.cmegroup.com/markets/agricultural/livestock.html",
    "https://www.cmegroup.com/markets/agricultural/dairy.html",
]


def init_db() -> sqlite3.Connection:
    """Initialize SQLite database with futures data schema."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cme_ag_futures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_date TEXT,
            product_name TEXT,
            settlement_price REAL,
            open_price REAL,
            high_price REAL,
            low_price REAL,
            last_price REAL,
            change REAL,
            change_percent REAL,
            volume INTEGER,
            open_interest INTEGER,
            value REAL,
            unit TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(contract_date, product_name, source_url)
        )
    """)
    
    conn.commit()
    return conn


def fetch_page(url: str, timeout: int = 60) -> dict | None:
    """Fetch webpage with error handling. Extended timeout for CME."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
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
    
    # Find all table elements that could contain price data
    for table in sel.css("table"):
        rows = table.css("tr")
        
        if len(rows) < 2:
            continue
        
        # Skip header row
        headers = [th.text.strip() for th in rows[0].css("th")]
        
        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            
            if not cells or len(cells) < 5:
                continue
            
            content = ' '.join(cells).lower()
            
            # Check if this looks like futures data
            if any(kw in content for kw in ["settle", "last", "high", "low", "open"]) or \
               any(re.search(r'\$?\d+\.?\d*', cell) for cell in cells):
                
                item = parse_futures_row(cells, source_url)
                if item:
                    items.append(item)
    
    return items


def extract_open_interest(html: str, source_url: str) -> list[dict[str, Any]]:
    """Extract open interest data by contract month."""
    items = []
    sel = Selector(html)
    
    body_text = sel.css("body").first.text
    
    # Look for pattern like "Open Interest: X contracts" or "OI: X"
    oi_patterns = [
        r"open.*?interest[\s\S]{0,50}?(\d+,?\d*)\s*(contracts)?",
        r"oi[\s\S]{0,50}?(?:(\d+,?\d*)|(\d{4,}))",
        r"(?:(?:corn|wheat|soybean|live cattle|lean hogs)[\s\n]+)?(?:open\s+interest|OI)[:\s]*(\d+,?\d*)",
    ]
    
    for pattern in oi_patterns:
        matches = re.findall(pattern, body_text, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                match = next(m for m in match if m)
            
            if match and re.match(r'\d{4,}', match):
                oi_value = int(re.sub(r'[^0-9]', '', match))
                
                # Try to detect product from surrounding context
                product = detect_product_from_context(body_text, source_url)
                
                items.append({
                    "contract_date": datetime.now().strftime("%Y-%m-%d"),
                    "product_name": product,
                    "settlement_price": None,
                    "open_price": None,
                    "high_price": None,
                    "low_price": None,
                    "last_price": None,
                    "change": None,
                    "change_percent": None,
                    "volume": None,
                    "open_interest": oi_value,
                    "value": None,
                    "unit": "contracts",
                    "source_url": source_url,
                    "raw_data": f"Open interest: {match}"
                })
    
    return items


def extract_volume_data(html: str, source_url: str) -> list[dict[str, Any]]:
    """Extract trading volume data."""
    items = []
    sel = Selector(html)
    
    body_text = sel.css("body").first.text
    
    # Volume patterns
    vol_patterns = [
        r"volume[\s\S]{0,50}?(?:(\d+,?\d*)|\b(\d{4,}))\s*(contracts)?",
        r"(?:(?:corn|wheat|soybean|live cattle|lean hogs)[\s\n]+)?volume[:\s]*(\d+,?\d*)",
        r"\b(\d+,?\d*)\s*contracts?[-–—]\s*(?:active|total)",
    ]
    
    for pattern in vol_patterns:
        matches = re.findall(pattern, body_text, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                match = next(m for m in match if m)
            
            if match and re.match(r'\d{4,}', match):
                vol_value = int(re.sub(r'[^0-9]', '', match))
                
                product = detect_product_from_context(body_text, source_url)
                
                items.append({
                    "contract_date": datetime.now().strftime("%Y-%m-%d"),
                    "product_name": product,
                    "settlement_price": None,
                    "open_price": None,
                    "high_price": None,
                    "low_price": None,
                    "last_price": None,
                    "change": None,
                    "change_percent": None,
                    "volume": vol_value,
                    "open_interest": None,
                    "value": None,
                    "unit": "contracts",
                    "source_url": source_url,
                    "raw_data": f"Volume: {match}"
                })
    
    return items


def extract_contract_spec(html: str, source_url: str) -> list[dict[str, Any]]:
    """Extract futures contract specifications."""
    items = []
    sel = Selector(html)
    
    # Look for contract spec tables
    for table in sel.css("table"):
        rows = table.css("tr")
        
        if len(rows) < 2:
            continue
        
        # Extract contract details
        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            
            if len(cells) >= 3:
                # Check if it's a contract specification
                if any(kw in ' '.join(cells).lower() for kw in 
                       ["contract", "tick", "size", "month", "delivery"]):
                    
                    items.append({
                        "contract_date": datetime.now().strftime("%Y-%m-%d"),
                        "product_name": cells[0] if cells else "unknown",
                        "settlement_price": None,
                        "open_price": None,
                        "high_price": None,
                        "low_price": None,
                        "last_price": None,
                        "change": None,
                        "change_percent": None,
                        "volume": None,
                        "open_interest": None,
                        "value": None,
                        "unit": "spec",
                        "source_url": source_url,
                        "raw_data": json.dumps({"cells": cells})
                    })
    
    return items


def detect_product_from_context(text: str, source_url: str) -> str:
    """Detect futures product type from URL or text context."""
    # From URL first
    url_lower = source_url.lower()
    if "corn" in url_lower:
        return "Corn Futures"
    elif "wheat" in url_lower:
        return "Wheat Futures"
    elif "soybean" in url_lower:
        return "Soybeans Futures"
    elif "livestock" in url_lower:
        return "Live Cattle / Lean Hogs Futures"
    elif "dairy" in url_lower:
        return "Dairy Products Futures"
    
    # From text context
    text_lower = text.lower()
    products = [
        ("corn", "Corn Futures"),
        ("wheat", "Wheat Futures"),
        ("soybean", "Soybeans Futures"),
        ("live cattle", "Live Cattle Futures"),
        ("lean hog", "Lean Hogs Futures"),
        ("class iii milk", "Class III Milk Futures"),
        ("chicken", "Pork Bellies/Cotton Futures"),
    ]
    
    for keyword, name in products:
        if keyword in text_lower:
            return name
    
    return "Agricultural Futures"


def parse_futures_row(cells: list[str], source_url: str) -> dict[str, Any] | None:
    """Parse a single row from futures price table."""
    try:
        def try_float(val: str) -> float | None:
            if val and re.match(r'\$?\d+\.?\d*', val):
                return float(re.sub(r'[^\d.]', '', val))
            return None
        
        def try_int(val: str) -> int | None:
            if val and re.match(r'\d+', val):
                return int(re.sub(r'[^0-9]', '', val))
            return None
        
        # Expected columns typically: Contract, Last, Settle, Change%, Volume, Open Interest
        contract_name = cells[0] if len(cells) > 0 else None
        last_price = try_float(cells[1]) if len(cells) > 1 else None
        settle_price = try_float(cells[2]) if len(cells) > 2 else None
        change = try_float(cells[3]) if len(cells) > 3 else None
        change_pct = try_float(cells[4]) if len(cells) > 4 and "%" in cells[4] else None
        volume = try_int(cells[5]) if len(cells) > 5 else None
        open_interest = try_int(cells[6]) if len(cells) > 6 else None
        
        if not contract_name:
            return None
        
        return {
            "contract_date": datetime.now().strftime("%Y-%m-%d"),
            "product_name": contract_name.replace("\n", " ").strip(),
            "settlement_price": settle_price,
            "open_price": try_float(cells[1]) if len(cells) > 1 and len(set(str(c) for c in cells[:3])) > 1 else None,
            "high_price": try_float(cells[2]) if len(cells) > 2 and settle_price is None else None,
            "low_price": try_float(cells[3]) if len(cells) > 3 else None,
            "last_price": last_price,
            "change": change,
            "change_percent": change_pct,
            "volume": volume,
            "open_interest": open_interest,
            "value": None,
            "unit": "USD/bu" if "corn" in contract_name.lower() or "wheat" in contract_name.lower() or "soybean" in contract_name.lower() else "USD/cwt",
            "source_url": source_url,
            "raw_data": json.dumps({"cells": cells})
        }
            
    except Exception as e:
        logger.warning("Failed to parse futures row: %s", e)
        return None


def save_to_db(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    """Save CME agriculture futures data to database."""
    inserted = 0
    
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO cme_ag_futures 
                (contract_date, product_name, settlement_price, open_price, high_price, low_price,
                 last_price, change, change_percent, volume, open_interest, value, unit, 
                 source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("contract_date"),
                item.get("product_name"),
                item.get("settlement_price"),
                item.get("open_price"),
                item.get("high_price"),
                item.get("low_price"),
                item.get("last_price"),
                item.get("change"),
                item.get("change_percent"),
                item.get("volume"),
                item.get("open_interest"),
                item.get("value"),
                item.get("unit"),
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
    logger.info("Starting CME Group Agriculture Futures Spider")
    logger.info("=" * 70)
    
    conn = init_db()
    all_items = []
    
    # Main site crawl
    logger.info("\n[1/%d] Crawling main agriculture page: %s", len(URLS), URLS[0]["url"])
    page = fetch_page(URLS[0]["url"])
    
    if page:
        logger.info("  Status: %d", page["status"])
        
        settle_prices = extract_settlement_prices(page["html"], page["url"])
        all_items.extend(settle_prices)
        logger.info("  → Extracted %d settlement price records", len(settle_prices))
        
        oi_data = extract_open_interest(page["html"], page["url"])
        all_items.extend(oi_data)
        logger.info("  → Extracted %d open interest records", len(oi_data))
        
        vol_data = extract_volume_data(page["html"], page["url"])
        all_items.extend(vol_data)
        logger.info("  → Extracted %d volume records", len(vol_data))
    
    # Specific product pages
    for i, ag_url in enumerate(AG_PAGES, 2):
        logger.info("\n[%d/%d] Crawling product page: %s", i, len(URLS) + len(AG_PAGES), ag_url)
        page = fetch_page(ag_url)
        
        if page:
            prices = extract_settlement_prices(page["html"], page["url"])
            all_items.extend(prices)
            logger.info("  → Extracted %d price records", len(prices))
            
            oi = extract_open_interest(page["html"], page["url"])
            all_items.extend(oi)
            logger.info("  → Extracted %d OI records", len(oi))
        
        time.sleep(3)  # Longer delay for CME pages
    
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
    print("CME Agriculture Spider - Execution Summary")
    print("=" * 70)
    print(f"Total records extracted: {len(all_items)}")
    print(f"Database: {DB_PATH}")
    print(f"JSON output: {JSON_PATH}")
    print("=" * 70)


if __name__ == "__main__":
    main()
