#!/usr/bin/env python3
"""
CPIA Spider - 中国医药工业信息中心数据爬虫

Target: http://www.cpia.org.cn/
Data: Pharmaceutical production statistics, drug sales data, export/import data, industry trends

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts pharmaceutical production and sales statistics
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
logger = logging.getLogger("cpia")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "cpia.db"
JSON_PRODUCTION_PATH = OUTPUT_DIR / "cpia_production.json"
JSON_SALES_PATH = OUTPUT_DIR / "cpia_sales.json"
JSON_TRADE_PATH = OUTPUT_DIR / "cpia_trade.json"
JSON_NEWS_PATH = OUTPUT_DIR / "cpia_news.json"

CATEGORIES = {
    "production": {
        "cn_name": "药品生产数据",
        "urls": [
            "http://www.cpia.org.cn/index/statistics/production.html",
            "http://www.cpia.org.cn/index/statistics/output.html",
            "http://www.cpia.org.cn/index/dataservice/pharmdata.html",
        ],
    },
    "sales": {
        "cn_name": "药品销售数据",
        "urls": [
            "http://www.cpia.org.cn/index/statistics/sales.html",
            "http://www.cpia.org.cn/index/statistics/market.html",
        ],
    },
    "trade": {
        "cn_name": "进出口贸易数据",
        "urls": [
            "http://www.cpia.org.cn/index/statistics/trade.html",
            "http://www.cpia.org.cn/index/statistics/import_export.html",
        ],
    },
    "news": {
        "cn_name": "行业新闻",
        "urls": [
            "http://www.cpia.org.cn/index/news/industry.html",
            "http://www.cpia.org.cn/index/news/market.html",
            "http://www.cpia.org.cn/index/news/policy.html",
        ],
    },
}

PRODUCTS = {
    "chemical_drugs": {"cn_name": "化学药品", "unit": "亿元"},
    "chinese_patent_medicine": {"cn_name": "中成药", "unit": "亿元"},
    "biological_products": {"cn_name": "生物制品", "unit": "亿元"},
    "api": {"cn_name": "原料药", "unit": "万吨"},
    "antibiotics": {"cn_name": "抗生素", "unit": "万吨"},
    "vitamins": {"cn_name": "维生素", "unit": "万吨"},
    "medical_pieces": {"cn_name": "药用辅料", "unit": "亿元"},
    "injection": {"cn_name": "注射剂", "unit": "亿支"},
    "tablets": {"cn_name": "片剂", "unit": "亿片"},
    "capsules": {"cn_name": "胶囊剂", "unit": "亿粒"},
    "granules": {"cn_name": "颗粒剂", "unit": "万吨"},
}


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS drug_production (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            product TEXT NOT NULL,
            product_cn TEXT,
            production REAL,
            unit TEXT,
            yoy_change REAL,
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, product, region, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS drug_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            product TEXT NOT NULL,
            product_cn TEXT,
            sales_value REAL,
            sales_volume REAL,
            unit TEXT,
            yoy_change REAL,
            channel TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, product, channel, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS drug_trade (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            product TEXT NOT NULL,
            product_cn TEXT,
            import_value REAL,
            export_value REAL,
            import_volume REAL,
            export_volume REAL,
            trade_balance REAL,
            currency TEXT DEFAULT 'USD',
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, product, source_url)
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


def extract_production_tables(html: str, url: str, category: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "产量", "生产", "万吨", "亿元", "同比", "增长",
            "化学药", "中成药", "生物制品", "原料药", "抗生素",
            "output", "production", "pharmaceutical",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            product_key = identify_product(" ".join(cells))
            numeric_values = extract_numeric_values(cells)

            item = {
                "date": extract_date(cells[0]),
                "product": product_key,
                "product_cn": PRODUCTS.get(product_key, {}).get("cn_name", ""),
                "unit": PRODUCTS.get(product_key, {}).get("unit", detect_unit(" ".join(cells))),
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


def extract_sales_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "销售", "销售额", "市场", "金额", "销量",
            "sales", "market", "revenue",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            product_key = identify_product(" ".join(cells))
            numeric_values = extract_sales_values(cells)

            item = {
                "date": extract_date(cells[0]),
                "product": product_key,
                "product_cn": PRODUCTS.get(product_key, {}).get("cn_name", ""),
                "unit": PRODUCTS.get(product_key, {}).get("unit", detect_unit(" ".join(cells))),
                "channel": detect_channel(" ".join(cells)),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(numeric_values)

            if item.get("sales_value") or item.get("sales_volume"):
                items.append(item)

    return items


def extract_trade_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "进口", "出口", "贸易", "进出口", "金额", "数量",
            "import", "export", "trade",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            product_key = identify_product(" ".join(cells))
            numeric_values = extract_trade_values(cells)

            item = {
                "date": extract_date(cells[0]),
                "product": product_key,
                "product_cn": PRODUCTS.get(product_key, {}).get("cn_name", ""),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(numeric_values)

            if item.get("import_value") or item.get("export_value"):
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
            href = "http://www.cpia.org.cn" + href

        date_text = ""
        date_elem = article.css("span, .date, time")
        if date_elem:
            date_text = date_elem[0].text.strip()
        if not date_text:
            date_text = extract_date(title)

        category = "news"
        if "market" in url.lower() or "市场" in title:
            category = "market"
        elif "policy" in url.lower() or "政策" in title:
            category = "policy"

        items.append({
            "date": date_text,
            "title": title,
            "content": "",
            "category": category,
            "source_url": href or url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def identify_product(text: str) -> str:
    for key, info in PRODUCTS.items():
        if key in text.lower() or info["cn_name"] in text:
            return key
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

        if "增长" in cell or "同比" in cell or "增速" in cell:
            result["yoy"] = value
        elif "primary" not in result:
            result["primary"] = value

    return result


def extract_sales_values(cells: list[str]) -> dict:
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

        if "销售额" in cell or "销售金额" in cell:
            result["sales_value"] = value
        elif "销量" in cell or "销售量" in cell:
            result["sales_volume"] = value
        elif "增长" in cell or "同比" in cell:
            result["yoy_change"] = value
        elif i >= 1 and "sales_value" not in result:
            result["sales_value"] = value
        elif i >= 2 and "sales_volume" not in result:
            result["sales_volume"] = value

    return result


def extract_trade_values(cells: list[str]) -> dict:
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

        if "进口额" in cell or "进口金额" in cell:
            result["import_value"] = value
        elif "出口额" in cell or "出口金额" in cell:
            result["export_value"] = value
        elif "进口量" in cell:
            result["import_volume"] = value
        elif "出口量" in cell:
            result["export_volume"] = value
        elif "差额" in cell or "平衡" in cell:
            result["trade_balance"] = value
        elif i >= 1 and "import_value" not in result:
            result["import_value"] = value
        elif i >= 2 and "export_value" not in result:
            result["export_value"] = value

    if "import_value" in result and "export_value" in result:
        result["trade_balance"] = result["export_value"] - result["import_value"]

    return result


def detect_unit(text: str) -> str:
    if "万吨" in text:
        return "万吨"
    if "亿元" in text:
        return "亿元"
    if "亿美元" in text:
        return "亿美元"
    if "亿片" in text:
        return "亿片"
    if "亿支" in text:
        return "亿支"
    if "亿粒" in text:
        return "亿粒"
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


def detect_channel(text: str) -> str:
    if "医院" in text:
        return "hospital"
    if "零售" in text or "药店" in text:
        return "retail"
    if "电商" in text or "网上" in text:
        return "online"
    if "基层" in text:
        return "primary_care"
    return "all"


def save_to_sqlite(items: list[dict], conn: sqlite3.Connection, table: str) -> int:
    inserted = 0
    for item in items:
        try:
            if table == "drug_production":
                conn.execute(
                    """INSERT OR REPLACE INTO drug_production
                    (date, product, product_cn, production, unit, yoy_change, region, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("product"), item.get("product_cn"),
                     item.get("production"), item.get("unit"), item.get("yoy_change"),
                     item.get("region"), item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "drug_sales":
                conn.execute(
                    """INSERT OR REPLACE INTO drug_sales
                    (date, product, product_cn, sales_value, sales_volume, unit, yoy_change, channel, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("product"), item.get("product_cn"),
                     item.get("sales_value"), item.get("sales_volume"), item.get("unit"),
                     item.get("yoy_change"), item.get("channel"),
                     item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "drug_trade":
                conn.execute(
                    """INSERT OR REPLACE INTO drug_trade
                    (date, product, product_cn, import_value, export_value, import_volume,
                     export_volume, trade_balance, currency, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("product"), item.get("product_cn"),
                     item.get("import_value"), item.get("export_value"),
                     item.get("import_volume"), item.get("export_volume"),
                     item.get("trade_balance"), item.get("currency", "USD"),
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


def get_cpia_data(
    categories: list[str] | None = None,
    include_news: bool = True,
) -> dict:
    """Fetch pharmaceutical industry data from CPIA.

    Args:
        categories: List of category keys (default: all).
            Available: production, sales, trade.
        include_news: Whether to fetch industry news.

    Returns:
        Dict with keys: 'production', 'sales', 'trade', 'news'.
    """
    if categories is None:
        categories = list(CATEGORIES.keys())

    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "production": [],
        "sales": [],
        "trade": [],
        "news": [],
    }

    logger.info("Starting CPIA spider for categories: %s", ", ".join(categories))

    for cat in categories:
        if cat == "news":
            continue

        cat_info = CATEGORIES.get(cat, {})
        for url in cat_info.get("urls", []):
            page = fetch_page(url, fetcher)
            if page:
                if cat == "sales":
                    items = extract_sales_data(page["html"], url)
                    if items:
                        result["sales"].extend(items)
                        save_to_sqlite(items, conn, "drug_sales")
                        logger.info("  Extracted %d sales records", len(items))
                elif cat == "trade":
                    items = extract_trade_data(page["html"], url)
                    if items:
                        result["trade"].extend(items)
                        save_to_sqlite(items, conn, "drug_trade")
                        logger.info("  Extracted %d trade records", len(items))
                else:
                    items = extract_production_tables(page["html"], url, cat)
                    if items:
                        result[cat].extend(items)
                        save_to_sqlite(items, conn, "drug_production")
                        logger.info("  Extracted %d records for %s", len(items), cat)
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

    for key in ["production", "sales", "trade", "news"]:
        if result[key]:
            json_path = {
                "production": JSON_PRODUCTION_PATH,
                "sales": JSON_SALES_PATH,
                "trade": JSON_TRADE_PATH,
                "news": JSON_NEWS_PATH,
            }[key]
            save_to_json(result[key], json_path)
            logger.info("Saved %d %s records to %s", len(result[key]), key, json_path)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_cpia_data(include_news=True)
    print(f"\n{'=' * 70}")
    print(f"Total production records: {len(results['production'])}")
    print(f"Total sales records: {len(results['sales'])}")
    print(f"Total trade records: {len(results['trade'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"{'=' * 70}")
