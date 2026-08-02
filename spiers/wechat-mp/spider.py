#!/usr/bin/env python3
"""
WeChat Official Account Platform Spider - 微信公众号平台爬虫

Target: https://mp.weixin.qq.com/
Data: Public account articles, metrics, content analysis

AUTHENTICATION WARNING:
  WeChat Official Account Platform requires authentication for most data.
  This spider ONLY accesses publicly available article pages (shared article links).
  
  What this spider CAN access:
  - Public article pages shared via links (mp.weixin.qq.com/s/...)
  - Public profile pages for some accounts
  
  What this spider CANNOT access (requires login):
  - Account dashboard analytics
  - Private article metrics (views, likes, shares) - requires official account login
  - Follower data - requires official account login
  - Article management - requires official account login
  
  To access private data, you need:
  1. A registered WeChat Official Account
  2. Login credentials (scan QR code with WeChat app)
  3. Use the official API: https://developers.weixin.qq.com/doc/offiaccount/

Rate Limiting: 3s delay, 2 concurrent (aggressive anti-bot)
Anti-bot: Stealth session required (Cloudflare-like protection)
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
DB_PATH = os.path.join(BASE_DIR, "data", "wechat_mp.db")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

SEARCH_URLS = [
    "https://weixin.sogou.com/weixin?type=2&query=中国经济",
    "https://weixin.sogou.com/weixin?type=2&query=金融市场",
    "https://weixin.sogou.com/weixin?type=2&query=数据分析",
    "https://weixin.sogou.com/weixin?type=2&query=人工智能",
    "https://weixin.sogou.com/weixin?type=2&query=新能源",
]


class WeChatMPSpider(Spider):
    name = "wechat_mp"
    start_urls = SEARCH_URLS
    allowed_domains = {"weixin.sogou.com", "mp.weixin.qq.com"}
    concurrent_requests = 2
    download_delay = 3.0
    robots_txt_obey = True

    def configure_sessions(self, manager):
        manager.add("http", FetcherSession(impersonate="chrome"))
        manager.add("stealth", AsyncStealthySession(
            headless=True,
            solve_cloudflare=False,
            network_idle=True,
        ), lazy=True)

    async def parse(self, response: Response):
        if "weixin.sogou.com" in response.url:
            for article in response.css("div.txt-box, ul.news-list li"):
                title_el = article.css("h3 a, h4 a")
                title = title_el.css("::text").get("").strip() if title_el else ""
                href = title_el.css("::attr(href)").get("") if title_el else ""

                if not title or not href:
                    continue

                account = article.css("a.account::text, span.all-time-y2::text").get("").strip()
                summary = article.css("p.txt-info::text, div.txt p::text").get("").strip()
                pub_date = article.css("span.all-time-y2::text, span.s2::text").get("").strip()

                if href.startswith("/"):
                    href = f"https://weixin.sogou.com{href}"

                yield Request(
                    href,
                    callback=self.parse_article_redirect,
                    meta={
                        "search_title": title,
                        "account": account,
                        "summary": summary,
                        "search_date": pub_date,
                    },
                )

            next_page = response.css("a#sogou_next::attr(href), a.next::attr(href)").get()
            if next_page:
                yield response.follow(next_page, callback=self.parse)

        elif "mp.weixin.qq.com" in response.url:
            parsed = self._parse_wechat_article(response)
            if parsed:
                self._save_to_sqlite(parsed)
                yield parsed

    async def parse_article_redirect(self, response: Response):
        if "mp.weixin.qq.com" in response.url:
            parsed = self._parse_wechat_article(response)
            if parsed:
                parsed["search_account"] = response.meta.get("account", "")
                parsed["search_summary"] = response.meta.get("summary", "")
                parsed["search_date"] = response.meta.get("search_date", "")
                self._save_to_sqlite(parsed)
                yield parsed
        else:
            for link in response.css("a::attr(href)").getall():
                if "mp.weixin.qq.com" in link:
                    yield Request(
                        link,
                        callback=self.parse_article_redirect,
                        meta=response.meta,
                    )
                    break

    def _parse_wechat_article(self, response: Response) -> dict | None:
        title = response.css(
            "h1#activity-name::text, h1.rich_media_title::text"
        ).get("").strip()
        if not title:
            return None

        content_parts = response.css(
            "div#js_content, div.rich_media_content"
        ).css("::text").getall()
        content = "\n".join(t.strip() for t in content_parts if t.strip())

        author = response.css(
            "a#js_name::text, span.rich_media_meta_nickname::text, "
            "div#js_profile_qrcode span::text"
        ).get("").strip()

        pub_date = response.css(
            "em#publish_time::text, span.rich_media_meta_text::text, "
            "span#publish_time::text"
        ).get("").strip()

        return {
            "url": response.url,
            "title": title,
            "author": author,
            "account_name": author,
            "pub_date": pub_date or datetime.now().strftime("%Y-%m-%d"),
            "content": content[:30000],
            "content_length": len(content),
            "source": "mp.weixin.qq.com",
            "data_type": "wechat_article",
            "scraped_at": datetime.now().isoformat(),
        }

    def _save_to_sqlite(self, item: dict):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wechat_mp (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE,
                title TEXT,
                author TEXT,
                account_name TEXT,
                pub_date TEXT,
                content TEXT,
                content_length INTEGER,
                source TEXT,
                search_account TEXT,
                search_summary TEXT,
                search_date TEXT,
                data_type TEXT,
                scraped_at TEXT
            )
        """)
        try:
            conn.execute(
                """INSERT OR IGNORE INTO wechat_mp
                (url, title, author, account_name, pub_date, content, content_length,
                 source, search_account, search_summary, search_date, data_type, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item["url"], item["title"], item["author"], item["account_name"],
                    item["pub_date"], item["content"], item.get("content_length", 0),
                    item["source"], item.get("search_account", ""),
                    item.get("search_summary", ""), item.get("search_date", ""),
                    item["data_type"], item["scraped_at"],
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def on_close(self):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM wechat_mp ORDER BY id").fetchall()
        conn.close()
        output_path = os.path.join(OUTPUT_DIR, "wechat_mp.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump([dict(r) for r in rows], f, ensure_ascii=False, indent=2)
        self.logger.info(f"Exported {len(rows)} items to {output_path}")
