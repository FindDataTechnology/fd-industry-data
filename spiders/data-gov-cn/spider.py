#!/usr/bin/env python3
"""Data.gov (US Government Data) Scrapling Spider.

catalog.data.gov is a React SPA, but search results are server-rendered into
the HTML of query URLs such as:
    https://catalog.data.gov/?q=climate&sort=relevance
Each dataset card is a USWCD ``usa-collection`` item; ``parse_html`` extracts
one record per card (deduped by dataset slug).
"""

import argparse
import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from scrapling import Fetcher, Selector

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CATALOG_BASE = "https://catalog.data.gov"

START_URLS: List[str] = [
    "https://catalog.data.gov/?q=climate&sort=relevance",
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
        conn.close()
        return
    sql_create = """CREATE TABLE IF NOT EXISTS data_gov (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        source_url TEXT, dataset_title TEXT, description TEXT,
        organization TEXT, tags TEXT, formats TEXT,
        dataset_url TEXT, modified_date TEXT
    )"""
    cursor.execute(sql_create)
    for record in records:
        cursor.execute(
            "INSERT INTO data_gov (source_url, dataset_title, description, organization, tags, formats, dataset_url, modified_date) VALUES (?,?,?,?,?,?,?,?)",
            (record.get("source_url"), record.get("dataset_title"), record.get("description"),
             record.get("organization"), record.get("tags"), record.get("formats"),
             record.get("dataset_url"), record.get("modified_date"))
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


def _clean(text: str) -> str:
    return " ".join(text.split())


def parse_html(html: str, url: str = "") -> List[Dict[str, Any]]:
    """Pure parser for server-rendered catalog.data.gov search result pages.

    Takes the raw HTML string, returns one record per dataset card,
    deduplicated by dataset slug. No network / filesystem access.
    """
    results: List[Dict[str, Any]] = []
    if not html:
        return results
    try:
        page = Selector(content=html)
        cards = page.css("li.usa-collection__item.organization-datasets__item")
        if not cards:
            cards = page.css("li.usa-collection__item")
        seen_slugs = set()
        for card in cards:
            heading_links = card.css("h3.usa-collection__heading a.usa-link")
            if not heading_links:
                continue
            link = heading_links[0]
            href = link.attrib.get("href", "") or ""
            path = href.split("?", 1)[0].split("#", 1)[0]
            if not path.startswith("/dataset/"):
                continue
            slug = path[len("/dataset/"):].strip("/")
            if not slug or slug in seen_slugs:
                continue
            seen_slugs.add(slug)

            organization = ""
            org_links = card.css('ul.usa-collection__meta a[href*="/organization/"]')
            if org_links:
                organization = _clean(org_links[0].get_all_text())

            modified_date = ""
            for meta_item in card.css("ul.usa-collection__meta li.usa-collection__meta-item"):
                meta_text = _clean(meta_item.get_all_text())
                if "Dataset Last Updated" in meta_text:
                    modified_date = meta_text.split(":", 1)[1].strip()
                    break

            description = ""
            desc_nodes = card.css("p.usa-collection__description")
            if desc_nodes:
                description = _clean(desc_nodes[0].get_all_text())[:500]

            formats: List[str] = []
            for res_link in card.css("ul.dataset-resources a[data-format]"):
                fmt = (res_link.attrib.get("data-format") or "").strip()
                if fmt and fmt not in formats:
                    formats.append(fmt)

            results.append({
                "source_url": url,
                "dataset_title": _clean(link.get_all_text()),
                "description": description,
                "organization": organization,
                "tags": "",
                "formats": ", ".join(formats),
                "dataset_url": "{}/dataset/{}".format(CATALOG_BASE, slug),
                "modified_date": modified_date,
                "scraped_at": datetime.now().isoformat(),
            })
    except Exception as e:
        logger.error("Error parsing search page {}: {}".format(url or "<html>", e))
    return results


def parse_response(response) -> List[Dict[str, Any]]:
    """Feed a scrapling fetch Response into the pure parse_html function."""
    raw = response.body
    if isinstance(raw, bytes):
        encoding = getattr(response, "encoding", None) or "utf-8"
        html = raw.decode(encoding, errors="replace")
    else:
        html = str(raw or "")
    return parse_html(html, response.url)


def run_spider(urls: Optional[List[str]] = None, save_results: bool = True) -> List[Dict[str, Any]]:
    urls = (urls or START_URLS)[:MAX_URLS_PER_RUN]
    all_records: List[Dict[str, Any]] = []
    seen_datasets: set = set()
    logger.info("Starting spider for {} URL(s)...".format(len(urls)))
    for url in urls:
        retry_count = 0
        while retry_count < MAX_RETRIES:
            try:
                fetcher = Fetcher(auto_match=False, impersonate="chrome")
                response = fetcher.get(url, timeout=30, stealthy_headers=True)
                if response.status == 200:
                    records = parse_response(response)
                    fresh = [r for r in records if r.get("dataset_url") and r["dataset_url"] not in seen_datasets]
                    for record in fresh:
                        seen_datasets.add(record["dataset_url"])
                    all_records.extend(fresh)
                    if save_results:
                        save_to_sqlite(fresh)
                        save_to_json(fresh)
                    break
                else:
                    logger.warning("HTTP {} for {}, retry {}/{}".format(response.status, url, retry_count + 1, MAX_RETRIES))
            except Exception as e:
                logger.error("Error fetching {}: {}".format(url, e))
            retry_count += 1
            time.sleep(REQUEST_DELAY)
        if retry_count == MAX_RETRIES:
            logger.error("Failed to fetch {} after {} retries".format(url, MAX_RETRIES))
        if len(all_records) >= MAX_RECORDS_PER_RUN:
            break
        time.sleep(REQUEST_DELAY)
    all_records = all_records[:MAX_RECORDS_PER_RUN]
    logger.info("Spider completed. Total records: {}".format(len(all_records)))
    return all_records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrapling spider for Data.gov (US Government Data)")
    parser.add_argument("--urls", nargs="+", help="URLs to crawl")
    parser.add_argument("--dry-run", action="store_true", help="Run without saving results")
    args = parser.parse_args()
    urls = args.urls if args.urls else START_URLS
    save = not args.dry_run
    run_spider(urls=urls, save_results=save)
