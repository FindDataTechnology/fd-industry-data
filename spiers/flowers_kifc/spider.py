#!/usr/bin/env python3
"""
Scrapling spider for KIFC (Kunming International Flower Center) auction data.
Data source: http://www.kifc.cn

Manifest:
  version: "1"
  name: kifc-flower-auction
  label: KIFC Flower Auction Data
  source_url: http://www.kifc.cn
  functions:
    - command: get_daily_prices
      category: commodity-pricing
      description: Daily fresh-cut flower auction prices
      frequency: daily
    - command: get_trading_volumes
      category: market-volume
      description: Trading volumes by flower type
      frequency: daily
    - command: get_market_trends
      category: market-analysis
      description: Market trends and analysis
      frequency: weekly
    - command: get_export_data
      category: trade-data
      description: Export data and statistics
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
DB_PATH = BASE_DIR / "data" / "kifc_flowers.db"
JSON_PATH = BASE_DIR / "output" / "kifc_flowers.json"

PRICE_PAGE_PATHS = [
    "/price",
    "/prices",
    "/data",
    "/data/price",
    "/auction",
    "/auction/price",
    "/quote",
    "/market/price",
    "/info/price",
    "/trade/price",
    "/flower/price",
    "/product/price",
    "/list",
    "/news/price",
]

VOLUME_PAGE_PATHS = [
    "/volume",
    "/data/volume",
    "/trade/volume",
    "/auction/volume",
    "/statistics",
    "/data/statistics",
]

EXPORT_PAGE_PATHS = [
    "/export",
    "/trade/export",
    "/data/export",
    "/international",
    "/trade/international",
]

TREND_PAGE_PATHS = [
    "/trend",
    "/analysis",
    "/market/analysis",
    "/data/trend",
    "/report",
    "/market/report",
]

DATE_RE = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}")
PRICE_RE = re.compile(r"[\d]+(?:[.,]\d+)?")
VOLUME_RE = re.compile(r"[\d,]+(?:\.\d+)?")
CURRENCY_SYMBOLS = re.compile(r"[¥￥$€£]")


class KIFCFlowerSpider(Spider):
    name = "kifc_flower_auction"
    start_urls = ["http://www.kifc.cn"]
    allowed_domains = {"kifc.cn"}
    concurrent_requests = 2
    download_delay = 1.5
    logging_level = logging.INFO
    robots_txt_obey = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._items: list[dict] = []
        self._conn: sqlite3.Connection | None = None
        self._visited_paths: set[str] = set()

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
            CREATE TABLE IF NOT EXISTS flower_prices (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT    NOT NULL,
                flower_name TEXT    NOT NULL,
                variety     TEXT    DEFAULT '',
                spec        TEXT    DEFAULT '',
                price       REAL    NOT NULL,
                price_high  REAL,
                price_low   REAL,
                unit        TEXT    DEFAULT 'CNY/stem',
                source_url  TEXT    DEFAULT '',
                scraped_at  TEXT    NOT NULL,
                UNIQUE(date, flower_name, variety, spec)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trading_volumes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT    NOT NULL,
                flower_name TEXT    NOT NULL,
                category    TEXT    DEFAULT '',
                volume      REAL    NOT NULL,
                volume_unit TEXT    DEFAULT 'stems',
                turnover    REAL,
                source_url  TEXT    DEFAULT '',
                scraped_at  TEXT    NOT NULL,
                UNIQUE(date, flower_name, category)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS market_trends (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                period      TEXT    NOT NULL,
                flower_name TEXT    DEFAULT '',
                trend_type  TEXT    NOT NULL,
                value       TEXT    NOT NULL,
                change_pct  REAL,
                summary     TEXT    DEFAULT '',
                source_url  TEXT    DEFAULT '',
                scraped_at  TEXT    NOT NULL,
                UNIQUE(period, flower_name, trend_type)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS export_data (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                period          TEXT    NOT NULL,
                flower_name     TEXT    DEFAULT '',
                destination     TEXT    DEFAULT '',
                quantity        REAL,
                quantity_unit   TEXT    DEFAULT 'kg',
                value_usd       REAL,
                value_cny       REAL,
                source_url      TEXT    DEFAULT '',
                scraped_at      TEXT    NOT NULL,
                UNIQUE(period, flower_name, destination)
            )
        """)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_prices_date ON flower_prices(date)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_prices_name ON flower_prices(flower_name)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_volumes_date ON trading_volumes(date)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_trends_period ON market_trends(period)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_export_period ON export_data(period)")
        self._conn.commit()

    async def parse(self, response: Response):
        page_type = self._detect_page_type(response.url)

        if page_type == "price":
            items = self._extract_prices(response)
            for item in items:
                self._persist("flower_prices", item)
                self._items.append({**item, "_table": "flower_prices"})
            if items:
                self.logger.info("Found %d price items on %s", len(items), response.url)
                return

        elif page_type == "volume":
            items = self._extract_volumes(response)
            for item in items:
                self._persist("trading_volumes", item)
                self._items.append({**item, "_table": "trading_volumes"})
            if items:
                self.logger.info("Found %d volume items on %s", len(items), response.url)
                return

        elif page_type == "trend":
            items = self._extract_trends(response)
            for item in items:
                self._persist("market_trends", item)
                self._items.append({**item, "_table": "market_trends"})
            if items:
                self.logger.info("Found %d trend items on %s", len(items), response.url)
                return

        elif page_type == "export":
            items = self._extract_exports(response)
            for item in items:
                self._persist("export_data", item)
                self._items.append({**item, "_table": "export_data"})
            if items:
                self.logger.info("Found %d export items on %s", len(items), response.url)
                return

        links = response.css("a::attr(href)").getall()
        for href in links:
            href_lower = href.lower()
            if any(kw in href_lower for kw in ("price", "价格", "行情", "拍卖", "auction", "quote")):
                yield response.follow(href, callback=self.parse)
            elif any(kw in href_lower for kw in ("volume", "量", "统计", "statistic")):
                yield response.follow(href, callback=self.parse)
            elif any(kw in href_lower for kw in ("export", "出口", "国际", "international")):
                yield response.follow(href, callback=self.parse)
            elif any(kw in href_lower for kw in ("trend", "分析", "报告", "report", "analysis")):
                yield response.follow(href, callback=self.parse)

    def _detect_page_type(self, url: str) -> str:
        url_lower = url.lower()
        if any(p in url_lower for p in ("price", "价格", "行情", "auction")):
            return "price"
        if any(p in url_lower for p in ("volume", "量", "统计")):
            return "volume"
        if any(p in url_lower for p in ("trend", "分析", "report", "analysis")):
            return "trend"
        if any(p in url_lower for p in ("export", "出口", "international")):
            return "export"
        return "home"

    def _extract_prices(self, response: Response) -> list[dict]:
        items: list[dict] = []
        for table in response.css("table"):
            header_row = table.css("tr:first-child")
            headers = []
            if header_row:
                headers = [h.get_all_text(strip=True).lower() for h in header_row.css("th")]

            date_col, name_col, price_col, variety_col, spec_col, high_col, low_col = self._detect_price_columns(headers)

            for row in table.css("tr")[1:] if headers else table.css("tr"):
                cells = row.css("td")
                if len(cells) < 3:
                    continue
                texts = [c.get_all_text(strip=True) for c in cells]
                item = self._parse_price_row(texts, date_col, name_col, price_col,
                                             variety_col, spec_col, high_col, low_col, response.url)
                if item:
                    items.append(item)
        return items

    @staticmethod
    def _detect_price_columns(headers: list[str]) -> tuple:
        date_col = name_col = price_col = variety_col = spec_col = high_col = low_col = None
        for i, h in enumerate(headers):
            if date_col is None and any(k in h for k in ("日期", "date", "时间", "time", "交易日")):
                date_col = i
            elif name_col is None and any(k in h for k in ("品种", "名称", "flower", "name", "品名", "花卉")):
                name_col = i
            elif price_col is None and any(k in h for k in ("价格", "price", "均价", "单价", "成交价")):
                price_col = i
            elif variety_col is None and any(k in h for k in ("品种", "variety", "类别", "类型")):
                variety_col = i
            elif spec_col is None and any(k in h for k in ("规格", "spec", "等级", "grade", "长度")):
                spec_col = i
            elif high_col is None and any(k in h for k in ("最高", "high", "最高价")):
                high_col = i
            elif low_col is None and any(k in h for k in ("最低", "low", "最低价")):
                low_col = i
        return date_col, name_col, price_col, variety_col, spec_col, high_col, low_col

    @staticmethod
    def _parse_price_row(texts, date_col, name_col, price_col,
                         variety_col, spec_col, high_col, low_col, url) -> dict | None:
        try:
            if date_col is not None and name_col is not None and price_col is not None:
                date_str = texts[date_col].strip()
                flower = texts[name_col].strip()
                price_str = texts[price_col].strip()
                variety = texts[variety_col].strip() if variety_col is not None and variety_col < len(texts) else ""
                spec = texts[spec_col].strip() if spec_col is not None and spec_col < len(texts) else ""
                high_str = texts[high_col].strip() if high_col is not None and high_col < len(texts) else ""
                low_str = texts[low_col].strip() if low_col is not None and low_col < len(texts) else ""
            else:
                date_str = flower = price_str = ""
                variety = spec = high_str = low_str = ""
                for t in texts:
                    if not date_str and DATE_RE.search(t):
                        date_str = DATE_RE.search(t).group()
                    elif not flower and re.search(r"[\u4e00-\u9fff]", t) and len(t) >= 2:
                        flower = t
                    elif not price_str and PRICE_RE.search(CURRENCY_SYMBOLS.sub("", t)):
                        cleaned = CURRENCY_SYMBOLS.sub("", t).replace(",", "").strip()
                        if PRICE_RE.fullmatch(cleaned):
                            price_str = cleaned

            if not all([date_str, flower, price_str]):
                return None

            price = float(PRICE_RE.search(price_str).group().replace(",", ""))
            price_high = float(PRICE_RE.search(high_str).group().replace(",", "")) if high_str and PRICE_RE.search(high_str) else None
            price_low = float(PRICE_RE.search(low_str).group().replace(",", "")) if low_str and PRICE_RE.search(low_str) else None

            return {
                "date": date_str,
                "flower_name": flower,
                "variety": variety,
                "spec": spec,
                "price": price,
                "price_high": price_high,
                "price_low": price_low,
                "unit": "CNY/stem",
                "source_url": url,
            }
        except (ValueError, IndexError, AttributeError):
            return None

    def _extract_volumes(self, response: Response) -> list[dict]:
        items: list[dict] = []
        for table in response.css("table"):
            header_row = table.css("tr:first-child")
            headers = [h.get_all_text(strip=True).lower() for h in header_row.css("th")] if header_row else []

            date_col, name_col, vol_col, turnover_col, cat_col = self._detect_volume_columns(headers)

            for row in table.css("tr")[1:] if headers else table.css("tr"):
                cells = row.css("td")
                if len(cells) < 2:
                    continue
                texts = [c.get_all_text(strip=True) for c in cells]
                item = self._parse_volume_row(texts, date_col, name_col, vol_col, turnover_col, cat_col, response.url)
                if item:
                    items.append(item)
        return items

    @staticmethod
    def _detect_volume_columns(headers: list[str]) -> tuple:
        date_col = name_col = vol_col = turnover_col = cat_col = None
        for i, h in enumerate(headers):
            if date_col is None and any(k in h for k in ("日期", "date", "时间", "月份", "month")):
                date_col = i
            elif name_col is None and any(k in h for k in ("品种", "名称", "flower", "name", "品名", "花卉")):
                name_col = i
            elif vol_col is None and any(k in h for k in ("成交量", "volume", "数量", "交易量", "拍卖量")):
                vol_col = i
            elif turnover_col is None and any(k in h for k in ("成交额", "turnover", "金额", "交易额")):
                turnover_col = i
            elif cat_col is None and any(k in h for k in ("类别", "category", "分类", "类型")):
                cat_col = i
        return date_col, name_col, vol_col, turnover_col, cat_col

    @staticmethod
    def _parse_volume_row(texts, date_col, name_col, vol_col, turnover_col, cat_col, url) -> dict | None:
        try:
            date_str = texts[date_col].strip() if date_col is not None and date_col < len(texts) else ""
            flower = texts[name_col].strip() if name_col is not None and name_col < len(texts) else ""
            vol_str = texts[vol_col].strip() if vol_col is not None and vol_col < len(texts) else ""
            turnover_str = texts[turnover_col].strip() if turnover_col is not None and turnover_col < len(texts) else ""
            category = texts[cat_col].strip() if cat_col is not None and cat_col < len(texts) else ""

            if not date_str or not flower or not vol_str:
                return None

            volume = float(VOLUME_RE.search(vol_str).group().replace(",", "")) if VOLUME_RE.search(vol_str) else 0
            turnover = float(VOLUME_RE.search(turnover_str).group().replace(",", "")) if turnover_str and VOLUME_RE.search(turnover_str) else None

            return {
                "date": date_str,
                "flower_name": flower,
                "category": category,
                "volume": volume,
                "volume_unit": "stems",
                "turnover": turnover,
                "source_url": url,
            }
        except (ValueError, IndexError, AttributeError):
            return None

    def _extract_trends(self, response: Response) -> list[dict]:
        items: list[dict] = []
        for table in response.css("table"):
            header_row = table.css("tr:first-child")
            headers = [h.get_all_text(strip=True).lower() for h in header_row.css("th")] if header_row else []

            for row in table.css("tr")[1:] if headers else table.css("tr"):
                cells = row.css("td")
                if len(cells) < 2:
                    continue
                texts = [c.get_all_text(strip=True) for c in cells]
                item = self._parse_trend_row(texts, headers, response.url)
                if item:
                    items.append(item)

        for article in response.css("div.article, div.content, div.report, div.analysis"):
            title = article.css("h1::text, h2::text, h3::text").get("").strip()
            summary = article.css("p::text").get("").strip()
            if title:
                items.append({
                    "period": datetime.now().strftime("%Y-%m"),
                    "flower_name": "",
                    "trend_type": "report",
                    "value": title,
                    "change_pct": None,
                    "summary": summary[:500],
                    "source_url": response.url,
                })
        return items

    @staticmethod
    def _parse_trend_row(texts, headers, url) -> dict | None:
        try:
            period = texts[0].strip() if len(texts) > 0 else ""
            flower = texts[1].strip() if len(texts) > 1 else ""
            trend_type = texts[2].strip() if len(texts) > 2 else "price_index"
            value = texts[3].strip() if len(texts) > 3 else ""
            change_pct = None
            if len(texts) > 4:
                pct_match = re.search(r"[-+]?\d+\.?\d*", texts[4].replace("%", ""))
                if pct_match:
                    change_pct = float(pct_match.group())

            if not period or not value:
                return None

            return {
                "period": period,
                "flower_name": flower,
                "trend_type": trend_type,
                "value": value,
                "change_pct": change_pct,
                "summary": "",
                "source_url": url,
            }
        except (ValueError, IndexError):
            return None

    def _extract_exports(self, response: Response) -> list[dict]:
        items: list[dict] = []
        for table in response.css("table"):
            header_row = table.css("tr:first-child")
            headers = [h.get_all_text(strip=True).lower() for h in header_row.css("th")] if header_row else []

            period_col, name_col, dest_col, qty_col, val_col = self._detect_export_columns(headers)

            for row in table.css("tr")[1:] if headers else table.css("tr"):
                cells = row.css("td")
                if len(cells) < 3:
                    continue
                texts = [c.get_all_text(strip=True) for c in cells]
                item = self._parse_export_row(texts, period_col, name_col, dest_col, qty_col, val_col, response.url)
                if item:
                    items.append(item)
        return items

    @staticmethod
    def _detect_export_columns(headers: list[str]) -> tuple:
        period_col = name_col = dest_col = qty_col = val_col = None
        for i, h in enumerate(headers):
            if period_col is None and any(k in h for k in ("日期", "date", "月份", "month", "年份", "year", "period")):
                period_col = i
            elif name_col is None and any(k in h for k in ("品种", "名称", "flower", "name", "品名")):
                name_col = i
            elif dest_col is None and any(k in h for k in ("目的地", "destination", "国家", "country", "地区", "region")):
                dest_col = i
            elif qty_col is None and any(k in h for k in ("数量", "quantity", "重量", "weight", "volume")):
                qty_col = i
            elif val_col is None and any(k in h for k in ("金额", "value", "价格", "amount", "总额")):
                val_col = i
        return period_col, name_col, dest_col, qty_col, val_col

    @staticmethod
    def _parse_export_row(texts, period_col, name_col, dest_col, qty_col, val_col, url) -> dict | None:
        try:
            period = texts[period_col].strip() if period_col is not None and period_col < len(texts) else ""
            flower = texts[name_col].strip() if name_col is not None and name_col < len(texts) else ""
            destination = texts[dest_col].strip() if dest_col is not None and dest_col < len(texts) else ""
            qty_str = texts[qty_col].strip() if qty_col is not None and qty_col < len(texts) else ""
            val_str = texts[val_col].strip() if val_col is not None and val_col < len(texts) else ""

            if not period:
                return None

            quantity = float(VOLUME_RE.search(qty_str).group().replace(",", "")) if qty_str and VOLUME_RE.search(qty_str) else None
            value = float(VOLUME_RE.search(val_str).group().replace(",", "")) if val_str and VOLUME_RE.search(val_str) else None

            return {
                "period": period,
                "flower_name": flower,
                "destination": destination,
                "quantity": quantity,
                "quantity_unit": "kg",
                "value_usd": None,
                "value_cny": value,
                "source_url": url,
            }
        except (ValueError, IndexError, AttributeError):
            return None

    def _persist(self, table: str, item: dict):
        assert self._conn is not None
        try:
            if table == "flower_prices":
                self._conn.execute(
                    """INSERT OR REPLACE INTO flower_prices
                        (date, flower_name, variety, spec, price, price_high, price_low, unit, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["date"], item["flower_name"], item.get("variety", ""), item.get("spec", ""),
                     item["price"], item.get("price_high"), item.get("price_low"),
                     item["unit"], item.get("source_url", ""), datetime.now().isoformat()),
                )
            elif table == "trading_volumes":
                self._conn.execute(
                    """INSERT OR REPLACE INTO trading_volumes
                        (date, flower_name, category, volume, volume_unit, turnover, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["date"], item["flower_name"], item.get("category", ""),
                     item["volume"], item.get("volume_unit", "stems"), item.get("turnover"),
                     item.get("source_url", ""), datetime.now().isoformat()),
                )
            elif table == "market_trends":
                self._conn.execute(
                    """INSERT OR REPLACE INTO market_trends
                        (period, flower_name, trend_type, value, change_pct, summary, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["period"], item.get("flower_name", ""), item["trend_type"],
                     item["value"], item.get("change_pct"), item.get("summary", ""),
                     item.get("source_url", ""), datetime.now().isoformat()),
                )
            elif table == "export_data":
                self._conn.execute(
                    """INSERT OR REPLACE INTO export_data
                        (period, flower_name, destination, quantity, quantity_unit, value_usd, value_cny, source_url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["period"], item.get("flower_name", ""), item.get("destination", ""),
                     item.get("quantity"), item.get("quantity_unit", "kg"),
                     item.get("value_usd"), item.get("value_cny"),
                     item.get("source_url", ""), datetime.now().isoformat()),
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
    result = KIFCFlowerSpider().start()
    print(f"\n{'=' * 50}")
    print(f"Items   : {result.stats.items_scraped}")
    print(f"Requests: {result.stats.requests_count}")
    print(f"Time    : {result.stats.elapsed_seconds:.2f}s")
    print(f"{'=' * 50}")
