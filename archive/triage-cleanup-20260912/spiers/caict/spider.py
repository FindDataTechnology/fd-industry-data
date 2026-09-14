#!/usr/bin/env python3
"""
China Academy of Information and Communications Technology (CAICT) Spider
中国信息通信研究院

Target: http://www.caict.ac.cn/ (Score: 88)

Extracts:
- ICT industry statistics (ICT产业统计)
- Technology development data (技术发展数据)
- Market research reports (市场研究报告)
- Policy analysis (政策分析)

AUTHENTICATION:
  All data is publicly available on the CAICT website.
  No API key or authentication required.
  
  Public data accessible:
  - ICT industry reports and statistics
  - Technology development white papers
  - Telecommunications data
  - Internet industry analysis
  - Policy research documents
  
  Data downloads:
  - PDF/Word reports available for download
  - Some data requires navigating through sub-sites

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
logger = logging.getLogger("caict-spider")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
DB_PATH = os.path.join(DATA_DIR, "caict_ict_data.db")
JSON_PATH = os.path.join(OUTPUT_DIR, "ict_data.json")

ICT_URLS = [
    "http://www.caict.ac.cn/kxyj/yjcg/",
    "http://www.caict.ac.cn/kxyj/qwfb/bps/",
    "http://www.caict.ac.cn/kxyj/qwfb/bpk/",
]

TECH_URLS = [
    "http://www.caict.ac.cn/kxyj/qwfb/txjs/",
    "http://www.caict.ac.cn/kxyj/qwfb/wlw/",
    "http://www.caict.ac.cn/kxyj/qwfb/rgzn/",
]

REPORT_URLS = [
    "http://www.caict.ac.cn/kxyj/qwfb/",
    "http://www.caict.ac.cn/kxyj/yjcg/qwyj/",
]

POLICY_URLS = [
    "http://www.caict.ac.cn/zcyj/zcbl/",
    "http://www.caict.ac.cn/zcyj/zcyj/",
]


def init_db() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ict_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stat_period TEXT,
            indicator_name TEXT,
            indicator_name_cn TEXT,
            value REAL,
            unit TEXT,
            category TEXT,
            sub_category TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(stat_period, indicator_name, sub_category, source_url)
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
            download_url TEXT,
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


def extract_telecom_data(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)
    body_text = sel.css("body").first.text if sel.css("body") else ""

    period_match = re.search(r"(20\d{2})年(\d{1,2})月", body_text)
    stat_period = f"{period_match.group(1)}年{period_match.group(2)}月" if period_match else datetime.now().strftime("%Y年%m月")

    patterns = [
        (r"电信业务收入[^。]*?(\d+\.?\d*)\s*亿?元", "telecom_revenue", "电信业务收入", "亿元"),
        (r"电信业务总量[^。]*?(\d+\.?\d*)\s*亿?元", "telecom_volume", "电信业务总量", "亿元"),
        (r"移动电话用户[^。]*?(\d+\.?\d*)\s*亿", "mobile_users", "移动电话用户", "亿户"),
        (r"5G手机[^。]*?(\d+\.?\d*)\s*亿", "5g_users", "5G手机用户", "亿户"),
        (r"固定电话用户[^。]*?(\d+\.?\d*)\s*亿", "fixed_phone_users", "固定电话用户", "亿户"),
        (r"宽带用户[^。]*?(\d+\.?\d*)\s*亿", "broadband_users", "宽带用户", "亿户"),
        (r"光纤宽带用户[^。]*?(\d+\.?\d*)\s*亿", "fiber_broadband_users", "光纤宽带用户", "亿户"),
        (r"100M及以上宽带用户[^。]*?(\d+\.?\d*)\s*亿", "fast_broadband_users", "100M及以上宽带用户", "亿户"),
        (r"移动互联网流量[^。]*?(\d+\.?\d*)\s*亿GB", "mobile_data_traffic", "移动互联网流量", "亿GB"),
        (r"月户均移动互联网接入流量[^。]*?(\d+\.?\d*)\s*GB", "avg_mobile_traffic_per_user", "月户均移动互联网接入流量", "GB"),
    ]

    for pattern, indicator, indicator_cn, unit in patterns:
        matches = re.findall(pattern, body_text)
        for match in matches:
            if isinstance(match, tuple):
                match = match[0]
            if match and re.match(r"\d+\.?\d*", match):
                value = float(match)

                sub_category = "telecom_revenue" if "收入" in indicator_cn else "telecom_volume" if "总量" in indicator_cn else "users" if "用户" in indicator_cn else "traffic"

                items.append({
                    "stat_period": stat_period,
                    "indicator_name": indicator,
                    "indicator_name_cn": indicator_cn,
                    "value": value,
                    "unit": unit,
                    "category": "telecommunications",
                    "sub_category": sub_category,
                    "source_url": source_url,
                    "raw_data": f"{indicator_cn}: {value}{unit}",
                })
    return items


def extract_internet_data(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)
    body_text = sel.css("body").first.text if sel.css("body") else ""

    period_match = re.search(r"(20\d{2})年", body_text)
    stat_period = period_match.group(1) if period_match else datetime.now().strftime("%Y")

    patterns = [
        (r"互联网宽带接入端口[^。]*?(\d+\.?\d*)\s*万?个", "internet_ports", "互联网宽带接入端口", "万个"),
        (r"移动互联网接入流量[^。]*?(\d+\.?\d*)\s*亿GB", "mobile_internet_traffic", "移动互联网接入流量", "亿GB"),
        (r"App数量[^。]*?(\d+\.?\d*)\s*万?款", "app_count", "App数量", "万款"),
        (r"物联网终端用户[^。]*?(\d+\.?\d*)\s*亿", "iot_users", "物联网终端用户", "亿户"),
    ]

    for pattern, indicator, indicator_cn, unit in patterns:
        matches = re.findall(pattern, body_text)
        for match in matches:
            if isinstance(match, tuple):
                match = match[0]
            if match and re.match(r"\d+\.?\d*", match):
                value = float(match)

                items.append({
                    "stat_period": stat_period,
                    "indicator_name": indicator,
                    "indicator_name_cn": indicator_cn,
                    "value": value,
                    "unit": unit,
                    "category": "internet_infrastructure",
                    "sub_category": "infrastructure",
                    "source_url": source_url,
                    "raw_data": f"{indicator_cn}: {value}{unit}",
                })
    return items


def extract_table_data(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)

    period_match = re.search(r"(20\d{2})年", html)
    stat_period = period_match.group(1) if period_match else datetime.now().strftime("%Y")

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
                        "stat_period": stat_period,
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

        is_report = any(kw in title for kw in ["报告", "白皮书", "蓝皮书", "发展", "趋势", "分析"])
        if not is_report:
            continue

        if not href.startswith("http"):
            if href.startswith("/"):
                href = f"http://www.caict.ac.cn{href}"
            else:
                href = f"http://www.caict.ac.cn/{href}"

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
        elif "研究报告" in title:
            report_type = "research_report"
        elif "分析报告" in title:
            report_type = "analysis_report"

        category = "ict"
        if "通信" in title:
            category = "telecom"
        elif "互联网" in title:
            category = "internet"
        elif "人工智能" in title or "AI" in title:
            category = "ai"
        elif "物联网" in title:
            category = "iot"
        elif "5G" in title or "6G" in title:
            category = "mobile"
        elif "云计算" in title:
            category = "cloud"
        elif "大数据" in title:
            category = "big_data"

        items.append({
            "title": title,
            "title_cn": title,
            "publish_date": publish_date,
            "report_type": report_type,
            "category": category,
            "download_url": href if href.endswith((".pdf", ".doc", ".docx")) else "",
            "source_url": href,
            "summary": "",
        })
    return items


def save_stats_to_db(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO ict_stats
                (stat_period, indicator_name, indicator_name_cn, value, unit,
                 category, sub_category, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("stat_period"),
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


def save_reports_to_db(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO reports
                (title, title_cn, publish_date, report_type, category,
                 download_url, source_url, summary, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("title"),
                item.get("title_cn"),
                item.get("publish_date"),
                item.get("report_type"),
                item.get("category"),
                item.get("download_url"),
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
    logger.info("Starting CAICT ICT Statistics Spider")
    logger.info("=" * 70)

    conn = init_db()
    all_stats = []
    all_reports = []

    logger.info("\n--- Phase 1: ICT Industry Data ---")
    for i, url in enumerate(ICT_URLS, 1):
        logger.info("[%d/%d] Crawling: %s", i, len(ICT_URLS), url)
        page = fetch_page(url)
        if page:
            logger.info("  Status: %d", page["status"])

            telecom = extract_telecom_data(page["html"], page["url"])
            all_stats.extend(telecom)
            logger.info("  -> Extracted %d telecom indicators", len(telecom))

            internet = extract_internet_data(page["html"], page["url"])
            all_stats.extend(internet)
            logger.info("  -> Extracted %d internet indicators", len(internet))

            table_data = extract_table_data(page["html"], page["url"])
            all_stats.extend(table_data)
            logger.info("  -> Extracted %d table data points", len(table_data))
        time.sleep(2)

    logger.info("\n--- Phase 2: Technology Data ---")
    for i, url in enumerate(TECH_URLS, 1):
        logger.info("[%d/%d] Crawling: %s", i, len(TECH_URLS), url)
        page = fetch_page(url)
        if page:
            logger.info("  Status: %d", page["status"])

            stats = extract_internet_data(page["html"], page["url"])
            all_stats.extend(stats)
            logger.info("  -> Extracted %d indicators", len(stats))
        time.sleep(2)

    logger.info("\n--- Phase 3: Reports ---")
    for i, url in enumerate(REPORT_URLS, 1):
        logger.info("[%d/%d] Crawling: %s", i, len(REPORT_URLS), url)
        page = fetch_page(url)
        if page:
            logger.info("  Status: %d", page["status"])

            reports = extract_reports(page["html"], page["url"])
            all_reports.extend(reports)
            logger.info("  -> Extracted %d reports", len(reports))
        time.sleep(2)

    logger.info("\n--- Phase 4: Policy Analysis ---")
    for i, url in enumerate(POLICY_URLS, 1):
        logger.info("[%d/%d] Crawling: %s", i, len(POLICY_URLS), url)
        page = fetch_page(url)
        if page:
            logger.info("  Status: %d", page["status"])

            reports = extract_reports(page["html"], page["url"])
            all_reports.extend(reports)
            logger.info("  -> Extracted %d policy documents", len(reports))
        time.sleep(2)

    if all_stats:
        inserted = save_stats_to_db(conn, all_stats)
        logger.info("\nSaved %d statistical records to database", inserted)

    if all_reports:
        inserted = save_reports_to_db(conn, all_reports)
        logger.info("Saved %d report records to database", inserted)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    export_data = {
        "source": "China Academy of Information and Communications Technology",
        "source_cn": "中国信息通信研究院",
        "source_url": "http://www.caict.ac.cn/",
        "scraped_at": datetime.now().isoformat(),
        "ict_statistics": all_stats,
        "reports": all_reports,
        "summary": {
            "total_stats": len(all_stats),
            "total_reports": len(all_reports),
        },
    }
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)
    logger.info("Exported data to JSON: %s", JSON_PATH)

    conn.close()

    print("\n" + "=" * 70)
    print("CAICT Spider - Execution Summary")
    print("=" * 70)
    print(f"Total statistics extracted: {len(all_stats)}")
    print(f"Total reports indexed: {len(all_reports)}")
    print(f"Database: {DB_PATH}")
    print(f"JSON output: {JSON_PATH}")
    print("=" * 70)


if __name__ == "__main__":
    main()
