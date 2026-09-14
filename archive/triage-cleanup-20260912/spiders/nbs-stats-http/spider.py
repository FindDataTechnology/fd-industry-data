"""http://data.stats.gov.cn/ Scrapling Spider."""
from scrapling import Fetcher, Selector
from scrapling.core.custom_types import PageResponse
import sqlite3
import json
import time
import logging
from datetime import datetime
from pathlib import Path
import argparse

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

START_URLS = ["http://data.stats.gov.cn/"]
CUSTOM_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]
MAX_RETRIES = 3
REQUEST_DELAY = 2.0

def configure_sessions():
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    return headers

def parse_response(response: PageResponse, url: str):
    html = response.html
    items = []
    try:
        logger.info(f"Parsing {url}")
        # TODO: Implement site-specific parsing logic
        pass
    except Exception as e:
        logger.error(f"Parse error: {e}")
    return items

def save_to_sqlite(items, db_path="data/nbs_stats_http.db"):
    if not items:
        return
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS nbs_stats_http (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_date TEXT,
            indicator_name TEXT,
            value REAL,
            unit TEXT,
            source_url TEXT,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    for item in items:
        cursor.execute("""
            INSERT OR REPLACE INTO nbs_stats_http
            (data_date, indicator_name, value, unit, source_url, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (item.get('data_date'), item.get('indicator_name'), item.get('value'),
              item.get('unit'), item.get('_source_url'), datetime.now()))
    conn.commit()
    conn.close()
    logger.info(f"Saved {len(items)} items to {db_path}")

def save_to_json(items, json_path="output/nbs_stats_http.json"):
    if not items:
        return
    Path(json_path).parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    logger.info(f"Saved {len(items)} items to {json_path}")

def run_spider(urls=None, save_results=True):
    if urls is None:
        urls = START_URLS
    headers = configure_sessions()
    fetcher = Fetcher(
        custom_ua=CUSTOM_UAS[0],
        headless=True,
        bypass_navigator=True
    )
    all_items = []
    for url in urls:
        for attempt in range(MAX_RETRIES):
            try:
                logger.info(f"Fetching {url} (attempt {attempt+1}/{MAX_RETRIES})")
                response = fetcher.get(url, headers=headers)
                if response.status == 200:
                    items = parse_response(response, url)
                    all_items.extend(items)
                    if save_results:
                        save_to_sqlite(items)
                        save_to_json(items)
                    break
                else:
                    logger.warning(f"HTTP {response.status} for {url}")
            except Exception as e:
                logger.error(f"Error fetching {url}: {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(REQUEST_DELAY)
        time.sleep(REQUEST_DELAY)
    return all_items

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--urls', nargs='+', help='URLs to crawl')
    parser.add_argument('--dry-run', action='store_true', help='Do not save results')
    args = parser.parse_args()
    items = run_spider(urls=args.urls, save_results=not args.dry_run)
    print(f"\nTotal items extracted: {len(items)}")
