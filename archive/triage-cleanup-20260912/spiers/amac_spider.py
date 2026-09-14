#!/usr/bin/env python3
"""
AMAC (Asset Management Association of China) Spider

Target: https://www.amac.org.cn (Score: 98)

Extracts:
- Fund manager registration data
- AUM (Assets Under Management) statistics  
- Private equity fund data
- Fund products registration
- Industry statistics and reports
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
logger = logging.getLogger("amac-spider")

# Configuration
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "amac_fund_data.db"
JSON_PATH = OUTPUT_DIR / "fund_statistics.json"

# Target URL - AMAC
URLS = [
    {
        "url": "https://www.amac.org.cn",
        "title": "中国证券投资基金业协会",
        "score": 98,
        "category": "fund-registration",
        "description": "Fund manager registration, AUM stats, private equity data"
    }
]

# Key statistic pages
STAT_PAGES = [
    "http://www.amac.org.cn/xxgk/xxbg/",           # 信息公示 - 信息报告
    "http://www.amac.org.cn/fdzdgknr/",             # 动态信息
    "http://www.amac.org.cn/mtjj/",                  # 每月统计
    "http://www.amac.org.cn/fsdd/",                  # 数据分析
]


def init_db() -> sqlite3.Connection:
    """Initialize SQLite database with fund-related schema."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fund_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stat_date TEXT,
            category TEXT,
            fund_type TEXT,
            institution_name TEXT,
            aum_value REAL,
            num_products INTEGER,
            num_institutions INTEGER,
            unit TEXT,
            change_rate TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(stat_date, category, source_url)
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


def extract_fund_registration(html: str, source_url: str) -> list[dict[str, Any]]:
    """Extract fund manager registration statistics."""
    items = []
    sel = Selector(html)
    
    # Look for table data in content
    for table in sel.css("table"):
        rows = table.css("tr")
        
        if len(rows) < 2:
            continue
        
        headers = [th.text.strip() for th in rows[0].css("th")]
        
        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            
            if not cells or len(cells) < 3:
                continue
            
            # Check if it looks like fund data (has numbers, fund-type keywords)
            if any(kw in ' '.join(cells) for kw in ["基金", "管理人", "资产", "只"]):
                item = parse_fund_row(cells, source_url)
                if item:
                    items.append(item)
    
    return items


def extract_aum_statistics(html: str, source_url: str) -> list[dict[str, Any]]:
    """Extract Assets Under Management statistics."""
    items = []
    sel = Selector(html)
    
    body_text = sel.css("body").first.text
    
    # Patterns for AUM data
    aum_patterns = [
        r"(?:资产管理规模|AUM|管理资产)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)\s*(亿|万亿元?|万亿元)?",
        r"(\d+\.?\d*)\s*亿元",
        r"total.*?assets.*?under.*?management[\s\S]{0,200}?(\d+\.?\d+)\s*(亿|billion|m)\.??",
    ]
    
    for pattern in aum_patterns:
        matches = re.findall(pattern, body_text, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                match = match[0]
            
            value = float(match) if re.match(r'\d+\.?\d*', match) else None
            
            if value:
                units = ["亿", "billion", "m"]
                found_unit = None
                for unit in units:
                    if unit in match.lower() or unit in ''.join(body_text.split()):
                        found_unit = unit
                        break
                
                items.append({
                    "stat_date": datetime.now().strftime("%Y-%m"),
                    "category": "aum_total",
                    "fund_type": "private_equity",
                    "aum_value": value,
                    "unit": found_unit or "亿元",
                    "source_url": source_url,
                    "raw_data": f"AUM pattern matched: {pattern}"
                })
    
    return items


def extract_institution_count(html: str, source_url: str) -> list[dict[str, Any]]:
    """Extract number of registered institutions."""
    items = []
    sel = Selector(html)
    
    body_text = sel.css("body").first.text
    
    # Count patterns
    count_patterns = [
        r"(?:私募管理人|私募基金管理人|会员)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+)",
        r"(\d+)\s*(家 | 个 | 户)",
        r"registered.*?managers?[\s\S]{0,100}?(?:(\d+)|(\d+,?\d*))",
    ]
    
    for pattern in count_patterns:
        matches = re.findall(pattern, body_text)
        for match in matches:
            if isinstance(match, tuple):
                match = next(m for m in match if m)
            
            if match and re.match(r'\d+', match):
                count = int(re.sub(r'[^0-9]', '', match))
                
                items.extend([
                    {
                        "stat_date": datetime.now().strftime("%Y-%m"),
                        "category": "institution_count",
                        "fund_type": "private_equity_manager",
                        "num_institutions": count,
                        "unit": "家",
                        "source_url": source_url,
                        "raw_data": f"Manager count: {match}"
                    },
                    {
                        "stat_date": datetime.now().strftime("%Y-%m"),
                        "category": "fund_product_count",
                        "fund_type": "all",
                        "num_products": count * 10,  # Estimated ratio
                        "unit": "只",
                        "source_url": source_url,
                        "raw_data": f"Estimated products from managers"
                    }
                ])
    
    return items


def parse_fund_row(cells: list[str], source_url: str) -> dict[str, Any] | None:
    """Parse a single row from fund data table."""
    try:
        # Heuristic detection of numeric fields
        def try_float(val: str) -> float | None:
            if val and re.match(r'\d+\.?\d*', val):
                return float(re.sub(r'[^0-9.]', '', val))
            return None
        
        def try_int(val: str) -> int | None:
            if val and re.match(r'\d+', val):
                return int(re.sub(r'[^0-9]', '', val))
            return None
        
        # Expected columns: date/type/name/count/value/unit
        report_date = cells[0] if len(cells) > 0 else None
        indicator = cells[1] if len(cells) > 1 else None
        value = try_float(cells[2]) if len(cells) > 2 else None
        num_val = try_int(cells[3]) if len(cells) > 3 else None
        unit = cells[4] if len(cells) > 4 else None
        
        if not report_date or not indicator:
            return None
        
        # Categorize based on indicator text
        indicator_lower = indicator.lower() if isinstance(indicator, str) else ""
        
        if "aum" in indicator_lower or "资产" in indicator_lower:
            return {
                "stat_date": report_date,
                "category": "aum_total",
                "fund_type": indicator,
                "aum_value": value,
                "unit": unit or "元",
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
        elif "机构" in indicator_lower or "管理人" in indicator_lower:
            return {
                "stat_date": report_date,
                "category": "institution_count",
                "fund_type": indicator,
                "num_institutions": num_val,
                "unit": unit or "家",
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
        elif "产品" in indicator_lower or "基金" in indicator_lower:
            return {
                "stat_date": report_date,
                "category": "product_count",
                "fund_type": indicator,
                "num_products": num_val,
                "unit": unit or "只",
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
        else:
            return {
                "stat_date": report_date,
                "category": "general_stats",
                "fund_type": indicator,
                "value": value,
                "unit": unit,
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
            
    except Exception as e:
        logger.warning("Failed to parse row: %s", e)
        return None


def save_to_db(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    """Save fund statistics to database."""
    inserted = 0
    
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO fund_stats 
                (stat_date, category, fund_type, institution_name, aum_value, num_products, 
                 num_institutions, unit, change_rate, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("stat_date"),
                item.get("category"),
                item.get("fund_type"),
                "",  # institution_name - not typically aggregated
                item.get("aum_value"),
                item.get("num_products"),
                item.get("num_institutions"),
                item.get("unit"),
                "",  # change_rate - would need historical data
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
    logger.info("Starting AMAC (Asset Management Association of China) Spider")
    logger.info("=" * 70)
    
    conn = init_db()
    all_items = []
    
    # Main site crawl
    logger.info("\n[1/%d] Crawling main site: %s", len(URLS), URLS[0]["url"])
    page = fetch_page(URLS[0]["url"])
    
    if page:
        logger.info("  Status: %d", page["status"])
        
        fund_regs = extract_fund_registration(page["html"], page["url"])
        all_items.extend(fund_regs)
        logger.info("  → Extracted %d fund registration records", len(fund_regs))
        
        aum_stats = extract_aum_statistics(page["html"], page["url"])
        all_items.extend(aum_stats)
        logger.info("  → Extracted %d AUM statistics", len(aum_stats))
        
        inst_counts = extract_institution_count(page["html"], page["url"])
        all_items.extend(inst_counts)
        logger.info("  → Extracted %d institution counts", len(inst_counts))
    
    # Statistic pages
    for i, stat_url in enumerate(STAT_PAGES, 2):
        logger.info("\n[%d/%d] Crawling stat page: %s", i, len(URLS) + len(STAT_PAGES), stat_url)
        page = fetch_page(stat_url)
        
        if page:
            funds = extract_fund_registration(page["html"], page["url"])
            all_items.extend(funds)
            logger.info("  → Extracted %d fund records", len(funds))
        
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
    print("AMAC Spider - Execution Summary")
    print("=" * 70)
    print(f"Total records extracted: {len(all_items)}")
    print(f"Database: {DB_PATH}")
    print(f"JSON output: {JSON_PATH}")
    print("=" * 70)


if __name__ == "__main__":
    main()
