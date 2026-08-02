#!/usr/bin/env python3
"""
China Nuclear Energy Association Spider - 中国核能行业协会数据爬虫

Target: http://www.china-nea.org/
Data: Nuclear power statistics, plant operation data, safety records, industry development

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts nuclear power generation and safety data
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
logger = logging.getLogger("china_nea")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "china_nea.db"
JSON_POWER_PATH = OUTPUT_DIR / "china_nea_power.json"
JSON_PLANT_PATH = OUTPUT_DIR / "china_nea_plants.json"
JSON_SAFETY_PATH = OUTPUT_DIR / "china_nea_safety.json"
JSON_NEWS_PATH = OUTPUT_DIR / "china_nea_news.json"

CATEGORIES = {
    "power": {
        "cn_name": "核电统计",
        "urls": [
            "http://www.china-nea.org/data/power_stats.html",
            "http://www.china-nea.org/data/generation.html",
            "http://www.china-nea.org/data/annual_report.html",
        ],
    },
    "plants": {
        "cn_name": "电站运行数据",
        "urls": [
            "http://www.china-nea.org/data/plant_operation.html",
            "http://www.china-nea.org/data/unit_status.html",
            "http://www.china-nea.org/data/construction.html",
        ],
    },
    "safety": {
        "cn_name": "安全记录",
        "urls": [
            "http://www.china-nea.org/data/safety.html",
            "http://www.china-nea.org/data/wano_index.html",
        ],
    },
    "news": {
        "cn_name": "行业动态",
        "urls": [
            "http://www.china-nea.org/news/industry.html",
            "http://www.china-nea.org/news/policy.html",
            "http://www.china-nea.org/news/technology.html",
        ],
    },
}

NUCLEAR_PLANTS = {
    "daya_bay": {"cn_name": "大亚湾核电站", "location": "广东深圳"},
    "lingao": {"cn_name": "岭澳核电站", "location": "广东深圳"},
    "qinshan": {"cn_name": "秦山核电站", "location": "浙江海盐"},
    "tianwan": {"cn_name": "田湾核电站", "location": "江苏连云港"},
    "hongyanhe": {"cn_name": "红沿河核电站", "location": "辽宁大连"},
    "ningde": {"cn_name": "宁德核电站", "location": "福建宁德"},
    "yangjiang": {"cn_name": "阳江核电站", "location": "广东阳江"},
    "taishan": {"cn_name": "台山核电站", "location": "广东台山"},
    "fuqing": {"cn_name": "福清核电站", "location": "福建福清"},
    "fangchenggang": {"cn_name": "防城港核电站", "location": "广西防城港"},
    "changjiang": {"cn_name": "昌江核电站", "location": "海南昌江"},
    "sanmen": {"cn_name": "三门核电站", "location": "浙江三门"},
    "haiyang": {"cn_name": "海阳核电站", "location": "山东海阳"},
    "rongcheng": {"cn_name": "荣成石岛湾核电站", "location": "山东荣成"},
    "xiapu": {"cn_name": "霞浦核电站", "location": "福建霞浦"},
}


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS power_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            indicator TEXT NOT NULL,
            value REAL,
            unit TEXT,
            yoy_change REAL,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, indicator, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plant_operation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            plant_name TEXT NOT NULL,
            plant_cn TEXT,
            location TEXT,
            unit_count INTEGER,
            capacity REAL,
            capacity_unit TEXT DEFAULT 'MWe',
            generation REAL,
            generation_unit TEXT DEFAULT 'TWh',
            capacity_factor REAL,
            status TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, plant_name, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS safety_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            plant_name TEXT NOT NULL,
            plant_cn TEXT,
            wano_score REAL,
            wano_index TEXT,
            safety_level TEXT,
            incidents INTEGER DEFAULT 0,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, plant_name, wano_index, source_url)
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


def extract_power_tables(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "发电量", "装机", "容量", "核电", "亿千瓦", "万千瓦",
            "利用小时", "上网", "运行",
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

            if item.get("value"):
                items.append(item)

    return items


def extract_plant_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "电站", "机组", "容量", "运行", "MWe", "兆瓦", "发电",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            plant_key = identify_plant(" ".join(cells))
            plant_info = NUCLEAR_PLANTS.get(plant_key, {})
            numeric_values = extract_plant_values(cells)

            item = {
                "date": extract_date(cells[0]),
                "plant_name": plant_key,
                "plant_cn": plant_info.get("cn_name", cells[0]),
                "location": plant_info.get("location", ""),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(numeric_values)

            if item.get("capacity") or item.get("generation"):
                items.append(item)

    return items


def extract_safety_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "安全", "WANO", "指标", "评分", "事件", "等级", "INES",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            plant_key = identify_plant(" ".join(cells))
            plant_info = NUCLEAR_PLANTS.get(plant_key, {})
            safety_values = extract_safety_values(cells, headers)

            item = {
                "date": extract_date(cells[0]),
                "plant_name": plant_key,
                "plant_cn": plant_info.get("cn_name", cells[0]),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(safety_values)

            if item.get("wano_score") or item.get("safety_level"):
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
            href = "http://www.china-nea.org" + href

        date_text = ""
        date_elem = article.css("span, .date, time")
        if date_elem:
            date_text = date_elem[0].text.strip()
        if not date_text:
            date_text = extract_date(title)

        category = "news"
        if "policy" in url.lower() or "政策" in title:
            category = "policy"
        elif "technology" in url.lower() or "技术" in title:
            category = "technology"

        items.append({
            "date": date_text,
            "title": title,
            "content": "",
            "category": category,
            "source_url": href or url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def identify_plant(text: str) -> str:
    for key, info in NUCLEAR_PLANTS.items():
        if info["cn_name"] in text or key.replace("_", " ") in text.lower():
            return key
    return "unknown"


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


def extract_plant_values(cells: list[str]) -> dict:
    result = {}
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

        if "机组" in cell or "台" in cell:
            result["unit_count"] = int(value)
        elif "容量" in cell or "MWe" in cell or "兆瓦" in cell:
            result["capacity"] = value
            result["capacity_unit"] = "MWe"
        elif "发电" in cell or "TWh" in cell or "亿千瓦" in cell:
            result["generation"] = value
            result["generation_unit"] = "TWh"
        elif "利用" in cell or "因子" in cell or "%" in cell:
            result["capacity_factor"] = value
        elif "运行" in cell or "状态" in cell:
            result["status"] = cell
        elif i == 1 and "unit_count" not in result:
            result["unit_count"] = int(value)
        elif i == 2 and "capacity" not in result:
            result["capacity"] = value
            result["capacity_unit"] = "MWe"

    return result


def extract_safety_values(cells: list[str], headers: list[str]) -> dict:
    result = {}
    number_pattern = r"[\d,]+\.?\d*"

    for i, cell in enumerate(cells):
        header = headers[i] if i < len(headers) else ""

        if "WANO" in header or "wano" in header.lower():
            numbers = re.findall(number_pattern, cell.replace(",", ""))
            if numbers:
                try:
                    result["wano_score"] = float(numbers[0])
                except ValueError:
                    pass
            result["wano_index"] = header
        elif "等级" in header or "level" in header.lower():
            result["safety_level"] = cell
        elif "事件" in header or "incident" in header.lower():
            numbers = re.findall(number_pattern, cell.replace(",", ""))
            if numbers:
                try:
                    result["incidents"] = int(numbers[0])
                except ValueError:
                    pass

    return result


def detect_unit(text: str) -> str:
    if "亿千瓦时" in text:
        return "亿千瓦时"
    if "万千瓦" in text or "MWe" in text:
        return "万千瓦"
    if "小时" in text:
        return "小时"
    if "%" in text:
        return "%"
    return "N/A"


def save_to_sqlite(items: list[dict], conn: sqlite3.Connection, table: str) -> int:
    inserted = 0
    for item in items:
        try:
            if table == "power_stats":
                conn.execute(
                    """INSERT OR REPLACE INTO power_stats
                    (date, indicator, value, unit, yoy_change, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("indicator"), item.get("value"),
                     item.get("unit"), item.get("yoy_change"),
                     item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "plant_operation":
                conn.execute(
                    """INSERT OR REPLACE INTO plant_operation
                    (date, plant_name, plant_cn, location, unit_count, capacity,
                     capacity_unit, generation, generation_unit, capacity_factor,
                     status, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("plant_name"), item.get("plant_cn"),
                     item.get("location"), item.get("unit_count"), item.get("capacity"),
                     item.get("capacity_unit"), item.get("generation"),
                     item.get("generation_unit"), item.get("capacity_factor"),
                     item.get("status"), item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "safety_records":
                conn.execute(
                    """INSERT OR REPLACE INTO safety_records
                    (date, plant_name, plant_cn, wano_score, wano_index,
                     safety_level, incidents, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("plant_name"), item.get("plant_cn"),
                     item.get("wano_score"), item.get("wano_index"),
                     item.get("safety_level"), item.get("incidents"),
                     item.get("source_url"), item.get("scraped_at")),
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


def get_china_nea_data(
    categories: list[str] | None = None,
    include_news: bool = True,
) -> dict:
    """Fetch nuclear energy data from China Nuclear Energy Association.

    Args:
        categories: List of category keys (default: all).
            Available: power, plants, safety.
        include_news: Whether to fetch industry news.

    Returns:
        Dict with keys: 'power', 'plants', 'safety', 'news'.
    """
    if categories is None:
        categories = list(CATEGORIES.keys())

    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "power": [],
        "plants": [],
        "safety": [],
        "news": [],
    }

    logger.info("Starting China NEA spider for categories: %s", ", ".join(categories))

    extractors = {
        "power": extract_power_tables,
        "plants": extract_plant_data,
        "safety": extract_safety_data,
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
                        "power": "power_stats",
                        "plants": "plant_operation",
                        "safety": "safety_records",
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
        "power": JSON_POWER_PATH,
        "plants": JSON_PLANT_PATH,
        "safety": JSON_SAFETY_PATH,
        "news": JSON_NEWS_PATH,
    }
    for key, path in json_map.items():
        if result[key]:
            save_to_json(result[key], path)
            logger.info("Saved %d %s records to %s", len(result[key]), key, path)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_china_nea_data(include_news=True)
    print(f"\n{'=' * 70}")
    print(f"Total power records: {len(results['power'])}")
    print(f"Total plant records: {len(results['plants'])}")
    print(f"Total safety records: {len(results['safety'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"{'=' * 70}")
