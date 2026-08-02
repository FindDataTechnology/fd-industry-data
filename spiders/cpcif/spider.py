#!/usr/bin/env python3
"""
China Petroleum & Chemical Industry Federation (CPCIF) Spider
中国石油和化学工业联合会数据爬虫

Target: http://www.cpcifa.org.cn/
Data Coverage:
  - Chemical production data
  - Petrochemical industry statistics
  - Market analysis reports
  - Trade data

Authentication:
  - Public data, no login required
  - Some detailed reports may require membership
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
logger = logging.getLogger("cpcif")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "cpcif.db"

CHEMICAL_CATEGORIES = {
    "basic_chemicals": {
        "cn_name": "基础化学品",
        "products": ["硫酸", "烧碱", "纯碱", "合成氨", "尿素", "乙烯", "丙烯"],
    },
    "petrochemicals": {
        "cn_name": "石油化工",
        "products": ["原油加工", "汽油", "柴油", "煤油", "润滑油", "石脑油"],
    },
    "fine_chemicals": {
        "cn_name": "精细化工",
        "products": ["涂料", "染料", "颜料", "催化剂", "助剂"],
    },
    "fertilizers": {
        "cn_name": "化肥",
        "products": ["氮肥", "磷肥", "钾肥", "复合肥"],
    },
    "polymers": {
        "cn_name": "合成材料",
        "products": ["合成树脂", "合成纤维", "合成橡胶"],
    },
}

TARGET_URLS = {
    "homepage": "http://www.cpcifa.org.cn/",
    "statistics": [
        "http://www.cpcifa.org.cn/data/stats/",
        "http://www.cpcifa.org.cn/data/production/",
        "http://www.cpcifa.org.cn/data/output/",
    ],
    "market": [
        "http://www.cpcifa.org.cn/market/analysis/",
        "http://www.cpcifa.org.cn/market/prices/",
        "http://www.cpcifa.org.cn/market/supply-demand/",
    ],
    "reports": [
        "http://www.cpcifa.org.cn/reports/monthly/",
        "http://www.cpcifa.org.cn/reports/annual/",
        "http://www.cpcifa.org.cn/reports/industry/",
    ],
    "trade": [
        "http://www.cpcifa.org.cn/trade/import-export/",
        "http://www.cpcifa.org.cn/trade/statistics/",
    ],
    "news": [
        "http://www.cpcifa.org.cn/news/industry/",
        "http://www.cpcifa.org.cn/news/policy/",
    ],
}


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS production_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            product TEXT NOT NULL,
            product_cn TEXT,
            category TEXT,
            production_volume REAL,
            production_unit TEXT DEFAULT '万吨',
            yoy_change REAL,
            yoy_unit TEXT DEFAULT '%',
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, product, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            product TEXT,
            product_cn TEXT,
            price REAL,
            price_unit TEXT DEFAULT '元/吨',
            currency TEXT DEFAULT 'CNY',
            supply_index REAL,
            demand_index REAL,
            market_trend TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, product, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            product TEXT,
            product_cn TEXT,
            category TEXT,
            import_volume REAL,
            export_volume REAL,
            volume_unit TEXT DEFAULT '万吨',
            import_value REAL,
            export_value REAL,
            value_unit TEXT DEFAULT '亿美元',
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, product, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS industry_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            title TEXT NOT NULL,
            category TEXT,
            summary TEXT,
            content TEXT,
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
    for cat_key, cat_info in CHEMICAL_CATEGORIES.items():
        for product in cat_info["products"]:
            if product in text:
                return product, product, cat_key
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
            kw in header_text for kw in ["产量", "生产", "产出", "产值", "production"]
        )
        if not is_production:
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
                if date_val and "period" not in item:
                    item["period"] = date_val

                num_val = extract_number(cell)
                if num_val is not None:
                    if "产量" in header_text or "production" in header_text.lower():
                        if "production_volume" not in item:
                            item["production_volume"] = num_val
                            item["production_unit"] = "万吨"
                    if "%" in cell or "同比" in cell or "增长" in cell:
                        item["yoy_change"] = num_val
                        item["yoy_unit"] = "%"

            if "period" in item:
                items.append(item)

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
            kw in header_text for kw in ["价格", "均价", "行情", "price", "市场", "供需"]
        )
        if not is_market:
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            row_text = " ".join(cells)
            product_cn, product_name, _ = identify_product(row_text)

            item = {
                "product": product_name,
                "product_cn": product_cn,
                "price_unit": "元/吨",
                "currency": "CNY",
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            for cell in cells:
                date_val = extract_date(cell)
                if date_val and "date" not in item:
                    item["date"] = date_val

                num_val = extract_number(cell)
                if num_val is not None and num_val > 100:
                    if "price" not in item:
                        item["price"] = num_val

                if "涨" in cell or "跌" in cell or "趋势" in cell:
                    item["market_trend"] = cell

            if "date" in item:
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

        is_trade = any(kw in header_text for kw in ["进口", "出口", "贸易", "进出口", "trade"])
        if not is_trade:
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            row_text = " ".join(cells)
            product_cn, product_name, category = identify_product(row_text)

            item = {
                "product": product_name,
                "product_cn": product_cn,
                "category": category,
                "volume_unit": "万吨",
                "value_unit": "亿美元",
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            for cell in cells:
                date_val = extract_date(cell)
                if date_val and "period" not in item:
                    item["period"] = date_val

            num_cells = [extract_number(c) for c in cells]
            num_cells = [n for n in num_cells if n is not None]

            if len(num_cells) >= 1:
                item["import_volume"] = num_cells[0]
            if len(num_cells) >= 2:
                item["export_volume"] = num_cells[1]
            if len(num_cells) >= 3:
                item["import_value"] = num_cells[2]
            if len(num_cells) >= 4:
                item["export_value"] = num_cells[3]

            if "period" in item:
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
        if not title:
            continue

        content_elem = article.css("p, .content, .summary, .excerpt")
        content = content_elem[0].text.strip() if content_elem else ""

        date_elem = article.css("time, .date, .publish-date, .time")
        date = date_elem[0].text.strip() if date_elem else ""
        date = extract_date(date)

        link_elem = article.css("a")
        source_url = url
        if link_elem:
            href = link_elem[0].attrib.get("href", "")
            if href and href.startswith("http"):
                source_url = href
            elif href and href.startswith("/"):
                source_url = f"http://www.cpcifa.org.cn{href}"

        category = "report"
        if "monthly" in url.lower() or "月报" in title:
            category = "monthly_report"
        elif "annual" in url.lower() or "年报" in title:
            category = "annual_report"
        elif "industry" in url.lower() or "行业" in title:
            category = "industry_report"

        items.append({
            "date": date,
            "title": title,
            "category": category,
            "summary": content[:500],
            "content": content,
            "source_url": source_url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    for link in sel.css("a"):
        href = link.attrib.get("href", "")
        text = link.text.strip()
        if not text or not href:
            continue
        if any(kw in text for kw in ["报告", "分析", "统计", "月报", "年报", "行业"]):
            full_url = href if href.startswith("http") else f"http://www.cpcifa.org.cn{href}"
            if not any(item["source_url"] == full_url for item in items):
                items.append({
                    "date": extract_date(text),
                    "title": text,
                    "category": "report",
                    "summary": "",
                    "content": "",
                    "source_url": full_url,
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
                (period, product, product_cn, category, production_volume,
                 production_unit, yoy_change, yoy_unit, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"),
                    item.get("product"),
                    item.get("product_cn"),
                    item.get("category"),
                    item.get("production_volume"),
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


def save_market_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO market_analysis
                (date, product, product_cn, price, price_unit, currency,
                 supply_index, demand_index, market_trend, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"),
                    item.get("product"),
                    item.get("product_cn"),
                    item.get("price"),
                    item.get("price_unit", "元/吨"),
                    item.get("currency", "CNY"),
                    item.get("supply_index"),
                    item.get("demand_index"),
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


def save_trade_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO trade_data
                (period, product, product_cn, category, import_volume, export_volume,
                 volume_unit, import_value, export_value, value_unit, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"),
                    item.get("product"),
                    item.get("product_cn"),
                    item.get("category"),
                    item.get("import_volume"),
                    item.get("export_volume"),
                    item.get("volume_unit", "万吨"),
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


def save_reports_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO industry_reports
                (date, title, category, summary, content, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"),
                    item.get("title"),
                    item.get("category"),
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


def save_news_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO industry_news
                (date, title, category, content, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"),
                    item.get("title"),
                    item.get("category"),
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


def get_cpcif_data(
    categories: list[str] | None = None,
    include_reports: bool = True,
    include_news: bool = True,
) -> dict:
    """Fetch data from China Petroleum & Chemical Industry Federation.

    Args:
        categories: Not used currently, reserved for future filtering.
        include_reports: Whether to fetch industry reports.
        include_news: Whether to fetch industry news.

    Returns:
        Dict with keys: production, market, trade, reports, news.
    """
    conn = init_db()
    result = {
        "production": [],
        "market": [],
        "trade": [],
        "reports": [],
        "news": [],
    }

    logger.info("Starting CPCIF data fetch")

    for url in TARGET_URLS["statistics"]:
        page = fetch_page(url)
        if page:
            prod_items = extract_production_data(page["html"], url)
            if prod_items:
                result["production"].extend(prod_items)
                logger.info("  Extracted %d production records from %s", len(prod_items), url)
        time.sleep(3)

    for url in TARGET_URLS["market"]:
        page = fetch_page(url)
        if page:
            market_items = extract_market_data(page["html"], url)
            if market_items:
                result["market"].extend(market_items)
                logger.info("  Extracted %d market records from %s", len(market_items), url)
        time.sleep(3)

    for url in TARGET_URLS["trade"]:
        page = fetch_page(url)
        if page:
            trade_items = extract_trade_data(page["html"], url)
            if trade_items:
                result["trade"].extend(trade_items)
                logger.info("  Extracted %d trade records from %s", len(trade_items), url)
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

    if result["production"]:
        n = save_production_to_sqlite(result["production"], conn)
        save_to_json(result["production"], OUTPUT_DIR / "cpcif_production.json")
        logger.info("Saved %d production records (%d to SQLite)", len(result["production"]), n)

    if result["market"]:
        n = save_market_to_sqlite(result["market"], conn)
        save_to_json(result["market"], OUTPUT_DIR / "cpcif_market.json")
        logger.info("Saved %d market records (%d to SQLite)", len(result["market"]), n)

    if result["trade"]:
        n = save_trade_to_sqlite(result["trade"], conn)
        save_to_json(result["trade"], OUTPUT_DIR / "cpcif_trade.json")
        logger.info("Saved %d trade records (%d to SQLite)", len(result["trade"]), n)

    if result["reports"]:
        n = save_reports_to_sqlite(result["reports"], conn)
        save_to_json(result["reports"], OUTPUT_DIR / "cpcif_reports.json")
        logger.info("Saved %d reports (%d to SQLite)", len(result["reports"]), n)

    if result["news"]:
        n = save_news_to_sqlite(result["news"], conn)
        save_to_json(result["news"], OUTPUT_DIR / "cpcif_news.json")
        logger.info("Saved %d news articles (%d to SQLite)", len(result["news"]), n)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_cpcif_data(include_reports=True, include_news=True)

    print(f"\n{'=' * 70}")
    print(f"CPCIF Data Fetch Complete")
    print(f"{'=' * 70}")
    print(f"Production records: {len(results['production'])}")
    print(f"Market records: {len(results['market'])}")
    print(f"Trade records: {len(results['trade'])}")
    print(f"Industry reports: {len(results['reports'])}")
    print(f"News articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON files in: {OUTPUT_DIR}")
    print(f"{'=' * 70}")
