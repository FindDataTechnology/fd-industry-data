#!/usr/bin/env python3
"""
Shanghai Stock Exchange (SSE) Spider

Target: https://www.sse.com.cn (Score: 90)

Extracts:
- Stock trading data (volume, turnover, price indices)
- Market overview statistics
- Listed company information
- Bond trading data
- Fund trading statistics
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
logger = logging.getLogger("sse-spider")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "sse_market_data.db"
JSON_PATH = OUTPUT_DIR / "sse_trading_data.json"

URLS = [
    {
        "url": "https://www.sse.com.cn",
        "title": "上海证券交易所",
        "score": 90,
        "category": "stock-exchange",
        "description": "Stock trading data, market indices, listed companies"
    }
]

STAT_PAGES = [
    "https://www.sse.com.cn/market/stockdata/overview/",
    "https://www.sse.com.cn/market/bonddata/overview/",
    "https://www.sse.com.cn/market/funddata/overview/",
    "https://www.sse.com.cn/market/tradingdata/kline/",
]


def init_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sse_trading (
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
            UNIQUE(stat_date, indicator_name, instrument_type, source_url)
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


def extract_market_overview(html: str, source_url: str) -> list[dict[str, Any]]:
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
            if any(kw in content for kw in ["股票", "债券", "基金", "成交", "上市"]):
                item = parse_overview_row(cells, source_url)
                if item:
                    items.append(item)
    return items


def extract_trading_volume(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)
    body_text = sel.css("body").first.text if sel.css("body") else ""

    volume_patterns = [
        r"(?:成交量|总成交量|股票成交量)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)\s*(亿|万)?(股|手)?",
        r"(?:成交额|总成交额|股票成交额)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)\s*(亿|万亿)?元?",
        r"(?:上证指数|上证综指|综合指数)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+\.?\d*)",
        r"(?:上市公司|挂牌公司)[\u4e00-\u9fa5\s]*[:：]?\s*(\d+)",
    ]

    for pattern in volume_patterns:
        matches = re.findall(pattern, body_text)
        for match in matches:
            if isinstance(match, tuple):
                value_str = match[0]
                unit_parts = [m for m in match[1:] if m]
            else:
                value_str = match
                unit_parts = []

            if value_str and re.match(r"\d+\.?\d*", value_str):
                value = float(re.sub(r"[^0-9.]", "", value_str))
                unit = "".join(unit_parts) if unit_parts else ""

                if "成交" in pattern and ("股" in unit or "手" in unit or not unit):
                    items.append({
                        "stat_date": datetime.now().strftime("%Y-%m-%d"),
                        "indicator_name": "trading_volume",
                        "value": value,
                        "unit": unit or "亿股",
                        "change_rate": "",
                        "category": "trading_volume",
                        "instrument_type": "stock",
                        "source_url": source_url,
                        "raw_data": f"Volume pattern: {pattern}"
                    })
                elif "成交" in pattern and "元" in pattern:
                    items.append({
                        "stat_date": datetime.now().strftime("%Y-%m-%d"),
                        "indicator_name": "trading_turnover",
                        "value": value,
                        "unit": unit + "元" if unit else "亿元",
                        "change_rate": "",
                        "category": "trading_turnover",
                        "instrument_type": "stock",
                        "source_url": source_url,
                        "raw_data": f"Turnover pattern: {pattern}"
                    })
                elif "指数" in pattern:
                    items.append({
                        "stat_date": datetime.now().strftime("%Y-%m-%d"),
                        "indicator_name": "sse_composite_index",
                        "value": value,
                        "unit": "点",
                        "change_rate": "",
                        "category": "price_index",
                        "instrument_type": "index",
                        "source_url": source_url,
                        "raw_data": f"Index pattern: {pattern}"
                    })
                elif "上市" in pattern:
                    items.append({
                        "stat_date": datetime.now().strftime("%Y-%m-%d"),
                        "indicator_name": "listed_companies",
                        "value": value,
                        "unit": "家",
                        "change_rate": "",
                        "category": "market_overview",
                        "instrument_type": "listed_company",
                        "source_url": source_url,
                        "raw_data": f"Listed company count: {value_str}"
                    })

    return items


def extract_bond_data(html: str, source_url: str) -> list[dict[str, Any]]:
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
            if any(kw in content for kw in ["债券", "国债", "企业债", "公司债", "可转债"]):
                item = parse_bond_row(cells, source_url)
                if item:
                    items.append(item)
    return items


def extract_fund_data(html: str, source_url: str) -> list[dict[str, Any]]:
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
            if any(kw in content for kw in ["基金", "ETF", "LOF", "封闭式"]):
                item = parse_fund_row(cells, source_url)
                if item:
                    items.append(item)
    return items


def parse_overview_row(cells: list[str], source_url: str) -> dict[str, Any] | None:
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

        if "成交" in indicator:
            return {
                "stat_date": report_date,
                "indicator_name": "trading_volume" if "量" in indicator else "trading_turnover",
                "value": value,
                "unit": unit or ("亿股" if "量" in indicator else "亿元"),
                "change_rate": "",
                "category": "trading_volume" if "量" in indicator else "trading_turnover",
                "instrument_type": indicator,
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
        elif "指数" in indicator:
            return {
                "stat_date": report_date,
                "indicator_name": "price_index",
                "value": value,
                "unit": unit or "点",
                "change_rate": "",
                "category": "price_index",
                "instrument_type": indicator,
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
                "category": "market_overview",
                "instrument_type": indicator,
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells})
            }
    except Exception as e:
        logger.warning("Failed to parse overview row: %s", e)
        return None


def parse_bond_row(cells: list[str], source_url: str) -> dict[str, Any] | None:
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

        return {
            "stat_date": report_date,
            "indicator_name": "bond_" + indicator,
            "value": value,
            "unit": unit or "亿元",
            "change_rate": "",
            "category": "bond_trading",
            "instrument_type": indicator,
            "source_url": source_url,
            "raw_data": json.dumps({"cells": cells})
        }
    except Exception as e:
        logger.warning("Failed to parse bond row: %s", e)
        return None


def parse_fund_row(cells: list[str], source_url: str) -> dict[str, Any] | None:
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

        return {
            "stat_date": report_date,
            "indicator_name": "fund_" + indicator,
            "value": value,
            "unit": unit or "亿份",
            "change_rate": "",
            "category": "fund_trading",
            "instrument_type": indicator,
            "source_url": source_url,
            "raw_data": json.dumps({"cells": cells})
        }
    except Exception as e:
        logger.warning("Failed to parse fund row: %s", e)
        return None


def save_to_db(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO sse_trading
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
    logger.info("=" * 70)
    logger.info("Starting Shanghai Stock Exchange (SSE) Spider")
    logger.info("=" * 70)

    conn = init_db()
    all_items = []

    logger.info("\n[1/%d] Crawling main site: %s", len(URLS), URLS[0]["url"])
    page = fetch_page(URLS[0]["url"])

    if page:
        logger.info("  Status: %d", page["status"])

        overview = extract_market_overview(page["html"], page["url"])
        all_items.extend(overview)
        logger.info("  -> Extracted %d market overview records", len(overview))

        volume = extract_trading_volume(page["html"], page["url"])
        all_items.extend(volume)
        logger.info("  -> Extracted %d trading volume records", len(volume))

        bonds = extract_bond_data(page["html"], page["url"])
        all_items.extend(bonds)
        logger.info("  -> Extracted %d bond records", len(bonds))

        funds = extract_fund_data(page["html"], page["url"])
        all_items.extend(funds)
        logger.info("  -> Extracted %d fund records", len(funds))

    for i, stat_url in enumerate(STAT_PAGES, 2):
        logger.info("\n[%d/%d] Crawling stat page: %s", i, len(URLS) + len(STAT_PAGES), stat_url)
        page = fetch_page(stat_url)
        if page:
            overview = extract_market_overview(page["html"], page["url"])
            all_items.extend(overview)
            logger.info("  -> Extracted %d records", len(overview))
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
    print("SSE Spider - Execution Summary")
    print("=" * 70)
    print(f"Total records extracted: {len(all_items)}")
    print(f"Database: {DB_PATH}")
    print(f"JSON output: {JSON_PATH}")
    print("=" * 70)


if __name__ == "__main__":
    main()
