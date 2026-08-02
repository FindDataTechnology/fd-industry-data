#!/usr/bin/env python3
"""
People's Daily Data Center Spider - 人民日报数据中心爬虫

Target: https://data.people.com.cn/
Data: News articles, economic statistics, policy documents, historical archives

Authentication: None required for public data
Rate Limiting: 2s delay, 4 concurrent requests
Anti-bot: Browser impersonation via Scrapling FetcherSession

Notes:
  - Public data only; no login required
  - Respect robots.txt
  - Some pages use JS rendering; stealth session available as fallback
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime

from scrapling.fetchers import FetcherSession, AsyncStealthySession
from scrapling.spiders import Request, Response, Spider

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "people_daily.db")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")


class PeopleDailySpider(Spider):
    name = "people_daily"
    start_urls = [
        "http://data.people.com.cn/rmrbs/data",
        "http://data.people.com.cn/rmrbs/data/2024",
        "http://data.people.com.cn/rmrbs/data/2025",
        "http://data.people.com.cn/rmrbs/data/2026",
        "http://paper.people.com.cn/rmrb/page/1.html",
        "http://data.people.com.cn/index",
    ]
    allowed_domains = {"people.com.cn", "data.people.com.cn", "paper.people.com.cn"}
    concurrent_requests = 4
    download_delay = 2.0
    robots_txt_obey = True

    def configure_sessions(self, manager):
        manager.add("http", FetcherSession(impersonate="chrome"))
        manager.add("stealth", AsyncStealthySession(headless=True), lazy=True)

    async def parse(self, response: Response):
        page_type = self._detect_page_type(response.url)

        if page_type == "index":
            for link in response.css("a::attr(href)").getall():
                if any(kw in link for kw in ["/data", "/rmrbs", "/paper"]):
                    yield response.follow(link, callback=self.parse)

        elif page_type == "date_list":
            for article_link in response.css("ul.list a, div.list a, table a"):
                href = article_link.css("::attr(href)").get("")
                title = article_link.css("::text").get("").strip()
                if href and title:
                    yield response.follow(
                        href,
                        callback=self.parse_article,
                        meta={"list_title": title},
                    )

            next_page = response.css("a.next::attr(href), a:contains('下一页')::attr(href)").get()
            if next_page:
                yield response.follow(next_page, callback=self.parse)

        elif page_type == "article":
            parsed = self._parse_article(response)
            if parsed:
                self._save_to_sqlite(parsed)
                yield parsed

        elif page_type == "paper_page":
            for article in response.css("div.article, div.content-box a"):
                href = article.css("::attr(href)").get("")
                if href:
                    yield response.follow(href, callback=self.parse_paper_article)

    async def parse_article(self, response: Response):
        parsed = self._parse_article(response)
        if parsed:
            parsed["list_title"] = response.meta.get("list_title", "")
            self._save_to_sqlite(parsed)
            yield parsed

    async def parse_paper_article(self, response: Response):
        title = response.css("h1::text, h2::text, div.article-title::text").get("").strip()
        if not title:
            return

        content_parts = response.css("div.article-content, div.content, article").css("::text").getall()
        content = "\n".join(t.strip() for t in content_parts if t.strip())

        author = response.css("span.author::text, div.author::text, span.source::text").get("").strip()
        pub_date = response.css("span.date::text, div.date::text, time::attr(datetime)").get("").strip()

        parsed = {
            "url": response.url,
            "title": title,
            "author": author,
            "pub_date": pub_date or datetime.now().strftime("%Y-%m-%d"),
            "content": content[:20000],
            "category": "人民日报电子版",
            "source": "paper.people.com.cn",
            "data_type": "paper_article",
            "scraped_at": datetime.now().isoformat(),
        }
        self._save_to_sqlite(parsed)
        yield parsed

    def _parse_article(self, response: Response) -> dict | None:
        title = response.css("h1::text, h2.title::text, div.title::text").get("").strip()
        if not title:
            return None

        content_parts = response.css(
            "div.rm_txtbox, div.article-content, div.text, div.content, article"
        ).css("::text").getall()
        content = "\n".join(t.strip() for t in content_parts if t.strip())

        author = response.css(
            "span.author::text, div.editor::text, span.source::text, p.editor::text"
        ).get("").strip()

        pub_date = response.css(
            "span.date::text, div.date::text, time::attr(datetime), span.time::text"
        ).get("").strip()

        category = response.css(
            "span.category::text, div.category a::text, a.channel::text"
        ).get("").strip()

        return {
            "url": response.url,
            "title": title,
            "author": author,
            "pub_date": pub_date or datetime.now().strftime("%Y-%m-%d"),
            "content": content[:20000],
            "category": category,
            "source": "data.people.com.cn",
            "data_type": "article",
            "scraped_at": datetime.now().isoformat(),
        }

    def _detect_page_type(self, url: str) -> str:
        if "paper.people.com.cn" in url:
            return "paper_page"
        if url.rstrip("/").endswith("/data") or url.endswith("/index"):
            return "index"
        if "/data/" in url and url.split("/data/")[-1].isdigit():
            return "date_list"
        if any(ext in url for ext in [".html", ".htm"]):
            return "article"
        return "index"

    def _save_to_sqlite(self, item: dict):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS people_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE,
                title TEXT,
                author TEXT,
                pub_date TEXT,
                content TEXT,
                category TEXT,
                source TEXT,
                list_title TEXT,
                data_type TEXT,
                scraped_at TEXT
            )
        """)
        try:
            conn.execute(
                """INSERT OR IGNORE INTO people_daily
                (url, title, author, pub_date, content, category, source, list_title, data_type, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item["url"], item["title"], item["author"], item["pub_date"],
                    item["content"], item["category"], item["source"],
                    item.get("list_title", ""), item["data_type"], item["scraped_at"],
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def on_close(self):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM people_daily ORDER BY id").fetchall()
        conn.close()
        output_path = os.path.join(OUTPUT_DIR, "people_daily.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump([dict(r) for r in rows], f, ensure_ascii=False, indent=2)
        self.logger.info(f"Exported {len(rows)} items to {output_path}")
