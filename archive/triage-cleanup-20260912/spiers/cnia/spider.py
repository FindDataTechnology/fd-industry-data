#!/usr/bin/env python3
"""
CNIA (China Non-ferrous Metal Industry Association) Spider
中国有色金属工业协会数据爬虫

Target: https://www.cnia.org.cn
Data: Industry statistics, production data, policy documents, association news
Focus: Mining production, smelting output, industry reports

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts production statistics and industry reports
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scrapling import Fetcher, Selector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("cnia")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "cnia.db"
JSON_STATS_PATH = OUTPUT_DIR / "cnia_statistics.json"
JSON_NEWS_PATH = OUTPUT_DIR / "cnia_news.json"
JSON_REPORTS_PATH = OUTPUT_DIR / "cnia_reports.json"

TARGET_URLS = {
    "statistics": [
        "https://www.cnia.org.cn/data/statistics",
        "https://www.cnia.org.cn/data/production",
        "https://www.cnia.org.cn/data/output",
    ],
    "news": [
        "https://www.cnia.org.cn/news/industry",
        "https://www.cnia.org.cn/news/association",
        "https://www.cnia.org.cn/news/policy",
    ],
    "reports": [
        "https://www.cnia.org.cn/reports/annual",
        "https://www.cnia.org.cn/reports/monthly",
        "https://www.cnia.org.cn/reports/analysis",
    ],
}

METALS = ["copper", "aluminum", "zinc", "lead", "nickel", "tin"]


def init_db():
    """Initialize SQLite database with CNIA schema."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS production_statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            metal TEXT NOT NULL,
            metal_cn TEXT,
            production_volume REAL,
            production_unit TEXT,
            growth_rate REAL,
            growth_unit TEXT DEFAULT '%',
            region TEXT,
            category TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, metal, category, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS industry_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            title TEXT,
            content TEXT,
            category TEXT,
            metal TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS industry_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            title TEXT,
            summary TEXT,
            content TEXT,
            report_type TEXT,
            metal TEXT,
            author TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def fetch_page(url: str, fetcher: Fetcher) -> dict | None:
    """Fetch a page with browser impersonation."""
    try:
        logger.info("Fetching: %s", url)
        response = fetcher.get(url, timeout=30)
        if response.status != 200:
            logger.warning("HTTP %d for %s", response.status, url)
            return None
        return {"url": url, "html": response.html, "status": response.status}
    except Exception as e:
        logger.error("Fetch failed for %s: %s", url, e)
        return None


def extract_statistics(html: str, url: str) -> list[dict]:
    """Extract production statistics from HTML tables."""
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers).lower()

        if not any(keyword in header_text for keyword in ["产量", "production", "output", "统计", "统计"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            item = {
                "period": extract_period(cells[0] if cells else ""),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            metal_info = identify_metal_in_cells(cells)
            if metal_info:
                item.update(metal_info)
            else:
                continue

            stats_data = extract_production_stats(cells)
            item.update(stats_data)

            if item.get("production_volume"):
                items.append(item)

    return items


def extract_news(html: str, url: str) -> list[dict]:
    """Extract industry news and policy documents."""
    sel = Selector(html)
    items = []

    for article in sel.css("article, .news-item, .article-item, .post, li"):
        title_elem = article.css("h1, h2, h3, h4, .title, a")
        if not title_elem:
            continue

        title = title_elem[0].text.strip() if title_elem else ""
        if not title or len(title) < 5:
            continue

        content_elem = article.css("p, .content, .summary, .excerpt, .desc")
        content = content_elem[0].text.strip() if content_elem else ""

        date_elem = article.css("time, .date, .publish-date, span")
        date = date_elem[0].text.strip() if date_elem else ""
        date = extract_date(date)

        category = "news"
        if "policy" in url.lower() or "政策" in title:
            category = "policy"
        elif "association" in url.lower() or "协会" in title:
            category = "association"
        elif "industry" in url.lower() or "行业" in title:
            category = "industry"

        metal = identify_metal_in_text(title + " " + content)

        items.append({
            "date": date,
            "title": title,
            "content": content[:500] if content else "",
            "category": category,
            "metal": metal,
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def extract_reports(html: str, url: str) -> list[dict]:
    """Extract industry reports and analysis."""
    sel = Selector(html)
    items = []

    for article in sel.css("article, .report-item, .article-item, .post"):
        title_elem = article.css("h1, h2, h3, .title")
        if not title_elem:
            continue

        title = title_elem[0].text.strip() if title_elem else ""
        if not title:
            continue

        summary_elem = article.css("p, .summary, .excerpt, .abstract")
        summary = summary_elem[0].text.strip() if summary_elem else ""

        content_elem = article.css(".content, .body, article")
        content = content_elem[0].text.strip() if content_elem else summary

        date_elem = article.css("time, .date, .publish-date")
        date = date_elem[0].text.strip() if date_elem else ""
        date = extract_date(date)

        report_type = "analysis"
        if "annual" in url.lower() or "年度" in title:
            report_type = "annual"
        elif "monthly" in url.lower() or "月度" in title:
            report_type = "monthly"
        elif "analysis" in url.lower() or "分析" in title:
            report_type = "analysis"

        metal = identify_metal_in_text(title + " " + summary)

        author_elem = article.css(".author, .byline, span")
        author = author_elem[0].text.strip() if author_elem else ""

        items.append({
            "date": date,
            "title": title,
            "summary": summary[:500] if summary else "",
            "content": content[:1000] if content else "",
            "report_type": report_type,
            "metal": metal,
            "author": author,
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def identify_metal_in_cells(cells: list[str]) -> dict | None:
    """Identify metal from table cells."""
    cell_text = " ".join(cells).lower()
    metal_map = {
        "copper": {"cn_name": "铜", "keywords": ["copper", "铜", "cu"]},
        "aluminum": {"cn_name": "铝", "keywords": ["aluminum", "铝", "al"]},
        "zinc": {"cn_name": "锌", "keywords": ["zinc", "锌", "zn"]},
        "lead": {"cn_name": "铅", "keywords": ["lead", "铅", "pb"]},
        "nickel": {"cn_name": "镍", "keywords": ["nickel", "镍", "ni"]},
        "tin": {"cn_name": "锡", "keywords": ["tin", "锡", "sn"]},
    }

    for metal_key, metal_info in metal_map.items():
        if any(keyword in cell_text for keyword in metal_info["keywords"]):
            return {
                "metal": metal_key,
                "metal_cn": metal_info["cn_name"],
            }
    return None


def identify_metal_in_text(text: str) -> str:
    """Identify metal mentioned in text."""
    text_lower = text.lower()
    metal_keywords = {
        "copper": ["copper", "铜", "cu"],
        "aluminum": ["aluminum", "铝", "al"],
        "zinc": ["zinc", "锌", "zn"],
        "lead": ["lead", "铅", "pb"],
        "nickel": ["nickel", "镍", "ni"],
        "tin": ["tin", "锡", "sn"],
    }

    for metal_key, keywords in metal_keywords.items():
        if any(keyword in text_lower for keyword in keywords):
            return metal_key
    return "general"


def extract_production_stats(cells: list[str]) -> dict:
    """Extract production statistics from table cells."""
    stats = {}
    number_pattern = r"[\d,]+\.?\d*"

    for i, cell in enumerate(cells):
        cell_clean = cell.replace(",", "").replace("，", "")
        numbers = re.findall(number_pattern, cell_clean)

        if not numbers:
            continue

        try:
            value = float(numbers[0])
        except (ValueError, IndexError):
            continue

        if "产量" in cell or "production" in cell.lower() or "output" in cell.lower():
            stats["production_volume"] = value
            stats["production_unit"] = "吨" if "吨" in cell else "万吨"
        elif "增长" in cell or "growth" in cell.lower() or "同比" in cell:
            if "%" in cell:
                stats["growth_rate"] = value
                stats["growth_unit"] = "%"
            else:
                stats["growth_rate"] = value
                stats["growth_unit"] = "吨"

    return stats


def extract_period(text: str) -> str:
    """Extract time period from text."""
    patterns = [
        r"(\d{4}年\d{1,2}月)",
        r"(\d{4}-\d{2})",
        r"(\d{4}年)",
        r"(\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return datetime.now().strftime("%Y-%m")


def save_stats_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    """Save statistics to SQLite database."""
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO production_statistics
                (period, metal, metal_cn, production_volume, production_unit,
                 growth_rate, growth_unit, region, category, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"),
                    item.get("metal"),
                    item.get("metal_cn"),
                    item.get("production_volume"),
                    item.get("production_unit"),
                    item.get("growth_rate"),
                    item.get("growth_unit"),
                    item.get("region"),
                    item.get("category"),
                    item.get("source_url"),
                    item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_news_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    """Save news to SQLite database."""
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO industry_news
                (date, title, content, category, metal, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"),
                    item.get("title"),
                    item.get("content"),
                    item.get("category"),
                    item.get("metal"),
                    item.get("source_url"),
                    item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_reports_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    """Save reports to SQLite database."""
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO industry_reports
                (date, title, summary, content, report_type, metal, author, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"),
                    item.get("title"),
                    item.get("summary"),
                    item.get("content"),
                    item.get("report_type"),
                    item.get("metal"),
                    item.get("author"),
                    item.get("source_url"),
                    item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_to_json(items: list[dict], json_path: Path) -> None:
    """Save items to JSON file."""
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def get_cnia_data(
    include_stats: bool = True,
    include_news: bool = True,
    include_reports: bool = True,
) -> dict:
    """Fetch data from China Non-ferrous Metal Industry Association.

    Args:
        include_stats: Whether to fetch production statistics.
        include_news: Whether to fetch industry news.
        include_reports: Whether to fetch industry reports.

    Returns:
        Dict with keys: 'statistics', 'news', 'reports'.
    """
    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "statistics": [],
        "news": [],
        "reports": [],
    }

    logger.info("Starting CNIA spider")

    if include_stats:
        for url in TARGET_URLS["statistics"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_statistics(page["html"], url)
                if items:
                    result["statistics"].extend(items)
                    logger.info("  Extracted %d statistics records", len(items))
            time.sleep(2)

    if include_news:
        for url in TARGET_URLS["news"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_news(page["html"], url)
                if items:
                    result["news"].extend(items)
                    logger.info("  Extracted %d news articles", len(items))
            time.sleep(2)

    if include_reports:
        for url in TARGET_URLS["reports"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_reports(page["html"], url)
                if items:
                    result["reports"].extend(items)
                    logger.info("  Extracted %d reports", len(items))
            time.sleep(2)

    if result["statistics"]:
        n_sqlite = save_stats_to_sqlite(result["statistics"], conn)
        save_to_json(result["statistics"], JSON_STATS_PATH)
        logger.info(
            "Saved %d statistics: %d to SQLite (%s), JSON (%s)",
            len(result["statistics"]),
            n_sqlite,
            DB_PATH,
            JSON_STATS_PATH,
        )

    if result["news"]:
        save_news_to_sqlite(result["news"], conn)
        save_to_json(result["news"], JSON_NEWS_PATH)
        logger.info("Saved %d news articles to JSON (%s)", len(result["news"]), JSON_NEWS_PATH)

    if result["reports"]:
        save_reports_to_sqlite(result["reports"], conn)
        save_to_json(result["reports"], JSON_REPORTS_PATH)
        logger.info("Saved %d reports to JSON (%s)", len(result["reports"]), JSON_REPORTS_PATH)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_cnia_data()
    print(f"\n{'=' * 70}")
    print(f"Total statistics records: {len(results['statistics'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"Total reports: {len(results['reports'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON (stats): {JSON_STATS_PATH}")
    print(f"JSON (news): {JSON_NEWS_PATH}")
    print(f"JSON (reports): {JSON_REPORTS_PATH}")
    print(f"{'=' * 70}")

    if results["statistics"]:
        print(f"\nProduction Statistics ({len(results['statistics'])} records):")
        for stat in results["statistics"][:10]:
            print(f"  {stat.get('period', 'N/A'):>12s} | {stat.get('metal_cn', 'N/A'):>4s} | "
                  f"{stat.get('production_volume', 'N/A'):>12} {stat.get('production_unit', '')}")

    if results["news"]:
        print(f"\nRecent News ({len(results['news'])} articles):")
        for article in results["news"][:5]:
            print(f"  [{article.get('date', 'N/A')}] {article.get('title', 'N/A')[:60]}")

    if results["reports"]:
        print(f"\nIndustry Reports ({len(results['reports'])} reports):")
        for report in results["reports"][:5]:
            print(f"  [{report.get('date', 'N/A')}] {report.get('title', 'N/A')[:60]}")
