#!/usr/bin/env python3
"""
CPCIA Spider - 中国石油和化学工业联合会数据爬虫

Target: http://www.cpcia.org.cn/
Data: Oil and gas production, refinery statistics, chemical industry data, trade data

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts petrochemical production and trade statistics
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
logger = logging.getLogger("cpcia")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "cpcia.db"
JSON_OILGAS_PATH = OUTPUT_DIR / "cpcia_oilgas.json"
JSON_CHEMICAL_PATH = OUTPUT_DIR / "cpcia_chemical.json"
JSON_TRADE_PATH = OUTPUT_DIR / "cpcia_trade.json"
JSON_NEWS_PATH = OUTPUT_DIR / "cpcia_news.json"

CATEGORIES = {
    "oilgas": {
        "cn_name": "油气生产数据",
        "urls": [
            "http://www.cpcia.org.cn/index/statistics/oil.html",
            "http://www.cpcia.org.cn/index/statistics/gas.html",
            "http://www.cpcia.org.cn/index/dataservice/petrodata.html",
        ],
    },
    "chemical": {
        "cn_name": "化工行业数据",
        "urls": [
            "http://www.cpcia.org.cn/index/statistics/chemical.html",
            "http://www.cpcia.org.cn/index/statistics/fertilizer.html",
            "http://www.cpcia.org.cn/index/statistics/coating.html",
        ],
    },
    "trade": {
        "cn_name": "进出口贸易数据",
        "urls": [
            "http://www.cpcia.org.cn/index/statistics/trade.html",
            "http://www.cpcia.org.cn/index/statistics/import_export.html",
        ],
    },
    "news": {
        "cn_name": "行业新闻",
        "urls": [
            "http://www.cpcia.org.cn/index/news/industry.html",
            "http://www.cpcia.org.cn/index/news/market.html",
            "http://www.cpcia.org.cn/index/news/policy.html",
        ],
    },
}

PRODUCTS = {
    "crude_oil": {"cn_name": "原油", "unit": "万吨"},
    "natural_gas": {"cn_name": "天然气", "unit": "亿立方米"},
    "gasoline": {"cn_name": "汽油", "unit": "万吨"},
    "diesel": {"cn_name": "柴油", "unit": "万吨"},
    "kerosene": {"cn_name": "煤油", "unit": "万吨"},
    "ethylene": {"cn_name": "乙烯", "unit": "万吨"},
    "methanol": {"cn_name": "甲醇", "unit": "万吨"},
    "synthetic_ammonia": {"cn_name": "合成氨", "unit": "万吨"},
    "chemical_fiber": {"cn_name": "化学纤维", "unit": "万吨"},
    "plastics": {"cn_name": "塑料制品", "unit": "万吨"},
    "rubber": {"cn_name": "橡胶制品", "unit": "万吨"},
    "paint": {"cn_name": "涂料", "unit": "万吨"},
    "fertilizer": {"cn_name": "化肥", "unit": "万吨"},
}


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oilgas_stats (
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
        CREATE TABLE IF NOT EXISTS chemical_stats (
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
        CREATE TABLE IF NOT EXISTS trade_stats (
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
            "产量", "生产", "万吨", "亿立方米", "同比", "增长", "原油", "天然气",
            "化工", "乙烯", "甲醇", "化肥", "产量", "output", "production",
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
            href = "http://www.cpcia.org.cn" + href

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
    if "亿立方米" in text:
        return "亿立方米"
    if "亿美元" in text:
        return "亿美元"
    if "亿元" in text:
        return "亿元"
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
            if table == "oilgas_stats":
                conn.execute(
                    """INSERT OR REPLACE INTO oilgas_stats
                    (date, product, product_cn, production, unit, yoy_change, region, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("product"), item.get("product_cn"),
                     item.get("production"), item.get("unit"), item.get("yoy_change"),
                     item.get("region"), item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "chemical_stats":
                conn.execute(
                    """INSERT OR REPLACE INTO chemical_stats
                    (date, product, product_cn, production, unit, yoy_change, region, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("product"), item.get("product_cn"),
                     item.get("production"), item.get("unit"), item.get("yoy_change"),
                     item.get("region"), item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "trade_stats":
                conn.execute(
                    """INSERT OR REPLACE INTO trade_stats
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


def get_cpcia_data(
    categories: list[str] | None = None,
    include_news: bool = True,
) -> dict:
    """Fetch petrochemical industry data from CPCIA.

    Args:
        categories: List of category keys (default: all).
            Available: oilgas, chemical, trade.
        include_news: Whether to fetch industry news.

    Returns:
        Dict with keys: 'oilgas', 'chemical', 'trade', 'news'.
    """
    if categories is None:
        categories = list(CATEGORIES.keys())

    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "oilgas": [],
        "chemical": [],
        "trade": [],
        "news": [],
    }

    logger.info("Starting CPCIA spider for categories: %s", ", ".join(categories))

    for cat in categories:
        if cat == "news":
            continue

        cat_info = CATEGORIES.get(cat, {})
        for url in cat_info.get("urls", []):
            page = fetch_page(url, fetcher)
            if page:
                if cat == "trade":
                    items = extract_trade_data(page["html"], url)
                    if items:
                        result["trade"].extend(items)
                        save_to_sqlite(items, conn, "trade_stats")
                        logger.info("  Extracted %d trade records", len(items))
                else:
                    items = extract_production_tables(page["html"], url, cat)
                    if items:
                        result[cat].extend(items)
                        save_to_sqlite(items, conn, f"{cat}_stats")
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

    for key in ["oilgas", "chemical", "trade", "news"]:
        if result[key]:
            json_path = {
                "oilgas": JSON_OILGAS_PATH,
                "chemical": JSON_CHEMICAL_PATH,
                "trade": JSON_TRADE_PATH,
                "news": JSON_NEWS_PATH,
            }[key]
            save_to_json(result[key], json_path)
            logger.info("Saved %d %s records to %s", len(result[key]), key, json_path)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_cpcia_data(include_news=True)
    print(f"\n{'=' * 70}")
    print(f"Total oil/gas records: {len(results['oilgas'])}")
    print(f"Total chemical records: {len(results['chemical'])}")
    print(f"Total trade records: {len(results['trade'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"{'=' * 70}")
