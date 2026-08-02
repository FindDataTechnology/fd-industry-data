#!/usr/bin/env python3
"""
Baiinfo (百川盈孚) Spider
https://www.baiinfo.com/

Data Coverage:
  - Chemical product prices (daily/weekly/monthly)
  - Market analysis and trends
  - Supply chain data (supply/demand/inventory)
  - Industry news and events

Authentication:
  - Public pages: no login required
  - Detailed price data: requires paid subscription
  - Some summary data available on public pages

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
logger = logging.getLogger("baiinfo")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "baiinfo.db"

PRODUCT_CATEGORIES = {
    "organic": {
        "cn_name": "有机化工",
        "products": ["甲醇", "乙二醇", "苯乙烯", "丙烯腈", "PTA", "PVC", "纯苯", "环氧丙烷"],
    },
    "inorganic": {
        "cn_name": "无机化工",
        "products": ["烧碱", "纯碱", "硫酸", "盐酸", "液氯", "钛白粉"],
    },
    "polymers": {
        "cn_name": "合成材料",
        "products": ["聚乙烯", "聚丙烯", "聚苯乙烯", "ABS", "聚碳酸酯", "聚甲醛"],
    },
    "coatings": {
        "cn_name": "涂料原料",
        "products": ["环氧树脂", "不饱和树脂", "丙烯酸", "醇酸树脂"],
    },
    "specialty": {
        "cn_name": "特种化工",
        "products": ["有机硅", "环氧氯丙烷", "BDO", "DMF", "TDI", "MDI"],
    },
}

TARGET_URLS = {
    "homepage": "https://www.baiinfo.com/",
    "prices": [
        "https://www.baiinfo.com/price/organic/",
        "https://www.baiinfo.com/price/inorganic/",
        "https://www.baiinfo.com/price/polymers/",
        "https://www.baiinfo.com/price/coatings/",
        "https://www.baiinfo.com/price/specialty/",
    ],
    "market": [
        "https://www.baiinfo.com/market/analysis/",
        "https://www.baiinfo.com/market/supply-demand/",
        "https://www.baiinfo.com/market/supply-chain/",
    ],
    "news": [
        "https://www.baiinfo.com/news/industry/",
        "https://www.baiinfo.com/news/company/",
        "https://www.baiinfo.com/news/policy/",
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
            market_trend TEXT,
            analysis_text TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, product, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS industry_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            title TEXT NOT NULL,
            category TEXT,
            content TEXT,
            related_products TEXT,
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

                if any(kw in cell for kw in ["华东", "华南", "华北", "山东", "江苏", "浙江", "广东"]):
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
            for kw in ["供需", "库存", "开工率", "产能", "supply", "demand", "inventory"]
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
                    elif "供应" in header_text or "supply" in header_text.lower():
                        item["supply_index"] = num_val
                    elif "需求" in header_text or "demand" in header_text.lower():
                        item["demand_index"] = num_val

                if any(kw in cell for kw in ["趋势", "涨", "跌", "上行", "下行", "震荡"]):
                    item["market_trend"] = cell

            if "date" in item:
                items.append(item)

    for article in sel.css(".analysis-item, .market-analysis, .trend-article"):
        text_elem = article.css("p, .content, .text")
        if text_elem:
            analysis_text = text_elem[0].text.strip()
            title_elem = article.css("h1, h2, h3, .title")
            title = title_elem[0].text.strip() if title_elem else ""
            product_cn, product_name, category = identify_product(title + " " + analysis_text)

            date_elem = article.css("time, .date")
            date = date_elem[0].text.strip() if date_elem else datetime.now().strftime("%Y-%m-%d")

            items.append({
                "date": extract_date(date),
                "product": product_name,
                "product_cn": product_cn,
                "category": category,
                "analysis_text": analysis_text[:500],
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            })

    return items


def extract_news(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for article in sel.css("article, .news-item, .article-item, .post"):
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

        related_products = []
        for cat_key, cat_info in PRODUCT_CATEGORIES.items():
            for product in cat_info["products"]:
                if product in title or product in content:
                    related_products.append(product)

        category = "news"
        if "policy" in url.lower() or "政策" in title:
            category = "policy"
        elif "company" in url.lower() or "企业" in title:
            category = "company"
        elif "industry" in url.lower() or "行业" in title:
            category = "industry"

        items.append({
            "date": date,
            "title": title,
            "category": category,
            "content": content[:500],
            "related_products": ",".join(related_products),
            "source_url": url,
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
                 market_trend, analysis_text, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    item.get("market_trend"),
                    item.get("analysis_text"),
                    item.get("source_url"),
                    item.get("scraped_at"),
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
                INSERT OR REPLACE INTO industry_news
                (date, title, category, content, related_products, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"),
                    item.get("title"),
                    item.get("category"),
                    item.get("content"),
                    item.get("related_products"),
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


def get_baiinfo_data(
    include_market: bool = True,
    include_news: bool = True,
) -> dict:
    """Fetch data from Baiinfo (百川盈孚).

    Args:
        include_market: Whether to fetch market analysis data.
        include_news: Whether to fetch industry news.

    Returns:
        Dict with keys: prices, market, news.
    """
    conn = init_db()
    result = {
        "prices": [],
        "market": [],
        "news": [],
    }

    logger.info("Starting Baiinfo data fetch")

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

    if include_news:
        for url in TARGET_URLS["news"]:
            page = fetch_page(url)
            if page:
                news_items = extract_news(page["html"], url)
                if news_items:
                    result["news"].extend(news_items)
                    logger.info("  Extracted %d news articles from %s", len(news_items), url)
            time.sleep(2)

    if result["prices"]:
        n = save_prices_to_sqlite(result["prices"], conn)
        save_to_json(result["prices"], OUTPUT_DIR / "baiinfo_prices.json")
        logger.info("Saved %d price records (%d to SQLite)", len(result["prices"]), n)

    if result["market"]:
        n = save_market_to_sqlite(result["market"], conn)
        save_to_json(result["market"], OUTPUT_DIR / "baiinfo_market.json")
        logger.info("Saved %d market records (%d to SQLite)", len(result["market"]), n)

    if result["news"]:
        n = save_news_to_sqlite(result["news"], conn)
        save_to_json(result["news"], OUTPUT_DIR / "baiinfo_news.json")
        logger.info("Saved %d news articles (%d to SQLite)", len(result["news"]), n)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_baiinfo_data(
        include_market=True,
        include_news=True,
    )

    print(f"\n{'=' * 70}")
    print(f"Baiinfo Data Fetch Complete")
    print(f"{'=' * 70}")
    print(f"Price records: {len(results['prices'])}")
    print(f"Market records: {len(results['market'])}")
    print(f"News articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON files in: {OUTPUT_DIR}")
    print(f"{'=' * 70}")
