#!/usr/bin/env python3
"""World Steel Association Scrapling Spider."""

import argparse
import html as html_lib
import json
import logging
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from scrapling.fetchers import Fetcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

START_URLS: List[str] = [
    "https://worldsteel.org/media/press-releases/2026/",
    "https://worldsteel.org/steel-topics/statistics/",
]

MAX_URLS_PER_RUN = 5
MAX_RECORDS_PER_PAGE = 60
MAX_RECORDS_PER_RUN = 60

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

# The worldsteel listing markup is minified with UNQUOTED attribute values
# (class=news-date, href=https://... ending at whitespace), so the listing
# cards are extracted with tolerant regexes instead of strict CSS selectors.
# ("cateogry" is the site's own typo in the card class name.)
CARD_SPLIT_RE = re.compile(r"<div\s+class=[\"']?news-cateogry-listing", re.IGNORECASE)
H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
H3_RE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.IGNORECASE | re.DOTALL)
NEWS_DATE_RE = re.compile(r"<div\s+class=[\"']?news-date[^>]*>(.*?)</div>", re.IGNORECASE | re.DOTALL)
NEWS_PLACE_RE = re.compile(r"<span\s+class=[\"']?news-place[^>]*>(.*?)</span>", re.IGNORECASE | re.DOTALL)
NEWS_TEXT_RE = re.compile(r"<div\s+class=[\"']?news-text[^>]*>(.*?)</div>", re.IGNORECASE | re.DOTALL)
PARAGRAPH_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
READMORE_RE = re.compile(r"<span\s+class=[\"']?readmore[^>]*>.*?</span>", re.IGNORECASE | re.DOTALL)
STRETCHED_LINK_RE = re.compile(r"<a\b[^>]*stretched-link[^>]*>", re.IGNORECASE)
HREF_RE = re.compile(r"href=(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")


def _clean_text(fragment: str) -> str:
    """Strip tags, unescape entities and collapse whitespace."""
    text = TAG_RE.sub(" ", fragment)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_href(tag: str) -> str:
    match = HREF_RE.search(tag)
    if not match:
        return ""
    value = next((group for group in match.groups() if group is not None), "")
    return value.strip()


def parse_html(html: str, url: str = "") -> List[Dict[str, Any]]:
    """Parse a worldsteel listing page into records (one per news card)."""
    results: List[Dict[str, Any]] = []
    try:
        h1 = H1_RE.search(html)
        page_title = _clean_text(h1.group(1)) if h1 else ""
        if not page_title:
            title = TITLE_RE.search(html)
            page_title = _clean_text(title.group(1)) if title else ""

        chunks = CARD_SPLIT_RE.split(html)[1:]
        for chunk in chunks:
            news_text = NEWS_TEXT_RE.search(chunk)
            # Every field of a card lives before/inside its news-text block;
            # cutting there keeps trailing page content out of the card.
            head = chunk[: news_text.end()] if news_text else chunk
            h3 = H3_RE.search(head)
            report_title = _clean_text(h3.group(1)) if h3 else ""

            publish_date = ""
            category = ""
            date_block = NEWS_DATE_RE.search(head)
            if date_block:
                inner = date_block.group(1)
                place = NEWS_PLACE_RE.search(inner)
                category = _clean_text(place.group(1)) if place else ""
                publish_date = _clean_text(NEWS_PLACE_RE.sub(" ", inner))

            description = ""
            download_url = ""
            if news_text:
                paragraph = PARAGRAPH_RE.search(news_text.group(1))
                if paragraph:
                    description = _clean_text(READMORE_RE.sub(" ", paragraph.group(1)))[:300]
                link = STRETCHED_LINK_RE.search(news_text.group(1))
                if link:
                    download_url = _extract_href(link.group(0))

            results.append({
                "source_url": url,
                "page_title": page_title,
                "report_title": report_title,
                "publish_date": publish_date,
                "category": category,
                "download_url": download_url,
                "description": description,
                "scraped_at": datetime.now().isoformat(),
            })
            if len(results) >= MAX_RECORDS_PER_PAGE:
                break

        if not results:
            # Metadata fallback for pages without listing cards
            # (e.g. the statistics hub). Never fires on press listings.
            results.append({
                "source_url": url,
                "page_title": page_title,
                "report_title": "",
                "publish_date": "",
                "category": "",
                "download_url": "",
                "description": "",
                "scraped_at": datetime.now().isoformat(),
            })
        logger.info("Parsed {} record(s) from {}".format(len(results), url or "<html>"))
    except Exception as e:
        logger.error("Error parsing html from {}: {}".format(url or "<html>", e))
    return results


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


def fetch_html(url: str, user_agent: str) -> Optional[str]:
    """Fetch a URL via scrapling and return its HTML text (None on non-200)."""
    response = Fetcher.get(url, headers={"User-Agent": user_agent}, timeout=30, retries=0, stealthy_headers=True)
    if response.status == 200:
        return str(response.html_content)
    logger.warning("HTTP {} for {}".format(response.status, url))
    return None


def run_spider(urls: Optional[List[str]] = None, save_results: bool = True) -> List[Dict[str, Any]]:
    urls = (urls or START_URLS)[:MAX_URLS_PER_RUN]
    all_records: List[Dict[str, Any]] = []
    logger.info("Starting spider for {} URL(s)...".format(len(urls)))
    for url in urls:
        retry_count = 0
        while retry_count < MAX_RETRIES and len(all_records) < MAX_RECORDS_PER_RUN:
            try:
                ua = CUSTOM_UAS[retry_count % len(CUSTOM_UAS)]
                html = fetch_html(url, ua)
                if html is not None:
                    records = parse_html(html, url)
                    remaining = MAX_RECORDS_PER_RUN - len(all_records)
                    records = records[:remaining]
                    all_records.extend(records)
                    if save_results:
                        save_to_sqlite(records)
                        save_to_json(records)
                    break
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
