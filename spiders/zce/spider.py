from scrapling.spiders import Spider, Response, Request
from scrapling.fetchers import FetcherSession
import sqlite3
import json
import os
from datetime import datetime


DB_PATH = os.path.join(os.path.dirname(__file__), "data", "zce_prices.db")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")


class ZCESpider(Spider):
    name = "zce"
    
    # ZCE main data pages
    START_URLS = [
        "https://www.czce.com.cn/zh/cn/ssps/index.html",  # Spot prices
        "https://www.czce.com.cn/zh/cn/yhqh/index.html",  # Futures info
        "https://www.czce.com.cn/zh/cn/hsqk/index.html",  # Market overview
        "https://www.czce.com.cn/zh/cn/zcjj/index.html",  # Economic indicators
    ]
    
    # Product categories with URLs
    CATEGORY_URLS = {
        "agricultural": [
            "https://www.czce.com.cn/zh/cn/yhqh/mjg/index.html",     # Cotton
            "https://www.czce.com.cn/zh/cn/yhqh/tb/index.html",       # Sugar
            "https://www.czce.com.cn/zh/cn/yhqh/pg/index.html",       # PTA
            "https://www.czce.com.cn/zh/cn/yhqh/mp/index.html",       # Methanol
            "https://www.czce.com.cn/zh/cn/yhqh/pm/index.html",       # PP
            "https://www.czce.com.cn/zh/cn/yhqh/apple/index.html",    # Apple futures
            "https://www.czce.com.cn/zh/cn/yhqh/juju/index.html",     # Jujube dates
        ],
        "chemicals": [
            "https://www.czce.com.cn/zh/cn/yhqh/zty/index.html",      # Short fiber
            "https://www.czce.com.cn/zh/cn/yhqh/polyester/index.html", # Polyester chips
            "https://www.czce.com.cn/zh/cn/yhqh/ca/index.html",        # CA (Copolymer)
        ],
        "coal_coke": [
            "https://www.czce.com.cn/zh/cn/yhqh/mth/index.html",      # Methanol
            "https://www.czce.com.cn/zh/cn/yhqh/kx/index.html",        # Coke
            "https://www.czce.com.cn/zh/cn/yhqh/zw/index.html",        # Coal
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
        """Main parser for ZCE listing pages"""
        self.logger.info(f"Parsing ZCE page: {response.url}")
        
        if "/ssps/" in response.url:  # Spot prices
            await self._parse_spot_prices(response)
        elif "/yhqh/" in response.url:  # Futures
            await self._parse_futures_data(response)
        elif "/hsqk/" in response.url:  # Market overview
            await self._parse_market_overview(response)
        elif "/zcjj/" in response.url:  # Economic indicators
            await self._parse_economic_indicators(response)
        else:
            await self._parse_main_page(response)
    
    async def _parse_main_page(self, response: Response):
        """Parse ZCE homepage"""
        # Extract all links to product categories
        for link in response.css("a"):
            href = link.attrib.get("href", "")
            
            if href and isinstance(href, str):
                # Handle relative URLs
                if href.startswith("/"):
                    full_url = "https://www.czce.com.cn" + href
                elif not href.startswith("http"):
                    continue
                else:
                    full_url = href
                
                # Check if it's a relevant page
                if any(cat in href for cat in ["/ssps/", "/yhqh/", "/hsqk/", "/zcjj/"]):
                    yield Response(full_url, callback=self.parse)
                
                # Look for navigation menus and buttons
                nav_links = response.css("a.btn, a.nav-link, .menu-item a")
                for nlink in nav_links:
                    nhref = nlink.attrib.get("href", "")
                    if nhref and isinstance(nhref, str):
                        if nhref.startswith("/"):
                            yield Response("https://www.czce.com.cn" + nhref, callback=self.parse)
        
        # Extract table data from main page
        tables = response.css("table")
        for table in tables[:5]:
            await self._try_extract_table_data(table, response)
    
    async def _parse_spot_prices(self, response: Response):
        """Parse spot market prices"""
        commodity_type = self._identify_category(response.url)
        self.logger.info(f"Extracting spot prices for {commodity_type}")
        
        tables = response.css("table", ".Table1", "table[id^='grid']")
        
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
            
            # Parse each row
            for row in rows[1:]:
                cells = row.css("td")
                if len(cells) >= 4:
                    item = self._build_item_from_cells(cells, headers, response.url, commodity_type)
                    
                    if item:
                        item["scraped_at"] = datetime.now().isoformat()
                        item["source_url"] = response.url
                        item["price_type"] = "spot"
                        
                        self._save_to_sqlite(item)
                        yield item
        
        # Pagination
        next_pages = response.css("a[onclick*='page'], a:contains('下一页'), a.page-link")
        for link in next_pages:
            onclick = link.attrib.get("onclick", "")
            if "javascript" in onclick.lower():
                # Try to extract URL from onclick
                import re
                urls = re.findall(r"'([^']+)'", onclick)
                for url in urls:
                    if url.startswith("/"):
                        yield Response("https://www.czce.com.cn" + url, callback=self.parse)
    
    async def _parse_futures_data(self, response: Response):
        """Parse futures contract data"""
        commodity_type = self._identify_category(response.url)
        self.logger.info(f"Extracting futures data for {commodity_type}")
        
        tables = response.css("table")
        for table in tables:
            rows = table.css("tr")
            if len(rows) < 2:
                continue
            
            # Skip header row
            for row in rows[1:]:
                cells = row.css("td")
                if len(cells) >= 5:
                    item = self._build_item_from_cells(cells, [], response.url, commodity_type, is_futures=True)
                    
                    if item:
                        item["scraped_at"] = datetime.now().isoformat()
                        item["source_url"] = response.url
                        item["price_type"] = "futures"
                        
                        self._save_to_sqlite(item)
                        yield item
    
    async def _parse_market_overview(self, response: Response):
        """Parse market overview statistics"""
        overview_data = {}
        
        # Extract key statistics from the page
        stats_tables = response.css("table[width='100%']", ".statistics-table")
        for table in stats_tables:
            rows = table.css("tr")
            for row in rows:
                cells = row.css("td")
                if len(cells) >= 2:
                    label = cells[0].css("::text").get("").strip()
                    value = cells[1].css("::text").get("").strip()
                    if label and value:
                        overview_data[label] = value
        
        if overview_data:
            item = {
                "title": "Market Overview",
                "data": json.dumps(overview_data),
                "scraped_at": datetime.now().isoformat(),
                "source_url": response.url,
                "category": "market_overview",
            }
            
            self._save_to_sqlite(item)
            yield item
    
    async def _parse_economic_indicators(self, response: Response):
        """Parse economic indicator news and reports"""
        # Extract news articles about economic indicators
        articles = response.css("ul.news-list li, ul.list-row li")
        
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
                    "type": "economic_indicator",
                    "scraped_at": datetime.now().isoformat(),
                    "source_url": response.url,
                    "category": "economic_news",
                }
                
                self._save_to_sqlite(item)
                yield item
    
    async def _try_extract_table_data(self, table, response):
        """Try to extract structured table data"""
        rows = table.css("tr")
        if len(rows) < 2:
            return
        
        commodity_type = self._identify_category(response.url)
        
        for row in rows[1:]:
            cells = row.css("td")
            if len(cells) >= 3:
                item = {}
                
                for i, cell in enumerate(cells):
                    value = cell.css("::text").get("").strip()
                    
                    # Try to clean numeric values
                    if value:
                        try:
                            # Remove commas and convert
                            value = value.replace(",", "").replace("万", "*10000").replace("亿", "*100000000")
                            value = float(eval(value)) if "*" in value or "." in value else int(value)
                        except:
                            pass
                    
                    item[f"col_{i}"] = value
                
                if item:
                    item["scraped_at"] = datetime.now().isoformat()
                    item["source_url"] = response.url
                    item["category"] = commodity_type
                    
                    self._save_to_sqlite(item)
                    yield item
    
    def _identify_category(self, url: str) -> str:
        """Identify which category the URL belongs to"""
        if "/mjg/" in url.lower():  # 棉籽粕 / cottonseed meal
            return "agricultural"
        elif "/tb/" in url.lower():  # 糖 / sugar
            return "agricultural"
        elif "/pg/" in url.lower():  # PTA
            return "agricultural"
        elif "/mp/" in url.lower():  # Methanol
            return "chemicals"
        elif "/pm/" in url.lower():  # PP
            return "chemicals"
        elif "/apple/" in url.lower():  # Apple futures
            return "agricultural"
        elif "/juju/" in url.lower():  # Jujube
            return "agricultural"
        elif "/zty/" in url.lower():  # 短纤 / short fiber
            return "chemicals"
        elif "/polyester/" in url.lower():  # 聚酯
            return "chemicals"
        elif "/ca/" in url.lower():  # CA
            return "chemicals"
        elif "/mth/" in url.lower():  # 甲醇 / methanol
            return "coal_coke"
        elif "/kx/" in url.lower():  # 焦炭 / coke
            return "coal_coke"
        elif "/zw/" in url.lower():  # 动力煤 / coal
            return "coal_coke"
        else:
            return "general"
    
    def _build_item_from_cells(self, cells, headers, source_url, category, is_futures=False):
        """Build item dict from table cells"""
        item = {}
        
        # If headers provided, use them
        if headers:
            for i, header in enumerate(headers[:len(cells)]):
                value = cells[i].css("::text").get("").strip()
                
                if value == "-" or value == "":
                    value = None
                elif value:
                    # Clean up Chinese units
                    if "万" in value or "亿" in value:
                        try:
                            value = float(eval(value.replace("万", "*10000").replace("亿", "*100000000")))
                        except:
                            pass
                    else:
                        try:
                            value = float(value.replace(",", ""))
                        except:
                            pass
                
                key = header.lower().replace(" ", "_").replace("/", "_")
                item[key] = value
        else:
            # No headers - auto-generate keys
            for i, cell in enumerate(cells):
                value = cell.css("::text").get("").strip()
                
                if value:
                    try:
                        value = float(value.replace(",", ""))
                    except:
                        pass
                
                key = f"col_{i}"
                item[key] = value
        
        # Add common fields
        item["category"] = category
        item["is_futures"] = is_futures
        
        return item
    
    def _save_to_sqlite(self, item: dict):
        """Save item to SQLite database"""
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        
        conn = sqlite3.connect(DB_PATH)
        
        try:
            category = item.get("category", "unknown")
            category_safe = category.replace("-", "_")
            price_type = item.get("price_type", "regular")
            
            # Create table based on price type
            table_name = f"{category_safe}_{price_type}" if price_type != "regular" else category_safe
            
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scraped_at TEXT,
                    source_url TEXT,
                    category TEXT,
                    price_type TEXT,
                    contract_name TEXT,
                    opening REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    settlement REAL,
                    change TEXT,
                    volume REAL,
                    open_interest REAL,
                    other_data JSONB
                )
            """)
            
            # Build columns dynamically
            columns = ["id", "scraped_at", "source_url", "category", "price_type"]
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
            
            conn.execute(f"INSERT INTO {table_name} ({cols}) VALUES ({placeholders})", values)
            conn.commit()
            
        except Exception as e:
            self.logger.error(f"Error saving ZCE item: {e}")
        finally:
            conn.close()
    
    async def on_close(self):
        """Export all data to JSON on close"""
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        categories = ["agricultural", "chemicals", "coal_coke", "general", "market_overview", "economic_news"]
        
        for category in categories:
            category_safe = category.replace("-", "_")
            
            # Check different price type variants
            output_paths = [
                os.path.join(OUTPUT_DIR, f"{category}.json"),
                os.path.join(OUTPUT_DIR, f"{category}_spot.json"),
                os.path.join(OUTPUT_DIR, f"{category}_futures.json"),
            ]
            
            for output_path in output_paths:
                table_name = output_path.split("/")[-1].replace(".json", "")
                
                try:
                    conn = sqlite3.connect(DB_PATH)
                    conn.row_factory = sqlite3.Row
                    
                    # Try multiple table names
                    possible_tables = [table_name, category_safe]
                    rows = []
                    
                    for table in possible_tables:
                        try:
                            rows.extend(conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall())
                        except:
                            pass
                    
                    conn.close()
                    
                    if rows:
                        with open(output_path, "w", encoding="utf-8") as f:
                            json.dump([dict(r) for r in rows], f, ensure_ascii=False, indent=2)
                        
                        self.logger.info(f"Exported {len(rows)} items to {output_path}")
                        break
                
                except Exception as e:
                    self.logger.error(f"Failed to export {category}: {e}")


async def run_spider():
    """Helper function to run the spider"""
    spider = ZCESpider()
    result = spider.start()
    
    print(f"\n{'='*50}")
    print(f"ZCE Scraping Complete")
    print(f"{'='*50}")
    print(f"Items scraped: {result.stats.items_scraped}")
    print(f"Duration: {result.stats.elapsed_seconds:.1f}s")
    print(f"Output directory: {OUTPUT_DIR}")
    
    return result


if __name__ == "__main__":
    import asyncio
    result = asyncio.run(run_spider())
