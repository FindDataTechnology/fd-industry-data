#!/usr/bin/env python3
"""
Eastmoney Choice Spider - 东方财富Choice金融终端数据爬虫

Target: https://choice.eastmoney.com, https://data.eastmoney.com
Data: Stock market data, financial statements, market analysis

Authentication Requirements:
  - Choice terminal requires paid subscription
  - data.eastmoney.com has extensive FREE public data:
    - Stock quotes (A-share, HK, US)
    - Financial statements (balance sheet, income, cash flow)
    - Fund data (NAV, rankings, holdings)
    - Bond data
    - Macro economic data
    - Industry data
  - No API key needed for web scraping public pages
  - Some advanced analytics require Choice subscription

Publicly Accessible (data.eastmoney.com):
  - A-share real-time/delayed quotes
  - Historical K-line data
  - Financial statement summaries
  - Fund rankings and NAV
  - Index data
  - Sector/industry performance
  - Margin trading data
  - Block trades (大宗交易)
  - Shareholder information

Premium (Choice Subscription Required):
  - Advanced stock screening
  - Custom financial models
  - Analyst consensus
  - Institutional research database
  - Excel plugin data feed

Architecture:
  - Fetcher with browser impersonation
  - Rate limiting (2s delay)
  - SQLite + JSON output
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scrapling import Fetcher, Selector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("eastmoney_choice")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "eastmoney_choice.db"
JSON_PATH = OUTPUT_DIR / "eastmoney_data.json"

STOCK_URLS = [
    "https://data.eastmoney.com/stockdata/.html",
    "https://quote.eastmoney.com/center/board.html",
    "https://data.eastmoney.com/bkzj/hy.html",
]

FINANCIAL_URLS = [
    "https://data.eastmoney.com/bbsj/202412.html",
    "https://data.eastmoney.com/yjbb/202412.html",
]

FUND_URLS = [
    "https://fund.eastmoney.com/data/fundranking.html",
    "https://data.eastmoney.com/fund/etf.html",
]

INDEX_URLS = [
    "https://quote.eastmoney.com/center/zzzs.html",
    "https://data.eastmoney.com/zlsj/",
]

MACRO_URLS = [
    "https://data.eastmoney.com/cjsj/gdp.html",
    "https://data.eastmoney.com/cjsj/cpi.html",
    "https://data.eastmoney.com/cjsj/pmi.html",
    "https://data.eastmoney.com/cjsj/hb.html",
]


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT,
            stock_name TEXT,
            price REAL,
            change_value REAL,
            change_pct REAL,
            volume REAL,
            turnover REAL,
            amplitude REAL,
            high REAL,
            low REAL,
            open_price REAL,
            prev_close REAL,
            market_cap REAL,
            circulating_cap REAL,
            pe_ratio REAL,
            pb_ratio REAL,
            trade_date TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(stock_code, trade_date, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS financial_statements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT,
            stock_name TEXT,
            report_date TEXT,
            eps REAL,
            bvps REAL,
            roe REAL,
            revenue REAL,
            net_profit REAL,
            profit_yoy REAL,
            gross_margin REAL,
            net_margin REAL,
            debt_ratio REAL,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(stock_code, report_date, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fund_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fund_code TEXT,
            fund_name TEXT,
            fund_type TEXT,
            manager TEXT,
            nav REAL,
            accumulated_nav REAL,
            return_1d REAL,
            return_1w REAL,
            return_1m REAL,
            return_3m REAL,
            return_6m REAL,
            return_1y REAL,
            return_2y REAL,
            fund_scale REAL,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(fund_code, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_code TEXT,
            index_name TEXT,
            index_value REAL,
            change_value REAL,
            change_pct REAL,
            volume REAL,
            turnover REAL,
            trade_date TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(index_code, trade_date, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS macro_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            indicator TEXT NOT NULL,
            value TEXT,
            unit TEXT,
            release_date TEXT,
            previous_value TEXT,
            yoy_change TEXT,
            region TEXT DEFAULT '中国',
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(indicator, release_date, source_url)
        )
    """)
    conn.commit()
    return conn


def fetch_page(url: str, fetcher: Fetcher) -> dict | None:
    try:
        logger.info("Fetching: %s", url)
        response = fetcher.get(url, timeout=30, stealthy_headers=True)
        if response.status != 200:
            logger.warning("HTTP %d for %s", response.status, url)
            return None
        return {"url": url, "html": response.html, "status": response.status}
    except Exception as e:
        logger.error("Fetch failed for %s: %s", url, e)
        return None


def extract_stock_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []
    now = datetime.now(timezone.utc).isoformat()

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue
        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)
        if not any(kw in header_text for kw in ["代码", "名称", "最新价", "涨跌幅", "成交量"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            item = {
                "stock_code": cells[0],
                "stock_name": cells[1] if len(cells) > 1 else "",
                "price": parse_float(cells[2]) if len(cells) > 2 else None,
                "change_value": parse_float(cells[3]) if len(cells) > 3 else None,
                "change_pct": parse_float(cells[4]) if len(cells) > 4 else None,
                "volume": parse_float(cells[5]) if len(cells) > 5 else None,
                "turnover": parse_float(cells[6]) if len(cells) > 6 else None,
                "amplitude": parse_float(cells[7]) if len(cells) > 7 else None,
                "high": parse_float(cells[8]) if len(cells) > 8 else None,
                "low": parse_float(cells[9]) if len(cells) > 9 else None,
                "open_price": parse_float(cells[10]) if len(cells) > 10 else None,
                "prev_close": parse_float(cells[11]) if len(cells) > 11 else None,
                "market_cap": parse_float(cells[12]) if len(cells) > 12 else None,
                "circulating_cap": parse_float(cells[13]) if len(cells) > 13 else None,
                "pe_ratio": parse_float(cells[14]) if len(cells) > 14 else None,
                "pb_ratio": parse_float(cells[15]) if len(cells) > 15 else None,
                "trade_date": "",
                "source_url": url,
                "scraped_at": now,
            }
            items.append(item)

    return items


def extract_financial_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []
    now = datetime.now(timezone.utc).isoformat()

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue
        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)
        if not any(kw in header_text for kw in ["代码", "每股收益", "净利润", "EPS", "营收"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            item = {
                "stock_code": cells[0],
                "stock_name": cells[1] if len(cells) > 1 else "",
                "report_date": extract_date(cells[2]) if len(cells) > 2 else "",
                "eps": parse_float(cells[3]) if len(cells) > 3 else None,
                "bvps": parse_float(cells[4]) if len(cells) > 4 else None,
                "roe": parse_float(cells[5]) if len(cells) > 5 else None,
                "revenue": parse_float(cells[6]) if len(cells) > 6 else None,
                "net_profit": parse_float(cells[7]) if len(cells) > 7 else None,
                "profit_yoy": parse_float(cells[8]) if len(cells) > 8 else None,
                "gross_margin": parse_float(cells[9]) if len(cells) > 9 else None,
                "net_margin": parse_float(cells[10]) if len(cells) > 10 else None,
                "debt_ratio": parse_float(cells[11]) if len(cells) > 11 else None,
                "source_url": url,
                "scraped_at": now,
            }
            items.append(item)

    return items


def extract_fund_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []
    now = datetime.now(timezone.utc).isoformat()

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue
        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)
        if not any(kw in header_text for kw in ["基金", "净值", "排名", "fund", "NAV"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            item = {
                "fund_code": cells[0],
                "fund_name": cells[1] if len(cells) > 1 else "",
                "fund_type": cells[2] if len(cells) > 2 else "",
                "manager": cells[3] if len(cells) > 3 else "",
                "nav": parse_float(cells[4]) if len(cells) > 4 else None,
                "accumulated_nav": parse_float(cells[5]) if len(cells) > 5 else None,
                "return_1d": parse_float(cells[6]) if len(cells) > 6 else None,
                "return_1w": parse_float(cells[7]) if len(cells) > 7 else None,
                "return_1m": parse_float(cells[8]) if len(cells) > 8 else None,
                "return_3m": parse_float(cells[9]) if len(cells) > 9 else None,
                "return_6m": parse_float(cells[10]) if len(cells) > 10 else None,
                "return_1y": parse_float(cells[11]) if len(cells) > 11 else None,
                "return_2y": parse_float(cells[12]) if len(cells) > 12 else None,
                "fund_scale": parse_float(cells[13]) if len(cells) > 13 else None,
                "source_url": url,
                "scraped_at": now,
            }
            items.append(item)

    return items


def extract_index_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []
    now = datetime.now(timezone.utc).isoformat()

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue
        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)
        if not any(kw in header_text for kw in ["指数", "点位", "index", "收盘"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            item = {
                "index_code": cells[0],
                "index_name": cells[1] if len(cells) > 1 else "",
                "index_value": parse_float(cells[2]) if len(cells) > 2 else None,
                "change_value": parse_float(cells[3]) if len(cells) > 3 else None,
                "change_pct": parse_float(cells[4]) if len(cells) > 4 else None,
                "volume": parse_float(cells[5]) if len(cells) > 5 else None,
                "turnover": parse_float(cells[6]) if len(cells) > 6 else None,
                "trade_date": "",
                "source_url": url,
                "scraped_at": now,
            }
            items.append(item)

    return items


def extract_macro_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []
    now = datetime.now(timezone.utc).isoformat()

    indicator = "unknown"
    if "gdp" in url.lower():
        indicator = "GDP"
    elif "cpi" in url.lower():
        indicator = "CPI"
    elif "pmi" in url.lower():
        indicator = "PMI"
    elif "hb" in url.lower() or "money" in url.lower():
        indicator = "货币供应量"

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            item = {
                "indicator": indicator,
                "value": cells[0],
                "unit": detect_unit(cells[0]),
                "release_date": extract_date(cells[1]) if len(cells) > 1 else "",
                "previous_value": cells[2] if len(cells) > 2 else "",
                "yoy_change": cells[3] if len(cells) > 3 else "",
                "region": "中国",
                "source_url": url,
                "scraped_at": now,
            }
            items.append(item)

    return items


def extract_date(text: str) -> str:
    patterns = [
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{4}年\d{1,2}月\d{1,2}日)",
        r"(\d{4}/\d{2}/\d{2})",
        r"(\d{4}\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def detect_unit(text: str) -> str:
    if not text:
        return ""
    if "%" in text:
        return "%"
    if "万亿" in text:
        return "万亿元"
    if "亿" in text:
        return "亿元"
    if "万" in text:
        return "万元"
    return ""


def parse_float(text: str) -> float | None:
    if not text:
        return None
    cleaned = text.replace(",", "").replace("，", "").replace("%", "").replace("--", "").strip()
    if not cleaned or cleaned == "-":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def save_to_sqlite(conn: sqlite3.Connection, table: str, items: list[dict]) -> int:
    inserted = 0
    for item in items:
        try:
            if table == "stock_data":
                conn.execute(
                    """INSERT OR REPLACE INTO stock_data
                    (stock_code, stock_name, price, change_value, change_pct, volume,
                     turnover, amplitude, high, low, open_price, prev_close,
                     market_cap, circulating_cap, pe_ratio, pb_ratio,
                     trade_date, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("stock_code", ""), item.get("stock_name", ""),
                     item.get("price"), item.get("change_value"), item.get("change_pct"),
                     item.get("volume"), item.get("turnover"), item.get("amplitude"),
                     item.get("high"), item.get("low"), item.get("open_price"),
                     item.get("prev_close"), item.get("market_cap"),
                     item.get("circulating_cap"), item.get("pe_ratio"),
                     item.get("pb_ratio"), item.get("trade_date", ""),
                     item["source_url"], item["scraped_at"]),
                )
            elif table == "financial_statements":
                conn.execute(
                    """INSERT OR REPLACE INTO financial_statements
                    (stock_code, stock_name, report_date, eps, bvps, roe, revenue,
                     net_profit, profit_yoy, gross_margin, net_margin, debt_ratio,
                     source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("stock_code", ""), item.get("stock_name", ""),
                     item.get("report_date", ""), item.get("eps"), item.get("bvps"),
                     item.get("roe"), item.get("revenue"), item.get("net_profit"),
                     item.get("profit_yoy"), item.get("gross_margin"),
                     item.get("net_margin"), item.get("debt_ratio"),
                     item["source_url"], item["scraped_at"]),
                )
            elif table == "fund_data":
                conn.execute(
                    """INSERT OR REPLACE INTO fund_data
                    (fund_code, fund_name, fund_type, manager, nav, accumulated_nav,
                     return_1d, return_1w, return_1m, return_3m, return_6m,
                     return_1y, return_2y, fund_scale, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("fund_code", ""), item.get("fund_name", ""),
                     item.get("fund_type", ""), item.get("manager", ""),
                     item.get("nav"), item.get("accumulated_nav"),
                     item.get("return_1d"), item.get("return_1w"),
                     item.get("return_1m"), item.get("return_3m"),
                     item.get("return_6m"), item.get("return_1y"),
                     item.get("return_2y"), item.get("fund_scale"),
                     item["source_url"], item["scraped_at"]),
                )
            elif table == "index_data":
                conn.execute(
                    """INSERT OR REPLACE INTO index_data
                    (index_code, index_name, index_value, change_value, change_pct,
                     volume, turnover, trade_date, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("index_code", ""), item.get("index_name", ""),
                     item.get("index_value"), item.get("change_value"),
                     item.get("change_pct"), item.get("volume"),
                     item.get("turnover"), item.get("trade_date", ""),
                     item["source_url"], item["scraped_at"]),
                )
            elif table == "macro_data":
                conn.execute(
                    """INSERT OR REPLACE INTO macro_data
                    (indicator, value, unit, release_date, previous_value,
                     yoy_change, region, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["indicator"], item.get("value", ""),
                     item.get("unit", ""), item.get("release_date", ""),
                     item.get("previous_value", ""), item.get("yoy_change", ""),
                     item.get("region", "中国"), item["source_url"], item["scraped_at"]),
                )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error (%s): %s", table, e)
    conn.commit()
    return inserted


def save_to_json(items: list[dict], json_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def get_eastmoney_data(categories: list[str] | None = None) -> dict[str, list[dict]]:
    """Fetch public data from Eastmoney Choice / data.eastmoney.com.

    Args:
        categories: List of categories to fetch.
            Available: stock, financial, fund, index, macro.
            Default: all.

    Returns:
        Dict with keys: stock_data, financial_statements, fund_data, index_data, macro_data.
    """
    if categories is None:
        categories = ["stock", "financial", "fund", "index", "macro"]

    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()
    results = {
        "stock_data": [],
        "financial_statements": [],
        "fund_data": [],
        "index_data": [],
        "macro_data": [],
    }

    if "stock" in categories:
        logger.info("=== Fetching Stock Data ===")
        for url in STOCK_URLS:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_stock_data(page["html"], url)
                results["stock_data"].extend(items)
                save_to_sqlite(conn, "stock_data", items)
                logger.info("  Extracted %d stock records from %s", len(items), url)
            time.sleep(2)

    if "financial" in categories:
        logger.info("=== Fetching Financial Statements ===")
        for url in FINANCIAL_URLS:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_financial_data(page["html"], url)
                results["financial_statements"].extend(items)
                save_to_sqlite(conn, "financial_statements", items)
                logger.info("  Extracted %d financial records from %s", len(items), url)
            time.sleep(2)

    if "fund" in categories:
        logger.info("=== Fetching Fund Data ===")
        for url in FUND_URLS:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_fund_data(page["html"], url)
                results["fund_data"].extend(items)
                save_to_sqlite(conn, "fund_data", items)
                logger.info("  Extracted %d fund records from %s", len(items), url)
            time.sleep(2)

    if "index" in categories:
        logger.info("=== Fetching Index Data ===")
        for url in INDEX_URLS:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_index_data(page["html"], url)
                results["index_data"].extend(items)
                save_to_sqlite(conn, "index_data", items)
                logger.info("  Extracted %d index records from %s", len(items), url)
            time.sleep(2)

    if "macro" in categories:
        logger.info("=== Fetching Macro Economic Data ===")
        for url in MACRO_URLS:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_macro_data(page["html"], url)
                results["macro_data"].extend(items)
                save_to_sqlite(conn, "macro_data", items)
                logger.info("  Extracted %d macro records from %s", len(items), url)
            time.sleep(2)

    all_items = []
    for category_items in results.values():
        all_items.extend(category_items)
    save_to_json(all_items, JSON_PATH)

    total = sum(len(v) for v in results.values())
    logger.info("Done! %d total items -> SQLite (%s), JSON (%s)", total, DB_PATH, JSON_PATH)

    conn.close()
    return results


if __name__ == "__main__":
    results = get_eastmoney_data()
    print(f"\n{'=' * 70}")
    print(f"Eastmoney Choice Spider Results")
    print(f"{'=' * 70}")
    for key, items in results.items():
        print(f"  {key}: {len(items)} items")
    print(f"  SQLite: {DB_PATH}")
    print(f"  JSON:   {JSON_PATH}")
    print(f"{'=' * 70}")
