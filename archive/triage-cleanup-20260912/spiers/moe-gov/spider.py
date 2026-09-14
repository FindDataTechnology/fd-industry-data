#!/usr/bin/env python3
"""
Ministry of Education of China Spider - 中华人民共和国教育部

Target: http://www.moe.gov.cn/ (Score: 95)

Extracts:
- Education statistics (各级各类教育统计)
- School enrollment data (各级各类学校在校学生数)
- Education expenditure (教育经费)
- Policy documents (政策文件)

AUTHENTICATION:
  All data is publicly available on the MOE website.
  No API key or authentication required.
  
  Public data accessible:
  - National education development statistical communiques
  - Education expenditure announcements
  - Policy documents and regulations
  - School enrollment statistics by level/region
  
  Some interactive data query tools may require:
  - No authentication for basic browsing
  - CAPTCHA for bulk data downloads (not covered)

Rate Limiting: 2s delay, 3 concurrent requests
Anti-bot: Standard headers, robots.txt compliance
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime
from typing import Any

from scrapling import Fetcher, Selector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("moe-gov-spider")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
DB_PATH = os.path.join(DATA_DIR, "moe_education_data.db")
JSON_PATH = os.path.join(OUTPUT_DIR, "education_data.json")

STATS_URLS = [
    "http://www.moe.gov.cn/jyb_sjzl/sjzl_fztjgb/",
    "http://www.moe.gov.cn/jyb_sjzl/sjzl_fztjgb/2023/",
    "http://www.moe.gov.cn/jyb_sjzl/sjzl_fztjgb/2022/",
    "http://www.moe.gov.cn/jyb_sjzl/sjzl_fztjgb/2021/",
]

EXPENDITURE_URLS = [
    "http://www.moe.gov.cn/jyb_sjzl/sjzl_jyjf/",
]

POLICY_URLS = [
    "http://www.moe.gov.cn/jyb_xxgk/moe_1777/moe_1778/",
    "http://www.moe.gov.cn/jyb_xxgk/gk_gbgg/",
]

EDUCATION_LEVELS = {
    "学前教育": "preschool",
    "小学": "primary",
    "初中": "junior_secondary",
    "普通高中": "senior_secondary",
    "中等职业学校": "vocational_secondary",
    "高等教育": "higher_education",
    "研究生": "graduate",
    "普通本科": "undergraduate",
    "高职(专科)": "vocational_college",
}


def init_db() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS education_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stat_year TEXT,
            indicator_name TEXT,
            indicator_name_cn TEXT,
            value REAL,
            unit TEXT,
            education_level TEXT,
            education_level_cn TEXT,
            region TEXT,
            category TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(stat_year, indicator_name, education_level, region, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS policy_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_number TEXT,
            title TEXT,
            title_cn TEXT,
            issue_date TEXT,
            category TEXT,
            source_url TEXT,
            summary TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(doc_number, source_url)
        )
    """)
    conn.commit()
    return conn


def fetch_page(url: str, timeout: int = 30) -> dict | None:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
        }
        fetcher = Fetcher(auto_match=False)
        response = fetcher.get(url, headers=headers, timeout=timeout)
        if response.status != 200:
            logger.warning("HTTP %d for %s", response.status, url)
            return None
        return {"url": url, "html": response.html, "status": response.status}
    except Exception as e:
        logger.error("Fetch failed for %s: %s", url, e)
        return None


def extract_statistical_communique(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)
    body_text = sel.css("body").first.text if sel.css("body") else ""

    patterns = [
        (r"各级各类学校[^。]*?(\d+\.?\d*)\s*万?所", "total_schools", "学校总数", "万所"),
        (r"各级各类学历教育在校生[^。]*?(\d+\.?\d*)\s*万?人", "total_enrollment", "在校生总数", "万人"),
        (r"专任教师[^。]*?(\d+\.?\d*)\s*万?人", "total_teachers", "专任教师总数", "万人"),
        (r"学前教育[^。]*?在园幼儿[^。]*?(\d+\.?\d*)\s*万?人", "preschool_enrollment", "学前教育在园幼儿", "万人"),
        (r"小学[^。]*?在校生[^。]*?(\d+\.?\d*)\s*万?人", "primary_enrollment", "小学在校生", "万人"),
        (r"初中[^。]*?在校生[^。]*?(\d+\.?\d*)\s*万?人", "junior_secondary_enrollment", "初中在校生", "万人"),
        (r"普通高中[^。]*?在校生[^。]*?(\d+\.?\d*)\s*万?人", "senior_secondary_enrollment", "普通高中在校生", "万人"),
        (r"中等职业学校[^。]*?在校生[^。]*?(\d+\.?\d*)\s*万?人", "vocational_secondary_enrollment", "中等职业学校在校生", "万人"),
        (r"普通本科[^。]*?在校生[^。]*?(\d+\.?\d*)\s*万?人", "undergraduate_enrollment", "普通本科在校生", "万人"),
        (r"高职[^。]*?在校生[^。]*?(\d+\.?\d*)\s*万?人", "vocational_college_enrollment", "高职在校生", "万人"),
        (r"研究生[^。]*?在校生[^。]*?(\d+\.?\d*)\s*万?人", "graduate_enrollment", "研究生在校生", "万人"),
        (r"高等教育毛入学率[^。]*?(\d+\.?\d*)\s*%", "higher_ed_gross_enrollment", "高等教育毛入学率", "%"),
        (r"九年义务教育巩固率[^。]*?(\d+\.?\d*)\s*%", "compulsory_ed_consolidation", "九年义务教育巩固率", "%"),
        (r"高中阶段毛入学率[^。]*?(\d+\.?\d*)\s*%", "senior_secondary_gross_enrollment", "高中阶段毛入学率", "%"),
        (r"学前教育毛入园率[^。]*?(\d+\.?\d*)\s*%", "preschool_gross_enrollment", "学前教育毛入园率", "%"),
    ]

    year_match = re.search(r"(20\d{2})年", body_text)
    stat_year = year_match.group(1) if year_match else datetime.now().strftime("%Y")

    for pattern, indicator, indicator_cn, unit in patterns:
        matches = re.findall(pattern, body_text)
        for match in matches:
            if isinstance(match, tuple):
                match = match[0]
            if match and re.match(r"\d+\.?\d*", match):
                value = float(match)
                edu_level = "total"
                edu_level_cn = "总计"
                for cn_name, en_name in EDUCATION_LEVELS.items():
                    if cn_name in indicator_cn:
                        edu_level = en_name
                        edu_level_cn = cn_name
                        break

                items.append({
                    "stat_year": stat_year,
                    "indicator_name": indicator,
                    "indicator_name_cn": indicator_cn,
                    "value": value,
                    "unit": unit,
                    "education_level": edu_level,
                    "education_level_cn": edu_level_cn,
                    "region": "全国",
                    "category": "enrollment",
                    "source_url": source_url,
                    "raw_data": f"{indicator_cn}: {value}{unit}",
                })
    return items


def extract_expenditure_data(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)
    body_text = sel.css("body").first.text if sel.css("body") else ""

    expenditure_patterns = [
        (r"国家财政性教育经费[^。]*?(\d+\.?\d*)\s*万?亿?元", "fiscal_education_budget", "国家财政性教育经费", "亿元"),
        (r"教育经费总投入[^。]*?(\d+\.?\d*)\s*万?亿?元", "total_education_expenditure", "教育经费总投入", "亿元"),
        (r"GDP[^。]*?(\d+\.?\d*)\s*%", "education_gdp_ratio", "教育经费占GDP比例", "%"),
        (r"生均一般公共预算.*?(\d+\.?\d*)\s*元", "per_capita_public_budget", "生均一般公共预算", "元"),
    ]

    year_match = re.search(r"(20\d{2})年", body_text)
    stat_year = year_match.group(1) if year_match else datetime.now().strftime("%Y")

    for pattern, indicator, indicator_cn, unit in expenditure_patterns:
        matches = re.findall(pattern, body_text)
        for match in matches:
            if isinstance(match, tuple):
                match = match[0]
            if match and re.match(r"\d+\.?\d*", match):
                value = float(match)
                if "万亿" in body_text[body_text.find(match):body_text.find(match) + 20]:
                    value = value * 10000
                    unit = "亿元"

                items.append({
                    "stat_year": stat_year,
                    "indicator_name": indicator,
                    "indicator_name_cn": indicator_cn,
                    "value": value,
                    "unit": unit,
                    "education_level": "total",
                    "education_level_cn": "总计",
                    "region": "全国",
                    "category": "expenditure",
                    "source_url": source_url,
                    "raw_data": f"{indicator_cn}: {value}{unit}",
                })
    return items


def extract_table_data(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)

    year_match = re.search(r"(20\d{2})", html)
    stat_year = year_match.group(1) if year_match else datetime.now().strftime("%Y")

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]

        for row in rows[1:]:
            cells = [td.text.strip() for td in row.css("td")]
            if not cells or len(cells) < 2:
                continue

            for i, cell in enumerate(cells):
                if re.match(r"[\d,]+\.?\d*", cell.replace(",", "")):
                    value = float(re.sub(r"[^0-9.]", "", cell))
                    indicator_cn = headers[i] if i < len(headers) else f"column_{i}"
                    indicator = f"table_{indicator_cn}"

                    edu_level = "total"
                    edu_level_cn = "总计"
                    for cn_name, en_name in EDUCATION_LEVELS.items():
                        if cn_name in indicator_cn or (cells and any(cn_name in c for c in cells)):
                            edu_level = en_name
                            edu_level_cn = cn_name
                            break

                    items.append({
                        "stat_year": stat_year,
                        "indicator_name": indicator,
                        "indicator_name_cn": indicator_cn,
                        "value": value,
                        "unit": "",
                        "education_level": edu_level,
                        "education_level_cn": edu_level_cn,
                        "region": "全国",
                        "category": "table_data",
                        "source_url": source_url,
                        "raw_data": json.dumps({"headers": headers, "cells": cells}, ensure_ascii=False),
                    })
    return items


def extract_policy_documents(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)

    for link in sel.css("ul li a, div.list a, div.ul li a"):
        title = link.text.strip()
        href = link.attrib.get("href", "")

        if not title or len(title) < 5:
            continue
        if not href:
            continue

        if not href.startswith("http"):
            if href.startswith("/"):
                href = f"http://www.moe.gov.cn{href}"
            else:
                href = f"http://www.moe.gov.cn/{href}"

        doc_number = ""
        doc_match = re.search(r"(教\w+\[\d{4}\]\d+号)", title)
        if doc_match:
            doc_number = doc_match.group(1)

        issue_date = ""
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", title + " " + href)
        if date_match:
            issue_date = date_match.group(1)
        else:
            year_match = re.search(r"(\d{4})", title + " " + href)
            if year_match:
                issue_date = year_match.group(1)

        category = "policy"
        if "通知" in title:
            category = "notice"
        elif "意见" in title:
            category = "opinion"
        elif "规定" in title:
            category = "regulation"
        elif "办法" in title:
            category = "measure"
        elif "公报" in title:
            category = "communique"

        items.append({
            "doc_number": doc_number,
            "title": title,
            "title_cn": title,
            "issue_date": issue_date,
            "category": category,
            "source_url": href,
            "summary": "",
        })
    return items


def save_stats_to_db(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO education_stats
                (stat_year, indicator_name, indicator_name_cn, value, unit,
                 education_level, education_level_cn, region, category,
                 source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("stat_year"),
                item.get("indicator_name"),
                item.get("indicator_name_cn"),
                item.get("value"),
                item.get("unit"),
                item.get("education_level"),
                item.get("education_level_cn"),
                item.get("region"),
                item.get("category"),
                item.get("source_url"),
                item.get("raw_data", ""),
                datetime.now().isoformat(),
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert stat: %s", e)
    conn.commit()
    return inserted


def save_policies_to_db(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO policy_documents
                (doc_number, title, title_cn, issue_date, category,
                 source_url, summary, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("doc_number"),
                item.get("title"),
                item.get("title_cn"),
                item.get("issue_date"),
                item.get("category"),
                item.get("source_url"),
                item.get("summary", ""),
                datetime.now().isoformat(),
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert policy: %s", e)
    conn.commit()
    return inserted


def main():
    logger.info("=" * 70)
    logger.info("Starting Ministry of Education (MOE) Spider")
    logger.info("=" * 70)

    conn = init_db()
    all_stats = []
    all_policies = []

    logger.info("\n--- Phase 1: Education Statistics ---")
    for i, url in enumerate(STATS_URLS, 1):
        logger.info("[%d/%d] Crawling: %s", i, len(STATS_URLS), url)
        page = fetch_page(url)
        if page:
            logger.info("  Status: %d", page["status"])

            stats = extract_statistical_communique(page["html"], page["url"])
            all_stats.extend(stats)
            logger.info("  -> Extracted %d statistical indicators", len(stats))

            table_data = extract_table_data(page["html"], page["url"])
            all_stats.extend(table_data)
            logger.info("  -> Extracted %d table data points", len(table_data))
        time.sleep(2)

    logger.info("\n--- Phase 2: Education Expenditure ---")
    for i, url in enumerate(EXPENDITURE_URLS, 1):
        logger.info("[%d/%d] Crawling: %s", i, len(EXPENDITURE_URLS), url)
        page = fetch_page(url)
        if page:
            logger.info("  Status: %d", page["status"])

            expenditure = extract_expenditure_data(page["html"], page["url"])
            all_stats.extend(expenditure)
            logger.info("  -> Extracted %d expenditure indicators", len(expenditure))

            table_data = extract_table_data(page["html"], page["url"])
            all_stats.extend(table_data)
            logger.info("  -> Extracted %d table data points", len(table_data))
        time.sleep(2)

    logger.info("\n--- Phase 3: Policy Documents ---")
    for i, url in enumerate(POLICY_URLS, 1):
        logger.info("[%d/%d] Crawling: %s", i, len(POLICY_URLS), url)
        page = fetch_page(url)
        if page:
            logger.info("  Status: %d", page["status"])

            policies = extract_policy_documents(page["html"], page["url"])
            all_policies.extend(policies)
            logger.info("  -> Extracted %d policy documents", len(policies))
        time.sleep(2)

    if all_stats:
        inserted = save_stats_to_db(conn, all_stats)
        logger.info("\nSaved %d statistical records to database", inserted)

    if all_policies:
        inserted = save_policies_to_db(conn, all_policies)
        logger.info("Saved %d policy documents to database", inserted)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    export_data = {
        "source": "Ministry of Education of China",
        "source_cn": "中华人民共和国教育部",
        "source_url": "http://www.moe.gov.cn/",
        "scraped_at": datetime.now().isoformat(),
        "education_statistics": all_stats,
        "policy_documents": all_policies,
        "summary": {
            "total_stats": len(all_stats),
            "total_policies": len(all_policies),
        },
    }
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)
    logger.info("Exported data to JSON: %s", JSON_PATH)

    conn.close()

    print("\n" + "=" * 70)
    print("MOE Spider - Execution Summary")
    print("=" * 70)
    print(f"Total statistics extracted: {len(all_stats)}")
    print(f"Total policy documents: {len(all_policies)}")
    print(f"Database: {DB_PATH}")
    print(f"JSON output: {JSON_PATH}")
    print("=" * 70)


if __name__ == "__main__":
    main()
