#!/usr/bin/env python3
"""
China Consumer Association Spider
中国消费者协会数据爬虫

Target: http://www.cca.org.cn/
Data: Consumer complaint data, market monitoring, product quality reports, consumer rights
Focus: Complaint statistics, product recalls, consumer satisfaction, market supervision

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts complaint statistics and product quality data
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
logger = logging.getLogger("cca")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "cca.db"
JSON_COMPLAINTS_PATH = OUTPUT_DIR / "cca_complaints.json"
JSON_QUALITY_PATH = OUTPUT_DIR / "cca_product_quality.json"
JSON_SATISFACTION_PATH = OUTPUT_DIR / "cca_satisfaction.json"
JSON_NEWS_PATH = OUTPUT_DIR / "cca_news.json"

TARGET_URLS = {
    "complaints": [
        "http://www.cca.org.cn/ts/dynamic/index.html",
        "http://www.cca.org.cn/zxbs/index.html",
    ],
    "quality": [
        "http://www.cca.org.cn/jdbd/index.html",
        "http://www.cca.org.cn/bjcs/index.html",
    ],
    "satisfaction": [
        "http://www.cca.org.cn/dcbg/index.html",
        "http://www.cca.org.cn/xfpj/index.html",
    ],
    "news": [
        "http://www.cca.org.cn/xw/index.html",
        "http://www.cca.org.cn/zyxw/index.html",
    ],
}

COMPLAINT_CATEGORIES = {
    "商品": "goods",
    "服务": "service",
    "食品": "food",
    "药品": "pharmaceutical",
    "家电": "home_appliance",
    "汽车": "automobile",
    "房产": "real_estate",
    "金融": "finance",
    "教育": "education",
    "网络": "internet",
    "通信": "telecom",
    "旅游": "tourism",
    "保险": "insurance",
    "快递": "express_delivery",
}


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS complaint_statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            category TEXT,
            category_cn TEXT,
            complaint_count INTEGER,
            resolved_count INTEGER,
            resolution_rate REAL,
            amount_involved REAL,
            amount_unit TEXT DEFAULT '万元',
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, category, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS product_quality (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            product_name TEXT,
            brand TEXT,
            manufacturer TEXT,
            issue_type TEXT,
            severity TEXT,
            recall_status TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, product_name, brand, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS satisfaction_surveys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            industry TEXT,
            industry_cn TEXT,
            score REAL,
            sample_size INTEGER,
            ranking INTEGER,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(period, industry, source_url)
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


def identify_complaint_category(text: str) -> dict | None:
    for cn_name, en_name in COMPLAINT_CATEGORIES.items():
        if cn_name in text:
            return {"category": en_name, "category_cn": cn_name}
    return None


def extract_period(text: str) -> str:
    patterns = [
        r"(\d{4}年\d{1,2}月)",
        r"(\d{4}-\d{2})",
        r"(\d{4}年第[一二三四]季度)",
        r"(\d{4}年)",
        r"(\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return datetime.now().strftime("%Y-%m")


def extract_complaint_statistics(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["投诉", "举报", "咨询", "受理", "解决", "件", "万元"]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            cell_text = " ".join(cells)
            cat_info = identify_complaint_category(cell_text)
            if not cat_info:
                cat_info = {"category": "general", "category_cn": "综合投诉"}

            item = {
                "period": extract_period(cell_text),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(cat_info)

            for cell in cells:
                nums = re.findall(r"[\d,]+\.?\d*", cell.replace(",", ""))
                if not nums:
                    continue
                try:
                    val = float(nums[0])
                except ValueError:
                    continue

                if "投诉" in header_text or "件" in header_text:
                    if "complaint_count" not in item:
                        item["complaint_count"] = int(val)
                    elif "解决" in header_text or "处理" in header_text:
                        item["resolved_count"] = int(val)
                elif "率" in cell or "解决率" in header_text:
                    item["resolution_rate"] = val
                elif "金额" in header_text or "万" in cell or "亿" in cell:
                    item["amount_involved"] = val
                    if "亿" in cell:
                        item["amount_unit"] = "亿元"
                    else:
                        item["amount_unit"] = "万元"

            if item.get("complaint_count") or item.get("amount_involved"):
                items.append(item)

    return items


def extract_product_quality(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for article in sel.css("article, .news-item, .article-item, .list-item, li, tr"):
        title_elem = article.css("h1, h2, h3, h4, .title, a, td:first-child")
        if not title_elem:
            continue

        title = title_elem[0].text.strip() if title_elem else ""
        if not title or len(title) < 5:
            continue

        if not any(kw in title for kw in ["不合格", "召回", "质量", "检测", "抽检", "问题"]):
            continue

        date_elem = article.css("time, .date, .publish-date, span")
        date = date_elem[0].text.strip() if date_elem else ""
        date = extract_period(date)

        item = {
            "date": date,
            "product_name": title[:100],
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }

        if "不合格" in title:
            item["issue_type"] = "substandard"
            item["severity"] = "medium"
        elif "召回" in title:
            item["issue_type"] = "recall"
            item["severity"] = "high"
            item["recall_status"] = "recalled"
        elif "问题" in title:
            item["issue_type"] = "quality_issue"
            item["severity"] = "medium"
        else:
            item["issue_type"] = "inspection"
            item["severity"] = "low"

        brand_patterns = [
            r"([\u4e00-\u9fa5]{2,6})(牌|品牌)",
            r"(品牌[：:])\s*([\u4e00-\u9fa5]+)",
        ]
        for pattern in brand_patterns:
            match = re.search(pattern, title)
            if match:
                item["brand"] = match.group(1) if "品牌" not in pattern else match.group(2)
                break

        items.append(item)

    return items


def extract_satisfaction_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in ["满意度", "评分", "评价", "得分", "排名"]):
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
                    cat_info = identify_complaint_category(cell)
                    if cat_info:
                        item["industry"] = cat_info["category"]
                        item["industry_cn"] = cat_info["category_cn"]
                    else:
                        item["industry_cn"] = cell
                        item["industry"] = "other"

                nums = re.findall(r"[\d,]+\.?\d*", cell.replace(",", ""))
                if nums:
                    try:
                        val = float(nums[0])
                        if "分" in header_text or val <= 10:
                            item["score"] = val
                        elif "样本" in header_text or "调查" in header_text:
                            item["sample_size"] = int(val)
                        elif "排名" in header_text:
                            item["ranking"] = int(val)
                    except ValueError:
                        pass

            if item.get("score") or item.get("industry_cn"):
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
        elif "投诉" in title:
            category = "complaint"
        elif "质量" in title or "检测" in title:
            category = "quality"
        elif "召回" in title:
            category = "recall"

        items.append({
            "date": date,
            "title": title,
            "content": content[:500] if content else "",
            "category": category,
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def save_complaints_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO complaint_statistics
                (period, category, category_cn, complaint_count, resolved_count, resolution_rate,
                 amount_involved, amount_unit, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("category"), item.get("category_cn"),
                    item.get("complaint_count"), item.get("resolved_count"),
                    item.get("resolution_rate"), item.get("amount_involved"),
                    item.get("amount_unit", "万元"), item.get("source_url"), item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_quality_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO product_quality
                (date, product_name, brand, manufacturer, issue_type, severity, recall_status, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("date"), item.get("product_name"), item.get("brand"),
                    item.get("manufacturer"), item.get("issue_type"), item.get("severity"),
                    item.get("recall_status"), item.get("source_url"), item.get("scraped_at"),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_satisfaction_to_sqlite(items: list[dict], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO satisfaction_surveys
                (period, industry, industry_cn, score, sample_size, ranking, source_url, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("period"), item.get("industry"), item.get("industry_cn"),
                    item.get("score"), item.get("sample_size"), item.get("ranking"),
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


def get_cca_data(
    include_complaints: bool = True,
    include_quality: bool = True,
    include_satisfaction: bool = True,
    include_news: bool = True,
) -> dict:
    """Fetch data from China Consumer Association.

    Args:
        include_complaints: Whether to fetch complaint statistics.
        include_quality: Whether to fetch product quality data.
        include_satisfaction: Whether to fetch satisfaction survey data.
        include_news: Whether to fetch consumer news.

    Returns:
        Dict with keys: 'complaints', 'quality', 'satisfaction', 'news'.
    """
    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "complaints": [],
        "quality": [],
        "satisfaction": [],
        "news": [],
    }

    logger.info("Starting CCA spider")

    if include_complaints:
        for url in TARGET_URLS["complaints"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_complaint_statistics(page["html"], url)
                if items:
                    result["complaints"].extend(items)
                    logger.info("  Extracted %d complaint records", len(items))
            time.sleep(2)

    if include_quality:
        for url in TARGET_URLS["quality"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_product_quality(page["html"], url)
                if items:
                    result["quality"].extend(items)
                    logger.info("  Extracted %d quality records", len(items))
            time.sleep(2)

    if include_satisfaction:
        for url in TARGET_URLS["satisfaction"]:
            page = fetch_page(url, fetcher)
            if page:
                items = extract_satisfaction_data(page["html"], url)
                if items:
                    result["satisfaction"].extend(items)
                    logger.info("  Extracted %d satisfaction records", len(items))
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

    if result["complaints"]:
        n = save_complaints_to_sqlite(result["complaints"], conn)
        save_to_json(result["complaints"], JSON_COMPLAINTS_PATH)
        logger.info("Saved %d complaints: SQLite=%d, JSON=%s", len(result["complaints"]), n, JSON_COMPLAINTS_PATH)

    if result["quality"]:
        n = save_quality_to_sqlite(result["quality"], conn)
        save_to_json(result["quality"], JSON_QUALITY_PATH)
        logger.info("Saved %d quality records: SQLite=%d, JSON=%s", len(result["quality"]), n, JSON_QUALITY_PATH)

    if result["satisfaction"]:
        n = save_satisfaction_to_sqlite(result["satisfaction"], conn)
        save_to_json(result["satisfaction"], JSON_SATISFACTION_PATH)
        logger.info("Saved %d satisfaction records: SQLite=%d, JSON=%s", len(result["satisfaction"]), n, JSON_SATISFACTION_PATH)

    if result["news"]:
        n = save_news_to_sqlite(result["news"], conn)
        save_to_json(result["news"], JSON_NEWS_PATH)
        logger.info("Saved %d news articles: SQLite=%d, JSON=%s", len(result["news"]), n, JSON_NEWS_PATH)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_cca_data()
    print(f"\n{'=' * 70}")
    print(f"Total complaint records: {len(results['complaints'])}")
    print(f"Total quality records: {len(results['quality'])}")
    print(f"Total satisfaction records: {len(results['satisfaction'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON (complaints): {JSON_COMPLAINTS_PATH}")
    print(f"JSON (quality): {JSON_QUALITY_PATH}")
    print(f"JSON (satisfaction): {JSON_SATISFACTION_PATH}")
    print(f"JSON (news): {JSON_NEWS_PATH}")
    print(f"{'=' * 70}")

    if results["complaints"]:
        print(f"\nComplaint Statistics ({len(results['complaints'])} records):")
        for c in results["complaints"][:10]:
            print(f"  {c.get('period', 'N/A'):>15s} | {c.get('category_cn', 'N/A'):>8s} | "
                  f"Complaints: {c.get('complaint_count', 'N/A')} | "
                  f"Amount: {c.get('amount_involved', 'N/A')} {c.get('amount_unit', '')}")
