#!/usr/bin/env python3
"""EastMoney Data Center (东方财富数据中心) Scrapling Spider.

The data.eastmoney.com CPI/PPI pages render their tables client-side, so the
spider now hits the page's own JSON API instead (it returns pure JSON when the
JSONP ``callback=`` parameter is omitted).
"""

import argparse
import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from scrapling.fetchers import Fetcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

API_BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"

CPI_COLUMNS = ",".join([
    "REPORT_DATE",
    "NATIONAL_BASE", "NATIONAL_SAME", "NATIONAL_SEQUENTIAL", "NATIONAL_ACCUMULATE",
    "CITY_BASE", "CITY_SAME", "CITY_SEQUENTIAL", "CITY_ACCUMULATE",
    "RURAL_BASE", "RURAL_SAME", "RURAL_SEQUENTIAL", "RURAL_ACCUMULATE",
])


def build_api_url(report_name: str, columns: str, page_size: int = 20, page_number: int = 1) -> str:
    """Build a datacenter-web JSON API URL (columns list is URL-encoded)."""
    params = {
        "columns": columns,
        "sortColumns": "REPORT_DATE",
        "sortTypes": "-1",
        "source": "WEB",
        "client": "WEB",
        "reportName": report_name,
        "pageNumber": str(page_number),
        "pageSize": str(page_size),
    }
    return API_BASE + "?" + urlencode(params)


START_URLS: List[str] = [
    build_api_url("RPT_ECONOMY_CPI", CPI_COLUMNS),
    build_api_url("RPT_ECONOMY_PPI", "ALL"),
]

REPORT_REFERERS = {
    "RPT_ECONOMY_CPI": "https://data.eastmoney.com/cjsj/cpi.html",
    "RPT_ECONOMY_PPI": "https://data.eastmoney.com/cjsj/ppi.html",
}


def referer_for(url: str) -> str:
    upper = url.upper()
    for report_name, referer in REPORT_REFERERS.items():
        if report_name in upper:
            return referer
    return "https://data.eastmoney.com/"


CUSTOM_UAS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0",
]

MAX_RETRIES = 3
REQUEST_DELAY = 2.0
MAX_RECORDS = 60

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
        conn.close()
        return
    sql_create = """CREATE TABLE IF NOT EXISTS eastmoney_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        source_url TEXT, page_title TEXT, indicator_name TEXT,
        indicator_value TEXT, period TEXT, change_value TEXT,
        category TEXT
    )"""
    cursor.execute(sql_create)
    for record in records:
        cursor.execute(
            "INSERT INTO eastmoney_data (source_url, page_title, indicator_name, indicator_value, period, change_value, category) VALUES (?,?,?,?,?,?,?)",
            (record.get("source_url"), record.get("page_title"), record.get("indicator_name"),
             record.get("indicator_value"), record.get("period"), record.get("change_value"),
             record.get("category"))
        )
    conn.commit()
    conn.close()
    logger.info("Saved {} records to {}".format(len(records), DATA_DB))


def save_to_json(records: List[Dict[str, Any]]) -> None:
    OUTPUT_DIR_LOCAL.mkdir(parents=True, exist_ok=True)
    if not records:
        return
    with open(JSON_OUTPUT, "a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    logger.info("Saved {} records to {}".format(len(records), JSON_OUTPUT))


def _fmt(value: Any) -> str:
    return "" if value is None else str(value)


def _detect_category(url: str, row: Dict[str, Any]) -> str:
    upper = url.upper()
    if "RPT_ECONOMY_CPI" in upper:
        return "CPI"
    if "RPT_ECONOMY_PPI" in upper:
        return "PPI"
    # Fall back to the row shape: CPI uses NATIONAL_* columns, PPI uses BASE*.
    return "CPI" if "NATIONAL_BASE" in row else "PPI"


def parse_payload(text: str, url: str = "") -> List[Dict[str, Any]]:
    """Parse a raw datacenter-web JSON API body into indicator records.

    Pure function: no fetching, no I/O. Emits one record per row per indicator
    family (当月/绝对值 base value and 同比 year-over-year for 全国).
    """
    results: List[Dict[str, Any]] = []
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as e:
        logger.error("Invalid JSON payload from {}: {}".format(url, e))
        return results
    rows = ((payload.get("result") or {}).get("data")) or []
    if not rows:
        return results
    category = _detect_category(url, rows[0] if isinstance(rows[0], dict) else {})
    page_title = "东方财富数据中心 {} 月度数据".format(category)
    for row in rows:
        if not isinstance(row, dict):
            continue
        period = str(row.get("REPORT_DATE") or "").split(" ")[0][:7]
        base_value = row.get("NATIONAL_BASE", row.get("BASE"))
        same_value = row.get("NATIONAL_SAME", row.get("BASE_SAME"))
        families = [
            ("当月(全国)", base_value, same_value),
            ("同比(全国)", same_value, None),
        ]
        for label, value, change in families:
            if value is None:
                continue
            results.append({
                "source_url": url,
                "page_title": page_title,
                "indicator_name": "{} {}".format(category, label),
                "indicator_value": _fmt(value),
                "period": period,
                "change_value": _fmt(change),
                "category": category,
                "scraped_at": datetime.now().isoformat(),
            })
    logger.info("Parsed {} record(s) from {}".format(len(results), url or "payload"))
    return results


def run_spider(urls: Optional[List[str]] = None, save_results: bool = True) -> List[Dict[str, Any]]:
    urls = urls or START_URLS
    all_records: List[Dict[str, Any]] = []
    logger.info("Starting spider for {} URL(s)...".format(len(urls)))
    for url in urls:
        if len(all_records) >= MAX_RECORDS:
            logger.info("Record cap {} reached, stopping".format(MAX_RECORDS))
            break
        retry_count = 0
        while retry_count < MAX_RETRIES:
            try:
                headers = {
                    "User-Agent": CUSTOM_UAS[retry_count % len(CUSTOM_UAS)],
                    "Referer": referer_for(url),
                    "Accept": "application/json, text/plain, */*",
                }
                response = Fetcher.get(url, headers=headers, retries=0, timeout=30)
                if response.status == 200:
                    records = parse_payload(response.text, url=getattr(response, "url", None) or url)
                    if not records:
                        # Last-resort metadata row so the run is auditable; must
                        # not fire on well-formed API payloads.
                        records = [{
                            "source_url": url,
                            "page_title": "东方财富数据中心",
                            "indicator_name": "",
                            "indicator_value": "",
                            "period": "",
                            "change_value": "",
                            "category": "",
                            "scraped_at": datetime.now().isoformat(),
                        }]
                    remaining = MAX_RECORDS - len(all_records)
                    records = records[:remaining]
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
    parser = argparse.ArgumentParser(description="Scrapling spider for EastMoney Data Center (东方财富数据中心)")
    parser.add_argument("--urls", nargs="+", help="URLs to crawl")
    parser.add_argument("--dry-run", action="store_true", help="Run without saving results")
    args = parser.parse_args()
    urls = args.urls if args.urls else START_URLS
    save = not args.dry_run
    run_spider(urls=urls, save_results=save)
