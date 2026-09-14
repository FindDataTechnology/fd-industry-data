#!/usr/bin/env python3
"""
China Brand Association Spider
中国品牌促进会数据爬虫

Target: http://www.chinabrands.org/
Data: Brand value data, brand rankings, market share data, consumer preference
Focus: Brand valuation, top 500 brands, industry brand rankings, consumer brand preference

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts brand rankings and valuation data
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
logger = logging.getLogger("chinabrands")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "chinabrands.db"
JSON_BRAND_RANKINGS_PATH = OUTPUT_DIR / "chinabrands_rankings.json"
JSON_BRAND_VALUE_PATH = OUTPUT_DIR / "chinabrands_valuation.json"
JSON_MARKET_SHARE_PATH = OUTPUT_DIR / "chinabrands_market_share.json"
JSON_NEWS_PATH = OUTPUT_DIR / "chinabrands_news.json"

TARGET_URLS = {
    "rankings": [
        "http://www.chinabrands.org/rankings/",
        "http://www.chinabrands.org/top500/",
    ],
    "valuation": [
        "http://www.chinabrands.org/valuation/",
        "http://www.chinabrands.org/brand-value/",
    ],
    "market_share": [
        "http://www.chinabrands.org/market/",
        "http://www.chinabrands.org/consumer/",
    ],
    "news": [
        "http://www.chinabrands.org/news/",
        "http://www.chinabrands.org/events/",
    ],
}

BRAND_INDUSTRIES = {
    "科技": "technology",
    "互联网": "internet",
    "金融": "finance",
    "消费品": "consumer_goods",
    "食品": "food_beverage",
    "服装": "apparel",
    "家电": "electronics",
    "汽车": "automobile",
    "医药": "pharmaceutical",
    "房地产": "real_estate",
    "教育": "education",
    "传媒": "media",
    "能源": "energy",
    "农业": "agriculture",
}


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS brand_rankings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER NOT NULL,
            rank INTEGER,
            brand_name TEXT,
            brand_name_cn TEXT,
            industry TEXT,
            industry_cn TEXT,
            score REAL,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(year, rank, brand_name, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS brand_valuation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER NOT NULL,
            brand_name TEXT,
            brand_name_cn TEXT,
            valuation REAL,
            valuation_unit TEXT DEFAULT '亿元',
            industry TEXT,
            industry_cn TEXT,
            yoy_change REAL,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(year, brand_name, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_share (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            brand_name TEXT,
            brand_name_cn TEXT,
            industry TEXT,
            market_share REAL,
            market_share_unit TEXT DEFAULT '%',
            revenue REAL,
            revenue_unit TEXT DEFAULT '亿元',
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, brand_name, industry, source_url)
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


def identify_brand_industry(text: str) -> dict | None:
    for cn_name, en_name in BRAND_INDUSTRIES.items():
        if cn_name in text:
            return {"industry": en_name, "industry_cn": cn_name}
    return None


def extract_year(text: str) -> int:
    match = re.search(r"(\d{4})", text)
    if match:
        return int(match.group(1))
    return datetime.now().year


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


def extract_brand_rankings(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["排名", "品牌", "得分", "评分", "rank", "brand", "score"]):
            continue

        year = extract_year(header_text)

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            item = {
                "year": year,
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            rank_match = re.match(r"(\d+)", cells[0])
            if rank_match:
                item["rank"] = int(rank_match.group(1))
                name_idx = 1
            else:
                item["rank"] = None
                name_idx = 0

            if len(cells) > name_idx:
                name = cells[name_idx]
                if re.search(r"[a-zA-Z]", name):
                    item["brand_name"] = name.split()[0]
                    cn_match = re.search(r"[\u4e00-\u9fa5]+", name)
                    if cn_match:
                        item["brand_name_cn"] = cn_match.group()
                else:
                    item["brand_name_cn"] = name
                    item["brand_name"] = name

            for cell in cells[name_idx + 1:]:
                ind_info = identify_brand_industry(cell)
                if ind_info:
                    item["industry"] = ind_info["industry"]
                    item["industry_cn"] = ind_info["industry_cn"]
                    continue

                nums = re.findall(r"[\d,]+\.?\d*", cell.replace(",", ""))
                if nums:
                    try:
                        val = float(nums[0])
                        if "score" not in item:
                            item["score"] = val
                    except ValueError:
                        pass

            if item.get("brand_name") or item.get("brand_name_cn"):
                items.append(item)

    return items


def extract_brand_valuation(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["价值", "估值", "品牌价", "valuation", "value"]):
            continue

        year = extract_year(header_text)

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            cell_text = " ".join(cells)
            item = {
                "year": year,
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            for i, cell in enumerate(cells):
                if i == 0 and not re.search(r"\d", cell):
                    if re.search(r"[a-zA-Z]", cell):
                        item["brand_name"] = cell.split()[0]
                    else:
                        item["brand_name_cn"] = cell
                        item["brand_name"] = cell
                else:
                    ind_info = identify_brand_industry(cell)
                    if ind_info:
                        item["industry"] = ind_info["industry"]
                        item["industry_cn"] = ind_info["industry_cn"]
                        continue

                    nums = re.findall(r"[\d,]+\.?\d*", cell.replace(",", ""))
                    if nums:
                        try:
                            val = float(nums[0])
                            if "亿" in cell or "万亿" in cell:
                                if "valuation" not in item:
                                    item["valuation"] = val
                                    if "万亿" in cell:
                                        item["valuation"] = val * 10000
                                        item["valuation_unit"] = "亿元"
                                    else:
                                        item["valuation_unit"] = "亿元"
                            elif "同比" in cell or "增长" in cell:
                                item["yoy_change"] = val
                        except ValueError:
                            pass

            if item.get("brand_name") or item.get("brand_name_cn"):
                items.append(item)

    return items


def extract_market_share(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["份额", "占比", "市场", "销量", "market", "share"]):
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

            for i, cell in enumerate(cells):
                if i == 0 and not re.search(r"\d", cell):
                    if re.search(r"[a-zA-Z]", cell):
                        item["brand_name"] = cell.split()[0]
                    else:
                        item["brand_name_cn"] = cell
                        item["brand_name"] = cell
                else:
                    ind_info = identify_brand_industry(cell)
                    if ind_info:
                        item["industry"] = ind_info["industry"]
                        continue

                    nums = re.findall(r"[\d,]+\.?\d*", cell.replace(",", ""))
                    if nums:
                        try:
                            val = float(nums[0])
                            if "%" in cell or "份额" in header_text or "占比" in header_text:
                                item["market_share"] = val
                            elif "亿" in cell or "万" in cell:
                                item["revenue"] = val
                                if "亿" in cell:
                                    item["revenue_unit"] = "亿元"
                                else:
                                    item["revenue_unit"] = "万元"
                        except ValueError:
                            pass

            if item.get("brand_name") or item.get("brand_name_cn"):
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
        if "品牌" in title:
            category = "brand"
        elif "排名" in title or "榜单" in title:
            category = "ranking"
        elif "价值" in title:
            category = "valuation"
        elif "报告" in title:
            category = "report"

        items.append({
            "date": date,
            "title": title,
            "content": content[:500] if content else "",
            "category": category,
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def save_rankings_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO brand_rankings
                (year, rank, brand_name, brand_name_cn, industry, industry_cn, score, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("year"), item.get("rank"),
                    item.get("brand_name"), item.get("brand_name_cn"),
                    item.get("industry"), item.get("industry_cn"),
                    item.get("score"), item.get("source_url"), item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_valuation_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO brand_valuation
                (year, brand_name, brand_name_cn, valuation, valuation_unit, industry, industry_cn, yoy_change, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("year"), item.get("brand_name"), item.get("brand_name_cn"),
                    item.get("valuation"), item.get("valuation_unit", "亿元"),
                    item.get("industry"), item.get("industry_cn"),
                    item.get("yoy_change"), item.get("source_url"), item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_market_share_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO market_share
                (period, brand_name, brand_name_cn, industry, market_share, market_share_unit, revenue, revenue_unit, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("brand_name"), item.get("brand_name_cn"),
                    item.get("industry"), item.get("market_share"), item.get("market_share_unit", "%"),
                    item.get("revenue"), item.get("revenue_unit", "亿元"),
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


def get_chinabrands_data(
    include_rankings: bool = True,
    include_valuation: bool = True,
    include_market_share: bool = True,
    include_news: bool = True,
) -> dict:
    """Fetch data from China Brand Association.

    Args:
        include_rankings: Whether to fetch brand rankings.
        include_valuation: Whether to fetch brand valuation data.
        include_market_share: Whether to fetch market share data.
        include_news: Whether to fetch brand news.

    Returns:
        Dict with keys: 'rankings', 'valuation', 'market_share', 'news'.
    """
    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "rankings": [],
        "valuation": [],
        "market_share": [],
        "news": [],
    }

    logger.info("Starting China Brands spider")

    if include_rankings:
        for url in TARGET_URLS["rankings"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_brand_rankings(page["html"], url)
                if items:
                    result["rankings"].extend(items)
                    logger.info("  Extracted %d ranking records", len(items))
            time.sleep(2)

    if include_valuation:
        for url in TARGET_URLS["valuation"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_brand_valuation(page["html"], url)
                if items:
                    result["valuation"].extend(items)
                    logger.info("  Extracted %d valuation records", len(items))
            time.sleep(2)

    if include_market_share:
        for url in TARGET_URLS["market_share"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_market_share(page["html"], url)
                if items:
                    result["market_share"].extend(items)
                    logger.info("  Extracted %d market share records", len(items))
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

    if result["rankings"]:
        n = save_rankings_to_sqlite(result["rankings"], conn)
        save_to_json(result["rankings"], JSON_BRAND_RANKINGS_PATH)
        logger.info("Saved %d rankings: SQLite=%d, JSON=%s", len(result["rankings"]), n, JSON_BRAND_RANKINGS_PATH)

    if result["valuation"]:
        n = save_valuation_to_sqlite(result["valuation"], conn)
        save_to_json(result["valuation"], JSON_BRAND_VALUE_PATH)
        logger.info("Saved %d valuations: SQLite=%d, JSON=%s", len(result["valuation"]), n, JSON_BRAND_VALUE_PATH)

    if result["market_share"]:
        n = save_market_share_to_sqlite(result["market_share"], conn)
        save_to_json(result["market_share"], JSON_MARKET_SHARE_PATH)
        logger.info("Saved %d market share: SQLite=%d, JSON=%s", len(result["market_share"]), n, JSON_MARKET_SHARE_PATH)

    if result["news"]:
        n = save_news_to_sqlite(result["news"], conn)
        save_to_json(result["news"], JSON_NEWS_PATH)
        logger.info("Saved %d news articles: SQLite=%d, JSON=%s", len(result["news"]), n, JSON_NEWS_PATH)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_chinabrands_data()
    print(f"\n{'=' * 70}")
    print(f"Total brand rankings: {len(results['rankings'])}")
    print(f"Total brand valuations: {len(results['valuation'])}")
    print(f"Total market share records: {len(results['market_share'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON (rankings): {JSON_BRAND_RANKINGS_PATH}")
    print(f"JSON (valuation): {JSON_BRAND_VALUE_PATH}")
    print(f"JSON (market share): {JSON_MARKET_SHARE_PATH}")
    print(f"JSON (news): {JSON_NEWS_PATH}")
    print(f"{'=' * 70}")

    if results["rankings"]:
        print(f"\nBrand Rankings ({len(results['rankings'])} records):")
        for r in results["rankings"][:10]:
            name = r.get("brand_name_cn") or r.get("brand_name", "N/A")
            print(f"  #{r.get('rank', 'N/A'):>3} | {name[:25]:>25s} | "
                  f"Score: {r.get('score', 'N/A')} | {r.get('industry_cn', 'N/A')}")

    if results["valuation"]:
        print(f"\nBrand Valuations ({len(results['valuation'])} records):")
        for v in results["valuation"][:10]:
            name = v.get("brand_name_cn") or v.get("brand_name", "N/A")
            print(f"  {name[:25]:>25s} | Value: {v.get('valuation', 'N/A')} {v.get('valuation_unit', '')}")
