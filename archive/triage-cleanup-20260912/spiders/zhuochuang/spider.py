#!/usr/bin/env python3
"""
Zhuochuang Information (卓创资讯) Spider
https://www.sci99.com/

Data Coverage:
  - Chemical product prices (energy, organic, inorganic, polymers)
  - Market supply/demand data
  - Industry analysis reports
  - Production capacity data

Authentication:
  - Public pages: no login required
  - Detailed price data: requires paid subscription
  - Some endpoints return JSON via XHR

Architecture:
  - Primary: Scrapling Fetcher with Chrome impersonation
  - Anti-bot: Browser impersonation, stealthy headers, rate limiting
  - Output: SQLite DB + JSON export
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
logger = logging.getLogger("zhuochuang")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "zhuochuang.db"

PRODUCT_CATEGORIES = {
    "energy": {
        "cn_name": "能源",
        "products": ["原油", "天然气", "LNG", "成品油", "燃料油", "石油焦"],
    },
    "organic": {
        "cn_name": "有机化工",
        "products": ["甲醇", "乙二醇", "苯乙烯", "丙烯腈", "PTA", "PVC", "纯苯"],
    },
    "inorganic": {
        "cn_name": "无机化工",
        "products": ["烧碱", "纯碱", "硫酸", "盐酸", "液氯"],
    },
    "polymers": {
        "cn_name": "合成材料",
        "products": ["聚乙烯", "聚丙烯", "聚苯乙烯", "ABS", "EVA"],
    },
    "rubber": {
        "cn_name": "橡胶",
        "products": ["天然橡胶", "丁苯橡胶", "顺丁橡胶", "丁基橡胶"],
    },
}

TARGET_URLS = {
    "homepage": "https://www.sci99.com/",
    "prices": [
        "https://www.sci99.com/price/energy/",
        "https://www.sci99.com/price/organic/",
        "https://www.sci99.com/price/inorganic/",
        "https://www.sci99.com/price/polymers/",
        "https://www.sci99.com/price/rubber/",
    ],
    "market": [
        "https://www.sci99.com/market/analysis/",
        "https://www.sci99.com/market/supply-demand/",
        "https://www.sci99.com/market/capacity/",
    ],
    "index": [
        "https://www.sci99.com/index/price/",
        "https://www.sci99.com/index/commodity/",
    ],
    "reports": [
        "https://www.sci99.com/report/daily/",
        "https://www.sci99.com/report/weekly/",
        "https://www.sci99.com/report/monthly/",
    ],
}


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            product TEXT NOT NULL,
            product_cn TEXT,
            category TEXT,
            price REAL,
            price_unit TEXT DEFAULT '元/吨',
            currency TEXT DEFAULT 'CNY',
            price_change REAL,
            price_change_pct REAL,
            price_high REAL,
            price_low REAL,
            region TEXT DEFAULT 'China',
            market_location TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, product, region, market_location, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            product TEXT,
            product_cn TEXT,
            category TEXT,
            supply_index REAL,
            demand_index REAL,
            inventory REAL,
            inventory_unit TEXT DEFAULT '万吨',
            operating_rate REAL,
            operating_rate_unit TEXT DEFAULT '%',
            capacity REAL,
            capacity_unit TEXT DEFAULT '万吨/年',
            market_trend TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, product, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            index_name TEXT NOT NULL,
            value REAL,
            change REAL,
            change_pct REAL,
            change_direction TEXT,
            category TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, index_name, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS industry_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            title TEXT NOT NULL,
            category TEXT,
            product TEXT,
            summary TEXT,
            source_url TEXT UNIQUE,
            scraped_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def fetch_page(url: str) -> dict | None:
    try:
        logger.info("Fetching: %s", url)
        fetcher = Fetcher(auto_match=False, impersonate="chrome")
        response = fetcher.get(url, timeout=30, stealthy_headers=True)
        if response.status != 200:
            logger.warning("HTTP %d for %s", response.status, url)
            return None
        return {"url": url, "html": response.html, "status": response.status}
    except Exception as e:
        logger.error("Fetch failed for %s: %s", url, e)
        return None


def extract_date(text: str) -> str:
    patterns = [
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{4}年\d{1,2}月\d{1,2}日)",
        r"(\d{4}年\d{1,2}月)",
        r"(\d{4}/\d{2}/\d{2})",
        r"(\d{4}-\d{2})",
        r"(\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return datetime.now().strftime("%Y-%m-%d")


def extract_number(text: str) -> float | None:
    if not text:
        return None
    text = text.replace(",", "").replace("，", "").replace(" ", "")
    match = re.search(r"[-+]?\d+\.?\d*", text)
    if match:
        try:
            return float(match.group())
        except ValueError:
            return None
    return None


def identify_product(text: str) -> tuple[str, str, str]:
    for cat_key, cat_info in PRODUCT_CATEGORIES.items():
        for product in cat_info["products"]:
            if product in text:
                return product, product, cat_key
    return "other", text[:20], "other"


def extract_price_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        is_price = any(kw in header_text for kw in ["价格", "报价", "均价", "price", "元/吨", "市场价"])
        if not is_price:
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            row_text = " ".join(cells)
            product_cn, product_name, category = identify_product(row_text)

            item = {
                "product": product_name,
                "product_cn": product_cn,
                "category": category,
                "price_unit": "元/吨",
                "currency": "CNY",
                "region": "China",
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            for cell in cells:
                date_val = extract_date(cell)
                if date_val and "date" not in item:
                    item["date"] = date_val

                num_val = extract_number(cell)
                if num_val is not None:
                    if num_val > 100 and "price" not in item:
                        item["price"] = num_val
                    elif num_val > 100 and "price" in item and "price_high" not in item:
                        item["price_high"] = num_val
                    elif num_val > 100 and "price_high" in item and "price_low" not in item:
                        item["price_low"] = num_val
                    elif "涨跌" in cell or "变化" in cell:
                        if "%" in cell:
                            item["price_change_pct"] = num_val
                        else:
                            item["price_change"] = num_val

                if any(kw in cell for kw in ["华东", "华南", "华北", "东北", "西南", "西北", "山东", "江苏", "浙江", "广东"]):
                    item["market_location"] = cell

            if "date" in item and "price" in item:
                items.append(item)

    for article in sel.css("article, .price-item, .product-item, .commodity-item"):
        title_elem = article.css("h1, h2, h3, .title, .product-name, a")
        if not title_elem:
            continue

        title = title_elem[0].text.strip()
        if not title:
            continue

        product_cn, product_name, category = identify_product(title)

        price_elem = article.css(".price, .quote, .value, .current-price")
        if not price_elem:
            continue

        price_text = price_elem[0].text.strip()
        price_val = extract_number(price_text)
        if price_val is None:
            continue

        date_elem = article.css("time, .date, .update-time, .time")
        date = date_elem[0].text.strip() if date_elem else datetime.now().strftime("%Y-%m-%d")
        date = extract_date(date)

        items.append({
            "date": date,
            "product": product_name,
            "product_cn": product_cn,
            "category": category,
            "price": price_val,
            "price_unit": "元/吨",
            "currency": "CNY",
            "region": "China",
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def extract_market_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        is_market = any(
            kw in header_text
            for kw in ["供需", "库存", "开工率", "产能", "supply", "demand", "inventory", "产量"]
        )
        if not is_market:
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            row_text = " ".join(cells)
            product_cn, product_name, category = identify_product(row_text)

            item = {
                "product": product_name,
                "product_cn": product_cn,
                "category": category,
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            for cell in cells:
                date_val = extract_date(cell)
                if date_val and "date" not in item:
                    item["date"] = date_val

                num_val = extract_number(cell)
                if num_val is not None:
                    if "库存" in header_text or "inventory" in header_text.lower():
                        item["inventory"] = num_val
                        item["inventory_unit"] = "万吨"
                    elif "开工率" in header_text or "产能利用率" in header_text:
                        item["operating_rate"] = num_val
                        item["operating_rate_unit"] = "%"
                    elif "产能" in header_text and "利用率" not in header_text:
                        item["capacity"] = num_val
                        item["capacity_unit"] = "万吨/年"
                    elif "供需" in header_text:
                        if "供应" in header_text or "supply" in header_text.lower():
                            item["supply_index"] = num_val
                        else:
                            item["demand_index"] = num_val

                if any(kw in cell for kw in ["趋势", "涨", "跌", "上行", "下行", "震荡"]):
                    item["market_trend"] = cell

            if "date" in item:
                items.append(item)

    return items


def extract_index_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for card in sel.css(".index-card, .price-index-item, .index-product, .index-item"):
        name_elem = card.css(".comp-name, .product-name, .index-name, h3, h4")
        if not name_elem:
            continue

        name = name_elem[0].text.strip()
        if not name:
            continue

        value_elem = card.css(".value, .price-val, .index-val")
        if not value_elem:
            continue

        value = extract_number(value_elem[0].text.strip())
        if value is None:
            continue

        item = {
            "index_name": name,
            "value": value,
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }

        date_elem = card.css("time, .date, .update-time")
        if date_elem:
            item["date"] = extract_date(date_elem[0].text.strip())

        change_elem = card.css(".change, .diff, .change-val")
        if change_elem:
            change_text = change_elem[0].text.strip()
            change_val = extract_number(change_text)
            if change_val is not None:
                item["change"] = change_val
                item["change_direction"] = "up" if "+" in change_text else ("down" if "-" in change_text else "flat")

        pct_elem = card.css(".change-pct, .change-rate, .pct")
        if pct_elem:
            item["change_pct"] = extract_number(pct_elem[0].text.strip())

        if "date" not in item:
            item["date"] = datetime.now().strftime("%Y-%m-%d")

        items.append(item)

    return items


def extract_reports(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for article in sel.css("article, .report-item, .article-item, .post, .news-item"):
        title_elem = article.css("h1, h2, h3, .title, a")
        if not title_elem:
            continue

        title = title_elem[0].text.strip()
        if not title or len(title) < 5:
            continue

        content_elem = article.css("p, .content, .summary, .excerpt")
        content = content_elem[0].text.strip() if content_elem else ""

        date_elem = article.css("time, .date, .publish-date, .time")
        date = date_elem[0].text.strip() if date_elem else ""
        date = extract_date(date)

        product_cn, product_name, _ = identify_product(title)

        link_elem = article.css("a")
        source_url = url
        if link_elem:
            href = link_elem[0].attrib.get("href", "")
            if href and href.startswith("http"):
                source_url = href
            elif href and href.startswith("/"):
                source_url = f"https://www.sci99.com{href}"

        category = "report"
        if "daily" in url.lower() or "日报" in title:
            category = "daily_report"
        elif "weekly" in url.lower() or "周报" in title:
            category = "weekly_report"
        elif "monthly" in url.lower() or "月报" in title:
            category = "monthly_report"

        items.append({
            "date": date,
            "title": title,
            "category": category,
            "product": product_name,
            "summary": content[:500],
            "source_url": source_url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def save_prices_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO price_data
                (date, product, product_cn, category, price, price_unit, currency,
                 price_change, price_change_pct, price_high, price_low,
                 region, market_location, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"),
                    item.get("product"),
                    item.get("product_cn"),
                    item.get("category"),
                    item.get("price"),
                    item.get("price_unit", "元/吨"),
                    item.get("currency", "CNY"),
                    item.get("price_change"),
                    item.get("price_change_pct"),
                    item.get("price_high"),
                    item.get("price_low"),
                    item.get("region", "China"),
                    item.get("market_location"),
                    item.get("source_url"),
                    item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_market_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO market_analysis
                (date, product, product_cn, category, supply_index, demand_index,
                 inventory, inventory_unit, operating_rate, operating_rate_unit,
                 capacity, capacity_unit, market_trend, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"),
                    item.get("product"),
                    item.get("product_cn"),
                    item.get("category"),
                    item.get("supply_index"),
                    item.get("demand_index"),
                    item.get("inventory"),
                    item.get("inventory_unit", "万吨"),
                    item.get("operating_rate"),
                    item.get("operating_rate_unit", "%"),
                    item.get("capacity"),
                    item.get("capacity_unit", "万吨/年"),
                    item.get("market_trend"),
                    item.get("source_url"),
                    item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_index_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO price_index
                (date, index_name, value, change, change_pct, change_direction,
                 category, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"),
                    item.get("index_name"),
                    item.get("value"),
                    item.get("change"),
                    item.get("change_pct"),
                    item.get("change_direction"),
                    item.get("category"),
                    item.get("source_url"),
                    item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_reports_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO industry_reports
                (date, title, category, product, summary, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"),
                    item.get("title"),
                    item.get("category"),
                    item.get("product"),
                    item.get("summary"),
                    item.get("source_url"),
                    item.get("scraped_at"),
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


def get_zhuochuang_data(
    include_market: bool = True,
    include_index: bool = True,
    include_reports: bool = True,
) -> dict:
    """Fetch data from Zhuochuang Information (卓创资讯).

    Args:
        include_market: Whether to fetch market analysis data.
        include_index: Whether to fetch price index data.
        include_reports: Whether to fetch industry reports.

    Returns:
        Dict with keys: prices, market, index, reports.
    """
    conn = init_db()
    result = {
        "prices": [],
        "market": [],
        "index": [],
        "reports": [],
    }

    logger.info("Starting Zhuochuang data fetch")

    for url in TARGET_URLS["prices"]:
        page = fetch_page(url)
        if page:
            price_items = extract_price_data(page["html"], url)
            if price_items:
                result["prices"].extend(price_items)
                logger.info("  Extracted %d price records from %s", len(price_items), url)
        time.sleep(2)

    if include_market:
        for url in TARGET_URLS["market"]:
            page = fetch_page(url)
            if page:
                market_items = extract_market_data(page["html"], url)
                if market_items:
                    result["market"].extend(market_items)
                    logger.info("  Extracted %d market records from %s", len(market_items), url)
            time.sleep(2)

    if include_index:
        for url in TARGET_URLS["index"]:
            page = fetch_page(url)
            if page:
                index_items = extract_index_data(page["html"], url)
                if index_items:
                    result["index"].extend(index_items)
                    logger.info("  Extracted %d index records from %s", len(index_items), url)
            time.sleep(2)

    if include_reports:
        for url in TARGET_URLS["reports"]:
            page = fetch_page(url)
            if page:
                report_items = extract_reports(page["html"], url)
                if report_items:
                    result["reports"].extend(report_items)
                    logger.info("  Extracted %d reports from %s", len(report_items), url)
            time.sleep(2)

    if result["prices"]:
        n = save_prices_to_sqlite(result["prices"], conn)
        save_to_json(result["prices"], OUTPUT_DIR / "zhuochuang_prices.json")
        logger.info("Saved %d price records (%d to SQLite)", len(result["prices"]), n)

    if result["market"]:
        n = save_market_to_sqlite(result["market"], conn)
        save_to_json(result["market"], OUTPUT_DIR / "zhuochuang_market.json")
        logger.info("Saved %d market records (%d to SQLite)", len(result["market"]), n)

    if result["index"]:
        n = save_index_to_sqlite(result["index"], conn)
        save_to_json(result["index"], OUTPUT_DIR / "zhuochuang_index.json")
        logger.info("Saved %d index records (%d to SQLite)", len(result["index"]), n)

    if result["reports"]:
        n = save_reports_to_sqlite(result["reports"], conn)
        save_to_json(result["reports"], OUTPUT_DIR / "zhuochuang_reports.json")
        logger.info("Saved %d reports (%d to SQLite)", len(result["reports"]), n)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_zhuochuang_data(
        include_market=True,
        include_index=True,
        include_reports=True,
    )

    print(f"\n{'=' * 70}")
    print(f"Zhuochuang Data Fetch Complete")
    print(f"{'=' * 70}")
    print(f"Price records: {len(results['prices'])}")
    print(f"Market records: {len(results['market'])}")
    print(f"Price indices: {len(results['index'])}")
    print(f"Reports: {len(results['reports'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON files in: {OUTPUT_DIR}")
    print(f"{'=' * 70}")
