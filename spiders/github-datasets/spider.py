from scrapling.spiders import Spider, Response, Request
from scrapling.fetchers import FetcherSession
import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "github_datasets.db")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")


class GithubDatasetsSpider(Spider):
    name = "github_datasets"
    start_urls = [
        "https://github.com/awesome-china/data",
        "https://github.com/awesome-china",
        "https://github.com/topics/china-data",
        "https://github.com/topics/chinese-data",
        "https://github.com/topics/china-dataset",
    ]
    allowed_domains = {"github.com"}
    concurrent_requests = 4
    download_delay = 1.5
    robots_txt_obey = True

    def configure_sessions(self, manager):
        manager.add("default", FetcherSession(impersonate="chrome"))

    async def parse(self, response: Response):
        page_type = self._detect_page_type(response.url)

        if page_type == "readme":
            for link in response.css("article a::attr(href)").getall():
                if link.startswith("http") and "github.com" in link:
                    yield Request(link, callback=self.parse_repo, meta={"source": "awesome_list"})

        elif page_type == "repo":
            parsed = self._parse_repo_page(response)
            if parsed:
                self._save_to_sqlite(parsed)
                yield parsed

            readme_link = response.css("a[href*='/blob/main/README'], a[href*='/blob/master/README']::attr(href)").get()
            if readme_link:
                yield response.follow(readme_link, callback=self.parse_readme,
                                      meta={"repo_url": response.url})

        elif page_type == "topic":
            for repo_card in response.css("article.Box-row, div.col-12"):
                repo_url = repo_card.css("a::attr(href)").get("")
                if repo_url and repo_url.count("/") >= 2:
                    full_url = f"https://github.com{repo_url}" if repo_url.startswith("/") else repo_url
                    yield Request(full_url, callback=self.parse_repo, meta={"source": "topic"})

            next_page = response.css("a.next_page::attr(href), a[aria-label='Next']::attr(href)").get()
            if next_page:
                yield response.follow(next_page, callback=self.parse)

        elif page_type == "readme_file":
            parsed = self._parse_readme_content(response)
            if parsed:
                self._save_to_sqlite(parsed)
                yield parsed

    async def parse_repo(self, response: Response):
        parsed = self._parse_repo_page(response)
        if parsed:
            self._save_to_sqlite(parsed)
            yield parsed

    async def parse_readme(self, response: Response):
        parsed = self._parse_readme_content(response)
        if parsed:
            parsed["repo_url"] = response.meta.get("repo_url", "")
            self._save_to_sqlite(parsed)
            yield parsed

    def _parse_repo_page(self, response):
        title = response.css("strong[itemprop='name'] a::text, h1.lh-condensed::text").get("").strip()
        if not title:
            return None

        return {
            "url": response.url,
            "repo_name": title,
            "owner": response.url.split("/")[3] if len(response.url.split("/")) > 3 else "",
            "description": response.css("p.f4::text, p.about-text::text").get("").strip(),
            "stars": response.css("a[href$='/stargazers']::text, #repo-stars-counter-star::text").get("").strip(),
            "forks": response.css("a[href$='/forks']::text, #repo-network-counter::text").get("").strip(),
            "language": response.css("span[itemprop='programmingLanguage']::text").get("").strip(),
            "topics": response.css("a.topic-tag::text").getall(),
            "license": response.css("span.Label::text, a[href*='/blob/main/LICENSE']::text").get("").strip(),
            "last_commit": response.css("relative-time::attr(datetime), span.text-small time::attr(datetime)").get("").strip(),
            "data_type": "repo",
            "scraped_at": datetime.now().isoformat(),
        }

    def _parse_readme_content(self, response):
        content_parts = response.css("article markdown-engine, article").css("::text").getall()
        content = "\n".join(t.strip() for t in content_parts if t.strip())

        links = response.css("article a::attr(href)").getall()
        data_links = [l for l in links if any(ext in l.lower() for ext in [".csv", ".xlsx", ".json", ".zip", "download"])]

        return {
            "url": response.url,
            "content": content[:10000],
            "data_links": data_links,
            "data_type": "readme",
            "scraped_at": datetime.now().isoformat(),
        }

    def _detect_page_type(self, url: str) -> str:
        if "/topics/" in url:
            return "topic"
        if url.count("/") == 3 and not url.endswith("/blob/") and not url.endswith("/tree/"):
            parts = url.split("/")
            if len(parts) >= 5 and parts[3] and parts[4]:
                return "repo"
        if "/blob/" in url or "/tree/" in url:
            return "readme_file"
        if url.rstrip("/").endswith("/awesome-china/data") or url.rstrip("/").endswith("/awesome-china"):
            return "readme"
        return "repo"

    def _save_to_sqlite(self, item: dict):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS github_datasets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT,
                repo_name TEXT,
                owner TEXT,
                description TEXT,
                stars TEXT,
                forks TEXT,
                language TEXT,
                topics TEXT,
                license TEXT,
                last_commit TEXT,
                content TEXT,
                data_links TEXT,
                repo_url TEXT,
                data_type TEXT,
                scraped_at TEXT,
                UNIQUE(url, data_type)
            )
        """)
        try:
            topics_json = json.dumps(item.get("topics", [])) if item.get("topics") else "[]"
            data_links_json = json.dumps(item.get("data_links", [])) if item.get("data_links") else "[]"

            if item.get("data_type") == "repo":
                conn.execute(
                    """INSERT OR IGNORE INTO github_datasets
                    (url, repo_name, owner, description, stars, forks, language, topics, license, last_commit, data_type, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["url"], item["repo_name"], item["owner"], item["description"],
                     item["stars"], item["forks"], item["language"], topics_json,
                     item["license"], item["last_commit"], item["data_type"], item["scraped_at"])
                )
            elif item.get("data_type") == "readme":
                conn.execute(
                    """INSERT OR IGNORE INTO github_datasets
                    (url, content, data_links, repo_url, data_type, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (item["url"], item["content"], data_links_json,
                     item.get("repo_url", ""), item["data_type"], item["scraped_at"])
                )
            conn.commit()
        finally:
            conn.close()

    async def on_close(self):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM github_datasets ORDER BY id").fetchall()
        conn.close()
        output_path = os.path.join(OUTPUT_DIR, "github_datasets.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump([dict(r) for r in rows], f, ensure_ascii=False, indent=2)
        self.logger.info(f"Exported {len(rows)} items to {output_path}")
