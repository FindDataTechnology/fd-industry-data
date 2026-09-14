from scrapling.spiders import Spider, Response, Request
from scrapling.fetchers import FetcherSession
import sqlite3
import json
import os
from datetime import datetime


DB_PATH = os.path.join(os.path.dirname(__file__), "data", "cisa_data.db")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")


class CISASpider(Spider):
    name = "cisa"
    
    # CISA main pages
    START_URLS = [
        "https://www.cisa.org.cn/",  # Homepage
        "https://www.cisa.org.cn/production/",  # Production statistics
        "https://www.cisa.org.cn/trade/",  # Import/export data
        "https://www.cisa.org.cn/price/",  # Price indices
        "https://www.cisa.org.cn/analysis/",  # Industry analysis
    ]
    
    # Detailed category URLs
    CATEGORY_URLS = {
        "production": [
            "https://www.cisa.org.cn/production/output/",  # Production output
            "https://www.cisa.org.cn/production/efficiency/",  # Production efficiency
            "https://www.cisa.org.cn/production/capacity/",  # Capacity utilization
        ],
        "trade": [
            "https://www.cisa.org.cn/trade/import/",  # Import data
            "https://www.cisa.org.cn/trade/export/",  # Export data
            "https://www.cisa.org.cn/trade/balance/",  # Trade balance
        ],
        "price": [
            "https://www.cisa.org.cn/price/index/",  # Price indices
            "https://www.cisa.org.cn/price/regional/",  # Regional prices
            "https://www.cisa.org.cn/price/variety/",  # Product variety prices
        ],
        "analysis": [
            "https://www.cisa.org.cn/analysis/reports/",  # Analysis reports
            "https://www.cisa.org.cn/analysis/market/",  # Market analysis
            "https://www.cisa.org.cn/analysis/outlook/",  # Market outlook
        ],
    }
    
    concurrent_requests = 2
    download_delay = 3.0
    max_retries = 3
    
    robots_txt_obey = True
    timeout = 30
    
    def configure_sessions(self, manager):
        manager.add("default", FetcherSession(impersonate="chrome"))
    
    async def parse(self, response: Response):
        """Main parser for CISA pages"""
        self.logger.info(f"Parsing CISA page: {response.url}")
        
        if "/production/" in response.url:
            await self._parse_production_data(response)
        elif "/trade/" in response.url:
            await self._parse_trade_data(response)
        elif "/price/" in response.url:
            await self._parse_price_data(response)
        elif "/analysis/" in response.url:
            await self._parse_analysis_data(response)
        else:
            await self._parse_main_page(response)
    
    async def _parse_main_page(self, response: Response):
        """Parse CISA homepage"""
        # Extract all relevant links
        for link in response.css("a"):
            href = link.attrib.get("href", "")
            
            if href and isinstance(href, str):
                # Handle relative URLs
                if href.startswith("/"):
                    full_url = "https://www.cisa.org.cn" + href
                elif not href.startswith("http"):
                    continue
                else:
                    full_url = href
                
                # Check if it's a relevant data page
                if any(cat in href for cat in ["/production/", "/trade/", "/price/", "/analysis/"]):
                    yield Response(full_url, callback=self.parse)
                
                # Look for navigation menus
                nav_links = response.css("a.nav-link, .menu-item a, a.btn")
                for nlink in nav_links:
                    nhref = nlink.attrib.get("href", "")
                    if nhref and isinstance(nhref, str):
                        if nhref.startswith("/"):
                            yield Response("https://www.cisa.org.cn" + nhref, callback=self.parse)
        
        # Extract any table data from main page
        tables = response.css("table")
        for table in tables[:5]:
            await self._try_extract_table_data(table, response)
    
    async def _parse_production_data(self, response: Response):
        """Parse production statistics data"""
        self.logger.info("Extracting production statistics")
        
        # Extract production data from tables
        tables = response.css("table", ".Table1", "table[id^='grid']")
        
        for table in tables:
            rows = table.css("tr")
            if len(rows) < 2:
                continue
            
            # Extract headers
            headers = []
            for cell in rows[0].css("th, td"):
                text = cell.css("::text").get("").strip()
                if text:
                    headers.append(text)
            
            if not headers:
                continue
            
            # Parse each row
            for row in rows[1:]:
                cells = row.css("td")
                if len(cells) >= 4:
                    item = {}
                    
                    for i, header in enumerate(headers[:len(cells)]):
                        value = cells[i].css("::text").get("").strip()
                        
                        if value == "-" or value == "":
                            value = None
                        elif value:
                            # Try to convert numeric values
                            try:
                                if "." in value:
                                    value = float(value.replace(",", ""))
                                elif value.isdigit():
                                    value = int(value)
                            except:
                                pass
                        
                        key = header.lower().replace(" ", "_").replace("/", "_")
                        item[key] = value
                    
                    if item:
                        item["scraped_at"] = datetime.now().isoformat()
                        item["source_url"] = response.url
                        item["category"] = "production"
                        
                        self._save_to_sqlite(item)
                        yield item
        
        # Pagination
        next_pages = response.css("a:contains('下一页'), a.page-next, a[onclick*='page']")
        for link in next_pages:
            onclick = link.attrib.get("onclick", "")
            href = link.attrib.get("href", "")
            
            if href and "javascript" not in href.lower():
                yield Response(href, callback=self.parse)
            elif "javascript" in onclick.lower():
                # Try to extract URL from onclick
                import re
                urls = re.findall(r"'([^']+)'", onclick)
                for url in urls:
                    if url.startswith("/"):
                        yield Response("https://www.cisa.org.cn" + url, callback=self.parse)
    
    async def _parse_trade_data(self, response: Response):
        """Parse import/export trade data"""
        self.logger.info("Extracting trade data")
        
        tables = response.css("table")
        for table in tables:
            rows = table.css("tr")
            if len(rows) < 2:
                continue
            
            headers = []
            for cell in rows[0].css("th, td"):
                text = cell.css("::text").get("").strip()
                if text:
                    headers.append(text)
            
            if not headers:
                continue
            
            for row in rows[1:]:
                cells = row.css("td")
                if len(cells) >= 4:
                    item = {}
                    
                    for i, header in enumerate(headers[:len(cells)]):
                        value = cells[i].css("::text").get("").strip()
                        
                        if value == "-" or value == "":
                            value = None
                        elif value:
                            try:
                                if "." in value:
                                    value = float(value.replace(",", ""))
                                elif value.isdigit():
                                    value = int(value)
                            except:
                                pass
                        
                        key = header.lower().replace(" ", "_").replace("/", "_")
                        item[key] = value
                    
                    if item:
                        item["scraped_at"] = datetime.now().isoformat()
                        item["source_url"] = response.url
                        item["category"] = "trade"
                        
                        self._save_to_sqlite(item)
                        yield item
    
    async def _parse_price_data(self, response: Response):
        """Parse price indices and data"""
        self.logger.info("Extracting price data")
        
        tables = response.css("table")
        for table in tables:
            rows = table.css("tr")
            if len(rows) < 2:
                continue
            
            headers = []
            for cell in rows[0].css("th, td"):
                text = cell.css("::text").get("").strip()
                if text:
                    headers.append(text)
            
            if not headers:
                continue
            
            for row in rows[1:]:
                cells = row.css("td")
                if len(cells) >= 4:
                    item = {}
                    
                    for i, header in enumerate(headers[:len(cells)]):
                        value = cells[i].css("::text").get("").strip()
                        
                        if value == "-" or value == "":
                            value = None
                        elif value:
                            try:
                                if "." in value:
                                    value = float(value.replace(",", ""))
                                elif value.isdigit():
                                    value = int(value)
                            except:
                                pass
                        
                        key = header.lower().replace(" ", "_").replace("/", "_")
                        item[key] = value
                    
                    if item:
                        item["scraped_at"] = datetime.now().isoformat()
                        item["source_url"] = response.url
                        item["category"] = "price"
                        
                        self._save_to_sqlite(item)
                        yield item
    
    async def _parse_analysis_data(self, response: Response):
        """Parse analysis reports and market outlook"""
        self.logger.info("Extracting analysis reports")
        
        # Extract news articles
        articles = response.css("ul.news-list li, ul.list-row li, div.article-item")
        
        for article in articles:
            title = article.css("a::text").get("").strip()
            date = article.css("span.date, span.time::text").get("").strip()
            link = article.css("a::attr(href)").get("")
            
            if title:
                full_url = response.urljoin(link) if link else response.url
                
                item = {
                    "title": title,
                    "url": full_url,
                    "publish_date": date,
                    "type": "analysis_report",
                    "scraped_at": datetime.now().isoformat(),
                    "source_url": response.url,
                    "category": "analysis",
                }
                
                self._save_to_sqlite(item)
                yield item
                
                # Optionally crawl individual articles
                yield Response(full_url, callback=self._parse_analysis_detail, meta={"item": item})
    
    async def _parse_analysis_detail(self, response: Response):
        """Parse detailed analysis article"""
        item = response.meta.get("item", {})
        
        content = response.css("div.article-content, div.content, div.TRS_Editor").css("::text").getall()
        
        item["content"] = "\n".join(c.strip() for c in content if c.strip())
        
        self._save_to_sqlite(item)
        yield item
    
    async def _try_extract_table_data(self, table, response):
        """Try to extract structured table data"""
        rows = table.css("tr")
        if len(rows) < 2:
            return
        
        for row in rows[1:]:
            cells = row.css("td")
            if len(cells) >= 3:
                item = {}
                
                for i, cell in enumerate(cells):
                    value = cell.css("::text").get("").strip()
                    
                    if value:
                        try:
                            value = float(value.replace(",", ""))
                        except:
                            pass
                    
                    item[f"col_{i}"] = value
                
                if item:
                    item["scraped_at"] = datetime.now().isoformat()
                    item["source_url"] = response.url
                    item["category"] = "general"
                    
                    self._save_to_sqlite(item)
                    yield item
    
    def _save_to_sqlite(self, item: dict):
        """Save item to SQLite database"""
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        
        conn = sqlite3.connect(DB_PATH)
        
        try:
            category = item.get("category", "unknown")
            category_safe = category.replace("-", "_")
            
            # Create table
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {category_safe} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scraped_at TEXT,
                    source_url TEXT,
                    category TEXT,
                    title TEXT,
                    url TEXT,
                    publish_date TEXT,
                    content TEXT,
                    other_data JSONB
                )
            """)
            
            # Build columns dynamically
            columns = ["id", "scraped_at", "source_url", "category"]
            json_columns = ["other_data"]
            
            extra_columns = set(item.keys()) - set(columns + json_columns) - {"id"}
            for col in sorted(extra_columns):
                if col not in columns:
                    columns.append(col)
            
            placeholders = ",".join(["?" for _ in columns])
            cols = ",".join(columns)
            values = [item.get(col) for col in columns]
            
            # Store non-standard columns in JSON
            other_data = {k: v for k, v in item.items() if k not in columns}
            if other_data:
                values[-1] = json.dumps(other_data, ensure_ascii=False)
            else:
                values[-1] = json.dumps({}, ensure_ascii=False)
            
            conn.execute(f"INSERT INTO {category_safe} ({cols}) VALUES ({placeholders})", values)
            conn.commit()
            
        except Exception as e:
            self.logger.error(f"Error saving CISA item: {e}")
        finally:
            conn.close()
    
    async def on_close(self):
        """Export all data to JSON on close"""
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        categories = ["production", "trade", "price", "analysis", "general"]
        
        for category in categories:
            category_safe = category.replace("-", "_")
            output_path = os.path.join(OUTPUT_DIR, f"{category}.json")
            
            try:
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(f"SELECT * FROM {category_safe} ORDER BY id").fetchall()
                conn.close()
                
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump([dict(r) for r in rows], f, ensure_ascii=False, indent=2)
                
                self.logger.info(f"Exported {len(rows)} items from {category} to {output_path}")
            except Exception as e:
                self.logger.error(f"Failed to export {category}: {e}")


def run_cisa(limit: int = 100) -> list[dict]:
    """Fetch data from CISA (China Iron & Steel Association).

    Args:
        limit: Maximum records to return (default: 100)

    Returns:
        List of scraped records as dicts.
    """
    spider = CISASpider()
    results = []

    # Collect all items, respecting limit
    async def collect_items():
        collected = 0
        async for item in spider.parse_start_response(None):
            if collected >= limit:
                break
            results.append(item)
            collected += 1

        # Also try parsing category URLs
        for category, urls in spider.CATEGORY_URLS.items():
            if collected >= limit:
                break
            for url in urls[:3]:  # Limit URLs per category
                try:
                    response = await spider.request(url)
                    async for item in spider._parse_production_data(response) if category == "production" else \
                                spider._parse_trade_data(response) if category == "trade" else \
                                spider._parse_price_data(response) if category == "price" else \
                                spider._parse_analysis_data(response):
                        if collected >= limit:
                            break
                        results.append(item)
                        collected += 1
                except Exception:
                    pass

    # Run async collection
    import asyncio
    asyncio.run(collect_items())

    print(f"\n{'='*50}")
    print(f"CISA Data Fetch Complete")
    print(f"{'='*50}")
    print(f"Records fetched: {len(results)}")
    print(f"Output directory: {OUTPUT_DIR}")
    print(f"{'='*50}")

    return results


if __name__ == "__main__":
    import asyncio
    result = asyncio.run(run_spider())
