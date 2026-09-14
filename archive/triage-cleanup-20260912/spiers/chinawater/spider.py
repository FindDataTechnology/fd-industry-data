#!/usr/bin/env python3
"""
China Water Resources Association Spider - 中国水利学会数据爬虫

Target: http://www.chinawater.com.cn/
Data: Water resources data, irrigation statistics, water quality, infrastructure

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts water resource statistics and infrastructure data
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
logger = logging.getLogger("chinawater")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "chinawater.db"
JSON_RESOURCES_PATH = OUTPUT_DIR / "chinawater_resources.json"
JSON_IRRIGATION_PATH = OUTPUT_DIR / "chinawater_irrigation.json"
JSON_QUALITY_PATH = OUTPUT_DIR / "chinawater_quality.json"
JSON_INFRA_PATH = OUTPUT_DIR / "chinawater_infrastructure.json"
JSON_NEWS_PATH = OUTPUT_DIR / "chinawater_news.json"

CATEGORIES = {
    "resources": {
        "cn_name": "水资源数据",
        "urls": [
            "http://www.chinawater.com.cn/data/resources.html",
            "http://www.chinawater.com.cn/data/water_supply.html",
            "http://www.chinawater.com.cn/data/annual_report.html",
        ],
    },
    "irrigation": {
        "cn_name": "灌溉统计",
        "urls": [
            "http://www.chinawater.com.cn/data/irrigation.html",
            "http://www.chinawater.com.cn/data/farmland.html",
        ],
    },
    "quality": {
        "cn_name": "水质数据",
        "urls": [
            "http://www.chinawater.com.cn/data/quality.html",
            "http://www.chinawater.com.cn/data/monitoring.html",
        ],
    },
    "infrastructure": {
        "cn_name": "水利基础设施",
        "urls": [
            "http://www.chinawater.com.cn/data/infrastructure.html",
            "http://www.chinawater.com.cn/data/reservoir.html",
            "http://www.chinawater.com.cn/data/dam.html",
        ],
    },
    "news": {
        "cn_name": "水利新闻",
        "urls": [
            "http://www.chinawater.com.cn/news/industry.html",
            "http://www.chinawater.com.cn/news/policy.html",
        ],
    },
}


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS water_resources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            indicator TEXT NOT NULL,
            value REAL,
            unit TEXT,
            region TEXT,
            yoy_change REAL,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, indicator, region, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS irrigation_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            indicator TEXT NOT NULL,
            value REAL,
            unit TEXT,
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, indicator, region, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS water_quality (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            water_body TEXT NOT NULL,
            quality_class TEXT,
            ph REAL,
            dissolved_oxygen REAL,
            cod REAL,
            ammonia_nitrogen REAL,
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, water_body, region, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS infrastructure (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            facility_type TEXT NOT NULL,
            facility_name TEXT,
            capacity REAL,
            capacity_unit TEXT,
            region TEXT,
            status TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, facility_type, facility_name, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            title TEXT NOT NULL,
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


def extract_resource_tables(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "水资源", "供水量", "用水量", "径流", "降水", "亿立方米",
            "地下", "地表", "总量", "人均",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            numeric_values = extract_numeric_values(cells)
            item = {
                "date": extract_date(cells[0]),
                "indicator": cells[0],
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            if numeric_values:
                item["value"] = numeric_values.get("primary")
                item["yoy_change"] = numeric_values.get("yoy")
                item["unit"] = detect_unit(" ".join(cells))
                item["region"] = detect_region(" ".join(cells))

            if item.get("value"):
                items.append(item)

    return items


def extract_irrigation_tables(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "灌溉", "耕地", "有效", "面积", "万亩", "公顷", "渠道", "节水",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            numeric_values = extract_numeric_values(cells)
            item = {
                "date": extract_date(cells[0]),
                "indicator": cells[0],
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            if numeric_values:
                item["value"] = numeric_values.get("primary")
                item["unit"] = detect_unit(" ".join(cells))
                item["region"] = detect_region(" ".join(cells))

            if item.get("value"):
                items.append(item)

    return items


def extract_quality_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "水质", "类别", "pH", "溶解氧", "COD", "氨氮", "监测",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            item = {
                "date": extract_date(cells[0]),
                "water_body": cells[0],
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            quality_values = extract_quality_values(cells, headers)
            item.update(quality_values)
            item["region"] = detect_region(" ".join(cells))

            if item.get("quality_class") or item.get("ph"):
                items.append(item)

    return items


def extract_infrastructure_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "水库", "大坝", "闸", "泵站", "渠道", "容量", "库容", "亿立方米",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            facility_type = identify_facility_type(" ".join(cells))
            numeric_values = extract_numeric_values(cells)

            item = {
                "date": extract_date(cells[0]),
                "facility_type": facility_type,
                "facility_name": cells[0] if cells else "",
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            if numeric_values:
                item["capacity"] = numeric_values.get("primary")
                item["capacity_unit"] = detect_unit(" ".join(cells))
                item["region"] = detect_region(" ".join(cells))
                item["status"] = detect_status(" ".join(cells))

            if item.get("capacity"):
                items.append(item)

    return items


def extract_news(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for article in sel.css("li a, .news-item, .list-item, .article-item"):
        title_elem = article.css("a")
        if not title_elem:
            continue

        title = title_elem[0].text.strip() if title_elem else ""
        if not title or len(title) < 5:
            continue

        href = title_elem[0].attrib.get("href", "")
        if href and not href.startswith("http"):
            href = "http://www.chinawater.com.cn" + href

        date_text = ""
        date_elem = article.css("span, .date, time")
        if date_elem:
            date_text = date_elem[0].text.strip()
        if not date_text:
            date_text = extract_date(title)

        items.append({
            "date": date_text,
            "title": title,
            "content": "",
            "category": "news",
            "source_url": href or url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def identify_facility_type(text: str) -> str:
    if "水库" in text:
        return "reservoir"
    if "大坝" in text or "坝" in text:
        return "dam"
    if "闸" in text:
        return "sluice"
    if "泵站" in text:
        return "pump_station"
    if "渠道" in text:
        return "canal"
    if "堤防" in text:
        return "levee"
    return "other"


def extract_date(text: str) -> str:
    patterns = [
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{4}年\d{1,2}月\d{1,2}日)",
        r"(\d{4}年\d{1,2}月)",
        r"(\d{4}\.\d{1,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return datetime.now().strftime("%Y-%m-%d")


def extract_numeric_values(cells: list[str]) -> dict:
    result = {}
    number_pattern = r"[\d,]+\.?\d*"

    for cell in cells:
        cell_clean = cell.replace(",", "").replace("，", "")
        numbers = re.findall(number_pattern, cell_clean)
        if not numbers:
            continue

        try:
            value = float(numbers[0])
        except (ValueError, IndexError):
            continue

        if "增长" in cell or "同比" in cell:
            result["yoy"] = value
        elif "primary" not in result:
            result["primary"] = value

    return result


def extract_quality_values(cells: list[str], headers: list[str]) -> dict:
    result = {}
    number_pattern = r"[\d,]+\.?\d*"

    for i, cell in enumerate(cells):
        cell_clean = cell.replace(",", "").replace("，", "")

        if i < len(headers):
            header = headers[i].lower() if i < len(headers) else ""
        else:
            header = ""

        if "类" in cell and ("I" in cell or "II" in cell or "III" in cell or "IV" in cell or "V" in cell):
            result["quality_class"] = cell.strip()
            continue

        numbers = re.findall(number_pattern, cell_clean)
        if not numbers:
            continue

        try:
            value = float(numbers[0])
        except (ValueError, IndexError):
            continue

        if "ph" in header or "pH" in cell:
            result["ph"] = value
        elif "溶解氧" in header or "DO" in header:
            result["dissolved_oxygen"] = value
        elif "cod" in header or "COD" in header:
            result["cod"] = value
        elif "氨氮" in header or "氨氮" in cell:
            result["ammonia_nitrogen"] = value

    return result


def detect_unit(text: str) -> str:
    if "亿立方米" in text:
        return "亿立方米"
    if "万立方米" in text:
        return "万立方米"
    if "万亩" in text:
        return "万亩"
    if "公顷" in text:
        return "公顷"
    if "万千瓦" in text:
        return "万千瓦"
    if "公里" in text:
        return "公里"
    return "N/A"


def detect_region(text: str) -> str:
    regions = {
        "全国": "全国", "长江": "长江流域", "黄河": "黄河流域",
        "珠江": "珠江流域", "淮河": "淮河流域", "海河": "海河流域",
        "松花江": "松花江流域", "辽河": "辽河流域", "太湖": "太湖",
        "华北": "华北", "华东": "华东", "华南": "华南",
    }
    for key, val in regions.items():
        if key in text:
            return val
    return "全国"


def detect_status(text: str) -> str:
    if "在建" in text:
        return "under_construction"
    if "运行" in text or "运营" in text:
        return "operational"
    if "规划" in text:
        return "planned"
    return "unknown"


def save_to_sqlite(items: list[dict], conn: sqlite3.Connection, table: str) -> int:
    inserted = 0
    for item in items:
        try:
            if table == "water_resources":
                conn.execute(
                    """INSERT OR REPLACE INTO water_resources
                    (date, indicator, value, unit, region, yoy_change, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("indicator"), item.get("value"),
                     item.get("unit"), item.get("region"), item.get("yoy_change"),
                     item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "irrigation_stats":
                conn.execute(
                    """INSERT OR REPLACE INTO irrigation_stats
                    (date, indicator, value, unit, region, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("indicator"), item.get("value"),
                     item.get("unit"), item.get("region"),
                     item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "water_quality":
                conn.execute(
                    """INSERT OR REPLACE INTO water_quality
                    (date, water_body, quality_class, ph, dissolved_oxygen, cod,
                     ammonia_nitrogen, region, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("water_body"), item.get("quality_class"),
                     item.get("ph"), item.get("dissolved_oxygen"), item.get("cod"),
                     item.get("ammonia_nitrogen"), item.get("region"),
                     item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "infrastructure":
                conn.execute(
                    """INSERT OR REPLACE INTO infrastructure
                    (date, facility_type, facility_name, capacity, capacity_unit,
                     region, status, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("facility_type"), item.get("facility_name"),
                     item.get("capacity"), item.get("capacity_unit"), item.get("region"),
                     item.get("status"), item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "news_articles":
                conn.execute(
                    """INSERT OR REPLACE INTO news_articles
                    (date, title, content, category, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("title"), item.get("content"),
                     item.get("category"), item.get("source_url"), item.get("scraped_at")),
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


def get_chinawater_data(
    categories: list[str] | None = None,
    include_news: bool = True,
) -> dict:
    """Fetch water resources data from China Water Resources Association.

    Args:
        categories: List of category keys (default: all).
            Available: resources, irrigation, quality, infrastructure.
        include_news: Whether to fetch news.

    Returns:
        Dict with keys: 'resources', 'irrigation', 'quality', 'infrastructure', 'news'.
    """
    if categories is None:
        categories = list(CATEGORIES.keys())

    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "resources": [],
        "irrigation": [],
        "quality": [],
        "infrastructure": [],
        "news": [],
    }

    logger.info("Starting China Water spider for categories: %s", ", ".join(categories))

    extractors = {
        "resources": extract_resource_tables,
        "irrigation": extract_irrigation_tables,
        "quality": extract_quality_data,
        "infrastructure": extract_infrastructure_data,
    }

    for cat in categories:
        if cat == "news":
            continue

        cat_info = CATEGORIES.get(cat, {})
        extractor = extractors.get(cat)
        if not extractor:
            continue

        for url in cat_info.get("urls", []):
            page = fetch_page(url, fetcher)
            if page:
                items = extractor(page["html"], url)
                if items:
                    result[cat].extend(items)
                    save_to_sqlite(items, conn, {
                        "resources": "water_resources",
                        "irrigation": "irrigation_stats",
                        "quality": "water_quality",
                        "infrastructure": "infrastructure",
                    }[cat])
                    logger.info("  Extracted %d %s records", len(items), cat)
            time.sleep(2)

    if include_news:
        for url in CATEGORIES.get("news", {}).get("urls", []):
            page = fetch_page(url, fetcher)
            if page:
                items = extract_news(page["html"], url)
                if items:
                    result["news"].extend(items)
                    save_to_sqlite(items, conn, "news_articles")
                    logger.info("  Extracted %d news articles", len(items))
            time.sleep(2)

    json_map = {
        "resources": JSON_RESOURCES_PATH,
        "irrigation": JSON_IRRIGATION_PATH,
        "quality": JSON_QUALITY_PATH,
        "infrastructure": JSON_INFRA_PATH,
        "news": JSON_NEWS_PATH,
    }
    for key, path in json_map.items():
        if result[key]:
            save_to_json(result[key], path)
            logger.info("Saved %d %s records to %s", len(result[key]), key, path)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_chinawater_data(include_news=True)
    print(f"\n{'=' * 70}")
    print(f"Total resource records: {len(results['resources'])}")
    print(f"Total irrigation records: {len(results['irrigation'])}")
    print(f"Total quality records: {len(results['quality'])}")
    print(f"Total infrastructure records: {len(results['infrastructure'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"{'=' * 70}")
