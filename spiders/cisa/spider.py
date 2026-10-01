from scrapling.spiders import Spider, Response, Request
from scrapling.fetchers import FetcherSession
import sqlite3
import json
import logging
import os
import urllib.parse
from datetime import datetime

from lxml import html as _lxml_html


DB_PATH = os.path.join(os.path.dirname(__file__), "data", "cisa_data.db")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

# ---------------------------------------------------------------------------
# chinaisa.org.cn 数据门户（中钢协数据门户，注意与上方 cisa.org.cn 兄弟站区分）
# 新通道：独立函数 get_chinaisa_data()，不动既有 CISASpider / 既有 run_cisa 行为。
# ---------------------------------------------------------------------------
_log = logging.getLogger("cisa.spider")

CHINAISA_HOST = "https://www.chinaisa.org.cn"
# POST 端点（实测有效）：表单字段只有一个 params=<URL 编码的 JSON 字符串>
# （站点前端 psUtil.post: JSON.stringify(params) -> encodeURI -> $.post(path, {"params": ...})）
CHINAISA_COLUMNLIST_URL = CHINAISA_HOST + "/gxportal/xfpt/portal/getColumnList"
CHINAISA_LIST_PAGE_URL = CHINAISA_HOST + "/gxportal/xfgl/portal/list.html"
# 栏目 id（取自门户首页导航 list.html?columnId=...，实测有效）：
#   统计发布   —— 粗钢产量旬报 / 钢材库存旬报 等统计稿件
#   综合价格指数 —— 周度「国内市场八个品种价格及指数」
CHINAISA_COLUMN_STATS = "2e3c87064bdfc0e43d542d87fce8bcbc8fe0463d5a3da04d7e11b4c7d692194b"
CHINAISA_COLUMN_PRICE = "63913b906a7a663f7f71961952b1ddfa845714b5982655b773a62b85dd3b064e"
# 站点原生分页大小（list.js: locationUrl(pageNo, 25)），只发实测有效参数
CHINAISA_PAGE_SIZE = 25
CHINAISA_MAX_PAGES_PER_COLUMN = 4

CHINAISA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": CHINAISA_LIST_PAGE_URL,
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}


def _encode_uri(s: str) -> str:
    """Replicate JavaScript encodeURI (used by the site's own psUtil.post)."""
    keep = set(";,:@&=+$-_.!~*'()#/?")
    return "".join(
        c if (c.isalnum() or c in keep) else urllib.parse.quote(c, safe="")
        for c in s
    )


def _chinaisa_body(column_id: str, page_no: int | None = None) -> bytes:
    """Build the POST body: single form field `params` = encodeURI(JSON).

    载荷只含实测有效字段：columnId（栏目 id）+ 可选 param（encodeURI 过的
    {"pageNo","pageSize"} 分页 JSON，与站点 list.js 的 locationUrl 完全一致）。
    """
    payload: dict = {"columnId": column_id}
    if page_no is not None:
        payload["param"] = _encode_uri(
            json.dumps(
                {"pageNo": page_no, "pageSize": CHINAISA_PAGE_SIZE},
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
    return ("params=" + urllib.parse.quote(_encode_uri(json.dumps(payload, separators=(",", ":"), ensure_ascii=False)), safe="")).encode("utf-8")


async def _fetch_chinaisa_column(session, column_id: str, page_no: int | None) -> list[dict] | None:
    """POST one getColumnList request and parse articleListHtml into rows.

    Returns None on 5xx/非 JSON/缺 articleListHtml（调用方记录后跳过，不重试）。
    """
    try:
        r = await session.post(CHINAISA_COLUMNLIST_URL, data=_chinaisa_body(column_id, page_no), headers=CHINAISA_HEADERS)
    except Exception as exc:
        _log.warning("chinaisa getColumnList POST failed (column=%s page=%s): %s", column_id, page_no, exc)
        return None
    if r.status != 200:
        _log.warning("chinaisa getColumnList HTTP %s (column=%s page=%s) — skip", r.status, column_id, page_no)
        return None
    body = r.body if isinstance(r.body, bytes) else (r.text or "").encode("utf-8", "replace")
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        _log.warning("chinaisa getColumnList returned non-JSON (column=%s page=%s) — skip", column_id, page_no)
        return None
    article_html = data.get("articleListHtml")
    if not isinstance(article_html, str) or not article_html:
        # e.g. {"code":301,"message":"未获得所需要的参数"} — 参数被服务端拒绝
        _log.warning("chinaisa getColumnList rejected (column=%s page=%s): %s", column_id, page_no, body[:120])
        return None
    return _parse_chinaisa_list(article_html, column_id)


def _parse_chinaisa_list(article_html: str, column_id: str) -> list[dict]:
    """Parse the embedded articleListHtml fragment into schema rows."""
    doc = _lxml_html.fromstring(f"<div>{article_html}</div>")
    rows: list[dict] = []
    now_iso = datetime.now().isoformat()
    source_url = f"{CHINAISA_LIST_PAGE_URL}?columnId={column_id}"
    for li in doc.cssselect("ul.list > li"):
        a = li.cssselect("a")
        if not a:
            continue  # 分隔线等非条目节点，留空不写脏行
        title = (a[0].get("title") or a[0].text_content() or "").strip()
        href = (a[0].get("href") or "").strip()
        span = li.cssselect("span.times")
        publish_date = span[0].text_content().strip().strip("[] ") if span else ""
        if not title:
            continue
        if column_id == CHINAISA_COLUMN_PRICE:
            report_type = "周价格指数"
        elif "旬报" in title:
            report_type = "旬报"
        else:
            report_type = "统计发布"
        url = href if href.startswith("http") else CHINAISA_HOST + "/gxportal/xfgl/portal/" + href.lstrip("/")
        # 数值在 contentpdf/content 详情（PDF 附件为主），列表层无可解析数值：
        # value/unit 留空（None），不写脏行。
        rows.append({
            "report_type": report_type,
            "title": title,
            "publish_date": publish_date,
            "url": url,
            "value": None,
            "unit": None,
            "scraped_at": now_iso,
            "source_url": source_url,
        })
    return rows


def _dedupe(rows: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for r in rows:
        key = (r.get("report_type"), r.get("title"), r.get("publish_date"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


async def _get_chinaisa_data_async(limit: int) -> list[dict]:
    """Two proven columns (旬报 via 统计发布, weekly price index via 综合价格指数)."""
    limit = max(0, int(limit))
    if limit == 0:
        return []
    half = max(1, limit // 2)
    plan = [
        (CHINAISA_COLUMN_STATS, half),
        (CHINAISA_COLUMN_PRICE, limit - half),
    ]
    results: list[dict] = []
    async with FetcherSession(impersonate="chrome120", timeout=25, verify=False, stealthy_headers=False) as s:
        for column_id, budget in plan:
            if budget <= 0 or len(results) >= limit:
                break
            taken = 0
            prev_titles: set[str] | None = None
            for page_no in range(1, CHINAISA_MAX_PAGES_PER_COLUMN + 1):
                if taken >= budget or len(results) >= limit:
                    break
                rows = await _fetch_chinaisa_column(s, column_id, page_no)
                if rows is None:
                    break  # 5xx/空/参数被拒：记录后跳过，不重试
                titles = {r["title"] for r in rows}
                if prev_titles is not None and titles == prev_titles:
                    break  # 服务端翻页未生效，防重复
                prev_titles = titles
                for r in rows:
                    if taken >= budget or len(results) >= limit:
                        break
                    results.append(r)
                    taken += 1
                if len(rows) < CHINAISA_PAGE_SIZE:
                    break  # 末页
    return _dedupe(results)[:limit]


def get_chinaisa_data(limit: int = 100) -> list[dict]:
    """Fetch article listings from the CISA data portal (chinaisa.org.cn).

    中钢协数据门户（chinaisa.org.cn，区别于既有 cisa.org.cn 兄弟站）：
    - 统计发布栏目：粗钢产量旬报 / 钢材库存旬报（旬度口径，worldsteel 不覆盖中国旬度）
    - 综合价格指数栏目：周度「国内市场八个品种价格及指数」

    端点：POST /gxportal/xfpt/portal/getColumnList，表单字段 params=<URL 编码 JSON>，
    载荷只含实测有效参数（columnId + 可选分页 param）。

    Args:
        limit: Maximum records to return (default: 100)

    Returns:
        List of dicts: report_type / title / publish_date / url / value / unit /
        scraped_at / source_url. value/unit 为 None（数值在 PDF 附件中，列表层不可解析）。
    """
    import asyncio

    return asyncio.run(_get_chinaisa_data_async(limit))


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


def _run_cisa_legacy(limit: int = 100) -> list[dict]:
    """Legacy cisa.org.cn crawler — body unchanged from the original run_cisa.

    (Renamed only; fd-runner entry point is the merged run_cisa below. The
    legacy path is known-broken with scrapling 0.4.x — Spider has no
    parse_start_response/request — so run_cisa wraps this in try/except.)
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


def run_cisa(limit: int = 100) -> list[dict]:
    """fd-runner single entry point (CronJob only calls this).

    Merged two-channel result, total <= limit:
      1. Legacy cisa.org.cn crawler (_run_cisa_legacy, behavior unchanged) —
         wrapped in try/except so a legacy failure cannot take down the
         chinaisa channel. Known state: the legacy path raises with scrapling
         0.4.x (Spider.parse_start_response does not exist) and yields [].
      2. chinaisa.org.cn data portal (get_chinaisa_data).

    Args:
        limit: Maximum records to return (default: 100)

    Returns:
        Merged list of records as dicts (legacy rows first, then chinaisa rows).
    """
    results: list[dict] = []

    # Channel 1: legacy cisa.org.cn (wrapped — see docstring)
    try:
        results.extend(_run_cisa_legacy(limit=limit))
    except Exception as exc:
        _log.warning("legacy cisa.org.cn channel failed (skipped): %s: %s", type(exc).__name__, exc)

    # Channel 2: chinaisa.org.cn data portal
    remaining = limit - len(results)
    if remaining > 0:
        try:
            results.extend(get_chinaisa_data(limit=remaining))
        except Exception as exc:
            _log.warning("chinaisa.org.cn channel failed (skipped): %s: %s", type(exc).__name__, exc)

    return results[:limit]


if __name__ == "__main__":
    import asyncio
    result = asyncio.run(run_spider())
