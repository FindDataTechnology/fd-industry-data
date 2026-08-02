#!/usr/bin/env python3
"""
CHA Spider - 中国医院协会数据爬虫

Target: http://www.cha.org.cn/
Data: Hospital statistics, healthcare service data, medical resource data, industry reports

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts hospital operation and resource allocation statistics
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
logger = logging.getLogger("cha")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "cha.db"
JSON_HOSPITAL_PATH = OUTPUT_DIR / "cha_hospital.json"
JSON_SERVICE_PATH = OUTPUT_DIR / "cha_service.json"
JSON_RESOURCE_PATH = OUTPUT_DIR / "cha_resource.json"
JSON_REPORT_PATH = OUTPUT_DIR / "cha_report.json"

CATEGORIES = {
    "hospital": {
        "cn_name": "医院统计数据",
        "urls": [
            "http://www.cha.org.cn/index/statistics/hospital.html",
            "http://www.cha.org.cn/index/statistics/hospital_rank.html",
            "http://www.cha.org.cn/index/dataservice/hospitaldata.html",
        ],
    },
    "service": {
        "cn_name": "医疗服务数据",
        "urls": [
            "http://www.cha.org.cn/index/statistics/service.html",
            "http://www.cha.org.cn/index/statistics/outpatient.html",
            "http://www.cha.org.cn/index/statistics/inpatient.html",
        ],
    },
    "resource": {
        "cn_name": "医疗资源数据",
        "urls": [
            "http://www.cha.org.cn/index/statistics/resource.html",
            "http://www.cha.org.cn/index/statistics/beds.html",
            "http://www.cha.org.cn/index/statistics/staff.html",
        ],
    },
    "report": {
        "cn_name": "行业报告",
        "urls": [
            "http://www.cha.org.cn/index/report/annual.html",
            "http://www.cha.org.cn/index/report/special.html",
        ],
    },
}


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hospital_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            hospital_type TEXT,
            hospital_level TEXT,
            total_count INTEGER,
            bed_count INTEGER,
            staff_count INTEGER,
            revenue REAL,
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, hospital_type, hospital_level, region, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS healthcare_services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            hospital_type TEXT,
            outpatient_visits REAL,
            inpatient_admissions REAL,
            surgery_count REAL,
            avg_stay_days REAL,
            bed_utilization REAL,
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, hospital_type, region, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS medical_resources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            resource_cn TEXT,
            count REAL,
            per_capita REAL,
            unit TEXT,
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, resource_type, region, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            title TEXT NOT NULL,
            report_type TEXT,
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


def extract_hospital_stats(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "医院", "床位数", "职工", "收入", "机构",
            "三甲", "三级", "二级", "一级",
            "hospital", "beds", "staff",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            numeric_values = extract_hospital_values(cells)
            item = {
                "date": extract_date(cells[0]),
                "hospital_type": detect_hospital_type(" ".join(cells)),
                "hospital_level": detect_hospital_level(" ".join(cells)),
                "region": detect_region(" ".join(cells)),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(numeric_values)

            if item.get("total_count") or item.get("bed_count"):
                items.append(item)

    return items


def extract_service_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "门诊", "住院", "手术", "床位利用", "就诊",
            "outpatient", "inpatient", "surgery", "visits",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            numeric_values = extract_service_values(cells)
            item = {
                "date": extract_date(cells[0]),
                "hospital_type": detect_hospital_type(" ".join(cells)),
                "region": detect_region(" ".join(cells)),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(numeric_values)

            if item.get("outpatient_visits") or item.get("inpatient_admissions"):
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
            "资源", "床位数", "卫生人员", "执业医师", "注册护士",
            "设备", "千人", "人均",
            "resource", "beds", "physician", "nurse",
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

            if item.get("count") or item.get("per_capita"):
                items.append(item)

    return items


def extract_reports(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for article in sel.css("li a, .report-item, .list-item, .article-item"):
        title_elem = article.css("a")
        if not title_elem:
            continue

        title = title_elem[0].text.strip() if title_elem else ""
        if not title or len(title) < 5:
            continue

        href = title_elem[0].attrib.get("href", "")
        if href and not href.startswith("http"):
            href = "http://www.cha.org.cn" + href

        date_text = ""
        date_elem = article.css("span, .date, time")
        if date_elem:
            date_text = date_elem[0].text.strip()
        if not date_text:
            date_text = extract_date(title)

        report_type = "annual"
        if "专项" in title or "special" in url.lower():
            report_type = "special"
        elif "年度" in title or "annual" in url.lower():
            report_type = "annual"

        items.append({
            "date": date_text,
            "title": title,
            "report_type": report_type,
            "summary": "",
            "source_url": href or url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


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


def extract_hospital_values(cells: list[str]) -> dict:
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

        if "床位" in cell:
            result["bed_count"] = int(value)
        elif "职工" in cell or "人员" in cell:
            result["staff_count"] = int(value)
        elif "收入" in cell or "营收" in cell:
            result["revenue"] = value
        elif "总数" in cell or "数量" in cell or "机构" in cell:
            result["total_count"] = int(value)

    return result


def extract_service_values(cells: list[str]) -> dict:
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

        if "门诊" in cell or "就诊" in cell:
            result["outpatient_visits"] = value
        elif "住院" in cell or "入院" in cell:
            result["inpatient_admissions"] = value
        elif "手术" in cell:
            result["surgery_count"] = value
        elif "住院日" in cell or "平均住院" in cell:
            result["avg_stay_days"] = value
        elif "床位利用" in cell or "利用率" in cell:
            result["bed_utilization"] = value

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

        if "千人" in cell or "人均" in cell or "每千" in cell:
            result["per_capita"] = value
        elif "primary" not in result:
            result["count"] = value

    return result


def detect_hospital_type(text: str) -> str:
    if "综合" in text:
        return "general"
    if "专科" in text:
        return "specialty"
    if "中医" in text:
        return "tcm"
    if "民营" in text or "社会" in text:
        return "private"
    if "公立" in text:
        return "public"
    return "all"


def detect_hospital_level(text: str) -> str:
    if "三甲" in text or "三级甲等" in text:
        return "3A"
    if "三乙" in text or "三级乙等" in text:
        return "3B"
    if "三级" in text:
        return "3"
    if "二甲" in text or "二级甲等" in text:
        return "2A"
    if "二乙" in text or "二级乙等" in text:
        return "2B"
    if "二级" in text:
        return "2"
    if "一级" in text:
        return "1"
    return ""


def detect_resource_type(text: str) -> str:
    if "床位" in text:
        return "beds"
    if "执业" in text and ("医师" in text or "医生" in text):
        return "licensed_physicians"
    if "注册护士" in text or "护士" in text:
        return "registered_nurses"
    if "卫生人员" in text or "卫生技术" in text:
        return "health_workers"
    if "设备" in text:
        return "medical_equipment"
    if "机构" in text:
        return "institutions"
    return "other"


def resource_cn_name(key: str) -> str:
    mapping = {
        "beds": "床位",
        "licensed_physicians": "执业医师",
        "registered_nurses": "注册护士",
        "health_workers": "卫生技术人员",
        "medical_equipment": "医疗设备",
        "institutions": "医疗卫生机构",
    }
    return mapping.get(key, "其他")


def detect_unit(text: str) -> str:
    if "张" in text:
        return "张"
    if "万人" in text:
        return "万人"
    if "人" in text:
        return "人"
    if "台" in text:
        return "台"
    if "所" in text:
        return "所"
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


def save_to_sqlite(items: list[dict], conn: sqlite3.Connection, table: str) -> int:
    inserted = 0
    for item in items:
        try:
            if table == "hospital_stats":
                conn.execute(
                    """INSERT OR REPLACE INTO hospital_stats
                    (date, hospital_type, hospital_level, total_count, bed_count, staff_count, revenue, region, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("hospital_type"), item.get("hospital_level"),
                     item.get("total_count"), item.get("bed_count"), item.get("staff_count"),
                     item.get("revenue"), item.get("region"),
                     item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "healthcare_services":
                conn.execute(
                    """INSERT OR REPLACE INTO healthcare_services
                    (date, hospital_type, outpatient_visits, inpatient_admissions, surgery_count,
                     avg_stay_days, bed_utilization, region, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("hospital_type"),
                     item.get("outpatient_visits"), item.get("inpatient_admissions"),
                     item.get("surgery_count"), item.get("avg_stay_days"),
                     item.get("bed_utilization"), item.get("region"),
                     item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "medical_resources":
                conn.execute(
                    """INSERT OR REPLACE INTO medical_resources
                    (date, resource_type, resource_cn, count, per_capita, unit, region, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("resource_type"), item.get("resource_cn"),
                     item.get("count"), item.get("per_capita"), item.get("unit"),
                     item.get("region"), item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "reports":
                conn.execute(
                    """INSERT OR REPLACE INTO reports
                    (date, title, report_type, summary, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("title"), item.get("report_type"),
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


def get_cha_data(
    categories: list[str] | None = None,
    include_reports: bool = True,
) -> dict:
    """Fetch hospital and healthcare data from CHA.

    Args:
        categories: List of category keys (default: all).
            Available: hospital, service, resource.
        include_reports: Whether to fetch industry reports.

    Returns:
        Dict with keys: 'hospital', 'service', 'resource', 'report'.
    """
    if categories is None:
        categories = list(CATEGORIES.keys())

    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "hospital": [],
        "service": [],
        "resource": [],
        "report": [],
    }

    logger.info("Starting CHA spider for categories: %s", ", ".join(categories))

    for cat in categories:
        if cat == "report":
            continue

        cat_info = CATEGORIES.get(cat, {})
        for url in cat_info.get("urls", []):
            page = fetch_page(url, fetcher)
            if page:
                if cat == "hospital":
                    items = extract_hospital_stats(page["html"], url)
                    if items:
                        result["hospital"].extend(items)
                        save_to_sqlite(items, conn, "hospital_stats")
                        logger.info("  Extracted %d hospital records", len(items))
                elif cat == "service":
                    items = extract_service_data(page["html"], url)
                    if items:
                        result["service"].extend(items)
                        save_to_sqlite(items, conn, "healthcare_services")
                        logger.info("  Extracted %d service records", len(items))
                elif cat == "resource":
                    items = extract_resource_data(page["html"], url)
                    if items:
                        result["resource"].extend(items)
                        save_to_sqlite(items, conn, "medical_resources")
                        logger.info("  Extracted %d resource records", len(items))
            time.sleep(2)

    if include_reports:
        for url in CATEGORIES.get("report", {}).get("urls", []):
            page = fetch_page(url, fetcher)
            if page:
                items = extract_reports(page["html"], url)
                if items:
                    result["report"].extend(items)
                    save_to_sqlite(items, conn, "reports")
                    logger.info("  Extracted %d reports", len(items))
            time.sleep(2)

    for key in ["hospital", "service", "resource", "report"]:
        if result[key]:
            json_path = {
                "hospital": JSON_HOSPITAL_PATH,
                "service": JSON_SERVICE_PATH,
                "resource": JSON_RESOURCE_PATH,
                "report": JSON_REPORT_PATH,
            }[key]
            save_to_json(result[key], json_path)
            logger.info("Saved %d %s records to %s", len(result[key]), key, json_path)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_cha_data(include_reports=True)
    print(f"\n{'=' * 70}")
    print(f"Total hospital records: {len(results['hospital'])}")
    print(f"Total service records: {len(results['service'])}")
    print(f"Total resource records: {len(results['resource'])}")
    print(f"Total reports: {len(results['report'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"{'=' * 70}")
