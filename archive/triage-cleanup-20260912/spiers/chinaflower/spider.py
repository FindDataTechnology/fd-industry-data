#!/usr/bin/env python3
"""
Scrapling spider for China Flower Association (CFA) industry data.
Data source: https://www.chinaflower.org.cn

Manifest:
  version: "1"
  name: chinaflower
  label: China Flower Association Industry Data
  source_url: https://www.chinaflower.org.cn
  functions:
    - command: get_industry_statistics
      category: industry-data
      description: Flower industry statistics and annual reports
      frequency: monthly
    - command: get_production_data
      category: production-data
      description: Flower production data by region and variety
      frequency: quarterly
    - command: get_market_reports
      category: market-analysis
      description: Flower market reports and analysis
      frequency: weekly
    - command: get_policy_info
      category: policy-data
      description: Flower industry policy information
      frequency: monthly
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from scrapling.spiders import Spider, Response

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "chinaflower.db"
JSON_PATH = BASE_DIR / "output" / "chinaflower.json"

STATISTICS_PATHS = [
    "/statistics",
    "/data",
    "/data/statistics",
    "/info/statistics",
    "/tongji",
    "/sj",
    "/tjsj",
    "/data/report",
]

PRODUCTION_PATHS = [
    "/production",
    "/shengchan",
    "/data/production",
    "/info/production",
    "/nongye",
    "/zhongzhi",
    "/cultivation",
]

REPORT_PATHS = [
    "/report",
    "/baogao",
    "/market",
    "/market/report",
    "/analysis",
    "/yanjiu",
    "/info/report",
]

POLICY_PATHS = [
    "/policy",
    "/zhengce",
    "/zhengcewenjian",
    "/info/policy",
    "/notice",
    "/gonggao",
    "/tongzhi",
]

DATE_RE = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}")
NUMBER_RE = re.compile(r"[\d,]+(?:\.\d+)?")


class ChinaFlowerSpider(Spider):
    name = "chinaflower"
    start_urls = ["https://www.chinaflower.org.cn"]
    allowed_domains = {"chinaflower.org.cn"}
    concurrent_requests = 2
    download_delay = 2.0
    logging_level = logging.INFO
    robots_txt_obey = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._items: list[dict] = []
        self._conn: sqlite3.Connection | None = None

    async def on_start(self, resuming: bool = False):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(DB_PATH))
        self._init_db()
        self.logger.info("Database initialized: %s", DB_PATH)

    async def on_close(self):
        if self._conn:
            self._conn.commit()
            self._conn.close()
            self.logger.info("Database connection closed")
        self._save_json()

    def _init_db(self):
        assert self._conn is not None
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS industry_statistics (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                period          TEXT    NOT NULL,
                indicator       TEXT    NOT NULL,
                value           TEXT    NOT NULL,
                unit            TEXT    DEFAULT '',
                region          TEXT    DEFAULT '',
                flower_type     TEXT    DEFAULT '',
                change_pct      REAL,
                source_url      TEXT    DEFAULT '',
                scraped_at      TEXT    NOT NULL,
                UNIQUE(period, indicator, region, flower_type)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS production_data (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                year            TEXT    NOT NULL,
                region          TEXT    NOT NULL,
                flower_name     TEXT    NOT NULL,
                area            REAL,
                area_unit       TEXT    DEFAULT 'hectares',
                output          REAL,
                output_unit     TEXT    DEFAULT 'tons',
                yield_per_area  REAL,
                source_url      TEXT    DEFAULT '',
                scraped_at      TEXT    NOT NULL,
                UNIQUE(year, region, flower_name)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS market_reports (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                title           TEXT    NOT NULL,
                publish_date    TEXT    NOT NULL,
                report_type     TEXT    DEFAULT 'market_report',
                summary         TEXT    DEFAULT '',
                content         TEXT    DEFAULT '',
                author          TEXT    DEFAULT '',
                source_url      TEXT    DEFAULT '',
                scraped_at      TEXT    NOT NULL,
                UNIQUE(title, publish_date)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS policy_info (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                title           TEXT    NOT NULL,
                publish_date    TEXT    NOT NULL,
                policy_type     TEXT    DEFAULT '',
                issuing_body    TEXT    DEFAULT '',
                doc_number      TEXT    DEFAULT '',
                summary         TEXT    DEFAULT '',
                content         TEXT    DEFAULT '',
                source_url      TEXT    DEFAULT '',
                scraped_at      TEXT    NOT NULL,
                UNIQUE(title, publish_date)
            )
        """)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_stats_period ON industry_statistics(period)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_production_year ON production_data(year)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_production_region ON production_data(region)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_date ON market_reports(publish_date)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_policy_date ON policy_info(publish_date)")
        self._conn.commit()

    async def parse(self, response: Response):
        page_type = self._detect_page_type(response.url)

        if page_type == "statistics":
            items = self._extract_statistics(response)
            for item in items:
                self._persist("industry_statistics", item)
                self._items.append({**item, "_table": "industry_statistics"})
            if items:
                self.logger.info("Found %d statistics items on %s", len(items), response.url)
                return

        elif page_type == "production":
            items = self._extract_production(response)
            for item in items:
                self._persist("production_data", item)
                self._items.append({**item, "_table": "production_data"})
            if items:
                self.logger.info("Found %d production items on %s", len(items), response.url)
                return

        elif page_type == "report":
            items = self._extract_reports(response)
            for item in items:
                self._persist("market_reports", item)
                self._items.append({**item, "_table": "market_reports"})
            if items:
                self.logger.info("Found %d report items on %s", len(items), response.url)
                return

        elif page_type == "policy":
            items = self._extract_policies(response)
            for item in items:
                self._persist("policy_info", item)
                self._items.append({**item, "_table": "policy_info"})
            if items:
                self.logger.info("Found %d policy items on %s", len(items), response.url)
                return

        links = response.css("a::attr(href)").getall()
        for href in links:
            href_lower = href.lower()
            if any(kw in href_lower for kw in ("statistic", "统计", "数据", "tongji", "sj")):
                yield response.follow(href, callback=self.parse)
            elif any(kw in href_lower for kw in ("production", "生产", "种植", "面积", "产量")):
                yield response.follow(href, callback=self.parse)
            elif any(kw in href_lower for kw in ("report", "报告", "分析", "市场", "行情")):
                yield response.follow(href, callback=self.parse)
            elif any(kw in href_lower for kw in ("policy", "政策", "通知", "公告", "文件")):
                yield response.follow(href, callback=self.parse)

    def _detect_page_type(self, url: str) -> str:
        url_lower = url.lower()
        if any(p in url_lower for p in ("statistic", "统计", "数据", "tongji")):
            return "statistics"
        if any(p in url_lower for p in ("production", "生产", "种植", "cultivation")):
            return "production"
        if any(p in url_lower for p in ("report", "报告", "分析", "market", "行情")):
            return "report"
        if any(p in url_lower for p in ("policy", "政策", "通知", "公告", "notice")):
            return "policy"
        return "home"

    def _extract_statistics(self, response: Response) -> list[dict]:
        items: list[dict] = []
        for table in response.css("table"):
            header_row = table.css("tr:first-child")
            headers = [h.get_all_text(strip=True).lower() for h in header_row.css("th")] if header_row else []

            period_col, indicator_col, value_col, unit_col, region_col, type_col, change_col = self._detect_stat_columns(headers)

            for row in table.css("tr")[1:] if headers else table.css("tr"):
                cells = row.css("td")
                if len(cells) < 2:
                    continue
                texts = [c.get_all_text(strip=True) for c in cells]
                item = self._parse_stat_row(texts, period_col, indicator_col, value_col,
                                            unit_col, region_col, type_col, change_col, response.url)
                if item:
                    items.append(item)
        return items

    @staticmethod
    def _detect_stat_columns(headers: list[str]) -> tuple:
        period_col = indicator_col = value_col = unit_col = region_col = type_col = change_col = None
        for i, h in enumerate(headers):
            if period_col is None and any(k in h for k in ("年份", "年度", "period", "year", "日期", "date", "月份", "month")):
                period_col = i
            elif indicator_col is None and any(k in h for k in ("指标", "indicator", "项目", "item", "名称", "name")):
                indicator_col = i
            elif value_col is None and any(k in h for k in ("数值", "value", "数量", "amount", "数据")):
                value_col = i
            elif unit_col is None and any(k in h for k in ("单位", "unit")):
                unit_col = i
            elif region_col is None and any(k in h for k in ("地区", "region", "省份", "province", "区域")):
                region_col = i
            elif type_col is None and any(k in h for k in ("花卉", "类型", "type", "category", "品种")):
                type_col = i
            elif change_col is None and any(k in h for k in ("同比", "环比", "增长", "change", "增幅")):
                change_col = i
        return period_col, indicator_col, value_col, unit_col, region_col, type_col, change_col

    @staticmethod
    def _parse_stat_row(texts, period_col, indicator_col, value_col,
                        unit_col, region_col, type_col, change_col, url) -> dict | None:
        try:
            period = texts[period_col].strip() if period_col is not None and period_col < len(texts) else ""
            indicator = texts[indicator_col].strip() if indicator_col is not None and indicator_col < len(texts) else ""
            value = texts[value_col].strip() if value_col is not None and value_col < len(texts) else ""
            unit = texts[unit_col].strip() if unit_col is not None and unit_col < len(texts) else ""
            region = texts[region_col].strip() if region_col is not None and region_col < len(texts) else ""
            flower_type = texts[type_col].strip() if type_col is not None and type_col < len(texts) else ""
            change_pct = None
            if change_col is not None and change_col < len(texts):
                pct_match = re.search(r"[-+]?\d+\.?\d*", texts[change_col].replace("%", ""))
                if pct_match:
                    change_pct = float(pct_match.group())

            if not period or not indicator or not value:
                return None

            return {
                "period": period,
                "indicator": indicator,
                "value": value,
                "unit": unit,
                "region": region,
                "flower_type": flower_type,
                "change_pct": change_pct,
                "source_url": url,
            }
        except (ValueError, IndexError):
            return None

    def _extract_production(self, response: Response) -> list[dict]:
        items: list[dict] = []
        for table in response.css("table"):
            header_row = table.css("tr:first-child")
            headers = [h.get_all_text(strip=True).lower() for h in header_row.css("th")] if header_row else []

            year_col, region_col, name_col, area_col, output_col = self._detect_production_columns(headers)

            for row in table.css("tr")[1:] if headers else table.css("tr"):
                cells = row.css("td")
                if len(cells) < 3:
                    continue
                texts = [c.get_all_text(strip=True) for c in cells]
                item = self._parse_production_row(texts, year_col, region_col, name_col, area_col, output_col, response.url)
                if item:
                    items.append(item)
        return items

    @staticmethod
    def _detect_production_columns(headers: list[str]) -> tuple:
        year_col = region_col = name_col = area_col = output_col = None
        for i, h in enumerate(headers):
            if year_col is None and any(k in h for k in ("年份", "year", "年度")):
                year_col = i
            elif region_col is None and any(k in h for k in ("地区", "region", "省份", "province", "区域", "产地")):
                region_col = i
            elif name_col is None and any(k in h for k in ("品种", "名称", "flower", "name", "品名", "花卉")):
                name_col = i
            elif area_col is None and any(k in h for k in ("面积", "area", "种植面积")):
                area_col = i
            elif output_col is None and any(k in h for k in ("产量", "output", "产出", "product")):
                output_col = i
        return year_col, region_col, name_col, area_col, output_col

    @staticmethod
    def _parse_production_row(texts, year_col, region_col, name_col, area_col, output_col, url) -> dict | None:
        try:
            year = texts[year_col].strip() if year_col is not None and year_col < len(texts) else ""
            region = texts[region_col].strip() if region_col is not None and region_col < len(texts) else ""
            flower_name = texts[name_col].strip() if name_col is not None and name_col < len(texts) else ""
            area_str = texts[area_col].strip() if area_col is not None and area_col < len(texts) else ""
            output_str = texts[output_col].strip() if output_col is not None and output_col < len(texts) else ""

            if not year or not region or not flower_name:
                return None

            area = float(NUMBER_RE.search(area_str).group().replace(",", "")) if area_str and NUMBER_RE.search(area_str) else None
            output = float(NUMBER_RE.search(output_str).group().replace(",", "")) if output_str and NUMBER_RE.search(output_str) else None
            yield_per_area = round(output / area, 2) if output and area and area > 0 else None

            return {
                "year": year,
                "region": region,
                "flower_name": flower_name,
                "area": area,
                "area_unit": "hectares",
                "output": output,
                "output_unit": "tons",
                "yield_per_area": yield_per_area,
                "source_url": url,
            }
        except (ValueError, IndexError, AttributeError):
            return None

    def _extract_reports(self, response: Response) -> list[dict]:
        items: list[dict] = []

        for article in response.css("div.article-item, div.news-item, div.report-item, li.news-item, li.article"):
            title = article.css("h1::text, h2::text, h3::text, a.title::text, .title::text").get("").strip()
            date = article.css("span.date::text, time::text, .date::text, .time::text").get("").strip()
            if not title:
                link = article.css("a")
                if link:
                    title = link.css("::text").get("").strip()
                    date = article.css("span::text").get("").strip()
            if not title:
                continue

            summary_parts = article.css("p::text, div.summary::text, div.abstract::text").getall()
            summary = " ".join(s.strip() for s in summary_parts if s.strip())[:500]

            if not date:
                date_match = DATE_RE.search(article.get_all_text())
                date = date_match.group() if date_match else datetime.now().strftime("%Y-%m-%d")

            items.append({
                "title": title,
                "publish_date": date,
                "report_type": "market_report",
                "summary": summary,
                "content": "",
                "author": article.css(".author::text, .source::text").get("").strip(),
                "source_url": response.url,
            })

        detail_links = response.css("a::attr(href)").getall()
        for href in detail_links:
            if any(kw in href for kw in ("report", "报告", "分析", "market", "行情")):
                if href not in [i.get("source_url", "") for i in items]:
                    pass

        return items

    def _extract_policies(self, response: Response) -> list[dict]:
        items: list[dict] = []

        for article in response.css("div.article-item, div.policy-item, div.notice-item, li.news-item, li.article"):
            title = article.css("h1::text, h2::text, h3::text, a.title::text, .title::text").get("").strip()
            date = article.css("span.date::text, time::text, .date::text, .time::text").get("").strip()
            if not title:
                link = article.css("a")
                if link:
                    title = link.css("::text").get("").strip()
                    date = article.css("span::text").get("").strip()
            if not title:
                continue

            summary_parts = article.css("p::text, div.summary::text").getall()
            summary = " ".join(s.strip() for s in summary_parts if s.strip())[:500]

            doc_number = article.css("span.doc-number::text, .doc-num::text").get("").strip()
            issuing_body = article.css("span.source::text, .department::text").get("").strip() or "中国花卉协会"

            policy_type = "policy"
            if any(kw in title for kw in ("通知", "公告")):
                policy_type = "notice"
            elif any(kw in title for kw in ("意见", "办法")):
                policy_type = "regulation"
            elif any(kw in title for kw in ("规划", "计划")):
                policy_type = "plan"

            if not date:
                date_match = DATE_RE.search(article.get_all_text())
                date = date_match.group() if date_match else datetime.now().strftime("%Y-%m-%d")

            items.append({
                "title": title,
                "publish_date": date,
                "policy_type": policy_type,
                "issuing_body": issuing_body,
                "doc_number": doc_number,
                "summary": summary,
                "content": "",
                "source_url": response.url,
            })
        return items

    def _persist(self, table: str, item: dict):
        assert self._conn is not None
        try:
            now = datetime.now().isoformat()
            if table == "industry_statistics":
                self._conn.execute(
                    """INSERT OR REPLACE INTO industry_statistics
                        (period, indicator, value, unit, region, flower_type, change_pct, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["period"], item["indicator"], item["value"], item.get("unit", ""),
                     item.get("region", ""), item.get("flower_type", ""),
                     item.get("change_pct"), item.get("source_url", ""), now),
                )
            elif table == "production_data":
                self._conn.execute(
                    """INSERT OR REPLACE INTO production_data
                        (year, region, flower_name, area, area_unit, output, output_unit, yield_per_area, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["year"], item["region"], item["flower_name"],
                     item.get("area"), item.get("area_unit", "hectares"),
                     item.get("output"), item.get("output_unit", "tons"),
                     item.get("yield_per_area"), item.get("source_url", ""), now),
                )
            elif table == "market_reports":
                self._conn.execute(
                    """INSERT OR REPLACE INTO market_reports
                        (title, publish_date, report_type, summary, content, author, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["title"], item["publish_date"], item.get("report_type", "market_report"),
                     item.get("summary", ""), item.get("content", ""), item.get("author", ""),
                     item.get("source_url", ""), now),
                )
            elif table == "policy_info":
                self._conn.execute(
                    """INSERT OR REPLACE INTO policy_info
                        (title, publish_date, policy_type, issuing_body, doc_number, summary, content, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["title"], item["publish_date"], item.get("policy_type", "policy"),
                     item.get("issuing_body", ""), item.get("doc_number", ""),
                     item.get("summary", ""), item.get("content", ""),
                     item.get("source_url", ""), now),
                )
        except Exception as exc:
            self.logger.error("DB insert error for %s: %s", item, exc)

    def _save_json(self):
        if not self._items:
            self.logger.warning("No items to save to JSON")
            return
        output = {}
        for item in self._items:
            table = item.pop("_table", "unknown")
            output.setdefault(table, []).append(item)
        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        self.logger.info("Saved %d items to %s", len(self._items), JSON_PATH)


if __name__ == "__main__":
    result = ChinaFlowerSpider().start()
    print(f"\n{'=' * 50}")
    print(f"Items   : {result.stats.items_scraped}")
    print(f"Requests: {result.stats.requests_count}")
    print(f"Time    : {result.stats.elapsed_seconds:.2f}s")
    print(f"{'=' * 50}")
