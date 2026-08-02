#!/usr/bin/env python3
"""
China Insurance Association (CIA) Spider

Target: https://www.iachina.cn (Score: 90)

Extracts:
- Insurance market data (premium income, claims, assets)
- Insurance company statistics
- Industry solvency ratios
- Insurance product distribution data
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
logger = logging.getLogger("cia-spider")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "cia_insurance_data.db"
JSON_PATH = OUTPUT_DIR / "insurance_market_data.json"

URLS = [
    {
        "url": "https://www.iachina.cn",
        "title": "中国保险行业协会",
        "score": 90,
        "category": "insurance-industry",
        "description": "Insurance market data, premium income, claims, solvency"
    }
]

STAT_PAGES = [
    "https://www.iachina.cn/QK/QKList.aspx",
    "https://www.iachina.cn/col/col76/index.html",
    "https://www.iachina.cn/col/col78/index.html",
    "https://www.iachina.cn/col/col80/index.html",
]


def init_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS insurance_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stat_date TEXT,
            indicator_name TEXT,
            value REAL,
            unit TEXT,
            change_rate TEXT,
            category TEXT,
            insurance_type TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(stat_date, indicator_name, insurance_type, source_url)
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


def extract_premium_data(html: str, source_url: str) -> list[dict[str, Any]]:
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
            if any(kw in content for kw in ["保费", "原保险", "保费收入", "保险收入"]):
                item = parse_premium_row(cells, source_url)
                if item:
                    items.append(item)
    return items


def extract_market_overview(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)
    body_text = sel.css("body").first.text if sel.css("body") else ""

    market_patterns = [
        r"(?:保险资产|总资产|行业总资产)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)\s*(万亿|亿)?元?",
        r"(?:保费收入|原保费收入)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)\s*(万亿|亿)?元?",
        r"(?:赔付|赔款支出|给付)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)\s*(亿)?元?",
        r"(?:保险公司|保险机构)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+)\s*家?",
    ]

    for pattern in market_patterns:
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

                if "资产" in pattern:
                    items.append({
                        "stat_date": datetime.now().strftime("%Y-%m"),
                        "indicator_name": "total_assets",
                        "value": value,
                        "unit": unit + "元" if unit else "亿元",
                        "change_rate": "",
                        "category": "balance_sheet",
                        "insurance_type": "industry_total",
                        "source_url": source_url,
                        "raw_data": f"Insurance assets: {value_str}"
                    })
                elif "保费" in pattern:
                    items.append({
                        "stat_date": datetime.now().strftime("%Y-%m"),
                        "indicator_name": "premium_income",
                        "value": value,
                        "unit": unit + "元" if unit else "亿元",
                        "change_rate": "",
                        "category": "premium",
                        "insurance_type": "total",
                        "source_url": source_url,
                        "raw_data": f"Premium income: {value_str}"
                    })
                elif "赔付" in pattern or "赔款" in pattern:
                    items.append({
                        "stat_date": datetime.now().strftime("%Y-%m"),
                        "indicator_name": "claims_paid",
                        "value": value,
                        "unit": unit + "元" if unit else "亿元",
                        "change_rate": "",
                        "category": "claims",
                        "insurance_type": "total",
                        "source_url": source_url,
                        "raw_data": f"Claims paid: {value_str}"
                    })
                elif "公司" in pattern or "机构" in pattern:
                    items.append({
                        "stat_date": datetime.now().strftime("%Y-%m"),
                        "indicator_name": "insurance_companies",
                        "value": value,
                        "unit": "家",
                        "change_rate": "",
                        "category": "institution_count",
                        "insurance_type": "all",
                        "source_url": source_url,
                        "raw_data": f"Insurance companies: {value_str}"
                    })
    return items


def extract_solvency_data(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)
    body_text = sel.css("body").first.text if sel.css("body") else ""

    solvency_patterns = [
        r"(?:偿付能力充足率|综合偿付能力)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)\s*%",
        r"(?:核心偿付能力)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)\s*%",
    ]

    for pattern in solvency_patterns:
        matches = re.findall(pattern, body_text)
        for match in matches:
            if isinstance(match, tuple):
                match = match[0]
            if match and re.match(r"\d+\.?\d*", match):
                value = float(match)
                indicator = "comprehensive_solvency" if "综合" in pattern else "core_solvency" if "核心" in pattern else "solvency_ratio"
                items.append({
                    "stat_date": datetime.now().strftime("%Y-%m"),
                    "indicator_name": indicator,
                    "value": value,
                    "unit": "%",
                    "change_rate": "",
                    "category": "solvency",
                    "insurance_type": "industry_avg",
                    "source_url": source_url,
                    "raw_data": f"Solvency: {value}%"
                })
    return items


def extract_product_distribution(html: str, source_url: str) -> list[dict[str, Any]]:
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
            if any(kw in content for kw in ["寿险", "财险", "健康险", "意外险", "再保险"]):
                item = parse_product_row(cells, source_url)
                if item:
                    items.append(item)
    return items


def parse_premium_row(cells: list[str], source_url: str) -> dict[str, Any] | None:
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

        ins_type = "life" if "寿" in indicator else "property" if "财" in indicator else "health" if "健康" in indicator else "accident" if "意外" in indicator else "total"

        return {
            "stat_date": report_date,
            "indicator_name": "premium_income",
            "value": value,
            "unit": unit or "亿元",
            "change_rate": "",
            "category": "premium",
            "insurance_type": ins_type,
            "source_url": source_url,
            "raw_data": json.dumps({"cells": cells})
        }
    except Exception as e:
        logger.warning("Failed to parse premium row: %s", e)
        return None


def parse_product_row(cells: list[str], source_url: str) -> dict[str, Any] | None:
    try:
        def try_float(val: str) -> float | None:
            if val and re.match(r"[\d,]+\.?\d*", val.replace(",", "")):
                return float(re.sub(r"[^0-9.]", "", val))
            return None

        report_date = cells[0] if len(cells) > 0 else None
        product_type = cells[1] if len(cells) > 1 else None
        value = try_float(cells[2]) if len(cells) > 2 else None
        unit = cells[3] if len(cells) > 3 else None

        if not product_type or value is None:
            return None

        ins_type = "life" if "寿" in product_type else "property" if "财" in product_type else "health" if "健康" in product_type else "accident" if "意外" in product_type else "other"

        return {
            "stat_date": report_date,
            "indicator_name": "product_premium",
            "value": value,
            "unit": unit or "亿元",
            "change_rate": "",
            "category": "product_distribution",
            "insurance_type": ins_type,
            "source_url": source_url,
            "raw_data": json.dumps({"cells": cells})
        }
    except Exception as e:
        logger.warning("Failed to parse product row: %s", e)
        return None


def save_to_db(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO insurance_stats
                (stat_date, indicator_name, value, unit, change_rate, category,
                 insurance_type, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("stat_date"),
                item.get("indicator_name"),
                item.get("value"),
                item.get("unit"),
                item.get("change_rate"),
                item.get("category"),
                item.get("insurance_type"),
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
    logger.info("=" * 70)
    logger.info("Starting China Insurance Association (CIA) Spider")
    logger.info("=" * 70)

    conn = init_db()
    all_items = []

    logger.info("\n[1/%d] Crawling main site: %s", len(URLS), URLS[0]["url"])
    page = fetch_page(URLS[0]["url"])

    if page:
        logger.info("  Status: %d", page["status"])

        premium = extract_premium_data(page["html"], page["url"])
        all_items.extend(premium)
        logger.info("  -> Extracted %d premium records", len(premium))

        market = extract_market_overview(page["html"], page["url"])
        all_items.extend(market)
        logger.info("  -> Extracted %d market overview records", len(market))

        solvency = extract_solvency_data(page["html"], page["url"])
        all_items.extend(solvency)
        logger.info("  -> Extracted %d solvency records", len(solvency))

        products = extract_product_distribution(page["html"], page["url"])
        all_items.extend(products)
        logger.info("  -> Extracted %d product distribution records", len(products))

    for i, stat_url in enumerate(STAT_PAGES, 2):
        logger.info("\n[%d/%d] Crawling stat page: %s", i, len(URLS) + len(STAT_PAGES), stat_url)
        page = fetch_page(stat_url)
        if page:
            premium = extract_premium_data(page["html"], page["url"])
            all_items.extend(premium)
            logger.info("  -> Extracted %d records", len(premium))
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
    print("CIA Spider - Execution Summary")
    print("=" * 70)
    print(f"Total records extracted: {len(all_items)}")
    print(f"Database: {DB_PATH}")
    print(f"JSON output: {JSON_PATH}")
    print("=" * 70)


if __name__ == "__main__":
    main()
