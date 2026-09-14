#!/usr/bin/env python3
"""东方财富期货数据 Scrapling Spider.

主力合约成交持仓龙虎榜目录：页面 (https://data.eastmoney.com/futures/) 的表格
由 JS 渲染，静态抓取解析不到行；数据实际来自 datacenter-web JSON API
(reportName=RPT_FUTU_POSITIONCODE, 纯 JSON 无需 callback)。本 spider 直接
请求该 API 并解析 result.data。
"""

import argparse
import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from scrapling import Fetcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# 成交持仓龙虎榜-主力合约目录（单一请求，pageSize=60 以满足每次运行 ≤60 条记录）
START_URLS: List[str] = [
    "https://datacenter-web.eastmoney.com/api/data/v1/get"
    "?reportName=RPT_FUTU_POSITIONCODE"
    "&columns=TRADE_MARKET_CODE,TRADE_CODE,TRADE_TYPE,SECURITY_CODE,IS_MAINCODE"
    "&filter=%28IS_MAINCODE%3D%221%22%29"
    "&pageNumber=1&pageSize=60"
    "&sortTypes=1,-1&sortColumns=IS_MAINCODE,SECURITY_CODE"
    "&source=WEB&client=WEB",
]

# API 需要浏览器 UA + 期货频道 Referer（直连可用，无需 cookie）
REFERER = "https://data.eastmoney.com/futures/sh/data.html"

CUSTOM_UAS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0",
]

MAX_RETRIES = 3
REQUEST_DELAY = 2.0
MAX_URLS_PER_RUN = 5
MAX_RECORDS_PER_RUN = 60

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR_LOCAL = BASE_DIR / "output"
DATA_DB = DATA_DIR / "data.sqlite"
JSON_OUTPUT = OUTPUT_DIR_LOCAL / "export.jsonl"


def save_to_sqlite(records: List[Dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA_DB)
    cursor = conn.cursor()
    if not records:
        logger.warning("No records to save to SQLite")
        conn.close()
        return
    sample = records[0]
    columns = []
    insert_params = []
    placeholders = []
    for key, value in sample.items():
        col_name = key.lower().replace(" ", "_").replace("-", "_")
        if isinstance(value, str):
            col_type = "TEXT"
        elif isinstance(value, (int, float)):
            col_type = "REAL"
        else:
            col_type = "TEXT"
        columns.append((col_name, col_type))
        insert_params.append(col_name)
        placeholders.append("?")
    table_name = "eastmoney_futures_records"
    col_defs = ", ".join([c[0] + " " + c[1] for c in columns])
    sql_create = "CREATE TABLE IF NOT EXISTS " + table_name + " (id INTEGER PRIMARY KEY AUTOINCREMENT, scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, " + ", ".join([c[0] for c in columns]) + ")"
    cursor.execute(sql_create)
    placeholders_str = ", ".join(placeholders)
    insert_sql = "INSERT INTO " + table_name + " (" + ", ".join(insert_params) + ") VALUES (" + placeholders_str + ")"
    for record in records:
        values = [record.get(col[0], None) for col in columns]
        cursor.execute(insert_sql, values)
    conn.commit()
    conn.close()
    logger.info("Saved {} records to {}".format(len(records), DATA_DB))


def save_to_json(records: List[Dict[str, Any]]) -> None:
    OUTPUT_DIR_LOCAL.mkdir(parents=True, exist_ok=True)
    if not records:
        logger.warning("No records to save to JSON")
        return
    with open(JSON_OUTPUT, "a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    logger.info("Saved {} records to {}".format(len(records), JSON_OUTPUT))


def parse_payload(text: str, url: str = "") -> List[Dict[str, Any]]:
    """Parse a raw datacenter-web JSON body into main-contract catalog records.

    Pure function (no network / no writes); tolerates an optional JSONP wrapper.
    Each row of ``result.data`` becomes a record:
    contract=SECURITY_CODE, product=TRADE_TYPE, trade_code=TRADE_CODE,
    market_code=TRADE_MARKET_CODE, is_main=IS_MAINCODE, source_url, scraped_at.
    """
    records: List[Dict[str, Any]] = []
    payload: Any = None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        stripped = text.strip()
        lpar = stripped.find("(")
        rpar = stripped.rfind(")")
        if lpar != -1 and rpar > lpar:
            try:
                payload = json.loads(stripped[lpar + 1 : rpar])
            except json.JSONDecodeError:
                payload = None
    if payload is None:
        logger.error("Response is not valid JSON/JSONP ({} chars)".format(len(text)))
        return records

    result = payload.get("result") if isinstance(payload, dict) else None
    rows = result.get("data") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        logger.error("No result.data rows in payload")
        return records

    now = datetime.now().isoformat()
    for row in rows:
        if not isinstance(row, dict):
            continue
        record = {
            "contract": str(row.get("SECURITY_CODE") or "").strip(),
            "product": str(row.get("TRADE_TYPE") or "").strip(),
            "trade_code": str(row.get("TRADE_CODE") or "").strip(),
            "market_code": str(row.get("TRADE_MARKET_CODE") or "").strip(),
            "is_main": str(row.get("IS_MAINCODE") or "").strip(),
            "source_url": url,
            "scraped_at": now,
        }
        if record["contract"]:
            records.append(record)
    logger.info("Parsed {} record(s) from payload".format(len(records)))
    return records


def run_spider(urls: Optional[List[str]] = None, save_results: bool = True) -> List[Dict[str, Any]]:
    urls = urls or START_URLS
    urls = urls[:MAX_URLS_PER_RUN]
    all_records: List[Dict[str, Any]] = []
    logger.info("Starting spider for {} URL(s)...".format(len(urls)))
    logger.info("Target: {}".format(urls[0]))
    for url in urls:
        retry_count = 0
        while retry_count < MAX_RETRIES:
            try:
                ua = CUSTOM_UAS[retry_count % len(CUSTOM_UAS)]
                headers = {
                    "User-Agent": ua,
                    "Referer": REFERER,
                    "Accept": "application/json, text/plain, */*",
                }
                fetcher = Fetcher()
                response = fetcher.get(url, headers=headers, timeout=30, retries=0)
                if response.status == 200:
                    records = parse_payload(response.text, url)
                    if not records:
                        parsed_data = {
                            "source_url": url,
                            "content_length": len(response.text),
                            "status_code": response.status,
                            "scraped_at": datetime.now().isoformat(),
                        }
                        records = [parsed_data]
                        logger.warning("No data rows from {}, metadata fallback only".format(url))
                    records = records[:MAX_RECORDS_PER_RUN]
                    all_records.extend(records)
                    if save_results:
                        save_to_sqlite(records)
                        save_to_json(records)
                    break
                else:
                    logger.warning("HTTP {} for {}, retry {}/{}".format(response.status, url, retry_count + 1, MAX_RETRIES))
            except Exception as e:
                logger.error("Error fetching {}: {}".format(url, e))
            retry_count += 1
            time.sleep(REQUEST_DELAY)
        if retry_count == MAX_RETRIES:
            logger.error("Failed to fetch {} after {} retries".format(url, MAX_RETRIES))
        time.sleep(REQUEST_DELAY)
    logger.info("Spider completed. Total records: {}".format(len(all_records)))
    return all_records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrapling spider for 东方财富期货数据")
    parser.add_argument("--urls", nargs="+", help="URLs to crawl (default: START_URLS)")
    parser.add_argument("--dry-run", action="store_true", help="Run without saving results")
    args = parser.parse_args()
    urls = args.urls if args.urls else START_URLS
    save = not args.dry_run
    run_spider(urls=urls, save_results=save)
