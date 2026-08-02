from scrapling.spiders import Spider, Response, Request
from scrapling.fetchers import FetcherSession, AsyncStealthySession
import sqlite3
import json
import os
import sys
from datetime import datetime
import argparse

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "ucimlrepository.db")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

START_URLS = [
    "https://archive.ics.uci.edu/ml/index.php",
]
CUSTOM_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Edge/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/17.2",
]
NAME = "UCI ML Repository"

class UcimlrepositorySpider(Spider):
    name = "ucimlrepository"
    
    def configure_sessions(self, manager):
        """Configure browser sessions with stealth mode."""
        manager.add("http", FetcherSession(impersonate="chrome120"))
        manager.add("stealth", AsyncStealthySession(headless=True, solve_cloudflare=False), lazy=True)
    
    async def parse(self, response: Response):
        """Main parsing hook."""
        self.logger.info(f"Parsing: {response.url}")
        try:
            items = await self.extract_data(response)
            if items:
                items = items if isinstance(items, list) else [items]
                for item in items:
                    self._save_sqlite(item)
                    self._save_json(item)
                    yield item
                count = len(items) if isinstance(items, list) else 1
                self.logger.info(f"Extracted {count} items from {response.url}")
        except Exception as e:
            self.logger.error(f"Error on {response.url}: {e}", exc_info=True)
            self._save_sqlite({"error": str(e), "url": response.url})
    
    async def extract_data(self, html):
        """Extract data based on page type."""
        url = html.meta.get("url", "") if hasattr(html, "meta") else getattr(html, "url", "")
        
        if "github.com" in url:
            return await self._extract_github(html)
        elif "archive.ics.uci.edu" in url:
            return await self._extract_ucis(html)
        elif "data.fred.stlouisfed.org" in url:
            return await self._extract_fred(html)
        elif "polygon.io" in url:
            return await self._extract_polygon(html)
        return await self._generic(html)
    
    async def _extract_github(self, resp):
        """Extract GitHub repository info."""
        title = resp.css("h1::text").get("").strip()
        desc = resp.css(".repo-description::text, p::text").get("").strip()
        stars = resp.css("[aria-label]:text").re_first(r"(\d+) stars?") or ""
        forks = resp.css("[aria-label]:text").re_first(r"(\d+) forks?") or ""
        lang = resp.css(".article-language span::text").get("")
        
        if not title:
            return []
        
        return [{
            "title": title,
            "description": desc,
            "url": resp.url,
            "language": lang,
            "stars": stars,
            "forks": forks,
            "scraped_at": datetime.now().isoformat()
        }]
    
    async def _extract_ucis(self, resp):
        """Extract UCI dataset listings."""
        items = []
        rows = resp.css("table.items-table tr")
        
        for row in rows[:200]:
            name = row.css("a::text, td:nth-child(2)::text").get("").strip()
            link = row.css("a::attr(href)").get("")
            instances = row.css("td:nth-child(3)::text").get("").strip()
            
            if name and link:
                if not link.startswith("http"):
                    link = "https://archive.ics.uci.edu" + link
                
                items.append({
                    "name": name,
                    "description": "",
                    "url": link,
                    "type": "Machine Learning",
                    "instances": instances,
                    "scraped_at": datetime.now().isoformat()
                })
        
        return items
    
    async def _extract_fred(self, resp):
        """Extract FRED economic data."""
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        series_elems = resp.css("div.series-link a, table.results tbody tr a")
        
        items = []
        for elem in series_elems[:100]:
            title_el = elem.css("::text").get("").strip()
            link = elem.css("::attr(href)").get("")
            
            if title_el and link:
                if not link.startswith("http"):
                    link = "https://data.fred.stlouisfed.org" + link
                
                items.append({
                    "series_id": link.split("/")[-1] if "/" in link else "",
                    "title": title_el,
                    "description": desc,
                    "url": link,
                    "frequency": "Quarterly",
                    "scraped_at": datetime.now().isoformat()
                })
        
        return items if items else [{"title": title, "description": desc, "url": resp.url}]
    
    async def _extract_polygon(self, resp):
        """Extract Polygon.io API docs."""
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content), .hero-desc::text").get("")
        
        endpoints = resp.css("code.api-endpoint::text, div.method::text").re(r"(GET|POST|PUT|DELETE) .+")
        
        items = []
        for ep in endpoints[:50]:
            items.append({
                "product": "Stocks",
                "title": ep.strip(),
                "description": desc,
                "url": resp.url,
                "endpoint": ep.strip(),
                "scraped_at": datetime.now().isoformat()
            })
        
        return items if items else [{"product": "Market Data", "title": title, "description": desc, "url": resp.url}]
    
    async def _generic(self, resp):
        """Generic fallback extraction."""
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url}]
    
    def _save_sqlite(self, item):
        """Save to SQLite database."""
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        
        try:
            cols = [k for k in item.keys()]
            placeholders = ", ".join(["?" for _ in cols])
            
            create_sql = f"""CREATE TABLE IF NOT EXISTS ucm_datasets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                description TEXT,
                url TEXT UNIQUE,
                scraped_at TEXT
            )"""
            
            conn.execute(create_sql)
            
            insert_sql = f"INSERT OR IGNORE INTO ucm_datasets ({col_str}) VALUES ({placeholders})".format(
                col_str=", ".join(cols), 
                placeholders=placeholders
            )
            conn.execute(insert_sql, [item.get(c, "") for c in cols])
            conn.commit()
        except Exception as e:
            self.logger.error(f"SQLite error: {e}")
        finally:
            conn.close()
    
    def _save_json(self, item):
        """Append to JSONL file."""
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        path = os.path.join(OUTPUT_DIR, f"{name}_items.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    
    def run_spider(self, urls=None, save=True):
        """Run spider programmatically."""
        from scrapy.crawler import CrawlerRunner
        from twisted.internet import reactor
        
        runner = CrawlerRunner({})
        d = runner.crawl(self, urls=urls or START_URLS)
        
        def done(_):
            reactor.stop()
            if save:
                self._export()
        
        d.addBoth(done)
        reactor.run()
        return True
    
    def _export(self):
        """Export final results to consolidated JSON."""
        if not os.path.exists(DB_PATH):
            return
        
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM ucm_datasets")
        rows = cursor.fetchall()
        conn.close()
        
        out_path = os.path.join(OUTPUT_DIR, f"{name}_complete.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump([dict(r) for r in rows], f, indent=2, ensure_ascii=False)
        
        self.logger.info(f"Exported {len(rows)} records")
    
    async def on_close(self):
        self.logger.info("Spider completed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run spider")
    parser.add_argument("--urls", nargs="+", help="Custom URLs")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    
    args = parser.parse_args()
    
    spider_instance = UcimlrepositorySpider()
    spider_instance.logger.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    
    ok = spider_instance.run_spider(urls=args.urls, save=not args.dry_run)
    print("✓ Done!" if ok else "✗ Failed!")
    sys.exit(0 if ok else 1)
