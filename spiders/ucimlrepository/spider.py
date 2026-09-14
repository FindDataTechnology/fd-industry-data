#!/usr/bin/env python3
"""UCI Machine Learning Repository dataset catalog Scrapling spider.

The legacy `table.items-table` listing at https://archive.ics.uci.edu/ml/index.php
is gone (that URL now redirects to a client-rendered app shell). This spider
instead reads the new UCI site's own tRPC JSON API (`donated_datasets.findAll`),
which is what https://archive.ics.uci.edu/datasets renders from. The response
shape is `[{"result":{"data":{"json":[<dataset>, ...]}}}]`.
"""

import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

try:  # reference layout (as in spiders/eastmoney-data/spider.py)
    from scrapling import DefaultFetcher, Fetcher, Request, Response
except ImportError:  # scrapling >= 0.4.9 top-level layout: only Fetcher et al.
    from scrapling import Fetcher

    DefaultFetcher = Request = Response = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_FIND_ALL_INPUT = json.dumps(
    {"0": {"json": {"skip": 0, "take": 20, "filter": None, "search": None, "sort": None}}},
    separators=(",", ":"),
)
START_URLS: List[str] = [
    "https://archive.ics.uci.edu/api/trpc/donated_datasets.findAll?batch=1&input="
    + quote(_FIND_ALL_INPUT, safe="")
]

# Bounded run: at most 5 URLs and MAX_RECORDS dataset rows per run.
MAX_URLS = 5
MAX_RECORDS = 60

CUSTOM_UAS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0",
]

MAX_RETRIES = 3
REQUEST_DELAY = 2.0

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR_LOCAL = BASE_DIR / "output"
DATA_DB = DATA_DIR / "data.sqlite"
JSON_OUTPUT = OUTPUT_DIR_LOCAL / "export.jsonl"

DATASET_URL_TEMPLATE = "https://archive.ics.uci.edu/dataset/{}"


def _loads_tolerant(text: str) -> Any:
    """json.loads with tolerance for bodies truncated mid-JSON.

    The tRPC findAll body is a single JSON array; if the tail is cut (e.g. a
    256KB fixture cap), trim to the last complete `}` and re-close the known
    `[{"result":{"data":{"json":[...]}}}]` nesting; if that still fails,
    sequentially raw-decode the complete objects of the inner array.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    cut = text.rfind("}")
    if cut != -1:
        try:
            return json.loads(text[: cut + 1] + "]}}}]")
        except json.JSONDecodeError:
            pass
    marker = text.find('"json"')
    if marker != -1:
        start = text.find("[", marker)
        if start != -1:
            decoder = json.JSONDecoder()
            objects: List[Any] = []
            pos = start + 1
            while pos < len(text):
                while pos < len(text) and text[pos] in " \t\r\n,":
                    pos += 1
                if pos >= len(text) or text[pos] == "]":
                    break
                try:
                    obj, pos = decoder.raw_decode(text, pos)
                except json.JSONDecodeError:
                    break
                if isinstance(obj, dict):
                    objects.append(obj)
            if objects:
                return [{"result": {"data": {"json": objects}}}]
    raise ValueError("payload is not UCI donated_datasets.findAll JSON")


def _dataset_rows(payload: Any) -> List[Dict[str, Any]]:
    """Navigate `[0]["result"]["data"]["json"]` to the dataset objects."""
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        result = payload[0].get("result")
        if isinstance(result, dict):
            data = result.get("data")
            if isinstance(data, dict):
                rows = data.get("json")
                if isinstance(rows, list):
                    return [row for row in rows if isinstance(row, dict)]
    return []


def parse_payload(text: str, url: str = "") -> List[Dict[str, Any]]:
    """Parse a raw donated_datasets.findAll response body into dataset records."""
    results: List[Dict[str, Any]] = []
    source_url = url or (START_URLS[0] if START_URLS else "")
    try:
        payload = _loads_tolerant(text)
    except (ValueError, json.JSONDecodeError) as e:
        logger.error("Error decoding payload from {}: {}".format(source_url, e))
        return results
    now = datetime.now().isoformat()
    for item in _dataset_rows(payload)[:MAX_RECORDS]:
        name = str(item.get("Name") or "").strip()
        if not name:
            continue
        instances = ""
        for key in ("NumInstances", "NumDownloads", "NumHits"):
            value = item.get(key)
            if value not in (None, ""):
                instances = str(value)
                break
        results.append({
            "name": name,
            "description": str(item.get("Abstract") or "").strip()[:300],
            "url": DATASET_URL_TEMPLATE.format(item.get("ID", "")),
            "type": str(item.get("Task") or "").strip(),
            "instances": instances,
            "area": str(item.get("Area") or "").strip(),
            "doi": str(item.get("DOI") or "").strip(),
            "source_url": source_url,
            "scraped_at": now,
        })
    logger.info("Parsed {} record(s) from {}".format(len(results), source_url))
    return results


def save_to_sqlite(records: List[Dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA_DB)
    cursor = conn.cursor()
    if not records:
        conn.close()
        return
    sql_create = """CREATE TABLE IF NOT EXISTS ucm_datasets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        name TEXT, description TEXT, url TEXT,
        type TEXT, instances TEXT, area TEXT,
        doi TEXT, source_url TEXT
    )"""
    cursor.execute(sql_create)
    for record in records:
        cursor.execute(
            "INSERT INTO ucm_datasets (name, description, url, type, instances, area, doi, source_url, scraped_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (record.get("name"), record.get("description"), record.get("url"), record.get("type"),
             record.get("instances"), record.get("area"), record.get("doi"), record.get("source_url"),
             record.get("scraped_at"))
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


def _fetch(url: str, ua: str) -> Any:
    """Fetch a URL via scrapling, supporting both scrapling import layouts."""
    if DefaultFetcher is not None and Request is not None:
        fetcher: Fetcher = DefaultFetcher(user_agent=ua, requests_per_minute=float("inf"), max_retries=0, timeout=30)
        return fetcher.fetch(Request(url))
    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    return fetcher.get(url, timeout=30, stealthy_headers=True, headers={"User-Agent": ua})


def _response_text(response: Any) -> str:
    body = getattr(response, "body", None)
    if isinstance(body, bytes):
        return body.decode("utf-8", "replace")
    if isinstance(body, str):
        return body
    return str(getattr(response, "text", "") or "")


def run_spider(urls: Optional[List[str]] = None, save_results: bool = True) -> List[Dict[str, Any]]:
    urls = urls or START_URLS
    all_records: List[Dict[str, Any]] = []
    logger.info("Starting spider for {} URL(s)...".format(len(urls)))
    for url in urls[:MAX_URLS]:
        retry_count = 0
        while retry_count < MAX_RETRIES:
            try:
                ua = CUSTOM_UAS[retry_count % len(CUSTOM_UAS)]
                response = _fetch(url, ua)
                if response.status == 200:
                    records = parse_payload(_response_text(response), url=url)[: MAX_RECORDS - len(all_records)]
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
        if len(all_records) >= MAX_RECORDS:
            break
        time.sleep(REQUEST_DELAY)
    logger.info("Spider completed. Total records: {}".format(len(all_records)))
    return all_records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrapling spider for the UCI Machine Learning Repository dataset catalog")
    parser.add_argument("--urls", nargs="+", help="URLs to crawl")
    parser.add_argument("--dry-run", action="store_true", help="Run without saving results")
    args = parser.parse_args()
    urls = args.urls if args.urls else START_URLS
    save = not args.dry_run
    run_spider(urls=urls, save_results=save)
    sys.exit(0)
