#!/usr/bin/env python3
"""
National Library of China (国家图书馆) Spider

Target: https://www.nlc.cn/ (Score: 80)

Extracts publicly available data:
- Digital collections metadata
- Historical archives catalog
- Publication catalog (books, periodicals, maps)
- Public access resources and exhibitions

Authentication Notes:
- Most catalog data is publicly accessible
- Digital collection browsing is public
- Some rare book databases require on-site access
- API access available via OPAC search interface
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from scrapling import Fetcher, Selector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("nlc-spider")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "nlc_collection_data.db"
JSON_PATH = OUTPUT_DIR / "nlc_collection_metadata.json"

URLS = {
    "home": "https://www.nlc.cn/",
    "opac": "http://opac.nlc.cn/F/",
    "digital": "https://www.nlc.cn/digital/",
    "exhibition": "https://www.nlc.cn/exhibition/",
    "news": "https://www.nlc.cn/news/",
    "resources": "https://www.nlc.cn/resources/",
    "special_collections": "https://www.nlc.cn/special/",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}


def init_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS catalog_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            author TEXT,
            publisher TEXT,
            publish_date TEXT,
            isbn TEXT,
            category TEXT,
            collection_type TEXT,
            description TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(title, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS digital_collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            collection_name TEXT,
            category TEXT,
            description TEXT,
            item_count INTEGER DEFAULT 0,
            access_type TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(title, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS exhibitions_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            event_type TEXT,
            start_date TEXT,
            end_date TEXT,
            location TEXT,
            description TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(title, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS library_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stat_date TEXT,
            category TEXT,
            indicator TEXT,
            value REAL,
            unit TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(stat_date, category, indicator, source_url)
        )
    """)
    conn.commit()
    return conn


def fetch_page(url: str, timeout: int = 30) -> dict | None:
    try:
        fetcher = Fetcher(auto_match=False)
        response = fetcher.get(url, headers=HEADERS, timeout=timeout)
        if response.status != 200:
            logger.warning("HTTP %d for %s", response.status, url)
            return None
        return {"url": url, "html": response.html, "status": response.status}
    except Exception as e:
        logger.error("Fetch failed for %s: %s", url, e)
        return None


def extract_catalog_records(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)

    for item_el in sel.css(".book-list li, .result-list li, .catalog-list li, table tr"):
        title_el = item_el.css("a.title, h3 a, .name a, td:first-child a")
        title = title_el.first.text.strip() if title_el else ""
        if not title or len(title) < 2:
            continue

        author_el = item_el.css(".author, .writer, td:nth-child(2)")
        author = author_el.first.text.strip() if author_el else ""

        publisher_el = item_el.css(".publisher, .press, td:nth-child(3)")
        publisher = publisher_el.first.text.strip() if publisher_el else ""

        date_el = item_el.css(".date, .year, .pub-date, td:nth-child(4)")
        publish_date = date_el.first.text.strip() if date_el else ""

        isbn_el = item_el.css(".isbn, .issn")
        isbn = isbn_el.first.text.strip() if isbn_el else ""

        category_el = item_el.css(".category, .class, .cls")
        category = category_el.first.text.strip() if category_el else ""

        desc_el = item_el.css(".desc, .summary, .abstract, .intro")
        description = desc_el.first.text.strip() if desc_el else ""

        items.append({
            "title": title,
            "author": author,
            "publisher": publisher,
            "publish_date": publish_date,
            "isbn": isbn,
            "category": category,
            "collection_type": "book",
            "description": description[:300] if description else "",
            "source_url": source_url,
            "raw_data": json.dumps({"title": title}, ensure_ascii=False),
        })
    return items


def extract_digital_collections(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)

    for item_el in sel.css(".digital-list li, .collection-list li, .resource-item, .db-list li"):
        title_el = item_el.css("a, h3, h4, .name")
        title = title_el.first.text.strip() if title_el else ""
        if not title or len(title) < 2:
            continue

        desc_el = item_el.css(".desc, .summary, p, .intro")
        description = desc_el.first.text.strip() if desc_el else ""

        count_el = item_el.css(".count, .num, .total")
        item_count = 0
        if count_el:
            count_text = count_el.first.text.strip()
            count_match = re.search(r"[\d,]+", count_text)
            if count_match:
                item_count = int(count_match.group().replace(",", ""))

        items.append({
            "title": title,
            "collection_name": title,
            "category": "",
            "description": description[:300] if description else "",
            "item_count": item_count,
            "access_type": "public",
            "source_url": source_url,
            "raw_data": json.dumps({"title": title}, ensure_ascii=False),
        })
    return items


def extract_exhibitions(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)

    for item_el in sel.css(".exhibition-list li, .event-list li, .news-list li, .activity-list li"):
        title_el = item_el.css("a, h3, h4, .title")
        title = title_el.first.text.strip() if title_el else ""
        if not title or len(title) < 2:
            continue

        date_el = item_el.css(".date, .time, span.time")
        date_text = date_el.first.text.strip() if date_el else ""

        desc_el = item_el.css(".desc, .summary, p, .intro")
        description = desc_el.first.text.strip() if desc_el else ""

        location_el = item_el.css(".location, .place, .venue")
        location = location_el.first.text.strip() if location_el else ""

        items.append({
            "title": title,
            "event_type": "exhibition",
            "start_date": date_text,
            "end_date": "",
            "location": location or "国家图书馆",
            "description": description[:300] if description else "",
            "source_url": source_url,
            "raw_data": json.dumps({"title": title}, ensure_ascii=False),
        })
    return items


def extract_library_stats(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)
    body_text = sel.css("body").first.text if sel.css("body") else ""

    stat_patterns = [
        (r"馆藏文献\s*(\d[\d,]+)", "total_collection", "册/件"),
        (r"藏书\s*(\d[\d,]+)", "total_books", "册"),
        (r"古籍\s*(\d[\d,]+)", "rare_books", "册"),
        (r"善本\s*(\d[\d,]+)", "rare_editions", "册"),
        (r"电子资源\s*(\d[\d,]+)", "digital_resources", "种"),
        (r"数据库\s*(\d[\d,]+)", "databases", "个"),
        (r"读者\s*(\d[\d,]+)", "registered_readers", "人"),
        (r"年接待读者\s*(\d[\d,]+)", "annual_visitors", "人次"),
    ]

    for pattern, indicator, unit in stat_patterns:
        matches = re.findall(pattern, body_text)
        for match in matches:
            value = int(match.replace(",", ""))
            items.append({
                "stat_date": datetime.now().strftime("%Y-%m-%d"),
                "category": "library_statistics",
                "indicator": indicator,
                "value": float(value),
                "unit": unit,
                "source_url": source_url,
                "raw_data": f"Pattern: {pattern}, Match: {match}",
            })
    return items


def save_catalog(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO catalog_records
                (title, author, publisher, publish_date, isbn, category,
                 collection_type, description, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("title", ""),
                item.get("author", ""),
                item.get("publisher", ""),
                item.get("publish_date", ""),
                item.get("isbn", ""),
                item.get("category", ""),
                item.get("collection_type", ""),
                item.get("description", ""),
                item.get("source_url", ""),
                item.get("raw_data", ""),
                datetime.now().isoformat(),
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert catalog record: %s", e)
    conn.commit()
    return inserted


def save_digital(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO digital_collections
                (title, collection_name, category, description, item_count,
                 access_type, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("title", ""),
                item.get("collection_name", ""),
                item.get("category", ""),
                item.get("description", ""),
                item.get("item_count", 0),
                item.get("access_type", ""),
                item.get("source_url", ""),
                item.get("raw_data", ""),
                datetime.now().isoformat(),
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert digital collection: %s", e)
    conn.commit()
    return inserted


def save_exhibitions(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO exhibitions_events
                (title, event_type, start_date, end_date, location,
                 description, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("title", ""),
                item.get("event_type", ""),
                item.get("start_date", ""),
                item.get("end_date", ""),
                item.get("location", ""),
                item.get("description", ""),
                item.get("source_url", ""),
                item.get("raw_data", ""),
                datetime.now().isoformat(),
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert exhibition: %s", e)
    conn.commit()
    return inserted


def save_stats(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO library_stats
                (stat_date, category, indicator, value, unit, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("stat_date", ""),
                item.get("category", ""),
                item.get("indicator", ""),
                item.get("value"),
                item.get("unit", ""),
                item.get("source_url", ""),
                item.get("raw_data", ""),
                datetime.now().isoformat(),
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert stat: %s", e)
    conn.commit()
    return inserted


def main():
    logger.info("=" * 70)
    logger.info("Starting National Library of China (国家图书馆) Spider")
    logger.info("=" * 70)
    logger.info("NOTE: Most catalog and digital collection data is publicly accessible.")
    logger.info("Some rare book databases may require on-site access.")
    logger.info("=" * 70)

    conn = init_db()
    all_items: dict[str, list] = {
        "catalog": [], "digital": [], "exhibitions": [], "stats": []
    }

    logger.info("\n[1/%d] Fetching NLC homepage: %s", len(URLS), URLS["home"])
    page = fetch_page(URLS["home"])
    if page:
        logger.info("  Status: %d", page["status"])
        stats = extract_library_stats(page["html"], page["url"])
        all_items["stats"].extend(stats)
        logger.info("  -> Extracted %d library statistics", len(stats))

        exhibitions = extract_exhibitions(page["html"], page["url"])
        all_items["exhibitions"].extend(exhibitions)
        logger.info("  -> Extracted %d exhibition/event records", len(exhibitions))

    time.sleep(2)

    for i, (key, url) in enumerate(URLS.items()):
        if key == "home":
            continue
        logger.info("\n[%d/%d] Fetching %s: %s", i + 1, len(URLS), key, url)
        page = fetch_page(url)
        if page:
            logger.info("  Status: %d", page["status"])

            catalog = extract_catalog_records(page["html"], page["url"])
            all_items["catalog"].extend(catalog)
            logger.info("  -> Extracted %d catalog records", len(catalog))

            digital = extract_digital_collections(page["html"], page["url"])
            all_items["digital"].extend(digital)
            logger.info("  -> Extracted %d digital collection records", len(digital))

            exhibitions = extract_exhibitions(page["html"], page["url"])
            all_items["exhibitions"].extend(exhibitions)
            logger.info("  -> Extracted %d exhibition/event records", len(exhibitions))

            stats = extract_library_stats(page["html"], page["url"])
            all_items["stats"].extend(stats)
            logger.info("  -> Extracted %d statistics", len(stats))

        time.sleep(3)

    total = 0
    if all_items["catalog"]:
        n = save_catalog(conn, all_items["catalog"])
        logger.info("\nSaved %d catalog records to database", n)
        total += n
    if all_items["digital"]:
        n = save_digital(conn, all_items["digital"])
        logger.info("Saved %d digital collection records to database", n)
        total += n
    if all_items["exhibitions"]:
        n = save_exhibitions(conn, all_items["exhibitions"])
        logger.info("Saved %d exhibition/event records to database", n)
        total += n
    if all_items["stats"]:
        n = save_stats(conn, all_items["stats"])
        logger.info("Saved %d statistics records to database", n)
        total += n

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    export_data = {
        "source": "National Library of China (国家图书馆)",
        "source_url": "https://www.nlc.cn/",
        "fetched_at": datetime.now().isoformat(),
        "auth_required": {
            "rare_books_onsite": True,
            "api_access": False,
        },
        "public_data": {
            "opac_catalog": True,
            "digital_collections": True,
            "exhibitions": True,
            "library_statistics": True,
        },
        "catalog": all_items["catalog"],
        "digital_collections": all_items["digital"],
        "exhibitions": all_items["exhibitions"],
        "statistics": all_items["stats"],
    }
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)
    logger.info("Exported data to JSON: %s", JSON_PATH)

    conn.close()

    print("\n" + "=" * 70)
    print("NLC Spider - Execution Summary")
    print("=" * 70)
    print(f"Catalog records: {len(all_items['catalog'])}")
    print(f"Digital collections: {len(all_items['digital'])}")
    print(f"Exhibitions/events: {len(all_items['exhibitions'])}")
    print(f"Statistics: {len(all_items['stats'])}")
    print(f"Total DB records: {total}")
    print(f"Database: {DB_PATH}")
    print(f"JSON output: {JSON_PATH}")
    print("=" * 70)
    print("\nAccess Notes:")
    print("  - OPAC catalog: Publicly accessible")
    print("  - Digital collections: Mostly public, some restricted")
    print("  - Rare books: May require on-site access at NLC")
    print("  - Exhibitions: Public information")


if __name__ == "__main__":
    main()
