#!/usr/bin/env python3
"""
199IT Internet Data Center Spider

Target: https://www.199it.com/ (Score: 70)

Extracts publicly available data:
- Industry research report metadata
- Market analysis summaries
- Internet industry statistics
- Technology trend reports

Authentication Notes:
- Report titles, summaries, and categories are publicly accessible
- Full report PDFs may require registration or payment
- Some premium reports require paid membership
- Newsletter subscription available for updates
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
logger = logging.getLogger("199it-spider")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "199it_research_data.db"
JSON_PATH = OUTPUT_DIR / "199it_research_reports.json"

URLS = {
    "home": "https://www.199it.com/",
    "reports": "https://www.199it.com/archives/category/report",
    "internet": "https://www.199it.com/archives/category/internet",
    "ecommerce": "https://www.199it.com/archives/category/ecommerce",
    "mobile": "https://www.199it.com/archives/category/mobile",
    "social": "https://www.199it.com/archives/category/social",
    "cloud": "https://www.199it.com/archives/category/cloud-computing",
    "ai": "https://www.199it.com/archives/category/ai",
    "fintech": "https://www.199it.com/archives/category/fintech",
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
        CREATE TABLE IF NOT EXISTS research_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            category TEXT,
            source_organization TEXT,
            publish_date TEXT,
            summary TEXT,
            tags TEXT,
            report_type TEXT,
            download_url TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(title, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS industry_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stat_date TEXT,
            category TEXT,
            indicator TEXT,
            value REAL,
            unit TEXT,
            source_report TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(stat_date, category, indicator, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trend_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trend_date TEXT,
            category TEXT,
            topic TEXT,
            description TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(trend_date, topic, source_url)
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


def extract_reports(html: str, source_url: str, category: str = "") -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)

    for article in sel.css("article, .post, .entry, .blog-post, .item-list li"):
        title_el = article.css("h1 a, h2 a, h3 a, .entry-title a, .post-title a")
        title = title_el.first.text.strip() if title_el else ""
        if not title or len(title) < 4:
            continue

        date_el = article.css("time, .date, .post-date, .entry-date, .time")
        publish_date = ""
        if date_el:
            date_text = date_el.first.attrib.get("datetime", "") or date_el.first.text.strip()
            publish_date = date_text

        summary_el = article.css(".entry-summary, .excerpt, .summary, p")
        summary = ""
        if summary_el:
            for p_el in summary_el:
                text = p_el.text.strip()
                if len(text) > 20:
                    summary = text
                    break

        tags_el = article.css(".tags a, .tag a, .category a")
        tags = ", ".join(t.text.strip() for t in tags_el if t.text.strip())

        source_org = ""
        for text_match in re.findall(r"([\u4e00-\u9fa5]{2,}(?:研究院|咨询公司|研究中心|实验室|智库))", title + " " + summary):
            source_org = text_match
            break

        download_el = article.css("a[href*='download'], a[href*='pdf'], a.download")
        download_url = ""
        if download_el:
            download_url = download_el.first.attrib.get("href", "")

        items.append({
            "title": title,
            "category": category,
            "source_organization": source_org,
            "publish_date": publish_date,
            "summary": summary[:500] if summary else "",
            "tags": tags,
            "report_type": "industry_report",
            "download_url": download_url,
            "source_url": source_url,
            "raw_data": json.dumps({"title": title}, ensure_ascii=False),
        })
    return items


def extract_stats_from_text(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)
    body_text = sel.css("body").first.text if sel.css("body") else ""

    stat_patterns = [
        (r"用户规模[达到约为]*\s*(\d+\.?\d*)\s*(亿|万|百万)", "user_scale"),
        (r"市场规模[达到约为]*\s*(\d+\.?\d*)\s*(亿|万亿|万元)", "market_size"),
        (r"同比增长\s*(\d+\.?\d*)\s*%", "yoy_growth"),
        (r"环比增长\s*(\d+\.?\d*)\s*%", "qoq_growth"),
        (r"渗透率[达到约为]*\s*(\d+\.?\d*)\s*%", "penetration_rate"),
        (r"市场份额[达到约为]*\s*(\d+\.?\d*)\s*%", "market_share"),
    ]

    for pattern, indicator in stat_patterns:
        matches = re.findall(pattern, body_text)
        for match in matches:
            if isinstance(match, tuple):
                value = float(match[0])
                unit = match[1] if len(match) > 1 else ""
            else:
                value = float(match)
                unit = ""

            if "%" in pattern:
                unit = "%"

            items.append({
                "stat_date": datetime.now().strftime("%Y-%m-%d"),
                "category": "industry_metric",
                "indicator": indicator,
                "value": value,
                "unit": unit,
                "source_report": "",
                "source_url": source_url,
                "raw_data": f"Pattern: {pattern}",
            })
    return items


def extract_trends(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)

    for article in sel.css("article, .post, .entry, .blog-post"):
        title_el = article.css("h1 a, h2 a, h3 a, .entry-title a")
        title = title_el.first.text.strip() if title_el else ""
        if not title or len(title) < 4:
            continue

        date_el = article.css("time, .date, .post-date")
        trend_date = ""
        if date_el:
            trend_date = date_el.first.attrib.get("datetime", "") or date_el.first.text.strip()

        summary_el = article.css(".entry-summary, .excerpt, p")
        description = ""
        if summary_el:
            for p_el in summary_el:
                text = p_el.text.strip()
                if len(text) > 20:
                    description = text
                    break

        items.append({
            "trend_date": trend_date,
            "category": "tech_trend",
            "topic": title,
            "description": description[:300] if description else "",
            "source_url": source_url,
            "raw_data": json.dumps({"title": title}, ensure_ascii=False),
        })
    return items


def save_reports(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO research_reports
                (title, category, source_organization, publish_date, summary,
                 tags, report_type, download_url, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("title", ""),
                item.get("category", ""),
                item.get("source_organization", ""),
                item.get("publish_date", ""),
                item.get("summary", ""),
                item.get("tags", ""),
                item.get("report_type", ""),
                item.get("download_url", ""),
                item.get("source_url", ""),
                item.get("raw_data", ""),
                datetime.now().isoformat(),
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert report: %s", e)
    conn.commit()
    return inserted


def save_stats(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO industry_stats
                (stat_date, category, indicator, value, unit, source_report, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("stat_date", ""),
                item.get("category", ""),
                item.get("indicator", ""),
                item.get("value"),
                item.get("unit", ""),
                item.get("source_report", ""),
                item.get("source_url", ""),
                item.get("raw_data", ""),
                datetime.now().isoformat(),
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert stat: %s", e)
    conn.commit()
    return inserted


def save_trends(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO trend_data
                (trend_date, category, topic, description, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("trend_date", ""),
                item.get("category", ""),
                item.get("topic", ""),
                item.get("description", ""),
                item.get("source_url", ""),
                item.get("raw_data", ""),
                datetime.now().isoformat(),
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert trend: %s", e)
    conn.commit()
    return inserted


CATEGORY_MAP = {
    "reports": "行业报告",
    "internet": "互联网",
    "ecommerce": "电子商务",
    "mobile": "移动互联网",
    "social": "社交媒体",
    "cloud": "云计算",
    "ai": "人工智能",
    "fintech": "金融科技",
}


def main():
    logger.info("=" * 70)
    logger.info("Starting 199IT Internet Data Center Spider")
    logger.info("=" * 70)
    logger.info("NOTE: Report summaries are public. Full PDF downloads may")
    logger.info("require registration or paid membership.")
    logger.info("=" * 70)

    conn = init_db()
    all_items: dict[str, list] = {"reports": [], "stats": [], "trends": []}

    logger.info("\n[1/%d] Fetching 199IT homepage: %s", len(URLS), URLS["home"])
    page = fetch_page(URLS["home"])
    if page:
        logger.info("  Status: %d", page["status"])
        reports = extract_reports(page["html"], page["url"], "首页推荐")
        all_items["reports"].extend(reports)
        logger.info("  -> Extracted %d report records", len(reports))

        trends = extract_trends(page["html"], page["url"])
        all_items["trends"].extend(trends)
        logger.info("  -> Extracted %d trend records", len(trends))

    time.sleep(2)

    for i, (key, url) in enumerate(URLS.items()):
        if key == "home":
            continue
        category = CATEGORY_MAP.get(key, key)
        logger.info("\n[%d/%d] Fetching %s (%s): %s", i + 1, len(URLS), key, category, url)
        page = fetch_page(url)
        if page:
            logger.info("  Status: %d", page["status"])

            reports = extract_reports(page["html"], page["url"], category)
            all_items["reports"].extend(reports)
            logger.info("  -> Extracted %d report records", len(reports))

            stats = extract_stats_from_text(page["html"], page["url"])
            all_items["stats"].extend(stats)
            logger.info("  -> Extracted %d industry statistics", len(stats))

            trends = extract_trends(page["html"], page["url"])
            all_items["trends"].extend(trends)
            logger.info("  -> Extracted %d trend records", len(trends))

        time.sleep(3)

    total = 0
    if all_items["reports"]:
        n = save_reports(conn, all_items["reports"])
        logger.info("\nSaved %d report records to database", n)
        total += n
    if all_items["stats"]:
        n = save_stats(conn, all_items["stats"])
        logger.info("Saved %d statistics records to database", n)
        total += n
    if all_items["trends"]:
        n = save_trends(conn, all_items["trends"])
        logger.info("Saved %d trend records to database", n)
        total += n

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    export_data = {
        "source": "199IT Internet Data Center",
        "source_url": "https://www.199it.com/",
        "fetched_at": datetime.now().isoformat(),
        "auth_required": {
            "full_report_pdfs": True,
            "premium_reports": True,
        },
        "public_data": {
            "report_titles_summaries": True,
            "industry_statistics": True,
            "tech_trends": True,
            "category_browsing": True,
        },
        "reports": all_items["reports"],
        "statistics": all_items["stats"],
        "trends": all_items["trends"],
    }
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)
    logger.info("Exported data to JSON: %s", JSON_PATH)

    conn.close()

    print("\n" + "=" * 70)
    print("199IT Spider - Execution Summary")
    print("=" * 70)
    print(f"Reports extracted: {len(all_items['reports'])}")
    print(f"Statistics extracted: {len(all_items['stats'])}")
    print(f"Trends extracted: {len(all_items['trends'])}")
    print(f"Total DB records: {total}")
    print(f"Database: {DB_PATH}")
    print(f"JSON output: {JSON_PATH}")
    print("=" * 70)
    print("\nAccess Notes:")
    print("  - Report titles & summaries: Publicly accessible")
    print("  - Full report PDFs: May require registration or paid membership")
    print("  - Premium reports: Paid membership required")
    print("  - Industry statistics: Publicly visible in articles")


if __name__ == "__main__":
    main()
