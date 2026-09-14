#!/usr/bin/env python3
"""
ChinaCDC Spider - 中国疾病预防控制中心数据爬虫

Target: http://www.chinacdc.cn/
Data: Disease statistics, epidemic data, public health reports, vaccination data

Architecture:
  - Primary: HTML scraping with Scrapling Fetcher
  - Anti-bot: Browser impersonation, rate limiting
  - Output: SQLite DB + JSON export
  - Special: Extracts epidemic surveillance and immunization data
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
logger = logging.getLogger("chinacdc")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "chinacdc.db"
JSON_DISEASE_PATH = OUTPUT_DIR / "chinacdc_disease.json"
JSON_EPIDEMIC_PATH = OUTPUT_DIR / "chinacdc_epidemic.json"
JSON_VACCINATION_PATH = OUTPUT_DIR / "chinacdc_vaccination.json"
JSON_REPORT_PATH = OUTPUT_DIR / "chinacdc_report.json"

CATEGORIES = {
    "disease": {
        "cn_name": "疾病统计数据",
        "urls": [
            "http://www.chinacdc.cn/jkzt/crb/zl/",
            "http://www.chinacdc.cn/jkzt/txb/jcjs/",
            "http://www.chinacdc.cn/jkzt/mxfcrxjb/jcjs/",
        ],
    },
    "epidemic": {
        "cn_name": "疫情监测数据",
        "urls": [
            "http://www.chinacdc.cn/jkzt/crb/zl/szkb/",
            "http://www.chinacdc.cn/jkzt/crb/zl/kb/",
            "http://www.chinacdc.cn/jkzt/crb/zl/yyy/",
        ],
    },
    "vaccination": {
        "cn_name": "免疫规划数据",
        "urls": [
            "http://www.chinacdc.cn/jkzt/mygh/",
            "http://www.chinacdc.cn/jkzt/mygh/jzxx/",
            "http://www.chinacdc.cn/jkzt/mygh/jcjs/",
        ],
    },
    "report": {
        "cn_name": "公共卫生报告",
        "urls": [
            "http://www.chinacdc.cn/gzdt/xwfb/",
            "http://www.chinacdc.cn/gzdt/gzdt/",
            "http://www.chinacdc.cn/jkzt/jkzp/",
        ],
    },
}

DISEASES = {
    "viral_hepatitis": {"cn_name": "病毒性肝炎", "category": "乙类"},
    "tuberculosis": {"cn_name": "肺结核", "category": "乙类"},
    "syphilis": {"cn_name": "梅毒", "category": "乙类"},
    "hiv_aids": {"cn_name": "艾滋病", "category": "乙类"},
    "gonorrhea": {"cn_name": "淋病", "category": "乙类"},
    "bacterial_dysentery": {"cn_name": "细菌性痢疾", "category": "乙类"},
    "scarlet_fever": {"cn_name": "猩红热", "category": "乙类"},
    "hand_foot_mouth": {"cn_name": "手足口病", "category": "丙类"},
    "influenza": {"cn_name": "流行性感冒", "category": "丙类"},
    "covid_19": {"cn_name": "新型冠状病毒感染", "category": "乙类乙管"},
    "measles": {"cn_name": "麻疹", "category": "乙类"},
    "dengue": {"cn_name": "登革热", "category": "乙类"},
    "mumps": {"cn_name": "流行性腮腺炎", "category": "丙类"},
    "chickenpox": {"cn_name": "水痘", "category": "丙类"},
    "typhoid": {"cn_name": "伤寒", "category": "乙类"},
    "cholera": {"cn_name": "霍乱", "category": "甲类"},
    "plague": {"cn_name": "鼠疫", "category": "甲类"},
}

VACCINES = {
    "hepb": {"cn_name": "乙肝疫苗", "abbr": "HepB"},
    "bcg": {"cn_name": "卡介苗", "abbr": "BCG"},
    "opv": {"cn_name": "脊灰疫苗", "abbr": "OPV"},
    "dpt": {"cn_name": "百白破疫苗", "abbr": "DPT"},
    "dt": {"cn_name": "白破疫苗", "abbr": "DT"},
    "mmr": {"cn_name": "麻腮风疫苗", "abbr": "MMR"},
    "meningitis_a": {"cn_name": "A群流脑疫苗", "abbr": "MenA"},
    "japanese_enc": {"cn_name": "乙脑疫苗", "abbr": "JE"},
    "hep_a": {"cn_name": "甲肝疫苗", "abbr": "HepA"},
}


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS disease_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            disease_name TEXT NOT NULL,
            disease_cn TEXT,
            disease_category TEXT,
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
        CREATE TABLE IF NOT EXISTS epidemic_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_cn TEXT,
            location TEXT,
            cases INTEGER,
            severity TEXT,
            status TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, event_type, location, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vaccination_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            vaccine_name TEXT NOT NULL,
            vaccine_cn TEXT,
            coverage_rate REAL,
            doses_administered REAL,
            target_population REAL,
            region TEXT,
            source_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, vaccine_name, region, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS health_reports (
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


def extract_disease_stats(html: str, url: str) -> list[dict]:
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
            "发病率", "死亡率", "甲类", "乙类", "丙类",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 3:
                continue

            disease_info = identify_disease(" ".join(cells))
            numeric_values = extract_disease_values(cells)

            item = {
                "date": extract_date(cells[0]),
                "disease_name": disease_info["name"],
                "disease_cn": disease_info["cn_name"],
                "disease_category": disease_info.get("category", ""),
                "region": detect_region(" ".join(cells)),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(numeric_values)

            if item.get("cases") is not None or item.get("deaths") is not None:
                items.append(item)

    return items


def extract_epidemic_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for article in sel.css("li, .list-item, .event-item, tr"):
        cells = article.css("td")
        if cells and len(cells) >= 3:
            text_cells = [td.text.strip() for td in cells]
            item = {
                "date": extract_date(text_cells[0]),
                "event_type": detect_event_type(" ".join(text_cells)),
                "event_cn": text_cells[1] if len(text_cells) > 1 else "",
                "location": detect_region(" ".join(text_cells)),
                "cases": extract_int_value(" ".join(text_cells)),
                "severity": detect_severity(" ".join(text_cells)),
                "status": detect_status(" ".join(text_cells)),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            if item["event_cn"]:
                items.append(item)
            continue

        link = article.css("a")
        if not link:
            continue

        title = link[0].text.strip()
        if not title or len(title) < 5:
            continue

        href = link[0].attrib.get("href", "")
        if href and not href.startswith("http"):
            href = "http://www.chinacdc.cn" + href

        date_text = ""
        date_elem = article.css("span, .date, time")
        if date_elem:
            date_text = date_elem[0].text.strip()

        items.append({
            "date": date_text or extract_date(title),
            "event_type": detect_event_type(title),
            "event_cn": title,
            "location": detect_region(title),
            "cases": None,
            "severity": detect_severity(title),
            "status": detect_status(title),
            "source_url": href or url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def extract_vaccination_data(html: str, url: str) -> list[dict]:
    sel = Selector(html)
    items = []

    for table in sel.css("table"):
        rows = table.css("tr")
        if not rows or len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]
        header_text = " ".join(headers)

        if not any(kw in header_text for kw in [
            "接种", "覆盖率", "接种率", "疫苗", "免疫",
            "vaccination", "coverage", "immunization",
        ]):
            continue

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if len(cells) < 2:
                continue

            vaccine_info = identify_vaccine(" ".join(cells))
            numeric_values = extract_vaccination_values(cells)

            item = {
                "date": extract_date(cells[0]),
                "vaccine_name": vaccine_info["name"],
                "vaccine_cn": vaccine_info["cn_name"],
                "region": detect_region(" ".join(cells)),
                "source_url": url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
            item.update(numeric_values)

            if item.get("coverage_rate") is not None or item.get("doses_administered") is not None:
                items.append(item)

    return items


def extract_reports(html: str, url: str) -> list[dict]:
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
            href = "http://www.chinacdc.cn" + href

        date_text = ""
        date_elem = article.css("span, .date, time")
        if date_elem:
            date_text = date_elem[0].text.strip()
        if not date_text:
            date_text = extract_date(title)

        report_type = "general"
        if "疫情" in title or "通报" in title:
            report_type = "epidemic_bulletin"
        elif "预警" in title:
            report_type = "warning"
        elif "监测" in title:
            report_type = "surveillance"
        elif "报告" in title:
            report_type = "annual_report"

        items.append({
            "date": date_text,
            "title": title,
            "report_type": report_type,
            "summary": "",
            "source_url": href or url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    return items


def identify_disease(text: str) -> dict:
    for key, info in DISEASES.items():
        if info["cn_name"] in text or key in text.lower():
            return {"name": key, "cn_name": info["cn_name"], "category": info.get("category", "")}
    if "肝炎" in text:
        return {"name": "viral_hepatitis", "cn_name": "病毒性肝炎", "category": "乙类"}
    if "结核" in text:
        return {"name": "tuberculosis", "cn_name": "肺结核", "category": "乙类"}
    return {"name": "other", "cn_name": "其他", "category": ""}


def identify_vaccine(text: str) -> dict:
    for key, info in VACCINES.items():
        if info["cn_name"] in text or key in text.lower() or info["abbr"] in text:
            return {"name": key, "cn_name": info["cn_name"]}
    if "乙肝" in text:
        return {"name": "hepb", "cn_name": "乙肝疫苗"}
    if "卡介" in text:
        return {"name": "bcg", "cn_name": "卡介苗"}
    if "脊灰" in text:
        return {"name": "opv", "cn_name": "脊灰疫苗"}
    return {"name": "other", "cn_name": "其他疫苗"}


def extract_date(text: str) -> str:
    patterns = [
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{4}年\d{1,2}月\d{1,2}日)",
        r"(\d{4}年\d{1,2}月)",
        r"(\d{4}\.\d{1,2})",
        r"(\d{4}年第\d+期)",
        r"(\d{4}年)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return datetime.now().strftime("%Y-%m-%d")


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

        if "发病率" in cell:
            result["incidence_rate"] = value
        elif "死亡率" in cell:
            result["mortality_rate"] = value
        elif "死亡" in cell:
            result["deaths"] = int(value)
        elif "发病" in cell or "病例" in cell:
            result["cases"] = int(value)

    return result


def extract_vaccination_values(cells: list[str]) -> dict:
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

        if "覆盖率" in cell or "接种率" in cell:
            result["coverage_rate"] = value
        elif "接种" in cell and ("万" in cell or "剂" in cell or "人次" in cell):
            result["doses_administered"] = value
        elif "人口" in cell or "目标" in cell:
            result["target_population"] = value

    return result


def extract_int_value(text: str) -> int | None:
    number_pattern = r"[\d,]+"
    numbers = re.findall(number_pattern, text.replace(",", ""))
    for n in numbers:
        try:
            return int(n)
        except ValueError:
            continue
    return None


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
        "云南", "广西", "贵州", "西藏", "新疆", "内蒙古",
        "黑龙江", "吉林", "海南", "甘肃", "青海", "宁夏", "天津", "重庆",
    ]
    for p in provinces:
        if p in text:
            return p
    return "全国"


def detect_event_type(text: str) -> str:
    if "聚集性" in text:
        return "cluster"
    if "暴发" in text:
        return "outbreak"
    if "突发" in text:
        return "emergency"
    if "预警" in text:
        return "warning"
    return "routine"


def detect_severity(text: str) -> str:
    if "特别重大" in text:
        return "level_i"
    if "重大" in text:
        return "level_ii"
    if "较大" in text:
        return "level_iii"
    if "一般" in text:
        return "level_iv"
    return ""


def detect_status(text: str) -> str:
    if "进行中" in text or "处置中" in text:
        return "ongoing"
    if "已结束" in text or "解除" in text:
        return "resolved"
    if "关注" in text:
        return "monitoring"
    return ""


def save_to_sqlite(items: list[dict], conn: sqlite3.Connection, table: str) -> int:
    inserted = 0
    for item in items:
        try:
            if table == "disease_stats":
                conn.execute(
                    """INSERT OR REPLACE INTO disease_stats
                    (date, disease_name, disease_cn, disease_category, cases, deaths,
                     incidence_rate, mortality_rate, region, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("disease_name"), item.get("disease_cn"),
                     item.get("disease_category"), item.get("cases"), item.get("deaths"),
                     item.get("incidence_rate"), item.get("mortality_rate"),
                     item.get("region"), item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "epidemic_data":
                conn.execute(
                    """INSERT OR REPLACE INTO epidemic_data
                    (date, event_type, event_cn, location, cases, severity, status, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("event_type"), item.get("event_cn"),
                     item.get("location"), item.get("cases"), item.get("severity"),
                     item.get("status"), item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "vaccination_data":
                conn.execute(
                    """INSERT OR REPLACE INTO vaccination_data
                    (date, vaccine_name, vaccine_cn, coverage_rate, doses_administered,
                     target_population, region, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.get("date"), item.get("vaccine_name"), item.get("vaccine_cn"),
                     item.get("coverage_rate"), item.get("doses_administered"),
                     item.get("target_population"), item.get("region"),
                     item.get("source_url"), item.get("scraped_at")),
                )
            elif table == "health_reports":
                conn.execute(
                    """INSERT OR REPLACE INTO health_reports
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


def get_chinacdc_data(
    categories: list[str] | None = None,
    include_reports: bool = True,
) -> dict:
    """Fetch disease control and public health data from China CDC.

    Args:
        categories: List of category keys (default: all).
            Available: disease, epidemic, vaccination.
        include_reports: Whether to fetch public health reports.

    Returns:
        Dict with keys: 'disease', 'epidemic', 'vaccination', 'report'.
    """
    if categories is None:
        categories = list(CATEGORIES.keys())

    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    conn = init_db()

    result = {
        "disease": [],
        "epidemic": [],
        "vaccination": [],
        "report": [],
    }

    logger.info("Starting ChinaCDC spider for categories: %s", ", ".join(categories))

    for cat in categories:
        if cat == "report":
            continue

        cat_info = CATEGORIES.get(cat, {})
        for url in cat_info.get("urls", []):
            page = fetch_page(url, fetcher)
            if page:
                if cat == "disease":
                    items = extract_disease_stats(page["html"], url)
                    if items:
                        result["disease"].extend(items)
                        save_to_sqlite(items, conn, "disease_stats")
                        logger.info("  Extracted %d disease records", len(items))
                elif cat == "epidemic":
                    items = extract_epidemic_data(page["html"], url)
                    if items:
                        result["epidemic"].extend(items)
                        save_to_sqlite(items, conn, "epidemic_data")
                        logger.info("  Extracted %d epidemic records", len(items))
                elif cat == "vaccination":
                    items = extract_vaccination_data(page["html"], url)
                    if items:
                        result["vaccination"].extend(items)
                        save_to_sqlite(items, conn, "vaccination_data")
                        logger.info("  Extracted %d vaccination records", len(items))
            time.sleep(2)

    if include_reports:
        for url in CATEGORIES.get("report", {}).get("urls", []):
            page = fetch_page(url, fetcher)
            if page:
                items = extract_reports(page["html"], url)
                if items:
                    result["report"].extend(items)
                    save_to_sqlite(items, conn, "health_reports")
                    logger.info("  Extracted %d reports", len(items))
            time.sleep(2)

    for key in ["disease", "epidemic", "vaccination", "report"]:
        if result[key]:
            json_path = {
                "disease": JSON_DISEASE_PATH,
                "epidemic": JSON_EPIDEMIC_PATH,
                "vaccination": JSON_VACCINATION_PATH,
                "report": JSON_REPORT_PATH,
            }[key]
            save_to_json(result[key], json_path)
            logger.info("Saved %d %s records to %s", len(result[key]), key, json_path)

    conn.close()
    return result


if __name__ == "__main__":
    results = get_chinacdc_data(include_reports=True)
    print(f"\n{'=' * 70}")
    print(f"Total disease records: {len(results['disease'])}")
    print(f"Total epidemic records: {len(results['epidemic'])}")
    print(f"Total vaccination records: {len(results['vaccination'])}")
    print(f"Total reports: {len(results['report'])}")
    print(f"SQLite: {DB_PATH}")
    print(f"{'=' * 70}")
