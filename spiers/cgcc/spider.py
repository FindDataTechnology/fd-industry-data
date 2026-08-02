#!/usr/bin/env python3
"""
China National Commercial Association Spider
中国商业联合会数据爬虫

Target: http://www.cgcc.org.cn/
Data: Commercial industry data, trade statistics, market analysis, industry reports
Focus: Retail sales, wholesale trade, commercial index, consumer goods market

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts commercial index and trade statistics
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
logger = logging.getLogger("cgcc")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "cgcc.db"
JSON_TRADE_STATS_PATH = OUTPUT_DIR / "cgcc_trade_statistics.json"
JSON_COMMERCIAL_INDEX_PATH = OUTPUT_DIR / "cgcc_commercial_index.json"
JSON_MARKET_ANALYSIS_PATH = OUTPUT_DIR / "cgcc_market_analysis.json"
JSON_NEWS_PATH = OUTPUT_DIR / "cgcc_news.json"

TARGET_URLS = {
    "trade_stats": [
        "http://www.cgcc.org.cn/Article/List/73.html",
        "http://www.cgcc.org.cn/Article/List/74.html",
    ],
    "commercial_index": [
        "http://www.cgcc.org.cn/Article/List/75.html",
        "http://www.cgcc.org.cn/Article/List/76.html",
    ],
    "market_analysis": [
        "http://www.cgcc.org.cn/Article/List/77.html",
        "http://www.cgcc.org.cn/Article/List/78.html",
    ],
    "news": [
        "http://www.cgcc.org.cn/Article/List/1.html",
        "http://www.cgcc.org.cn/Article/List/2.html",
    ],
}

COMMERCIAL_CATEGORIES = {
    "批发": "wholesale",
    "零售": "retail",
    "住宿": "accommodation",
    "餐饮": "catering",
    "物流": "logistics",
    "供应链": "supply_chain",
    "消费品": "consumer_goods",
    "生产资料": "producer_goods",
    "进出口": "import_export",
}


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            category TEXT,
            category_cn TEXT,
            indicator_name TEXT,
            value REAL,
            unit TEXT,
            growth_rate REAL,
            growth_unit TEXT DEFAULT '%',
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, category, indicator_name, region, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS commercial_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            index_name TEXT,
            index_value REAL,
            change_value REAL,
            sub_index TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, index_name, sub_index, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            title TEXT,
            summary TEXT,
            category TEXT,
            key_findings TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS industry_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            title TEXT,
            content TEXT,
            category TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def fetch_page(url: str, fetcher: Fetcher) -> dict | None:
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


def identify_commercial_category(text: str) -> dict | None:
    for cn_name, en_name in COMMERCIAL_CATEGORIES.items():
        if cn_name in text:
            return {"category": en_name, "category_cn": cn_name}
    return None


def extract_period(text: str) -> str:
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


def extract_trade_statistics(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["销售", "贸易", "批发", "零售", "营收", "增长", "亿元", "万亿"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            cell_text = " ".join(cells)
            cat_info = identify_commercial_category(cell_text)
            if not cat_info:
                cat_info = {"category": "general", "category_cn": "综合商业"}

            item = {
                "period": extract_period(cell_text),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(cat_info)

            numbers = re.findall(r"[\d,]+\.?\d*", " ".join(cells).replace(",", ""))
            if numbers:
                try:
                    item["value"] = float(numbers[0])
                    if "万亿" in cell_text:
                        item["value"] = item["value"] * 10000
                        item["unit"] = "亿元"
                    elif "亿" in cell_text:
                        item["unit"] = "亿元"
                    elif "万" in cell_text:
                        item["unit"] = "万元"
                    else:
                        item["unit"] = "亿元"
                except ValueError:
                    continue

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate"] = float(pct[0])
                        item["growth_unit"] = "%"

            if "销售" in header_text or "零售" in cell_text:
                item["indicator_name"] = "retail_sales"
            elif "批发" in header_text:
                item["indicator_name"] = "wholesale_trade"
            elif "利润" in header_text:
                item["indicator_name"] = "profit"
            else:
                item["indicator_name"] = "general"

            if item.get("value"):
                items.append(item)

    return items


def extract_commercial_index(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["指数", "景气", "信心", "商业", "景气度"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            cell_text = " ".join(cells)
            item = {
                "period": extract_period(cell_text),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            for i, cell in enumerate(cells):
                if i == 0 and not re.search(r"\d", cell):
                    item["index_name"] = cell
                    cat_info = identify_commercial_category(cell)
                    if cat_info:
                        item["sub_index"] = cat_info["category_cn"]
                else:
                    nums = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if nums:
                        try:
                            val = float(nums[0])
                            if "指数" in header_text or val > 50:
                                if "index_value" not in item:
                                    item["index_value"] = val
                                elif "change" not in item:
                                    item["change_value"] = val
                        except ValueError:
                            pass

            if item.get("index_name") or item.get("index_value"):
                items.append(item)

    return items


def extract_market_analysis(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for article in sel.css("article, .news-item, .article-item, .list-item, li"):
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
        date = extract_period(date)

        category = "general"
        if "零售" in title:
            category = "retail"
        elif "批发" in title:
            category = "wholesale"
        elif "消费" in title:
            category = "consumer"
        elif "市场" in title:
            category = "market"

        item = {
            "date": date,
            "title": title,
            "summary": content[:500] if content else "",
            "category": category,
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }

        key_patterns = [
            r"增长[\s:：]*(\d+\.?\d*%)",
            r"同比[\s:：]*(增长|下降)?[\s:：]*(\d+\.?\d*%)",
            r"达到[\s:：]*([\d,.]+[亿万]?)",
        ]
        findings = []
        for pattern in key_patterns:
            matches = re.findall(pattern, title + " " + content[:200])
            findings.extend(matches[:3])
        if findings:
            item["key_findings"] = "; ".join(str(f) for f in findings[:3])

        items.append(item)

    return items


def extract_news(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for article in sel.css("article, .news-item, .article-item, .list-item, li"):
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
        date = extract_period(date)

        category = "news"
        if "政策" in title:
            category = "policy"
        elif "报告" in title or "分析" in title:
            category = "analysis"
        elif "数据" in title:
            category = "data"

        items.append({
            "date": date,
            "title": title,
            "content": content[:500] if content else "",
            "category": category,
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def save_trade_stats_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO trade_statistics
                (period, category, category_cn, indicator_name, value, unit,
                 growth_rate, growth_unit, region, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("category"), item.get("category_cn"),
                    item.get("indicator_name"), item.get("value"), item.get("unit"),
                    item.get("growth_rate"), item.get("growth_unit"),
                    item.get("region"), item.get("source_url"), item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_index_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO commercial_index
                (period, index_name, index_value, change_value, sub_index, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("index_name"),
                    item.get("index_value"), item.get("change_value"),
                    item.get("sub_index"), item.get("source_url"), item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_analysis_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO market_analysis
                (date, title, summary, category, key_findings, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"), item.get("title"), item.get("summary"),
                    item.get("category"), item.get("key_findings"),
                    item.get("source_url"), item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_news_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO industry_news
                (date, title, content, category, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"), item.get("title"), item.get("content"),
                    item.get("category"), item.get("source_url"), item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_to_json(items: list[dict], json_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def get_cgcc_data(
    include_trade: bool = True,
    include_index: bool = True,
    include_analysis: bool = True,
    include_news: bool = True,
) -> dict:
    """Fetch data from China National Commercial Association.

    Args:
        include_trade: Whether to fetch trade statistics.
        include_index: Whether to fetch commercial index data.
        include_analysis: Whether to fetch market analysis reports.
        include_news: Whether to fetch industry news.

    Returns:
        Dict with keys: 'trade', 'index', 'analysis', 'news'.
    """
    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "trade": [],
        "index": [],
        "analysis": [],
        "news": [],
    }

    logger.info("Starting CGCC spider")

    if include_trade:
        for url in TARGET_URLS["trade_stats"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_trade_statistics(page["html"], url)
                if items:
                    result["trade"].extend(items)
                    logger.info("  Extracted %d trade records", len(items))
            time.sleep(2)

    if include_index:
        for url in TARGET_URLS["commercial_index"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_commercial_index(page["html"], url)
                if items:
                    result["index"].extend(items)
                    logger.info("  Extracted %d index records", len(items))
            time.sleep(2)

    if include_analysis:
        for url in TARGET_URLS["market_analysis"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_market_analysis(page["html"], url)
                if items:
                    result["analysis"].extend(items)
                    logger.info("  Extracted %d analysis reports", len(items))
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

    if result["trade"]:
        n = save_trade_stats_to_sqlite(result["trade"], conn)
        save_to_json(result["trade"], JSON_TRADE_STATS_PATH)
        logger.info("Saved %d trade records: SQLite=%d, JSON=%s", len(result["trade"]), n, JSON_TRADE_STATS_PATH)

    if result["index"]:
        n = save_index_to_sqlite(result["index"], conn)
        save_to_json(result["index"], JSON_COMMERCIAL_INDEX_PATH)
        logger.info("Saved %d index records: SQLite=%d, JSON=%s", len(result["index"]), n, JSON_COMMERCIAL_INDEX_PATH)

    if result["analysis"]:
        n = save_analysis_to_sqlite(result["analysis"], conn)
        save_to_json(result["analysis"], JSON_MARKET_ANALYSIS_PATH)
        logger.info("Saved %d analysis reports: SQLite=%d, JSON=%s", len(result["analysis"]), n, JSON_MARKET_ANALYSIS_PATH)

    if result["news"]:
        n = save_news_to_sqlite(result["news"], conn)
        save_to_json(result["news"], JSON_NEWS_PATH)
        logger.info("Saved %d news articles: SQLite=%d, JSON=%s", len(result["news"]), n, JSON_NEWS_PATH)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_cgcc_data()
    print(f"\n{'=' * 70}")
    print(f"Total trade records: {len(results['trade'])}")
    print(f"Total index records: {len(results['index'])}")
    print(f"Total analysis reports: {len(results['analysis'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON (trade): {JSON_TRADE_STATS_PATH}")
    print(f"JSON (index): {JSON_COMMERCIAL_INDEX_PATH}")
    print(f"JSON (analysis): {JSON_MARKET_ANALYSIS_PATH}")
    print(f"JSON (news): {JSON_NEWS_PATH}")
    print(f"{'=' * 70}")

    if results["trade"]:
        print(f"\nTrade Statistics ({len(results['trade'])} records):")
        for stat in results["trade"][:10]:
            print(f"  {stat.get('period', 'N/A'):>12s} | {stat.get('category_cn', 'N/A'):>8s} | "
                  f"{stat.get('indicator_name', 'N/A'):>15s} | {stat.get('value', 'N/A'):>12} {stat.get('unit', '')}")

    if results["index"]:
        print(f"\nCommercial Index ({len(results['index'])} records):")
        for idx in results["index"][:10]:
            print(f"  {idx.get('period', 'N/A'):>12s} | {idx.get('index_name', 'N/A'):>20s} | "
                  f"Value: {idx.get('index_value', 'N/A')}")
