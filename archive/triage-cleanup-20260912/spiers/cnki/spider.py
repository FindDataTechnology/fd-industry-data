#!/usr/bin/env python3
"""
CNKI (中国知网) Spider

Target: https://www.cnki.net/ (Score: 90)

Extracts publicly available metadata:
- Journal listing and metadata
- Conference proceeding metadata
- Publication statistics from open pages
- Research trend data from public search results

Authentication Notes:
- Full-text access requires institutional or personal subscription
- Public search results show titles, authors, abstracts, and citation counts
- Some statistics pages are publicly accessible without login
- API access requires formal agreement with CNKI
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
logger = logging.getLogger("cnki-spider")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "cnki_academic_data.db"
JSON_PATH = OUTPUT_DIR / "academic_metadata.json"

URLS = {
    "home": "https://www.cnki.net/",
    "journals": "https://navi.cnki.net/knavi/journals/search",
    "stats": "https://cnki.net/kcms/detail/search.aspx",
    "newspapers": "https://navi.cnki.net/knavi/newspapers/search",
    "conferences": "https://navi.cnki.net/knavi/conferences/search",
    "yearbooks": "https://navi.cnki.net/knavi/yearbooks/search",
}

SEARCH_URL = "https://kns.cnki.net/kns8s/defaultresult/index"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Referer": "https://www.cnki.net/",
}


def init_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            authors TEXT,
            source_journal TEXT,
            publish_date TEXT,
            abstract TEXT,
            keywords TEXT,
            doi TEXT,
            citation_count INTEGER DEFAULT 0,
            download_count INTEGER DEFAULT 0,
            source_type TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(title, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS journal_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            journal_name TEXT,
            issn TEXT,
            cn_number TEXT,
            publisher TEXT,
            category TEXT,
            impact_factor REAL,
            description TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(journal_name, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS publication_stats (
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


def extract_journal_metadata(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)

    for row in sel.css("table tr"):
        cells = row.css("td")
        if len(cells) < 3:
            continue
        cell_texts = [c.text.strip() for c in cells]
        journal_name = cell_texts[0] if cell_texts else ""
        if not journal_name or len(journal_name) < 2:
            continue

        issn = ""
        cn_number = ""
        publisher = ""
        category = ""

        for text in cell_texts:
            if re.match(r"\d{4}-?\d{3}[\dXx]", text):
                issn = text
            elif re.match(r"CN\s*\d+", text, re.IGNORECASE):
                cn_number = text
            elif any(kw in text for kw in ["大学", "学院", "出版社", "研究院", "研究所"]):
                publisher = text
            elif any(kw in text for kw in ["自然科学", "社会科学", "工程技术", "农业", "医学"]):
                category = text

        items.append({
            "journal_name": journal_name,
            "issn": issn,
            "cn_number": cn_number,
            "publisher": publisher,
            "category": category,
            "source_url": source_url,
            "raw_data": json.dumps({"cells": cell_texts}, ensure_ascii=False),
        })
    return items


def extract_paper_metadata(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)

    for item_el in sel.css(".result-table-list .result-table-list-inner"):
        title_el = item_el.css(".name a")
        title = title_el.first.text.strip() if title_el else ""
        if not title:
            continue

        authors_el = item_el.css(".author")
        authors = authors_el.first.text.strip() if authors_el else ""

        source_el = item_el.css(".source")
        source_journal = source_el.first.text.strip() if source_el else ""

        date_el = item_sel.css(".date") if hasattr(item_el, '_sel') else item_el.css(".date")
        publish_date = date_el.first.text.strip() if date_el else ""

        abstract_el = item_el.css(".summary")
        abstract = abstract_el.first.text.strip() if abstract_el else ""

        keywords_el = item_el.css(".keywords")
        keywords = keywords_el.first.text.strip() if keywords_el else ""

        citation_el = item_el.css(".quote")
        citation_count = 0
        if citation_el:
            cite_text = citation_el.first.text.strip()
            cite_match = re.search(r"\d+", cite_text)
            if cite_match:
                citation_count = int(cite_match.group())

        download_el = item_el.css(".download")
        download_count = 0
        if download_el:
            dl_text = download_el.first.text.strip()
            dl_match = re.search(r"\d+", dl_text)
            if dl_match:
                download_count = int(dl_match.group())

        items.append({
            "title": title,
            "authors": authors,
            "source_journal": source_journal,
            "publish_date": publish_date,
            "abstract": abstract[:500] if abstract else "",
            "keywords": keywords,
            "citation_count": citation_count,
            "download_count": download_count,
            "source_type": "journal_paper",
            "source_url": source_url,
            "raw_data": json.dumps({"title": title}, ensure_ascii=False),
        })

    for item_el in sel.css(".s-main-list .s-main-list-inner"):
        title_el = item_el.css("a.title")
        title = title_el.first.text.strip() if title_el else ""
        if not title:
            continue

        authors_el = item_el.css(".author")
        authors = authors_el.first.text.strip() if authors_el else ""

        source_el = item_el.css(".source")
        source_journal = source_el.first.text.strip() if source_el else ""

        abstract_el = item_el.css(".info")
        abstract = abstract_el.first.text.strip() if abstract_el else ""

        items.append({
            "title": title,
            "authors": authors,
            "source_journal": source_journal,
            "publish_date": "",
            "abstract": abstract[:500] if abstract else "",
            "keywords": "",
            "citation_count": 0,
            "download_count": 0,
            "source_type": "paper",
            "source_url": source_url,
            "raw_data": json.dumps({"title": title}, ensure_ascii=False),
        })
    return items


def extract_publication_stats(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)
    body_text = sel.css("body").first.text if sel.css("body") else ""

    stat_patterns = [
        (r"期刊\s*(\d[\d,]+)", "journal_count", "种"),
        (r"论文\s*(\d[\d,]+)", "paper_count", "篇"),
        (r"博硕士论文\s*(\d[\d,]+)", "thesis_count", "篇"),
        (r"会议论文\s*(\d[\d,]+)", "conference_paper_count", "篇"),
        (r"报纸\s*(\d[\d,]+)", "newspaper_count", "篇"),
        (r"年鉴\s*(\d[\d,]+)", "yearbook_count", "种"),
        (r"全文文献总量\s*(\d[\d,]+)", "total_documents", "篇"),
    ]

    for pattern, indicator, unit in stat_patterns:
        matches = re.findall(pattern, body_text)
        for match in matches:
            value = int(match.replace(",", ""))
            items.append({
                "stat_date": datetime.now().strftime("%Y-%m-%d"),
                "category": "platform_statistics",
                "indicator": indicator,
                "value": float(value),
                "unit": unit,
                "source_url": source_url,
                "raw_data": f"Pattern: {pattern}, Match: {match}",
            })
    return items


def save_papers(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO paper_metadata
                (title, authors, source_journal, publish_date, abstract, keywords,
                 doi, citation_count, download_count, source_type, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("title", ""),
                item.get("authors", ""),
                item.get("source_journal", ""),
                item.get("publish_date", ""),
                item.get("abstract", ""),
                item.get("keywords", ""),
                item.get("doi", ""),
                item.get("citation_count", 0),
                item.get("download_count", 0),
                item.get("source_type", ""),
                item.get("source_url", ""),
                item.get("raw_data", ""),
                datetime.now().isoformat(),
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert paper: %s", e)
    conn.commit()
    return inserted


def save_journals(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO journal_metadata
                (journal_name, issn, cn_number, publisher, category, impact_factor,
                 description, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("journal_name", ""),
                item.get("issn", ""),
                item.get("cn_number", ""),
                item.get("publisher", ""),
                item.get("category", ""),
                item.get("impact_factor"),
                item.get("description", ""),
                item.get("source_url", ""),
                item.get("raw_data", ""),
                datetime.now().isoformat(),
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert journal: %s", e)
    conn.commit()
    return inserted


def save_stats(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO publication_stats
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
    logger.info("Starting CNKI (中国知网) Spider")
    logger.info("=" * 70)
    logger.info("NOTE: Full-text access requires subscription. This spider only")
    logger.info("extracts publicly available metadata (titles, abstracts, stats).")
    logger.info("=" * 70)

    conn = init_db()
    all_items: dict[str, list] = {"papers": [], "journals": [], "stats": []}

    logger.info("\n[1/%d] Fetching CNKI homepage: %s", len(URLS), URLS["home"])
    page = fetch_page(URLS["home"])
    if page:
        logger.info("  Status: %d", page["status"])
        stats = extract_publication_stats(page["html"], page["url"])
        all_items["stats"].extend(stats)
        logger.info("  -> Extracted %d publication statistics", len(stats))

    time.sleep(2)

    for i, (key, url) in enumerate(URLS.items()):
        if key == "home":
            continue
        logger.info("\n[%d/%d] Fetching %s: %s", i + 1, len(URLS), key, url)
        page = fetch_page(url)
        if page:
            logger.info("  Status: %d", page["status"])

            journals = extract_journal_metadata(page["html"], page["url"])
            all_items["journals"].extend(journals)
            logger.info("  -> Extracted %d journal records", len(journals))

            papers = extract_paper_metadata(page["html"], page["url"])
            all_items["papers"].extend(papers)
            logger.info("  -> Extracted %d paper records", len(papers))

            stats = extract_publication_stats(page["html"], page["url"])
            all_items["stats"].extend(stats)
            logger.info("  -> Extracted %d statistics", len(stats))

        time.sleep(3)

    total = 0
    if all_items["papers"]:
        n = save_papers(conn, all_items["papers"])
        logger.info("\nSaved %d paper records to database", n)
        total += n
    if all_items["journals"]:
        n = save_journals(conn, all_items["journals"])
        logger.info("Saved %d journal records to database", n)
        total += n
    if all_items["stats"]:
        n = save_stats(conn, all_items["stats"])
        logger.info("Saved %d statistics records to database", n)
        total += n

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    export_data = {
        "source": "CNKI (中国知网)",
        "source_url": "https://www.cnki.net/",
        "fetched_at": datetime.now().isoformat(),
        "auth_required": {
            "full_text": True,
            "detailed_metrics": True,
            "api_access": True,
        },
        "public_data": {
            "paper_titles": True,
            "abstracts": True,
            "citation_counts": True,
            "journal_metadata": True,
            "platform_statistics": True,
        },
        "papers": all_items["papers"],
        "journals": all_items["journals"],
        "statistics": all_items["stats"],
    }
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)
    logger.info("Exported data to JSON: %s", JSON_PATH)

    conn.close()

    print("\n" + "=" * 70)
    print("CNKI Spider - Execution Summary")
    print("=" * 70)
    print(f"Papers extracted: {len(all_items['papers'])}")
    print(f"Journals extracted: {len(all_items['journals'])}")
    print(f"Statistics extracted: {len(all_items['stats'])}")
    print(f"Total DB records: {total}")
    print(f"Database: {DB_PATH}")
    print(f"JSON output: {JSON_PATH}")
    print("=" * 70)
    print("\nAuthentication Notes:")
    print("  - Full-text PDFs: Requires institutional/personal subscription")
    print("  - Detailed citation metrics: Requires CNKI API agreement")
    print("  - Public data: titles, abstracts, basic citation counts, journal info")


if __name__ == "__main__":
    main()
