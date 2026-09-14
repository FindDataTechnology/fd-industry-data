#!/usr/bin/env python3
"""
China Electricity Council Spider - 中国电力企业联合会数据爬虫

Target: http://www.cec.org.cn/
Data: Electricity production statistics, power consumption, grid infrastructure,
      renewable energy statistics

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts statistical tables and industry reports
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
logger = logging.getLogger("cec")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "cec.db"
JSON_STATS_PATH = OUTPUT_DIR / "cec_electricity_stats.json"
JSON_NEWS_PATH = OUTPUT_DIR / "cec_news.json"
JSON_RENEWABLE_PATH = OUTPUT_DIR / "cec_renewable.json"

CATEGORIES = {
    "production": {
        "cn_name": "电力生产统计",
        "urls": [
            "http://www.cec.org.cn/cec/ywlm/dlsj/",
            "http://www.cec.org.cn/cec/ywlm/fxyc/",
        ],
    },
    "consumption": {
        "cn_name": "用电统计",
        "urls": [
            "http://www.cec.org.cn/cec/ywlm/ydlj/",
            "http://www.cec.org.cn/cec/ywlm/dlyx/",
        ],
    },
    "grid": {
        "cn_name": "电网基础设施",
        "urls": [
            "http://www.cec.org.cn/cec/ywlm/gdwlb/",
            "http://www.cec.org.cn/cec/ywlm/dlgh/",
        ],
    },
    "renewable": {
        "cn_name": "可再生能源统计",
        "urls": [
            "http://www.cec.org.cn/cec/ywlm/xny/",
            "http://www.cec.org.cn/cec/ywlm/flfd/",
        ],
    },
    "news": {
        "cn_name": "行业新闻",
        "urls": [
            "http://www.cec.org.cn/cec/xwzx/hynews/",
            "http://www.cec.org.cn/cec/xwzx/zhxw/",
        ],
    },
}


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS electricity_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            category TEXT NOT NULL,
            indicator TEXT NOT NULL,
            value REAL,
            unit TEXT,
            region TEXT,
            yoy_change REAL,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, category, indicator, region, source_url)
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS renewable_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            energy_type TEXT NOT NULL,
            capacity REAL,
            generation REAL,
            capacity_unit TEXT DEFAULT 'GW',
            generation_unit TEXT DEFAULT 'TWh',
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, energy_type, region, source_url)
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


def extract_stat_tables(html: str, url: str, category: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "发电量", "用电量", "负荷", "装机", "容量", "增长", "同比",
            "统计", "合计", "总量", "万千瓦", "亿千瓦", "billion", "GW",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            item = {
                "date": extract_date(cells[0] if cells else ""),
                "category": category,
                "indicator": cells[0] if cells else "",
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            numeric_values = extract_numeric_values(cells)
            if numeric_values:
                item["value"] = numeric_values.get("primary")
                item["yoy_change"] = numeric_values.get("yoy")
                item["unit"] = detect_unit(" ".join(cells))
                item["region"] = detect_region(" ".join(cells))
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
            href = "http://www.cec.org.cn" + href

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


def extract_renewable_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "风电", "光伏", "水电", "核电", "新能源", "可再生", "清洁能源",
            "wind", "solar", "hydro", "nuclear",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            energy_type = identify_energy_type(" ".join(cells))
            numeric_values = extract_numeric_values(cells)

            item = {
                "date": extract_date(cells[0]),
                "energy_type": energy_type,
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            if numeric_values:
                item["capacity"] = numeric_values.get("capacity")
                item["generation"] = numeric_values.get("generation")
                item["capacity_unit"] = "GW"
                item["generation_unit"] = "TWh"
                item["region"] = detect_region(" ".join(cells))
                items.append(item)

    return items


def identify_energy_type(text: str) -> str:
    if "风电" in text or "wind" in text.lower():
        return "wind"
    if "光伏" in text or "solar" in text.lower():
        return "solar"
    if "水电" in text or "hydro" in text.lower():
        return "hydro"
    if "核电" in text or "nuclear" in text.lower():
        return "nuclear"
    if "生物质" in text or "biomass" in text.lower():
        return "biomass"
    return "other_renewable"


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

        if "增长" in cell or "同比" in cell or "增速" in cell:
            result["yoy"] = value
        elif "装机" in cell or "容量" in cell or "capacity" in cell.lower():
            result["capacity"] = value
        elif "发电" in cell or "发电" in cell or "generation" in cell.lower():
            result["generation"] = value
        elif "primary" not in result:
            result["primary"] = value

    return result


def detect_unit(text: str) -> str:
    if "万千瓦" in text:
        return "万千瓦"
    if "亿千瓦" in text:
        return "亿千瓦"
    if "亿千瓦时" in text:
        return "亿千瓦时"
    if "万吨" in text:
        return "万吨"
    if "%" in text:
        return "%"
    return "N/A"


def detect_region(text: str) -> str:
    regions = {
        "全国": "全国", "华北": "华北", "华东": "华东", "华南": "华南",
        "华中": "华中", "西北": "西北", "东北": "东北", "西南": "西南",
        "南方": "南方", "北方": "北方",
    }
    for key, val in regions.items():
        if key in text:
            return val
    return "全国"


def save_stats_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO electricity_stats
                (date, category, indicator, value, unit, region, yoy_change, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"), item.get("category"), item.get("indicator"),
                    item.get("value"), item.get("unit"), item.get("region"),
                    item.get("yoy_change"), item.get("source_url"), item.get("scraped_at"),
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
                INSERT OR REPLACE INTO news_articles
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


def save_renewable_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO renewable_stats
                (date, energy_type, capacity, generation, capacity_unit, generation_unit, region, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"), item.get("energy_type"),
                    item.get("capacity"), item.get("generation"),
                    item.get("capacity_unit"), item.get("generation_unit"),
                    item.get("region"), item.get("source_url"), item.get("scraped_at"),
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


def get_cec_data(
    categories: list[str] | None = None,
    include_news: bool = True,
) -> dict:
    """Fetch electricity industry data from China Electricity Council.

    Args:
        categories: List of category keys (default: all).
            Available: production, consumption, grid, renewable.
        include_news: Whether to fetch industry news.

    Returns:
        Dict with keys: 'stats', 'news', 'renewable'.
    """
    if categories is None:
        categories = list(CATEGORIES.keys())

    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "stats": [],
        "news": [],
        "renewable": [],
    }

    logger.info("Starting CEC spider for categories: %s", ", ".join(categories))

    for cat in categories:
        if cat == "news":
            continue

        cat_info = CATEGORIES.get(cat, {})
        for url in cat_info.get("urls", []):
            page = fetch_page(url, fetcher)
            if page:
                if cat == "renewable":
                    items = extract_renewable_data(page["html"], url)
                    if items:
                        result["renewable"].extend(items)
                        save_renewable_to_sqlite(items, conn)
                        logger.info("  Extracted %d renewable records", len(items))
                else:
                    items = extract_stat_tables(page["html"], url, cat)
                    if items:
                        result["stats"].extend(items)
                        save_stats_to_sqlite(items, conn)
                        logger.info("  Extracted %d stat records for %s", len(items), cat)
            time.sleep(2)

    if include_news:
        for url in CATEGORIES.get("news", {}).get("urls", []):
            page = fetch_page(url, fetcher)
            if page:
                items = extract_news(page["html"], url)
                if items:
                    result["news"].extend(items)
                    save_news_to_sqlite(items, conn)
                    logger.info("  Extracted %d news articles", len(items))
            time.sleep(2)

    if result["stats"]:
        save_to_json(result["stats"], JSON_STATS_PATH)
        logger.info("Saved %d stat records to %s", len(result["stats"]), JSON_STATS_PATH)

    if result["news"]:
        save_to_json(result["news"], JSON_NEWS_PATH)
        logger.info("Saved %d news articles to %s", len(result["news"]), JSON_NEWS_PATH)

    if result["renewable"]:
        save_to_json(result["renewable"], JSON_RENEWABLE_PATH)
        logger.info("Saved %d renewable records to %s", len(result["renewable"]), JSON_RENEWABLE_PATH)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_cec_data(include_news=True)
    print(f"\n{'=' * 70}")
    print(f"Total stat records: {len(results['stats'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"Total renewable records: {len(results['renewable'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON (stats): {JSON_STATS_PATH}")
    print(f"JSON (news): {JSON_NEWS_PATH}")
    print(f"JSON (renewable): {JSON_RENEWABLE_PATH}")
    print(f"{'=' * 70}")
