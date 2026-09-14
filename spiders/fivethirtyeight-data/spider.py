#!/usr/bin/env python3
"""FiveThirtyEight Data Scrapling Spider.

Data surface: GitHub REST git/trees API for the fivethirtyeight/data repo
(the rendered github.com file listing moved to React and is no longer
scrapable). The tree listing IS the dataset catalogue.
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

START_URLS: List[str] = [
    "https://api.github.com/repos/fivethirtyeight/data/git/trees/master",
]

MAX_RETRIES = 3
REQUEST_DELAY = 2.0
MAX_URLS = 5
MAX_RECORDS = 60

API_HEADERS: Dict[str, str] = {
    "Accept": "application/vnd.github+json",
}

CUSTOM_UAS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0",
]

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
        insert_params.append(col_name)
        placeholders.append("?")
        if col_name == "scraped_at":
            continue  # already declared in the CREATE TABLE statement below
        if isinstance(value, str):
            col_type = "TEXT"
        elif isinstance(value, (int, float)):
            col_type = "REAL"
        else:
            col_type = "TEXT"
        columns.append((col_name, col_type))
    table_name = "fivethirtyeight_data_records"
    sql_create = "CREATE TABLE IF NOT EXISTS " + table_name + " (id INTEGER PRIMARY KEY AUTOINCREMENT, scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, " + ", ".join([c[0] for c in columns]) + ")"
    cursor.execute(sql_create)
    placeholders_str = ", ".join(placeholders)
    insert_sql = "INSERT INTO " + table_name + " (" + ", ".join(insert_params) + ") VALUES (" + placeholders_str + ")"
    for record in records:
        values = [record.get(param, None) for param in insert_params]
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
    """Pure parser for the GitHub git/trees API response body.

    One record per top-level tree entry (dataset directories and files):
    file_name / file_type / is_dataset_dir / blob_sha / source_url / scraped_at.
    """
    results: List[Dict[str, Any]] = []
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as e:
        logger.error("Invalid JSON payload from {}: {}".format(url or "<unknown>", e))
        return results
    tree = payload.get("tree") if isinstance(payload, dict) else None
    if not isinstance(tree, list):
        logger.error("No 'tree' array in payload from {}".format(url or "<unknown>"))
        return results
    source_url = url or payload.get("url") or START_URLS[0]
    scraped_at = datetime.now().isoformat()
    for entry in tree:
        if len(results) >= MAX_RECORDS:
            logger.info("Reached record cap ({}), stopping tree iteration".format(MAX_RECORDS))
            break
        if not isinstance(entry, dict):
            continue
        path = entry.get("path") or ""
        entry_type = entry.get("type") or ""
        if not path or entry_type not in ("blob", "tree"):
            continue
        results.append({
            "source_url": source_url,
            "file_name": path,
            "file_type": entry_type,
            "is_dataset_dir": entry_type == "tree" and not path.startswith("."),
            "blob_sha": entry.get("sha") or "",
            "scraped_at": scraped_at,
        })
    logger.info("Parsed {} record(s) from {}".format(len(results), source_url))
    return results


def _response_status(response: Any) -> int:
    # This unit historically used `status_code`; scrapling >= 0.3 uses `status`.
    status = getattr(response, "status_code", None)
    if status is None:
        status = getattr(response, "status", 0)
    return int(status)


def _response_text(response: Any) -> str:
    # Raw body first: the HTML-selector `.text` is empty for JSON bodies.
    body = getattr(response, "body", None)
    if isinstance(body, bytes):
        return body.decode("utf-8", "replace")
    if isinstance(body, str) and body:
        return body
    return getattr(response, "text", "") or ""


def run_spider(urls: Optional[List[str]] = None, save_results: bool = True) -> List[Dict[str, Any]]:
    urls = list(urls or START_URLS)[:MAX_URLS]
    all_records: List[Dict[str, Any]] = []
    logger.info("Starting spider for {} URL(s)...".format(len(urls)))
    logger.info("Target: {}".format(urls[0]))
    for url in urls:
        retry_count = 0
        while retry_count < MAX_RETRIES:
            try:
                ua = CUSTOM_UAS[retry_count % len(CUSTOM_UAS)]
                fetcher: Fetcher = Fetcher()
                headers = dict(API_HEADERS)
                headers["User-Agent"] = ua
                response = fetcher.get(url, headers=headers, retries=0, timeout=30)
                status = _response_status(response)
                if status == 200:
                    records = parse_payload(_response_text(response), url=url)
                    all_records.extend(records)
                    if save_results and records:
                        save_to_sqlite(records)
                        save_to_json(records)
                    break
                else:
                    logger.warning("HTTP {} for {}, retry {}/{}".format(status, url, retry_count + 1, MAX_RETRIES))
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
    parser = argparse.ArgumentParser(description="Scrapling spider for FiveThirtyEight Data")
    parser.add_argument("--urls", nargs="+", help="URLs to crawl (default: START_URLS)")
    parser.add_argument("--dry-run", action="store_true", help="Run without saving results")
    args = parser.parse_args()
    urls = args.urls if args.urls else START_URLS
    save = not args.dry_run
    run_spider(urls=urls, save_results=save)
