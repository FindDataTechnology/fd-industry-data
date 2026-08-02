#!/usr/bin/env python3
"""World Steel Association Scrapling Spider."""

import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from scrapling import DefaultFetcher, Fetcher, Request, Response

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

START_URLS: List[str] = [
    "https://www.worldsteel.org",
    "https://www.worldsteel.org/en/databases.html",
    "https://www.worldsteel.org/media/press-releases.html",
]

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


def save_to_sqlite(records: List[Dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA_DB)
    cursor = conn.cursor()
    if not records:
        conn.close()
        return
    sql_create = """CREATE TABLE IF NOT EXISTS worldsteel (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        source_url TEXT, page_title TEXT, report_title TEXT,
        publish_date TEXT, category TEXT, download_url TEXT,
        description TEXT
    )"""
    cursor.execute(sql_create)
    for record in records:
        cursor.execute(
            "INSERT INTO worldsteel (source_url, page_title, report_title, publish_date, category, download_url, description) VALUES (?,?,?,?,?,?,?)",
            (record.get("source_url"), record.get("page_title"), record.get("report_title"),
             record.get("publish_date"), record.get("category"), record.get("download_url"),
             record.get("description"))
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


def parse_response(response: Response) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    try:
        title = response.css("title::text").get("").strip()
        articles = response.css("article, .news-item, .press-release, .media-item")
        for article in articles:
            link = article.css("a")
            parsed_data = {
                "source_url": response.url,
                "page_title": title,
                "report_title": article.css("h2::text, h3::text, .title::text").get("").strip(),
                "publish_date": article.css("time::text, .date::text").get("").strip(),
                "category": article.css(".category::text, .tag::text").get("").strip(),
                "download_url": link[0].attrib.get("href", "") if link else "",
                "description": article.css("p::text").get("").strip()[:500],
                "scraped_at": datetime.now().isoformat(),
            }
            results.append(parsed_data)
        if not results:
            results.append({
                "source_url": response.url,
                "page_title": title,
                "report_title": "",
                "publish_date": "",
                "category": "",
                "download_url": "",
                "description": "",
                "scraped_at": datetime.now().isoformat(),
            })
        logger.info("Parsed {} record(s) from {}".format(len(results), response.url))
    except Exception as e:
        logger.error("Error parsing response from {}: {}".format(response.url, e))
    return results


def run_spider(urls: Optional[List[str]] = None, save_results: bool = True) -> List[Dict[str, Any]]:
    urls = urls or START_URLS
    all_records: List[Dict[str, Any]] = []
    logger.info("Starting spider for {} URL(s)...".format(len(urls)))
    for url in urls:
        retry_count = 0
        while retry_count < MAX_RETRIES:
            try:
                ua = CUSTOM_UAS[retry_count % len(CUSTOM_UAS)]
                fetcher: Fetcher = DefaultFetcher(user_agent=ua, requests_per_minute=float("inf"), max_retries=0, timeout=30)
                request = Request(url)
                response = fetcher.fetch(request)
                if response.status == 200:
                    records = parse_response(response)
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
    parser = argparse.ArgumentParser(description="Scrapling spider for World Steel Association")
    parser.add_argument("--urls", nargs="+", help="URLs to crawl")
    parser.add_argument("--dry-run", action="store_true", help="Run without saving results")
    args = parser.parse_args()
    urls = args.urls if args.urls else START_URLS
    save = not args.dry_run
    run_spider(urls=urls, save_results=save)
