#!/usr/bin/env python3
"""
China Logistics and Purchasing Federation (CLPF) Spider
中国物流与采购联合会数据爬虫

Target: http://www.chinawuliu.com.cn/
Data: Logistics industry data, PMI indices, transportation statistics, supply chain data
Focus: PMI (Purchasing Managers' Index), logistics cost, freight volume

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts PMI index data and logistics cost indices
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
logger = logging.getLogger("logistics")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "logistics.db"
JSON_PMI_PATH = OUTPUT_DIR / "logistics_pmi.json"
JSON_STATS_PATH = OUTPUT_DIR / "logistics_statistics.json"
JSON_NEWS_PATH = OUTPUT_DIR / "logistics_news.json"

TARGET_URLS = {
    "pmi": [
        "http://www.chinawuliu.com.cn/zlxw/pmi/",
        "http://www.chinawuliu.com.cn/zlxw/index_pmi/",
    ],
    "statistics": [
        "http://www.chinawuliu.com.cn/zlxw/data/",
        "http://www.chinawuliu.com.cn/zlxw/cost/",
    ],
    "news": [
        "http://www.chinawuliu.com.cn/zlxw/industry/",
        "http://www.chinawuliu.com.cn/zlxw/policy/",
    ],
}

PMI_INDICATORS = {
    "PMI": "pmi",
    "采购经理指数": "pmi",
    "新订单": "new_orders",
    "生产": "production",
    "从业人员": "employment",
    "供应商配送时间": "supplier_delivery",
    "原材料库存": "raw_material_inventory",
    "产成品库存": "finished_inventory",
    "采购量": "purchase_quantity",
    "进口": "import",
    "购进价格": "purchase_price",
    "出厂价格": "factory_price",
}


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pmi_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            indicator TEXT NOT NULL,
            indicator_cn TEXT,
            value REAL,
            change REAL,
            threshold REAL DEFAULT 50.0,
            category TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, indicator, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS logistics_statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            indicator_name TEXT,
            value REAL,
            unit TEXT,
            growth_rate REAL,
            growth_unit TEXT DEFAULT '%',
            category TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, indicator_name, source_url)
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


def identify_pmi_indicator(text: str) -> dict | None:
    for cn_name, en_name in PMI_INDICATORS.items():
        if cn_name in text:
            return {"indicator": en_name, "indicator_cn": cn_name}
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


def extract_pmi_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["PMI", "指数", "采购", "荣枯"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            cell_text = " ".join(cells)
            ind_info = identify_pmi_indicator(cell_text)
            if not ind_info:
                ind_info = {"indicator": "composite_pmi", "indicator_cn": "综合PMI"}

            item = {
                "period": extract_period(cell_text),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(ind_info)

            numbers = re.findall(r"[\d,]+\.?\d*", " ".join(cells).replace(",", ""))
            if numbers:
                try:
                    item["value"] = float(numbers[0])
                except ValueError:
                    continue

            if len(numbers) >= 2:
                try:
                    item["change"] = float(numbers[1])
                except ValueError:
                    pass

            if item.get("value"):
                items.append(item)

    body_text = sel.css("body").first.text if sel.css("body") else ""
    pmi_patterns = [
        r"(?:制造业|非制造业|综合)?PMI[为达到]*\s*(\d+\.?\d*)\s*%",
        r"(?:制造业|非制造业|综合)?PMI[为达到]*\s*(\d+\.?\d*)",
    ]
    for pattern in pmi_patterns:
        matches = re.findall(pattern, body_text)
        for match in matches:
            if match and re.match(r"\d+\.?\d*", match):
                value = float(match)
                if 0 < value <= 100:
                    items.append({
                        "period": extract_period(body_text),
                        "indicator": "pmi",
                        "indicator_cn": "PMI",
                        "value": value,
                        "source_url": url,
                        "scraped_at": datetime.now(timezone.utc).isoformat(),
                    })

    return items


def extract_logistics_stats(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["物流", "货运", "运输", "成本", "费用", "总额"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            cell_text = " ".join(cells)

            item = {
                "period": extract_period(cell_text),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            numbers = re.findall(r"[\d,]+\.?\d*", " ".join(cells).replace(",", ""))
            if numbers:
                try:
                    item["value"] = float(numbers[0])
                    if "万亿" in cell_text:
                        item["value"] = item["value"] * 10000
                        item["unit"] = "亿元"
                    elif "亿" in cell_text:
                        item["unit"] = "亿元"
                    elif "亿吨" in cell_text:
                        item["unit"] = "亿吨"
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

            if "成本" in header_text or "费用" in cell_text:
                item["category"] = "cost"
            elif "货运" in header_text or "运输" in cell_text:
                item["category"] = "freight"
            else:
                item["category"] = "general"

            if item.get("value"):
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
        if "PMI" in title:
            category = "pmi"
        elif "政策" in title:
            category = "policy"
        elif "数据" in title:
            category = "data"
        elif "成本" in title:
            category = "cost"

        items.append({
            "date": date,
            "title": title,
            "content": content[:500] if content else "",
            "category": category,
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def save_pmi_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO pmi_data
                (period, indicator, indicator_cn, value, change, threshold, category, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("indicator"), item.get("indicator_cn"),
                    item.get("value"), item.get("change"), 50.0,
                    item.get("category"), item.get("source_url"), item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_stats_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO logistics_statistics
                (period, indicator_name, value, unit, growth_rate, growth_unit, category, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("indicator_name"),
                    item.get("value"), item.get("unit"),
                    item.get("growth_rate"), item.get("growth_unit"),
                    item.get("category"), item.get("source_url"), item.get("scraped_at"),
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


def get_logistics_data(
    include_pmi: bool = True,
    include_stats: bool = True,
    include_news: bool = True,
) -> dict:
    """Fetch data from China Logistics and Purchasing Federation.

    Args:
        include_pmi: Whether to fetch PMI index data.
        include_stats: Whether to fetch logistics statistics.
        include_news: Whether to fetch industry news.

    Returns:
        Dict with keys: 'pmi', 'statistics', 'news'.
    """
    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "pmi": [],
        "statistics": [],
        "news": [],
    }

    logger.info("Starting Logistics spider")

    if include_pmi:
        for url in TARGET_URLS["pmi"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_pmi_data(page["html"], url)
                if items:
                    result["pmi"].extend(items)
                    logger.info("  Extracted %d PMI records", len(items))
            time.sleep(2)

    if include_stats:
        for url in TARGET_URLS["statistics"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_logistics_stats(page["html"], url)
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

    if result["pmi"]:
        n = save_pmi_to_sqlite(result["pmi"], conn)
        save_to_json(result["pmi"], JSON_PMI_PATH)
        logger.info("Saved %d PMI records: SQLite=%d, JSON=%s", len(result["pmi"]), n, JSON_PMI_PATH)

    if result["statistics"]:
        n = save_stats_to_sqlite(result["statistics"], conn)
        save_to_json(result["statistics"], JSON_STATS_PATH)
        logger.info("Saved %d statistics: SQLite=%d, JSON=%s", len(result["statistics"]), n, JSON_STATS_PATH)

    if result["news"]:
        n = save_news_to_sqlite(result["news"], conn)
        save_to_json(result["news"], JSON_NEWS_PATH)
        logger.info("Saved %d news articles: SQLite=%d, JSON=%s", len(result["news"]), n, JSON_NEWS_PATH)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_logistics_data()
    print(f"\n{'=' * 70}")
    print(f"Total PMI records: {len(results['pmi'])}")
    print(f"Total statistics records: {len(results['statistics'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON (PMI): {JSON_PMI_PATH}")
    print(f"JSON (stats): {JSON_STATS_PATH}")
    print(f"JSON (news): {JSON_NEWS_PATH}")
    print(f"{'=' * 70}")

    if results["pmi"]:
        print(f"\nPMI Data ({len(results['pmi'])} records):")
        for stat in results["pmi"][:10]:
            print(f"  {stat.get('period', 'N/A'):>12s} | {stat.get('indicator_cn', 'N/A'):>12s} | "
                  f"{stat.get('value', 'N/A'):>8}")

    if results["statistics"]:
        print(f"\nLogistics Statistics ({len(results['statistics'])} records):")
        for stat in results["statistics"][:10]:
            print(f"  {stat.get('period', 'N/A'):>12s} | "
                  f"{stat.get('value', 'N/A'):>12} {stat.get('unit', '')}")

    if results["news"]:
        print(f"\nRecent News ({len(results['news'])} articles):")
        for article in results["news"][:5]:
            print(f"  [{article.get('date', 'N/A')}] {article.get('title', 'N/A')[:60]}")
