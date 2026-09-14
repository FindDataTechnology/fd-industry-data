#!/usr/bin/env python3
"""
China Association of Science and Technology (中国科协) Spider

Target: http://www.cast.org.cn/ (Score: 85)

Extracts publicly available data:
- Science and technology statistics
- Research institution data
- Academic conference information
- Technology development reports
- Policy documents and publications

Authentication Notes:
- Most content is publicly accessible (government organization)
- Conference registration may require login
- Report downloads are generally free
- No subscription required for public data
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
logger = logging.getLogger("cast-spider")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "cast_science_data.db"
JSON_PATH = OUTPUT_DIR / "cast_science_tech_data.json"

URLS = {
    "home": "http://www.cast.org.cn/",
    "news": "http://www.cast.org.cn/col/col60/index.html",
    "events": "http://www.cast.org.cn/col/col62/index.html",
    "publications": "http://www.cast.org.cn/col/col66/index.html",
    "science_stats": "http://www.cast.org.cn/col/col70/index.html",
    "policy": "http://www.cast.org.cn/col/col74/index.html",
    "institutions": "http://www.cast.org.cn/col/col80/index.html",
    "talent": "http://www.cast.org.cn/col/col84/index.html",
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
        CREATE TABLE IF NOT EXISTS news_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            publish_date TEXT,
            category TEXT,
            summary TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(title, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            conference_date TEXT,
            location TEXT,
            organizer TEXT,
            description TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(title, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS publications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            publish_date TEXT,
            pub_type TEXT,
            authors TEXT,
            description TEXT,
            download_url TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(title, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS institution_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            category TEXT,
            location TEXT,
            description TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(name, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS science_stats (
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


def extract_news_articles(html: str, source_url: str, category: str = "") -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)

    for item_el in sel.css("ul.news-list li, .list-item, .news-list li, .xxgk-list li, ul li"):
        title_el = item_el.css("a")
        title = title_el.first.text.strip() if title_el else ""
        if not title or len(title) < 4:
            continue

        date_el = item_el.css("span.date, .time, span:last-child")
        publish_date = ""
        if date_el:
            publish_date = date_el.first.text.strip()
        else:
            date_match = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", item_el.text)
            if date_match:
                publish_date = date_match.group(1)

        summary_el = item_el.css("p, .summary, .desc")
        summary = summary_el.first.text.strip() if summary_el else ""

        items.append({
            "title": title,
            "publish_date": publish_date,
            "category": category,
            "summary": summary[:300] if summary else "",
            "source_url": source_url,
            "raw_data": json.dumps({"title": title}, ensure_ascii=False),
        })
    return items


def extract_conferences(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)

    for item_el in sel.css("ul.news-list li, .list-item, .news-list li, ul li"):
        title_el = item_el.css("a")
        title = title_el.first.text.strip() if title_el else ""
        if not title or len(title) < 4:
            continue
        if not any(kw in title for kw in ["会议", "论坛", "峰会", "研讨会", "年会", "大会", "学术"]):
            continue

        date_el = item_el.css("span.date, .time, span:last-child")
        conference_date = date_el.first.text.strip() if date_el else ""

        desc_el = item_el.css("p, .summary, .desc")
        description = desc_el.first.text.strip() if desc_el else ""

        location = ""
        loc_match = re.search(r"(?:在|地点[：:]?)\s*([\u4e00-\u9fa5]{2,}(?:市|省|区))", title + " " + description)
        if loc_match:
            location = loc_match.group(1)

        items.append({
            "title": title,
            "conference_date": conference_date,
            "location": location,
            "organizer": "中国科协",
            "description": description[:300] if description else "",
            "source_url": source_url,
            "raw_data": json.dumps({"title": title}, ensure_ascii=False),
        })
    return items


def extract_publications(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)

    for item_el in sel.css("ul.news-list li, .list-item, .news-list li, ul li"):
        title_el = item_el.css("a")
        title = title_el.first.text.strip() if title_el else ""
        if not title or len(title) < 4:
            continue

        date_el = item_el.css("span.date, .time, span:last-child")
        publish_date = date_el.first.text.strip() if date_el else ""

        desc_el = item_el.css("p, .summary, .desc")
        description = desc_el.first.text.strip() if desc_el else ""

        download_el = item_el.css("a[href*='download'], a[href*='.pdf'], a[href*='.doc']")
        download_url = download_el.first.attrib.get("href", "") if download_el else ""

        pub_type = "report"
        if any(kw in title for kw in ["报告", "蓝皮书"]):
            pub_type = "report"
        elif any(kw in title for kw in ["期刊", "学报"]):
            pub_type = "journal"
        elif any(kw in title for kw in ["标准", "规范"]):
            pub_type = "standard"

        items.append({
            "title": title,
            "publish_date": publish_date,
            "pub_type": pub_type,
            "authors": "",
            "description": description[:300] if description else "",
            "download_url": download_url,
            "source_url": source_url,
            "raw_data": json.dumps({"title": title}, ensure_ascii=False),
        })
    return items


def extract_institutions(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)

    for item_el in sel.css("ul li, .list-item, .institution-list li, table tr"):
        name_el = item_el.css("a, td:first-child")
        name = name_el.first.text.strip() if name_el else ""
        if not name or len(name) < 2:
            continue
        if not any(kw in name for kw in ["学会", "协会", "研究会", "研究院", "研究所", "大学", "实验室", "中心"]):
            continue

        desc_el = item_el.css("p, .desc, .summary, td:nth-child(2)")
        description = desc_el.first.text.strip() if desc_el else ""

        category = ""
        if "学会" in name:
            category = "academic_society"
        elif "研究所" in name or "研究院" in name:
            category = "research_institute"
        elif "大学" in name:
            category = "university"
        elif "实验室" in name:
            category = "laboratory"

        items.append({
            "name": name,
            "category": category,
            "location": "",
            "description": description[:300] if description else "",
            "source_url": source_url,
            "raw_data": json.dumps({"name": name}, ensure_ascii=False),
        })
    return items


def extract_science_stats(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)
    body_text = sel.css("body").first.text if sel.css("body") else ""

    stat_patterns = [
        (r"全国科技工作者\s*(\d[\d,]+)", "sci_tech_workers", "人"),
        (r"科技人员\s*(\d[\d,]+)", "sci_tech_personnel", "人"),
        (r"研发人员\s*(\d[\d,]+)", "rd_personnel", "人"),
        (r"院士\s*(\d[\d,]+)", "academicians", "人"),
        (r"学会\s*(\d[\d,]+)", "academic_societies", "个"),
        (r"研发投入\s*(\d+\.?\d*)\s*(亿|万亿)", "rd_expenditure", "元"),
        (r"科技经费\s*(\d+\.?\d*)\s*(亿|万亿)", "sci_tech_funding", "元"),
        (r"专利\s*(\d[\d,]+)", "patents", "件"),
        (r"论文\s*(\d[\d,]+)", "papers", "篇"),
    ]

    for pattern, indicator, unit in stat_patterns:
        matches = re.findall(pattern, body_text)
        for match in matches:
            if isinstance(match, tuple):
                value = float(match[0].replace(",", ""))
            else:
                value = float(match.replace(",", ""))
            items.append({
                "stat_date": datetime.now().strftime("%Y-%m-%d"),
                "category": "science_statistics",
                "indicator": indicator,
                "value": value,
                "unit": unit,
                "source_url": source_url,
                "raw_data": f"Pattern: {pattern}",
            })
    return items


def save_news(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO news_articles
                (title, publish_date, category, summary, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("title", ""),
                item.get("publish_date", ""),
                item.get("category", ""),
                item.get("summary", ""),
                item.get("source_url", ""),
                item.get("raw_data", ""),
                datetime.now().isoformat(),
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert news: %s", e)
    conn.commit()
    return inserted


def save_conferences(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO conferences
                (title, conference_date, location, organizer, description, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("title", ""),
                item.get("conference_date", ""),
                item.get("location", ""),
                item.get("organizer", ""),
                item.get("description", ""),
                item.get("source_url", ""),
                item.get("raw_data", ""),
                datetime.now().isoformat(),
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert conference: %s", e)
    conn.commit()
    return inserted


def save_publications(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO publications
                (title, publish_date, pub_type, authors, description, download_url, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("title", ""),
                item.get("publish_date", ""),
                item.get("pub_type", ""),
                item.get("authors", ""),
                item.get("description", ""),
                item.get("download_url", ""),
                item.get("source_url", ""),
                item.get("raw_data", ""),
                datetime.now().isoformat(),
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert publication: %s", e)
    conn.commit()
    return inserted


def save_institutions(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO institution_data
                (name, category, location, description, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("name", ""),
                item.get("category", ""),
                item.get("location", ""),
                item.get("description", ""),
                item.get("source_url", ""),
                item.get("raw_data", ""),
                datetime.now().isoformat(),
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert institution: %s", e)
    conn.commit()
    return inserted


def save_stats(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO science_stats
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


CATEGORY_MAP = {
    "news": "科协新闻",
    "events": "学术会议",
    "publications": "科技出版物",
    "science_stats": "科技统计",
    "policy": "科技政策",
    "institutions": "科研机构",
    "talent": "科技人才",
}


def main():
    logger.info("=" * 70)
    logger.info("Starting CAST (中国科协) Spider")
    logger.info("=" * 70)
    logger.info("NOTE: CAST is a government organization. Most content is")
    logger.info("publicly accessible without authentication.")
    logger.info("=" * 70)

    conn = init_db()
    all_items: dict[str, list] = {
        "news": [], "conferences": [], "publications": [],
        "institutions": [], "stats": []
    }

    logger.info("\n[1/%d] Fetching CAST homepage: %s", len(URLS), URLS["home"])
    page = fetch_page(URLS["home"])
    if page:
        logger.info("  Status: %d", page["status"])
        news = extract_news_articles(page["html"], page["url"], "首页要闻")
        all_items["news"].extend(news)
        logger.info("  -> Extracted %d news articles", len(news))

        confs = extract_conferences(page["html"], page["url"])
        all_items["conferences"].extend(confs)
        logger.info("  -> Extracted %d conference records", len(confs))

        stats = extract_science_stats(page["html"], page["url"])
        all_items["stats"].extend(stats)
        logger.info("  -> Extracted %d statistics", len(stats))

    time.sleep(2)

    for i, (key, url) in enumerate(URLS.items()):
        if key == "home":
            continue
        category = CATEGORY_MAP.get(key, key)
        logger.info("\n[%d/%d] Fetching %s (%s): %s", i + 1, len(URLS), key, category, url)
        page = fetch_page(url)
        if page:
            logger.info("  Status: %d", page["status"])

            news = extract_news_articles(page["html"], page["url"], category)
            all_items["news"].extend(news)
            logger.info("  -> Extracted %d news articles", len(news))

            confs = extract_conferences(page["html"], page["url"])
            all_items["conferences"].extend(confs)
            logger.info("  -> Extracted %d conference records", len(confs))

            pubs = extract_publications(page["html"], page["url"])
            all_items["publications"].extend(pubs)
            logger.info("  -> Extracted %d publication records", len(pubs))

            insts = extract_institutions(page["html"], page["url"])
            all_items["institutions"].extend(insts)
            logger.info("  -> Extracted %d institution records", len(insts))

            stats = extract_science_stats(page["html"], page["url"])
            all_items["stats"].extend(stats)
            logger.info("  -> Extracted %d statistics", len(stats))

        time.sleep(3)

    total = 0
    if all_items["news"]:
        n = save_news(conn, all_items["news"])
        logger.info("\nSaved %d news articles to database", n)
        total += n
    if all_items["conferences"]:
        n = save_conferences(conn, all_items["conferences"])
        logger.info("Saved %d conference records to database", n)
        total += n
    if all_items["publications"]:
        n = save_publications(conn, all_items["publications"])
        logger.info("Saved %d publication records to database", n)
        total += n
    if all_items["institutions"]:
        n = save_institutions(conn, all_items["institutions"])
        logger.info("Saved %d institution records to database", n)
        total += n
    if all_items["stats"]:
        n = save_stats(conn, all_items["stats"])
        logger.info("Saved %d statistics records to database", n)
        total += n

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    export_data = {
        "source": "China Association of Science and Technology (中国科协)",
        "source_url": "http://www.cast.org.cn/",
        "fetched_at": datetime.now().isoformat(),
        "auth_required": {
            "conference_registration": True,
            "all_other_data": False,
        },
        "public_data": {
            "news_articles": True,
            "conference_info": True,
            "publications": True,
            "institution_data": True,
            "science_statistics": True,
            "policy_documents": True,
        },
        "news": all_items["news"],
        "conferences": all_items["conferences"],
        "publications": all_items["publications"],
        "institutions": all_items["institutions"],
        "statistics": all_items["stats"],
    }
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)
    logger.info("Exported data to JSON: %s", JSON_PATH)

    conn.close()

    print("\n" + "=" * 70)
    print("CAST Spider - Execution Summary")
    print("=" * 70)
    print(f"News articles: {len(all_items['news'])}")
    print(f"Conferences: {len(all_items['conferences'])}")
    print(f"Publications: {len(all_items['publications'])}")
    print(f"Institutions: {len(all_items['institutions'])}")
    print(f"Statistics: {len(all_items['stats'])}")
    print(f"Total DB records: {total}")
    print(f"Database: {DB_PATH}")
    print(f"JSON output: {JSON_PATH}")
    print("=" * 70)
    print("\nAccess Notes:")
    print("  - All content publicly accessible (government organization)")
    print("  - Conference registration may require login")
    print("  - Report downloads are generally free")
    print("  - No subscription required for public data")


if __name__ == "__main__":
    main()
