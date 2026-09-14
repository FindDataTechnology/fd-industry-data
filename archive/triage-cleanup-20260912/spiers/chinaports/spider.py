#!/usr/bin/env python3
"""
China Ports Association Spider
中国港口协会数据爬虫

Target: http://www.chinaports.com/
Data: Port throughput statistics, container traffic, cargo volume, trade flow data
Focus: Major port throughput, TEU container volumes, bulk cargo statistics

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts port ranking and throughput comparison data
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
logger = logging.getLogger("chinaports")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "chinaports.db"
JSON_THROUGHPUT_PATH = OUTPUT_DIR / "port_throughput.json"
JSON_CONTAINER_PATH = OUTPUT_DIR / "port_container.json"
JSON_NEWS_PATH = OUTPUT_DIR / "port_news.json"

TARGET_URLS = {
    "throughput": [
        "http://www.chinaports.com/data/statistics/",
        "http://www.chinaports.com/data/throughput/",
        "http://www.chinaports.com/www/portdata/",
    ],
    "container": [
        "http://www.chinaports.com/data/container/",
        "http://www.chinaports.com/data/teu/",
    ],
    "news": [
        "http://www.chinaports.com/news/industry/",
        "http://www.chinaports.com/news/policy/",
    ],
}

MAJOR_PORTS = {
    "上海港": "shanghai",
    "宁波舟山港": "ningbo_zhoushan",
    "深圳港": "shenzhen",
    "广州港": "guangzhou",
    "青岛港": "qingdao",
    "天津港": "tianjin",
    "厦门港": "xiamen",
    "大连港": "dalian",
    "苏州港": "suzhou",
    "营口港": "yingkou",
    "唐山港": "tangshan",
    "日照港": "rizhao",
    "烟台港": "yantai",
    "连云港": "lianyungang",
    "福州港": "fuzhou",
    "泉州港": "quanzhou",
    "湛江港": "zhanjiang",
    "北部湾港": "beibu_gulf",
    "重庆港": "chongqing",
    "南京港": "nanjing",
    "武汉港": "wuhan",
}

CARGO_TYPES = {
    "货物吞吐量": "total_throughput",
    "集装箱": "container",
    "煤炭": "coal",
    "铁矿石": "iron_ore",
    "原油": "crude_oil",
    "粮食": "grain",
    "钢铁": "steel",
    "化肥": "fertilizer",
    "集装箱吞吐量": "container_throughput",
    "外贸货物": "foreign_trade_cargo",
    "内贸货物": "domestic_trade_cargo",
}


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS port_throughput (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            port_name TEXT,
            port_name_en TEXT,
            cargo_type TEXT,
            cargo_type_cn TEXT,
            value REAL,
            unit TEXT DEFAULT '万吨',
            growth_rate REAL,
            growth_unit TEXT DEFAULT '%',
            ranking INTEGER,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, port_name, cargo_type, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS container_traffic (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            port_name TEXT,
            port_name_en TEXT,
            teu_value REAL,
            teu_unit TEXT DEFAULT '万TEU',
            growth_rate REAL,
            growth_unit TEXT DEFAULT '%',
            ranking INTEGER,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, port_name, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS port_news (
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


def identify_port(text: str) -> dict | None:
    for cn_name, en_name in MAJOR_PORTS.items():
        if cn_name in text:
            return {"port_name": cn_name, "port_name_en": en_name}
    return None


def identify_cargo_type(text: str) -> dict | None:
    for cn_name, en_name in CARGO_TYPES.items():
        if cn_name in text:
            return {"cargo_type": en_name, "cargo_type_cn": cn_name}
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


def extract_throughput_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["吞吐", "货运", "港口", "货物", "万吨", "亿吨"]):
            continue

        for idx, row in enumerate(rows[1:], start=1):
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            cell_text = " ".join(cells)
            port_info = identify_port(cell_text)
            if not port_info:
                port_info = {"port_name": cells[0], "port_name_en": ""}

            cargo_info = identify_cargo_type(cell_text)
            if not cargo_info:
                cargo_info = {"cargo_type": "total_throughput", "cargo_type_cn": "货物吞吐量"}

            item = {
                "period": extract_period(cell_text),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
                "ranking": idx,
            }
            item.update(port_info)
            item.update(cargo_info)

            numbers = re.findall(r"[\d,]+\.?\d*", " ".join(cells).replace(",", ""))
            if numbers:
                try:
                    item["value"] = float(numbers[0])
                except ValueError:
                    continue

            if "亿吨" in cell_text:
                item["unit"] = "亿吨"
            elif "万吨" in cell_text:
                item["unit"] = "万吨"
            else:
                item["unit"] = "万吨"

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate"] = float(pct[0])
                        item["growth_unit"] = "%"

            if item.get("value"):
                items.append(item)

    body_text = sel.css("body").first.text if sel.css("body") else ""
    throughput_patterns = [
        r"([\u4e00-\u9fa5]+港)[^\d]*(\d+\.?\d*)\s*(?:万吨|亿吨)",
        r"吞吐量[为达到]*\s*(\d+\.?\d*)\s*(?:万吨|亿吨)",
    ]
    for pattern in throughput_patterns:
        matches = re.findall(pattern, body_text)
        for match in matches:
            if isinstance(match, tuple) and len(match) >= 2:
                port_name = match[0]
                value = float(match[1])
                if value > 0:
                    port_info = identify_port(port_name) or {"port_name": port_name, "port_name_en": ""}
                    items.append({
                        "period": extract_period(body_text),
                        "port_name": port_info["port_name"],
                        "port_name_en": port_info.get("port_name_en", ""),
                        "cargo_type": "total_throughput",
                        "cargo_type_cn": "货物吞吐量",
                        "value": value,
                        "unit": "万吨",
                        "source_url": url,
                        "scraped_at": datetime.now(timezone.utc).isoformat(),
                    })

    return items


def extract_container_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["集装箱", "TEU", "标箱"]):
            continue

        for idx, row in enumerate(rows[1:], start=1):
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            cell_text = " ".join(cells)
            port_info = identify_port(cell_text)
            if not port_info:
                port_info = {"port_name": cells[0], "port_name_en": ""}

            item = {
                "period": extract_period(cell_text),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
                "ranking": idx,
            }
            item.update(port_info)

            numbers = re.findall(r"[\d,]+\.?\d*", " ".join(cells).replace(",", ""))
            if numbers:
                try:
                    item["teu_value"] = float(numbers[0])
                except ValueError:
                    continue

            if "万TEU" in cell_text or "万标箱" in cell_text:
                item["teu_unit"] = "万TEU"
            else:
                item["teu_unit"] = "万TEU"

            for cell in cells:
                if "同比" in cell or "增长" in cell:
                    pct = re.findall(r"[-+]?\d+\.?\d*", cell.replace(",", ""))
                    if pct:
                        item["growth_rate"] = float(pct[0])
                        item["growth_unit"] = "%"

            if item.get("teu_value"):
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
        if "吞吐" in title or "集装箱" in title:
            category = "throughput"
        elif "政策" in title:
            category = "policy"
        elif "贸易" in title:
            category = "trade"
        elif "建设" in title:
            category = "construction"

        items.append({
            "date": date,
            "title": title,
            "content": content[:500] if content else "",
            "category": category,
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def save_throughput_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO port_throughput
                (period, port_name, port_name_en, cargo_type, cargo_type_cn,
                 value, unit, growth_rate, growth_unit, ranking, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("port_name"), item.get("port_name_en"),
                    item.get("cargo_type"), item.get("cargo_type_cn"),
                    item.get("value"), item.get("unit"),
                    item.get("growth_rate"), item.get("growth_unit"),
                    item.get("ranking"), item.get("source_url"), item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_container_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO container_traffic
                (period, port_name, port_name_en, teu_value, teu_unit,
                 growth_rate, growth_unit, ranking, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("port_name"), item.get("port_name_en"),
                    item.get("teu_value"), item.get("teu_unit"),
                    item.get("growth_rate"), item.get("growth_unit"),
                    item.get("ranking"), item.get("source_url"), item.get("scraped_at"),
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
                INSERT OR REPLACE INTO port_news
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


def get_chinaports_data(
    include_throughput: bool = True,
    include_container: bool = True,
    include_news: bool = True,
) -> dict:
    """Fetch data from China Ports Association.

    Args:
        include_throughput: Whether to fetch port throughput data.
        include_container: Whether to fetch container traffic data.
        include_news: Whether to fetch port industry news.

    Returns:
        Dict with keys: 'throughput', 'container', 'news'.
    """
    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "throughput": [],
        "container": [],
        "news": [],
    }

    logger.info("Starting China Ports Association spider")

    if include_throughput:
        for url in TARGET_URLS["throughput"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_throughput_data(page["html"], url)
                if items:
                    result["throughput"].extend(items)
                    logger.info("  Extracted %d throughput records", len(items))
            time.sleep(2)

    if include_container:
        for url in TARGET_URLS["container"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_container_data(page["html"], url)
                if items:
                    result["container"].extend(items)
                    logger.info("  Extracted %d container records", len(items))
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

    if result["throughput"]:
        n = save_throughput_to_sqlite(result["throughput"], conn)
        save_to_json(result["throughput"], JSON_THROUGHPUT_PATH)
        logger.info("Saved %d throughput records: SQLite=%d, JSON=%s", len(result["throughput"]), n, JSON_THROUGHPUT_PATH)

    if result["container"]:
        n = save_container_to_sqlite(result["container"], conn)
        save_to_json(result["container"], JSON_CONTAINER_PATH)
        logger.info("Saved %d container records: SQLite=%d, JSON=%s", len(result["container"]), n, JSON_CONTAINER_PATH)

    if result["news"]:
        n = save_news_to_sqlite(result["news"], conn)
        save_to_json(result["news"], JSON_NEWS_PATH)
        logger.info("Saved %d news articles: SQLite=%d, JSON=%s", len(result["news"]), n, JSON_NEWS_PATH)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_chinaports_data()
    print(f"\n{'=' * 70}")
    print(f"Total throughput records: {len(results['throughput'])}")
    print(f"Total container records: {len(results['container'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON (throughput): {JSON_THROUGHPUT_PATH}")
    print(f"JSON (container): {JSON_CONTAINER_PATH}")
    print(f"JSON (news): {JSON_NEWS_PATH}")
    print(f"{'=' * 70}")

    if results["throughput"]:
        print(f"\nPort Throughput ({len(results['throughput'])} records):")
        for stat in results["throughput"][:10]:
            print(f"  {stat.get('port_name', 'N/A'):>10s} | "
                  f"{stat.get('value', 'N/A'):>10} {stat.get('unit', '')}")

    if results["container"]:
        print(f"\nContainer Traffic ({len(results['container'])} records):")
        for stat in results["container"][:10]:
            print(f"  {stat.get('port_name', 'N/A'):>10s} | "
                  f"{stat.get('teu_value', 'N/A'):>10} {stat.get('teu_unit', '')}")

    if results["news"]:
        print(f"\nRecent News ({len(results['news'])} articles):")
        for article in results["news"][:5]:
            print(f"  [{article.get('date', 'N/A')}] {article.get('title', 'N/A')[:60]}")
