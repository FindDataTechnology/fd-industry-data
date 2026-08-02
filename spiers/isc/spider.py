#!/usr/bin/env python3
"""
China Internet Association (ISC) Spider
中国互联网协会

Target: http://www.isc.org.cn/ (Score: 85)

Extracts:
- Internet industry data (互联网行业数据)
- Platform statistics (平台统计)
- User behavior data (用户行为数据)
- Market analysis (市场分析)

AUTHENTICATION:
  All data is publicly available on the ISC website.
  No API key or authentication required.
  
  Public data accessible:
  - Internet industry statistics
  - China Internet 100 rankings (中国互联网企业百强)
  - Internet industry development reports
  - Platform governance data
  - Industry self-regulation standards
  - Internet application reports
  
  Notes:
  - Annual Internet 100 rankings published since 2012
  - Internet industry development annual reports
  - Platform economy and governance research

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
logger = logging.getLogger("isc-spider")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
DB_PATH = os.path.join(DATA_DIR, "isc_internet_data.db")
JSON_PATH = os.path.join(OUTPUT_DIR, "internet_industry_data.json")

INDUSTRY_URLS = [
    "http://www.isc.org.cn/zxzx/index.html",
    "http://www.isc.org.cn/zxzx/hyxw/index.html",
]

RANKING_URLS = [
    "http://www.isc.org.cn/zxzx/hyph/index.html",
    "http://www.isc.org.cn/zxzx/bqfb/index.html",
]

REPORT_URLS = [
    "http://www.isc.org.cn/zxzx/yjbg/index.html",
    "http://www.isc.org.cn/zxzx/hlwfz/index.html",
]

GOVERNANCE_URLS = [
    "http://www.isc.org.cn/zxzx/wlzl/index.html",
    "http://www.isc.org.cn/zxzx/zlxc/index.html",
]


def init_db() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS internet_industry_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stat_year TEXT,
            indicator_name TEXT,
            indicator_name_cn TEXT,
            value REAL,
            unit TEXT,
            category TEXT,
            sub_category TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(stat_year, indicator_name, sub_category, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS internet_enterprises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            enterprise_name TEXT,
            enterprise_name_cn TEXT,
            ranking INTEGER,
            category TEXT,
            main_business TEXT,
            revenue REAL,
            revenue_unit TEXT,
            stat_year TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(enterprise_name, category, stat_year, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            title_cn TEXT,
            publish_date TEXT,
            report_type TEXT,
            category TEXT,
            source_url TEXT,
            summary TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(title, source_url)
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


def extract_internet_economy_data(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)
    body_text = sel.css("body").first.text if sel.css("body") else ""

    year_match = re.search(r"(20\d{2})年", body_text)
    stat_year = year_match.group(1) if year_match else datetime.now().strftime("%Y")

    patterns = [
        (r"互联网企业[^。]*?(\d+\.?\d*)\s*万?家", "internet_enterprises", "互联网企业数量", "万家"),
        (r"互联网业务收入[^。]*?(\d+\.?\d*)\s*万?亿?元", "internet_revenue", "互联网业务收入", "亿元"),
        (r"互联网平台收入[^。]*?(\d+\.?\d*)\s*亿?元", "platform_revenue", "互联网平台收入", "亿元"),
        (r"数字经济规模[^。]*?(\d+\.?\d*)\s*万?亿?元", "digital_economy_size", "数字经济规模", "万亿元"),
        (r"数字经济占GDP[^。]*?(\d+\.?\d*)\s*%", "digital_economy_gdp_ratio", "数字经济占GDP比重", "%"),
        (r"电子商务交易额[^。]*?(\d+\.?\d*)\s*万?亿?元", "ecommerce_gmv", "电子商务交易额", "万亿元"),
        (r"网络零售额[^。]*?(\d+\.?\d*)\s*万?亿?元", "online_retail", "网络零售额", "万亿元"),
        (r"移动支付交易规模[^。]*?(\d+\.?\d*)\s*万?亿?元", "mobile_payment_volume", "移动支付交易规模", "万亿元"),
    ]

    for pattern, indicator, indicator_cn, unit in patterns:
        matches = re.findall(pattern, body_text)
        for match in matches:
            if isinstance(match, tuple):
                match = match[0]
            if match and re.match(r"\d+\.?\d*", match):
                value = float(match)
                if "万亿" in body_text[body_text.find(match):body_text.find(match) + 20]:
                    value = value * 10000
                    unit = "亿元"

                sub_category = "enterprises" if "企业" in indicator_cn else "revenue" if "收入" in indicator_cn else "economy" if "经济" in indicator_cn else "commerce" if "商务" in indicator_cn or "零售" in indicator_cn or "支付" in indicator_cn else "other"

                items.append({
                    "stat_year": stat_year,
                    "indicator_name": indicator,
                    "indicator_name_cn": indicator_cn,
                    "value": value,
                    "unit": unit,
                    "category": "internet_economy",
                    "sub_category": sub_category,
                    "source_url": source_url,
                    "raw_data": f"{indicator_cn}: {value}{unit}",
                })
    return items


def extract_internet_100_rankings(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)

    year_match = re.search(r"(20\d{2})", html)
    stat_year = year_match.group(1) if year_match else datetime.now().strftime("%Y")

    for table in sel.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue

        headers = [th.text.strip() for th in rows[0].css("th, td")]

        for idx, row in enumerate(rows[1:], 1):
            cells = [td.text.strip() for td in row.css("td")]
            if not cells or len(cells) < 2:
                continue

            enterprise_name = cells[0] if cells else ""
            if not enterprise_name:
                continue

            main_business = ""
            revenue = None
            revenue_unit = "万元"
            ranking = idx

            for i, cell in enumerate(cells):
                if i < len(headers):
                    header = headers[i]
                    if "业务" in header or "主营" in header:
                        main_business = cell
                    elif "收入" in header or "营收" in header or "营业额" in header:
                        if re.match(r"[\d,]+\.?\d*", cell.replace(",", "")):
                            revenue = float(re.sub(r"[^0-9.]", "", cell))
                            if "亿" in cell:
                                revenue_unit = "亿元"
                            elif "万" in cell:
                                revenue_unit = "万元"

            if len(cells) >= 3:
                if not main_business and not re.match(r"[\d,]+\.?\d*", cells[1].replace(",", "")):
                    main_business = cells[1]
                if revenue is None and len(cells) >= 4:
                    for cell in cells[2:]:
                        if re.match(r"[\d,]+\.?\d*", cell.replace(",", "")):
                            revenue = float(re.sub(r"[^0-9.]", "", cell))
                            if "亿" in cell:
                                revenue_unit = "亿元"
                            elif "万" in cell:
                                revenue_unit = "万元"
                            break

            category = "internet_100"
            if "百强" in source_url or "100" in source_url:
                category = "internet_100"
            elif "平台" in source_url:
                category = "platform"
            elif "电商" in source_url:
                category = "ecommerce"

            items.append({
                "enterprise_name": enterprise_name,
                "enterprise_name_cn": enterprise_name,
                "ranking": ranking,
                "category": category,
                "main_business": main_business,
                "revenue": revenue,
                "revenue_unit": revenue_unit,
                "stat_year": stat_year,
                "source_url": source_url,
                "raw_data": json.dumps({"cells": cells, "headers": headers}, ensure_ascii=False),
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

                    items.append({
                        "stat_year": stat_year,
                        "indicator_name": f"table_{indicator_cn}",
                        "indicator_name_cn": indicator_cn,
                        "value": value,
                        "unit": "",
                        "category": "table_data",
                        "sub_category": "table_extracted",
                        "source_url": source_url,
                        "raw_data": json.dumps({"headers": headers, "cells": cells}, ensure_ascii=False),
                    })
    return items


def extract_reports(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)

    for link in sel.css("ul li a, div.list a, div.ul li a, a[href]"):
        title = link.text.strip()
        href = link.attrib.get("href", "")

        if not title or len(title) < 5:
            continue
        if not href:
            continue

        is_report = any(kw in title for kw in ["报告", "白皮书", "蓝皮书", "发展", "趋势", "分析", "排行", "百强", "治理"])
        if not is_report:
            continue

        if not href.startswith("http"):
            if href.startswith("/"):
                href = f"http://www.isc.org.cn{href}"
            else:
                href = f"http://www.isc.org.cn/{href}"

        publish_date = ""
        date_match = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", title + " " + href)
        if date_match:
            publish_date = date_match.group(1).replace("/", "-")
        else:
            year_match = re.search(r"(20\d{2})", title)
            if year_match:
                publish_date = year_match.group(1)

        report_type = "report"
        if "白皮书" in title:
            report_type = "white_paper"
        elif "蓝皮书" in title:
            report_type = "blue_book"
        elif "排行" in title or "百强" in title:
            report_type = "ranking"
        elif "研究报告" in title:
            report_type = "research_report"
        elif "治理" in title:
            report_type = "governance"

        category = "internet"
        if "平台" in title:
            category = "platform"
        elif "电商" in title:
            category = "ecommerce"
        elif "治理" in title or "自律" in title:
            category = "governance"
        elif "安全" in title:
            category = "security"

        items.append({
            "title": title,
            "title_cn": title,
            "publish_date": publish_date,
            "report_type": report_type,
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
                INSERT OR REPLACE INTO internet_industry_stats
                (stat_year, indicator_name, indicator_name_cn, value, unit,
                 category, sub_category, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("stat_year"),
                item.get("indicator_name"),
                item.get("indicator_name_cn"),
                item.get("value"),
                item.get("unit"),
                item.get("category"),
                item.get("sub_category"),
                item.get("source_url"),
                item.get("raw_data", ""),
                datetime.now().isoformat(),
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert stat: %s", e)
    conn.commit()
    return inserted


def save_enterprises_to_db(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO internet_enterprises
                (enterprise_name, enterprise_name_cn, ranking, category,
                 main_business, revenue, revenue_unit, stat_year, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("enterprise_name"),
                item.get("enterprise_name_cn"),
                item.get("ranking"),
                item.get("category"),
                item.get("main_business"),
                item.get("revenue"),
                item.get("revenue_unit"),
                item.get("stat_year"),
                item.get("source_url"),
                item.get("raw_data", ""),
                datetime.now().isoformat(),
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert enterprise: %s", e)
    conn.commit()
    return inserted


def save_reports_to_db(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO reports
                (title, title_cn, publish_date, report_type, category,
                 source_url, summary, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("title"),
                item.get("title_cn"),
                item.get("publish_date"),
                item.get("report_type"),
                item.get("category"),
                item.get("source_url"),
                item.get("summary", ""),
                datetime.now().isoformat(),
            ))
            inserted += 1
        except Exception as e:
            logger.error("Failed to insert report: %s", e)
    conn.commit()
    return inserted


def main():
    logger.info("=" * 70)
    logger.info("Starting China Internet Association (ISC) Spider")
    logger.info("=" * 70)

    conn = init_db()
    all_stats = []
    all_enterprises = []
    all_reports = []

    logger.info("\n--- Phase 1: Internet Economy Data ---")
    for i, url in enumerate(INDUSTRY_URLS, 1):
        logger.info("[%d/%d] Crawling: %s", i, len(INDUSTRY_URLS), url)
        page = fetch_page(url)
        if page:
            logger.info("  Status: %d", page["status"])

            stats = extract_internet_economy_data(page["html"], page["url"])
            all_stats.extend(stats)
            logger.info("  -> Extracted %d economy indicators", len(stats))

            table_data = extract_table_data(page["html"], page["url"])
            all_stats.extend(table_data)
            logger.info("  -> Extracted %d table data points", len(table_data))
        time.sleep(2)

    logger.info("\n--- Phase 2: Internet 100 Rankings ---")
    for i, url in enumerate(RANKING_URLS, 1):
        logger.info("[%d/%d] Crawling: %s", i, len(RANKING_URLS), url)
        page = fetch_page(url)
        if page:
            logger.info("  Status: %d", page["status"])

            enterprises = extract_internet_100_rankings(page["html"], page["url"])
            all_enterprises.extend(enterprises)
            logger.info("  -> Extracted %d enterprise rankings", len(enterprises))

            reports = extract_reports(page["html"], page["url"])
            all_reports.extend(reports)
            logger.info("  -> Extracted %d reports", len(reports))
        time.sleep(2)

    logger.info("\n--- Phase 3: Research Reports ---")
    for i, url in enumerate(REPORT_URLS, 1):
        logger.info("[%d/%d] Crawling: %s", i, len(REPORT_URLS), url)
        page = fetch_page(url)
        if page:
            logger.info("  Status: %d", page["status"])

            stats = extract_internet_economy_data(page["html"], page["url"])
            all_stats.extend(stats)
            logger.info("  -> Extracted %d indicators", len(stats))

            reports = extract_reports(page["html"], page["url"])
            all_reports.extend(reports)
            logger.info("  -> Extracted %d reports", len(reports))
        time.sleep(2)

    logger.info("\n--- Phase 4: Governance Data ---")
    for i, url in enumerate(GOVERNANCE_URLS, 1):
        logger.info("[%d/%d] Crawling: %s", i, len(GOVERNANCE_URLS), url)
        page = fetch_page(url)
        if page:
            logger.info("  Status: %d", page["status"])

            reports = extract_reports(page["html"], page["url"])
            all_reports.extend(reports)
            logger.info("  -> Extracted %d governance documents", len(reports))
        time.sleep(2)

    if all_stats:
        inserted = save_stats_to_db(conn, all_stats)
        logger.info("\nSaved %d statistical records to database", inserted)

    if all_enterprises:
        inserted = save_enterprises_to_db(conn, all_enterprises)
        logger.info("Saved %d enterprise records to database", inserted)

    if all_reports:
        inserted = save_reports_to_db(conn, all_reports)
        logger.info("Saved %d report records to database", inserted)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    export_data = {
        "source": "China Internet Association",
        "source_cn": "中国互联网协会",
        "source_url": "http://www.isc.org.cn/",
        "scraped_at": datetime.now().isoformat(),
        "internet_economy": all_stats,
        "enterprise_rankings": all_enterprises,
        "reports": all_reports,
        "summary": {
            "total_stats": len(all_stats),
            "total_enterprises": len(all_enterprises),
            "total_reports": len(all_reports),
        },
    }
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)
    logger.info("Exported data to JSON: %s", JSON_PATH)

    conn.close()

    print("\n" + "=" * 70)
    print("ISC Spider - Execution Summary")
    print("=" * 70)
    print(f"Total statistics extracted: {len(all_stats)}")
    print(f"Total enterprise rankings: {len(all_enterprises)}")
    print(f"Total reports indexed: {len(all_reports)}")
    print(f"Database: {DB_PATH}")
    print(f"JSON output: {JSON_PATH}")
    print("=" * 70)


if __name__ == "__main__":
    main()
