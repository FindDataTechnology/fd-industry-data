#!/usr/bin/env python3
"""
Tonghuashun iFinD Spider - 同花顺iFinD数据爬虫

Target: https://www.5ifin.com
Data: Public financial data, market trends, industry reports

Authentication Requirements:
  - iFinD terminal requires paid subscription (institutional/individual)
  - Public website provides: news, basic quotes, fund rankings
  - API access requires iFinD Data API license
  - Advanced screening and analytics are premium-only

Publicly Accessible:
  - Financial news and market commentary
  - Basic stock quotes (delayed)
  - Fund rankings and NAV data
  - Industry trend summaries
  - Economic calendar

Premium (Subscription Required):
  - Real-time Level-2 market data
  - Financial statement database
  - Custom stock screening
  - Analyst consensus estimates
  - Institutional research database

Architecture:
  - Fetcher with browser impersonation
  - Rate limiting (3s delay)
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
logger = logging.getLogger("ifind_5ifin")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "ifind_5ifin.db"
JSON_PATH = OUTPUT_DIR / "ifind_data.json"

NEWS_URLS = [
    "https://www.5ifin.com/news/stock",
    "https://www.5ifin.com/news/finance",
    "https://www.5ifin.com/news/global",
]

MARKET_URLS = [
    "https://www.5ifin.com/quote/stock",
    "https://www.5ifin.com/quote/index",
    "https://www.5ifin.com/quote/fund",
]

INDUSTRY_URLS = [
    "https://www.5ifin.com/industry/overview",
    "https://www.5ifin.com/industry/trends",
]

FUND_URLS = [
    "https://www.5ifin.com/fund/ranking",
    "https://www.5ifin.com/fund/nav",
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
            source TEXT DEFAULT 'iFinD',
            publish_date TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(title, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT,
            stock_name TEXT,
            price REAL,
            change_value REAL,
            change_pct REAL,
            volume REAL,
            turnover REAL,
            market_cap REAL,
            pe_ratio REAL,
            trade_date TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(stock_code, trade_date, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS industry_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            industry_name TEXT NOT NULL,
            change_pct REAL,
            turnover REAL,
            leading_stock TEXT,
            leading_change REAL,
            sentiment TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(industry_name, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fund_ranking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fund_code TEXT,
            fund_name TEXT,
            fund_type TEXT,
            nav REAL,
            accumulated_nav REAL,
            return_1d REAL,
            return_1w REAL,
            return_1m REAL,
            return_3m REAL,
            return_1y REAL,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(fund_code, source_url)
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

    category = "stock"
    if "finance" in url:
        category = "finance"
    elif "global" in url:
        category = "global"

    for article in sel.css("div.news-list li, div.article-item, div.list-item, li.news-item"):
        title_el = article.css("a::text, h3::text, .title::text, h4::text")
        title = title_el[0].text.strip() if title_el else ""
        if not title:
            continue

        link_el = article.css("a::attr(href)")
        link = link_el[0].text.strip() if link_el else ""
        if link and not link.startswith("http"):
            link = "https://www.5ifin.com" + link

        summary_el = article.css("p::text, .summary::text, .desc::text, .abstract::text")
        summary = summary_el[0].text.strip() if summary_el else ""

        date_el = article.css("span.date::text, .time::text, time::text, span.time::text")
        publish_date = ""
        if date_el:
            publish_date = extract_date(date_el[0].text.strip())

        items.append({
            "title": title,
            "summary": summary,
            "category": category,
            "source": "iFinD",
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
        if not any(kw in header_text for kw in ["新闻", "标题", "资讯", "news", "title"]):
            continue
        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) >= 2 and cells[0]:
                items.append({
                    "title": cells[0],
                    "summary": cells[1] if len(cells) > 1 else "",
                    "category": category,
                    "source": "iFinD",
                    "publish_date": extract_date(cells[-1]) if len(cells) > 2 else "",
                    "source_url": url,
                    "scraped_at": now,
                })

    return items


def extract_stock_quotes(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []
    now = datetime.now(timezone.utc).isoformat()

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue
        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)
        if not any(kw in header_text for kw in ["代码", "名称", "最新价", "涨跌", "code", "name", "price"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            item = {
                "stock_code": cells[0],
                "stock_name": cells[1] if len(cells) > 1 else "",
                "price": parse_float(cells[2]) if len(cells) > 2 else None,
                "change_value": parse_float(cells[3]) if len(cells) > 3 else None,
                "change_pct": parse_float(cells[4]) if len(cells) > 4 else None,
                "volume": parse_float(cells[5]) if len(cells) > 5 else None,
                "turnover": parse_float(cells[6]) if len(cells) > 6 else None,
                "market_cap": parse_float(cells[7]) if len(cells) > 7 else None,
                "pe_ratio": parse_float(cells[8]) if len(cells) > 8 else None,
                "trade_date": "",
                "source_url": url,
                "scraped_at": now,
            }
            items.append(item)

    for row in sel.css("div.stock-item, div.quote-item, tr.stock-row"):
        code_el = row.css(".code::text, .stock-code::text")
        name_el = row.css(".name::text, .stock-name::text, h4::text")
        price_el = row.css(".price::text, .latest::text, .value::text")
        change_el = row.css(".change::text, .chg::text")
        pct_el = row.css(".change-pct::text, .pct::text")

        code = code_el[0].text.strip() if code_el else ""
        name = name_el[0].text.strip() if name_el else ""
        if not code and not name:
            continue

        items.append({
            "stock_code": code,
            "stock_name": name,
            "price": parse_float(price_el[0].text.strip()) if price_el else None,
            "change_value": parse_float(change_el[0].text.strip()) if change_el else None,
            "change_pct": parse_float(pct_el[0].text.strip()) if pct_el else None,
            "volume": None,
            "turnover": None,
            "market_cap": None,
            "pe_ratio": None,
            "trade_date": "",
            "source_url": url,
            "scraped_at": now,
        })

    return items


def extract_industry_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []
    now = datetime.now(timezone.utc).isoformat()

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue
        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)
        if not any(kw in header_text for kw in ["行业", "板块", "industry", "sector", "涨跌"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            item = {
                "industry_name": cells[0],
                "change_pct": parse_float(cells[1]) if len(cells) > 1 else None,
                "turnover": parse_float(cells[2]) if len(cells) > 2 else None,
                "leading_stock": cells[3] if len(cells) > 3 else "",
                "leading_change": parse_float(cells[4]) if len(cells) > 4 else None,
                "sentiment": cells[5] if len(cells) > 5 else "",
                "source_url": url,
                "scraped_at": now,
            }
            items.append(item)

    return items


def extract_fund_ranking(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []
    now = datetime.now(timezone.utc).isoformat()

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue
        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)
        if not any(kw in header_text for kw in ["基金", "净值", "fund", "NAV", "排名"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            item = {
                "fund_code": cells[0],
                "fund_name": cells[1] if len(cells) > 1 else "",
                "fund_type": cells[2] if len(cells) > 2 else "",
                "nav": parse_float(cells[3]) if len(cells) > 3 else None,
                "accumulated_nav": parse_float(cells[4]) if len(cells) > 4 else None,
                "return_1d": parse_float(cells[5]) if len(cells) > 5 else None,
                "return_1w": parse_float(cells[6]) if len(cells) > 6 else None,
                "return_1m": parse_float(cells[7]) if len(cells) > 7 else None,
                "return_3m": parse_float(cells[8]) if len(cells) > 8 else None,
                "return_1y": parse_float(cells[9]) if len(cells) > 9 else None,
                "source_url": url,
                "scraped_at": now,
            }
            items.append(item)

    return items


def extract_date(text: str) -> str:
    patterns = [
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{4}年\d{1,2}月\d{1,2}日)",
        r"(\d{4}/\d{2}/\d{2})",
        r"(\d{2}-\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def parse_float(text: str) -> float | None:
    if not text:
        return None
    cleaned = text.replace(",", "").replace("，", "").replace("%", "").replace("--", "").strip()
    if not cleaned or cleaned == "-":
        return None
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
                     item.get("source", "iFinD"), item.get("publish_date", ""),
                     item["source_url"], item["scraped_at"]),
                )
            elif table == "stock_quotes":
                conn.execute(
                    """INSERT OR REPLACE INTO stock_quotes
                    (stock_code, stock_name, price, change_value, change_pct, volume,
                     turnover, market_cap, pe_ratio, trade_date, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("stock_code", ""), item.get("stock_name", ""),
                     item.get("price"), item.get("change_value"), item.get("change_pct"),
                     item.get("volume"), item.get("turnover"), item.get("market_cap"),
                     item.get("pe_ratio"), item.get("trade_date", ""),
                     item["source_url"], item["scraped_at"]),
                )
            elif table == "industry_data":
                conn.execute(
                    """INSERT OR REPLACE INTO industry_data
                    (industry_name, change_pct, turnover, leading_stock, leading_change,
                     sentiment, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["industry_name"], item.get("change_pct"),
                     item.get("turnover"), item.get("leading_stock", ""),
                     item.get("leading_change"), item.get("sentiment", ""),
                     item["source_url"], item["scraped_at"]),
                )
            elif table == "fund_ranking":
                conn.execute(
                    """INSERT OR REPLACE INTO fund_ranking
                    (fund_code, fund_name, fund_type, nav, accumulated_nav,
                     return_1d, return_1w, return_1m, return_3m, return_1y,
                     source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("fund_code", ""), item.get("fund_name", ""),
                     item.get("fund_type", ""), item.get("nav"),
                     item.get("accumulated_nav"), item.get("return_1d"),
                     item.get("return_1w"), item.get("return_1m"),
                     item.get("return_3m"), item.get("return_1y"),
                     item["source_url"], item["scraped_at"]),
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


def get_ifind_data(categories: list[str] | None = None) -> dict[str, list[dict]]:
    """Fetch public data from Tonghuashun iFinD.

    Args:
        categories: List of categories to fetch.
            Available: news, market, industry, fund.
            Default: all.

    Returns:
        Dict with keys: news, stock_quotes, industry_data, fund_ranking.
    """
    if categories is None:
        categories = ["news", "market", "industry", "fund"]

    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()
    results = {
        "news": [],
        "stock_quotes": [],
        "industry_data": [],
        "fund_ranking": [],
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

    if "market" in categories:
        logger.info("=== Fetching Stock Quotes ===")
        for url in MARKET_URLS:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_stock_quotes(page["html"], url)
                results["stock_quotes"].extend(items)
                save_to_sqlite(conn, "stock_quotes", items)
                logger.info("  Extracted %d quotes from %s", len(items), url)
            time.sleep(3)

    if "industry" in categories:
        logger.info("=== Fetching Industry Data ===")
        for url in INDUSTRY_URLS:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_industry_data(page["html"], url)
                results["industry_data"].extend(items)
                save_to_sqlite(conn, "industry_data", items)
                logger.info("  Extracted %d industry items from %s", len(items), url)
            time.sleep(3)

    if "fund" in categories:
        logger.info("=== Fetching Fund Rankings ===")
        for url in FUND_URLS:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_fund_ranking(page["html"], url)
                results["fund_ranking"].extend(items)
                save_to_sqlite(conn, "fund_ranking", items)
                logger.info("  Extracted %d fund items from %s", len(items), url)
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
    results = get_ifind_data()
    print(f"\n{'=' * 70}")
    print(f"Tonghuashun iFinD Spider Results")
    print(f"{'=' * 70}")
    for key, items in results.items():
        print(f"  {key}: {len(items)} items")
    print(f"  SQLite: {DB_PATH}")
    print(f"  JSON:   {JSON_PATH}")
    print(f"{'=' * 70}")
