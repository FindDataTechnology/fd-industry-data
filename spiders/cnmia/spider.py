#!/usr/bin/env python3
"""
China Nonferrous Metals Industry Association (中国有色金属工业协会) Spider
https://www.chinametal.org.cn/

Data Coverage:
  - Non-ferrous metal statistics (copper, aluminum, zinc, lead, nickel, tin)
  - Production data (monthly/annual)
  - Import/export data
  - Market analysis and reports
  - Price indices

Authentication:
  - Public data: no login required
  - Statistical reports freely available
  - Some detailed data may require membership

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
logger = logging.getLogger("cnmia")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "cnmia.db"

METAL_CATEGORIES = {
    "copper": {
        "cn_name": "铜",
        "products": ["电解铜", "铜精矿", "铜材", "废铜"],
    },
    "aluminum": {
        "cn_name": "铝",
        "products": ["电解铝", "氧化铝", "铝锭", "铝合金", "废铝"],
    },
    "zinc": {
        "cn_name": "锌",
        "products": ["锌锭", "锌精矿", "氧化锌"],
    },
    "lead": {
        "cn_name": "铅",
        "products": ["铅锭", "铅精矿"],
    },
    "nickel": {
        "cn_name": "镍",
        "products": ["电解镍", "镍矿", "镍铁"],
    },
    "tin": {
        "cn_name": "锡",
        "products": ["锡锭", "锡精矿"],
    },
    "rare_earth": {
        "cn_name": "稀土",
        "products": ["氧化镨钕", "氧化镝", "氧化铽", "稀土永磁"],
    },
    "precious": {
        "cn_name": "贵金属",
        "products": ["黄金", "白银", "铂金", "钯金"],
    },
}

TARGET_URLS = {
    "homepage": "https://www.chinametal.org.cn/",
    "statistics": [
        "https://www.chinametal.org.cn/data/production/",
        "https://www.chinametal.org.cn/data/trade/",
        "https://www.chinametal.org.cn/data/inventory/",
    ],
    "prices": [
        "https://www.chinametal.org.cn/price/domestic/",
        "https://www.chinametal.org.cn/price/international/",
        "https://www.chinametal.org.cn/price/index/",
    ],
    "reports": [
        "https://www.chinametal.org.cn/report/monthly/",
        "https://www.chinametal.org.cn/report/annual/",
        "https://www.chinametal.org.cn/report/analysis/",
    ],
    "news": [
        "https://www.chinametal.org.cn/news/industry/",
        "https://www.chinametal.org.cn/news/policy/",
    ],
}


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS production_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            metal TEXT NOT NULL,
            metal_cn TEXT,
            category TEXT,
            production REAL,
            production_unit TEXT DEFAULT '万吨',
            yoy_change REAL,
            yoy_unit TEXT DEFAULT '%',
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, metal, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            metal TEXT NOT NULL,
            metal_cn TEXT,
            category TEXT,
            import_volume REAL,
            export_volume REAL,
            trade_unit TEXT DEFAULT '万吨',
            import_value REAL,
            export_value REAL,
            value_unit TEXT DEFAULT '亿美元',
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, metal, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            metal TEXT NOT NULL,
            metal_cn TEXT,
            category TEXT,
            price REAL,
            price_unit TEXT DEFAULT '元/吨',
            currency TEXT DEFAULT 'CNY',
            price_change REAL,
            price_change_pct REAL,
            market_type TEXT DEFAULT 'domestic',
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, metal, market_type, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS industry_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            title TEXT NOT NULL,
            category TEXT,
            metal TEXT,
            summary TEXT,
            source_url TEXT UNIQUE,
            scraped_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS industry_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            title TEXT NOT NULL,
            category TEXT,
            content TEXT,
            related_metals TEXT,
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


def identify_metal(text: str) -> tuple[str, str, str]:
    for cat_key, cat_info in METAL_CATEGORIES.items():
        for product in cat_info["products"]:
            if product in text:
                return product, product, cat_key
    for cat_key, cat_info in METAL_CATEGORIES.items():
        if cat_info["cn_name"] in text:
            return cat_info["cn_name"], cat_info["cn_name"], cat_key
    return "other", text[:20], "other"


def extract_production_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        is_production = any(
            kw in header_text
            for kw in ["产量", "产出", "production", "万吨", "同比", "增速"]
        )
        if not is_production:
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            row_text = " ".join(cells)
            metal_cn, metal_name, category = identify_metal(row_text)

            item = {
                "metal": metal_name,
                "metal_cn": metal_cn,
                "category": category,
                "production_unit": "万吨",
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            for cell in cells:
                date_val = extract_date(cell)
                if date_val and "date" not in item:
                    item["date"] = date_val

                num_val = extract_number(cell)
                if num_val is not None:
                    if "产量" in header_text or "production" in header_text.lower():
                        if "production" not in item:
                            item["production"] = num_val
                    elif "同比" in header_text or "增速" in header_text:
                        item["yoy_change"] = num_val
                        item["yoy_unit"] = "%"

            if "date" in item and "production" in item:
                items.append(item)

    return items


def extract_trade_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        is_trade = any(
            kw in header_text
            for kw in ["进口", "出口", "import", "export", "贸易"]
        )
        if not is_trade:
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            row_text = " ".join(cells)
            metal_cn, metal_name, category = identify_metal(row_text)

            item = {
                "metal": metal_name,
                "metal_cn": metal_cn,
                "category": category,
                "trade_unit": "万吨",
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            for cell in cells:
                date_val = extract_date(cell)
                if date_val and "date" not in item:
                    item["date"] = date_val

                num_val = extract_number(cell)
                if num_val is not None:
                    if "进口量" in header_text or "进口" in header_text:
                        if "进口" in cell or "import" in header_text.lower():
                            if "import_volume" not in item:
                                item["import_volume"] = num_val
                        elif "金额" in header_text or "value" in header_text.lower():
                            if "import_value" not in item:
                                item["import_value"] = num_val
                                item["value_unit"] = "亿美元"
                    elif "出口量" in header_text or "出口" in header_text:
                        if "出口" in cell or "export" in header_text.lower():
                            if "export_volume" not in item:
                                item["export_volume"] = num_val
                        elif "金额" in header_text or "value" in header_text.lower():
                            if "export_value" not in item:
                                item["export_value"] = num_val
                                item["value_unit"] = "亿美元"

            if "date" in item:
                items.append(item)

    return items


def extract_price_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    market_type = "domestic"
    if "international" in url:
        market_type = "international"
    elif "index" in url:
        market_type = "index"

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        is_price = any(kw in header_text for kw in ["价格", "报价", "均价", "price", "元/吨", "美元/吨"])
        if not is_price:
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            row_text = " ".join(cells)
            metal_cn, metal_name, category = identify_metal(row_text)

            item = {
                "metal": metal_name,
                "metal_cn": metal_cn,
                "category": category,
                "market_type": market_type,
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            if market_type == "international":
                item["price_unit"] = "美元/吨"
                item["currency"] = "USD"
            else:
                item["price_unit"] = "元/吨"
                item["currency"] = "CNY"

            for cell in cells:
                date_val = extract_date(cell)
                if date_val and "date" not in item:
                    item["date"] = date_val

                num_val = extract_number(cell)
                if num_val is not None:
                    if num_val > 10 and "price" not in item:
                        item["price"] = num_val
                    elif "涨跌" in cell or "变化" in cell:
                        if "%" in cell:
                            item["price_change_pct"] = num_val
                        else:
                            item["price_change"] = num_val

            if "date" in item and "price" in item:
                items.append(item)

    for card in sel.css(".index-card, .price-index-item, .index-product"):
        name_elem = card.css(".comp-name, .product-name, .index-name, h3, h4")
        if not name_elem:
            continue

        name = name_elem[0].text.strip()
        metal_cn, metal_name, category = identify_metal(name)

        value_elem = card.css(".value, .price-val, .index-val")
        if not value_elem:
            continue

        value = extract_number(value_elem[0].text.strip())
        if value is None:
            continue

        date_elem = card.css("time, .date, .update-time")
        date = date_elem[0].text.strip() if date_elem else datetime.now().strftime("%Y-%m-%d")

        item = {
            "date": extract_date(date),
            "metal": metal_name,
            "metal_cn": metal_cn,
            "category": category,
            "price": value,
            "price_unit": "元/吨",
            "currency": "CNY",
            "market_type": "index",
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }

        change_elem = card.css(".change, .diff")
        if change_elem:
            change_text = change_elem[0].text.strip()
            change_val = extract_number(change_text)
            if change_val is not None:
                item["price_change"] = change_val

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

        metal_cn, metal_name, _ = identify_metal(title)

        link_elem = article.css("a")
        source_url = url
        if link_elem:
            href = link_elem[0].attrib.get("href", "")
            if href and href.startswith("http"):
                source_url = href
            elif href and href.startswith("/"):
                source_url = f"https://www.chinametal.org.cn{href}"

        category = "report"
        if "monthly" in url.lower() or "月报" in title:
            category = "monthly_report"
        elif "annual" in url.lower() or "年报" in title:
            category = "annual_report"
        elif "analysis" in url.lower() or "分析" in title:
            category = "analysis"

        items.append({
            "date": date,
            "title": title,
            "category": category,
            "metal": metal_name,
            "summary": content[:500],
            "source_url": source_url,
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

        related_metals = []
        for cat_key, cat_info in METAL_CATEGORIES.items():
            for product in cat_info["products"]:
                if product in title or product in content:
                    related_metals.append(product)

        category = "news"
        if "policy" in url.lower() or "政策" in title:
            category = "policy"
        elif "industry" in url.lower() or "行业" in title:
            category = "industry"

        items.append({
            "date": date,
            "title": title,
            "category": category,
            "content": content[:500],
            "related_metals": ",".join(related_metals),
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def save_production_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO production_data
                (date, metal, metal_cn, category, production, production_unit,
                 yoy_change, yoy_unit, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"),
                    item.get("metal"),
                    item.get("metal_cn"),
                    item.get("category"),
                    item.get("production"),
                    item.get("production_unit", "万吨"),
                    item.get("yoy_change"),
                    item.get("yoy_unit", "%"),
                    item.get("source_url"),
                    item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_trade_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO trade_data
                (date, metal, metal_cn, category, import_volume, export_volume,
                 trade_unit, import_value, export_value, value_unit,
                 source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"),
                    item.get("metal"),
                    item.get("metal_cn"),
                    item.get("category"),
                    item.get("import_volume"),
                    item.get("export_volume"),
                    item.get("trade_unit", "万吨"),
                    item.get("import_value"),
                    item.get("export_value"),
                    item.get("value_unit", "亿美元"),
                    item.get("source_url"),
                    item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_prices_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO price_data
                (date, metal, metal_cn, category, price, price_unit, currency,
                 price_change, price_change_pct, market_type, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"),
                    item.get("metal"),
                    item.get("metal_cn"),
                    item.get("category"),
                    item.get("price"),
                    item.get("price_unit", "元/吨"),
                    item.get("currency", "CNY"),
                    item.get("price_change"),
                    item.get("price_change_pct"),
                    item.get("market_type", "domestic"),
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
                (date, title, category, metal, summary, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"),
                    item.get("title"),
                    item.get("category"),
                    item.get("metal"),
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


def save_news_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO industry_news
                (date, title, category, content, related_metals, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"),
                    item.get("title"),
                    item.get("category"),
                    item.get("content"),
                    item.get("related_metals"),
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


def get_cnmia_data(
    include_production: bool = True,
    include_trade: bool = True,
    include_prices: bool = True,
    include_reports: bool = True,
    include_news: bool = True,
) -> dict:
    """Fetch data from China Nonferrous Metals Industry Association.

    Args:
        include_production: Whether to fetch production statistics.
        include_trade: Whether to fetch import/export data.
        include_prices: Whether to fetch price data.
        include_reports: Whether to fetch industry reports.
        include_news: Whether to fetch industry news.

    Returns:
        Dict with keys: production, trade, prices, reports, news.
    """
    conn = init_db()
    result = {
        "production": [],
        "trade": [],
        "prices": [],
        "reports": [],
        "news": [],
    }

    logger.info("Starting CNMIA data fetch")

    if include_production:
        for url in TARGET_URLS["statistics"]:
            if "production" in url or "inventory" in url:
                page = fetch_page(url)
                if page:
                    prod_items = extract_production_data(page["html"], url)
                    if prod_items:
                        result["production"].extend(prod_items)
                        logger.info("  Extracted %d production records from %s", len(prod_items), url)
                time.sleep(2)

    if include_trade:
        for url in TARGET_URLS["statistics"]:
            if "trade" in url:
                page = fetch_page(url)
                if page:
                    trade_items = extract_trade_data(page["html"], url)
                    if trade_items:
                        result["trade"].extend(trade_items)
                        logger.info("  Extracted %d trade records from %s", len(trade_items), url)
                time.sleep(2)

    if include_prices:
        for url in TARGET_URLS["prices"]:
            page = fetch_page(url)
            if page:
                price_items = extract_price_data(page["html"], url)
                if price_items:
                    result["prices"].extend(price_items)
                    logger.info("  Extracted %d price records from %s", len(price_items), url)
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

    if include_news:
        for url in TARGET_URLS["news"]:
            page = fetch_page(url)
            if page:
                news_items = extract_news(page["html"], url)
                if news_items:
                    result["news"].extend(news_items)
                    logger.info("  Extracted %d news articles from %s", len(news_items), url)
            time.sleep(2)

    if result["production"]:
        n = save_production_to_sqlite(result["production"], conn)
        save_to_json(result["production"], OUTPUT_DIR / "cnmia_production.json")
        logger.info("Saved %d production records (%d to SQLite)", len(result["production"]), n)

    if result["trade"]:
        n = save_trade_to_sqlite(result["trade"], conn)
        save_to_json(result["trade"], OUTPUT_DIR / "cnmia_trade.json")
        logger.info("Saved %d trade records (%d to SQLite)", len(result["trade"]), n)

    if result["prices"]:
        n = save_prices_to_sqlite(result["prices"], conn)
        save_to_json(result["prices"], OUTPUT_DIR / "cnmia_prices.json")
        logger.info("Saved %d price records (%d to SQLite)", len(result["prices"]), n)

    if result["reports"]:
        n = save_reports_to_sqlite(result["reports"], conn)
        save_to_json(result["reports"], OUTPUT_DIR / "cnmia_reports.json")
        logger.info("Saved %d reports (%d to SQLite)", len(result["reports"]), n)

    if result["news"]:
        n = save_news_to_sqlite(result["news"], conn)
        save_to_json(result["news"], OUTPUT_DIR / "cnmia_news.json")
        logger.info("Saved %d news articles (%d to SQLite)", len(result["news"]), n)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_cnmia_data(
        include_production=True,
        include_trade=True,
        include_prices=True,
        include_reports=True,
        include_news=True,
    )

    print(f"\n{'=' * 70}")
    print(f"CNMIA Data Fetch Complete")
    print(f"{'=' * 70}")
    print(f"Production records: {len(results['production'])}")
    print(f"Trade records: {len(results['trade'])}")
    print(f"Price records: {len(results['prices'])}")
    print(f"Reports: {len(results['reports'])}")
    print(f"News articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON files in: {OUTPUT_DIR}")
    print(f"{'=' * 70}")
