#!/usr/bin/env python3
"""
Toutiao Open Platform Spider - 今日头条开放平台爬虫

Target: https://open.toutiao.com/ / https://www.toutiao.com/
Data: News articles, trending topics, user engagement data

AUTHENTICATION WARNING:
  Toutiao Open Platform API requires developer registration.
  This spider ONLY accesses publicly available web pages.
  
  What this spider CAN access:
  - Public news articles and content pages
  - Trending topics / hot list
  - Channel/category pages
  - Public user profiles
  
  What this spider CANNOT access (requires API key):
  - Content creation API - needs Open Platform registration
  - Analytics dashboard - needs creator account
  - Comment management API - needs OAuth
  - Monetization data - needs creator account
  
  To access API data:
  1. Register at https://open.toutiao.com/
  2. Create an application
  3. API docs: https://open.toutiao.com/docs

Rate Limiting: 2s delay, 4 concurrent
Anti-bot: Stealth session for protected pages, dynamic content requires JS rendering
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime

from scrapling.fetchers import FetcherSession, AsyncStealthySession
from scrapling.spiders import Request, Response, Spider

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "toutiao_open.db")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")


class ToutiaoOpenSpider(Spider):
    name = "toutiao_open"
    start_urls = [
        "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc",
        "https://www.toutiao.com/ch/news_tech/",
        "https://www.toutiao.com/ch/news_finance/",
        "https://www.toutiao.com/ch/news_society/",
        "https://www.toutiao.com/",
    ]
    allowed_domains = {"toutiao.com", "open.toutiao.com", "www.toutiao.com"}
    concurrent_requests = 4
    download_delay = 2.0
    robots_txt_obey = True

    def configure_sessions(self, manager):
        manager.add("http", FetcherSession(impersonate="chrome"))
        manager.add("stealth", AsyncStealthySession(
            headless=True,
            network_idle=True,
        ), lazy=True)

    async def parse(self, response: Response):
        page_type = self._detect_page_type(response.url)

        if page_type == "hot_board":
            for item in response.css("div.hot-item, li.hot-item, div[class*='hot']"):
                title = item.css("a span::text, h3::text, div.title::text").get("").strip()
                href = item.css("a::attr(href)").get("")
                hot_value = item.css("span.hot-value::text, div.hot-value::text").get("").strip()
                rank = item.css("span.rank::text, div.rank::text").get("").strip()

                if not title:
                    continue

                if href and not href.startswith("http"):
                    href = f"https://www.toutiao.com{href}"

                parsed = {
                    "url": href or response.url,
                    "title": title,
                    "hot_value": hot_value,
                    "rank": rank,
                    "category": "热榜",
                    "data_type": "hot_item",
                    "scraped_at": datetime.now().isoformat(),
                }
                self._save_to_sqlite(parsed)
                yield parsed

                if href:
                    yield Request(
                        href,
                        callback=self.parse_article,
                        meta={"rank": rank, "hot_value": hot_value},
                    )

        elif page_type == "channel":
            for card in response.css("div.article-card, div.feed-card, div[class*='Card']"):
                title = card.css("a span.title::text, h3::text, div.title::text").get("").strip()
                href = card.css("a::attr(href)").get("")

                if not title or not href:
                    continue

                if not href.startswith("http"):
                    href = f"https://www.toutiao.com{href}"

                author = card.css("span.name::text, a.author::text").get("").strip()
                comment_count = card.css("span.comment::text").get("").strip()

                yield Request(
                    href,
                    callback=self.parse_article,
                    meta={
                        "list_title": title,
                        "list_author": author,
                        "list_comments": comment_count,
                    },
                )

            next_page = response.css("a.load-more::attr(href), a.next::attr(href)").get()
            if next_page:
                yield response.follow(next_page, callback=self.parse)

        elif page_type == "article":
            parsed = self._parse_article_detail(response)
            if parsed:
                self._save_to_sqlite(parsed)
                yield parsed

        elif page_type == "index":
            for link in response.css("a::attr(href)").getall():
                if "/article/" in link or "/group/" in link:
                    full_url = f"https://www.toutiao.com{link}" if link.startswith("/") else link
                    yield Request(full_url, callback=self.parse_article)

    async def parse_article(self, response: Response):
        parsed = self._parse_article_detail(response)
        if parsed:
            parsed["list_title"] = response.meta.get("list_title", "")
            parsed["list_author"] = response.meta.get("list_author", "")
            parsed["list_comments"] = response.meta.get("list_comments", "")
            parsed["hot_rank"] = response.meta.get("rank", "")
            parsed["hot_value"] = response.meta.get("hot_value", "")
            self._save_to_sqlite(parsed)
            yield parsed

    def _parse_article_detail(self, response: Response) -> dict | None:
        title = response.css(
            "h1.article-title::text, h1::text, div.article-card-title::text"
        ).get("").strip()
        if not title:
            return None

        content_parts = response.css(
            "div.article-content, div.content, article"
        ).css("::text").getall()
        content = "\n".join(t.strip() for t in content_parts if t.strip())

        author = response.css(
            "a.name::text, span.source::text, div.author-info span::text"
        ).get("").strip()

        pub_date = response.css(
            "time::attr(datetime), span.time::text, em.time::text"
        ).get("").strip()

        comment_count = response.css(
            "span.comment-count::text, span.reply-num::text"
        ).get("").strip()

        return {
            "url": response.url,
            "title": title,
            "author": author,
            "pub_date": pub_date or datetime.now().strftime("%Y-%m-%d"),
            "content": content[:20000],
            "comment_count": comment_count,
            "category": "文章",
            "source": "toutiao.com",
            "data_type": "article",
            "scraped_at": datetime.now().isoformat(),
        }

    def _detect_page_type(self, url: str) -> str:
        if "hot-board" in url or "hot-event" in url:
            return "hot_board"
        if "/ch/" in url:
            return "channel"
        if "/article/" in url or "/group/" in url:
            return "article"
        return "index"

    def _save_to_sqlite(self, item: dict):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS toutiao_open (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT,
                title TEXT,
                author TEXT,
                pub_date TEXT,
                content TEXT,
                hot_value TEXT,
                rank TEXT,
                comment_count TEXT,
                category TEXT,
                source TEXT,
                list_title TEXT,
                list_author TEXT,
                list_comments TEXT,
                hot_rank TEXT,
                data_type TEXT,
                scraped_at TEXT,
                UNIQUE(url, data_type, title)
            )
        """)
        try:
            conn.execute(
                """INSERT OR IGNORE INTO toutiao_open
                (url, title, author, pub_date, content, hot_value, rank,
                 comment_count, category, source, list_title, list_author,
                 list_comments, hot_rank, data_type, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.get("url", ""), item.get("title", ""), item.get("author", ""),
                    item.get("pub_date", ""), item.get("content", ""),
                    item.get("hot_value", ""), item.get("rank", ""),
                    item.get("comment_count", ""), item.get("category", ""),
                    item.get("source", ""), item.get("list_title", ""),
                    item.get("list_author", ""), item.get("list_comments", ""),
                    item.get("hot_rank", ""), item.get("data_type", ""),
                    item.get("scraped_at", ""),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def on_close(self):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM toutiao_open ORDER BY id").fetchall()
        conn.close()
        output_path = os.path.join(OUTPUT_DIR, "toutiao_open.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump([dict(r) for r in rows], f, ensure_ascii=False, indent=2)
        self.logger.info(f"Exported {len(rows)} items to {output_path}")
