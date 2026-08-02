#!/usr/bin/env python3
"""
CAEP Spider - 中国环境保护产业协会数据爬虫

Target: http://www.caep.org.cn/
Data: Environmental industry data, pollution control statistics, green technology, market trends

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts environmental protection industry statistics and certification data
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
logger = logging.getLogger("caep")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "caep.db"
JSON_INDUSTRY_PATH = OUTPUT_DIR / "caep_industry.json"
JSON_POLLUTION_PATH = OUTPUT_DIR / "caep_pollution.json"
JSON_TECHNOLOGY_PATH = OUTPUT_DIR / "caep_technology.json"
JSON_NEWS_PATH = OUTPUT_DIR / "caep_news.json"

CATEGORIES = {
    "industry": {
        "cn_name": "环保产业数据",
        "urls": [
            "http://www.caep.org.cn/data/industry_stats.html",
            "http://www.caep.org.cn/data/market_report.html",
            "http://www.caep.org.cn/data/enterprise_list.html",
        ],
    },
    "pollution": {
        "cn_name": "污染治理统计",
        "urls": [
            "http://www.caep.org.cn/data/pollution_control.html",
            "http://www.caep.org.cn/data/emission_stats.html",
            "http://www.caep.org.cn/data/wastewater.html",
            "http://www.caep.org.cn/data/exhaust_gas.html",
        ],
    },
    "technology": {
        "cn_name": "绿色技术数据",
        "urls": [
            "http://www.caep.org.cn/data/green_tech.html",
            "http://www.caep.org.cn/data/certification.html",
            "http://www.caep.org.cn/data/technology_catalog.html",
        ],
    },
    "news": {
        "cn_name": "行业新闻",
        "urls": [
            "http://www.caep.org.cn/news/industry.html",
            "http://www.caep.org.cn/news/policy.html",
            "http://www.caep.org.cn/news/market.html",
        ],
    },
}

POLLUTION_TYPES = {
    "wastewater": {"cn_name": "废水", "unit": "万吨"},
    "exhaust_gas": {"cn_name": "废气", "unit": "亿标立方米"},
    "solid_waste": {"cn_name": "固体废物", "unit": "万吨"},
    "so2": {"cn_name": "二氧化硫", "unit": "万吨"},
    "nox": {"cn_name": "氮氧化物", "unit": "万吨"},
    "cod": {"cn_name": "化学需氧量", "unit": "万吨"},
    "ammonia_n": {"cn_name": "氨氮", "unit": "万吨"},
    "pm25": {"cn_name": "PM2.5", "unit": "微克/立方米"},
    "pm10": {"cn_name": "PM10", "unit": "微克/立方米"},
}


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS industry_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            indicator TEXT NOT NULL,
            value REAL,
            unit TEXT,
            sub_sector TEXT,
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, indicator, sub_sector, region, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pollution_control (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            pollutant_type TEXT NOT NULL,
            pollutant_cn TEXT,
            emission REAL,
            removal REAL,
            removal_rate REAL,
            unit TEXT,
            source_type TEXT,
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, pollutant_type, source_type, region, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS green_technology (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            tech_name TEXT NOT NULL,
            tech_type TEXT,
            certification_no TEXT,
            applicant TEXT,
            application_area TEXT,
            status TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, tech_name, certification_no, source_url)
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


def extract_industry_tables(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "产值", "营收", "收入", "利润", "企业", "从业", "亿元", "万人",
            "环保产业", "水处理", "大气", "固废", "噪声",
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
                item["sub_sector"] = detect_sub_sector(" ".join(cells))
                item["region"] = detect_region(" ".join(cells))

            if item.get("value"):
                items.append(item)

    return items


def extract_pollution_tables(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "排放", "去除", "治理", "废水", "废气", "固废", "脱硫", "脱硝",
            "COD", "氨氮", "二氧化硫", "氮氧化物",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            pollutant_type = identify_pollutant(" ".join(cells))
            numeric_values = extract_pollution_values(cells)

            item = {
                "date": extract_date(cells[0]),
                "pollutant_type": pollutant_type,
                "pollutant_cn": POLLUTION_TYPES.get(pollutant_type, {}).get("cn_name", ""),
                "unit": POLLUTION_TYPES.get(pollutant_type, {}).get("unit", detect_unit(" ".join(cells))),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(numeric_values)
            item["source_type"] = detect_source_type(" ".join(cells))
            item["region"] = detect_region(" ".join(cells))

            if item.get("emission") or item.get("removal"):
                items.append(item)

    return items


def extract_technology_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "技术", "认证", "编号", "申请", "工艺", "设备", "示范",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            item = {
                "date": extract_date(cells[0]),
                "tech_name": cells[0],
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            for i, cell in enumerate(cells[1:], 1):
                if i < len(headers):
                    header = headers[i]
                    if "类型" in header:
                        item["tech_type"] = cell
                    elif "编号" in header:
                        item["certification_no"] = cell
                    elif "申请" in header or "单位" in header:
                        item["applicant"] = cell
                    elif "领域" in header or "应用" in header:
                        item["application_area"] = cell
                    elif "状态" in header:
                        item["status"] = cell
                else:
                    if not item.get("tech_type"):
                        item["tech_type"] = cell
                    elif not item.get("certification_no"):
                        item["certification_no"] = cell

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
            href = "http://www.caep.org.cn" + href

        date_text = ""
        date_elem = article.css("span, .date, time")
        if date_elem:
            date_text = date_elem[0].text.strip()
        if not date_text:
            date_text = extract_date(title)

        category = "news"
        if "policy" in url.lower() or "政策" in title:
            category = "policy"
        elif "market" in url.lower() or "市场" in title:
            category = "market"

        items.append({
            "date": date_text,
            "title": title,
            "content": "",
            "category": category,
            "source_url": href or url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def identify_pollutant(text: str) -> str:
    if "废水" in text:
        return "wastewater"
    if "废气" in text:
        return "exhaust_gas"
    if "固废" in text or "固体废物" in text:
        return "solid_waste"
    if "二氧化硫" in text or "SO2" in text:
        return "so2"
    if "氮氧化物" in text or "NOx" in text:
        return "nox"
    if "COD" in text or "化学需氧量" in text:
        return "cod"
    if "氨氮" in text:
        return "ammonia_n"
    if "PM2.5" in text:
        return "pm25"
    if "PM10" in text:
        return "pm10"
    return "other"


def detect_sub_sector(text: str) -> str:
    if "水处理" in text or "废水" in text:
        return "water_treatment"
    if "大气" in text or "废气" in text or "脱硫" in text or "脱硝" in text:
        return "air_pollution"
    if "固废" in text or "废物" in text or "垃圾" in text:
        return "solid_waste"
    if "噪声" in text:
        return "noise"
    if "土壤" in text:
        return "soil"
    if "修复" in text:
        return "remediation"
    return "general"


def detect_source_type(text: str) -> str:
    if "工业" in text:
        return "industrial"
    if "生活" in text:
        return "municipal"
    if "农业" in text:
        return "agricultural"
    if "集中" in text:
        return "centralized"
    return "general"


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

        if "primary" not in result:
            result["primary"] = value

    return result


def extract_pollution_values(cells: list[str]) -> dict:
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

        if "排放" in cell:
            result["emission"] = value
        elif "去除" in cell or "削减" in cell:
            result["removal"] = value
        elif "率" in cell:
            result["removal_rate"] = value
        elif i == 1 and "emission" not in result:
            result["emission"] = value
        elif i == 2 and "removal" not in result:
            result["removal"] = value

    return result


def detect_unit(text: str) -> str:
    if "亿元" in text:
        return "亿元"
    if "万吨" in text:
        return "万吨"
    if "万人" in text:
        return "万人"
    if "亿标立方米" in text:
        return "亿标立方米"
    if "%" in text:
        return "%"
    return "N/A"


def detect_region(text: str) -> str:
    regions = {
        "全国": "全国", "华北": "华北", "华东": "华东", "华南": "华南",
        "华中": "华中", "西北": "西北", "东北": "东北", "西南": "西南",
    }
    for key, val in regions.items():
        if key in text:
            return val
    return "全国"


def save_to_sqlite(items: list[dict], conn: sqlite3.Connection, table: str) -> int:
    inserted = 0
    for item in items:
        try:
            if table == "industry_stats":
                conn.execute(
                    """INSERT OR REPLACE INTO industry_stats
                    (date, indicator, value, unit, sub_sector, region, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("indicator"), item.get("value"),
                     item.get("unit"), item.get("sub_sector"), item.get("region"),
                     item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "pollution_control":
                conn.execute(
                    """INSERT OR REPLACE INTO pollution_control
                    (date, pollutant_type, pollutant_cn, emission, removal, removal_rate,
                     unit, source_type, region, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("pollutant_type"), item.get("pollutant_cn"),
                     item.get("emission"), item.get("removal"), item.get("removal_rate"),
                     item.get("unit"), item.get("source_type"), item.get("region"),
                     item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "green_technology":
                conn.execute(
                    """INSERT OR REPLACE INTO green_technology
                    (date, tech_name, tech_type, certification_no, applicant,
                     application_area, status, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("tech_name"), item.get("tech_type"),
                     item.get("certification_no"), item.get("applicant"),
                     item.get("application_area"), item.get("status"),
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


def get_caep_data(
    categories: list[str] | None = None,
    include_news: bool = True,
) -> dict:
    """Fetch environmental industry data from CAEP.

    Args:
        categories: List of category keys (default: all).
            Available: industry, pollution, technology.
        include_news: Whether to fetch news.

    Returns:
        Dict with keys: 'industry', 'pollution', 'technology', 'news'.
    """
    if categories is None:
        categories = list(CATEGORIES.keys())

    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "industry": [],
        "pollution": [],
        "technology": [],
        "news": [],
    }

    logger.info("Starting CAEP spider for categories: %s", ", ".join(categories))

    extractors = {
        "industry": extract_industry_tables,
        "pollution": extract_pollution_tables,
        "technology": extract_technology_data,
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
                        "industry": "industry_stats",
                        "pollution": "pollution_control",
                        "technology": "green_technology",
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
        "industry": JSON_INDUSTRY_PATH,
        "pollution": JSON_POLLUTION_PATH,
        "technology": JSON_TECHNOLOGY_PATH,
        "news": JSON_NEWS_PATH,
    }
    for key, path in json_map.items():
        if result[key]:
            save_to_json(result[key], path)
            logger.info("Saved %d %s records to %s", len(result[key]), key, path)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_caep_data(include_news=True)
    print(f"\n{'=' * 70}")
    print(f"Total industry records: {len(results['industry'])}")
    print(f"Total pollution records: {len(results['pollution'])}")
    print(f"Total technology records: {len(results['technology'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"{'=' * 70}")
