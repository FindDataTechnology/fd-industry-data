#!/usr/bin/env python3
"""
Wanfang Data (万方数据) Spider

Target: https://www.wanfangdata.com.cn/ (Score: 85)

Extracts publicly available metadata:
- Academic paper metadata (titles, authors, abstracts)
- Patent information (titles, applicants, abstracts)
- Standards documents metadata
- Conference proceedings metadata

Authentication Notes:
- Full-text access requires institutional or personal subscription
- Paper metadata (titles, abstracts) is publicly searchable
- Patent abstracts are generally public
- Standards full-text may require purchase
- API access requires formal agreement
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
logger = logging.getLogger("wanfang-spider")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "wanfang_data.db"
JSON_PATH = OUTPUT_DIR / "wanfang_metadata.json"

URLS = {
    "home": "https://www.wanfangdata.com.cn/",
    "papers": "https://www.wanfangdata.com.cn/search/searchList.do?searchType=all",
    "patents": "https://www.wanfangdata.com.cn/search/searchList.do?searchType=patent",
    "standards": "https://www.wanfangdata.com.cn/search/searchList.do?searchType=standard",
    "conferences": "https://www.wanfangdata.com.cn/search/searchList.do?searchType=conference",
    "degree_thesis": "https://www.wanfangdata.com.cn/search/searchList.do?searchType=degree",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Referer": "https://www.wanfangdata.com.cn/",
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
            source TEXT,
            publish_date TEXT,
            abstract TEXT,
            keywords TEXT,
            doi TEXT,
            citation_count INTEGER DEFAULT 0,
            document_type TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(title, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS patent_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            applicant TEXT,
            inventor TEXT,
            patent_number TEXT,
            application_date TEXT,
            publication_date TEXT,
            abstract TEXT,
            ipc_classification TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(title, patent_number, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS standard_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            standard_number TEXT,
            title TEXT,
            status TEXT,
            issue_date TEXT,
            implement_date TEXT,
            publisher TEXT,
            category TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(standard_number, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS platform_stats (
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


def extract_paper_metadata(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)

    for item_el in sel.css(".result-list .result-item, .search-result-list li, .normal-list li"):
        title_el = item_el.css(".title a, h3 a, .result-title a")
        title = title_el.first.text.strip() if title_el else ""
        if not title:
            continue

        authors_el = item_el.css(".author, .authors, .writer")
        authors = authors_el.first.text.strip() if authors_el else ""

        source_el = item_el.css(".source, .journal-name, .periodical")
        source = source_el.first.text.strip() if source_el else ""

        date_el = item_el.css(".date, .time, .year")
        publish_date = date_el.first.text.strip() if date_el else ""

        abstract_el = item_el.css(".abstract, .summary, .content")
        abstract = abstract_el.first.text.strip() if abstract_el else ""

        keywords_el = item_el.css(".keyword, .keywords, .tags")
        keywords = keywords_el.first.text.strip() if keywords_el else ""

        cite_el = item_el.css(".cite, .citation, .be-cited")
        citation_count = 0
        if cite_el:
            cite_text = cite_el.first.text.strip()
            cite_match = re.search(r"\d+", cite_text)
            if cite_match:
                citation_count = int(cite_match.group())

        items.append({
            "title": title,
            "authors": authors,
            "source": source,
            "publish_date": publish_date,
            "abstract": abstract[:500] if abstract else "",
            "keywords": keywords,
            "citation_count": citation_count,
            "document_type": "paper",
            "source_url": source_url,
            "raw_data": json.dumps({"title": title}, ensure_ascii=False),
        })
    return items


def extract_patent_metadata(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)

    for item_el in sel.css(".result-list .result-item, .search-result-list li, .normal-list li"):
        title_el = item_el.css(".title a, h3 a, .result-title a")
        title = title_el.first.text.strip() if title_el else ""
        if not title:
            continue

        applicant_el = item_el.css(".applicant, .assignee")
        applicant = applicant_el.first.text.strip() if applicant_el else ""

        inventor_el = item_el.css(".inventor, .inventors")
        inventor = inventor_el.first.text.strip() if inventor_el else ""

        number_el = item_el.css(".patent-number, .application-number, .pub-number")
        patent_number = number_el.first.text.strip() if number_el else ""

        date_el = item_el.css(".date, .pub-date, .publication-date")
        publication_date = date_el.first.text.strip() if date_el else ""

        abstract_el = item_el.css(".abstract, .summary")
        abstract = abstract_el.first.text.strip() if abstract_el else ""

        ipc_el = item_el.css(".ipc, .classification, .ipc-class")
        ipc_classification = ipc_el.first.text.strip() if ipc_el else ""

        items.append({
            "title": title,
            "applicant": applicant,
            "inventor": inventor,
            "patent_number": patent_number,
            "application_date": "",
            "publication_date": publication_date,
            "abstract": abstract[:500] if abstract else "",
            "ipc_classification": ipc_classification,
            "source_url": source_url,
            "raw_data": json.dumps({"title": title, "patent_number": patent_number}, ensure_ascii=False),
        })
    return items


def extract_standard_metadata(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)

    for item_el in sel.css(".result-list .result-item, .search-result-list li, .normal-list li"):
        title_el = item_el.css(".title a, h3 a, .result-title a")
        title = title_el.first.text.strip() if title_el else ""
        if not title:
            continue

        number_el = item_el.css(".standard-number, .std-number, .number")
        standard_number = number_el.first.text.strip() if number_el else ""

        status_el = item_el.css(".status, .std-status")
        status = status_el.first.text.strip() if status_el else ""

        date_el = item_el.css(".date, .issue-date, .pub-date")
        issue_date = date_el.first.text.strip() if date_el else ""

        publisher_el = item_el.css(".publisher, .issuing-body")
        publisher = publisher_el.first.text.strip() if publisher_el else ""

        items.append({
            "standard_number": standard_number,
            "title": title,
            "status": status,
            "issue_date": issue_date,
            "implement_date": "",
            "publisher": publisher,
            "category": "",
            "source_url": source_url,
            "raw_data": json.dumps({"title": title, "standard_number": standard_number}, ensure_ascii=False),
        })
    return items


def extract_platform_stats(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)
    body_text = sel.css("body").first.text if sel.css("body") else ""

    stat_patterns = [
        (r"期刊\s*(\d[\d,]+)", "journal_count", "种"),
        (r"论文\s*(\d[\d,]+)", "paper_count", "篇"),
        (r"学位论文\s*(\d[\d,]+)", "thesis_count", "篇"),
        (r"会议论文\s*(\d[\d,]+)", "conference_paper_count", "篇"),
        (r"专利\s*(\d[\d,]+)", "patent_count", "件"),
        (r"标准\s*(\d[\d,]+)", "standard_count", "项"),
        (r"地方志\s*(\d[\d,]+)", "local_records_count", "种"),
        (r"文献总量\s*(\d[\d,]+)", "total_documents", "篇"),
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
                (title, authors, source, publish_date, abstract, keywords,
                 doi, citation_count, document_type, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("title", ""),
                item.get("authors", ""),
                item.get("source", ""),
                item.get("publish_date", ""),
                item.get("abstract", ""),
                item.get("keywords", ""),
                item.get("doi", ""),
                item.get("citation_count", 0),
                item.get("document_type", ""),
                item.get("source_url", ""),
                item.get("raw_data", ""),
                datetime.now().isoformat(),
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert paper: %s", e)
    conn.commit()
    return inserted


def save_patents(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO patent_metadata
                (title, applicant, inventor, patent_number, application_date,
                 publication_date, abstract, ipc_classification, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("title", ""),
                item.get("applicant", ""),
                item.get("inventor", ""),
                item.get("patent_number", ""),
                item.get("application_date", ""),
                item.get("publication_date", ""),
                item.get("abstract", ""),
                item.get("ipc_classification", ""),
                item.get("source_url", ""),
                item.get("raw_data", ""),
                datetime.now().isoformat(),
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert patent: %s", e)
    conn.commit()
    return inserted


def save_standards(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO standard_metadata
                (standard_number, title, status, issue_date, implement_date,
                 publisher, category, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("standard_number", ""),
                item.get("title", ""),
                item.get("status", ""),
                item.get("issue_date", ""),
                item.get("implement_date", ""),
                item.get("publisher", ""),
                item.get("category", ""),
                item.get("source_url", ""),
                item.get("raw_data", ""),
                datetime.now().isoformat(),
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert standard: %s", e)
    conn.commit()
    return inserted


def save_stats(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO platform_stats
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
    logger.info("Starting Wanfang Data (万方数据) Spider")
    logger.info("=" * 70)
    logger.info("NOTE: Full-text access requires subscription. This spider only")
    logger.info("extracts publicly available metadata.")
    logger.info("=" * 70)

    conn = init_db()
    all_items: dict[str, list] = {
        "papers": [], "patents": [], "standards": [], "stats": []
    }

    logger.info("\n[1/%d] Fetching Wanfang homepage: %s", len(URLS), URLS["home"])
    page = fetch_page(URLS["home"])
    if page:
        logger.info("  Status: %d", page["status"])
        stats = extract_platform_stats(page["html"], page["url"])
        all_items["stats"].extend(stats)
        logger.info("  -> Extracted %d platform statistics", len(stats))

    time.sleep(2)

    for i, (key, url) in enumerate(URLS.items()):
        if key == "home":
            continue
        logger.info("\n[%d/%d] Fetching %s: %s", i + 1, len(URLS), key, url)
        page = fetch_page(url)
        if page:
            logger.info("  Status: %d", page["status"])

            if key in ("papers", "conferences", "degree_thesis"):
                papers = extract_paper_metadata(page["html"], page["url"])
                all_items["papers"].extend(papers)
                logger.info("  -> Extracted %d paper records", len(papers))
            elif key == "patents":
                patents = extract_patent_metadata(page["html"], page["url"])
                all_items["patents"].extend(patents)
                logger.info("  -> Extracted %d patent records", len(patents))
            elif key == "standards":
                standards = extract_standard_metadata(page["html"], page["url"])
                all_items["standards"].extend(standards)
                logger.info("  -> Extracted %d standard records", len(standards))

            stats = extract_platform_stats(page["html"], page["url"])
            all_items["stats"].extend(stats)
            logger.info("  -> Extracted %d statistics", len(stats))

        time.sleep(3)

    total = 0
    if all_items["papers"]:
        n = save_papers(conn, all_items["papers"])
        logger.info("\nSaved %d paper records to database", n)
        total += n
    if all_items["patents"]:
        n = save_patents(conn, all_items["patents"])
        logger.info("Saved %d patent records to database", n)
        total += n
    if all_items["standards"]:
        n = save_standards(conn, all_items["standards"])
        logger.info("Saved %d standard records to database", n)
        total += n
    if all_items["stats"]:
        n = save_stats(conn, all_items["stats"])
        logger.info("Saved %d statistics records to database", n)
        total += n

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    export_data = {
        "source": "Wanfang Data (万方数据)",
        "source_url": "https://www.wanfangdata.com.cn/",
        "fetched_at": datetime.now().isoformat(),
        "auth_required": {
            "full_text": True,
            "standards_full_text": True,
            "api_access": True,
        },
        "public_data": {
            "paper_titles_abstracts": True,
            "patent_abstracts": True,
            "standard_metadata": True,
            "platform_statistics": True,
        },
        "papers": all_items["papers"],
        "patents": all_items["patents"],
        "standards": all_items["standards"],
        "statistics": all_items["stats"],
    }
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)
    logger.info("Exported data to JSON: %s", JSON_PATH)

    conn.close()

    print("\n" + "=" * 70)
    print("Wanfang Data Spider - Execution Summary")
    print("=" * 70)
    print(f"Papers extracted: {len(all_items['papers'])}")
    print(f"Patents extracted: {len(all_items['patents'])}")
    print(f"Standards extracted: {len(all_items['standards'])}")
    print(f"Statistics extracted: {len(all_items['stats'])}")
    print(f"Total DB records: {total}")
    print(f"Database: {DB_PATH}")
    print(f"JSON output: {JSON_PATH}")
    print("=" * 70)
    print("\nAuthentication Notes:")
    print("  - Full-text papers: Requires institutional/personal subscription")
    print("  - Standards full-text: May require purchase")
    print("  - Patent abstracts: Generally public")
    print("  - API access: Requires formal agreement with Wanfang")


if __name__ == "__main__":
    main()
