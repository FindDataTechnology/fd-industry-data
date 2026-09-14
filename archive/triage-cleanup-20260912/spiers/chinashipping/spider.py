#!/usr/bin/env python3
"""
China Shipping Association Spider
中国船东协会数据爬虫

Target: http://www.chinashipping.org.cn/
Data: Shipping industry data, fleet statistics, trade route data, market analysis
Focus: Shipping market indices, fleet capacity, container shipping data

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts shipping market indices and fleet data
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
logger = logging.getLogger("chinashipping")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "chinashipping.db"
JSON_MARKET_PATH = OUTPUT_DIR / "shipping_market.json"
JSON_FLEET_PATH = OUTPUT_DIR / "shipping_fleet.json"
JSON_TRADE_PATH = OUTPUT_DIR / "shipping_trade.json"
JSON_NEWS_PATH = OUTPUT_DIR / "shipping_news.json"

TARGET_URLS = {
    "market": [
        "http://www.chinashipping.org.cn/data/market/",
        "http://www.chinashipping.org.cn/data/index/",
        "http://www.chinashipping.org.cn/scindex/",
    ],
    "fleet": [
        "http://www.chinashipping.org.cn/data/fleet/",
        "http://www.chinashipping.org.cn/data/ship/",
    ],
    "trade": [
        "http://www.chinashipping.org.cn/data/trade/",
        "http://www.chinashipping.org.cn/data/route/",
    ],
    "news": [
        "http://www.chinashipping.org.cn/news/industry/",
        "http://www.chinashipping.org.cn/news/policy/",
    ],
}

SHIPPING_INDICATORS = {
    "中国出口集装箱运价指数": "ccfi",
    "CCFI": "ccfi",
    "中国沿海散货运价指数": "cbfi",
    "CBFI": "cbfi",
    "中国进口干散货运价指数": "cidfi",
    "CIDFI": "cidfi",
    "中国进口原油运价指数": "cicotfi",
    "集装箱运价": "container_freight",
    "散货运价": "bulk_freight",
    "油轮运价": "tanker_freight",
    "船舶保有量": "fleet_size",
    "运力": "capacity",
    "载重吨": "deadweight_tonnage",
    "集装箱船运力": "container_capacity",
    "散货船运力": "bulk_carrier_capacity",
    "油船运力": "tanker_capacity",
    "外贸货运量": "foreign_trade_volume",
    "内贸货运量": "domestic_trade_volume",
    "港口货物吞吐量": "port_throughput",
    "港口集装箱吞吐量": "port_container_throughput",
}

SHIP_TYPES = {
    "集装箱船": "container_ship",
    "散货船": "bulk_carrier",
    "油船": "tanker",
    "液化气船": "lng_lpg_carrier",
    "化学品船": "chemical_tanker",
    "滚装船": "ro_ro_ship",
    "杂货船": "general_cargo",
    "冷藏船": "reefer_ship",
    "客船": "passenger_ship",
}

TRADE_ROUTES = {
    "远东-欧洲": "far_east_europe",
    "远东-北美": "far_east_north_america",
    "远东-地中海": "far_east_mediterranean",
    "远东-波斯湾": "far_east_persian_gulf",
    "远东-南美": "far_east_south_america",
    "远东-南非": "far_east_south_africa",
    "中国-东南亚": "china_southeast_asia",
    "中国-日韩": "china_japan_korea",
    "中国-澳洲": "china_australia",
    "内贸": "domestic",
}


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shipping_market (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            indicator TEXT,
            indicator_cn TEXT,
            value REAL,
            unit TEXT,
            change_value REAL,
            change_unit TEXT DEFAULT '%',
            route TEXT,
            route_cn TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, indicator, route, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shipping_fleet (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            ship_type TEXT,
            ship_type_cn TEXT,
            vessel_count INTEGER,
            deadweight REAL,
            dw_unit TEXT DEFAULT '万载重吨',
            teu_capacity REAL,
            teu_unit TEXT DEFAULT '万TEU',
            growth_rate REAL,
            growth_unit TEXT DEFAULT '%',
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, ship_type, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shipping_trade (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            route TEXT,
            route_cn TEXT,
            cargo_type TEXT,
            cargo_type_cn TEXT,
            value REAL,
            unit TEXT DEFAULT '万TEU',
            growth_rate REAL,
            growth_unit TEXT DEFAULT '%',
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, route, cargo_type, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shipping_news (
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


def identify_indicator(text: str) -> dict | None:
    for cn_name, en_name in SHIPPING_INDICATORS.items():
        if cn_name in text:
            return {"indicator": en_name, "indicator_cn": cn_name}
    return None


def identify_ship_type(text: str) -> dict | None:
    for cn_name, en_name in SHIP_TYPES.items():
        if cn_name in text:
            return {"ship_type": en_name, "ship_type_cn": cn_name}
    return None


def identify_route(text: str) -> dict | None:
    for cn_name, en_name in TRADE_ROUTES.items():
        if cn_name in text:
            return {"route": en_name, "route_cn": cn_name}
    return None


def extract_period(text: str) -> str:
    patterns = [
        r"(\d{4}年\d{1,2}月)",
        r"(\d{4}-\d{2})",
        r"(\d{4}年\d{1,2}月\d{1,2}日)",
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{4}年)",
        r"(\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return datetime.now().strftime("%Y-%m")


def extract_market_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["运价", "指数", "CCFI", "CBFI", "航线", "point"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            cell_text = " ".join(cells)
            ind_info = identify_indicator(cell_text)
            if not ind_info:
                ind_info = {"indicator": "composite_index", "indicator_cn": "综合指数"}

            route_info = identify_route(cell_text)
            if not route_info:
                route_info = {"route": "composite", "route_cn": "综合"}

            item = {
                "period": extract_period(cell_text),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(ind_info)
            item.update(route_info)

            numbers = re.findall(r"[-+]?[\d,]+\.?\d*", " ".join(cells).replace(",", ""))
            if numbers:
                try:
                    item["value"] = float(numbers[0])
                except ValueError:
                    continue

            if len(numbers) >= 2:
                try:
                    item["change_value"] = float(numbers[1])
                    item["change_unit"] = "%"
                except ValueError:
                    pass

            if "点" in cell_text:
                item["unit"] = "点"
            else:
                item["unit"] = "点"

            if item.get("value"):
                items.append(item)

    body_text = sel.css("body").first.text if sel.css("body") else ""
    index_patterns = [
        r"(?:CCFI|中国出口集装箱运价指数)[^\d]*?(\d+\.?\d*)",
        r"(?:CBFI|中国沿海散货运价指数)[^\d]*?(\d+\.?\d*)",
    ]
    for pattern in index_patterns:
        matches = re.findall(pattern, body_text)
        for match in matches:
            if match:
                value = float(match)
                if value > 0:
                    items.append({
                        "period": extract_period(body_text),
                        "indicator": "ccfi" if "CCFI" in pattern else "cbfi",
                        "indicator_cn": "中国出口集装箱运价指数" if "CCFI" in pattern else "中国沿海散货运价指数",
                        "value": value,
                        "unit": "点",
                        "route": "composite",
                        "route_cn": "综合",
                        "source_url": url,
                        "scraped_at": datetime.now(timezone.utc).isoformat(),
                    })

    return items


def extract_fleet_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["船", "运力", "艘", "载重吨", "TEU", "船队"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            cell_text = " ".join(cells)
            type_info = identify_ship_type(cell_text)
            if not type_info:
                type_info = {"ship_type": "total", "ship_type_cn": "合计"}

            item = {
                "period": extract_period(cell_text),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(type_info)

            numbers = re.findall(r"[\d,]+\.?\d*", " ".join(cells).replace(",", ""))
            if numbers:
                try:
                    item["vessel_count"] = int(float(numbers[0]))
                except ValueError:
                    pass

            if len(numbers) >= 2:
                try:
                    item["deadweight"] = float(numbers[1])
                except ValueError:
                    pass

            if "万载重吨" in cell_text:
                item["dw_unit"] = "万载重吨"
            elif "载重吨" in cell_text:
                item["dw_unit"] = "载重吨"

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate"] = float(pct[0])
                        item["growth_unit"] = "%"

            if item.get("vessel_count") or item.get("deadweight"):
                items.append(item)

    return items


def extract_trade_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["航线", "贸易", "货运", "TEU", "集装箱", "吞吐"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            cell_text = " ".join(cells)
            route_info = identify_route(cell_text)
            if not route_info:
                route_info = {"route": "total", "route_cn": "合计"}

            item = {
                "period": extract_period(cell_text),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(route_info)

            if "集装箱" in cell_text:
                item["cargo_type"] = "container"
                item["cargo_type_cn"] = "集装箱"
            elif "干散货" in cell_text:
                item["cargo_type"] = "dry_bulk"
                item["cargo_type_cn"] = "干散货"
            elif "原油" in cell_text:
                item["cargo_type"] = "crude_oil"
                item["cargo_type_cn"] = "原油"
            elif "成品油" in cell_text:
                item["cargo_type"] = "refined_oil"
                item["cargo_type_cn"] = "成品油"
            elif "液化" in cell_text:
                item["cargo_type"] = "lng_lpg"
                item["cargo_type_cn"] = "液化气"
            else:
                item["cargo_type"] = "general"
                item["cargo_type_cn"] = "综合"

            numbers = re.findall(r"[\d,]+\.?\d*", " ".join(cells).replace(",", ""))
            if numbers:
                try:
                    item["value"] = float(numbers[0])
                except ValueError:
                    continue

            if "万TEU" in cell_text or "万标箱" in cell_text:
                item["unit"] = "万TEU"
            elif "万吨" in cell_text:
                item["unit"] = "万吨"
            elif "亿吨" in cell_text:
                item["unit"] = "亿吨"
            else:
                item["unit"] = "万TEU"

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate"] = float(pct[0])
                        item["growth_unit"] = "%"

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
        if "运价" in title or "指数" in title:
            category = "market"
        elif "航运" in title:
            category = "shipping"
        elif "政策" in title:
            category = "policy"
        elif "贸易" in title:
            category = "trade"
        elif "港口" in title:
            category = "port"

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
                INSERT OR REPLACE INTO shipping_market
                (period, indicator, indicator_cn, value, unit,
                 change_value, change_unit, route, route_cn, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("indicator"), item.get("indicator_cn"),
                    item.get("value"), item.get("unit"),
                    item.get("change_value"), item.get("change_unit"),
                    item.get("route"), item.get("route_cn"),
                    item.get("source_url"), item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_fleet_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO shipping_fleet
                (period, ship_type, ship_type_cn, vessel_count, deadweight,
                 dw_unit, teu_capacity, teu_unit, growth_rate, growth_unit,
                 source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("ship_type"), item.get("ship_type_cn"),
                    item.get("vessel_count"), item.get("deadweight"), item.get("dw_unit"),
                    item.get("teu_capacity"), item.get("teu_unit"),
                    item.get("growth_rate"), item.get("growth_unit"),
                    item.get("source_url"), item.get("scraped_at"),
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
                INSERT OR REPLACE INTO shipping_trade
                (period, route, route_cn, cargo_type, cargo_type_cn,
                 value, unit, growth_rate, growth_unit, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("route"), item.get("route_cn"),
                    item.get("cargo_type"), item.get("cargo_type_cn"),
                    item.get("value"), item.get("unit"),
                    item.get("growth_rate"), item.get("growth_unit"),
                    item.get("source_url"), item.get("scraped_at"),
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
                INSERT OR REPLACE INTO shipping_news
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


def get_chinashipping_data(
    include_market: bool = True,
    include_fleet: bool = True,
    include_trade: bool = True,
    include_news: bool = True,
) -> dict:
    """Fetch data from China Shipping Association.

    Args:
        include_market: Whether to fetch shipping market index data.
        include_fleet: Whether to fetch fleet statistics.
        include_trade: Whether to fetch trade route data.
        include_news: Whether to fetch shipping news.

    Returns:
        Dict with keys: 'market', 'fleet', 'trade', 'news'.
    """
    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "market": [],
        "fleet": [],
        "trade": [],
        "news": [],
    }

    logger.info("Starting China Shipping Association spider")

    if include_market:
        for url in TARGET_URLS["market"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_market_data(page["html"], url)
                if items:
                    result["market"].extend(items)
                    logger.info("  Extracted %d market records", len(items))
            time.sleep(2)

    if include_fleet:
        for url in TARGET_URLS["fleet"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_fleet_data(page["html"], url)
                if items:
                    result["fleet"].extend(items)
                    logger.info("  Extracted %d fleet records", len(items))
            time.sleep(2)

    if include_trade:
        for url in TARGET_URLS["trade"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_trade_data(page["html"], url)
                if items:
                    result["trade"].extend(items)
                    logger.info("  Extracted %d trade records", len(items))
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
        save_to_json(result["market"], JSON_MARKET_PATH)
        logger.info("Saved %d market records: SQLite=%d, JSON=%s", len(result["market"]), n, JSON_MARKET_PATH)

    if result["fleet"]:
        n = save_fleet_to_sqlite(result["fleet"], conn)
        save_to_json(result["fleet"], JSON_FLEET_PATH)
        logger.info("Saved %d fleet records: SQLite=%d, JSON=%s", len(result["fleet"]), n, JSON_FLEET_PATH)

    if result["trade"]:
        n = save_trade_to_sqlite(result["trade"], conn)
        save_to_json(result["trade"], JSON_TRADE_PATH)
        logger.info("Saved %d trade records: SQLite=%d, JSON=%s", len(result["trade"]), n, JSON_TRADE_PATH)

    if result["news"]:
        n = save_news_to_sqlite(result["news"], conn)
        save_to_json(result["news"], JSON_NEWS_PATH)
        logger.info("Saved %d news articles: SQLite=%d, JSON=%s", len(result["news"]), n, JSON_NEWS_PATH)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_chinashipping_data()
    print(f"\n{'=' * 70}")
    print(f"Total market records: {len(results['market'])}")
    print(f"Total fleet records: {len(results['fleet'])}")
    print(f"Total trade records: {len(results['trade'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON (market): {JSON_MARKET_PATH}")
    print(f"JSON (fleet): {JSON_FLEET_PATH}")
    print(f"JSON (trade): {JSON_TRADE_PATH}")
    print(f"JSON (news): {JSON_NEWS_PATH}")
    print(f"{'=' * 70}")

    if results["market"]:
        print(f"\nShipping Market ({len(results['market'])} records):")
        for stat in results["market"][:10]:
            print(f"  {stat.get('indicator_cn', 'N/A'):>20s} | "
                  f"{stat.get('value', 'N/A'):>10} {stat.get('unit', '')}")

    if results["fleet"]:
        print(f"\nFleet Statistics ({len(results['fleet'])} records):")
        for stat in results["fleet"][:10]:
            print(f"  {stat.get('ship_type_cn', 'N/A'):>12s} | "
                  f"{stat.get('vessel_count', 'N/A'):>6} vessels | "
                  f"{stat.get('deadweight', 'N/A'):>10} {stat.get('dw_unit', '')}")

    if results["trade"]:
        print(f"\nTrade Routes ({len(results['trade'])} records):")
        for stat in results["trade"][:10]:
            print(f"  {stat.get('route_cn', 'N/A'):>16s} | "
                  f"{stat.get('value', 'N/A'):>10} {stat.get('unit', '')}")

    if results["news"]:
        print(f"\nRecent News ({len(results['news'])} articles):")
        for article in results["news"][:5]:
            print(f"  [{article.get('date', 'N/A')}] {article.get('title', 'N/A')[:60]}")
