#!/usr/bin/env python3
"""
China Iron & Steel Association Spider (中国钢铁工业协会)

Target: https://www.cisa.org.cn (Score: 98)

Extracts:
- Industry production statistics
- Capacity utilization rates
- Steel import/export data
- Price indices
- Market analysis reports
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
logger = logging.getLogger("cisa-spider")

# Configuration
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "cisa_production.db"
JSON_PATH = OUTPUT_DIR / "production_statistics.json"

# Target URL - China Iron & Steel Association
URLS = [
    {
        "url": "https://www.cisa.org.cn",
        "title": "中国钢铁工业协会",
        "score": 98,
        "category": "industry-production",
        "description": "Official production stats, capacity utilization, export/import data"
    }
]

# Known statistic pages
STATISTIC_PAGES = [
    "http://www.cisa.org.cn/ssjg/index.html",  # 统计数据
    "http://www.cisa.org.cn/kytz/index.html",  # 行业情报
    "http://www.cisa.org.cn/hssd/index.html",  # 行业标准
]


def init_db() -> sqlite3.Connection:
    """Initialize SQLite database with proper schema."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS production_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT,
            indicator_name TEXT,
            value REAL,
            unit TEXT,
            change_rate TEXT,
            source_url TEXT,
            page_category TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(report_date, indicator_name, source_url)
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
            
        return {
            "url": url,
            "html": response.html,
            "status": response.status,
            "headers": dict(response.headers)
        }
    except Exception as e:
        logger.error("Fetch failed for %s: %s", url, e)
        return None


def extract_production_stats(html: str, source_url: str) -> list[dict[str, Any]]:
    """Extract production and capacity statistics from HTML tables."""
    items = []
    sel = Selector(html)
    
    # Find all table structures
    for table in sel.css("table"):
        rows = table.css("tr")
        
        if not rows:
            continue
        
        # Extract headers
        header_cells = rows[0].css("th, td")
        headers = [cell.text.strip() for cell in header_cells if cell.text.strip()]
        
        # Extract data rows
        for row in rows[1:]:
            cells = row.css("td")
            if not cells:
                continue
                
            cell_texts = [cell.text.strip() for cell in cells]
            
            # Check if this looks like statistics data (has numeric values)
            if any(re.search(r'\d+\.?\d*', text) for text in cell_texts):
                # Map to standardized fields
                stat_item = {
                    "report_date": cell_texts[0] if len(cell_texts) > 0 else None,
                    "indicator_name": cell_texts[1] if len(cell_texts) > 1 else None,
                    "value": float(cell_texts[2]) if len(cell_texts) > 2 and re.match(r'\d+\.?\d*', cell_texts[2]) else None,
                    "unit": cell_texts[3] if len(cell_texts) > 3 else None,
                    "change_rate": cell_texts[4] if len(cell_texts) > 4 else None,
                    "source_url": source_url,
                    "raw_data": json.dumps({"headers": headers, "row": cell_texts})
                }
                
                # Clean up None values
                stat_item = {k: v for k, v in stat_item.items() if v is not None}
                items.append(stat_item)
    
    return items


def extract_capacity_utilization(html: str, source_url: str) -> list[dict[str, Any]]:
    """Extract capacity utilization rate data."""
    items = []
    sel = Selector(html)
    
    # Look for capacity-related keywords in content
    capacity_keywords = ["产能利用率", "capacity utilization", "利用率为"]
    
    # Search for text patterns
    body_text = sel.css("body").first.text
    for keyword in capacity_keywords:
        if keyword in body_text:
            # Try to find numbers nearby
            pattern = rf"{keyword}.*?(\d+\.?\d*)%?"
            matches = re.findall(pattern, body_text)
            for match in matches:
                items.append({
                    "report_date": datetime.now().strftime("%Y-%m-%d"),
                    "indicator_name": "capacity_utilization_rate",
                    "value": float(match) if re.match(r'\d+\.?\d*', match) else None,
                    "unit": "%",
                    "change_rate": None,
                    "source_url": source_url,
                    "raw_data": f"Found pattern: {keyword}"
                })
    
    return items


def save_to_db(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    """Save items to SQLite database."""
    inserted = 0
    
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO production_stats 
                (report_date, indicator_name, value, unit, change_rate, source_url, page_category, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("report_date"),
                item.get("indicator_name"),
                item.get("value"),
                item.get("unit"),
                item.get("change_rate"),
                item.get("source_url"),
                "industry-production",
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
    logger.info("Starting CISA (China Iron & Steel Association) Spider")
    logger.info("=" * 70)
    
    conn = init_db()
    all_items = []
    
    # Primary crawl of base URL
    logger.info("\n[1/%d] Crawling main site: %s", len(URLS), URLS[0]["url"])
    page = fetch_page(URLS[0]["url"])
    
    if page:
        logger.info("  Status: %d", page["status"])
        
        # Extract production statistics from tables
        prod_stats = extract_production_stats(page["html"], page["url"])
        all_items.extend(prod_stats)
        logger.info("  → Extracted %d production statistics", len(prod_stats))
        
        # Extract capacity utilization data
        capacity_data = extract_capacity_utilization(page["html"], page["url"])
        all_items.extend(capacity_data)
        logger.info("  → Extracted %d capacity utilization records", len(capacity_data))
    
    # Crawl known statistic pages
    for i, stat_url in enumerate(STATISTIC_PAGES, 2):
        logger.info("\n[%d/%d] Crawling statistics page: %s", i, len(URLS) + len(STATISTIC_PAGES), stat_url)
        page = fetch_page(stat_url)
        
        if page:
            stats = extract_production_stats(page["html"], page["url"])
            all_items.extend(stats)
            logger.info("  → Extracted %d statistics", len(stats))
        
        time.sleep(2)  # Respectful delay
    
    # Save to database
    if all_items:
        inserted = save_to_db(conn, all_items)
        logger.info("\n✓ Saved %d records to database", inserted)
        
        # Export to JSON
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(all_items, f, ensure_ascii=False, indent=2)
        logger.info("✓ Exported %d records to JSON: %s", len(all_items), JSON_PATH)
    else:
        logger.warning("No data extracted")
    
    conn.close()
    
    # Summary
    print("\n" + "=" * 70)
    print("CISA Spider - Execution Summary")
    print("=" * 70)
    print(f"Total records extracted: {len(all_items)}")
    print(f"Database: {DB_PATH}")
    print(f"JSON output: {JSON_PATH}")
    print("=" * 70)


if __name__ == "__main__":
    main()
