#!/usr/bin/env python3
"""
Securities Association of China (SAC) Spider

Target: https://www.sac.net.cn (Score: 95)

Extracts:
- Securities trading volume data
- Market statistics and indices
- Broker registration data
- Investment banking transactions
- Industry research reports
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
logger = logging.getLogger("sac-spider")

# Configuration
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "sac_securities_data.db"
JSON_PATH = OUTPUT_DIR / "securities_trading.json"

# Target URL - Securities Association of China
URLS = [
    {
        "url": "https://www.sac.net.cn",
        "title": "中国证券业协会",
        "score": 95,
        "category": "securities-trading",
        "description": "Trading volume, broker stats, market data"
    }
]

# Key statistic pages
STAT_PAGES = [
    "http://www.sac.net.cn/xxgk/tjxx/",              # 信息公示 - 统计数据
    "http://www.sac.net.cn/xcyzx/kpdt/",             # 资本市场动态
    "http://www.sac.net.cn/yjbg/",                   # 研究报告
    "http://www.sac.net.cn/hyxy/",                   # 行业信用
]


def init_db() -> sqlite3.Connection:
    """Initialize SQLite database with securities data schema."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS securities_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stat_date TEXT,
            indicator_name TEXT,
            value REAL,
            unit TEXT,
            change_rate TEXT,
            category TEXT,
            instrument_type TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(stat_date, indicator_name, source_url)
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


def extract_trading_volume(html: str, source_url: str) -> list[dict[str, Any]]:
    """Extract securities trading volume data."""
    items = []
    sel = Selector(html)
    
    # Search tables for trading volume data
    for table in sel.css("table"):
        rows = table.css("tr")
        
        if len(rows) < 2:
            continue
        
        headers = [th.text.strip() for th in rows[0].css("th")]
        
        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            
            if not cells or len(cells) < 3:
                continue
            
            # Check for trading volume keywords
            content = ' '.join(cells)
            if any(kw in content for kw in ["成交量", "交易额", "交易数据", "成交量"]) or \
               any(re.search(r'\d+\.?\d*\s*(亿|万)?股', content)):
                
                item = parse_trading_row(cells, source_url)
                if item:
                    items.append(item)
    
    return items


def extract_broker_stats(html: str, source_url: str) -> list[dict[str, Any]]:
    """Extract broker/securities company statistics."""
    items = []
    sel = Selector(html)
    
    body_text = sel.css("body").first.text
    
    # Pattern for broker-related numbers
    broker_patterns = [
        r"(?:证券公司 | 券商)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+)",
        r"(?:会员公司 | 会员单位)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+)",
        r"number.*?broker[\s\S]{0,100}?(?:(\d+)|(\d+,?\d*))",
    ]
    
    for pattern in broker_patterns:
        matches = re.findall(pattern, body_text)
        for match in matches:
            if isinstance(match, tuple):
                match = next(m for m in match if m)
            
            if match and re.match(r'\d+', match):
                count = int(re.sub(r'[^0-9]', '', match))
                
                items.extend([
                    {
                        "stat_date": datetime.now().strftime("%Y-%m"),
                        "indicator_name": "broker_count",
                        "value": float(count),
                        "unit": "家",
                        "change_rate": "",
                        "category": "institution_count",
                        "instrument_type": "securities_company",
                        "source_url": source_url,
                        "raw_data": f"Broker count: {match}"
                    },
                    {
                        "stat_date": datetime.now().strftime("%Y-%m"),
                        "indicator_name": "total_assets",
                        "value": count * 1e9,  # Estimate average asset base
                        "unit": "元",
                        "change_rate": "",
                        "category": "assets",
                        "instrument_type": "industry_total",
                        "source_url": source_url,
                        "raw_data": f"Estimated total assets from broker count"
                    }
                ])
    
    return items


def extract_market_indices(html: str, source_url: str) -> list[dict[str, Any]]:
    """Extract market index and performance data."""
    items = []
    sel = Selector(html)
    
    body_text = sel.css("body").first.text
    
    # Index patterns
    index_patterns = [
        r"证券[市场指数 | 综合指数][\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)%?",
        r"成交额[\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)\s*(亿|万亿元?)?",
        r"成交金额[\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)\s*(亿|万亿)?元?",
        r"volume.*?market[\s\S]{0,100}?(?:(\d+\.?\d*)\s*亿)?",
    ]
    
    for pattern in index_patterns:
        matches = re.findall(pattern, body_text, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                match = match[0] if match[0] else (match[1] if len(match) > 1 else "")
            
            if match and re.match(r'\d+\.?\d*', str(match)):
                value = float(re.sub(r'[^0-9.]', '', str(match)))
                
                items.append({
                    "stat_date": datetime.now().strftime("%Y-%m"),
                    "indicator_name": "market_volume",
                    "value": value,
                    "unit": "亿元",
                    "change_rate": "",
                    "category": "trading_volume",
                    "instrument_type": "total_market",
                    "source_url": source_url,
                    "raw_data": f"Pattern matched: {pattern}"
                })
    
    return items


def extract_ipo_data(html: str, source_url: str) -> list[dict[str, Any]]:
    """Extract IPO and investment banking data."""
    items = []
    sel = Selector(html)
    
    body_text = sel.css("body").first.text
    
    # IPO patterns
    ipo_patterns = [
        r"(?:IPO|首次公开募|发行)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)\s*(亿|万元?)?",
        r"(?:保荐|承销)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)\s*(亿|万元?)?",
        r"ipo.*?amount[\s\S]{0,100}?(?:(\d+\.?\d*)\s*亿)?",
    ]
    
    for pattern in ipo_patterns:
        matches = re.findall(pattern, body_text, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                match = match[0] if match[0] else (match[1] if len(match) > 1 else "")
            
            if match and re.match(r'\d+\.?\d*', str(match)):
                value = float(re.sub(r'[^0-9.]', '', str(match)))
                
                # Determine unit
                has_yi = '亿' in str(match)
                unit = "亿元" if has_yi else "万元"
                
                if has_yi:
                    value = value * 10000  # Convert to yuan
                
                items.append({
                    "stat_date": datetime.now().strftime("%Y-%m"),
                    "indicator_name": "ipo_amount",
                    "value": value,
                    "unit": "元",
                    "change_rate": "",
                    "category": "investment_banking",
                    "instrument_type": "ipo_transaction",
                    "source_url": source_url,
                    "raw_data": f"IPO amount: {match}"
                })
    
    return items


def parse_trading_row(cells: list[str], source_url: str) -> dict[str, Any] | None:
    """Parse a single row from trading data table."""
    try:
        def try_float(val: str) -> float | None:
            if val and re.match(r'\d+\.?\d*', val):
                return float(re.sub(r'[^0-9.]', '', val))
            return None
        
        report_date = cells[0] if len(cells) > 0 else None
        indicator = cells[1] if len(cells) > 1 else None
        value = try_float(cells[2]) if len(cells) > 2 else None
        num_val = int(cells[3]) if len(cells) > 3 and re.match(r'\d+', cells[3]) else None
        unit = cells[4] if len(cells) > 4 else None
        
        if not report_date or not indicator:
            return None
        
        # Categorize based on indicator text
        indicator_lower = indicator.lower() if isinstance(indicator, str) else ""
        
        if "成交" in indicator_lower or "volume" in indicator_lower:
            return {
                "stat_date": report_date,
                "indicator_name": "trading_volume",
                "value": value,
                "unit": unit or "亿股",
                "change_rate": "",
                "category": "trading_volume",
                "instrument_type": indicator,
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
        elif "金额" in indicator_lower or "turnover" in indicator_lower:
            return {
                "stat_date": report_date,
                "indicator_name": "trading_turnover",
                "value": value,
                "unit": unit or "亿元",
                "change_rate": "",
                "category": "trading_turnover",
                "instrument_type": indicator,
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
        elif "笔数" in indicator_lower:
            return {
                "stat_date": report_date,
                "indicator_name": "trade_count",
                "value": float(num_val) if num_val else None,
                "unit": "笔",
                "change_rate": "",
                "category": "transaction_metrics",
                "instrument_type": indicator,
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
        else:
            return {
                "stat_date": report_date,
                "indicator_name": indicator,
                "value": value,
                "unit": unit,
                "change_rate": "",
                "category": "general_stats",
                "instrument_type": indicator,
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
            
    except Exception as e:
        logger.warning("Failed to parse trading row: %s", e)
        return None


def save_to_db(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    """Save securities statistics to database."""
    inserted = 0
    
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO securities_stats 
                (stat_date, indicator_name, value, unit, change_rate, category, 
                 instrument_type, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("stat_date"),
                item.get("indicator_name"),
                item.get("value"),
                item.get("unit"),
                item.get("change_rate"),
                item.get("category"),
                item.get("instrument_type"),
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
    logger.info("Starting SAC (Securities Association of China) Spider")
    logger.info("=" * 70)
    
    conn = init_db()
    all_items = []
    
    # Main site crawl
    logger.info("\n[1/%d] Crawling main site: %s", len(URLS), URLS[0]["url"])
    page = fetch_page(URLS[0]["url"])
    
    if page:
        logger.info("  Status: %d", page["status"])
        
        vol_data = extract_trading_volume(page["html"], page["url"])
        all_items.extend(vol_data)
        logger.info("  → Extracted %d trading volume records", len(vol_data))
        
        broker_stats = extract_broker_stats(page["html"], page["url"])
        all_items.extend(broker_stats)
        logger.info("  → Extracted %d broker statistics", len(broker_stats))
        
        market_idx = extract_market_indices(page["html"], page["url"])
        all_items.extend(market_idx)
        logger.info("  → Extracted %d market indices", len(market_idx))
        
        ipo_data = extract_ipo_data(page["html"], page["url"])
        all_items.extend(ipo_data)
        logger.info("  → Extracted %d IPO records", len(ipo_data))
    
    # Statistic pages
    for i, stat_url in enumerate(STAT_PAGES, 2):
        logger.info("\n[%d/%d] Crawling stat page: %s", i, len(URLS) + len(STAT_PAGES), stat_url)
        page = fetch_page(stat_url)
        
        if page:
            trades = extract_trading_volume(page["html"], page["url"])
            all_items.extend(trades)
            logger.info("  → Extracted %d trading records", len(trades))
        
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
    print("SAC Spider - Execution Summary")
    print("=" * 70)
    print(f"Total records extracted: {len(all_items)}")
    print(f"Database: {DB_PATH}")
    print(f"JSON output: {JSON_PATH}")
    print("=" * 70)


if __name__ == "__main__":
    main()
