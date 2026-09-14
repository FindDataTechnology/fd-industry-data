#!/usr/bin/env python3
"""
China Chemical Information Center (CCIC) Spider
中国化工信息中心数据爬虫

Target: http://www.ccin.com.cn/
Data Coverage:
  - Chemical product prices
  - Market supply/demand data
  - Industry news and analysis
  - Technical standards

Authentication:
  - Public data, no login required
  - Some detailed data may require subscription
  - Rate limiting recommended (2-3s delay)

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting, proper headers
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
logger = logging.getLogger("ccin")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "ccin.db"

CHEMICAL_PRODUCTS = {
    "organic": {
        "cn_name": "有机化工",
        "products": ["甲醇", "乙二醇", "苯乙烯", "丙烯腈", "PTA", "PVC"],
    },
    "inorganic": {
        "cn_name": "无机化工",
        "products": ["烧碱", "纯碱", "硫酸", "盐酸", "磷酸"],
    },
    "polymers": {
        "cn_name": "合成材料",
        "products": ["聚乙烯", "聚丙烯", "聚苯乙烯", "ABS", "聚碳酸酯"],
    },
    "intermediates": {
        "cn_name": "化工中间体",
        "products": ["苯", "甲苯", "二甲苯", "萘", "蒽"],
    },
    "fertilizers": {
        "cn_name": "化肥",
        "products": ["尿素", "磷酸二铵", "氯化钾", "复合肥"],
    },
}

TARGET_URLS = {
    "homepage": "http://www.ccin.com.cn/",
    "prices": [
        "http://www.ccin.com.cn/price/organic/",
        "http://www.ccin.com.cn/price/inorganic/",
        "http://www.ccin.com.cn/price/polymers/",
        "http://www.ccin.com.cn/price/daily/",
    ],
    "market": [
        "http://www.ccin.com.cn/market/analysis/",
        "http://www.ccin.com.cn/market/supply-demand/",
        "http://www.ccin.com.cn/market/trends/",
    ],
    "news": [
        "http://www.ccin.com.cn/news/industry/",
        "http://www.ccin.com.cn/news/company/",
        "http://www.ccin.com.cn/news/policy/",
    ],
    "standards": [
        "http://www.ccin.com.cn/standards/national/",
        "http://www.ccin.com.cn/standards/industry/",
    ],
    "reports": [
        "http://www.ccin.com.cn/reports/weekly/",
        "http://www.ccin.com.cn/reports/monthly/",
        "http://www.ccin.com.cn/reports/annual/",
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
            region TEXT DEFAULT 'China',
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, product, region, source_url)
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS technical_standards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            standard_no TEXT NOT NULL,
            title TEXT NOT NULL,
            category TEXT,
            status TEXT,
            publish_date TEXT,
            implement_date TEXT,
            description TEXT,
            source_url TEXT UNIQUE,
            scraped_at TEXT NOT NULL
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
            content TEXT,
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
        r"(\d{4}-\d{2})",
        r"(\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return datetime.now().strftime("%Y-%m-%d")


def extract_number(text: str) -> float | None:
    text = text.replace(",", "").replace("，", "").replace(" ", "")
    match = re.search(r"[-+]?\d+\.?\d*", text)
    if match:
        try:
            return float(match.group())
        except ValueError:
            return None
    return None


def identify_product(text: str) -> tuple[str, str, str]:
    for cat_key, cat_info in CHEMICAL_PRODUCTS.items():
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

        is_price = any(kw in header_text for kw in ["价格", "报价", "均价", "price", "元/吨"])
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
                    if num_val > 500 and "price" not in item:
                        item["price"] = num_val
                    elif "涨跌" in cell or "变化" in cell:
                        if "%" in cell:
                            item["price_change_pct"] = num_val
                        else:
                            item["price_change"] = num_val

            if "date" in item and "price" in item:
                items.append(item)

    for article in sel.css("article, .price-item, .product-item"):
        title_elem = article.css("h1, h2, h3, .title, .product-name")
        if not title_elem:
            continue

        title = title_elem[0].text.strip()
        product_cn, product_name, category = identify_product(title)

        price_elem = article.css(".price, .quote, .value")
        if not price_elem:
            continue

        price_text = price_elem[0].text.strip()
        price_val = extract_number(price_text)
        if price_val is None:
            continue

        date_elem = article.css("time, .date, .update-time")
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
                    elif "供需" in header_text:
                        if "supply" in header_text.lower() or "供应" in header_text:
                            item["supply_index"] = num_val
                        else:
                            item["demand_index"] = num_val

                if "趋势" in cell or "涨" in cell or "跌" in cell:
                    item["market_trend"] = cell

            if "date" in item:
                items.append(item)

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
        for cat_key, cat_info in CHEMICAL_PRODUCTS.items():
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


def extract_standards(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        is_standards = any(kw in header_text for kw in ["标准号", "标准名称", "standard", "GB"])
        if not is_standards:
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            item = {
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            for i, cell in enumerate(cells):
                if re.match(r"(GB|HG|SH|QB|DB)\s?/?\s?T?\s?\d+", cell):
                    item["standard_no"] = cell
                elif i == 1 and "title" not in item:
                    item["title"] = cell
                elif "状态" in header_text or "status" in header_text.lower():
                    if "现行" in cell or "有效" in cell or "active" in cell.lower():
                        item["status"] = "active"
                    elif "废止" in cell or "作废" in cell:
                        item["status"] = "withdrawn"
                    else:
                        item["status"] = cell
                else:
                    date_val = extract_date(cell)
                    if date_val:
                        if "发布" in header_text or "publish" in header_text.lower():
                            item["publish_date"] = date_val
                        elif "实施" in header_text or "implement" in header_text.lower():
                            item["implement_date"] = date_val

            if "standard_no" in item and "title" in item:
                items.append(item)

    return items


def extract_reports(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for article in sel.css("article, .report-item, .article-item, .post"):
        title_elem = article.css("h1, h2, h3, .title, a")
        if not title_elem:
            continue

        title = title_elem[0].text.strip()
        if not title:
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
                source_url = f"http://www.ccin.com.cn{href}"

        category = "report"
        if "weekly" in url.lower() or "周报" in title:
            category = "weekly_report"
        elif "monthly" in url.lower() or "月报" in title:
            category = "monthly_report"
        elif "annual" in url.lower() or "年报" in title:
            category = "annual_report"

        items.append({
            "date": date,
            "title": title,
            "category": category,
            "product": product_name,
            "summary": content[:500],
            "content": content,
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
                 price_change, price_change_pct, region, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    item.get("region", "China"),
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
                 market_trend, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def save_standards_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO technical_standards
                (standard_no, title, category, status, publish_date, implement_date,
                 description, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("standard_no"),
                    item.get("title"),
                    item.get("category"),
                    item.get("status"),
                    item.get("publish_date"),
                    item.get("implement_date"),
                    item.get("description"),
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
                (date, title, category, product, summary, content, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"),
                    item.get("title"),
                    item.get("category"),
                    item.get("product"),
                    item.get("summary"),
                    item.get("content"),
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


def get_ccin_data(
    products: list[str] | None = None,
    include_standards: bool = True,
    include_reports: bool = True,
    include_news: bool = True,
) -> dict:
    """Fetch data from China Chemical Information Center.

    Args:
        products: Not used currently, reserved for future filtering.
        include_standards: Whether to fetch technical standards.
        include_reports: Whether to fetch industry reports.
        include_news: Whether to fetch industry news.

    Returns:
        Dict with keys: prices, market, standards, reports, news.
    """
    conn = init_db()
    result = {
        "prices": [],
        "market": [],
        "standards": [],
        "reports": [],
        "news": [],
    }

    logger.info("Starting CCIC data fetch")

    for url in TARGET_URLS["prices"]:
        page = fetch_page(url)
        if page:
            price_items = extract_price_data(page["html"], url)
            if price_items:
                result["prices"].extend(price_items)
                logger.info("  Extracted %d price records from %s", len(price_items), url)
        time.sleep(3)

    for url in TARGET_URLS["market"]:
        page = fetch_page(url)
        if page:
            market_items = extract_market_data(page["html"], url)
            if market_items:
                result["market"].extend(market_items)
                logger.info("  Extracted %d market records from %s", len(market_items), url)
        time.sleep(3)

    if include_standards:
        for url in TARGET_URLS["standards"]:
            page = fetch_page(url)
            if page:
                std_items = extract_standards(page["html"], url)
                if std_items:
                    result["standards"].extend(std_items)
                    logger.info("  Extracted %d standards from %s", len(std_items), url)
            time.sleep(3)

    if include_reports:
        for url in TARGET_URLS["reports"]:
            page = fetch_page(url)
            if page:
                report_items = extract_reports(page["html"], url)
                if report_items:
                    result["reports"].extend(report_items)
                    logger.info("  Extracted %d reports from %s", len(report_items), url)
            time.sleep(3)

    if include_news:
        for url in TARGET_URLS["news"]:
            page = fetch_page(url)
            if page:
                news_items = extract_news(page["html"], url)
                if news_items:
                    result["news"].extend(news_items)
                    logger.info("  Extracted %d news articles from %s", len(news_items), url)
            time.sleep(3)

    if result["prices"]:
        n = save_prices_to_sqlite(result["prices"], conn)
        save_to_json(result["prices"], OUTPUT_DIR / "ccin_prices.json")
        logger.info("Saved %d price records (%d to SQLite)", len(result["prices"]), n)

    if result["market"]:
        n = save_market_to_sqlite(result["market"], conn)
        save_to_json(result["market"], OUTPUT_DIR / "ccin_market.json")
        logger.info("Saved %d market records (%d to SQLite)", len(result["market"]), n)

    if result["standards"]:
        n = save_standards_to_sqlite(result["standards"], conn)
        save_to_json(result["standards"], OUTPUT_DIR / "ccin_standards.json")
        logger.info("Saved %d standards (%d to SQLite)", len(result["standards"]), n)

    if result["reports"]:
        n = save_reports_to_sqlite(result["reports"], conn)
        save_to_json(result["reports"], OUTPUT_DIR / "ccin_reports.json")
        logger.info("Saved %d reports (%d to SQLite)", len(result["reports"]), n)

    if result["news"]:
        n = save_news_to_sqlite(result["news"], conn)
        save_to_json(result["news"], OUTPUT_DIR / "ccin_news.json")
        logger.info("Saved %d news articles (%d to SQLite)", len(result["news"]), n)

    conn.close()
    return result


def run_ccin(limit: int = 100) -> list[dict]:
    """Entry point for fd-open-data-protocol dispatch."""
    results = get_ccin_data(include_standards=True, include_reports=True, include_news=True)
    items = []
    for key in ("prices", "market", "standards", "reports", "news"):
        items.extend(results.get(key, []))
    return items[:limit]


if __name__ == "__main__":
    results = get_ccin_data(
        include_standards=True,
        include_reports=True,
        include_news=True,
    )

    print(f"\n{'=' * 70}")
    print(f"CCIC Data Fetch Complete")
    print(f"{'=' * 70}")
    print(f"Price records: {len(results['prices'])}")
    print(f"Market records: {len(results['market'])}")
    print(f"Technical standards: {len(results['standards'])}")
    print(f"Industry reports: {len(results['reports'])}")
    print(f"News articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON files in: {OUTPUT_DIR}")
    print(f"{'=' * 70}")
