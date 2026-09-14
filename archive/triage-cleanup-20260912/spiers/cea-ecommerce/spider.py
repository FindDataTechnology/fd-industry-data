#!/usr/bin/env python3
"""
China E-commerce Association Spider
中国电子商务协会数据爬虫

Target: http://www.ec.com.cn/
Data: E-commerce market data, online retail statistics, platform performance, consumer behavior
Focus: Online retail volume, e-commerce GMV, cross-border e-commerce, digital trade

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts e-commerce GMV and platform performance data
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
logger = logging.getLogger("cea_ecommerce")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "cea_ecommerce.db"
JSON_MARKET_STATS_PATH = OUTPUT_DIR / "cea_ecommerce_market.json"
JSON_PLATFORM_PATH = OUTPUT_DIR / "cea_ecommerce_platform.json"
JSON_CROSSBORDER_PATH = OUTPUT_DIR / "cea_ecommerce_crossborder.json"
JSON_NEWS_PATH = OUTPUT_DIR / "cea_ecommerce_news.json"

TARGET_URLS = {
    "market_stats": [
        "http://www.ec.com.cn/article/zgdsz/index.html",
        "http://www.ec.com.cn/article/dsdata/index.html",
    ],
    "platform": [
        "http://www.ec.com.cn/article/pingtai/index.html",
        "http://www.ec.com.cn/article/dianshang/index.html",
    ],
    "crossborder": [
        "http://www.ec.com.cn/article/kjd/index.html",
        "http://www.ec.com.cn/article/kjds/index.html",
    ],
    "news": [
        "http://www.ec.com.cn/article/xwzx/index.html",
        "http://www.ec.com.cn/article/zyxw/index.html",
    ],
}

ECOMMERCE_CATEGORIES = {
    "网络零售": "online_retail",
    "B2B": "b2b",
    "B2C": "b2c",
    "C2C": "c2c",
    "跨境电商": "cross_border",
    "社交电商": "social_ecommerce",
    "直播电商": "live_ecommerce",
    "农村电商": "rural_ecommerce",
    "生鲜电商": "fresh_ecommerce",
    "社区团购": "community_group_buy",
}


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ecommerce_market (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            category TEXT,
            category_cn TEXT,
            indicator_name TEXT,
            value REAL,
            unit TEXT,
            growth_rate REAL,
            growth_unit TEXT DEFAULT '%',
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, category, indicator_name, region, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS platform_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            platform_name TEXT,
            gmv REAL,
            gmv_unit TEXT DEFAULT '亿元',
            market_share REAL,
            user_count REAL,
            order_count REAL,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, platform_name, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS crossborder_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            direction TEXT DEFAULT 'import',
            value REAL,
            unit TEXT DEFAULT '亿元',
            growth_rate REAL,
            growth_unit TEXT DEFAULT '%',
            category TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, direction, category, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS industry_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            title TEXT,
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


def identify_ecommerce_category(text: str) -> dict | None:
    for cn_name, en_name in ECOMMERCE_CATEGORIES.items():
        if cn_name in text:
            return {"category": en_name, "category_cn": cn_name}
    return None


def extract_period(text: str) -> str:
    patterns = [
        r"(\d{4}年\d{1,2}月)",
        r"(\d{4}-\d{2})",
        r"(\d{4}年)",
        r"(\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return datetime.now().strftime("%Y-%m")


def extract_market_statistics(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["电商", "零售", "交易", "GMV", "规模", "增长", "亿元", "万亿"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            cell_text = " ".join(cells)
            cat_info = identify_ecommerce_category(cell_text)
            if not cat_info:
                cat_info = {"category": "general", "category_cn": "综合电商"}

            item = {
                "period": extract_period(cell_text),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(cat_info)

            numbers = re.findall(r"[\d,]+\.?\d*", " ".join(cells).replace(",", ""))
            if numbers:
                try:
                    item["value"] = float(numbers[0])
                    if "万亿" in cell_text:
                        item["value"] = item["value"] * 10000
                        item["unit"] = "亿元"
                    elif "亿" in cell_text:
                        item["unit"] = "亿元"
                    elif "万" in cell_text:
                        item["unit"] = "万人"
                    else:
                        item["unit"] = "亿元"
                except ValueError:
                    continue

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate"] = float(pct[0])
                        item["growth_unit"] = "%"

            if "规模" in header_text or "GMV" in header_text or "交易" in cell_text:
                item["indicator_name"] = "gmv"
            elif "用户" in header_text or "人数" in cell_text:
                item["indicator_name"] = "user_count"
            elif "订单" in header_text:
                item["indicator_name"] = "order_count"
            elif "渗透" in header_text:
                item["indicator_name"] = "penetration_rate"
            else:
                item["indicator_name"] = "general"

            if item.get("value"):
                items.append(item)

    return items


def extract_platform_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["平台", "淘宝", "京东", "拼多多", "抖音", "GMV", "份额"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            item = {
                "period": extract_period(" ".join(cells)),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            for i, cell in enumerate(cells):
                if i == 0 and not re.search(r"\d", cell):
                    item["platform_name"] = cell
                else:
                    nums = re.findall(r"[\d,]+\.?\d*", cell.replace(",", ""))
                    if nums:
                        try:
                            val = float(nums[0])
                            if "GMV" in header_text or "交易" in header_text:
                                if "gmv" not in item:
                                    item["gmv"] = val
                            elif "份额" in header_text or "占比" in header_text:
                                item["market_share"] = val
                            elif "用户" in header_text:
                                item["user_count"] = val
                            elif "订单" in header_text:
                                item["order_count"] = val
                        except ValueError:
                            pass

            if "万亿" in " ".join(cells):
                item["gmv_unit"] = "万亿元"
            elif "亿" in " ".join(cells):
                item["gmv_unit"] = "亿元"

            if item.get("platform_name"):
                items.append(item)

    return items


def extract_crossborder_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["跨境", "进口", "出口", "外贸", "海淘"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            cell_text = " ".join(cells)
            item = {
                "period": extract_period(cell_text),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            if "进口" in cell_text:
                item["direction"] = "import"
            elif "出口" in cell_text:
                item["direction"] = "export"
            else:
                item["direction"] = "total"

            numbers = re.findall(r"[\d,]+\.?\d*", cell_text.replace(",", ""))
            if numbers:
                try:
                    item["value"] = float(numbers[0])
                    if "万亿" in cell_text:
                        item["value"] = item["value"] * 10000
                        item["unit"] = "亿元"
                    elif "亿" in cell_text:
                        item["unit"] = "亿元"
                except ValueError:
                    continue

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate"] = float(pct[0])
                        item["growth_unit"] = "%"

            cat_info = identify_ecommerce_category(cell_text)
            if cat_info:
                item["category"] = cat_info["category"]

            if item.get("value"):
                items.append(item)

    return items


def extract_news(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for article in sel.css("article, .news-item, .article-item, .list-item, li"):
        title_elem = article.css("h1, h2, h3, h4, .title, a")
        if not title_elem:
            continue

        title = title_elem[0].text.strip() if title_elem else ""
        if not title or len(title) < 5:
            continue

        content_elem = article.css("p, .content, .summary, .excerpt, .desc")
        content = content_elem[0].text.strip() if content_elem else ""

        date_elem = article.css("time, .date, .publish-date, span")
        date = date_elem[0].text.strip() if date_elem else ""
        date = extract_period(date)

        category = "news"
        if "政策" in title:
            category = "policy"
        elif "报告" in title or "分析" in title:
            category = "analysis"
        elif "数据" in title:
            category = "data"
        elif "跨境" in title:
            category = "cross_border"
        elif "直播" in title:
            category = "live_ecommerce"

        items.append({
            "date": date,
            "title": title,
            "content": content[:500] if content else "",
            "category": category,
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def save_market_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO ecommerce_market
                (period, category, category_cn, indicator_name, value, unit,
                 growth_rate, growth_unit, region, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("category"), item.get("category_cn"),
                    item.get("indicator_name"), item.get("value"), item.get("unit"),
                    item.get("growth_rate"), item.get("growth_unit"),
                    item.get("region"), item.get("source_url"), item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_platform_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO platform_performance
                (period, platform_name, gmv, gmv_unit, market_share, user_count, order_count, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("platform_name"),
                    item.get("gmv"), item.get("gmv_unit", "亿元"),
                    item.get("market_share"), item.get("user_count"),
                    item.get("order_count"), item.get("source_url"), item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_crossborder_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO crossborder_stats
                (period, direction, value, unit, growth_rate, growth_unit, category, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("direction"),
                    item.get("value"), item.get("unit", "亿元"),
                    item.get("growth_rate"), item.get("growth_unit"),
                    item.get("category"), item.get("source_url"), item.get("scraped_at"),
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
                (date, title, content, category, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"), item.get("title"), item.get("content"),
                    item.get("category"), item.get("source_url"), item.get("scraped_at"),
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


def get_cea_ecommerce_data(
    include_market: bool = True,
    include_platform: bool = True,
    include_crossborder: bool = True,
    include_news: bool = True,
) -> dict:
    """Fetch data from China E-commerce Association.

    Args:
        include_market: Whether to fetch e-commerce market statistics.
        include_platform: Whether to fetch platform performance data.
        include_crossborder: Whether to fetch cross-border e-commerce data.
        include_news: Whether to fetch industry news.

    Returns:
        Dict with keys: 'market', 'platform', 'crossborder', 'news'.
    """
    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "market": [],
        "platform": [],
        "crossborder": [],
        "news": [],
    }

    logger.info("Starting CEA E-commerce spider")

    if include_market:
        for url in TARGET_URLS["market_stats"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_market_statistics(page["html"], url)
                if items:
                    result["market"].extend(items)
                    logger.info("  Extracted %d market records", len(items))
            time.sleep(2)

    if include_platform:
        for url in TARGET_URLS["platform"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_platform_data(page["html"], url)
                if items:
                    result["platform"].extend(items)
                    logger.info("  Extracted %d platform records", len(items))
            time.sleep(2)

    if include_crossborder:
        for url in TARGET_URLS["crossborder"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_crossborder_data(page["html"], url)
                if items:
                    result["crossborder"].extend(items)
                    logger.info("  Extracted %d cross-border records", len(items))
            time.sleep(2)

    if include_news:
        for url in TARGET_URLS["news"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_news(page["html"], url)
                if items:
                    result["news"].extend(items)
                    logger.info("  Extracted %d news articles", len(items))
            time.sleep(2)

    if result["market"]:
        n = save_market_to_sqlite(result["market"], conn)
        save_to_json(result["market"], JSON_MARKET_STATS_PATH)
        logger.info("Saved %d market records: SQLite=%d, JSON=%s", len(result["market"]), n, JSON_MARKET_STATS_PATH)

    if result["platform"]:
        n = save_platform_to_sqlite(result["platform"], conn)
        save_to_json(result["platform"], JSON_PLATFORM_PATH)
        logger.info("Saved %d platform records: SQLite=%d, JSON=%s", len(result["platform"]), n, JSON_PLATFORM_PATH)

    if result["crossborder"]:
        n = save_crossborder_to_sqlite(result["crossborder"], conn)
        save_to_json(result["crossborder"], JSON_CROSSBORDER_PATH)
        logger.info("Saved %d cross-border records: SQLite=%d, JSON=%s", len(result["crossborder"]), n, JSON_CROSSBORDER_PATH)

    if result["news"]:
        n = save_news_to_sqlite(result["news"], conn)
        save_to_json(result["news"], JSON_NEWS_PATH)
        logger.info("Saved %d news articles: SQLite=%d, JSON=%s", len(result["news"]), n, JSON_NEWS_PATH)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_cea_ecommerce_data()
    print(f"\n{'=' * 70}")
    print(f"Total market records: {len(results['market'])}")
    print(f"Total platform records: {len(results['platform'])}")
    print(f"Total cross-border records: {len(results['crossborder'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON (market): {JSON_MARKET_STATS_PATH}")
    print(f"JSON (platform): {JSON_PLATFORM_PATH}")
    print(f"JSON (crossborder): {JSON_CROSSBORDER_PATH}")
    print(f"JSON (news): {JSON_NEWS_PATH}")
    print(f"{'=' * 70}")

    if results["market"]:
        print(f"\nE-commerce Market ({len(results['market'])} records):")
        for stat in results["market"][:10]:
            print(f"  {stat.get('period', 'N/A'):>12s} | {stat.get('category_cn', 'N/A'):>8s} | "
                  f"{stat.get('indicator_name', 'N/A'):>15s} | {stat.get('value', 'N/A'):>12} {stat.get('unit', '')}")

    if results["platform"]:
        print(f"\nPlatform Performance ({len(results['platform'])} records):")
        for p in results["platform"][:10]:
            print(f"  {p.get('platform_name', 'N/A'):>15s} | GMV: {p.get('gmv', 'N/A')} {p.get('gmv_unit', '')} | "
                  f"Share: {p.get('market_share', 'N/A')}%")
