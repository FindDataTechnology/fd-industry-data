#!/usr/bin/env python3
"""
China Railway Corporation Spider
中国国家铁路集团数据爬虫

Target: http://www.china-railway.com.cn/
Data: Railway freight data, passenger volume, infrastructure statistics, investment data
Focus: National railway transport indicators, freight tonnage, high-speed rail data

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts railway transport and investment data
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
logger = logging.getLogger("china-railway")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "china_railway.db"
JSON_FREIGHT_PATH = OUTPUT_DIR / "railway_freight.json"
JSON_PASSENGER_PATH = OUTPUT_DIR / "railway_passenger.json"
JSON_INVESTMENT_PATH = OUTPUT_DIR / "railway_investment.json"
JSON_NEWS_PATH = OUTPUT_DIR / "railway_news.json"

TARGET_URLS = {
    "freight": [
        "http://www.china-railway.com.cn/xwzx/tyxw/",
        "http://www.china-railway.com.cn/sjzx/",
    ],
    "passenger": [
        "http://www.china-railway.com.cn/xwzx/tyxw/",
    ],
    "investment": [
        "http://www.china-railway.com.cn/sjzx/",
        "http://www.china-railway.com.cn/zfxxgk/",
    ],
    "news": [
        "http://www.china-railway.com.cn/xwzx/tyxw/",
        "http://www.china-railway.com.cn/xwzx/xwfb/",
    ],
}

RAILWAY_INDICATORS = {
    "旅客发送量": "passenger_dispatched",
    "旅客运输量": "passenger_volume",
    "货运发送量": "freight_dispatched",
    "货运总量": "total_freight",
    "货物发送量": "freight_dispatched",
    "货物周转量": "freight_turnover",
    "旅客周转量": "passenger_turnover",
    "运输总收入": "transport_revenue",
    "铁路投资": "railway_investment",
    "固定资产投资": "fixed_asset_investment",
    "基建投资": "infrastructure_investment",
    "装备投资": "equipment_investment",
    "营业里程": "operating_mileage",
    "高铁里程": "hsr_mileage",
    "电气化里程": "electrified_mileage",
    "复线里程": "double_track_mileage",
    "日均装车": "daily_avg_loading",
    "货车周转时间": "freight_car_turnaround",
}


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS railway_freight (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            indicator TEXT,
            indicator_cn TEXT,
            value REAL,
            unit TEXT DEFAULT '万吨',
            growth_rate REAL,
            growth_unit TEXT DEFAULT '%',
            category TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, indicator, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS railway_passenger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            indicator TEXT,
            indicator_cn TEXT,
            value REAL,
            unit TEXT DEFAULT '万人',
            growth_rate REAL,
            growth_unit TEXT DEFAULT '%',
            category TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, indicator, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS railway_investment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            indicator TEXT,
            indicator_cn TEXT,
            value REAL,
            unit TEXT DEFAULT '亿元',
            growth_rate REAL,
            growth_unit TEXT DEFAULT '%',
            category TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, indicator, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS railway_news (
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


def identify_indicator(text: str) -> dict | None:
    for cn_name, en_name in RAILWAY_INDICATORS.items():
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


def extract_freight_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["货运", "货物", "发送", "吨", "装车"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            cell_text = " ".join(cells)
            ind_info = identify_indicator(cell_text)
            if not ind_info:
                ind_info = {"indicator": "freight_dispatched", "indicator_cn": "货运发送量"}

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

            if "亿吨" in cell_text:
                item["unit"] = "亿吨"
            elif "万吨" in cell_text:
                item["unit"] = "万吨"
            elif "万吨公里" in cell_text:
                item["unit"] = "万吨公里"
            else:
                item["unit"] = "万吨"

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate"] = float(pct[0])
                        item["growth_unit"] = "%"

            if "集装箱" in cell_text:
                item["category"] = "container"
            elif "煤炭" in cell_text:
                item["category"] = "coal"
            else:
                item["category"] = "general"

            if item.get("value"):
                items.append(item)

    body_text = sel.css("body").first.text if sel.css("body") else ""
    freight_patterns = [
        r"(?:货运|货物)(?:发送|运输|完成)[^\d]*(\d+\.?\d*)\s*(?:万吨|亿吨)",
        r"发送[^\d]*(\d+\.?\d*)\s*(?:万吨|亿吨)",
    ]
    for pattern in freight_patterns:
        matches = re.findall(pattern, body_text)
        for match in matches:
            if match:
                value = float(match)
                if value > 0:
                    items.append({
                        "period": extract_period(body_text),
                        "indicator": "freight_dispatched",
                        "indicator_cn": "货运发送量",
                        "value": value,
                        "unit": "万吨",
                        "source_url": url,
                        "scraped_at": datetime.now(timezone.utc).isoformat(),
                        "category": "general",
                    })

    return items


def extract_passenger_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["旅客", "客运", "发送", "人次", "万人"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            cell_text = " ".join(cells)
            ind_info = identify_indicator(cell_text)
            if not ind_info:
                ind_info = {"indicator": "passenger_dispatched", "indicator_cn": "旅客发送量"}

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

            if "亿人" in cell_text:
                item["unit"] = "亿人"
            elif "万人" in cell_text:
                item["unit"] = "万人"
            elif "万人公里" in cell_text:
                item["unit"] = "万人公里"
            else:
                item["unit"] = "万人"

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate"] = float(pct[0])
                        item["growth_unit"] = "%"

            if "春运" in cell_text or "暑运" in cell_text:
                item["category"] = "seasonal"
            elif "高铁" in cell_text:
                item["category"] = "hsr"
            else:
                item["category"] = "general"

            if item.get("value"):
                items.append(item)

    return items


def extract_investment_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["投资", "建设", "里程", "公里", "亿"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            cell_text = " ".join(cells)
            ind_info = identify_indicator(cell_text)
            if not ind_info:
                continue

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

            if "亿元" in cell_text:
                item["unit"] = "亿元"
            elif "万公里" in cell_text:
                item["unit"] = "万公里"
            elif "公里" in cell_text:
                item["unit"] = "公里"
            else:
                item["unit"] = "亿元"

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate"] = float(pct[0])
                        item["growth_unit"] = "%"

            if "基建" in cell_text:
                item["category"] = "infrastructure"
            elif "装备" in cell_text:
                item["category"] = "equipment"
            elif "高铁" in cell_text:
                item["category"] = "hsr"
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
        if "货运" in title:
            category = "freight"
        elif "客运" in title or "旅客" in title:
            category = "passenger"
        elif "投资" in title or "建设" in title:
            category = "investment"
        elif "高铁" in title:
            category = "hsr"
        elif "政策" in title:
            category = "policy"

        items.append({
            "date": date,
            "title": title,
            "content": content[:500] if content else "",
            "category": category,
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def save_freight_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO railway_freight
                (period, indicator, indicator_cn, value, unit,
                 growth_rate, growth_unit, category, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("indicator"), item.get("indicator_cn"),
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


def save_passenger_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO railway_passenger
                (period, indicator, indicator_cn, value, unit,
                 growth_rate, growth_unit, category, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("indicator"), item.get("indicator_cn"),
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


def save_investment_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO railway_investment
                (period, indicator, indicator_cn, value, unit,
                 growth_rate, growth_unit, category, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("indicator"), item.get("indicator_cn"),
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
                INSERT OR REPLACE INTO railway_news
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


def get_china_railway_data(
    include_freight: bool = True,
    include_passenger: bool = True,
    include_investment: bool = True,
    include_news: bool = True,
) -> dict:
    """Fetch data from China Railway Corporation.

    Args:
        include_freight: Whether to fetch railway freight data.
        include_passenger: Whether to fetch passenger volume data.
        include_investment: Whether to fetch investment/infrastructure data.
        include_news: Whether to fetch railway news.

    Returns:
        Dict with keys: 'freight', 'passenger', 'investment', 'news'.
    """
    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "freight": [],
        "passenger": [],
        "investment": [],
        "news": [],
    }

    logger.info("Starting China Railway spider")

    if include_freight:
        for url in TARGET_URLS["freight"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_freight_data(page["html"], url)
                if items:
                    result["freight"].extend(items)
                    logger.info("  Extracted %d freight records", len(items))
            time.sleep(2)

    if include_passenger:
        for url in TARGET_URLS["passenger"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_passenger_data(page["html"], url)
                if items:
                    result["passenger"].extend(items)
                    logger.info("  Extracted %d passenger records", len(items))
            time.sleep(2)

    if include_investment:
        for url in TARGET_URLS["investment"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_investment_data(page["html"], url)
                if items:
                    result["investment"].extend(items)
                    logger.info("  Extracted %d investment records", len(items))
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

    if result["freight"]:
        n = save_freight_to_sqlite(result["freight"], conn)
        save_to_json(result["freight"], JSON_FREIGHT_PATH)
        logger.info("Saved %d freight records: SQLite=%d, JSON=%s", len(result["freight"]), n, JSON_FREIGHT_PATH)

    if result["passenger"]:
        n = save_passenger_to_sqlite(result["passenger"], conn)
        save_to_json(result["passenger"], JSON_PASSENGER_PATH)
        logger.info("Saved %d passenger records: SQLite=%d, JSON=%s", len(result["passenger"]), n, JSON_PASSENGER_PATH)

    if result["investment"]:
        n = save_investment_to_sqlite(result["investment"], conn)
        save_to_json(result["investment"], JSON_INVESTMENT_PATH)
        logger.info("Saved %d investment records: SQLite=%d, JSON=%s", len(result["investment"]), n, JSON_INVESTMENT_PATH)

    if result["news"]:
        n = save_news_to_sqlite(result["news"], conn)
        save_to_json(result["news"], JSON_NEWS_PATH)
        logger.info("Saved %d news articles: SQLite=%d, JSON=%s", len(result["news"]), n, JSON_NEWS_PATH)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_china_railway_data()
    print(f"\n{'=' * 70}")
    print(f"Total freight records: {len(results['freight'])}")
    print(f"Total passenger records: {len(results['passenger'])}")
    print(f"Total investment records: {len(results['investment'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON (freight): {JSON_FREIGHT_PATH}")
    print(f"JSON (passenger): {JSON_PASSENGER_PATH}")
    print(f"JSON (investment): {JSON_INVESTMENT_PATH}")
    print(f"JSON (news): {JSON_NEWS_PATH}")
    print(f"{'=' * 70}")

    if results["freight"]:
        print(f"\nRailway Freight ({len(results['freight'])} records):")
        for stat in results["freight"][:10]:
            print(f"  {stat.get('indicator_cn', 'N/A'):>12s} | "
                  f"{stat.get('value', 'N/A'):>10} {stat.get('unit', '')}")

    if results["passenger"]:
        print(f"\nRailway Passenger ({len(results['passenger'])} records):")
        for stat in results["passenger"][:10]:
            print(f"  {stat.get('indicator_cn', 'N/A'):>12s} | "
                  f"{stat.get('value', 'N/A'):>10} {stat.get('unit', '')}")

    if results["investment"]:
        print(f"\nRailway Investment ({len(results['investment'])} records):")
        for stat in results["investment"][:10]:
            print(f"  {stat.get('indicator_cn', 'N/A'):>12s} | "
                  f"{stat.get('value', 'N/A'):>10} {stat.get('unit', '')}")

    if results["news"]:
        print(f"\nRecent News ({len(results['news'])} articles):")
        for article in results["news"][:5]:
            print(f"  [{article.get('date', 'N/A')}] {article.get('title', 'N/A')[:60]}")
