#!/usr/bin/env python3
"""
China Banking Association (CBA) Spider

Target: https://www.chinabankass.org.cn (Score: 92)

Extracts:
- Banking industry statistics
- Bank asset quality data (NPL ratios, provision coverage)
- Banking sector total assets, liabilities, profits
- Industry regulatory data
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from datetime import datetime
from typing import Any

from scrapling import Fetcher, Selector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("cba-spider")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "cba_banking_data.db"
JSON_PATH = OUTPUT_DIR / "banking_statistics.json"

URLS = [
    {
        "url": "https://www.chinabankass.org.cn",
        "title": "中国银行业协会",
        "score": 92,
        "category": "banking-industry",
        "description": "Banking industry statistics, asset quality, regulatory data"
    }
]

STAT_PAGES = [
    "https://www.chinabankass.org.cn/n/2019/0425/c1004-6693.html",
    "https://www.chinabankass.org.cn/n/2019/0425/c1004-6694.html",
    "https://www.chinabankass.org.cn/xwzx/tjsj/index.shtml",
    "https://www.chinabankass.org.cn/hyfw/hydt/index.shtml",
]


def init_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS banking_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stat_date TEXT,
            indicator_name TEXT,
            value REAL,
            unit TEXT,
            change_rate TEXT,
            category TEXT,
            bank_type TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(stat_date, indicator_name, bank_type, source_url)
        )
    """)
    conn.commit()
    return conn


def fetch_page(url: str, timeout: int = 30) -> dict | None:
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


def extract_banking_stats(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue
        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if not cells or len(cells) < 3:
                continue
            content = " ".join(cells)
            if any(kw in content for kw in ["资产", "负债", "利润", "不良", "拨备", "资本"]):
                item = parse_banking_row(cells, source_url)
                if item:
                    items.append(item)
    return items


def extract_asset_quality(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)
    body_text = sel.css("body").first.text if sel.css("body") else ""

    npl_patterns = [
        r"(?:不良贷款率|不良率)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)\s*%",
        r"(?:拨备覆盖率)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)\s*%",
        r"(?:资本充足率)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)\s*%",
        r"(?:流动性比例|流动性覆盖率)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)\s*%",
    ]

    indicator_map = {
        "不良贷款率": "npl_ratio",
        "不良率": "npl_ratio",
        "拨备覆盖率": "provision_coverage",
        "资本充足率": "car_ratio",
        "流动性比例": "liquidity_ratio",
        "流动性覆盖率": "lcr_ratio",
    }

    for pattern in npl_patterns:
        matches = re.findall(pattern, body_text)
        for match in matches:
            if isinstance(match, tuple):
                match = match[0]
            if match and re.match(r"\d+\.?\d*", match):
                value = float(match)
                for cn_key, en_key in indicator_map.items():
                    if cn_key in pattern:
                        items.append({
                            "stat_date": datetime.now().strftime("%Y-%m"),
                            "indicator_name": en_key,
                            "value": value,
                            "unit": "%",
                            "change_rate": "",
                            "category": "asset_quality",
                            "bank_type": "commercial_bank",
                            "source_url": source_url,
                            "raw_data": f"{cn_key}: {value}%"
                        })
                        break
    return items


def extract_total_assets(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)
    body_text = sel.css("body").first.text if sel.css("body") else ""

    asset_patterns = [
        r"(?:银行业金融机构|商业银行|银行)(?:总资产|资产总额)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)\s*(万亿|亿)?元?",
        r"(?:净利润|利润总额)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)\s*(亿)?元?",
        r"(\d+\.?\d*)\s*万亿元",
    ]

    for pattern in asset_patterns:
        matches = re.findall(pattern, body_text)
        for match in matches:
            if isinstance(match, tuple):
                value_str = match[0]
                unit_parts = [m for m in match[1:] if m]
            else:
                value_str = match
                unit_parts = []

            if value_str and re.match(r"\d+\.?\d*", value_str):
                value = float(value_str)
                unit = "".join(unit_parts) if unit_parts else ""

                if "万亿" in unit:
                    value = value * 10000
                    unit = "亿元"

                if "利润" in pattern:
                    items.append({
                        "stat_date": datetime.now().strftime("%Y-%m"),
                        "indicator_name": "net_profit" if "净" in pattern else "total_profit",
                        "value": value,
                        "unit": unit + "元" if unit else "亿元",
                        "change_rate": "",
                        "category": "profitability",
                        "bank_type": "commercial_bank",
                        "source_url": source_url,
                        "raw_data": f"Profit: {value_str}"
                    })
                else:
                    items.append({
                        "stat_date": datetime.now().strftime("%Y-%m"),
                        "indicator_name": "total_assets",
                        "value": value,
                        "unit": unit + "元" if unit else "亿元",
                        "change_rate": "",
                        "category": "balance_sheet",
                        "bank_type": "commercial_bank",
                        "source_url": source_url,
                        "raw_data": f"Total assets: {value_str}"
                    })
    return items


def parse_banking_row(cells: list[str], source_url: str) -> dict[str, Any] | None:
    try:
        def try_float(val: str) -> float | None:
            if val and re.match(r"[\d,]+\.?\d*", val.replace(",", "")):
                return float(re.sub(r"[^0-9.]", "", val))
            return None

        report_date = cells[0] if len(cells) > 0 else None
        indicator = cells[1] if len(cells) > 1 else None
        value = try_float(cells[2]) if len(cells) > 2 else None
        unit = cells[3] if len(cells) > 3 else None

        if not indicator or value is None:
            return None

        indicator_lower = indicator.lower()

        if "资产" in indicator:
            return {
                "stat_date": report_date,
                "indicator_name": "total_assets",
                "value": value,
                "unit": unit or "亿元",
                "change_rate": "",
                "category": "balance_sheet",
                "bank_type": indicator,
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
        elif "不良" in indicator or "npl" in indicator_lower:
            return {
                "stat_date": report_date,
                "indicator_name": "npl_ratio",
                "value": value,
                "unit": unit or "%",
                "change_rate": "",
                "category": "asset_quality",
                "bank_type": indicator,
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
        elif "利润" in indicator:
            return {
                "stat_date": report_date,
                "indicator_name": "profit",
                "value": value,
                "unit": unit or "亿元",
                "change_rate": "",
                "category": "profitability",
                "bank_type": indicator,
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
        else:
            return {
                "stat_date": report_date,
                "indicator_name": indicator,
                "value": value,
                "unit": unit or "",
                "change_rate": "",
                "category": "general_banking",
                "bank_type": indicator,
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
    except Exception as e:
        logger.warning("Failed to parse banking row: %s", e)
        return None


def save_to_db(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO banking_stats
                (stat_date, indicator_name, value, unit, change_rate, category,
                 bank_type, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("stat_date"),
                item.get("indicator_name"),
                item.get("value"),
                item.get("unit"),
                item.get("change_rate"),
                item.get("category"),
                item.get("bank_type"),
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
    from pathlib import Path
    logger.info("=" * 70)
    logger.info("Starting China Banking Association (CBA) Spider")
    logger.info("=" * 70)

    conn = init_db()
    all_items = []

    logger.info("\n[1/%d] Crawling main site: %s", len(URLS), URLS[0]["url"])
    page = fetch_page(URLS[0]["url"])

    if page:
        logger.info("  Status: %d", page["status"])

        stats = extract_banking_stats(page["html"], page["url"])
        all_items.extend(stats)
        logger.info("  -> Extracted %d banking statistics", len(stats))

        quality = extract_asset_quality(page["html"], page["url"])
        all_items.extend(quality)
        logger.info("  -> Extracted %d asset quality records", len(quality))

        assets = extract_total_assets(page["html"], page["url"])
        all_items.extend(assets)
        logger.info("  -> Extracted %d total asset records", len(assets))

    for i, stat_url in enumerate(STAT_PAGES, 2):
        logger.info("\n[%d/%d] Crawling stat page: %s", i, len(URLS) + len(STAT_PAGES), stat_url)
        page = fetch_page(stat_url)
        if page:
            stats = extract_banking_stats(page["html"], page["url"])
            all_items.extend(stats)
            logger.info("  -> Extracted %d records", len(stats))
        time.sleep(2)

    if all_items:
        inserted = save_to_db(conn, all_items)
        logger.info("\nSaved %d records to database", inserted)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(all_items, f, ensure_ascii=False, indent=2)
        logger.info("Exported %d records to JSON: %s", len(all_items), JSON_PATH)
    else:
        logger.warning("No data extracted")

    conn.close()

    print("\n" + "=" * 70)
    print("CBA Spider - Execution Summary")
    print("=" * 70)
    print(f"Total records extracted: {len(all_items)}")
    print(f"Database: {DB_PATH}")
    print(f"JSON output: {JSON_PATH}")
    print("=" * 70)


if __name__ == "__main__":
    main()
