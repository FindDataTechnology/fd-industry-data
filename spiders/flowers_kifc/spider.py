#!/usr/bin/env python3
"""
Scrapling spider for KIFC (Kunming International Flower Center) auction prices.
Data source: http://www.kifc.cn

Manifest:
  version: "1"
  name: flowers-kifc-auction
  label: KIFC Flower Auction Prices
  source_url: http://www.kifc.cn
  functions:
    - command: get_daily_prices
      category: commodity-pricing
      description: Daily fresh-cut flower auction prices
      frequency: daily
      columns:
        - name: date        (str)   Trading date
        - name: flower_name (str)   Flower variety
        - name: price       (float) Unit price in CNY
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

TABLE_CSS = "table"
ROW_CSS = "tr"
CELL_CSS = "td"
HEADER_CSS = "th"

DATE_RE = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}")
PRICE_RE = re.compile(r"[\d]+(?:[.,]\d+)?")
CURRENCY_SYMBOLS = re.compile(r"[¥￥$€£]")


class KIFCFlowerSpider(Spider):
    name = "kifc_flower_auction"
    start_urls = ["http://www.kifc.cn"]
    allowed_domains = {"kifc.cn"}
    concurrent_requests = 2
    download_delay = 1.0
    logging_level = logging.INFO

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._items: list[dict] = []
        self._conn: sqlite3.Connection | None = None
        self._price_page_found = False

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
                price       REAL    NOT NULL,
                unit        TEXT    DEFAULT 'CNY',
                scraped_at  TEXT    NOT NULL,
                UNIQUE(date, flower_name)
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_flower_prices_date
            ON flower_prices(date)
        """)
        self._conn.commit()

    async def parse(self, response: Response):
        items = self._extract_from_tables(response)
        if items:
            self.logger.info("Found %d price items on %s", len(items), response.url)
            for item in items:
                self._persist(item)
                yield item
            self._price_page_found = True
            return

        if not self._price_page_found:
            for path in PRICE_PAGE_PATHS:
                yield response.follow(path, callback=self.parse)

        links = response.css("a::attr(href)").getall()
        price_keywords = ("price", "价格", "行情", "交易", "拍卖", "data", "auction", "quote")
        for href in links:
            if any(kw in href.lower() for kw in price_keywords):
                yield response.follow(href, callback=self.parse)

    def _extract_from_tables(self, response: Response) -> list[dict]:
        items: list[dict] = []
        for table in response.css(TABLE_CSS):
            header_row = table.css(f"{ROW_CSS}:first-child")
            headers = []
            if header_row:
                headers = [
                    h.get_all_text(strip=True).lower()
                    for h in header_row.css(HEADER_CSS)
                ]

            date_col, name_col, price_col = self._detect_columns(headers)

            for row in table.css(ROW_CSS)[1:] if headers else table.css(ROW_CSS):
                cells = row.css(CELL_CSS)
                if len(cells) < 3:
                    continue
                texts = [c.get_all_text(strip=True) for c in cells]
                item = self._parse_row(texts, date_col, name_col, price_col)
                if item:
                    items.append(item)
        return items

    @staticmethod
    def _detect_columns(headers: list[str]) -> tuple[int | None, int | None, int | None]:
        date_col = name_col = price_col = None
        for i, h in enumerate(headers):
            if date_col is None and any(k in h for k in ("日期", "date", "时间", "time", "交易日")):
                date_col = i
            elif name_col is None and any(k in h for k in ("品种", "名称", "flower", "name", "品名", "花卉")):
                name_col = i
            elif price_col is None and any(k in h for k in ("价格", "price", "均价", "单价", "成交价")):
                price_col = i
        return date_col, name_col, price_col

    @staticmethod
    def _parse_row(
        texts: list[str],
        date_col: int | None,
        name_col: int | None,
        price_col: int | None,
    ) -> dict | None:
        try:
            if date_col is not None and name_col is not None and price_col is not None:
                date_str = texts[date_col].strip()
                flower = texts[name_col].strip()
                price_str = texts[price_col].strip()
            else:
                date_str = flower = price_str = ""
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
            return {
                "date": date_str,
                "flower_name": flower,
                "price": price,
                "unit": "CNY",
            }
        except (ValueError, IndexError, AttributeError):
            return None

    def _persist(self, item: dict):
        assert self._conn is not None
        try:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO flower_prices
                    (date, flower_name, price, unit, scraped_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    item["date"],
                    item["flower_name"],
                    item["price"],
                    item["unit"],
                    datetime.now().isoformat(),
                ),
            )
            self._items.append(item)
        except Exception as exc:
            self.logger.error("DB insert error for %s: %s", item, exc)

    def _save_json(self):
        if not self._items:
            self.logger.warning("No items to save to JSON")
            return
        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(self._items, f, ensure_ascii=False, indent=2)
        self.logger.info("Saved %d items to %s", len(self._items), JSON_PATH)


if __name__ == "__main__":
    result = KIFCFlowerSpider().start()
    print(f"\n{'=' * 50}")
    print(f"Items   : {result.stats.items_scraped}")
    print(f"Requests: {result.stats.requests_count}")
    print(f"Time    : {result.stats.elapsed_seconds:.2f}s")
    print(f"{'=' * 50}")
