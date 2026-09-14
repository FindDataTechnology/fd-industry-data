#!/usr/bin/env python3
"""
NHC Spider - 国家卫生健康委员会数据爬虫

Target: http://www.nhc.gov.cn/
Data: Public health statistics, disease surveillance data, healthcare resource data, policy documents

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts national health statistics and policy documents
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
logger = logging.getLogger("nhc")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "nhc.db"
JSON_PUBLIC_HEALTH_PATH = OUTPUT_DIR / "nhc_public_health.json"
JSON_DISEASE_PATH = OUTPUT_DIR / "nhc_disease.json"
JSON_RESOURCES_PATH = OUTPUT_DIR / "nhc_resources.json"
JSON_POLICY_PATH = OUTPUT_DIR / "nhc_policy.json"

CATEGORIES = {
    "public_health": {
        "cn_name": "公共卫生统计",
        "urls": [
            "http://www.nhc.gov.cn/guihuaxxs/s3586s/new_list.shtml",
            "http://www.nhc.gov.cn/guihuaxxs/s3585u/new_list.shtml",
            "http://www.nhc.gov.cn/fzs/s3580/new_list.shtml",
        ],
    },
    "disease": {
        "cn_name": "疾病监测数据",
        "urls": [
            "http://www.nhc.gov.cn/jkj/s3581/new_list.shtml",
            "http://www.nhc.gov.cn/jkj/s3582/new_list.shtml",
            "http://www.nhc.gov.cn/jkj/s3583/new_list.shtml",
        ],
    },
    "resources": {
        "cn_name": "卫生资源数据",
        "urls": [
            "http://www.nhc.gov.cn/guihuaxxs/s3586s/new_list.shtml",
            "http://www.nhc.gov.cn/yzygj/s3585u/new_list.shtml",
        ],
    },
    "policy": {
        "cn_name": "政策文件",
        "urls": [
            "http://www.nhc.gov.cn/fzs/s3580/new_list.shtml",
            "http://www.nhc.gov.cn/fzs/s3576/new_list.shtml",
            "http://www.nhc.gov.cn/xwfb/s3581/new_list.shtml",
        ],
    },
}


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS public_health_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            indicator TEXT NOT NULL,
            indicator_cn TEXT,
            value REAL,
            unit TEXT,
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, indicator, region, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS disease_surveillance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            disease_name TEXT NOT NULL,
            disease_name_cn TEXT,
            cases INTEGER,
            deaths INTEGER,
            incidence_rate REAL,
            mortality_rate REAL,
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, disease_name, region, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS health_resources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            resource_cn TEXT,
            count REAL,
            per_10k REAL,
            unit TEXT,
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, resource_type, region, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS policy_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            title TEXT NOT NULL,
            doc_number TEXT,
            policy_type TEXT,
            issuing_body TEXT,
            summary TEXT,
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


def extract_public_health_stats(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "出生率", "死亡率", "发病率", "患病率", "预期寿命",
            "婴儿", "孕产妇", "新生儿", "接种率",
            "birth", "death", "mortality", "morbidity",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            indicator_key = detect_health_indicator(" ".join(cells))
            numeric_values = extract_numeric_values(cells)

            item = {
                "date": extract_date(cells[0]),
                "indicator": indicator_key,
                "indicator_cn": health_indicator_cn(indicator_key),
                "unit": detect_unit(" ".join(cells)),
                "region": detect_region(" ".join(cells)),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

            if numeric_values:
                item["value"] = numeric_values.get("primary")

            if item.get("value") is not None:
                items.append(item)

    return items


def extract_disease_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "发病", "死亡", "病例", "传染病", "感染",
            "发病率", "死亡率", "cases", "deaths", "disease",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            disease_info = extract_disease_info(cells)
            numeric_values = extract_disease_values(cells)

            item = {
                "date": extract_date(cells[0]),
                "disease_name": disease_info.get("name", "unknown"),
                "disease_name_cn": disease_info.get("cn_name", ""),
                "region": detect_region(" ".join(cells)),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(numeric_values)

            if item.get("cases") or item.get("deaths"):
                items.append(item)

    return items


def extract_resource_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "卫生", "医院", "床位", "医师", "护士", "人员",
            "机构", "万人", "每千",
            "hospital", "beds", "physician", "nurse",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            resource_key = detect_resource_type(" ".join(cells))
            numeric_values = extract_resource_values(cells)

            item = {
                "date": extract_date(cells[0]),
                "resource_type": resource_key,
                "resource_cn": resource_cn_name(resource_key),
                "unit": detect_unit(" ".join(cells)),
                "region": detect_region(" ".join(cells)),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(numeric_values)

            if item.get("count") or item.get("per_10k"):
                items.append(item)

    return items


def extract_policy_documents(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for article in sel.css("li, .list-item, .article-item"):
        title_elem = article.css("a")
        if not title_elem:
            continue

        title = title_elem[0].text.strip() if title_elem else ""
        if not title or len(title) < 5:
            continue

        href = title_elem[0].attrib.get("href", "")
        if href and not href.startswith("http"):
            href = "http://www.nhc.gov.cn" + href

        date_text = ""
        date_elem = article.css("span, .date, time")
        if date_elem:
            date_text = date_elem[0].text.strip()
        if not date_text:
            date_text = extract_date(title)

        doc_number = extract_doc_number(title + " " + article.text.strip())
        policy_type = detect_policy_type(title)
        issuing_body = detect_issuing_body(title + " " + article.text.strip())

        items.append({
            "date": date_text,
            "title": title,
            "doc_number": doc_number,
            "policy_type": policy_type,
            "issuing_body": issuing_body,
            "summary": "",
            "source_url": href or url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def detect_health_indicator(text: str) -> str:
    if "出生率" in text:
        return "birth_rate"
    if "死亡率" in text and ("婴儿" in text or "新生儿" in text):
        return "infant_mortality"
    if "死亡率" in text and "孕产妇" in text:
        return "maternal_mortality"
    if "死亡率" in text:
        return "death_rate"
    if "发病率" in text:
        return "incidence_rate"
    if "患病率" in text:
        return "prevalence_rate"
    if "预期寿命" in text or "期望寿命" in text:
        return "life_expectancy"
    if "接种率" in text:
        return "vaccination_rate"
    return "other"


def health_indicator_cn(key: str) -> str:
    mapping = {
        "birth_rate": "出生率",
        "death_rate": "死亡率",
        "infant_mortality": "婴儿死亡率",
        "maternal_mortality": "孕产妇死亡率",
        "incidence_rate": "发病率",
        "prevalence_rate": "患病率",
        "life_expectancy": "预期寿命",
        "vaccination_rate": "接种率",
    }
    return mapping.get(key, "其他")


def extract_disease_info(cells: list[str]) -> dict:
    text = " ".join(cells)
    diseases = {
        "病毒性肝炎": "viral_hepatitis",
        "肺结核": "tuberculosis",
        "梅毒": "syphilis",
        "艾滋病": "hiv_aids",
        "淋病": "gonorrhea",
        "细菌性痢疾": "bacterial_dysentery",
        "猩红热": "scarlet_fever",
        "手足口病": "hand_foot_mouth",
        "流感": "influenza",
        "新冠": "covid_19",
        "麻疹": "measles",
        "登革热": "dengue",
    }
    for cn_name, en_name in diseases.items():
        if cn_name in text:
            return {"name": en_name, "cn_name": cn_name}
    return {"name": "other", "cn_name": "其他传染病"}


def extract_disease_values(cells: list[str]) -> dict:
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

        if "发病" in cell and "率" in cell:
            result["incidence_rate"] = value
        elif "死亡" in cell and "率" in cell:
            result["mortality_rate"] = value
        elif "发病" in cell or "病例" in cell:
            result["cases"] = int(value)
        elif "死亡" in cell:
            result["deaths"] = int(value)

    return result


def extract_resource_values(cells: list[str]) -> dict:
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

        if "万人" in cell or "每千" in cell or "人均" in cell:
            result["per_10k"] = value
        elif "primary" not in result:
            result["count"] = value

    return result


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

        if "primary" not in result:
            result["primary"] = value

    return result


def detect_resource_type(text: str) -> str:
    if "床位" in text:
        return "beds"
    if "医师" in text or "医生" in text:
        return "physicians"
    if "护士" in text:
        return "nurses"
    if "卫生人员" in text or "卫生技术" in text:
        return "health_workers"
    if "医院" in text:
        return "hospitals"
    if "基层" in text or "社区" in text:
        return "primary_care_facilities"
    if "机构" in text:
        return "institutions"
    return "other"


def resource_cn_name(key: str) -> str:
    mapping = {
        "beds": "医疗卫生床位",
        "physicians": "执业医师",
        "nurses": "注册护士",
        "health_workers": "卫生技术人员",
        "hospitals": "医院",
        "primary_care_facilities": "基层医疗卫生机构",
        "institutions": "医疗卫生机构",
    }
    return mapping.get(key, "其他")


def detect_unit(text: str) -> str:
    if "‰" in text:
        return "‰"
    if "%" in text:
        return "%"
    if "岁" in text:
        return "岁"
    if "万人" in text:
        return "万人"
    if "万" in text:
        return "万"
    if "个" in text:
        return "个"
    if "张" in text:
        return "张"
    if "人" in text:
        return "人"
    return "N/A"


def detect_region(text: str) -> str:
    regions = {
        "全国": "全国", "华北": "华北", "华东": "华东", "华南": "华南",
        "华中": "华中", "西北": "西北", "东北": "东北", "西南": "西南",
    }
    for key, val in regions.items():
        if key in text:
            return val
    provinces = [
        "北京", "上海", "广东", "江苏", "浙江", "山东", "河南", "四川",
        "湖北", "湖南", "安徽", "河北", "福建", "辽宁", "陕西",
    ]
    for p in provinces:
        if p in text:
            return p
    return "全国"


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


def extract_doc_number(text: str) -> str:
    patterns = [
        r"(国卫\w+\[\d{4}\]\d+号)",
        r"(国卫\w+\(\d{4}\)\d+号)",
        r"(卫\w+\[\d{4}\]\d+号)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def detect_policy_type(text: str) -> str:
    if "通知" in text:
        return "notice"
    if "办法" in text:
        return "regulation"
    if "规划" in text:
        return "plan"
    if "标准" in text or "规范" in text:
        return "standard"
    if "意见" in text:
        return "guideline"
    if "方案" in text:
        return "scheme"
    return "other"


def detect_issuing_body(text: str) -> str:
    if "国务院" in text:
        return "国务院"
    if "卫健委" in text or "卫生计生委" in text:
        return "国家卫生健康委员会"
    if "医保局" in text:
        return "国家医疗保障局"
    if "药监局" in text or "药品监督" in text:
        return "国家药品监督管理局"
    return "国家卫生健康委员会"


def save_to_sqlite(items: list[dict], conn: sqlite3.Connection, table: str) -> int:
    inserted = 0
    for item in items:
        try:
            if table == "public_health_stats":
                conn.execute(
                    """INSERT OR REPLACE INTO public_health_stats
                    (date, indicator, indicator_cn, value, unit, region, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("indicator"), item.get("indicator_cn"),
                     item.get("value"), item.get("unit"), item.get("region"),
                     item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "disease_surveillance":
                conn.execute(
                    """INSERT OR REPLACE INTO disease_surveillance
                    (date, disease_name, disease_name_cn, cases, deaths, incidence_rate,
                     mortality_rate, region, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("disease_name"), item.get("disease_name_cn"),
                     item.get("cases"), item.get("deaths"), item.get("incidence_rate"),
                     item.get("mortality_rate"), item.get("region"),
                     item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "health_resources":
                conn.execute(
                    """INSERT OR REPLACE INTO health_resources
                    (date, resource_type, resource_cn, count, per_10k, unit, region, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("resource_type"), item.get("resource_cn"),
                     item.get("count"), item.get("per_10k"), item.get("unit"),
                     item.get("region"), item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "policy_documents":
                conn.execute(
                    """INSERT OR REPLACE INTO policy_documents
                    (date, title, doc_number, policy_type, issuing_body, summary, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("title"), item.get("doc_number"),
                     item.get("policy_type"), item.get("issuing_body"),
                     item.get("summary"), item.get("source_url"), item.get("scraped_at")),
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


def get_nhc_data(
    categories: list[str] | None = None,
    include_policy: bool = True,
) -> dict:
    """Fetch public health data from National Health Commission.

    Args:
        categories: List of category keys (default: all).
            Available: public_health, disease, resources.
        include_policy: Whether to fetch policy documents.

    Returns:
        Dict with keys: 'public_health', 'disease', 'resources', 'policy'.
    """
    if categories is None:
        categories = list(CATEGORIES.keys())

    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "public_health": [],
        "disease": [],
        "resources": [],
        "policy": [],
    }

    logger.info("Starting NHC spider for categories: %s", ", ".join(categories))

    for cat in categories:
        if cat == "policy":
            continue

        cat_info = CATEGORIES.get(cat, {})
        for url in cat_info.get("urls", []):
            page = fetch_page(url, fetcher)
            if page:
                if cat == "public_health":
                    items = extract_public_health_stats(page["html"], url)
                    if items:
                        result["public_health"].extend(items)
                        save_to_sqlite(items, conn, "public_health_stats")
                        logger.info("  Extracted %d public health records", len(items))
                elif cat == "disease":
                    items = extract_disease_data(page["html"], url)
                    if items:
                        result["disease"].extend(items)
                        save_to_sqlite(items, conn, "disease_surveillance")
                        logger.info("  Extracted %d disease records", len(items))
                elif cat == "resources":
                    items = extract_resource_data(page["html"], url)
                    if items:
                        result["resources"].extend(items)
                        save_to_sqlite(items, conn, "health_resources")
                        logger.info("  Extracted %d resource records", len(items))
            time.sleep(2)

    if include_policy:
        for url in CATEGORIES.get("policy", {}).get("urls", []):
            page = fetch_page(url, fetcher)
            if page:
                items = extract_policy_documents(page["html"], url)
                if items:
                    result["policy"].extend(items)
                    save_to_sqlite(items, conn, "policy_documents")
                    logger.info("  Extracted %d policy documents", len(items))
            time.sleep(2)

    for key in ["public_health", "disease", "resources", "policy"]:
        if result[key]:
            json_path = {
                "public_health": JSON_PUBLIC_HEALTH_PATH,
                "disease": JSON_DISEASE_PATH,
                "resources": JSON_RESOURCES_PATH,
                "policy": JSON_POLICY_PATH,
            }[key]
            save_to_json(result[key], json_path)
            logger.info("Saved %d %s records to %s", len(result[key]), key, json_path)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_nhc_data(include_policy=True)
    print(f"\n{'=' * 70}")
    print(f"Total public health records: {len(results['public_health'])}")
    print(f"Total disease records: {len(results['disease'])}")
    print(f"Total resource records: {len(results['resources'])}")
    print(f"Total policy documents: {len(results['policy'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"{'=' * 70}")
