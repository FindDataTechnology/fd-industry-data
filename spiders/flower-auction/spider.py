from scrapling.spiders import Spider, Response, Request
from scrapling.fetchers import FetcherSession
from fd_industry_data.browser import get_browser_session
import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "flower_auction.db")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")


class FlowerAuctionSpider(Spider):
    name = "flower_auction"
    start_urls = [
        "http://www.kifc.cn/",
        "http://www.kifc.cn/auction",
        "http://www.kifc.cn/price",
        "http://www.kifc.cn/data",
        "http://www.kifc.cn/market",
    ]
    allowed_domains = {"kifc.cn"}
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
                if any(kw in link for kw in ["/auction", "/price", "/data", "/market", "/result"]):
                    yield response.follow(link, callback=self.parse)

        elif page_type == "auction_list":
            for item in response.css("div.auction-item, tr.auction-row, div.result-item"):
                parsed = self._parse_auction_item(item, response)
                if parsed:
                    self._save_to_sqlite(parsed)
                    yield parsed

            next_page = response.css("a.next, a.page-next, li.next a::attr(href)").get()
            if next_page:
                yield response.follow(next_page, callback=self.parse)

        elif page_type == "price_data":
            for row in response.css("table.data-table tr, div.price-record"):
                parsed = self._parse_price_data(row, response)
                if parsed:
                    self._save_to_sqlite(parsed)
                    yield parsed

            next_page = response.css("a.next, li.next a::attr(href)").get()
            if next_page:
                yield response.follow(next_page, callback=self.parse)

        elif page_type == "market_analysis":
            for card in response.css("div.analysis-card, div.market-item"):
                parsed = self._parse_market_card(card, response)
                if parsed:
                    self._save_to_sqlite(parsed)
                    yield parsed

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

    def _parse_auction_item(self, item_elem, response):
        cells = item_elem.css("td::text, span::text").getall()
        cells = [c.strip() for c in cells if c.strip()]

        if len(cells) < 4:
            return None

        return {
            "url": response.url,
            "auction_date": cells[0] if len(cells) > 0 else "",
            "flower_name": cells[1] if len(cells) > 1 else "",
            "variety": cells[2] if len(cells) > 2 else "",
            "spec": cells[3] if len(cells) > 3 else "",
            "quantity": cells[4] if len(cells) > 4 else "",
            "unit": cells[5] if len(cells) > 5 else "枝",
            "avg_price": cells[6] if len(cells) > 6 else "",
            "price_high": cells[7] if len(cells) > 7 else "",
            "price_low": cells[8] if len(cells) > 8 else "",
            "transaction_rate": cells[9] if len(cells) > 9 else "",
            "data_type": "auction",
            "scraped_at": datetime.now().isoformat(),
        }

    def _parse_price_data(self, row, response):
        cells = row.css("td::text").getall()
        if len(cells) < 3:
            cells = row.css("span::text").getall()

        cells = [c.strip() for c in cells if c.strip()]
        if len(cells) < 3:
            return None

        return {
            "url": response.url,
            "date": cells[0] if len(cells) > 0 else "",
            "flower_name": cells[1] if len(cells) > 1 else "",
            "variety": cells[2] if len(cells) > 2 else "",
            "spec": cells[3] if len(cells) > 3 else "",
            "unit": cells[4] if len(cells) > 4 else "枝",
            "price": cells[5] if len(cells) > 5 else "",
            "price_high": cells[6] if len(cells) > 6 else "",
            "price_low": cells[7] if len(cells) > 7 else "",
            "volume": cells[8] if len(cells) > 8 else "",
            "data_type": "price",
            "scraped_at": datetime.now().isoformat(),
        }

    def _parse_market_card(self, card, response):
        title = card.css("h3::text, .title::text").get("").strip()
        if not title:
            return None

        stats = card.css("span.stat::text, .value::text").getall()
        return {
            "url": response.url,
            "title": title,
            "summary": card.css("p.summary::text, .desc::text").get("").strip(),
            "avg_price": stats[0].strip() if len(stats) > 0 else "",
            "total_volume": stats[1].strip() if len(stats) > 1 else "",
            "transaction_rate": stats[2].strip() if len(stats) > 2 else "",
            "date_range": card.css(".date-range::text, time::text").get("").strip(),
            "data_type": "market",
            "scraped_at": datetime.now().isoformat(),
        }

    def _detect_page_type(self, url: str) -> str:
        if url.rstrip("/") in ("http://www.kifc.cn", "http://www.kifc.cn/"):
            return "home"
        if "/auction" in url or "/result" in url:
            return "auction_list"
        if "/price" in url or "/data" in url:
            return "price_data"
        if "/market" in url:
            return "market_analysis"
        return "detail"

    def _save_to_sqlite(self, item: dict):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS flower_auction (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT,
                auction_date TEXT,
                date TEXT,
                flower_name TEXT,
                variety TEXT,
                spec TEXT,
                quantity TEXT,
                unit TEXT,
                price TEXT,
                avg_price TEXT,
                price_high TEXT,
                price_low TEXT,
                volume TEXT,
                total_volume TEXT,
                transaction_rate TEXT,
                title TEXT,
                summary TEXT,
                content TEXT,
                date_range TEXT,
                data_type TEXT,
                scraped_at TEXT,
                UNIQUE(url, flower_name, COALESCE(auction_date, date))
            )
        """)
        try:
            if item.get("data_type") == "auction":
                conn.execute(
                    """INSERT OR IGNORE INTO flower_auction
                    (url, auction_date, flower_name, variety, spec, quantity, unit, avg_price, price_high, price_low, transaction_rate, data_type, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["url"], item["auction_date"], item["flower_name"], item["variety"],
                     item["spec"], item["quantity"], item["unit"], item["avg_price"],
                     item["price_high"], item["price_low"], item["transaction_rate"],
                     item["data_type"], item["scraped_at"])
                )
            elif item.get("data_type") == "price":
                conn.execute(
                    """INSERT OR IGNORE INTO flower_auction
                    (url, date, flower_name, variety, spec, unit, price, price_high, price_low, volume, data_type, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["url"], item["date"], item["flower_name"], item["variety"],
                     item["spec"], item["unit"], item["price"], item["price_high"],
                     item["price_low"], item["volume"], item["data_type"], item["scraped_at"])
                )
            elif item.get("data_type") == "market":
                conn.execute(
                    """INSERT OR IGNORE INTO flower_auction
                    (url, title, summary, avg_price, total_volume, transaction_rate, date_range, data_type, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["url"], item["title"], item["summary"], item["avg_price"],
                     item["total_volume"], item["transaction_rate"], item["date_range"],
                     item["data_type"], item["scraped_at"])
                )
            else:
                conn.execute(
                    """INSERT OR IGNORE INTO flower_auction (url, title, content, data_type, scraped_at)
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
        rows = conn.execute("SELECT * FROM flower_auction ORDER BY id").fetchall()
        conn.close()
        output_path = os.path.join(OUTPUT_DIR, "flower_auction.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump([dict(r) for r in rows], f, ensure_ascii=False, indent=2)
        self.logger.info(f"Exported {len(rows)} items to {output_path}")


def run_flower_auction(limit: int | None = None) -> list[dict]:
    """Entry point for fd-open-data-protocol dispatch."""
    from scrapling import Fetcher

    items: list[dict] = []
    fetcher = Fetcher(auto_match=False, impersonate="chrome")
    spider = FlowerAuctionSpider()

    for url in FlowerAuctionSpider.start_urls:
        try:
            response = fetcher.get(url, timeout=30, stealthy_headers=True)
            if response.status != 200:
                continue
            page_type = spider._detect_page_type(url)
            if page_type == "auction_list":
                for item_elem in response.css("div.auction-item, tr.auction-row, div.result-item"):
                    parsed = spider._parse_auction_item(item_elem, response)
                    if parsed:
                        items.append(parsed)
            elif page_type == "price_data":
                for row in response.css("table.data-table tr, div.price-record"):
                    parsed = spider._parse_price_data(row, response)
                    if parsed:
                        items.append(parsed)
            elif page_type == "market_analysis":
                for card in response.css("div.analysis-card, div.market-item"):
                    parsed = spider._parse_market_card(card, response)
                    if parsed:
                        items.append(parsed)
        except Exception:
            pass

    if limit is not None:
        items = items[:limit]
    return items
