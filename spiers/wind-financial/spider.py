#!/usr/bin/env python3
"""
Wind Financial Terminal Spider - 万得金融终端数据爬虫

Target: https://www.wind.com.cn
Data: Financial news, economic indicators, public market data

Authentication Requirements:
  - Wind terminal requires paid subscription (WFT account)
  - Public website has limited data: news headlines, macro data previews
  - API access requires WindQuant API license
  - Most detailed data is behind paywall

Publicly Accessible:
  - Financial news headlines and summaries
  - Economic indicator calendar
  - Market overview snapshots
  - Research report titles/abstracts

Premium (Subscription Required):
  - Real-time market data feeds
  - Historical financial statements
  - Detailed economic database
  - Custom analytics and screening

Architecture:
  - Fetcher with browser impersonation for anti-bot bypass
  - StealthyFetcher for Cloudflare-protected pages
  - Rate limiting (3s delay between requests)
  - SQLite + JSON output
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
logger = logging.getLogger("wind_financial")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "wind_financial.db"
JSON_PATH = OUTPUT_DIR / "wind_data.json"

NEWS_URLS = [
    "https://www.wind.com.cn/News/financialnews.html",
    "https://www.wind.com.cn/News/marketnews.html",
    "https://www.wind.com.cn/News/globalnews.html",
]

ECONOMIC_URLS = [
    "https://www.wind.com.cn/data/macro.html",
    "https://www.wind.com.cn/data/indicator.html",
]

MARKET_URLS = [
    "https://www.wind.com.cn/data/market.html",
    "https://www.wind.com.cn/data/stock.html",
]

REPORT_URLS = [
    "https://www.wind.com.cn/Research/report.html",
    "https://www.wind.com.cn/Research/industry.html",
]


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS financial_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            summary TEXT,
            category TEXT,
            source TEXT,
            publish_date TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(title, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS economic_indicators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            indicator_name TEXT NOT NULL,
            indicator_value TEXT,
            indicator_unit TEXT,
            release_date TEXT,
            previous_value TEXT,
            yoy_change TEXT,
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(indicator_name, release_date, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_overview (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_name TEXT NOT NULL,
            index_value REAL,
            change_value REAL,
            change_pct REAL,
            volume REAL,
            turnover REAL,
            trade_date TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(index_name, trade_date, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS research_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT,
            institution TEXT,
            category TEXT,
            abstract TEXT,
            publish_date TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(title, source_url)
        )
    """)
    conn.commit()
    return conn


def fetch_page(url: str, fetcher: Fetcher) -> dict | None:
    try:
        logger.info("Fetching: %s", url)
        response = fetcher.get(url, timeout=30, stealthy_headers=True)
        if response.status != 200:
            logger.warning("HTTP %d for %s", response.status, url)
            return None
        return {"url": url, "html": response.html, "status": response.status}
    except Exception as e:
        logger.error("Fetch failed for %s: %s", url, e)
        return None


def extract_news(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []
    now = datetime.now(timezone.utc).isoformat()

    category = "financial"
    if "market" in url:
        category = "market"
    elif "global" in url:
        category = "global"

    for article in sel.css("div.news-list li, div.article-list li, div.list-item, ul.news-list li"):
        title_el = article.css("a::text, h3::text, h4::text, .title::text")
        title = title_el[0].text.strip() if title_el else ""
        if not title:
            continue

        link_el = article.css("a::attr(href)")
        link = link_el[0].text.strip() if link_el else ""
        if link and not link.startswith("http"):
            link = "https://www.wind.com.cn" + link

        summary_el = article.css("p::text, .summary::text, .desc::text, .abstract::text")
        summary = summary_el[0].text.strip() if summary_el else ""

        date_el = article.css("span.date::text, .time::text, time::text")
        publish_date = ""
        if date_el:
            publish_date = extract_date(date_el[0].text.strip())

        items.append({
            "title": title,
            "summary": summary,
            "category": category,
            "source": "Wind",
            "publish_date": publish_date,
            "source_url": link or url,
            "scraped_at": now,
        })

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue
        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)
        if not any(kw in header_text for kw in ["新闻", "标题", "news", "title", "报告"]):
            continue
        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) >= 2 and cells[0]:
                items.append({
                    "title": cells[0],
                    "summary": cells[1] if len(cells) > 1 else "",
                    "category": category,
                    "source": "Wind",
                    "publish_date": extract_date(cells[-1]) if len(cells) > 2 else "",
                    "source_url": url,
                    "scraped_at": now,
                })

    return items


def extract_economic_indicators(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []
    now = datetime.now(timezone.utc).isoformat()

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue
        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)
        if not any(kw in header_text for kw in ["指标", "数据", "indicator", "GDP", "CPI", "PMI"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            item = {
                "indicator_name": cells[0],
                "indicator_value": cells[1] if len(cells) > 1 else "",
                "indicator_unit": detect_unit(cells[1]) if len(cells) > 1 else "",
                "release_date": extract_date(cells[2]) if len(cells) > 2 else "",
                "previous_value": cells[3] if len(cells) > 3 else "",
                "yoy_change": cells[4] if len(cells) > 4 else "",
                "region": "中国",
                "source_url": url,
                "scraped_at": now,
            }
            items.append(item)

    for card in sel.css("div.indicator-card, div.data-item, div.macro-item"):
        name_el = card.css(".name::text, .indicator-name::text, h4::text")
        value_el = card.css(".value::text, .indicator-value::text, .num::text")
        name = name_el[0].text.strip() if name_el else ""
        value = value_el[0].text.strip() if value_el else ""
        if name:
            items.append({
                "indicator_name": name,
                "indicator_value": value,
                "indicator_unit": detect_unit(value),
                "release_date": "",
                "previous_value": "",
                "yoy_change": "",
                "region": "中国",
                "source_url": url,
                "scraped_at": now,
            })

    return items


def extract_market_overview(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []
    now = datetime.now(timezone.utc).isoformat()

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue
        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)
        if not any(kw in header_text for kw in ["指数", "index", "收盘", "涨跌", "成交"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            item = {
                "index_name": cells[0],
                "index_value": parse_float(cells[1]) if len(cells) > 1 else None,
                "change_value": parse_float(cells[2]) if len(cells) > 2 else None,
                "change_pct": parse_float(cells[3]) if len(cells) > 3 else None,
                "volume": parse_float(cells[4]) if len(cells) > 4 else None,
                "turnover": parse_float(cells[5]) if len(cells) > 5 else None,
                "trade_date": "",
                "source_url": url,
                "scraped_at": now,
            }
            items.append(item)

    return items


def extract_reports(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []
    now = datetime.now(timezone.utc).isoformat()

    for item_el in sel.css("div.report-list li, div.research-item, div.report-item"):
        title_el = item_el.css("a::text, h3::text, .title::text")
        title = title_el[0].text.strip() if title_el else ""
        if not title:
            continue

        author_el = item_el.css(".author::text, .analyst::text")
        author = author_el[0].text.strip() if author_el else ""

        inst_el = item_el.css(".institution::text, .org::text, .source::text")
        institution = inst_el[0].text.strip() if inst_el else ""

        abstract_el = item_el.css(".abstract::text, .summary::text, p::text")
        abstract = abstract_el[0].text.strip() if abstract_el else ""

        date_el = item_el.css(".date::text, time::text, .time::text")
        publish_date = ""
        if date_el:
            publish_date = extract_date(date_el[0].text.strip())

        items.append({
            "title": title,
            "author": author,
            "institution": institution,
            "category": "research",
            "abstract": abstract,
            "publish_date": publish_date,
            "source_url": url,
            "scraped_at": now,
        })

    return items


def extract_date(text: str) -> str:
    patterns = [
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{4}年\d{1,2}月\d{1,2}日)",
        r"(\d{4}/\d{2}/\d{2})",
        r"(\d{2}/\d{2}/\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def detect_unit(text: str) -> str:
    if not text:
        return ""
    if "%" in text:
        return "%"
    if "亿" in text:
        return "亿元"
    if "万亿" in text:
        return "万亿元"
    if "万" in text:
        return "万元"
    if "元" in text:
        return "元"
    return ""


def parse_float(text: str) -> float | None:
    if not text:
        return None
    cleaned = text.replace(",", "").replace("，", "").replace("%", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def save_to_sqlite(conn: sqlite3.Connection, table: str, items: list[dict]) -> int:
    inserted = 0
    for item in items:
        try:
            if table == "financial_news":
                conn.execute(
                    """INSERT OR REPLACE INTO financial_news
                    (title, summary, category, source, publish_date, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (item["title"], item.get("summary", ""), item.get("category", ""),
                     item.get("source", ""), item.get("publish_date", ""),
                     item["source_url"], item["scraped_at"]),
                )
            elif table == "economic_indicators":
                conn.execute(
                    """INSERT OR REPLACE INTO economic_indicators
                    (indicator_name, indicator_value, indicator_unit, release_date,
                     previous_value, yoy_change, region, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["indicator_name"], item.get("indicator_value", ""),
                     item.get("indicator_unit", ""), item.get("release_date", ""),
                     item.get("previous_value", ""), item.get("yoy_change", ""),
                     item.get("region", ""), item["source_url"], item["scraped_at"]),
                )
            elif table == "market_overview":
                conn.execute(
                    """INSERT OR REPLACE INTO market_overview
                    (index_name, index_value, change_value, change_pct, volume, turnover,
                     trade_date, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["index_name"], item.get("index_value"),
                     item.get("change_value"), item.get("change_pct"),
                     item.get("volume"), item.get("turnover"),
                     item.get("trade_date", ""), item["source_url"], item["scraped_at"]),
                )
            elif table == "research_reports":
                conn.execute(
                    """INSERT OR REPLACE INTO research_reports
                    (title, author, institution, category, abstract, publish_date,
                     source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["title"], item.get("author", ""), item.get("institution", ""),
                     item.get("category", ""), item.get("abstract", ""),
                     item.get("publish_date", ""), item["source_url"], item["scraped_at"]),
                )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error (%s): %s", table, e)
    conn.commit()
    return inserted


def save_to_json(items: list[dict], json_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def get_wind_data(categories: list[str] | None = None) -> dict[str, list[dict]]:
    """Fetch public data from Wind Financial Terminal.

    Args:
        categories: List of categories to fetch.
            Available: news, economic, market, reports.
            Default: all.

    Returns:
        Dict with keys: news, economic_indicators, market_overview, reports.
    """
    if categories is None:
        categories = ["news", "economic", "market", "reports"]

    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()
    results = {
        "news": [],
        "economic_indicators": [],
        "market_overview": [],
        "reports": [],
    }

    if "news" in categories:
        logger.info("=== Fetching Financial News ===")
        for url in NEWS_URLS:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_news(page["html"], url)
                results["news"].extend(items)
                save_to_sqlite(conn, "financial_news", items)
                logger.info("  Extracted %d news items from %s", len(items), url)
            time.sleep(3)

    if "economic" in categories:
        logger.info("=== Fetching Economic Indicators ===")
        for url in ECONOMIC_URLS:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_economic_indicators(page["html"], url)
                results["economic_indicators"].extend(items)
                save_to_sqlite(conn, "economic_indicators", items)
                logger.info("  Extracted %d indicators from %s", len(items), url)
            time.sleep(3)

    if "market" in categories:
        logger.info("=== Fetching Market Overview ===")
        for url in MARKET_URLS:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_market_overview(page["html"], url)
                results["market_overview"].extend(items)
                save_to_sqlite(conn, "market_overview", items)
                logger.info("  Extracted %d market items from %s", len(items), url)
            time.sleep(3)

    if "reports" in categories:
        logger.info("=== Fetching Research Reports ===")
        for url in REPORT_URLS:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_reports(page["html"], url)
                results["reports"].extend(items)
                save_to_sqlite(conn, "research_reports", items)
                logger.info("  Extracted %d reports from %s", len(items), url)
            time.sleep(3)

    all_items = []
    for category_items in results.values():
        all_items.extend(category_items)
    save_to_json(all_items, JSON_PATH)

    total = sum(len(v) for v in results.values())
    logger.info("Done! %d total items -> SQLite (%s), JSON (%s)", total, DB_PATH, JSON_PATH)

    conn.close()
    return results


if __name__ == "__main__":
    results = get_wind_data()
    print(f"\n{'=' * 70}")
    print(f"Wind Financial Terminal Spider Results")
    print(f"{'=' * 70}")
    for key, items in results.items():
        print(f"  {key}: {len(items)} items")
    print(f"  SQLite: {DB_PATH}")
    print(f"  JSON:   {JSON_PATH}")
    print(f"{'=' * 70}")
