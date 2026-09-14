from scrapling.spiders import Spider, Response, Request
from scrapling.fetchers import FetcherSession, AsyncStealthySession
import sqlite3
import json
import os
import sys
from datetime import datetime
import argparse

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "mof.db")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

START_URLS = [
    "https://www.mof.gov.cn/",
]
CUSTOM_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Edge/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/17.2",
]
NAME = "mof"

class MofSpider(Spider):
    name = "mof"
    
    def configure_sessions(self, manager):
        manager.add("http", FetcherSession(impersonate="chrome120"))
        manager.add("stealth", AsyncStealthySession(headless=True, solve_cloudflare=False), lazy=True)
    
    async def parse(self, response: Response):
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
        url = html.meta.get("url", "") if hasattr(html, "meta") else getattr(html, "url", "")
        
        if "github.com" in url:
            return await self._extract_github(html)
        elif "archive.ics.uci.edu" in url:
            return await self._extract_ucis(html)
        elif "data.fred.stlouisfed.org" in url:
            return await self._extract_fred(html)
        elif "polygon.io" in url:
            return await self._extract_polygon(html)
        elif "eastmoney" in url:
            return await self._extract_eastmoney(html)
        elif "stats.gov.cn" in url:
            return await self._extract_nbs(html)
        elif "smm.cn" in url:
            return await self._extract_smm(html)
        elif "mysteel" in url:
            return await self._extract_mysteel(html)
        elif "trendforce" in url:
            return await self._extract_trendforce(html)
        elif "chinamonitor" in url:
            return await self._extract_chinamonitor(html)
        elif "ccidconsulting" in url:
            return await self._extract_ccid(html)
        elif "crugroup" in url:
            return await self._extract_crugroup(html)
        elif "fastmarkets" in url:
            return await self._extract_fastmarkets(html)
        elif "inmetals" in url:
            return await self._extract_inmetals(html)
        elif "intrinio" in url:
            return await self._extract_intrinio(html)
        elif "coinmarketcap" in url:
            return await self._extract_coinmarketcap(html)
        elif "kaggle" in url:
            return await self._extract_kaggle(html)
        elif "wanfangdata" in url:
            return await self._extract_wanfang(html)
        elif "oschina" in url:
            return await self._extract_oschina(html)
        elif "nhc.gov.cn" in url:
            return await self._extract_nhc(html)
        elif "mof.gov.cn" in url:
            return await self._extract_mof(html)
        elif "metal.com" in url:
            return await self._extract_metal(html)
        elif "kitco" in url:
            return await self._extract_kitco(html)
        elif "isee.gov.cn" in url:
            return await self._extract_isee(html)
        elif "hqcdc.org.cn" in url:
            return await self._extract_hqcdc(html)
        elif "gtarsc.com" in url:
            return await self._extract_gtarsc(html)
        elif "digitimes" in url:
            return await self._extract_digitimes(html)
        elif "data.gov" in url:
            return await self._extract_datagov(html)
        elif "cssn.cn" in url:
            return await self._extract_cssn(html)
        elif "cqvip.com" in url:
            return await self._extract_cqvip(html)
        elif "cnki.net" in url:
            return await self._extract_cnki(html)
        elif "cas.cn" in url:
            return await self._extract_cas(html)
        elif "cap.gov.cn" in url:
            return await self._extract_cap(html)
        elif "caict.ac.cn" in url:
            return await self._extract_caict(html)
        elif "bls.gov" in url:
            return await self._extract_bls(html)
        elif "tushare.pro" in url:
            return await self._extract_tushare(html)
        elif "apipoer.com" in url:
            return await self._extract_apipoer(html)
        elif "huggingface.co" in url:
            return await self._extract_huggingface(html)
        elif "flowers.yunnan.gov.cn" in url:
            return await self._extract_yunnan_flowers(html)
        elif "cisia.org.cn" in url:
            return await self._extract_cisia(html)
        elif "caa.org.cn" in url:
            return await self._extract_caa(html)
        elif "agri-info.cn" in url:
            return await self._extract_agri_info(html)
        elif "nongye.yn.gov.cn" in url:
            return await self._extract_yn_agri(html)
        elif "zce.com.cn" in url:
            return await self._extract_zce(html)
        elif "wind.com.cn" in url:
            return await self._extract_wind(html)
        elif "stat.gov.cn" in url:
            return await self._extract_stat_gov(html)
        elif "kunfloralexport.com" in url:
            return await self._extract_kunfloral(html)
        elif "toutiao.com" in url:
            return await self._extract_toutiao(html)
        elif "zhihu.com" in url:
            return await self._extract_zhihu(html)
        elif "weibo.com" in url:
            return await self._extract_weibo(html)
        elif "people.com.cn" in url:
            return await self._extract_people(html)
        elif "mp.weixin.qq.com" in url:
            return await self._extract_wechat(html)
        
        return await self._generic(html)
    
    async def _extract_github(self, resp):
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
    
    async def _extract_eastmoney(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        items = resp.css("div.data-item, table.data-table tr")
        results = []
        
        for item in items[:100]:
            name = item.css("a::text, td::text").get("").strip()
            link = item.css("a::attr(href)").get("")
            
            if name:
                results.append({
                    "title": name,
                    "description": desc,
                    "url": link if link.startswith("http") else resp.url,
                    "scraped_at": datetime.now().isoformat()
                })
        
        return results if results else [{"title": title, "description": desc, "url": resp.url}]
    
    async def _extract_nbs(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "NBS"}]
    
    async def _extract_smm(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "SMM"}]
    
    async def _extract_mysteel(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "MySteel"}]
    
    async def _extract_trendforce(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "TrendForce"}]
    
    async def _extract_chinamonitor(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "ChinaMonitor"}]
    
    async def _extract_ccid(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "CCID"}]
    
    async def _extract_crugroup(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "CRU"}]
    
    async def _extract_fastmarkets(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "Fastmarkets"}]
    
    async def _extract_inmetals(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "Inmetals"}]
    
    async def _extract_intrinio(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "Intrinio"}]
    
    async def _extract_coinmarketcap(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "CoinMarketCap"}]
    
    async def _extract_kaggle(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "Kaggle"}]
    
    async def _extract_wanfang(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "WanfangData"}]
    
    async def _extract_oschina(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "OSChina"}]
    
    async def _extract_nhc(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "NHC"}]
    
    async def _extract_mof(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "MOF"}]
    
    async def _extract_metal(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "Metal.com"}]
    
    async def _extract_kitco(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "Kitco"}]
    
    async def _extract_isee(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "ISEE"}]
    
    async def _extract_hqcdc(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "HQCDC"}]
    
    async def _extract_gtarsc(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "GTARSC"}]
    
    async def _extract_digitimes(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "Digitimes"}]
    
    async def _extract_datagov(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "Data.gov"}]
    
    async def _extract_cssn(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "CSSN"}]
    
    async def _extract_cqvip(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "CQVIP"}]
    
    async def _extract_cnki(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "CNKI"}]
    
    async def _extract_cas(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "CAS"}]
    
    async def _extract_cap(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "CAP"}]
    
    async def _extract_caict(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "CAICT"}]
    
    async def _extract_bls(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "BLS"}]
    
    async def _extract_tushare(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "Tushare"}]
    
    async def _extract_apipoer(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "APIPoer"}]
    
    async def _extract_huggingface(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "HuggingFace"}]
    
    async def _extract_yunnan_flowers(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "Yunnan Flowers"}]
    
    async def _extract_cisia(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "CISIA"}]
    
    async def _extract_caa(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "CAA"}]
    
    async def _extract_agri_info(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "Agri-Info"}]
    
    async def _extract_yn_agri(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "YN Agriculture"}]
    
    async def _extract_zce(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "ZCE"}]
    
    async def _extract_wind(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "Wind"}]
    
    async def _extract_stat_gov(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "Stat.gov"}]
    
    async def _extract_kunfloral(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "Kunfloral"}]
    
    async def _extract_toutiao(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "Toutiao"}]
    
    async def _extract_zhihu(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "Zhihu"}]
    
    async def _extract_weibo(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "Weibo"}]
    
    async def _extract_people(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "People.com"}]
    
    async def _extract_wechat(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url, "source": "WeChat"}]
    
    async def _generic(self, resp):
        title = resp.css("h1::text, title::text").get("").strip()
        desc = resp.css("meta[name='description']::attr(content)").get("")
        
        return [{"title": title, "description": desc, "url": resp.url}]
    
    def _save_sqlite(self, item):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        
        try:
            cols = [k for k in item.keys()]
            placeholders = ", ".join(["?" for _ in cols])
            
            create_sql = f"""CREATE TABLE IF NOT EXISTS mof_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                description TEXT,
                url TEXT UNIQUE,
                source TEXT,
                scraped_at TEXT
            )"""
            
            conn.execute(create_sql)
            
            insert_sql = f"INSERT OR IGNORE INTO mof_data ({', '.join(cols)}) VALUES ({placeholders})"
            conn.execute(insert_sql, [item.get(c, "") for c in cols])
            conn.commit()
        except Exception as e:
            self.logger.error(f"SQLite error: {e}")
        finally:
            conn.close()
    
    def _save_json(self, item):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        path = os.path.join(OUTPUT_DIR, f"{name}_items.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    
    def run_spider(self, urls=None, save=True):
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
        if not os.path.exists(DB_PATH):
            return
        
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM mof_data")
        rows = cursor.fetchall()
        conn.close()
        
        out_path = os.path.join(OUTPUT_DIR, f"{name}_complete.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump([dict(r) for r in rows], f, indent=2, ensure_ascii=False)
        
        self.logger.info(f"Exported {len(rows)} records")
    
    async def on_close(self):
        self.logger.info("Spider completed.")


def run_mof(urls=None, limit: int = 100) -> list[dict]:
    """Entry point for fd-open-data-protocol dispatch.

    Args:
        urls: Optional list of URLs to crawl (defaults to START_URLS)
        limit: Maximum number of records to return

    Returns:
        List of extracted data records
    """
    from scrapy.crawler import CrawlerProcess
    from scrapy.utils.project import get_project_settings

    spider_instance = MofSpider()
    settings = get_project_settings()
    settings.set("CLOSESPIDER_ITEMCOUNT", limit)

    process = CrawlerProcess(settings)
    process.crawl(spider_instance, urls=urls or START_URLS)

    # Collect results
    results = []
    try:
        process.start()
        # Read from SQLite after crawl
        if os.path.exists(DB_PATH):
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM mof_data LIMIT ?", (limit,))
            results = [dict(row) for row in cursor.fetchall()]
            conn.close()
    except Exception as e:
        spider_instance.logger.error(f"Error in run_mof: {e}")

    return results[:limit]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run spider")
    parser.add_argument("--urls", nargs="+", help="Custom URLs")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    
    args = parser.parse_args()
    
    spider_instance = MofSpider()
    spider_instance.logger.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    
    ok = spider_instance.run_spider(urls=args.urls, save=not args.dry_run)
    print("✓ Done!" if ok else "✗ Failed!")
    sys.exit(0 if ok else 1)
