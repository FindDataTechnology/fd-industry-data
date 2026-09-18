#!/usr/bin/env python3
"""
Steel Industry Information Networks Spider — Extracting data from 5 steel industry sources.

Uses Scrapling's Spider framework with:
  - Multiple URLs per category
  - Automatic header rotation to avoid blocking
  - Error handling and retry logic
  - SQLite + JSON output

Output Structure:
  - data/info_data.db (SQLite database)
  - output/info_data.json (JSON export)
"""

from __future__ import annotations

import json
import logging
import random
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scrapling.fetchers import FetcherSession
from scrapling.spiders import Spider, Request, Response

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("steel-info")

# ===== Configuration =====
BASE_DIR = Path(__file__).resolve().parent.parent.parent / "steel-info"
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "info_data.db"
JSON_PATH = OUTPUT_DIR / "info_data.json"

# Target URLs for this category
START_URLS = [
"https://www.mysteel.com",
        "http://www.cisa.org.cn",
        "http://www.steelhome.cn",
        "https://www.smm.cn",
        "https://www.stats.gov.cn"
    ]

# Custom User Agents to rotate through
CUSTOM_UAS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/128.0.0.0 Safari/537.36",
]

MAX_RETRIES = 3
REQUEST_DELAY = 2.0

SOURCE_INFO = {
{SOURCE_INFO}
    }


def get_random_ua() -> str:
    """Return a random user agent to avoid detection."""
    return random.choice(CUSTOM_UAS)


def extract_data_from_page(html: str, page_url: str) -> list[dict[str, Any]]:
    """Extract relevant data from scraped HTML page.
    
    This is where you'd implement parsing logic specific to each site.
    The current implementation provides placeholder structure for demonstration.
    """
    items = []
    
    # Placeholder extraction - in real implementation, parse html here
    source_info = SOURCE_INFO.get(page_url, {"title": "", "description": ""})
    
    # Example: You would scrape actual data here based on the site structure
    # For now, return empty list - spiders need individual customization
    
    logger.info("Page %s accessed (source: %s)", page_url, source_info.get('title', 'Unknown'))
    
    return items


class SteelInfoSpider(Spider):
    """Spider for Steel Industry Information Networks.
    
    Crawls multiple steel industry data sources within this category.
    Each URL may require custom parsing logic based on its structure.
    """
    
    name = "steel-info"
    allowed_domains = set(u.split("//")[-1].split("/")[0] for u in START_URLS)
    start_urls = START_URLS
    
    download_delay = REQUEST_DELAY
    concurrent_requests = 2
    max_retries = MAX_RETRIES
    retry_statuses = {429, 500, 502, 503, 504}
    
    def configure_sessions(self, manager):
        """Configure FetcherSessions with custom headers."""
        for _ in range(self.concurrent_requests):
            session = FetcherSession(impersonate="chrome")
            session.update_headers({
                "User-Agent": get_random_ua(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
                "Connection": "keep-alive",
            })
            manager.add("default", session, default=True)
    
    async def parse(self, response: Response):
        """Parse each crawled page and extract data items."""
        
        if response.status != 200:
            logger.warning("Failed to fetch %s (status=%d)", response.url, response.status)
            return
        
        try:
            extracted_items = extract_data_from_page(response.text, response.url)
            
            for item in extracted_items:
                item["_source_url"] = response.url
                yield item
            
            logger.info("Page %s yielded %d items", response.url, len(extracted_items))
            
        except Exception as e:
            logger.error("Error parsing %s: %s", response.url, e)


def save_to_sqlite(items: list[dict[str, Any]], db_path: Path = DB_PATH) -> int:
    """Save items to SQLite database with upsert support."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    
    if items:
        columns = ", ".join([f'"{k}" TEXT' for k in items[0].keys()])
        placeholders = ", ".join(["?" for _ in items[0]])
        
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS info_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                {columns},
                fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        inserted = 0
        for item in items:
            values = [item.get(k) for k in item.keys()]
            cur.execute(f"""
                INSERT OR REPLACE INTO info_data ({", ".join(item.keys())}, fetched_at)
                VALUES ({placeholders}, ?)
            """, values + [datetime.now(timezone.utc).isoformat()])
            inserted += 1
        
        conn.commit()
        conn.close()
        return inserted
    
    conn.close()
    return 0


def save_to_json(items: list[dict[str, Any]], json_path: Path = JSON_PATH) -> None:
    """Save items to JSON file."""
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def run_spider(urls: list[str] | None = None, save_results: bool = True) -> list[dict[str, Any]]:
    """Run the spider and optionally save results.
    
    Args:
        urls: Optional list of specific URLs to crawl. If None, uses all defined URLs.
        save_results: Whether to save results to SQLite and JSON files.
    
    Returns:
        List of extracted items.
    """
    spider = SteelInfoSpider()
    if urls:
        spider.start_urls = urls
    
    result = spider.start()
    items = list(result.items)
    
    if save_results and items:
        save_to_sqlite(items)
        save_to_json(items)
        logger.info("Saved %d records to %s and %s", len(items), DB_PATH, JSON_PATH)
    elif not items:
        logger.warning("No items extracted from any source")
    
    return items


if __name__ == "__main__":
    logger.info("=" * 70)
    logger.info("Starting %s crawler", "steel-info".upper())
    logger.info("Total URLs to crawl: %d", len(START_URLS))
    
    for i, url in enumerate(START_URLS, 1):
        info = SOURCE_INFO.get(url, {})
        title = info.get("title", "Unknown")
        logger.info("  [%d] %s → %s", i, url[:50], title[:40] if title else "")
    
    logger.info("=" * 70)
    
    results = run_spider(save_results=True)
    
    print("\n" + "=" * 70)
    print(f"Extraction Summary")
    print(f"{'=' * 70}")
    print(f"  Total records extracted: {len(results)}")
    print(f"  Database: {DB_PATH}")
    print(f"  JSON:      {JSON_PATH}")
    print(f"{'=' * 70}")
