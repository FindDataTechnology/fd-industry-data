#!/usr/bin/env python3
"""
Scrapling spider for China Agricultural Information Network.
Data source: http://www.agri.cn/

Manifest:
  version: "1"
  name: agri-cn
  label: China Agricultural Information Network Data
  source_url: http://www.agri.cn/
  functions:
    - command: get_product_prices
      category: commodity-pricing
      description: Agricultural product prices from wholesale markets
      frequency: daily
    - command: get_supply_demand
      category: market-data
      description: Market supply and demand data
      frequency: weekly
    - command: get_weather_impact
      category: weather-data
      description: Weather and disaster impact on agriculture
      frequency: daily
    - command: get_agricultural_news
      category: news
      description: Agricultural news and market updates
      frequency: daily
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
DB_PATH = BASE_DIR / "data" / "agri_cn.db"
JSON_PATH = BASE_DIR / "output" / "agri_cn.json"

PRICE_PATHS = [
    "/price",
    "/prices",
    "/data/price",
    "/market/price",
    "/hangqing/",
    "/jiage/",
    "/scfx/price",
    "/nyb/price",
    "/nyb/hangqing/",
]

SUPPLY_DEMAND_PATHS = [
    "/supply",
    "/demand",
    "/data/supply",
    "/gongxu/",
    "/scfx/supply",
    "/market/supply",
    "/nyb/gongxu/",
]

WEATHER_PATHS = [
    "/weather",
    "/qixiang/",
    "/zaihai/",
    "/disaster",
    "/weather-impact",
    "/nyb/qixiang/",
    "/nyb/zaihai/",
]

NEWS_PATHS = [
    "/news",
    "/xinwen/",
    "/nyb/xinwen/",
    "/info/news",
    "/nyb/dongtai/",
    "/nyb/kuaixun/",
]

DATE_RE = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}")
PRICE_RE = re.compile(r"[\d]+(?:[.,]\d+)?")
NUMBER_RE = re.compile(r"[-+]?[\d,]+(?:\.\d+)?")
CURRENCY_SYMBOLS = re.compile(r"[¥￥$€£]")


class AgriCnSpider(Spider):
    name = "agri_cn"
    start_urls = ["http://www.agri.cn/"]
    allowed_domains = {"agri.cn"}
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
            CREATE TABLE IF NOT EXISTS product_prices (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT    NOT NULL,
                product_name    TEXT    NOT NULL,
                category        TEXT    DEFAULT '',
                market_name     TEXT    DEFAULT '',
                province        TEXT    DEFAULT '',
                price           REAL    NOT NULL,
                price_unit      TEXT    DEFAULT 'CNY/kg',
                price_high      REAL,
                price_low       REAL,
                change_pct      REAL,
                source_url      TEXT    DEFAULT '',
                scraped_at      TEXT    NOT NULL,
                UNIQUE(date, product_name, market_name, province)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS supply_demand (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT    NOT NULL,
                product_name    TEXT    NOT NULL,
                indicator_type  TEXT    NOT NULL,
                value           TEXT    NOT NULL,
                unit            TEXT    DEFAULT '',
                region          TEXT    DEFAULT '',
                trend           TEXT    DEFAULT '',
                summary         TEXT    DEFAULT '',
                source_url      TEXT    DEFAULT '',
                scraped_at      TEXT    NOT NULL,
                UNIQUE(date, product_name, indicator_type, region)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS weather_impact (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT    NOT NULL,
                event_type      TEXT    NOT NULL,
                region          TEXT    NOT NULL,
                severity        TEXT    DEFAULT '',
                affected_area   REAL,
                affected_unit   TEXT    DEFAULT 'hectares',
                affected_crops  TEXT    DEFAULT '',
                estimated_loss  REAL,
                loss_unit       TEXT    DEFAULT 'CNY',
                summary         TEXT    DEFAULT '',
                source_url      TEXT    DEFAULT '',
                scraped_at      TEXT    NOT NULL,
                UNIQUE(date, event_type, region)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS agricultural_news (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                title           TEXT    NOT NULL,
                publish_date    TEXT    NOT NULL,
                category        TEXT    DEFAULT '',
                source          TEXT    DEFAULT '',
                summary         TEXT    DEFAULT '',
                content         TEXT    DEFAULT '',
                source_url      TEXT    DEFAULT '',
                scraped_at      TEXT    NOT NULL,
                UNIQUE(title, publish_date)
            )
        """)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_prices_date ON product_prices(date)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_prices_product ON product_prices(product_name)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_prices_market ON product_prices(market_name)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_supply_date ON supply_demand(date)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_weather_date ON weather_impact(date)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_news_date ON agricultural_news(publish_date)")
        self._conn.commit()

    async def parse(self, response: Response):
        page_type = self._detect_page_type(response.url)

        if page_type == "price":
            items = self._extract_prices(response)
            for item in items:
                self._persist("product_prices", item)
                self._items.append({**item, "_table": "product_prices"})
            if items:
                self.logger.info("Found %d price items on %s", len(items), response.url)
                return

        elif page_type == "supply_demand":
            items = self._extract_supply_demand(response)
            for item in items:
                self._persist("supply_demand", item)
                self._items.append({**item, "_table": "supply_demand"})
            if items:
                self.logger.info("Found %d supply/demand items on %s", len(items), response.url)
                return

        elif page_type == "weather":
            items = self._extract_weather_impact(response)
            for item in items:
                self._persist("weather_impact", item)
                self._items.append({**item, "_table": "weather_impact"})
            if items:
                self.logger.info("Found %d weather items on %s", len(items), response.url)
                return

        elif page_type == "news":
            items = self._extract_news(response)
            for item in items:
                self._persist("agricultural_news", item)
                self._items.append({**item, "_table": "agricultural_news"})
            if items:
                self.logger.info("Found %d news items on %s", len(items), response.url)
                return

        links = response.css("a::attr(href)").getall()
        for href in links:
            href_lower = href.lower()
            if any(kw in href_lower for kw in ("price", "价格", "行情", "jiage", "hangqing")):
                yield response.follow(href, callback=self.parse)
            elif any(kw in href_lower for kw in ("supply", "demand", "供需", "供求", "gongxu")):
                yield response.follow(href, callback=self.parse)
            elif any(kw in href_lower for kw in ("weather", "气象", "灾害", "zaihai", "qixiang")):
                yield response.follow(href, callback=self.parse)
            elif any(kw in href_lower for kw in ("news", "新闻", "xinwen", "dongtai", "kuaixun")):
                yield response.follow(href, callback=self.parse)

    def _detect_page_type(self, url: str) -> str:
        url_lower = url.lower()
        if any(p in url_lower for p in ("price", "价格", "行情", "jiage", "hangqing")):
            return "price"
        if any(p in url_lower for p in ("supply", "demand", "供需", "供求", "gongxu")):
            return "supply_demand"
        if any(p in url_lower for p in ("weather", "气象", "灾害", "zaihai", "qixiang")):
            return "weather"
        if any(p in url_lower for p in ("news", "新闻", "xinwen", "dongtai")):
            return "news"
        return "home"

    def _extract_prices(self, response: Response) -> list[dict]:
        items: list[dict] = []
        for table in response.css("table"):
            header_row = table.css("tr:first-child")
            headers = [h.get_all_text(strip=True).lower() for h in header_row.css("th")] if header_row else []

            cols = self._detect_price_columns(headers)

            for row in table.css("tr")[1:] if headers else table.css("tr"):
                cells = row.css("td")
                if len(cells) < 3:
                    continue
                texts = [c.get_all_text(strip=True) for c in cells]
                item = self._parse_price_row(texts, cols, response.url)
                if item:
                    items.append(item)
        return items

    @staticmethod
    def _detect_price_columns(headers: list[str]) -> dict:
        cols = {}
        keywords_map = {
            "date": ("日期", "date", "时间", "time"),
            "product": ("品种", "名称", "product", "name", "品名", "产品", "农产品"),
            "category": ("类别", "category", "分类", "类型"),
            "market": ("市场", "market", "批发市场", "集贸市场"),
            "province": ("省份", "province", "地区", "region", "区域"),
            "price": ("价格", "price", "均价", "单价", "批发价"),
            "price_high": ("最高", "high", "最高价"),
            "price_low": ("最低", "low", "最低价"),
            "change_pct": ("涨跌", "change", "涨跌幅", "环比"),
        }
        for col_name, keywords in keywords_map.items():
            for i, h in enumerate(headers):
                if any(k in h for k in keywords):
                    cols[col_name] = i
                    break
        return cols

    @staticmethod
    def _parse_price_row(texts, cols, url) -> dict | None:
        try:
            def get_col(name, default=""):
                idx = cols.get(name)
                if idx is not None and idx < len(texts):
                    return texts[idx].strip()
                return default

            date_str = get_col("date")
            product_name = get_col("product")
            price_str = get_col("price")

            if not date_str or not product_name or not price_str:
                for t in texts:
                    if not date_str and DATE_RE.search(t):
                        date_str = DATE_RE.search(t).group()
                    elif not product_name and re.search(r"[\u4e00-\u9fff]", t) and len(t) >= 2:
                        product_name = t
                    elif not price_str and PRICE_RE.search(CURRENCY_SYMBOLS.sub("", t)):
                        cleaned = CURRENCY_SYMBOLS.sub("", t).replace(",", "").strip()
                        if PRICE_RE.fullmatch(cleaned):
                            price_str = cleaned

            if not all([date_str, product_name, price_str]):
                return None

            price = float(PRICE_RE.search(price_str).group().replace(",", ""))
            high_str = get_col("price_high")
            low_str = get_col("price_low")
            change_str = get_col("change_pct")

            price_high = float(PRICE_RE.search(high_str).group().replace(",", "")) if high_str and PRICE_RE.search(high_str) else None
            price_low = float(PRICE_RE.search(low_str).group().replace(",", "")) if low_str and PRICE_RE.search(low_str) else None
            change_pct = None
            if change_str:
                pct_match = NUMBER_RE.search(change_str.replace("%", ""))
                if pct_match:
                    change_pct = float(pct_match.group())

            return {
                "date": date_str,
                "product_name": product_name,
                "category": get_col("category"),
                "market_name": get_col("market"),
                "province": get_col("province"),
                "price": price,
                "price_unit": "CNY/kg",
                "price_high": price_high,
                "price_low": price_low,
                "change_pct": change_pct,
                "source_url": url,
            }
        except (ValueError, IndexError, AttributeError):
            return None

    def _extract_supply_demand(self, response: Response) -> list[dict]:
        items: list[dict] = []
        for table in response.css("table"):
            header_row = table.css("tr:first-child")
            headers = [h.get_all_text(strip=True).lower() for h in header_row.css("th")] if header_row else []

            date_col, product_col, indicator_col, value_col, unit_col, region_col, trend_col = self._detect_supply_columns(headers)

            for row in table.css("tr")[1:] if headers else table.css("tr"):
                cells = row.css("td")
                if len(cells) < 3:
                    continue
                texts = [c.get_all_text(strip=True) for c in cells]
                item = self._parse_supply_row(texts, date_col, product_col, indicator_col,
                                              value_col, unit_col, region_col, trend_col, response.url)
                if item:
                    items.append(item)

        for article in response.css("div.article, div.report, div.analysis, div.summary"):
            title = article.css("h1::text, h2::text, h3::text").get("").strip()
            summary_parts = article.css("p::text").getall()
            summary = " ".join(s.strip() for s in summary_parts if s.strip())[:500]
            date = article.css("span.date::text, time::text").get("").strip()
            if title and summary:
                items.append({
                    "date": date or datetime.now().strftime("%Y-%m-%d"),
                    "product_name": "",
                    "indicator_type": "analysis",
                    "value": title,
                    "unit": "",
                    "region": "",
                    "trend": "",
                    "summary": summary,
                    "source_url": response.url,
                })
        return items

    @staticmethod
    def _detect_supply_columns(headers: list[str]) -> tuple:
        date_col = product_col = indicator_col = value_col = unit_col = region_col = trend_col = None
        for i, h in enumerate(headers):
            if date_col is None and any(k in h for k in ("日期", "date", "时间", "月份", "month")):
                date_col = i
            elif product_col is None and any(k in h for k in ("品种", "名称", "product", "name", "品名", "产品")):
                product_col = i
            elif indicator_col is None and any(k in h for k in ("指标", "indicator", "项目", "类型", "供需")):
                indicator_col = i
            elif value_col is None and any(k in h for k in ("数值", "value", "数量", "amount", "数据")):
                value_col = i
            elif unit_col is None and any(k in h for k in ("单位", "unit")):
                unit_col = i
            elif region_col is None and any(k in h for k in ("地区", "region", "省份", "区域")):
                region_col = i
            elif trend_col is None and any(k in h for k in ("趋势", "trend", "走势", "变化")):
                trend_col = i
        return date_col, product_col, indicator_col, value_col, unit_col, region_col, trend_col

    @staticmethod
    def _parse_supply_row(texts, date_col, product_col, indicator_col,
                          value_col, unit_col, region_col, trend_col, url) -> dict | None:
        try:
            date_str = texts[date_col].strip() if date_col is not None and date_col < len(texts) else ""
            product = texts[product_col].strip() if product_col is not None and product_col < len(texts) else ""
            indicator = texts[indicator_col].strip() if indicator_col is not None and indicator_col < len(texts) else ""
            value = texts[value_col].strip() if value_col is not None and value_col < len(texts) else ""
            unit = texts[unit_col].strip() if unit_col is not None and unit_col < len(texts) else ""
            region = texts[region_col].strip() if region_col is not None and region_col < len(texts) else ""
            trend = texts[trend_col].strip() if trend_col is not None and trend_col < len(texts) else ""

            if not date_str or not product or not value:
                return None

            return {
                "date": date_str,
                "product_name": product,
                "indicator_type": indicator,
                "value": value,
                "unit": unit,
                "region": region,
                "trend": trend,
                "summary": "",
                "source_url": url,
            }
        except (ValueError, IndexError):
            return None

    def _extract_weather_impact(self, response: Response) -> list[dict]:
        items: list[dict] = []
        for table in response.css("table"):
            header_row = table.css("tr:first-child")
            headers = [h.get_all_text(strip=True).lower() for h in header_row.css("th")] if header_row else []

            date_col, event_col, region_col, severity_col, area_col, crop_col, loss_col = self._detect_weather_columns(headers)

            for row in table.css("tr")[1:] if headers else table.css("tr"):
                cells = row.css("td")
                if len(cells) < 3:
                    continue
                texts = [c.get_all_text(strip=True) for c in cells]
                item = self._parse_weather_row(texts, date_col, event_col, region_col,
                                               severity_col, area_col, crop_col, loss_col, response.url)
                if item:
                    items.append(item)

        for article in response.css("div.article, div.report, div.notice, div.warning"):
            title = article.css("h1::text, h2::text, h3::text").get("").strip()
            summary_parts = article.css("p::text").getall()
            summary = " ".join(s.strip() for s in summary_parts if s.strip())[:500]
            date = article.css("span.date::text, time::text").get("").strip()

            event_type = "weather_event"
            if any(kw in title for kw in ("旱", "干旱")):
                event_type = "drought"
            elif any(kw in title for kw in ("洪", "涝")):
                event_type = "flood"
            elif any(kw in title for kw in ("台风", "风灾")):
                event_type = "typhoon"
            elif any(kw in title for kw in ("冻", "寒", "霜")):
                event_type = "frost"
            elif any(kw in title for kw in ("雹", "冰雹")):
                event_type = "hail"

            if title:
                items.append({
                    "date": date or datetime.now().strftime("%Y-%m-%d"),
                    "event_type": event_type,
                    "region": "",
                    "severity": "",
                    "affected_area": None,
                    "affected_unit": "hectares",
                    "affected_crops": "",
                    "estimated_loss": None,
                    "loss_unit": "CNY",
                    "summary": summary or title,
                    "source_url": response.url,
                })
        return items

    @staticmethod
    def _detect_weather_columns(headers: list[str]) -> tuple:
        date_col = event_col = region_col = severity_col = area_col = crop_col = loss_col = None
        for i, h in enumerate(headers):
            if date_col is None and any(k in h for k in ("日期", "date", "时间", "发生日期")):
                date_col = i
            elif event_col is None and any(k in h for k in ("灾害", "类型", "event", "type", "灾害类型")):
                event_col = i
            elif region_col is None and any(k in h for k in ("地区", "region", "省份", "区域", "发生地区")):
                region_col = i
            elif severity_col is None and any(k in h for k in ("程度", "severity", "等级", "级别")):
                severity_col = i
            elif area_col is None and any(k in h for k in ("面积", "area", "受灾面积")):
                area_col = i
            elif crop_col is None and any(k in h for k in ("作物", "crop", "受灾作物", "品种")):
                crop_col = i
            elif loss_col is None and any(k in h for k in ("损失", "loss", "经济损失", "金额")):
                loss_col = i
        return date_col, event_col, region_col, severity_col, area_col, crop_col, loss_col

    @staticmethod
    def _parse_weather_row(texts, date_col, event_col, region_col,
                           severity_col, area_col, crop_col, loss_col, url) -> dict | None:
        try:
            date_str = texts[date_col].strip() if date_col is not None and date_col < len(texts) else ""
            event_type = texts[event_col].strip() if event_col is not None and event_col < len(texts) else ""
            region = texts[region_col].strip() if region_col is not None and region_col < len(texts) else ""
            severity = texts[severity_col].strip() if severity_col is not None and severity_col < len(texts) else ""
            area_str = texts[area_col].strip() if area_col is not None and area_col < len(texts) else ""
            crops = texts[crop_col].strip() if crop_col is not None and crop_col < len(texts) else ""
            loss_str = texts[loss_col].strip() if loss_col is not None and loss_col < len(texts) else ""

            if not date_str or not event_type or not region:
                return None

            affected_area = float(NUMBER_RE.search(area_str).group().replace(",", "")) if area_str and NUMBER_RE.search(area_str) else None
            estimated_loss = float(NUMBER_RE.search(loss_str).group().replace(",", "")) if loss_str and NUMBER_RE.search(loss_str) else None

            return {
                "date": date_str,
                "event_type": event_type,
                "region": region,
                "severity": severity,
                "affected_area": affected_area,
                "affected_unit": "hectares",
                "affected_crops": crops,
                "estimated_loss": estimated_loss,
                "loss_unit": "CNY",
                "summary": "",
                "source_url": url,
            }
        except (ValueError, IndexError, AttributeError):
            return None

    def _extract_news(self, response: Response) -> list[dict]:
        items: list[dict] = []

        for article in response.css("div.article-item, div.news-item, li.news-item, li.article, ul.list li, div.list-item"):
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

            source = article.css("span.source::text, .source::text, .from::text").get("").strip()

            category = "general"
            if any(kw in title for kw in ("价格", "行情", "市场")):
                category = "market"
            elif any(kw in title for kw in ("政策", "法规", "通知")):
                category = "policy"
            elif any(kw in title for kw in ("生产", "种植", "养殖")):
                category = "production"
            elif any(kw in title for kw in ("贸易", "出口", "进口")):
                category = "trade"
            elif any(kw in title for kw in ("科技", "技术")):
                category = "technology"

            if not date:
                date_match = DATE_RE.search(article.get_all_text())
                date = date_match.group() if date_match else datetime.now().strftime("%Y-%m-%d")

            items.append({
                "title": title,
                "publish_date": date,
                "category": category,
                "source": source or "中国农业信息网",
                "summary": summary,
                "content": "",
                "source_url": response.url,
            })
        return items

    def _persist(self, table: str, item: dict):
        assert self._conn is not None
        try:
            now = datetime.now().isoformat()
            if table == "product_prices":
                self._conn.execute(
                    """INSERT OR REPLACE INTO product_prices
                        (date, product_name, category, market_name, province, price, price_unit,
                         price_high, price_low, change_pct, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["date"], item["product_name"], item.get("category", ""),
                     item.get("market_name", ""), item.get("province", ""),
                     item["price"], item.get("price_unit", "CNY/kg"),
                     item.get("price_high"), item.get("price_low"),
                     item.get("change_pct"), item.get("source_url", ""), now),
                )
            elif table == "supply_demand":
                self._conn.execute(
                    """INSERT OR REPLACE INTO supply_demand
                        (date, product_name, indicator_type, value, unit, region, trend, summary, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["date"], item["product_name"], item["indicator_type"],
                     item["value"], item.get("unit", ""), item.get("region", ""),
                     item.get("trend", ""), item.get("summary", ""),
                     item.get("source_url", ""), now),
                )
            elif table == "weather_impact":
                self._conn.execute(
                    """INSERT OR REPLACE INTO weather_impact
                        (date, event_type, region, severity, affected_area, affected_unit,
                         affected_crops, estimated_loss, loss_unit, summary, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["date"], item["event_type"], item["region"], item.get("severity", ""),
                     item.get("affected_area"), item.get("affected_unit", "hectares"),
                     item.get("affected_crops", ""), item.get("estimated_loss"),
                     item.get("loss_unit", "CNY"), item.get("summary", ""),
                     item.get("source_url", ""), now),
                )
            elif table == "agricultural_news":
                self._conn.execute(
                    """INSERT OR REPLACE INTO agricultural_news
                        (title, publish_date, category, source, summary, content, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["title"], item["publish_date"], item.get("category", "general"),
                     item.get("source", "中国农业信息网"), item.get("summary", ""),
                     item.get("content", ""), item.get("source_url", ""), now),
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
    result = AgriCnSpider().start()
    print(f"\n{'=' * 50}")
    print(f"Items   : {result.stats.items_scraped}")
    print(f"Requests: {result.stats.requests_count}")
    print(f"Time    : {result.stats.elapsed_seconds:.2f}s")
    print(f"{'=' * 50}")
