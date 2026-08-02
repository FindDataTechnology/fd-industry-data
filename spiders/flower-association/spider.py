from scrapling.spiders import Spider, Response, Request
from scrapling.fetchers import FetcherSession
import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "flower_association.db")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")


class FlowerAssociationSpider(Spider):
    name = "flower_association"
    start_urls = [
        "https://www.chinaflower.org.cn/",
        "https://www.chinaflower.org.cn/list/27",
        "https://www.chinaflower.org.cn/list/28",
        "https://www.chinaflower.org.cn/list/29",
        "https://www.chinaflower.org.cn/list/30",
    ]
    allowed_domains = {"chinaflower.org.cn"}
    concurrent_requests = 4
    download_delay = 1.5
    robots_txt_obey = True

    def configure_sessions(self, manager):
        manager.add("default", FetcherSession(impersonate="chrome"))

    async def parse(self, response: Response):
        page_type = self._detect_page_type(response.url)

        if page_type == "listing":
            for item in response.css("div.news-list li, div.list-item, ul.news-list li"):
                title = item.css("a::text").get("").strip()
                link = item.css("a::attr(href)").get("")
                date = item.css("span.date, span.time, em::text").get("").strip()
                if title and link:
                    full_url = response.urljoin(link)
                    yield Request(full_url, callback=self.parse_detail,
                                  meta={"title": title, "date": date, "source_url": response.url})

            next_page = response.css("a.next, a.page-next, div.pagination a:last-child::attr(href)").get()
            if next_page:
                yield response.follow(next_page, callback=self.parse)

        elif page_type == "home":
            for link in response.css("a::attr(href)").getall():
                if any(kw in link for kw in ["/list/", "/news/", "/info/", "/data/"]):
                    yield response.follow(link, callback=self.parse)

        else:
            yield Request(response.url, callback=self.parse_detail)

    async def parse_detail(self, response: Response):
        title = response.css("h1::text, h2.title::text, div.article-title::text").get("").strip()
        content = response.css("div.content, div.article-content, div.TRS_Editor").css("::text").getall()
        content_text = "\n".join(t.strip() for t in content if t.strip())
        pub_date = response.css("span.date, span.time, span.publish-date::text").get("").strip()
        source = response.css("span.source, span.from::text").get("").strip()

        if not title:
            title = response.meta.get("title", "")

        item = {
            "url": response.url,
            "title": title,
            "content": content_text[:5000],
            "publish_date": pub_date or response.meta.get("date", ""),
            "source": source or "中国花卉协会",
            "scraped_at": datetime.now().isoformat(),
            "category": self._extract_category(response.url),
        }
        self._save_to_sqlite(item)
        yield item

    def _detect_page_type(self, url: str) -> str:
        if url.rstrip("/").endswith("chinaflower.org.cn"):
            return "home"
        if "/list/" in url or "/news/" in url:
            return "listing"
        return "detail"

    def _extract_category(self, url: str) -> str:
        if "/list/27" in url:
            return "行业新闻"
        if "/list/28" in url:
            return "政策法规"
        if "/list/29" in url:
            return "市场行情"
        if "/list/30" in url:
            return "统计数据"
        return "其他"

    def _save_to_sqlite(self, item: dict):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS flower_association (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE,
                title TEXT,
                content TEXT,
                publish_date TEXT,
                source TEXT,
                category TEXT,
                scraped_at TEXT
            )
        """)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO flower_association (url, title, content, publish_date, source, category, scraped_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (item["url"], item["title"], item["content"], item["publish_date"], item["source"], item["category"], item["scraped_at"])
            )
            conn.commit()
        finally:
            conn.close()

    async def on_close(self):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM flower_association ORDER BY id").fetchall()
        conn.close()
        output_path = os.path.join(OUTPUT_DIR, "flower_association.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump([dict(r) for r in rows], f, ensure_ascii=False, indent=2)
        self.logger.info(f"Exported {len(rows)} items to {output_path}")
