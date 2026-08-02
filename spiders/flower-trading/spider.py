from scrapling.spiders import Spider, Response, Request
from scrapling.fetchers import FetcherSession
from fd_industry_data.browser import get_browser_session
import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "flower_trading.db")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")


class FlowerTradingSpider(Spider):
    name = "flower_trading"
    start_urls = [
        "https://www.kunmingflower.com/",
        "https://www.kunmingflower.com/price",
        "https://www.kunmingflower.com/market",
        "https://www.kunmingflower.com/data",
    ]
    allowed_domains = {"kunmingflower.com"}
    concurrent_requests = 4
    download_delay = 2.0
    robots_txt_obey = True

    def configure_sessions(self, manager):
        manager.add("http", FetcherSession(impersonate="chrome"))
        manager.add("browser", get_browser_session(), lazy=True)

    async def parse(self, response: Response):
        page_type = self._detect_page_type(response.url)

        if page_type == "home":
            for link in response.css("a::attr(href)").getall():
                if any(kw in link for kw in ["/price", "/market", "/data", "/variety", "/trade"]):
                    yield response.follow(link, callback=self.parse)

        elif page_type == "price_list":
            for row in response.css("table.price-table tr, div.price-item, div.trade-record"):
                item = self._parse_price_row(row, response)
                if item:
                    self._save_to_sqlite(item)
                    yield item

            next_page = response.css("a.next, a.page-next, li.next a::attr(href)").get()
            if next_page:
                yield response.follow(next_page, callback=self.parse)

        elif page_type == "variety":
            for card in response.css("div.variety-card, div.product-item, li.variety-item"):
                item = self._parse_variety_card(card, response)
                if item:
                    self._save_to_sqlite(item)
                    yield item

            next_page = response.css("a.next, li.next a::attr(href)").get()
            if next_page:
                yield response.follow(next_page, callback=self.parse)

        elif page_type == "detail":
            yield Request(response.url, callback=self.parse_detail)

    async def parse_detail(self, response: Response):
        title = response.css("h1::text, h2::text").get("").strip()
        content_parts = response.css("div.content, div.detail, article").css("::text").getall()
        content = "\n".join(t.strip() for t in content_parts if t.strip())

        item = {
            "url": response.url,
            "title": title,
            "content": content[:5000],
            "data_type": "detail",
            "scraped_at": datetime.now().isoformat(),
        }
        self._save_to_sqlite(item)
        yield item

    def _parse_price_row(self, row, response):
        cells = row.css("td::text").getall()
        if len(cells) < 3:
            cells = row.css("span::text").getall()
        if len(cells) < 3:
            return None

        cells = [c.strip() for c in cells if c.strip()]
        if len(cells) < 3:
            return None

        return {
            "url": response.url,
            "flower_name": cells[0] if len(cells) > 0 else "",
            "variety": cells[1] if len(cells) > 1 else "",
            "spec": cells[2] if len(cells) > 2 else "",
            "unit": cells[3] if len(cells) > 3 else "枝",
            "price": cells[4] if len(cells) > 4 else "",
            "price_high": cells[5] if len(cells) > 5 else "",
            "price_low": cells[6] if len(cells) > 6 else "",
            "volume": cells[7] if len(cells) > 7 else "",
            "trade_date": cells[8] if len(cells) > 8 else "",
            "data_type": "price",
            "scraped_at": datetime.now().isoformat(),
        }

    def _parse_variety_card(self, card, response):
        name = card.css("h3::text, .name::text, .variety-name::text").get("").strip()
        if not name:
            return None

        stats = card.css("span.stat::text, .stat-value::text").getall()
        return {
            "url": response.url,
            "flower_name": name,
            "variety": card.css(".sub-name::text, .variety::text").get("").strip(),
            "spec": "",
            "unit": "枝",
            "price": stats[0].strip() if len(stats) > 0 else "",
            "price_high": stats[1].strip() if len(stats) > 1 else "",
            "price_low": stats[2].strip() if len(stats) > 2 else "",
            "volume": stats[3].strip() if len(stats) > 3 else "",
            "trade_date": card.css(".date::text, time::text").get("").strip(),
            "data_type": "variety",
            "scraped_at": datetime.now().isoformat(),
        }

    def _detect_page_type(self, url: str) -> str:
        if url.rstrip("/") in ("https://www.kunmingflower.com", "https://www.kunmingflower.com/"):
            return "home"
        if "/price" in url or "/market" in url or "/trade" in url:
            return "price_list"
        if "/variety" in url or "/data" in url:
            return "variety"
        return "detail"

    def _save_to_sqlite(self, item: dict):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS flower_trading (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT,
                flower_name TEXT,
                variety TEXT,
                spec TEXT,
                unit TEXT,
                price TEXT,
                price_high TEXT,
                price_low TEXT,
                volume TEXT,
                trade_date TEXT,
                data_type TEXT,
                title TEXT,
                content TEXT,
                scraped_at TEXT,
                UNIQUE(url, flower_name, trade_date)
            )
        """)
        try:
            if item.get("data_type") == "price" or item.get("data_type") == "variety":
                conn.execute(
                    """INSERT OR IGNORE INTO flower_trading
                    (url, flower_name, variety, spec, unit, price, price_high, price_low, volume, trade_date, data_type, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["url"], item["flower_name"], item["variety"], item["spec"],
                     item["unit"], item["price"], item["price_high"], item["price_low"],
                     item["volume"], item["trade_date"], item["data_type"], item["scraped_at"])
                )
            else:
                conn.execute(
                    """INSERT OR IGNORE INTO flower_trading (url, title, content, data_type, scraped_at)
                    VALUES (?, ?, ?, ?, ?)""",
                    (item["url"], item["title"], item["content"], item["data_type"], item["scraped_at"])
                )
            conn.commit()
        finally:
            conn.close()

    async def on_close(self):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM flower_trading ORDER BY id").fetchall()
        conn.close()
        output_path = os.path.join(OUTPUT_DIR, "flower_trading.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump([dict(r) for r in rows], f, ensure_ascii=False, indent=2)
        self.logger.info(f"Exported {len(rows)} items to {output_path}")
