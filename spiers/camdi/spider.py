#!/usr/bin/env python3
"""
CAMDI Spider - 中国医疗器械行业协会数据爬虫

Target: http://www.camdi.org/
Data: Medical device production data, market statistics, technology trends, regulatory information

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts medical device registration and market data
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
logger = logging.getLogger("camdi")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "camdi.db"
JSON_PRODUCTION_PATH = OUTPUT_DIR / "camdi_production.json"
JSON_MARKET_PATH = OUTPUT_DIR / "camdi_market.json"
JSON_REGULATORY_PATH = OUTPUT_DIR / "camdi_regulatory.json"
JSON_NEWS_PATH = OUTPUT_DIR / "camdi_news.json"

CATEGORIES = {
    "production": {
        "cn_name": "医疗器械生产数据",
        "urls": [
            "http://www.camdi.org/index/statistics/production.html",
            "http://www.camdi.org/index/statistics/output.html",
            "http://www.camdi.org/index/dataservice/devicedata.html",
        ],
    },
    "market": {
        "cn_name": "市场统计数据",
        "urls": [
            "http://www.camdi.org/index/statistics/market.html",
            "http://www.camdi.org/index/statistics/trade.html",
            "http://www.camdi.org/index/statistics/export_import.html",
        ],
    },
    "regulatory": {
        "cn_name": "注册审批信息",
        "urls": [
            "http://www.camdi.org/index/regulatory/registration.html",
            "http://www.camdi.org/index/regulatory/approval.html",
            "http://www.camdi.org/index/regulatory/standards.html",
        ],
    },
    "news": {
        "cn_name": "行业新闻",
        "urls": [
            "http://www.camdi.org/index/news/industry.html",
            "http://www.camdi.org/index/news/policy.html",
            "http://www.camdi.org/index/news/technology.html",
        ],
    },
}

DEVICE_CATEGORIES = {
    "imaging": {"cn_name": "医学影像设备", "unit": "台"},
    "ivd": {"cn_name": "体外诊断", "unit": "亿元"},
    "high_value_consumable": {"cn_name": "高值耗材", "unit": "亿元"},
    "low_value_consumable": {"cn_name": "低值耗材", "unit": "亿元"},
    "in_vitro_diagnostics": {"cn_name": "体外诊断试剂", "unit": "亿元"},
    "surgical_equipment": {"cn_name": "手术设备", "unit": "台"},
    "dental_equipment": {"cn_name": "口腔设备", "unit": "台"},
    "ophthalmic_equipment": {"cn_name": "眼科设备", "unit": "台"},
    "rehabilitation": {"cn_name": "康复器械", "unit": "台"},
    "home_medical": {"cn_name": "家用医疗器械", "unit": "亿元"},
    "medical_ai": {"cn_name": "医疗AI", "unit": "亿元"},
}


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS device_production (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            device_category TEXT NOT NULL,
            device_cn TEXT,
            production REAL,
            unit TEXT,
            yoy_change REAL,
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, device_category, region, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            device_category TEXT NOT NULL,
            device_cn TEXT,
            market_size REAL,
            growth_rate REAL,
            export_value REAL,
            import_value REAL,
            unit TEXT DEFAULT '亿元',
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, device_category, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS regulatory_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            title TEXT NOT NULL,
            category TEXT,
            device_type TEXT,
            approval_number TEXT,
            content TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            title TEXT NOT NULL,
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


def extract_production_tables(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "产量", "生产", "台", "亿元", "同比", "增长",
            "影像", "诊断", "耗材", "设备", "器械",
            "output", "production", "device",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            device_key = identify_device(" ".join(cells))
            numeric_values = extract_numeric_values(cells)

            item = {
                "date": extract_date(cells[0]),
                "device_category": device_key,
                "device_cn": DEVICE_CATEGORIES.get(device_key, {}).get("cn_name", ""),
                "unit": DEVICE_CATEGORIES.get(device_key, {}).get("unit", detect_unit(" ".join(cells))),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            if numeric_values:
                item["production"] = numeric_values.get("primary")
                item["yoy_change"] = numeric_values.get("yoy")
                item["region"] = detect_region(" ".join(cells))

            if item.get("production"):
                items.append(item)

    return items


def extract_market_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "市场", "规模", "增长", "出口", "进口", "贸易",
            "market", "size", "growth", "export", "import",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            device_key = identify_device(" ".join(cells))
            numeric_values = extract_market_values(cells)

            item = {
                "date": extract_date(cells[0]),
                "device_category": device_key,
                "device_cn": DEVICE_CATEGORIES.get(device_key, {}).get("cn_name", ""),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(numeric_values)

            if item.get("market_size") or item.get("export_value"):
                items.append(item)

    return items


def extract_regulatory_info(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for row in sel.css("table tr, .regulatory-item, .approval-list li"):
        cells = row.css("td")
        if not cells or len(cells) < 2:
            link = row.css("a")
            if not link:
                continue
            title = link[0].text.strip()
            if not title or len(title) < 3:
                continue

            href = link[0].attrib.get("href", "")
            if href and not href.startswith("http"):
                href = "http://www.camdi.org" + href

            date_text = ""
            date_elem = row.css("span, .date, time")
            if date_elem:
                date_text = date_elem[0].text.strip()

            category = detect_regulatory_category(title)
            items.append({
                "date": date_text or extract_date(title),
                "title": title,
                "category": category,
                "device_type": "",
                "approval_number": "",
                "content": "",
                "source_url": href or url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            })
            continue

        text_cells = [td.text.strip() for td in cells]
        title = text_cells[0] if text_cells else ""
        if not title or len(title) < 3:
            continue

        item = {
            "date": extract_date(" ".join(text_cells)),
            "title": title,
            "category": detect_regulatory_category(" ".join(text_cells)),
            "device_type": detect_device_type(" ".join(text_cells)),
            "approval_number": extract_approval_number(" ".join(text_cells)),
            "content": "",
            "source_url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }
        items.append(item)

    return items


def extract_news(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for article in sel.css("li a, .news-item, .list-item, .article-item"):
        title_elem = article.css("a")
        if not title_elem:
            continue

        title = title_elem[0].text.strip() if title_elem else ""
        if not title or len(title) < 5:
            continue

        href = title_elem[0].attrib.get("href", "")
        if href and not href.startswith("http"):
            href = "http://www.camdi.org" + href

        date_text = ""
        date_elem = article.css("span, .date, time")
        if date_elem:
            date_text = date_elem[0].text.strip()
        if not date_text:
            date_text = extract_date(title)

        category = "news"
        if "policy" in url.lower() or "政策" in title:
            category = "policy"
        elif "technology" in url.lower() or "技术" in title:
            category = "technology"

        items.append({
            "date": date_text,
            "title": title,
            "content": "",
            "category": category,
            "source_url": href or url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def identify_device(text: str) -> str:
    for key, info in DEVICE_CATEGORIES.items():
        if key in text.lower() or info["cn_name"] in text:
            return key
    if "影像" in text or "CT" in text or "MRI" in text or "超声" in text:
        return "imaging"
    if "体外诊断" in text or "IVD" in text or "试剂" in text:
        return "ivd"
    if "高值" in text or "支架" in text or "关节" in text:
        return "high_value_consumable"
    if "低值" in text or "注射" in text or "敷料" in text:
        return "low_value_consumable"
    return "other"


def extract_date(text: str) -> str:
    patterns = [
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{4}年\d{1,2}月\d{1,2}日)",
        r"(\d{4}年\d{1,2}月)",
        r"(\d{4}\.\d{1,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return datetime.now().strftime("%Y-%m-%d")


def extract_numeric_values(cells: list[str]) -> dict:
    result = {}
    number_pattern = r"[\d,]+\.?\d*"

    for cell in cells:
        cell_clean = cell.replace(",", "").replace("，", "")
        numbers = re.findall(number_pattern, cell_clean)
        if not numbers:
            continue

        try:
            value = float(numbers[0])
        except (ValueError, IndexError):
            continue

        if "增长" in cell or "同比" in cell or "增速" in cell:
            result["yoy"] = value
        elif "primary" not in result:
            result["primary"] = value

    return result


def extract_market_values(cells: list[str]) -> dict:
    result = {}
    number_pattern = r"[\d,]+\.?\d*"

    for i, cell in enumerate(cells):
        cell_clean = cell.replace(",", "").replace("，", "")
        numbers = re.findall(number_pattern, cell_clean)
        if not numbers:
            continue

        try:
            value = float(numbers[0])
        except (ValueError, IndexError):
            continue

        if "规模" in cell or "市场" in cell:
            result["market_size"] = value
        elif "增长" in cell or "增速" in cell:
            result["growth_rate"] = value
        elif "出口" in cell:
            result["export_value"] = value
        elif "进口" in cell:
            result["import_value"] = value
        elif i >= 1 and "market_size" not in result:
            result["market_size"] = value
        elif i >= 2 and "growth_rate" not in result:
            result["growth_rate"] = value

    return result


def detect_unit(text: str) -> str:
    if "万台" in text:
        return "万台"
    if "台" in text:
        return "台"
    if "亿元" in text:
        return "亿元"
    if "亿美元" in text:
        return "亿美元"
    return "N/A"


def detect_region(text: str) -> str:
    regions = {
        "全国": "全国", "华北": "华北", "华东": "华东", "华南": "华南",
        "华中": "华中", "西北": "西北", "东北": "东北", "西南": "西南",
    }
    for key, val in regions.items():
        if key in text:
            return val
    return "全国"


def detect_regulatory_category(text: str) -> str:
    if "注册" in text:
        return "registration"
    if "审批" in text or "批准" in text:
        return "approval"
    if "标准" in text or "规范" in text:
        return "standards"
    if "召回" in text:
        return "recall"
    if "分类" in text:
        return "classification"
    return "general"


def detect_device_type(text: str) -> str:
    if "二类" in text or "II" in text:
        return "class_ii"
    if "三类" in text or "III" in text:
        return "class_iii"
    if "一类" in text or "I" in text:
        return "class_i"
    return ""


def extract_approval_number(text: str) -> str:
    patterns = [
        r"(国械注准\d{8})",
        r"(国械注进\d{8})",
        r"(国械注许\d{8})",
        r"([A-Z]\d{4,8})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def save_to_sqlite(items: list[dict], conn: sqlite3.Connection, table: str) -> int:
    inserted = 0
    for item in items:
        try:
            if table == "device_production":
                conn.execute(
                    """INSERT OR REPLACE INTO device_production
                    (date, device_category, device_cn, production, unit, yoy_change, region, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("device_category"), item.get("device_cn"),
                     item.get("production"), item.get("unit"), item.get("yoy_change"),
                     item.get("region"), item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "market_stats":
                conn.execute(
                    """INSERT OR REPLACE INTO market_stats
                    (date, device_category, device_cn, market_size, growth_rate, export_value, import_value, unit, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("device_category"), item.get("device_cn"),
                     item.get("market_size"), item.get("growth_rate"),
                     item.get("export_value"), item.get("import_value"),
                     item.get("unit", "亿元"), item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "regulatory_info":
                conn.execute(
                    """INSERT OR REPLACE INTO regulatory_info
                    (date, title, category, device_type, approval_number, content, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("title"), item.get("category"),
                     item.get("device_type"), item.get("approval_number"),
                     item.get("content"), item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "news_articles":
                conn.execute(
                    """INSERT OR REPLACE INTO news_articles
                    (date, title, content, category, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("title"), item.get("content"),
                     item.get("category"), item.get("source_url"), item.get("scraped_at")),
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


def get_camdi_data(
    categories: list[str] | None = None,
    include_news: bool = True,
) -> dict:
    """Fetch medical device industry data from CAMDI.

    Args:
        categories: List of category keys (default: all).
            Available: production, market, regulatory.
        include_news: Whether to fetch industry news.

    Returns:
        Dict with keys: 'production', 'market', 'regulatory', 'news'.
    """
    if categories is None:
        categories = list(CATEGORIES.keys())

    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "production": [],
        "market": [],
        "regulatory": [],
        "news": [],
    }

    logger.info("Starting CAMDI spider for categories: %s", ", ".join(categories))

    for cat in categories:
        if cat == "news":
            continue

        cat_info = CATEGORIES.get(cat, {})
        for url in cat_info.get("urls", []):
            page = fetch_page(url, fetcher)
            if page:
                if cat == "market":
                    items = extract_market_data(page["html"], url)
                    if items:
                        result["market"].extend(items)
                        save_to_sqlite(items, conn, "market_stats")
                        logger.info("  Extracted %d market records", len(items))
                elif cat == "regulatory":
                    items = extract_regulatory_info(page["html"], url)
                    if items:
                        result["regulatory"].extend(items)
                        save_to_sqlite(items, conn, "regulatory_info")
                        logger.info("  Extracted %d regulatory records", len(items))
                else:
                    items = extract_production_tables(page["html"], url)
                    if items:
                        result["production"].extend(items)
                        save_to_sqlite(items, conn, "device_production")
                        logger.info("  Extracted %d production records", len(items))
            time.sleep(2)

    if include_news:
        for url in CATEGORIES.get("news", {}).get("urls", []):
            page = fetch_page(url, fetcher)
            if page:
                items = extract_news(page["html"], url)
                if items:
                    result["news"].extend(items)
                    save_to_sqlite(items, conn, "news_articles")
                    logger.info("  Extracted %d news articles", len(items))
            time.sleep(2)

    for key in ["production", "market", "regulatory", "news"]:
        if result[key]:
            json_path = {
                "production": JSON_PRODUCTION_PATH,
                "market": JSON_MARKET_PATH,
                "regulatory": JSON_REGULATORY_PATH,
                "news": JSON_NEWS_PATH,
            }[key]
            save_to_json(result[key], json_path)
            logger.info("Saved %d %s records to %s", len(result[key]), key, json_path)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_camdi_data(include_news=True)
    print(f"\n{'=' * 70}")
    print(f"Total production records: {len(results['production'])}")
    print(f"Total market records: {len(results['market'])}")
    print(f"Total regulatory records: {len(results['regulatory'])}")
    print(f"Total news articles: {len(results['news'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"{'=' * 70}")
