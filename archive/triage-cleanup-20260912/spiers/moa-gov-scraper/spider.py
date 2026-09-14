#!/usr/bin/env python3
"""
Scrapling spider for Ministry of Agriculture and Rural Affairs (MOA).
Data source: https://www.moa.gov.cn/

Manifest:
  version: "1"
  name: moa-gov
  label: Ministry of Agriculture and Rural Affairs Data
  source_url: https://www.moa.gov.cn/
  functions:
    - command: get_agricultural_statistics
      category: statistics
      description: Agricultural production statistics (crop, livestock, fishery)
      frequency: quarterly
    - command: get_crop_yield
      category: production-data
      description: Crop yield data by province and crop type
      frequency: annual
    - command: get_livestock_data
      category: production-data
      description: Livestock and poultry production data
      frequency: quarterly
    - command: get_policy_documents
      category: policy-data
      description: Agricultural policy documents and regulations
      frequency: monthly
    - command: get_rural_economy
      category: economic-data
      description: Rural economic indicators
      frequency: quarterly
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
DB_PATH = BASE_DIR / "data" / "moa_gov.db"
JSON_PATH = BASE_DIR / "output" / "moa_gov.json"

STATISTICS_PATHS = [
    "/zwllm/tjxx/",
    "/zwgk/tjxx/",
    "/tongjixinxi/",
    "/data/tongji/",
    "/sj/tjgb/",
    "/zwgk/zcfb/",
    "/zwllm/nyb/",
]

POLICY_PATHS = [
    "/zwgk/zcfg/",
    "/zwgk/bmgz/",
    "/zwllm/bmgz/",
    "/zhengce/",
    "/zhengcewenjian/",
    "/gongbao/",
    "/zwgk/gbgg/",
    "/tzgg/",
]

CROP_PATHS = [
    "/zwllm/zzys/",
    "/zzys/",
    "/nongzuowu/",
    "/data/crop/",
    "/zwllm/nyb/zzys/",
]

LIVESTOCK_PATHS = [
    "/zwllm/xmys/",
    "/xmys/",
    "/xumuyu/",
    "/data/livestock/",
    "/zwllm/nyb/xmys/",
]

RURAL_PATHS = [
    "/zwllm/nycy/",
    "/nongcun/",
    "/data/rural/",
    "/zwllm/nyb/nycy/",
    "/cwjcs/",
]

DATE_RE = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}")
NUMBER_RE = re.compile(r"[-+]?[\d,]+(?:\.\d+)?")
DOC_NUMBER_RE = re.compile(r"(?:农|渔|牧|农垦)?[\w]+〔\d{4}〕\d+号")


class MoAGovSpider(Spider):
    name = "moa_gov"
    start_urls = ["https://www.moa.gov.cn/"]
    allowed_domains = {"moa.gov.cn"}
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
            CREATE TABLE IF NOT EXISTS agricultural_statistics (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                period          TEXT    NOT NULL,
                indicator       TEXT    NOT NULL,
                value           TEXT    NOT NULL,
                unit            TEXT    DEFAULT '',
                category        TEXT    DEFAULT '',
                region          TEXT    DEFAULT '',
                change_pct      REAL,
                source_url      TEXT    DEFAULT '',
                scraped_at      TEXT    NOT NULL,
                UNIQUE(period, indicator, category, region)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS crop_yield (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                year            TEXT    NOT NULL,
                province        TEXT    NOT NULL,
                crop_name       TEXT    NOT NULL,
                area            REAL,
                area_unit       TEXT    DEFAULT 'thousand_hectares',
                total_output    REAL,
                output_unit     TEXT    DEFAULT 'ten_thousand_tons',
                yield_per_area  REAL,
                source_url      TEXT    DEFAULT '',
                scraped_at      TEXT    NOT NULL,
                UNIQUE(year, province, crop_name)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS livestock_data (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                period          TEXT    NOT NULL,
                province        TEXT    DEFAULT '',
                animal_type     TEXT    NOT NULL,
                indicator       TEXT    NOT NULL,
                value           TEXT    NOT NULL,
                unit            TEXT    DEFAULT '',
                source_url      TEXT    DEFAULT '',
                scraped_at      TEXT    NOT NULL,
                UNIQUE(period, province, animal_type, indicator)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS policy_documents (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                title           TEXT    NOT NULL,
                publish_date    TEXT    NOT NULL,
                doc_number      TEXT    DEFAULT '',
                doc_type        TEXT    DEFAULT '',
                issuing_body    TEXT    DEFAULT '',
                category        TEXT    DEFAULT '',
                summary         TEXT    DEFAULT '',
                content         TEXT    DEFAULT '',
                source_url      TEXT    DEFAULT '',
                scraped_at      TEXT    NOT NULL,
                UNIQUE(title, publish_date)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS rural_economy (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                period          TEXT    NOT NULL,
                province        TEXT    DEFAULT '',
                indicator       TEXT    NOT NULL,
                value           TEXT    NOT NULL,
                unit            TEXT    DEFAULT '',
                source_url      TEXT    DEFAULT '',
                scraped_at      TEXT    NOT NULL,
                UNIQUE(period, province, indicator)
            )
        """)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_agri_stats_period ON agricultural_statistics(period)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_crop_year ON crop_yield(year)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_crop_province ON crop_yield(province)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_livestock_period ON livestock_data(period)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_policy_date ON policy_documents(publish_date)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_rural_period ON rural_economy(period)")
        self._conn.commit()

    async def parse(self, response: Response):
        page_type = self._detect_page_type(response.url)

        if page_type == "statistics":
            items = self._extract_statistics(response)
            for item in items:
                self._persist("agricultural_statistics", item)
                self._items.append({**item, "_table": "agricultural_statistics"})
            if items:
                self.logger.info("Found %d statistics items on %s", len(items), response.url)
                return

        elif page_type == "crop":
            items = self._extract_crop_data(response)
            for item in items:
                self._persist("crop_yield", item)
                self._items.append({**item, "_table": "crop_yield"})
            if items:
                self.logger.info("Found %d crop items on %s", len(items), response.url)
                return

        elif page_type == "livestock":
            items = self._extract_livestock(response)
            for item in items:
                self._persist("livestock_data", item)
                self._items.append({**item, "_table": "livestock_data"})
            if items:
                self.logger.info("Found %d livestock items on %s", len(items), response.url)
                return

        elif page_type == "policy":
            items = self._extract_policies(response)
            for item in items:
                self._persist("policy_documents", item)
                self._items.append({**item, "_table": "policy_documents"})
            if items:
                self.logger.info("Found %d policy items on %s", len(items), response.url)
                return

        elif page_type == "rural":
            items = self._extract_rural_economy(response)
            for item in items:
                self._persist("rural_economy", item)
                self._items.append({**item, "_table": "rural_economy"})
            if items:
                self.logger.info("Found %d rural economy items on %s", len(items), response.url)
                return

        links = response.css("a::attr(href)").getall()
        for href in links:
            href_lower = href.lower()
            if any(kw in href_lower for kw in ("tjxx", "统计", "tongji", "data/tongji")):
                yield response.follow(href, callback=self.parse)
            elif any(kw in href_lower for kw in ("zzys", "种植", "作物", "crop")):
                yield response.follow(href, callback=self.parse)
            elif any(kw in href_lower for kw in ("xmys", "畜牧", "养殖", "livestock")):
                yield response.follow(href, callback=self.parse)
            elif any(kw in href_lower for kw in ("zcfg", "政策", "法规", "gongbao", "tzgg")):
                yield response.follow(href, callback=self.parse)
            elif any(kw in href_lower for kw in ("nycy", "农村", "rural", "cwjcs")):
                yield response.follow(href, callback=self.parse)

    def _detect_page_type(self, url: str) -> str:
        url_lower = url.lower()
        if any(p in url_lower for p in ("tjxx", "统计", "tongji")):
            return "statistics"
        if any(p in url_lower for p in ("zzys", "种植", "作物", "crop")):
            return "crop"
        if any(p in url_lower for p in ("xmys", "畜牧", "养殖", "livestock")):
            return "livestock"
        if any(p in url_lower for p in ("zcfg", "政策", "法规", "gongbao", "tzgg", "notice")):
            return "policy"
        if any(p in url_lower for p in ("nycy", "农村", "rural", "cwjcs")):
            return "rural"
        return "home"

    def _extract_statistics(self, response: Response) -> list[dict]:
        items: list[dict] = []
        for table in response.css("table"):
            header_row = table.css("tr:first-child")
            headers = [h.get_all_text(strip=True).lower() for h in header_row.css("th")] if header_row else []

            period_col, indicator_col, value_col, unit_col, category_col, region_col, change_col = self._detect_stat_columns(headers)

            for row in table.css("tr")[1:] if headers else table.css("tr"):
                cells = row.css("td")
                if len(cells) < 2:
                    continue
                texts = [c.get_all_text(strip=True) for c in cells]
                item = self._parse_stat_row(texts, period_col, indicator_col, value_col,
                                            unit_col, category_col, region_col, change_col, response.url)
                if item:
                    items.append(item)
        return items

    @staticmethod
    def _detect_stat_columns(headers: list[str]) -> tuple:
        period_col = indicator_col = value_col = unit_col = category_col = region_col = change_col = None
        for i, h in enumerate(headers):
            if period_col is None and any(k in h for k in ("年份", "年度", "period", "year", "日期", "quarter", "季度")):
                period_col = i
            elif indicator_col is None and any(k in h for k in ("指标", "indicator", "项目", "item", "名称")):
                indicator_col = i
            elif value_col is None and any(k in h for k in ("数值", "value", "数量", "amount", "数据", "总量")):
                value_col = i
            elif unit_col is None and any(k in h for k in ("单位", "unit")):
                unit_col = i
            elif category_col is None and any(k in h for k in ("类别", "category", "分类", "行业")):
                category_col = i
            elif region_col is None and any(k in h for k in ("地区", "region", "省份", "province", "区域")):
                region_col = i
            elif change_col is None and any(k in h for k in ("同比", "环比", "增长", "change", "增幅", "增速")):
                change_col = i
        return period_col, indicator_col, value_col, unit_col, category_col, region_col, change_col

    @staticmethod
    def _parse_stat_row(texts, period_col, indicator_col, value_col,
                        unit_col, category_col, region_col, change_col, url) -> dict | None:
        try:
            period = texts[period_col].strip() if period_col is not None and period_col < len(texts) else ""
            indicator = texts[indicator_col].strip() if indicator_col is not None and indicator_col < len(texts) else ""
            value = texts[value_col].strip() if value_col is not None and value_col < len(texts) else ""
            unit = texts[unit_col].strip() if unit_col is not None and unit_col < len(texts) else ""
            category = texts[category_col].strip() if category_col is not None and category_col < len(texts) else ""
            region = texts[region_col].strip() if region_col is not None and region_col < len(texts) else ""
            change_pct = None
            if change_col is not None and change_col < len(texts):
                pct_match = NUMBER_RE.search(texts[change_col].replace("%", ""))
                if pct_match:
                    change_pct = float(pct_match.group())

            if not period or not indicator or not value:
                return None

            return {
                "period": period,
                "indicator": indicator,
                "value": value,
                "unit": unit,
                "category": category,
                "region": region,
                "change_pct": change_pct,
                "source_url": url,
            }
        except (ValueError, IndexError):
            return None

    def _extract_crop_data(self, response: Response) -> list[dict]:
        items: list[dict] = []
        for table in response.css("table"):
            header_row = table.css("tr:first-child")
            headers = [h.get_all_text(strip=True).lower() for h in header_row.css("th")] if header_row else []

            year_col, province_col, crop_col, area_col, output_col = self._detect_crop_columns(headers)

            for row in table.css("tr")[1:] if headers else table.css("tr"):
                cells = row.css("td")
                if len(cells) < 3:
                    continue
                texts = [c.get_all_text(strip=True) for c in cells]
                item = self._parse_crop_row(texts, year_col, province_col, crop_col, area_col, output_col, response.url)
                if item:
                    items.append(item)
        return items

    @staticmethod
    def _detect_crop_columns(headers: list[str]) -> tuple:
        year_col = province_col = crop_col = area_col = output_col = None
        for i, h in enumerate(headers):
            if year_col is None and any(k in h for k in ("年份", "year", "年度")):
                year_col = i
            elif province_col is None and any(k in h for k in ("地区", "省份", "province", "区域", "产地")):
                province_col = i
            elif crop_col is None and any(k in h for k in ("作物", "品种", "crop", "品名", "粮食", "谷物")):
                crop_col = i
            elif area_col is None and any(k in h for k in ("面积", "area", "播种面积", "种植面积")):
                area_col = i
            elif output_col is None and any(k in h for k in ("产量", "output", "产出", "总产", "总产量")):
                output_col = i
        return year_col, province_col, crop_col, area_col, output_col

    @staticmethod
    def _parse_crop_row(texts, year_col, province_col, crop_col, area_col, output_col, url) -> dict | None:
        try:
            year = texts[year_col].strip() if year_col is not None and year_col < len(texts) else ""
            province = texts[province_col].strip() if province_col is not None and province_col < len(texts) else ""
            crop_name = texts[crop_col].strip() if crop_col is not None and crop_col < len(texts) else ""
            area_str = texts[area_col].strip() if area_col is not None and area_col < len(texts) else ""
            output_str = texts[output_col].strip() if output_col is not None and output_col < len(texts) else ""

            if not year or not province or not crop_name:
                return None

            area = float(NUMBER_RE.search(area_str).group().replace(",", "")) if area_str and NUMBER_RE.search(area_str) else None
            total_output = float(NUMBER_RE.search(output_str).group().replace(",", "")) if output_str and NUMBER_RE.search(output_str) else None
            yield_per_area = round(total_output / area, 2) if total_output and area and area > 0 else None

            return {
                "year": year,
                "province": province,
                "crop_name": crop_name,
                "area": area,
                "area_unit": "thousand_hectares",
                "total_output": total_output,
                "output_unit": "ten_thousand_tons",
                "yield_per_area": yield_per_area,
                "source_url": url,
            }
        except (ValueError, IndexError, AttributeError):
            return None

    def _extract_livestock(self, response: Response) -> list[dict]:
        items: list[dict] = []
        for table in response.css("table"):
            header_row = table.css("tr:first-child")
            headers = [h.get_all_text(strip=True).lower() for h in header_row.css("th")] if header_row else []

            period_col, province_col, animal_col, indicator_col, value_col, unit_col = self._detect_livestock_columns(headers)

            for row in table.css("tr")[1:] if headers else table.css("tr"):
                cells = row.css("td")
                if len(cells) < 3:
                    continue
                texts = [c.get_all_text(strip=True) for c in cells]
                item = self._parse_livestock_row(texts, period_col, province_col, animal_col,
                                                 indicator_col, value_col, unit_col, response.url)
                if item:
                    items.append(item)
        return items

    @staticmethod
    def _detect_livestock_columns(headers: list[str]) -> tuple:
        period_col = province_col = animal_col = indicator_col = value_col = unit_col = None
        for i, h in enumerate(headers):
            if period_col is None and any(k in h for k in ("年份", "年度", "period", "year", "季度", "quarter", "月份")):
                period_col = i
            elif province_col is None and any(k in h for k in ("地区", "省份", "province", "区域")):
                province_col = i
            elif animal_col is None and any(k in h for k in ("畜种", "品种", "animal", "猪", "牛", "羊", "禽")):
                animal_col = i
            elif indicator_col is None and any(k in h for k in ("指标", "indicator", "项目", "item")):
                indicator_col = i
            elif value_col is None and any(k in h for k in ("数值", "value", "数量", "amount")):
                value_col = i
            elif unit_col is None and any(k in h for k in ("单位", "unit")):
                unit_col = i
        return period_col, province_col, animal_col, indicator_col, value_col, unit_col

    @staticmethod
    def _parse_livestock_row(texts, period_col, province_col, animal_col,
                             indicator_col, value_col, unit_col, url) -> dict | None:
        try:
            period = texts[period_col].strip() if period_col is not None and period_col < len(texts) else ""
            province = texts[province_col].strip() if province_col is not None and province_col < len(texts) else ""
            animal_type = texts[animal_col].strip() if animal_col is not None and animal_col < len(texts) else ""
            indicator = texts[indicator_col].strip() if indicator_col is not None and indicator_col < len(texts) else ""
            value = texts[value_col].strip() if value_col is not None and value_col < len(texts) else ""
            unit = texts[unit_col].strip() if unit_col is not None and unit_col < len(texts) else ""

            if not period or not animal_type or not value:
                return None

            return {
                "period": period,
                "province": province,
                "animal_type": animal_type,
                "indicator": indicator,
                "value": value,
                "unit": unit,
                "source_url": url,
            }
        except (ValueError, IndexError):
            return None

    def _extract_policies(self, response: Response) -> list[dict]:
        items: list[dict] = []

        for article in response.css("div.article-item, div.policy-item, div.list-item, li.news-item, li.article, ul.list li"):
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

            doc_number = article.css("span.doc-number::text, .doc-num::text").get("").strip()
            if not doc_number:
                full_text = article.get_all_text()
                doc_match = DOC_NUMBER_RE.search(full_text)
                doc_number = doc_match.group() if doc_match else ""

            issuing_body = article.css("span.source::text, .department::text").get("").strip() or "农业农村部"

            doc_type = "policy"
            if any(kw in title for kw in ("通知", "公告")):
                doc_type = "notice"
            elif any(kw in title for kw in ("条例", "办法", "规定")):
                doc_type = "regulation"
            elif any(kw in title for kw in ("意见", "方案")):
                doc_type = "opinion"
            elif any(kw in title for kw in ("规划", "计划")):
                doc_type = "plan"
            elif any(kw in title for kw in ("公报", "通报")):
                doc_type = "bulletin"

            category = "agriculture"
            if any(kw in title for kw in ("畜牧", "养殖", "畜")):
                category = "livestock"
            elif any(kw in title for kw in ("渔业", "水产")):
                category = "fishery"
            elif any(kw in title for kw in ("种植", "作物", "粮食")):
                category = "crop"
            elif any(kw in title for kw in ("农村", "乡村")):
                category = "rural"

            if not date:
                date_match = DATE_RE.search(article.get_all_text())
                date = date_match.group() if date_match else datetime.now().strftime("%Y-%m-%d")

            items.append({
                "title": title,
                "publish_date": date,
                "doc_number": doc_number,
                "doc_type": doc_type,
                "issuing_body": issuing_body,
                "category": category,
                "summary": summary,
                "content": "",
                "source_url": response.url,
            })
        return items

    def _extract_rural_economy(self, response: Response) -> list[dict]:
        items: list[dict] = []
        for table in response.css("table"):
            header_row = table.css("tr:first-child")
            headers = [h.get_all_text(strip=True).lower() for h in header_row.css("th")] if header_row else []

            period_col, province_col, indicator_col, value_col, unit_col = self._detect_rural_columns(headers)

            for row in table.css("tr")[1:] if headers else table.css("tr"):
                cells = row.css("td")
                if len(cells) < 2:
                    continue
                texts = [c.get_all_text(strip=True) for c in cells]
                item = self._parse_rural_row(texts, period_col, province_col, indicator_col, value_col, unit_col, response.url)
                if item:
                    items.append(item)
        return items

    @staticmethod
    def _detect_rural_columns(headers: list[str]) -> tuple:
        period_col = province_col = indicator_col = value_col = unit_col = None
        for i, h in enumerate(headers):
            if period_col is None and any(k in h for k in ("年份", "年度", "period", "year", "季度")):
                period_col = i
            elif province_col is None and any(k in h for k in ("地区", "省份", "province", "区域")):
                province_col = i
            elif indicator_col is None and any(k in h for k in ("指标", "indicator", "项目", "item", "名称")):
                indicator_col = i
            elif value_col is None and any(k in h for k in ("数值", "value", "数量", "amount", "数据")):
                value_col = i
            elif unit_col is None and any(k in h for k in ("单位", "unit")):
                unit_col = i
        return period_col, province_col, indicator_col, value_col, unit_col

    @staticmethod
    def _parse_rural_row(texts, period_col, province_col, indicator_col, value_col, unit_col, url) -> dict | None:
        try:
            period = texts[period_col].strip() if period_col is not None and period_col < len(texts) else ""
            province = texts[province_col].strip() if province_col is not None and province_col < len(texts) else ""
            indicator = texts[indicator_col].strip() if indicator_col is not None and indicator_col < len(texts) else ""
            value = texts[value_col].strip() if value_col is not None and value_col < len(texts) else ""
            unit = texts[unit_col].strip() if unit_col is not None and unit_col < len(texts) else ""

            if not period or not indicator or not value:
                return None

            return {
                "period": period,
                "province": province,
                "indicator": indicator,
                "value": value,
                "unit": unit,
                "source_url": url,
            }
        except (ValueError, IndexError):
            return None

    def _persist(self, table: str, item: dict):
        assert self._conn is not None
        try:
            now = datetime.now().isoformat()
            if table == "agricultural_statistics":
                self._conn.execute(
                    """INSERT OR REPLACE INTO agricultural_statistics
                        (period, indicator, value, unit, category, region, change_pct, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["period"], item["indicator"], item["value"], item.get("unit", ""),
                     item.get("category", ""), item.get("region", ""),
                     item.get("change_pct"), item.get("source_url", ""), now),
                )
            elif table == "crop_yield":
                self._conn.execute(
                    """INSERT OR REPLACE INTO crop_yield
                        (year, province, crop_name, area, area_unit, total_output, output_unit, yield_per_area, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["year"], item["province"], item["crop_name"],
                     item.get("area"), item.get("area_unit", "thousand_hectares"),
                     item.get("total_output"), item.get("output_unit", "ten_thousand_tons"),
                     item.get("yield_per_area"), item.get("source_url", ""), now),
                )
            elif table == "livestock_data":
                self._conn.execute(
                    """INSERT OR REPLACE INTO livestock_data
                        (period, province, animal_type, indicator, value, unit, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["period"], item.get("province", ""), item["animal_type"],
                     item["indicator"], item["value"], item.get("unit", ""),
                     item.get("source_url", ""), now),
                )
            elif table == "policy_documents":
                self._conn.execute(
                    """INSERT OR REPLACE INTO policy_documents
                        (title, publish_date, doc_number, doc_type, issuing_body, category, summary, content, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["title"], item["publish_date"], item.get("doc_number", ""),
                     item.get("doc_type", "policy"), item.get("issuing_body", "农业农村部"),
                     item.get("category", "agriculture"), item.get("summary", ""),
                     item.get("content", ""), item.get("source_url", ""), now),
                )
            elif table == "rural_economy":
                self._conn.execute(
                    """INSERT OR REPLACE INTO rural_economy
                        (period, province, indicator, value, unit, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (item["period"], item.get("province", ""), item["indicator"],
                     item["value"], item.get("unit", ""), item.get("source_url", ""), now),
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
    result = MoAGovSpider().start()
    print(f"\n{'=' * 50}")
    print(f"Items   : {result.stats.items_scraped}")
    print(f"Requests: {result.stats.requests_count}")
    print(f"Time    : {result.stats.elapsed_seconds:.2f}s")
    print(f"{'=' * 50}")
