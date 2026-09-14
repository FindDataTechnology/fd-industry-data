#!/usr/bin/env python3
"""
Ministry of Transport - Highway Administration Spider
交通运输部公路管理数据爬虫

Target: http://www.mot.gov.cn/
Data: Highway traffic data, vehicle flow statistics, infrastructure data, investment statistics
Focus: National highway network, toll station data, freight volume by road

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts highway infrastructure and traffic flow data
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
logger = logging.getLogger("mot-highway")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "mot_highway.db"
JSON_TRAFFIC_PATH = OUTPUT_DIR / "highway_traffic.json"
JSON_INFRA_PATH = OUTPUT_DIR / "highway_infrastructure.json"
JSON_INVESTMENT_PATH = OUTPUT_DIR / "highway_investment.json"
JSON_NEWS_PATH = OUTPUT_DIR / "highway_news.json"

TARGET_URLS = {
    "traffic": [
        "http://www.mot.gov.cn/shujufenxi/",
        "http://www.mot.gov.cn/tongjigongbao/",
        "http://www.mot.gov.cn/roadmap/",
    ],
    "infrastructure": [
        "http://www.mot.gov.cn/shujufenxi/",
        "http://www.mot.gov.cn/gonglujianshe/",
    ],
    "investment": [
        "http://www.mot.gov.cn/touzi/",
        "http://www.mot.gov.cn/shujufenxi/",
    ],
    "news": [
        "http://www.mot.gov.cn/xinwen/",
        "http://www.mot.gov.cn/zhengce/",
    ],
}

HIGHWAY_INDICATORS = {
    "公路客运量": "highway_passenger_volume",
    "公路货运量": "highway_freight_volume",
    "公路旅客周转量": "highway_passenger_turnover",
    "公路货物周转量": "highway_freight_turnover",
    "高速公路里程": "expressway_mileage",
    "公路里程": "highway_mileage",
    "国道里程": "national_road_mileage",
    "省道里程": "provincial_road_mileage",
    "农村公路里程": "rural_road_mileage",
    "桥梁数量": "bridge_count",
    "隧道数量": "tunnel_count",
    "收费站数量": "toll_station_count",
    "日均交通量": "daily_avg_traffic",
    "断面交通量": "section_traffic",
    "交通固定资产投资": "transport_fixed_investment",
    "公路建设投资": "highway_construction_investment",
    "公路养护投资": "highway_maintenance_investment",
    "收费公路里程": "toll_highway_mileage",
    "高速公路通车里程": "expressway_open_mileage",
    "机动车保有量": "motor_vehicle_ownership",
    "营运车辆数": "commercial_vehicle_count",
}


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS highway_traffic (
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
        CREATE TABLE IF NOT EXISTS highway_infrastructure (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            indicator TEXT,
            indicator_cn TEXT,
            value REAL,
            unit TEXT,
            growth_rate REAL,
            growth_unit TEXT DEFAULT '%',
            category TEXT,
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, indicator, region, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS highway_investment (
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
        CREATE TABLE IF NOT EXISTS highway_news (
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
    for cn_name, en_name in HIGHWAY_INDICATORS.items():
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


def extract_traffic_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["客运", "货运", "运输", "周转", "交通量", "车辆"]):
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

            if "亿人" in cell_text:
                item["unit"] = "亿人"
            elif "万人" in cell_text:
                item["unit"] = "万人"
            elif "亿吨" in cell_text:
                item["unit"] = "亿吨"
            elif "万吨" in cell_text:
                item["unit"] = "万吨"
            elif "亿人公里" in cell_text:
                item["unit"] = "亿人公里"
            elif "万吨公里" in cell_text:
                item["unit"] = "万吨公里"
            elif "万辆次" in cell_text:
                item["unit"] = "万辆次"
            else:
                item["unit"] = ""

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate"] = float(pct[0])
                        item["growth_unit"] = "%"

            if "客运" in cell_text:
                item["category"] = "passenger"
            elif "货运" in cell_text:
                item["category"] = "freight"
            else:
                item["category"] = "general"

            if item.get("value"):
                items.append(item)

    return items


def extract_infrastructure_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["里程", "公路", "高速", "桥梁", "隧道", "公里"]):
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
                "region": "",
            }
            item.update(ind_info)

            numbers = re.findall(r"[\d,]+\.?\d*", " ".join(cells).replace(",", ""))
            if numbers:
                try:
                    item["value"] = float(numbers[0])
                except ValueError:
                    continue

            if "万公里" in cell_text:
                item["unit"] = "万公里"
            elif "公里" in cell_text:
                item["unit"] = "公里"
            elif "座" in cell_text:
                item["unit"] = "座"
            elif "个" in cell_text:
                item["unit"] = "个"
            else:
                item["unit"] = "万公里"

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate"] = float(pct[0])
                        item["growth_unit"] = "%"

            if "高速" in cell_text:
                item["category"] = "expressway"
            elif "国道" in cell_text:
                item["category"] = "national"
            elif "省道" in cell_text:
                item["category"] = "provincial"
            elif "农村" in cell_text:
                item["category"] = "rural"
            else:
                item["category"] = "general"

            for cell in cells:
                for province in ["北京", "上海", "广东", "江苏", "浙江", "山东", "四川", "湖北", "河南", "河北"]:
                    if province in cell:
                        item["region"] = province

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

        if not any(kw in header_text for kw in ["投资", "资金", "建设", "亿"]):
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
            elif "万元" in cell_text:
                item["unit"] = "万元"
            else:
                item["unit"] = "亿元"

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate"] = float(pct[0])
                        item["growth_unit"] = "%"

            if "建设" in cell_text:
                item["category"] = "construction"
            elif "养护" in cell_text:
                item["category"] = "maintenance"
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
        if "公路" in title:
            category = "highway"
        elif "投资" in title:
            category = "investment"
        elif "政策" in title:
            category = "policy"
        elif "建设" in title:
            category = "construction"
        elif "收费" in title:
            category = "toll"

        items.append({
            "date": date,
            "title": title,
            "content": content[:500] if content else "",
            "category": category,
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def save_traffic_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO highway_traffic
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


def save_infra_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO highway_infrastructure
                (period, indicator, indicator_cn, value, unit,
                 growth_rate, growth_unit, category, region, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("indicator"), item.get("indicator_cn"),
                    item.get("value"), item.get("unit"),
                    item.get("growth_rate"), item.get("growth_unit"),
                    item.get("category"), item.get("region"),
                    item.get("source_url"), item.get("scraped_at"),
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
                INSERT OR REPLACE INTO highway_investment
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
                INSERT OR REPLACE INTO highway_news
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


def get_mot_highway_data(
    include_traffic: bool = True,
    include_infrastructure: bool = True,
    include_investment: bool = True,
    include_news: bool = True,
) -> dict:
    """Fetch data from Ministry of Transport (Highway Administration).

    Args:
        include_traffic: Whether to fetch highway traffic data.
        include_infrastructure: Whether to fetch infrastructure data.
        include_investment: Whether to fetch investment data.
        include_news: Whether to fetch highway news.

    Returns:
        Dict with keys: 'traffic', 'infrastructure', 'investment', 'news'.
    """
    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "traffic": [],
        "infrastructure": [],
        "investment": [],
        "news": [],
    }

    logger.info("Starting MOT Highway spider")

    if include_traffic:
        for url in TARGET_URLS["traffic"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_traffic_data(page["html"], url)
                if items:
                    result["traffic"].extend(items)
                    logger.info("  Extracted %d traffic records", len(items))
            time.sleep(2)

    if include_infrastructure:
        for url in TARGET_URLS["infrastructure"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_infrastructure_data(page["html"], url)
                if items:
                    result["infrastructure"].extend(items)
                    logger.info("  Extracted %d infrastructure records", len(items))
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

    if result["traffic"]:
        n = save_traffic_to_sqlite(result["traffic"], conn)
        save_to_json(result["traffic"], JSON_TRAFFIC_PATH)
        logger.info("Saved %d traffic records: SQLite=%d, JSON=%s", len(result["traffic"]), n, JSON_TRAFFIC_PATH)

    if result["infrastructure"]:
        n = save_infra_to_sqlite(result["infrastructure"], conn)
        save_to_json(result["infrastructure"], JSON_INFRA_PATH)
        logger.info("Saved %d infrastructure records: SQLite=%d, JSON=%s", len(result["infrastructure"]), n, JSON_INFRA_PATH)

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
    results = get_mot_highway_data()
    print(f"\n{'=' * 70}")
    print(f"Total traffic records: {len(results['traffic'])}")
    print(f"Total infrastructure records: {len(results['infrastructure'])}")
    print(f"Total investment records: {len(results['investment'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON (traffic): {JSON_TRAFFIC_PATH}")
    print(f"JSON (infrastructure): {JSON_INFRA_PATH}")
    print(f"JSON (investment): {JSON_INVESTMENT_PATH}")
    print(f"JSON (news): {JSON_NEWS_PATH}")
    print(f"{'=' * 70}")

    if results["traffic"]:
        print(f"\nHighway Traffic ({len(results['traffic'])} records):")
        for stat in results["traffic"][:10]:
            print(f"  {stat.get('indicator_cn', 'N/A'):>16s} | "
                  f"{stat.get('value', 'N/A'):>10} {stat.get('unit', '')}")

    if results["infrastructure"]:
        print(f"\nInfrastructure ({len(results['infrastructure'])} records):")
        for stat in results["infrastructure"][:10]:
            print(f"  {stat.get('indicator_cn', 'N/A'):>16s} | "
                  f"{stat.get('value', 'N/A'):>10} {stat.get('unit', '')}")

    if results["investment"]:
        print(f"\nInvestment ({len(results['investment'])} records):")
        for stat in results["investment"][:10]:
            print(f"  {stat.get('indicator_cn', 'N/A'):>16s} | "
                  f"{stat.get('value', 'N/A'):>10} {stat.get('unit', '')}")

    if results["news"]:
        print(f"\nRecent News ({len(results['news'])} articles):")
        for article in results["news"][:5]:
            print(f"  [{article.get('date', 'N/A')}] {article.get('title', 'N/A')[:60]}")
