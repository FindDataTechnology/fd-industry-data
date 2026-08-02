#!/usr/bin/env python3
"""
MySteel Spider - 我的钢铁网数据爬虫

Target: https://www.mysteel.com
Data: Steel prices, production statistics, import/export data, industry news

Authentication Requirements:
  - Basic price indices and news are publicly accessible
  - Detailed price data requires Mysteel Gold/Silver membership
  - API access requires Mysteel Data API license
  - Historical data and advanced analytics are premium-only

Publicly Accessible:
  - Steel price index (MSPI) daily
  - Major product prices (rebar, HRC, CRC, wire rod, plate)
  - Steel industry news headlines
  - Production overview summaries
  - Iron ore port inventory snapshots

Premium (Membership Required):
  - Detailed daily prices by region/grade/specification
  - Historical price database (20+ years)
  - Production statistics by mill
  - Import/export detailed customs data
  - Downstream demand indicators
  - Cost model and margin analysis

Architecture:
  - Fetcher with browser impersonation
  - Rate limiting (3s delay)
  - SQLite + JSON output
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
logger = logging.getLogger("mysteel")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "mysteel.db"
JSON_PATH = OUTPUT_DIR / "mysteel_data.json"

STEEL_PRODUCTS = {
    "rebar": {"cn_name": "螺纹钢", "spec": "HRB400 20mm", "unit": "元/吨"},
    "hrc": {"cn_name": "热轧板卷", "spec": "Q235B 4.75mm", "unit": "元/吨"},
    "crc": {"cn_name": "冷轧板卷", "spec": "SPCC 1.0mm", "unit": "元/吨"},
    "wire_rod": {"cn_name": "线材", "spec": "HPB300 6.5mm", "unit": "元/吨"},
    "plate": {"cn_name": "中厚板", "spec": "Q235B 20mm", "unit": "元/吨"},
    "angle_steel": {"cn_name": "角钢", "spec": "Q235B 5#", "unit": "元/吨"},
    "h_beam": {"cn_name": "H型钢", "spec": "Q235B 200*200", "unit": "元/吨"},
}

PRICE_URLS = [
    "https://www.mysteel.com/price/p_price.html",
    "https://www.mysteel.com/price/p_steel.html",
    "https://www.mysteel.com/price/p_rebar.html",
    "https://www.mysteel.com/price/p_hrc.html",
]

INDEX_URLS = [
    "https://www.mysteel.com/index/mspi.html",
    "https://www.mysteel.com/index/steel.html",
]

NEWS_URLS = [
    "https://www.mysteel.com/news/steel.html",
    "https://www.mysteel.com/news/market.html",
    "https://www.mysteel.com/news/international.html",
]

PRODUCTION_URLS = [
    "https://www.mysteel.com/data/production.html",
    "https://www.mysteel.com/data/inventory.html",
]

IRON_ORE_URLS = [
    "https://www.mysteel.com/price/p_ironore.html",
    "https://www.mysteel.com/price/p_coke.html",
]


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS steel_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product TEXT NOT NULL,
            product_cn TEXT,
            specification TEXT,
            price_low REAL,
            price_high REAL,
            price_avg REAL,
            price_change REAL,
            price_change_pct REAL,
            unit TEXT DEFAULT '元/吨',
            region TEXT,
            city TEXT,
            trade_date TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(product, region, city, trade_date, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS steel_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_name TEXT NOT NULL,
            index_value REAL,
            change_value REAL,
            change_pct REAL,
            trade_date TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(index_name, trade_date, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS steel_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            summary TEXT,
            category TEXT,
            source TEXT DEFAULT 'Mysteel',
            publish_date TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(title, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS production_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product TEXT NOT NULL,
            production_volume REAL,
            capacity_utilization REAL,
            inventory_volume REAL,
            unit TEXT DEFAULT '万吨',
            stat_period TEXT,
            yoy_change REAL,
            mom_change REAL,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(product, stat_period, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_material_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material TEXT NOT NULL,
            price REAL,
            price_change REAL,
            price_change_pct REAL,
            unit TEXT,
            specification TEXT,
            trade_date TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(material, specification, trade_date, source_url)
        )
    """)
    conn.commit()
    return conn


def fetch_page(url: str, fetcher: Fetcher) -> dict | None:
    try:
        logger.info("Fetching: %s", url)
        response = fetcher.get(url, timeout=30, stealthy_headers=True)
        if response.status != 200:
            logger.warning("HTTP %d for %s", response.status, url)
            return None
        return {"url": url, "html": response.html, "status": response.status}
    except Exception as e:
        logger.error("Fetch failed for %s: %s", url, e)
        return None


def extract_steel_prices(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []
    now = datetime.now(timezone.utc).isoformat()

    product_key = detect_product_from_url(url)
    product_info = STEEL_PRODUCTS.get(product_key, {})

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue
        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)
        if not any(kw in header_text for kw in ["价格", "品名", "材质", "规格", "price", "钢厂"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            item = {
                "product": product_info.get("cn_name", cells[0]),
                "product_cn": product_info.get("cn_name", cells[0]),
                "specification": product_info.get("spec", cells[1] if len(cells) > 1 else ""),
                "price_low": parse_float(cells[2]) if len(cells) > 2 else None,
                "price_high": parse_float(cells[3]) if len(cells) > 3 else None,
                "price_avg": parse_float(cells[4]) if len(cells) > 4 else None,
                "price_change": parse_float(cells[5]) if len(cells) > 5 else None,
                "price_change_pct": parse_float(cells[6]) if len(cells) > 6 else None,
                "unit": "元/吨",
                "region": cells[7] if len(cells) > 7 else "",
                "city": cells[8] if len(cells) > 8 else detect_city(cells),
                "trade_date": extract_date(cells[-1]) if len(cells) > 0 else "",
                "source_url": url,
                "scraped_at": now,
            }

            if "price_avg" not in item or item["price_avg"] is None:
                if item["price_low"] and item["price_high"]:
                    item["price_avg"] = (item["price_low"] + item["price_high"]) / 2

            if item.get("price_low") or item.get("price_avg"):
                items.append(item)

    return items


def extract_steel_index(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []
    now = datetime.now(timezone.utc).isoformat()

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue
        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)
        if not any(kw in header_text for kw in ["指数", "index", "MSPI", "涨跌"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            item = {
                "index_name": cells[0],
                "index_value": parse_float(cells[1]) if len(cells) > 1 else None,
                "change_value": parse_float(cells[2]) if len(cells) > 2 else None,
                "change_pct": parse_float(cells[3]) if len(cells) > 3 else None,
                "trade_date": extract_date(cells[-1]) if len(cells) > 4 else "",
                "source_url": url,
                "scraped_at": now,
            }
            items.append(item)

    for card in sel.css("div.index-card, div.index-item, div.data-card"):
        name_el = card.css(".name::text, .index-name::text, h4::text")
        value_el = card.css(".value::text, .index-value::text, .num::text")
        change_el = card.css(".change::text, .chg::text")

        name = name_el[0].text.strip() if name_el else ""
        if not name:
            continue

        items.append({
            "index_name": name,
            "index_value": parse_float(value_el[0].text.strip()) if value_el else None,
            "change_value": parse_float(change_el[0].text.strip()) if change_el else None,
            "change_pct": None,
            "trade_date": "",
            "source_url": url,
            "scraped_at": now,
        })

    return items


def extract_steel_news(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []
    now = datetime.now(timezone.utc).isoformat()

    category = "steel"
    if "market" in url:
        category = "market"
    elif "international" in url:
        category = "international"

    for article in sel.css("div.news-list li, div.article-item, li.news-item, div.list-item"):
        title_el = article.css("a::text, h3::text, .title::text, h4::text")
        title = title_el[0].text.strip() if title_el else ""
        if not title:
            continue

        link_el = article.css("a::attr(href)")
        link = link_el[0].text.strip() if link_el else ""
        if link and not link.startswith("http"):
            link = "https://www.mysteel.com" + link

        summary_el = article.css("p::text, .summary::text, .desc::text")
        summary = summary_el[0].text.strip() if summary_el else ""

        date_el = article.css("span.date::text, .time::text, time::text")
        publish_date = ""
        if date_el:
            publish_date = extract_date(date_el[0].text.strip())

        items.append({
            "title": title,
            "summary": summary,
            "category": category,
            "source": "Mysteel",
            "publish_date": publish_date,
            "source_url": link or url,
            "scraped_at": now,
        })

    return items


def extract_production_stats(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []
    now = datetime.now(timezone.utc).isoformat()

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue
        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)
        if not any(kw in header_text for kw in ["产量", "产能", "库存", "production", "inventory", "utilization"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            item = {
                "product": cells[0],
                "production_volume": parse_float(cells[1]) if len(cells) > 1 else None,
                "capacity_utilization": parse_float(cells[2]) if len(cells) > 2 else None,
                "inventory_volume": parse_float(cells[3]) if len(cells) > 3 else None,
                "unit": "万吨",
                "stat_period": extract_date(cells[4]) if len(cells) > 4 else "",
                "yoy_change": parse_float(cells[5]) if len(cells) > 5 else None,
                "mom_change": parse_float(cells[6]) if len(cells) > 6 else None,
                "source_url": url,
                "scraped_at": now,
            }
            items.append(item)

    return items


def extract_raw_material_prices(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []
    now = datetime.now(timezone.utc).isoformat()

    material = "铁矿石"
    if "coke" in url or "焦炭" in url:
        material = "焦炭"

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue
        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)
        if not any(kw in header_text for kw in ["价格", "品名", "price", "矿", "焦"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            item = {
                "material": cells[0] if not any(c.isdigit() for c in cells[0]) else material,
                "price": parse_float(cells[1]) if len(cells) > 1 else None,
                "price_change": parse_float(cells[2]) if len(cells) > 2 else None,
                "price_change_pct": parse_float(cells[3]) if len(cells) > 3 else None,
                "unit": "元/吨",
                "specification": cells[4] if len(cells) > 4 else "",
                "trade_date": extract_date(cells[-1]) if len(cells) > 5 else "",
                "source_url": url,
                "scraped_at": now,
            }
            items.append(item)

    return items


def detect_product_from_url(url: str) -> str:
    if "rebar" in url or "螺纹" in url:
        return "rebar"
    if "hrc" in url or "热轧" in url:
        return "hrc"
    if "crc" in url or "冷轧" in url:
        return "crc"
    return "rebar"


def detect_city(cells: list[str]) -> str:
    major_cities = ["上海", "北京", "天津", "广州", "杭州", "南京", "武汉", "成都", "重庆", "沈阳", "西安", "郑州", "长沙", "济南", "合肥"]
    for cell in cells:
        for city in major_cities:
            if city in cell:
                return city
    return ""


def extract_date(text: str) -> str:
    patterns = [
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{4}年\d{1,2}月\d{1,2}日)",
        r"(\d{4}/\d{2}/\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def parse_float(text: str) -> float | None:
    if not text:
        return None
    cleaned = text.replace(",", "").replace("，", "").replace("%", "").replace("--", "").strip()
    if not cleaned or cleaned == "-":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def save_to_sqlite(conn: sqlite3.Connection, table: str, items: list[dict]) -> int:
    inserted = 0
    for item in items:
        try:
            if table == "steel_prices":
                conn.execute(
                    """INSERT OR REPLACE INTO steel_prices
                    (product, product_cn, specification, price_low, price_high, price_avg,
                     price_change, price_change_pct, unit, region, city, trade_date,
                     source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("product", ""), item.get("product_cn", ""),
                     item.get("specification", ""), item.get("price_low"),
                     item.get("price_high"), item.get("price_avg"),
                     item.get("price_change"), item.get("price_change_pct"),
                     item.get("unit", "元/吨"), item.get("region", ""),
                     item.get("city", ""), item.get("trade_date", ""),
                     item["source_url"], item["scraped_at"]),
                )
            elif table == "steel_index":
                conn.execute(
                    """INSERT OR REPLACE INTO steel_index
                    (index_name, index_value, change_value, change_pct,
                     trade_date, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (item["index_name"], item.get("index_value"),
                     item.get("change_value"), item.get("change_pct"),
                     item.get("trade_date", ""), item["source_url"], item["scraped_at"]),
                )
            elif table == "steel_news":
                conn.execute(
                    """INSERT OR REPLACE INTO steel_news
                    (title, summary, category, source, publish_date, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (item["title"], item.get("summary", ""), item.get("category", ""),
                     item.get("source", "Mysteel"), item.get("publish_date", ""),
                     item["source_url"], item["scraped_at"]),
                )
            elif table == "production_stats":
                conn.execute(
                    """INSERT OR REPLACE INTO production_stats
                    (product, production_volume, capacity_utilization, inventory_volume,
                     unit, stat_period, yoy_change, mom_change, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["product"], item.get("production_volume"),
                     item.get("capacity_utilization"), item.get("inventory_volume"),
                     item.get("unit", "万吨"), item.get("stat_period", ""),
                     item.get("yoy_change"), item.get("mom_change"),
                     item["source_url"], item["scraped_at"]),
                )
            elif table == "raw_material_prices":
                conn.execute(
                    """INSERT OR REPLACE INTO raw_material_prices
                    (material, price, price_change, price_change_pct, unit,
                     specification, trade_date, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["material"], item.get("price"), item.get("price_change"),
                     item.get("price_change_pct"), item.get("unit", "元/吨"),
                     item.get("specification", ""), item.get("trade_date", ""),
                     item["source_url"], item["scraped_at"]),
                )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error (%s): %s", table, e)
    conn.commit()
    return inserted


def save_to_json(items: list[dict], json_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def get_mysteel_data(categories: list[str] | None = None) -> dict[str, list[dict]]:
    """Fetch public data from MySteel (我的钢铁网).

    Args:
        categories: List of categories to fetch.
            Available: prices, index, news, production, raw_materials.
            Default: all.

    Returns:
        Dict with keys: steel_prices, steel_index, steel_news, production_stats, raw_material_prices.
    """
    if categories is None:
        categories = ["prices", "index", "news", "production", "raw_materials"]

    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()
    results = {
        "steel_prices": [],
        "steel_index": [],
        "steel_news": [],
        "production_stats": [],
        "raw_material_prices": [],
    }

    if "prices" in categories:
        logger.info("=== Fetching Steel Prices ===")
        for url in PRICE_URLS:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_steel_prices(page["html"], url)
                results["steel_prices"].extend(items)
                save_to_sqlite(conn, "steel_prices", items)
                logger.info("  Extracted %d price records from %s", len(items), url)
            time.sleep(3)

    if "index" in categories:
        logger.info("=== Fetching Steel Indices ===")
        for url in INDEX_URLS:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_steel_index(page["html"], url)
                results["steel_index"].extend(items)
                save_to_sqlite(conn, "steel_index", items)
                logger.info("  Extracted %d index records from %s", len(items), url)
            time.sleep(3)

    if "news" in categories:
        logger.info("=== Fetching Steel News ===")
        for url in NEWS_URLS:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_steel_news(page["html"], url)
                results["steel_news"].extend(items)
                save_to_sqlite(conn, "steel_news", items)
                logger.info("  Extracted %d news items from %s", len(items), url)
            time.sleep(3)

    if "production" in categories:
        logger.info("=== Fetching Production Statistics ===")
        for url in PRODUCTION_URLS:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_production_stats(page["html"], url)
                results["production_stats"].extend(items)
                save_to_sqlite(conn, "production_stats", items)
                logger.info("  Extracted %d production records from %s", len(items), url)
            time.sleep(3)

    if "raw_materials" in categories:
        logger.info("=== Fetching Raw Material Prices ===")
        for url in IRON_ORE_URLS:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_raw_material_prices(page["html"], url)
                results["raw_material_prices"].extend(items)
                save_to_sqlite(conn, "raw_material_prices", items)
                logger.info("  Extracted %d raw material records from %s", len(items), url)
            time.sleep(3)

    all_items = []
    for category_items in results.values():
        all_items.extend(category_items)
    save_to_json(all_items, JSON_PATH)

    total = sum(len(v) for v in results.values())
    logger.info("Done! %d total items -> SQLite (%s), JSON (%s)", total, DB_PATH, JSON_PATH)

    conn.close()
    return results


if __name__ == "__main__":
    results = get_mysteel_data()
    print(f"\n{'=' * 70}")
    print(f"MySteel Spider Results")
    print(f"{'=' * 70}")
    for key, items in results.items():
        print(f"  {key}: {len(items)} items")
    print(f"  SQLite: {DB_PATH}")
    print(f"  JSON:   {JSON_PATH}")
    print(f"{'=' * 70}")
