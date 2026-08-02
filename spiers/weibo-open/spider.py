#!/usr/bin/env python3
"""
Weibo Open Platform Spider - 微博开放平台爬虫

Target: https://open.weibo.com/ / https://weibo.com/
Data: Trending topics, public posts, user influence metrics

AUTHENTICATION WARNING:
  Weibo Open Platform API requires developer registration and OAuth2.
  This spider ONLY accesses publicly available web pages.
  
  What this spider CAN access:
  - Weibo hot search / trending topics (public)
  - Public user profiles and posts
  - Public topic/hashtag pages
  
  What this spider CANNOT access (requires API key):
  - Full search API - needs Open Platform registration
  - User timeline API - needs OAuth2
  - Comments/reposts API - needs OAuth2
  - Direct messages - needs OAuth2
  
  To access API data:
  1. Register at https://open.weibo.com/
  2. Create an app and get App Key + App Secret
  3. Use OAuth2 for user authorization
  4. API docs: https://open.weibo.com/wiki/API

Rate Limiting: 2s delay, 3 concurrent
Anti-bot: Stealth session for protected pages
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
DB_PATH = os.path.join(BASE_DIR, "data", "weibo_open.db")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")


class WeiboOpenSpider(Spider):
    name = "weibo_open"
    start_urls = [
        "https://s.weibo.com/top/summary",
        "https://weibo.com/hot/search",
        "https://s.weibo.com/top/summary?cate=sc Socia",
    ]
    allowed_domains = {"weibo.com", "s.weibo.com", "open.weibo.com"}
    concurrent_requests = 3
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

        if page_type == "hot_search":
            for row in response.css("table tbody tr, div.hot-list li, div[data-type='hot']"):
                rank = row.css("td:first-child::text, span.rank::text").get("").strip()
                topic = row.css("td a, a.topic, a.word").css("::text").get("").strip()
                href = row.css("a::attr(href)").get("")
                hot_value = row.css("td:last-child::text, span.hot-value::text").get("").strip()

                if not topic:
                    continue

                if href and not href.startswith("http"):
                    href = f"https://s.weibo.com{href}"

                item = {
                    "url": href or response.url,
                    "rank": rank,
                    "topic": topic,
                    "hot_value": hot_value,
                    "category": "热搜",
                    "data_type": "hot_search",
                    "scraped_at": datetime.now().isoformat(),
                }
                self._save_to_sqlite(item)
                yield item

                if href:
                    yield Request(
                        href,
                        callback=self.parse_topic,
                        meta={"rank": rank, "hot_value": hot_value},
                    )

        elif page_type == "topic":
            parsed = self._parse_topic_page(response)
            if parsed:
                parsed["topic_rank"] = response.meta.get("rank", "")
                parsed["topic_hot_value"] = response.meta.get("hot_value", "")
                self._save_to_sqlite(parsed)
                yield parsed

        elif page_type == "profile":
            parsed = self._parse_profile(response)
            if parsed:
                self._save_to_sqlite(parsed)
                yield parsed

            for post in response.css("div.card-wrap[action-type='feed_list_item']"):
                post_data = self._parse_weibo_post(post, response)
                if post_data:
                    self._save_to_sqlite(post_data)
                    yield post_data

    async def parse_topic(self, response: Response):
        parsed = self._parse_topic_page(response)
        if parsed:
            parsed["topic_rank"] = response.meta.get("rank", "")
            parsed["topic_hot_value"] = response.meta.get("hot_value", "")
            self._save_to_sqlite(parsed)
            yield parsed

    def _parse_topic_page(self, response: Response) -> dict | None:
        topic_title = response.css(
            "h1 span::text, div.main-title::text, div#pl_topic_topband h1::text"
        ).get("").strip()
        if not topic_title:
            return None

        description = response.css(
            "div.topic-descr::text, p.description::text"
        ).get("").strip()

        post_count = response.css(
            "span[node-type='follow'] span::text, span.count::text"
        ).get("").strip()

        content_parts = response.css(
            "div.card-wrap div.txt, div.weibo-text"
        ).css("::text").getall()
        content = "\n".join(t.strip() for t in content_parts if t.strip())

        return {
            "url": response.url,
            "topic": topic_title,
            "description": description,
            "post_count": post_count,
            "content": content[:10000],
            "category": "话题",
            "data_type": "topic_page",
            "scraped_at": datetime.now().isoformat(),
        }

    def _parse_profile(self, response: Response) -> dict | None:
        username = response.css(
            "h1.username::text, div.pf_username span::text"
        ).get("").strip()
        if not username:
            return None

        followers = response.css(
            "span[node-type='follow'] strong::text, div.pf_atten span::text"
        ).get("").strip()
        following = response.css(
            "span[node-type='fans'] strong::text, div.pf_fans span::text"
        ).get("").strip()
        posts_count = response.css(
            "span[node-type='weibo'] strong::text, div.pf_weibo span::text"
        ).get("").strip()

        return {
            "url": response.url,
            "username": username,
            "followers": followers,
            "following": following,
            "posts_count": posts_count,
            "category": "用户",
            "data_type": "profile",
            "scraped_at": datetime.now().isoformat(),
        }

    def _parse_weibo_post(self, post_el, response) -> dict | None:
        text = post_el.css("div.txt::text, p.txt::text").get("").strip()
        if not text:
            return None

        author = post_el.css(
            "a.name::text, div.info span a::text"
        ).get("").strip()

        pub_time = post_el.css(
            "a.from:first-child::text, span.from a::text"
        ).get("").strip()

        reposts = post_el.css("span[action-type='feed_list_forward']::text").get("").strip()
        comments = post_el.css("span[action-type='feed_list_comment']::text").get("").strip()
        likes = post_el.css("span[action-type='feed_list_like'] em::text, em.count::text").get("").strip()

        return {
            "url": response.url,
            "author": author,
            "content": text[:5000],
            "pub_time": pub_time,
            "reposts": reposts,
            "comments": comments,
            "likes": likes,
            "category": "微博",
            "data_type": "post",
            "scraped_at": datetime.now().isoformat(),
        }

    def _detect_page_type(self, url: str) -> str:
        if "top/summary" in url or "hot/search" in url:
            return "hot_search"
        if "/u/" in url or "/profile" in url:
            return "profile"
        if "topic" in url or "weibo?topic" in url:
            return "topic"
        return "hot_search"

    def _save_to_sqlite(self, item: dict):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS weibo_open (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT,
                topic TEXT,
                rank TEXT,
                hot_value TEXT,
                author TEXT,
                content TEXT,
                pub_time TEXT,
                description TEXT,
                post_count TEXT,
                followers TEXT,
                following TEXT,
                posts_count TEXT,
                username TEXT,
                reposts TEXT,
                comments TEXT,
                likes TEXT,
                category TEXT,
                data_type TEXT,
                topic_rank TEXT,
                topic_hot_value TEXT,
                scraped_at TEXT,
                UNIQUE(url, data_type, topic, content)
            )
        """)
        try:
            conn.execute(
                """INSERT OR IGNORE INTO weibo_open
                (url, topic, rank, hot_value, author, content, pub_time,
                 description, post_count, followers, following, posts_count,
                 username, reposts, comments, likes, category, data_type,
                 topic_rank, topic_hot_value, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.get("url", ""), item.get("topic", ""), item.get("rank", ""),
                    item.get("hot_value", ""), item.get("author", ""),
                    item.get("content", ""), item.get("pub_time", ""),
                    item.get("description", ""), item.get("post_count", ""),
                    item.get("followers", ""), item.get("following", ""),
                    item.get("posts_count", ""), item.get("username", ""),
                    item.get("reposts", ""), item.get("comments", ""),
                    item.get("likes", ""), item.get("category", ""),
                    item.get("data_type", ""), item.get("topic_rank", ""),
                    item.get("topic_hot_value", ""), item.get("scraped_at", ""),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def on_close(self):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM weibo_open ORDER BY id").fetchall()
        conn.close()
        output_path = os.path.join(OUTPUT_DIR, "weibo_open.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump([dict(r) for r in rows], f, ensure_ascii=False, indent=2)
        self.logger.info(f"Exported {len(rows)} items to {output_path}")
