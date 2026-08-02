#!/usr/bin/env python3
"""
CCTDA Spider - 中国煤炭运销协会数据爬虫

Target: http://www.cctda.com.cn/
Data: Coal production statistics, transportation data, price indices, market analysis

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts coal price indices and transport volume data
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
logger = logging.getLogger("cctda")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "cctda.db"
JSON_PRODUCTION_PATH = OUTPUT_DIR / "cctda_production.json"
JSON_TRANSPORT_PATH = OUTPUT_DIR / "cctda_transport.json"
JSON_PRICE_PATH = OUTPUT_DIR / "cctda_price.json"
JSON_NEWS_PATH = OUTPUT_DIR / "cctda_news.json"

CATEGORIES = {
    "production": {
        "cn_name": "煤炭生产统计",
        "urls": [
            "http://www.cctda.com.cn/index/statistics/production.html",
            "http://www.cctda.com.cn/index/statistics/output.html",
        ],
    },
    "transport": {
        "cn_name": "煤炭运输数据",
        "urls": [
            "http://www.cctda.com.cn/index/statistics/transport.html",
            "http://www.cctda.com.cn/index/statistics/railway.html",
            "http://www.cctda.com.cn/index/statistics/port.html",
        ],
    },
    "price": {
        "cn_name": "煤炭价格指数",
        "urls": [
            "http://www.cctda.com.cn/index/statistics/price.html",
            "http://www.cctda.com.cn/index/statistics/index.html",
            "http://www.cctda.com.cn/index/data/price_index.html",
        ],
    },
    "news": {
        "cn_name": "市场分析",
        "urls": [
            "http://www.cctda.com.cn/index/news/market.html",
            "http://www.cctda.com.cn/index/news/analysis.html",
        ],
    },
}

COAL_TYPES = {
    "thermal_coal": {"cn_name": "动力煤", "unit": "元/吨"},
    "coking_coal": {"cn_name": "焦煤", "unit": "元/吨"},
    "anthracite": {"cn_name": "无烟煤", "unit": "元/吨"},
    "lignite": {"cn_name": "褐煤", "unit": "元/吨"},
    "raw_coal": {"cn_name": "原煤", "unit": "万吨"},
}


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS production_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            coal_type TEXT NOT NULL,
            coal_type_cn TEXT,
            production REAL,
            unit TEXT DEFAULT '万吨',
            yoy_change REAL,
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, coal_type, region, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transport_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            transport_mode TEXT NOT NULL,
            volume REAL,
            unit TEXT DEFAULT '万吨',
            route TEXT,
            yoy_change REAL,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, transport_mode, route, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            coal_type TEXT NOT NULL,
            coal_type_cn TEXT,
            price_index REAL,
            price_low REAL,
            price_high REAL,
            price_avg REAL,
            unit TEXT DEFAULT '元/吨',
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, coal_type, region, source_url)
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


def extract_production_tables(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "产量", "原煤", "动力煤", "焦煤", "万吨", "同比", "生产",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            coal_type = identify_coal_type(" ".join(cells))
            numeric_values = extract_numeric_values(cells)

            item = {
                "date": extract_date(cells[0]),
                "coal_type": coal_type,
                "coal_type_cn": COAL_TYPES.get(coal_type, {}).get("cn_name", ""),
                "unit": COAL_TYPES.get(coal_type, {}).get("unit", "万吨"),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            if numeric_values:
                item["production"] = numeric_values.get("primary")
                item["yoy_change"] = numeric_values.get("yoy")
                item["region"] = detect_region(" ".join(cells))

            if item.get("production"):
                items.append(item)

    return items


def extract_transport_tables(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "运输", "运量", "铁路", "港口", "发运", "调运", "万吨",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            transport_mode = identify_transport_mode(" ".join(cells))
            numeric_values = extract_numeric_values(cells)

            item = {
                "date": extract_date(cells[0]),
                "transport_mode": transport_mode,
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            if numeric_values:
                item["volume"] = numeric_values.get("primary")
                item["yoy_change"] = numeric_values.get("yoy")
                item["unit"] = "万吨"
                item["route"] = detect_route(" ".join(cells))

            if item.get("volume"):
                items.append(item)

    return items


def extract_price_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "价格", "指数", "均价", "元/吨", "涨跌", "报价",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            coal_type = identify_coal_type(" ".join(cells))
            price_values = extract_price_values(cells)

            item = {
                "date": extract_date(cells[0]),
                "coal_type": coal_type,
                "coal_type_cn": COAL_TYPES.get(coal_type, {}).get("cn_name", ""),
                "unit": "元/吨",
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(price_values)
            item["region"] = detect_region(" ".join(cells))

            if item.get("price_avg") or item.get("price_index"):
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
            href = "http://www.cctda.com.cn" + href

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
            "category": "market" if "分析" in url or "market" in url else "news",
            "source_url": href or url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def identify_coal_type(text: str) -> str:
    if "动力煤" in text or "thermal" in text.lower():
        return "thermal_coal"
    if "焦煤" in text or "coking" in text.lower():
        return "coking_coal"
    if "无烟煤" in text or "anthracite" in text.lower():
        return "anthracite"
    if "褐煤" in text or "lignite" in text.lower():
        return "lignite"
    return "raw_coal"


def identify_transport_mode(text: str) -> str:
    if "铁路" in text or "railway" in text.lower():
        return "railway"
    if "港口" in text or "port" in text.lower():
        return "port"
    if "公路" in text or "road" in text.lower():
        return "road"
    if "水路" in text or "waterway" in text.lower():
        return "waterway"
    return "total"


def detect_route(text: str) -> str:
    routes = {
        "大秦线": "大秦线", "朔黄线": "朔黄线", "唐山港": "唐山港",
        "秦皇岛": "秦皇岛港", "黄骅港": "黄骅港", "天津港": "天津港",
        "曹妃甸": "曹妃甸", "环渤海": "环渤海",
    }
    for key, val in routes.items():
        if key in text:
            return val
    return ""


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
        elif "primary" not in result:
            result["primary"] = value

    return result


def extract_price_values(cells: list[str]) -> dict:
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

        if "指数" in cell or "index" in cell.lower():
            result["price_index"] = value
        elif "低" in cell or "最低" in cell:
            result["price_low"] = value
        elif "高" in cell or "最高" in cell:
            result["price_high"] = value
        elif "均" in cell or "平均" in cell:
            result["price_avg"] = value
        elif i == 1 and "price_low" not in result:
            result["price_low"] = value
        elif i == 2 and "price_high" not in result:
            result["price_high"] = value
        elif i == 3 and "price_avg" not in result:
            result["price_avg"] = value

    if "price_avg" not in result and "price_low" in result and "price_high" in result:
        result["price_avg"] = (result["price_low"] + result["price_high"]) / 2

    return result


def detect_region(text: str) -> str:
    regions = {
        "全国": "全国", "山西": "山西", "陕西": "陕西", "内蒙古": "内蒙古",
        "河北": "河北", "山东": "山东", "河南": "河南", "新疆": "新疆",
        "贵州": "贵州", "安徽": "安徽",
    }
    for key, val in regions.items():
        if key in text:
            return val
    return "全国"


def save_to_sqlite(items: list[dict], conn: sqlite3.Connection, table: str) -> int:
    inserted = 0
    for item in items:
        try:
            if table == "production_stats":
                conn.execute(
                    """INSERT OR REPLACE INTO production_stats
                    (date, coal_type, coal_type_cn, production, unit, yoy_change, region, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("coal_type"), item.get("coal_type_cn"),
                     item.get("production"), item.get("unit"), item.get("yoy_change"),
                     item.get("region"), item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "transport_stats":
                conn.execute(
                    """INSERT OR REPLACE INTO transport_stats
                    (date, transport_mode, volume, unit, route, yoy_change, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("transport_mode"), item.get("volume"),
                     item.get("unit"), item.get("route"), item.get("yoy_change"),
                     item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "price_index":
                conn.execute(
                    """INSERT OR REPLACE INTO price_index
                    (date, coal_type, coal_type_cn, price_index, price_low, price_high,
                     price_avg, unit, region, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("coal_type"), item.get("coal_type_cn"),
                     item.get("price_index"), item.get("price_low"), item.get("price_high"),
                     item.get("price_avg"), item.get("unit"), item.get("region"),
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


def get_cctda_data(
    categories: list[str] | None = None,
    include_news: bool = True,
) -> dict:
    """Fetch coal industry data from CCTDA.

    Args:
        categories: List of category keys (default: all).
            Available: production, transport, price.
        include_news: Whether to fetch market analysis.

    Returns:
        Dict with keys: 'production', 'transport', 'price', 'news'.
    """
    if categories is None:
        categories = list(CATEGORIES.keys())

    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "production": [],
        "transport": [],
        "price": [],
        "news": [],
    }

    logger.info("Starting CCTDA spider for categories: %s", ", ".join(categories))

    for cat in categories:
        if cat == "news":
            continue

        cat_info = CATEGORIES.get(cat, {})
        for url in cat_info.get("urls", []):
            page = fetch_page(url, fetcher)
            if page:
                if cat == "production":
                    items = extract_production_tables(page["html"], url)
                    if items:
                        result["production"].extend(items)
                        save_to_sqlite(items, conn, "production_stats")
                        logger.info("  Extracted %d production records", len(items))
                elif cat == "transport":
                    items = extract_transport_tables(page["html"], url)
                    if items:
                        result["transport"].extend(items)
                        save_to_sqlite(items, conn, "transport_stats")
                        logger.info("  Extracted %d transport records", len(items))
                elif cat == "price":
                    items = extract_price_data(page["html"], url)
                    if items:
                        result["price"].extend(items)
                        save_to_sqlite(items, conn, "price_index")
                        logger.info("  Extracted %d price records", len(items))
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
        "production": JSON_PRODUCTION_PATH,
        "transport": JSON_TRANSPORT_PATH,
        "price": JSON_PRICE_PATH,
        "news": JSON_NEWS_PATH,
    }
    for key in ["production", "transport", "price", "news"]:
        if result[key]:
            save_to_json(result[key], json_map[key])
            logger.info("Saved %d %s records to %s", len(result[key]), key, json_map[key])

    conn.close()
    return result


if __name__ == "__main__":
    results = get_cctda_data(include_news=True)
    print(f"\n{'=' * 70}")
    print(f"Total production records: {len(results['production'])}")
    print(f"Total transport records: {len(results['transport'])}")
    print(f"Total price records: {len(results['price'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"{'=' * 70}")
