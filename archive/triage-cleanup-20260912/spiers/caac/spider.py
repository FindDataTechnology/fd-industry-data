#!/usr/bin/env python3
"""
China Civil Aviation Administration (CAAC) Spider
中国民用航空局数据爬虫

Target: http://www.caac.gov.cn/
Data: Aviation statistics, passenger traffic, cargo volume, route statistics
Focus: Monthly/annual aviation transport data, airport rankings, airline performance

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts aviation transport indicators and airport ranking data
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
logger = logging.getLogger("caac")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "caac.db"
JSON_PASSENGER_PATH = OUTPUT_DIR / "aviation_passenger.json"
JSON_CARGO_PATH = OUTPUT_DIR / "aviation_cargo.json"
JSON_STATS_PATH = OUTPUT_DIR / "aviation_statistics.json"
JSON_NEWS_PATH = OUTPUT_DIR / "aviation_news.json"

TARGET_URLS = {
    "statistics": [
        "http://www.caac.gov.cn/XXGK/XXGK/TJSJ/",
        "http://www.caac.gov.cn/XXGK/XXGK/TJGB/",
    ],
    "passenger": [
        "http://www.caac.gov.cn/XXGK/XXGK/TJSJ/kyzl/",
        "http://www.caac.gov.cn/XXGK/XXGK/TJSJ/mhyzl/",
    ],
    "cargo": [
        "http://www.caac.gov.cn/XXGK/XXGK/TJSJ/hyzl/",
    ],
    "news": [
        "http://www.caac.gov.cn/XWZX/MHYW/",
        "http://www.caac.gov.cn/XWZX/HYXW/",
    ],
}

AVIATION_INDICATORS = {
    "旅客运输量": "passenger_volume",
    "旅客吞吐量": "passenger_throughput",
    "货邮运输量": "cargo_mail_volume",
    "货邮吞吐量": "cargo_mail_throughput",
    "货物运输量": "cargo_volume",
    "航班正常率": "flight_on_time_rate",
    "运输总周转量": "total_transport_turnover",
    "旅客周转量": "passenger_turnover",
    "货邮周转量": "cargo_mail_turnover",
    "飞机起降架次": "aircraft_movements",
    "通航机场数": "general_aviation_airports",
    "运输机场数": "transport_airports",
    "航线条数": "route_count",
    "航线里程": "route_mileage",
}

MAJOR_AIRPORTS = {
    "首都机场": "beijing_capital",
    "北京首都": "beijing_capital",
    "大兴机场": "beijing_daxing",
    "北京大兴": "beijing_daxing",
    "浦东机场": "shanghai_pudong",
    "上海浦东": "shanghai_pudong",
    "虹桥机场": "shanghai_hongqiao",
    "上海虹桥": "shanghai_hongqiao",
    "白云机场": "guangzhou_baiyun",
    "广州白云": "guangzhou_baiyun",
    "宝安机场": "shenzhen_baoan",
    "深圳宝安": "shenzhen_baoan",
    "双流机场": "chengdu_shuangliu",
    "天府机场": "chengdu_tianfu",
    "萧山机场": "hangzhou_xiaoshan",
    "杭州萧山": "hangzhou_xiaoshan",
    "江北机场": "chongqing_jiangbei",
    "重庆江北": "chongqing_jiangbei",
    "咸阳机场": "xian_xianyang",
    "西安咸阳": "xian_xianyang",
}


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS aviation_passenger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            airport_name TEXT,
            airport_code TEXT,
            indicator TEXT,
            indicator_cn TEXT,
            value REAL,
            unit TEXT DEFAULT '万人次',
            growth_rate REAL,
            growth_unit TEXT DEFAULT '%',
            ranking INTEGER,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, airport_name, indicator, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS aviation_cargo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            airport_name TEXT,
            airport_code TEXT,
            indicator TEXT,
            indicator_cn TEXT,
            value REAL,
            unit TEXT DEFAULT '万吨',
            growth_rate REAL,
            growth_unit TEXT DEFAULT '%',
            ranking INTEGER,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, airport_name, indicator, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS aviation_statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            indicator TEXT,
            indicator_cn TEXT,
            value REAL,
            unit TEXT,
            growth_rate REAL,
            growth_unit TEXT DEFAULT '%',
            category TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, indicator, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS aviation_news (
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


def identify_airport(text: str) -> dict | None:
    for cn_name, code in MAJOR_AIRPORTS.items():
        if cn_name in text:
            return {"airport_name": cn_name, "airport_code": code}
    return None


def identify_indicator(text: str) -> dict | None:
    for cn_name, en_name in AVIATION_INDICATORS.items():
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


def extract_passenger_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["旅客", "客运", "吞吐", "万人次", "人次"]):
            continue

        for idx, row in enumerate(rows[1:], start=1):
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            cell_text = " ".join(cells)
            airport_info = identify_airport(cell_text)
            if not airport_info:
                airport_info = {"airport_name": cells[0], "airport_code": ""}

            ind_info = identify_indicator(cell_text)
            if not ind_info:
                ind_info = {"indicator": "passenger_throughput", "indicator_cn": "旅客吞吐量"}

            item = {
                "period": extract_period(cell_text),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
                "ranking": idx,
            }
            item.update(airport_info)
            item.update(ind_info)

            numbers = re.findall(r"[\d,]+\.?\d*", " ".join(cells).replace(",", ""))
            if numbers:
                try:
                    item["value"] = float(numbers[0])
                except ValueError:
                    continue

            if "万人次" in cell_text:
                item["unit"] = "万人次"
            elif "万人次" in header_text:
                item["unit"] = "万人次"
            else:
                item["unit"] = "万人次"

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate"] = float(pct[0])
                        item["growth_unit"] = "%"

            if item.get("value"):
                items.append(item)

    return items


def extract_cargo_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["货邮", "货运", "货物", "吨"]):
            continue

        for idx, row in enumerate(rows[1:], start=1):
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            cell_text = " ".join(cells)
            airport_info = identify_airport(cell_text)
            if not airport_info:
                airport_info = {"airport_name": cells[0], "airport_code": ""}

            ind_info = identify_indicator(cell_text)
            if not ind_info:
                ind_info = {"indicator": "cargo_mail_throughput", "indicator_cn": "货邮吞吐量"}

            item = {
                "period": extract_period(cell_text),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
                "ranking": idx,
            }
            item.update(airport_info)
            item.update(ind_info)

            numbers = re.findall(r"[\d,]+\.?\d*", " ".join(cells).replace(",", ""))
            if numbers:
                try:
                    item["value"] = float(numbers[0])
                except ValueError:
                    continue

            if "万吨" in cell_text:
                item["unit"] = "万吨"
            elif "吨" in cell_text:
                item["unit"] = "吨"
            else:
                item["unit"] = "万吨"

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate"] = float(pct[0])
                        item["growth_unit"] = "%"

            if item.get("value"):
                items.append(item)

    return items


def extract_aviation_stats(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["航空", "运输", "周转", "航班", "航线", "机场"]):
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

            if "亿吨公里" in cell_text:
                item["unit"] = "亿吨公里"
            elif "万公里" in cell_text:
                item["unit"] = "万公里"
            elif "亿人次" in cell_text:
                item["unit"] = "亿人次"
            elif "万" in cell_text:
                item["unit"] = "万"
            else:
                item["unit"] = ""

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate"] = float(pct[0])
                        item["growth_unit"] = "%"

            if "运输" in header_text:
                item["category"] = "transport"
            elif "机场" in header_text:
                item["category"] = "airport"
            elif "航线" in header_text:
                item["category"] = "route"
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
        if "统计" in title or "数据" in title:
            category = "statistics"
        elif "政策" in title:
            category = "policy"
        elif "安全" in title:
            category = "safety"
        elif "航线" in title:
            category = "route"

        items.append({
            "date": date,
            "title": title,
            "content": content[:500] if content else "",
            "category": category,
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def save_passenger_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO aviation_passenger
                (period, airport_name, airport_code, indicator, indicator_cn,
                 value, unit, growth_rate, growth_unit, ranking, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("airport_name"), item.get("airport_code"),
                    item.get("indicator"), item.get("indicator_cn"),
                    item.get("value"), item.get("unit"),
                    item.get("growth_rate"), item.get("growth_unit"),
                    item.get("ranking"), item.get("source_url"), item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_cargo_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO aviation_cargo
                (period, airport_name, airport_code, indicator, indicator_cn,
                 value, unit, growth_rate, growth_unit, ranking, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("airport_name"), item.get("airport_code"),
                    item.get("indicator"), item.get("indicator_cn"),
                    item.get("value"), item.get("unit"),
                    item.get("growth_rate"), item.get("growth_unit"),
                    item.get("ranking"), item.get("source_url"), item.get("scraped_at"),
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
                INSERT OR REPLACE INTO aviation_statistics
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
                INSERT OR REPLACE INTO aviation_news
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


def get_caac_data(
    include_passenger: bool = True,
    include_cargo: bool = True,
    include_stats: bool = True,
    include_news: bool = True,
) -> dict:
    """Fetch data from Civil Aviation Administration of China.

    Args:
        include_passenger: Whether to fetch passenger traffic data.
        include_cargo: Whether to fetch cargo/mail data.
        include_stats: Whether to fetch aviation statistics.
        include_news: Whether to fetch aviation news.

    Returns:
        Dict with keys: 'passenger', 'cargo', 'statistics', 'news'.
    """
    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "passenger": [],
        "cargo": [],
        "statistics": [],
        "news": [],
    }

    logger.info("Starting CAAC spider")

    if include_passenger:
        for url in TARGET_URLS["passenger"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_passenger_data(page["html"], url)
                if items:
                    result["passenger"].extend(items)
                    logger.info("  Extracted %d passenger records", len(items))
            time.sleep(2)

    if include_cargo:
        for url in TARGET_URLS["cargo"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_cargo_data(page["html"], url)
                if items:
                    result["cargo"].extend(items)
                    logger.info("  Extracted %d cargo records", len(items))
            time.sleep(2)

    if include_stats:
        for url in TARGET_URLS["statistics"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_aviation_stats(page["html"], url)
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

    if result["passenger"]:
        n = save_passenger_to_sqlite(result["passenger"], conn)
        save_to_json(result["passenger"], JSON_PASSENGER_PATH)
        logger.info("Saved %d passenger records: SQLite=%d, JSON=%s", len(result["passenger"]), n, JSON_PASSENGER_PATH)

    if result["cargo"]:
        n = save_cargo_to_sqlite(result["cargo"], conn)
        save_to_json(result["cargo"], JSON_CARGO_PATH)
        logger.info("Saved %d cargo records: SQLite=%d, JSON=%s", len(result["cargo"]), n, JSON_CARGO_PATH)

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
    results = get_caac_data()
    print(f"\n{'=' * 70}")
    print(f"Total passenger records: {len(results['passenger'])}")
    print(f"Total cargo records: {len(results['cargo'])}")
    print(f"Total statistics records: {len(results['statistics'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON (passenger): {JSON_PASSENGER_PATH}")
    print(f"JSON (cargo): {JSON_CARGO_PATH}")
    print(f"JSON (stats): {JSON_STATS_PATH}")
    print(f"JSON (news): {JSON_NEWS_PATH}")
    print(f"{'=' * 70}")

    if results["passenger"]:
        print(f"\nPassenger Traffic ({len(results['passenger'])} records):")
        for stat in results["passenger"][:10]:
            print(f"  {stat.get('airport_name', 'N/A'):>12s} | "
                  f"{stat.get('value', 'N/A'):>10} {stat.get('unit', '')}")

    if results["cargo"]:
        print(f"\nCargo/Mail Traffic ({len(results['cargo'])} records):")
        for stat in results["cargo"][:10]:
            print(f"  {stat.get('airport_name', 'N/A'):>12s} | "
                  f"{stat.get('value', 'N/A'):>10} {stat.get('unit', '')}")

    if results["statistics"]:
        print(f"\nAviation Statistics ({len(results['statistics'])} records):")
        for stat in results["statistics"][:10]:
            print(f"  {stat.get('indicator_cn', 'N/A'):>16s} | "
                  f"{stat.get('value', 'N/A'):>10} {stat.get('unit', '')}")

    if results["news"]:
        print(f"\nRecent News ({len(results['news'])} articles):")
        for article in results["news"][:5]:
            print(f"  [{article.get('date', 'N/A')}] {article.get('title', 'N/A')[:60]}")
