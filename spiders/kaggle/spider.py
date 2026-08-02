from scrapling.spiders import Spider, Response, Request
from scrapling.fetchers import FetcherSession, AsyncStealthySession
import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "kaggle.db")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")


class KaggleDatasetsSpider(Spider):
    name = "kaggle"
    start_urls = [
        "https://www.kaggle.com/datasets",
        "https://www.kaggle.com/search?q=flower+in%3Adatasets",
        "https://www.kaggle.com/search?q=china+in%3Adatasets",
        "https://www.kaggle.com/search?q=agriculture+in%3Adatasets",
    ]
    allowed_domains = {"kaggle.com"}
    concurrent_requests = 4
    download_delay = 2.0
    robots_txt_obey = True

    def configure_sessions(self, manager):
        manager.add("http", FetcherSession(impersonate="chrome"))
        manager.add("stealth", AsyncStealthySession(
            headless=True,
            solve_cloudflare=False,
        ), lazy=True)

    async def parse(self, response: Response):
        page_type = self._detect_page_type(response.url)

        if page_type == "search":
            for item in response.css("li[role='listitem'], div.dataset-item"):
                parsed = self._parse_dataset_card(item, response)
                if parsed:
                    self._save_to_sqlite(parsed)
                    yield parsed
                    detail_url = item.css("a::attr(href)").get("")
                    if detail_url and "/datasets/" in detail_url:
                        yield Request(
                            f"https://www.kaggle.com{detail_url}",
                            callback=self.parse_dataset_detail,
                            meta={"dataset_id": parsed["dataset_id"]}
                        )

            next_page = response.css("a[aria-label='Next Page']::attr(href), a.next::attr(href)").get()
            if next_page:
                yield response.follow(next_page, callback=self.parse)

        elif page_type == "datasets_list":
            for item in response.css("li[role='listitem'], div.dataset-item"):
                parsed = self._parse_dataset_card(item, response)
                if parsed:
                    self._save_to_sqlite(parsed)
                    yield parsed

            next_page = response.css("a[aria-label='Next Page']::attr(href)").get()
            if next_page:
                yield response.follow(next_page, callback=self.parse)

    async def parse_dataset_detail(self, response: Response):
        title = response.css("h1[data-testid='dataset-title']::text, h1::text").get("").strip()
        description = response.css("div[data-testid='dataset-description'], div.dataset-description").css("::text").getall()
        description_text = "\n".join(t.strip() for t in description if t.strip())

        metadata = {
            "url": response.url,
            "dataset_id": response.meta.get("dataset_id", ""),
            "title": title,
            "description": description_text[:10000],
            "author": response.css("a[data-testid='author-link']::text, span.author::text").get("").strip(),
            "license": response.css("span.license::text, a.license::text").get("").strip(),
            "size": response.css("span.size::text").get("").strip(),
            "usability_rating": response.css("span.usability::text").get("").strip(),
            "votes": response.css("span.votes::text").get("").strip(),
            "downloads": response.css("span.downloads::text").get("").strip(),
            "tags": response.css("a.tag::text, span.tag::text").getall(),
            "file_count": response.css("span.file-count::text").get("").strip(),
            "last_updated": response.css("time::attr(datetime), span.updated::text").get("").strip(),
            "data_type": "dataset_detail",
            "scraped_at": datetime.now().isoformat(),
        }

        self._save_to_sqlite(metadata)
        yield metadata

        for file_row in response.css("table.files tr, div.file-item"):
            file_info = self._parse_file_info(file_row, response)
            if file_info:
                file_info["dataset_id"] = metadata["dataset_id"]
                self._save_to_sqlite(file_info)
                yield file_info

    def _parse_dataset_card(self, card, response):
        title = card.css("a[data-testid], h3::text, .title::text").get("").strip()
        if not title:
            return None

        link = card.css("a::attr(href)").get("")
        dataset_id = link.split("/")[-1] if link else ""

        return {
            "url": f"https://www.kaggle.com{link}" if link else response.url,
            "dataset_id": dataset_id,
            "title": title,
            "author": card.css("span.author::text, a.author::text").get("").strip(),
            "description": card.css("p.description::text, .desc::text").get("").strip()[:500],
            "tags": card.css("span.tag::text, a.tag::text").getall(),
            "votes": card.css("span.votes::text").get("").strip(),
            "usability": card.css("span.usability::text").get("").strip(),
            "size": card.css("span.size::text").get("").strip(),
            "last_updated": card.css("time::attr(datetime), span.updated::text").get("").strip(),
            "data_type": "dataset_card",
            "scraped_at": datetime.now().isoformat(),
        }

    def _parse_file_info(self, row, response):
        cells = row.css("td::text").getall()
        if len(cells) < 2:
            return None

        return {
            "file_name": cells[0].strip() if len(cells) > 0 else "",
            "file_size": cells[1].strip() if len(cells) > 1 else "",
            "file_type": cells[2].strip() if len(cells) > 2 else "",
            "download_url": row.css("a::attr(href)").get(""),
            "data_type": "file_info",
            "scraped_at": datetime.now().isoformat(),
        }

    def _detect_page_type(self, url: str) -> str:
        if "/search" in url:
            return "search"
        if "/datasets" in url:
            return "datasets_list"
        return "detail"

    def _save_to_sqlite(self, item: dict):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kaggle_datasets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT,
                dataset_id TEXT,
                title TEXT,
                author TEXT,
                description TEXT,
                tags TEXT,
                votes TEXT,
                usability TEXT,
                usability_rating TEXT,
                size TEXT,
                downloads TEXT,
                license TEXT,
                file_count TEXT,
                last_updated TEXT,
                file_name TEXT,
                file_size TEXT,
                file_type TEXT,
                download_url TEXT,
                data_type TEXT,
                scraped_at TEXT,
                UNIQUE(url, dataset_id, data_type, file_name)
            )
        """)
        try:
            tags_json = json.dumps(item.get("tags", [])) if item.get("tags") else "[]"

            if item.get("data_type") == "dataset_card":
                conn.execute(
                    """INSERT OR IGNORE INTO kaggle_datasets
                    (url, dataset_id, title, author, description, tags, votes, usability, size, last_updated, data_type, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["url"], item["dataset_id"], item["title"], item["author"],
                     item["description"], tags_json, item["votes"], item["usability"],
                     item["size"], item["last_updated"], item["data_type"], item["scraped_at"])
                )
            elif item.get("data_type") == "dataset_detail":
                conn.execute(
                    """INSERT OR IGNORE INTO kaggle_datasets
                    (url, dataset_id, title, author, description, tags, license, size, usability_rating, votes, downloads, file_count, last_updated, data_type, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["url"], item["dataset_id"], item["title"], item["author"],
                     item["description"], tags_json, item["license"], item["size"],
                     item["usability_rating"], item["votes"], item["downloads"],
                     item["file_count"], item["last_updated"], item["data_type"], item["scraped_at"])
                )
            elif item.get("data_type") == "file_info":
                conn.execute(
                    """INSERT OR IGNORE INTO kaggle_datasets
                    (dataset_id, file_name, file_size, file_type, download_url, data_type, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (item["dataset_id"], item["file_name"], item["file_size"],
                     item["file_type"], item["download_url"], item["data_type"], item["scraped_at"])
                )
            conn.commit()
        finally:
            conn.close()

    async def on_close(self):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM kaggle_datasets ORDER BY id").fetchall()
        conn.close()
        output_path = os.path.join(OUTPUT_DIR, "kaggle_datasets.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump([dict(r) for r in rows], f, ensure_ascii=False, indent=2)
        self.logger.info(f"Exported {len(rows)} items to {output_path}")
