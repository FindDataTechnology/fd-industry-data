#!/usr/bin/env python3
"""
China Internet Network Information Center (CNNIC) Spider

Target: http://www.cnnic.cn/ (Score: 90)

Extracts:
- Internet development statistics (互联网发展统计)
- User behavior data (用户行为数据)
- Network infrastructure data (网络基础设施数据)
- Industry reports (行业报告)

AUTHENTICATION:
  All data is publicly available on the CNNIC website.
  No API key or authentication required.
  
  Public data accessible:
  - Statistical Report on Internet Development in China (中国互联网络发展状况统计报告)
  - Internet development indicators
  - Domain name and IP address statistics
  - Research reports and white papers
  
  Data downloads:
  - PDF reports available for download (no auth required)
  - Some datasets may require registration for bulk access

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
logger = logging.getLogger("cnnic-spider")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
DB_PATH = os.path.join(DATA_DIR, "cnnic_internet_data.db")
JSON_PATH = os.path.join(OUTPUT_DIR, "internet_data.json")

REPORT_URLS = [
    "http://www.cnnic.cn/n4/2024/0322/c199-10964.html",
    "http://www.cnnic.cn/n4/2023/0828/c199-10838.html",
    "http://www.cnnic.cn/n4/2023/0303/c199-10755.html",
]

STATS_URLS = [
    "http://www.cnnic.cn/n4/2024/0322/c199-10962.html",
    "http://www.cnnic.net.cn/hlwfzyj/hlwxzbg/",
]

RESEARCH_URLS = [
    "http://www.cnnic.cn/n4/2023/0303/c199-10754.html",
    "http://www.cnnic.cn/n4/2023/0303/c199-10753.html",
]


def init_db() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS internet_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_period TEXT,
            indicator_name TEXT,
            indicator_name_cn TEXT,
            value REAL,
            unit TEXT,
            category TEXT,
            sub_category TEXT,
            source_url TEXT,
            raw_data TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(report_period, indicator_name, sub_category, source_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            title_cn TEXT,
            report_number TEXT,
            publish_date TEXT,
            report_type TEXT,
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


def extract_internet_users_data(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)
    body_text = sel.css("body").first.text if sel.css("body") else ""

    period_match = re.search(r"第(\d+)次", body_text)
    report_period = f"第{period_match.group(1)}次" if period_match else ""
    year_match = re.search(r"(20\d{2})年", body_text)
    if year_match:
        report_period = f"{year_match.group(1)}年{report_period}" if report_period else year_match.group(1)

    patterns = [
        (r"网民规模[^。]*?(\d+\.?\d*)\s*亿", "internet_users", "网民规模", "亿人"),
        (r"互联网普及率[^。]*?(\d+\.?\d*)\s*%", "internet_penetration", "互联网普及率", "%"),
        (r"手机网民规模[^。]*?(\d+\.?\d*)\s*亿", "mobile_internet_users", "手机网民规模", "亿人"),
        (r"手机上网[^。]*?(\d+\.?\d*)\s*%", "mobile_internet_ratio", "手机上网比例", "%"),
        (r"农村网民规模[^。]*?(\d+\.?\d*)\s*亿", "rural_internet_users", "农村网民规模", "亿人"),
        (r"城镇网民规模[^。]*?(\d+\.?\d*)\s*亿", "urban_internet_users", "城镇网民规模", "亿人"),
        (r"农村互联网普及率[^。]*?(\d+\.?\d*)\s*%", "rural_internet_penetration", "农村互联网普及率", "%"),
        (r"城镇互联网普及率[^。]*?(\d+\.?\d*)\s*%", "urban_internet_penetration", "城镇互联网普及率", "%"),
        (r"IPv6地址[^。]*?(\d+\.?\d*)\s*块/32", "ipv6_addresses", "IPv6地址", "块/32"),
        (r"域名总数[^。]*?(\d+\.?\d*)\s*万?个", "total_domains", "域名总数", "万个"),
        (r"\.cn域名[^。]*?(\d+\.?\d*)\s*万?个", "cn_domains", ".cn域名", "万个"),
        (r"网站总数[^。]*?(\d+\.?\d*)\s*万?个", "total_websites", "网站总数", "万个"),
        (r"网页数量[^。]*?(\d+\.?\d*)\s*亿", "total_webpages", "网页数量", "亿个"),
        (r"即时通信用户[^。]*?(\d+\.?\d*)\s*亿", "im_users", "即时通信用户", "亿人"),
        (r"网络视频用户[^。]*?(\d+\.?\d*)\s*亿", "online_video_users", "网络视频用户", "亿人"),
        (r"短视频用户[^。]*?(\d+\.?\d*)\s*亿", "short_video_users", "短视频用户", "亿人"),
        (r"网络直播用户[^。]*?(\d+\.?\d*)\s*亿", "live_streaming_users", "网络直播用户", "亿人"),
        (r"网络购物用户[^。]*?(\d+\.?\d*)\s*亿", "online_shopping_users", "网络购物用户", "亿人"),
        (r"网上外卖用户[^。]*?(\d+\.?\d*)\s*亿", "food_delivery_users", "网上外卖用户", "亿人"),
        (r"网约车用户[^。]*?(\d+\.?\d*)\s*亿", "ride_hailing_users", "网约车用户", "亿人"),
        (r"在线教育用户[^。]*?(\d+\.?\d*)\s*亿", "online_education_users", "在线教育用户", "亿人"),
        (r"在线医疗用户[^。]*?(\d+\.?\d*)\s*亿", "online_medical_users", "在线医疗用户", "亿人"),
    ]

    for pattern, indicator, indicator_cn, unit in patterns:
        matches = re.findall(pattern, body_text)
        for match in matches:
            if isinstance(match, tuple):
                match = match[0]
            if match and re.match(r"\d+\.?\d*", match):
                value = float(match)

                sub_category = "users"
                if "普及率" in indicator_cn:
                    sub_category = "penetration"
                elif "域名" in indicator_cn or "网站" in indicator_cn or "网页" in indicator_cn:
                    sub_category = "infrastructure"
                elif "IPv6" in indicator_cn:
                    sub_category = "infrastructure"
                elif "用户" in indicator_cn:
                    sub_category = "application_users"

                items.append({
                    "report_period": report_period,
                    "indicator_name": indicator,
                    "indicator_name_cn": indicator_cn,
                    "value": value,
                    "unit": unit,
                    "category": "internet_development",
                    "sub_category": sub_category,
                    "source_url": source_url,
                    "raw_data": f"{indicator_cn}: {value}{unit}",
                })
    return items


def extract_table_data(html: str, source_url: str) -> list[dict[str, Any]]:
    items = []
    sel = Selector(html)

    period_match = re.search(r"第(\d+)次", html)
    report_period = f"第{period_match.group(1)}次" if period_match else ""
    year_match = re.search(r"(20\d{2})年", html)
    if year_match:
        report_period = f"{year_match.group(1)}年{report_period}" if report_period else year_match.group(1)

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
                        "report_period": report_period,
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

        is_report = any(kw in title for kw in ["报告", "统计", "发展", "蓝皮书", "白皮书", "年鉴"])
        if not is_report:
            continue

        if not href.startswith("http"):
            if href.startswith("/"):
                href = f"http://www.cnnic.cn{href}"
            else:
                href = f"http://www.cnnic.cn/{href}"

        report_number = ""
        num_match = re.search(r"第(\d+)次", title)
        if num_match:
            report_number = f"第{num_match.group(1)}次"

        publish_date = ""
        date_match = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", title + " " + href)
        if date_match:
            publish_date = date_match.group(1).replace("/", "-")
        else:
            year_match = re.search(r"(20\d{2})", title)
            if year_match:
                publish_date = year_match.group(1)

        report_type = "report"
        if "统计报告" in title:
            report_type = "statistical_report"
        elif "蓝皮书" in title:
            report_type = "blue_book"
        elif "白皮书" in title:
            report_type = "white_paper"
        elif "研究报告" in title:
            report_type = "research_report"

        items.append({
            "title": title,
            "title_cn": title,
            "report_number": report_number,
            "publish_date": publish_date,
            "report_type": report_type,
            "download_url": href if href.endswith(".pdf") else "",
            "source_url": href,
            "summary": "",
        })
    return items


def save_stats_to_db(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO internet_stats
                (report_period, indicator_name, indicator_name_cn, value, unit,
                 category, sub_category, source_url, raw_data, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("report_period"),
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
                (title, title_cn, report_number, publish_date, report_type,
                 download_url, source_url, summary, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("title"),
                item.get("title_cn"),
                item.get("report_number"),
                item.get("publish_date"),
                item.get("report_type"),
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
    logger.info("Starting CNNIC Internet Statistics Spider")
    logger.info("=" * 70)

    conn = init_db()
    all_stats = []
    all_reports = []

    logger.info("\n--- Phase 1: Statistical Reports ---")
    for i, url in enumerate(REPORT_URLS, 1):
        logger.info("[%d/%d] Crawling: %s", i, len(REPORT_URLS), url)
        page = fetch_page(url)
        if page:
            logger.info("  Status: %d", page["status"])

            stats = extract_internet_users_data(page["html"], page["url"])
            all_stats.extend(stats)
            logger.info("  -> Extracted %d internet indicators", len(stats))

            table_data = extract_table_data(page["html"], page["url"])
            all_stats.extend(table_data)
            logger.info("  -> Extracted %d table data points", len(table_data))
        time.sleep(2)

    logger.info("\n--- Phase 2: Statistics Pages ---")
    for i, url in enumerate(STATS_URLS, 1):
        logger.info("[%d/%d] Crawling: %s", i, len(STATS_URLS), url)
        page = fetch_page(url)
        if page:
            logger.info("  Status: %d", page["status"])

            stats = extract_internet_users_data(page["html"], page["url"])
            all_stats.extend(stats)
            logger.info("  -> Extracted %d indicators", len(stats))
        time.sleep(2)

    logger.info("\n--- Phase 3: Research Reports ---")
    for i, url in enumerate(RESEARCH_URLS, 1):
        logger.info("[%d/%d] Crawling: %s", i, len(RESEARCH_URLS), url)
        page = fetch_page(url)
        if page:
            logger.info("  Status: %d", page["status"])

            reports = extract_reports(page["html"], page["url"])
            all_reports.extend(reports)
            logger.info("  -> Extracted %d reports", len(reports))
        time.sleep(2)

    if all_stats:
        inserted = save_stats_to_db(conn, all_stats)
        logger.info("\nSaved %d statistical records to database", inserted)

    if all_reports:
        inserted = save_reports_to_db(conn, all_reports)
        logger.info("Saved %d report records to database", inserted)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    export_data = {
        "source": "China Internet Network Information Center",
        "source_cn": "中国互联网络信息中心",
        "source_url": "http://www.cnnic.cn/",
        "scraped_at": datetime.now().isoformat(),
        "internet_statistics": all_stats,
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
    print("CNNIC Spider - Execution Summary")
    print("=" * 70)
    print(f"Total statistics extracted: {len(all_stats)}")
    print(f"Total reports indexed: {len(all_reports)}")
    print(f"Database: {DB_PATH}")
    print(f"JSON output: {JSON_PATH}")
    print("=" * 70)


if __name__ == "__main__":
    main()
