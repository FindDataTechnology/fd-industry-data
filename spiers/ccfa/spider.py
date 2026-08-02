#!/usr/bin/env python3
"""
China Chain Store & Franchise Association Spider
中国连锁经营协会数据爬虫

Target: http://www.ccfa.org.cn/
Data: Retail industry statistics, chain store data, franchise information, market trends
Focus: Chain store revenue, franchise counts, retail market size, top chain rankings

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts chain store rankings and franchise data
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
logger = logging.getLogger("ccfa")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "ccfa.db"
JSON_CHAIN_STATS_PATH = OUTPUT_DIR / "ccfa_chain_statistics.json"
JSON_FRANCHISE_PATH = OUTPUT_DIR / "ccfa_franchise.json"
JSON_RANKINGS_PATH = OUTPUT_DIR / "ccfa_rankings.json"
JSON_NEWS_PATH = OUTPUT_DIR / "ccfa_news.json"

TARGET_URLS = {
    "chain_stats": [
        "http://www.ccfa.org.cn/zhishu/lszs/",
        "http://www.ccfa.org.cn/zhishu/lshy/",
    ],
    "franchise": [
        "http://www.ccfa.org.cn/jiameng/jmbs/",
        "http://www.ccfa.org.cn/jiameng/jmtj/",
    ],
    "rankings": [
        "http://www.ccfa.org.cn/top100/",
        "http://www.ccfa.org.cn/zhishu/bq100/",
    ],
    "news": [
        "http://www.ccfa.org.cn/xwzx/hynews/",
        "http://www.ccfa.org.cn/xwzx/zhxw/",
    ],
}

RETAIL_CATEGORIES = {
    "超市": "supermarket",
    "便利店": "convenience_store",
    "百货": "department_store",
    "专业店": "specialty_store",
    "购物中心": "shopping_mall",
    "餐饮": "restaurant",
    "酒店": "hotel",
    "药店": "pharmacy",
    "家电": "electronics",
    "服装": "apparel",
}


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chain_statistics (
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
        CREATE TABLE IF NOT EXISTS franchise_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            brand_name TEXT,
            industry TEXT,
            store_count INTEGER,
            franchise_fee REAL,
            total_investment REAL,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, brand_name, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chain_rankings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER NOT NULL,
            rank INTEGER,
            company_name TEXT,
            revenue REAL,
            revenue_unit TEXT DEFAULT '亿元',
            store_count INTEGER,
            industry TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(year, rank, company_name, source_url)
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


def identify_retail_category(text: str) -> dict | None:
    for cn_name, en_name in RETAIL_CATEGORIES.items():
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


def extract_chain_statistics(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["连锁", "零售", "门店", "销售", "营收", "增长", "亿元", "万亿"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            cell_text = " ".join(cells)
            cat_info = identify_retail_category(cell_text)
            if not cat_info:
                cat_info = {"category": "general", "category_cn": "综合零售"}

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
                        item["unit"] = "万家"
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

            if "销售" in header_text or "营收" in cell_text:
                item["indicator_name"] = "revenue"
            elif "门店" in header_text or "店" in cell_text:
                item["indicator_name"] = "store_count"
            elif "加盟" in header_text:
                item["indicator_name"] = "franchise_count"
            else:
                item["indicator_name"] = "general"

            if item.get("value"):
                items.append(item)

    return items


def extract_franchise_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["品牌", "加盟", "门店", "费用", "投资"]):
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
                    item["brand_name"] = cell
                elif "门店" in header_text or "店" in header_text:
                    nums = re.findall(r"[\d,]+", cell.replace(",", ""))
                    if nums and "store_count" not in item:
                        try:
                            item["store_count"] = int(nums[0])
                        except ValueError:
                            pass
                elif "费" in cell or "投资" in cell:
                    nums = re.findall(r"[\d,]+\.?\d*", cell.replace(",", ""))
                    if nums:
                        try:
                            val = float(nums[0])
                            if "加盟费" in header_text or "费" in cell:
                                item["franchise_fee"] = val
                            else:
                                item["total_investment"] = val
                        except ValueError:
                            pass

            cat_info = identify_retail_category(" ".join(cells))
            if cat_info:
                item["industry"] = cat_info["category"]

            if item.get("brand_name"):
                items.append(item)

    return items


def extract_rankings(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["排名", "企业", "营收", "销售", "门店"]):
            continue

        year_match = re.search(r"(\d{4})", header_text)
        year = int(year_match.group(1)) if year_match else datetime.now().year

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            item = {
                "year": year,
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            rank_match = re.match(r"(\d+)", cells[0])
            if rank_match:
                item["rank"] = int(rank_match.group(1))
                item["company_name"] = cells[1] if len(cells) > 1 else ""
            else:
                item["rank"] = None
                item["company_name"] = cells[0]

            for cell in cells:
                nums = re.findall(r"[\d,]+\.?\d*", cell.replace(",", ""))
                if nums:
                    try:
                        val = float(nums[0])
                        if "营收" in header_text or "销售" in header_text:
                            if "revenue" not in item:
                                item["revenue"] = val
                        elif "门店" in header_text:
                            if "store_count" not in item:
                                item["store_count"] = int(val)
                    except ValueError:
                        pass

            cat_info = identify_retail_category(" ".join(cells))
            if cat_info:
                item["industry"] = cat_info["category"]

            if item.get("company_name"):
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
        elif "连锁" in title:
            category = "chain_store"
        elif "加盟" in title:
            category = "franchise"

        items.append({
            "date": date,
            "title": title,
            "content": content[:500] if content else "",
            "category": category,
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def save_chain_stats_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO chain_statistics
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


def save_franchise_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO franchise_data
                (period, brand_name, industry, store_count, franchise_fee, total_investment, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("brand_name"), item.get("industry"),
                    item.get("store_count"), item.get("franchise_fee"),
                    item.get("total_investment"), item.get("source_url"), item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_rankings_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO chain_rankings
                (year, rank, company_name, revenue, revenue_unit, store_count, industry, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("year"), item.get("rank"), item.get("company_name"),
                    item.get("revenue"), item.get("revenue_unit", "亿元"),
                    item.get("store_count"), item.get("industry"),
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


def get_ccfa_data(
    include_stats: bool = True,
    include_franchise: bool = True,
    include_rankings: bool = True,
    include_news: bool = True,
) -> dict:
    """Fetch data from China Chain Store & Franchise Association.

    Args:
        include_stats: Whether to fetch chain store statistics.
        include_franchise: Whether to fetch franchise data.
        include_rankings: Whether to fetch chain store rankings.
        include_news: Whether to fetch industry news.

    Returns:
        Dict with keys: 'chain_stats', 'franchise', 'rankings', 'news'.
    """
    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "chain_stats": [],
        "franchise": [],
        "rankings": [],
        "news": [],
    }

    logger.info("Starting CCFA spider")

    if include_stats:
        for url in TARGET_URLS["chain_stats"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_chain_statistics(page["html"], url)
                if items:
                    result["chain_stats"].extend(items)
                    logger.info("  Extracted %d chain statistics records", len(items))
            time.sleep(2)

    if include_franchise:
        for url in TARGET_URLS["franchise"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_franchise_data(page["html"], url)
                if items:
                    result["franchise"].extend(items)
                    logger.info("  Extracted %d franchise records", len(items))
            time.sleep(2)

    if include_rankings:
        for url in TARGET_URLS["rankings"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_rankings(page["html"], url)
                if items:
                    result["rankings"].extend(items)
                    logger.info("  Extracted %d ranking records", len(items))
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

    if result["chain_stats"]:
        n = save_chain_stats_to_sqlite(result["chain_stats"], conn)
        save_to_json(result["chain_stats"], JSON_CHAIN_STATS_PATH)
        logger.info("Saved %d chain stats: SQLite=%d, JSON=%s", len(result["chain_stats"]), n, JSON_CHAIN_STATS_PATH)

    if result["franchise"]:
        n = save_franchise_to_sqlite(result["franchise"], conn)
        save_to_json(result["franchise"], JSON_FRANCHISE_PATH)
        logger.info("Saved %d franchise records: SQLite=%d, JSON=%s", len(result["franchise"]), n, JSON_FRANCHISE_PATH)

    if result["rankings"]:
        n = save_rankings_to_sqlite(result["rankings"], conn)
        save_to_json(result["rankings"], JSON_RANKINGS_PATH)
        logger.info("Saved %d rankings: SQLite=%d, JSON=%s", len(result["rankings"]), n, JSON_RANKINGS_PATH)

    if result["news"]:
        n = save_news_to_sqlite(result["news"], conn)
        save_to_json(result["news"], JSON_NEWS_PATH)
        logger.info("Saved %d news articles: SQLite=%d, JSON=%s", len(result["news"]), n, JSON_NEWS_PATH)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_ccfa_data()
    print(f"\n{'=' * 70}")
    print(f"Total chain statistics: {len(results['chain_stats'])}")
    print(f"Total franchise records: {len(results['franchise'])}")
    print(f"Total rankings: {len(results['rankings'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON (stats): {JSON_CHAIN_STATS_PATH}")
    print(f"JSON (franchise): {JSON_FRANCHISE_PATH}")
    print(f"JSON (rankings): {JSON_RANKINGS_PATH}")
    print(f"JSON (news): {JSON_NEWS_PATH}")
    print(f"{'=' * 70}")

    if results["chain_stats"]:
        print(f"\nChain Statistics ({len(results['chain_stats'])} records):")
        for stat in results["chain_stats"][:10]:
            print(f"  {stat.get('period', 'N/A'):>12s} | {stat.get('category_cn', 'N/A'):>8s} | "
                  f"{stat.get('value', 'N/A'):>12} {stat.get('unit', '')}")

    if results["rankings"]:
        print(f"\nTop Rankings ({len(results['rankings'])} records):")
        for r in results["rankings"][:10]:
            print(f"  #{r.get('rank', 'N/A'):>3} | {r.get('company_name', 'N/A')[:30]:>30s} | "
                  f"Revenue: {r.get('revenue', 'N/A')} {r.get('revenue_unit', '')}")
