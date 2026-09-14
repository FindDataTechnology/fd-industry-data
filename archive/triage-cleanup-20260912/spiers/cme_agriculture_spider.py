#!/usr/bin/env python3
"""
Securities Association of China (SAC) Spider

Target: https://www.sac.net.cn (Score: 95)

Extracts:
- Securities trading volume data
- Market statistics and analysis
- Brokerage industry reports
- Investor statistics
- IPO and bond issuance data
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

# Target URL - SAC
URLS = [
    {
        "url": "https://www.sac.net.cn",
        "title": "中国证券业协会",
        "score": 95,
        "category": "securities-trading",
        "description": "Securities trading volume, market stats, brokerage reports"
    }
]

# Statistic pages
STAT_PAGES = [
    "http://www.sac.net.cn/xyxw/",           # 行业动态
    "http://www.sac.net.cn/tjxx/",           # 统计数据
    "http://www.sac.net.cn/syjcx/",          # 研究分析
    "http://www.sac.net.cn/hyzqyxz/",        # 行业运行
]


def init_db() -> sqlite3.Connection:
    """Initialize SQLite database with securities trading schema."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS securities_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stat_date TEXT,
            indicator_type TEXT,
            security_type TEXT,
            value REAL,
            volume REAL,
            unit TEXT,
            change_rate TEXT,
            institution_count INTEGER,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(stat_date, indicator_type, source_url)
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
    """Extract securities trading volume statistics."""
    items = []
    sel = Selector(html)
    
    # Look for table-based statistics
    for table in sel.css("table"):
        rows = table.css("tr")
        
        if len(rows) < 2:
            continue
        
        headers = [th.text.strip() for th in rows[0].css("th")]
        
        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            
            if not cells or len(cells) < 3:
                continue
            
            # Check if contains trading-related data
            if any(kw in ' '.join(cells) for kw in ["成交", "交易", "交易额", "股票", "债券", "金额"]):
                item = parse_trading_row(cells, source_url)
                if item:
                    items.append(item)
    
    return items


def extract_investor_stats(html: str, source_url: str) -> list[dict[str, Any]]:
    """Extract investor account statistics."""
    items = []
    sel = Selector(html)
    
    body_text = sel.css("body").first.text
    
    # Investor count patterns
    investor_patterns = [
        r"(?:新增投资者|投资者数量|开户数)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+)",
        r"(\d+)\s*(户 | 万人 | 家)",
        r"investors?[\s\S]{0,100}?(?:(\d+)|(\d+,?\d*))",
    ]
    
    for pattern in investor_patterns:
        matches = re.findall(pattern, body_text)
        for match in matches:
            if isinstance(match, tuple):
                match = next(m for m in match if m)
            
            if match and re.match(r'\d+', match):
                count = int(re.sub(r'[^0-9]', '', match))
                
                items.append({
                    "stat_date": datetime.now().strftime("%Y-%m"),
                    "indicator_type": "investor_accounts",
                    "security_type": "total",
                    "value": float(count),
                    "unit": "户",
                    "source_url": source_url,
                    "raw_data": f"Investor count: {match}"
                })
    
    return items


def extract_ipo_bond_data(html: str, source_url: str) -> list[dict[str, Any]]:
    """Extract IPO and bond issuance statistics."""
    items = []
    sel = Selector(html)
    
    body_text = sel.css("body").first.text
    
    # IPO/Bond patterns
    issue_patterns = [
        r"(?:IPO|首发|发行)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)\s*(亿元?|亿股)?",
        r"(?:债券 | bond)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)\s*(亿元?|万亿元?)?",
        r"issuance.*?volume[\s\S]{0,100}?(\d+\.?\d*)\s*(亿|m)\.??",
    ]
    
    for pattern in issue_patterns:
        matches = re.findall(pattern, body_text, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                match = match[0] if match[0] else match[1] if len(match) > 1 else None
            
            if match and re.match(r'\d+\.?\d*', str(match)):
                value = float(re.sub(r'[^0-9.]', '', str(match)))
                
                # Detect type from context
                if "bond" in pattern.lower() or "债券" in str(match):
                    items.append({
                        "stat_date": datetime.now().strftime("%Y-%m"),
                        "indicator_type": "bond_issuance",
                        "security_type": "corporate_bond",
                        "value": value,
                        "unit": "亿元",
                        "source_url": source_url,
                        "raw_data": f"Bond issuance: {match}"
                    })
                else:
                    items.append({
                        "stat_date": datetime.now().strftime("%Y-%m"),
                        "indicator_type": "ipo_issuance",
                        "security_type": "stock",
                        "value": value,
                        "unit": "亿元",
                        "source_url": source_url,
                        "raw_data": f"IPO issuance: {match}"
                    })
    
    return items


def extract_brk_statistics(html: str, source_url: str) -> list[dict[str, Any]]:
    """Extract brokerage industry statistics."""
    items = []
    sel = Selector(html)
    
    # Table-based brokerage stats
    for table in sel.css("table"):
        rows = table.css("tr")
        
        if len(rows) < 2:
            continue
        
        header_cells = rows[0].css("th, td")
        headers = [cell.text.strip() for cell in header_cells if cell.text.strip()]
        
        for row in rows[1:]:
            cells = [cell.text.strip() for cell in row.css("td")]
            
            if not cells:
                continue
            
            # Brokerage-related keywords
            if any(kw in ' '.join(cells) for kw in ["券商", "证券公司", "营业收入", "净利润"]):
                item = parse_brk_row(cells, source_url)
                if item:
                    items.append(item)
    
    return items


def parse_trading_row(cells: list[str], source_url: str) -> dict[str, Any] | None:
    """Parse a single row from trading statistics table."""
    try:
        def try_float(val: str) -> float | None:
            if val and re.match(r'\d+\.?\d*', val):
                return float(re.sub(r'[^0-9.]', '', val))
            return None
        
        report_date = cells[0] if len(cells) > 0 else None
        indicator = cells[1] if len(cells) > 1 else None
        value = try_float(cells[2]) if len(cells) > 2 else None
        vol = try_float(cells[3]) if len(cells) > 3 else None
        unit = cells[4] if len(cells) > 4 else None
        
        if not report_date or not indicator:
            return None
        
        indicator_lower = indicator.lower() if isinstance(indicator, str) else ""
        
        # Categorize by indicator type
        if "成交" in indicator_lower or "trading" in indicator_lower or "volume" in indicator_lower:
            return {
                "stat_date": report_date,
                "indicator_type": "trading_volume",
                "security_type": indicator,
                "volume": value,
                "unit": unit or "元",
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
        elif "金额" in indicator_lower or "amount" in indicator_lower:
            return {
                "stat_date": report_date,
                "indicator_type": "trading_amount",
                "security_type": indicator,
                "value": value,
                "unit": unit or "元",
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
        elif "笔数" in indicator_lower or "transactions" in indicator_lower:
            return {
                "stat_date": report_date,
                "indicator_type": "transaction_count",
                "security_type": indicator,
                "value": value,
                "unit": unit or "笔",
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
        else:
            return {
                "stat_date": report_date,
                "indicator_type": "securities_stats",
                "security_type": indicator,
                "value": value,
                "unit": unit,
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
            
    except Exception as e:
        logger.warning("Failed to parse trading row: %s", e)
        return None


def parse_brk_row(cells: list[str], source_url: str) -> dict[str, Any] | None:
    """Parse a single row from brokerage statistics table."""
    try:
        def try_float(val: str) -> float | None:
            if val and re.match(r'\d+\.?\d*', val):
                return float(re.sub(r'[^0-9.]', '', val))
            return None
        
        period = cells[0] if len(cells) > 0 else None
        indicator = cells[1] if len(cells) > 1 else None
        value = try_float(cells[2]) if len(cells) > 2 else None
        change = cells[3] if len(cells) > 3 else None
        
        if not period or not indicator:
            return None
        
        indicator_lower = indicator.lower() if isinstance(indicator, str) else ""
        
        if "收入" in indicator_lower or "revenue" in indicator_lower:
            return {
                "stat_date": period,
                "indicator_type": "brokerage_revenue",
                "security_type": indicator,
                "value": value,
                "change_rate": change,
                "unit": "亿元",
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
        elif "利润" in indicator_lower or "profit" in indicator_lower or "earnings" in indicator_lower:
            return {
                "stat_date": period,
                "indicator_type": "brokerage_profit",
                "security_type": indicator,
                "value": value,
                "change_rate": change,
                "unit": "亿元",
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
        else:
            return {
                "stat_date": period,
                "indicator_type": "brokerage_stats",
                "security_type": indicator,
                "value": value,
                "unit": "亿元",
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
            
    except Exception as e:
        logger.warning("Failed to parse brokerage row: %s", e)
        return None


def save_to_db(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    """Save securities statistics to database."""
    inserted = 0
    
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO securities_stats 
                (stat_date, indicator_type, security_type, value, volume, unit, change_rate,
                 institution_count, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("stat_date"),
                item.get("indicator_type"),
                item.get("security_type"),
                item.get("value"),
                item.get("volume"),
                item.get("unit"),
                "",  # change_rate - would need historical comparison
                "",  # institution_count - typically aggregated elsewhere
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
        
        trades = extract_trading_volume(page["html"], page["url"])
        all_items.extend(trades)
        logger.info("  → Extracted %d trading records", len(trades))
        
        brokers = extract_brk_statistics(page["html"], page["url"])
        all_items.extend(brokers)
        logger.info("  → Extracted %d brokerage records", len(brokers))
        
        investors = extract_investor_stats(page["html"], page["url"])
        all_items.extend(investors)
        logger.info("  → Extracted %d investor records", len(investors))
        
        ipos = extract_ipo_bond_data(page["html"], page["url"])
        all_items.extend(ipos)
        logger.info("  → Extracted %d IPO/bond records", len(ipos))
    
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
