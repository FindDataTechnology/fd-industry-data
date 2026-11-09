"""DCE (大连商品交易所) data unit — two channels, Sina daily-kline is default.

Channel 1 (DEFAULT, 2026-11-07 restaurant-vertical wave-0 repair):
    Sina futures daily kline — InnerFuturesNewService.getDailyKLine, the same
    endpoint akshare's futures_zh_daily_sina wraps. Carries exchange daily
    bars relayed by Sina: date / open / high / low / close / volume / hold
    (持仓量) / settle (结算价). Main-continuous symbols M0 (豆粕主力连续) and
    C0 (玉米主力连续) each return the full roll-adjusted history
    (2005-01-04 .. today, ~5300 rows; measured 2026-11-07). Direct stdlib
    urllib — no scrapling, no third-party dependency, survives the official
    site's anti-bot stack entirely.

Channel 2 (RETAINED, not default):
    The original scrapling page crawler against www.dce.com.cn. DCE's site
    sits behind a Ruishu (瑞数) JS challenge — plain HTTP fetches get 412 and
    an empty shell page, which is why production kept reporting success with
    rows_written=0. The code is kept verbatim for reference
    (DCESpider / run_spider / run_dce_pages); nothing schedules it.

Why the default flipped: the page channel is structurally blocked (would need
a full JS runtime and continuous Ruishu unfencing for zero marginal data —
the same bars are on Sina), and wave 0 needs a daily settlement-price supply.
This consciously overturns the 2026-11-06 park adjudication; the ledger entry
output/adjudication/ledger.json records the reversal (decided_by
restaurant-vertical-2026-11-07).

Data caveats (measured 2026-11-07):
    * settle is 0.000 on scattered historical days (~1589 of 5315 for M0,
      last occurrence 2024-09-25) — a sentinel for "not relayed", kept as-is
      in the payload and skipped by the restaurant extractor.
    * The ``type`` query param akshare hardcodes ("2021_04_12") is inert —
      responses are byte-identical with any value or without it.
"""

import json
import os
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "dce_prices.db")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

# ── Sina daily-kline channel (default) ───────────────────────────────────────

SINA_KLINE_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
    "var%20_dce_=/InnerFuturesNewService.getDailyKLine"
)

DEFAULT_SYMBOLS = ["M0", "C0"]

# contract_desc for well-known main-continuous symbols; anything else keeps
# its own name (e.g. a specific contract like M2701 stays "M2701").
CONTRACT_DESC = {
    "M0": "豆粕主力连续",
    "C0": "玉米主力连续",
}

_DATE_FMT = "%Y-%m-%d"


def _num(value):
    """Parse a Sina numeric field to float; None when absent/unparsable."""
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_sina_daily_kline(symbol: str, timeout: int = 30) -> list[dict]:
    """Fetch one symbol's full daily-kline history from Sina (ascending by date).

    Returns the raw rows as dicts with Sina's short keys
    (d/o/h/l/c/v/p/s) — parsing into payloads lives in run_dce so the
    golden path stays testable at one level.
    """
    url = f"{SINA_KLINE_URL}?{urllib.parse.urlencode({'symbol': symbol})}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Referer": f"https://finance.sina.com.cn/futures/quotes/{symbol}.shtml",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        text = response.read().decode("utf-8")
    # jsonp envelope: /*anti-frame comment*/ var _dce_=([...]);
    if "=(" not in text or ");" not in text:
        raise ValueError(f"sina kline {symbol}: non-jsonp response head: {text[:80]!r}")
    payload = text.split("=(")[1].split(");")[0]
    rows = json.loads(payload)
    if not isinstance(rows, list):
        raise ValueError(f"sina kline {symbol}: unexpected payload type {type(rows).__name__}")
    return rows


def run_dce(symbols: list[str] | None = None, limit: int | None = None) -> list[dict]:
    """Entry point for fd-open-data-protocol dispatch (Sina daily-kline channel).

    Payload per row:
        {symbol, contract_desc, date, settle, close, volume, open_interest,
         source_url, scraped_at}

    - settle is the exchange settlement price (结算价), the primary measure;
    - open_interest is Sina's ``hold`` field (持仓量, in lots);
    - rows with settle==0 are kept faithfully (sentinel for "not relayed";
      the restaurant extractor skips them);
    - deterministic order: **newest-first, round-robin across symbols**
      (M0 latest day, C0 latest day, M0 second-latest, ...) — so any
      ``limit`` budget covers every symbol's most recent days. The runner
      defaults to ``limit=100``, which with this ordering keeps a rolling
      ~50-trading-day window fresh per symbol on every scheduled run; the
      full ~10.6k-row history lands via one big-``limit`` backfill
      (crawl_items persist across runs, the extractor reads all of them).
    """
    if symbols is None:
        symbols = DEFAULT_SYMBOLS
    scraped_at = datetime.now(timezone.utc).isoformat()
    per_symbol: list[list[dict]] = []
    for symbol in symbols:
        rows = fetch_sina_daily_kline(symbol)
        source_url = (
            "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
            f"InnerFuturesNewService.getDailyKLine?symbol={urllib.parse.quote(symbol)}"
        )
        parsed: list[dict] = []
        for row in rows:
            date = str(row.get("d", "")).strip()
            try:
                datetime.strptime(date, _DATE_FMT)
            except ValueError:
                continue  # malformed date row: skipped, never fabricated
            parsed.append({
                "symbol": symbol,
                "contract_desc": CONTRACT_DESC.get(symbol, symbol),
                "date": date,
                "settle": _num(row.get("s")),
                "close": _num(row.get("c")),
                "volume": _num(row.get("v")),
                "open_interest": _num(row.get("p")),
                "source_url": source_url,
                "scraped_at": scraped_at,
            })
        parsed.sort(key=lambda r: r["date"], reverse=True)
        per_symbol.append(parsed)

    # newest-first round-robin across symbols: any limit budget covers every
    # symbol's most recent days (see run_dce docstring)
    items: list[dict] = []
    while any(per_symbol):
        for series in per_symbol:
            if series:
                items.append(series.pop(0))
    if limit is not None:
        items = items[:limit]
    return items


# ── Legacy page channel (RETAINED, not default — see module docstring) ──────
# Everything below the fold needs scrapling and talks to www.dce.com.cn,
# which is Ruishu-blocked (HTTP 412). It is kept for reference only.

try:
    from scrapling.spiders import Spider, Response, Request
    from scrapling.fetchers import FetcherSession

    _SCRAPING_AVAILABLE = True
except ImportError:  # the default Sina channel must not depend on scrapling
    _SCRAPING_AVAILABLE = False

if _SCRAPING_AVAILABLE:

    class DCESpider(Spider):
        name = "dce"

        # DCE main data pages
        START_URLS = [
            "https://www.dce.com.cn/dalianshangpin/flk/index.html",
            "https://www.dce.com.cn/dalianshangpin/qhsj/index.html",
            "https://www.dce.com.cn/dalianshangpin/jcyj/index.html",
        ]

        # Main commodity categories
        CATEGORY_URLS = {
            "agriculture": [
                "https://www.dce.com.cn/dalianshangpin/flk/sdb/index.html",  # Soybeans
                "https://www.dce.com.cn/dalianshangpin/flk/cb/index.html",   # Corn
                "https://www.dce.com.cn/dalianshangpin/flk/my/index.html",   # Meal
                "https://www.dce.com.cn/dalianshangpin/flk/ls/index.html",   # Palm Oil
            ],
            "chemicals": [
                "https://www.dce.com.cn/dalianshangpin/flk/lp/index.html",   # PP
                "https://www.dce.com.cn/dalianshangpin/flk/v/index.html",    # PVC
                "https://www.dce.com.cn/dalianshangpin/flk/cp/index.html",   # PE
                "https://www.dce.com.cn/dalianshangpin/flk/xbyz/index.html",  # Styrene
            ],
            "livestock": [
                "https://www.dce.com.cn/dalianshangpin/flk/hgy/index.html",  # Live Hogs
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
            """Main parser for DCE listing pages"""
            self.logger.info(f"Parsing DCE page: {response.url}")

            if "/flk/" in response.url:  # Futures page
                await self._parse_futures_page(response)
            elif "/qhsj/" in response.url:  # Price discovery
                await self._parse_price_data(response)
            elif "/jcyj/" in response.url:  # Economic analysis
                await self._parse_analysis_page(response)
            else:
                await self._parse_main_page(response)

        async def _parse_main_page(self, response: Response):
            """Parse DCE main homepage"""
            # Extract all category links
            for link in response.css("a"):
                href = link.attrib.get("href", "")

                if href and isinstance(href, str):
                    # Add full URL if relative
                    if href.startswith("/"):
                        href = "https://www.dce.com.cn" + href
                    elif not href.startswith("http"):
                        continue

                    # Check if it's a relevant data page
                    if any(cat in href.lower() for cat in ["/flk/", "/qhsj/", "/jcyj/", "/data/"]):
                        yield Response(href, callback=self.parse)

                    # Look for table data on main page
                    tables = response.css("table")
                    for table in tables[:5]:
                        await self._try_extract_table_data(table, response)

        async def _parse_futures_page(self, response: Response):
            """Parse futures product pages"""
            commodity_type = self._identify_commodity_type(response.url)
            self.logger.info(f"Extracting {commodity_type} futures from DCE")

            # Try to extract price tables
            tables = response.css("table[width]", ".Table1", "table[id^='grid']")

            for table in tables:
                rows = table.css("tr")

                # Skip empty or header-only tables
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
                    if len(cells) == len(headers) or len(cells) >= 4:
                        item = {}

                        # Map cells to headers
                        for i, header in enumerate(headers[:len(cells)]):
                            value = cells[i].css("::text").get("").strip()

                            # Clean up values
                            if value == "-" or value == "":
                                value = None

                            # Convert numeric fields
                            if header and value:
                                # Try to convert to float if looks like number
                                try:
                                    if "." in value or ("万" in value) or ("亿" in value):
                                        value = self._clean_numeric(value)
                                    else:
                                        value = int(value)
                                except ValueError:
                                    pass

                            key = header.lower().replace(" ", "_").replace("/", "_")
                            item[key] = value

                        if item:
                            item["scraped_at"] = datetime.now().isoformat()
                            item["source_url"] = response.url
                            item["category"] = commodity_type

                            self._save_to_sqlite(item)
                            yield item

            # Pagination handling
            pagination = response.css("a:contains('下一页'), a:contains('下一页码'), a.page-next")
            for link in pagination:
                url = link.attrib.get("href", "")
                if url and "javascript" not in url.lower():
                    yield Response(url, callback=self.parse)

        async def _parse_price_data(self, response: Response):
            """Parse price discovery/inquiry data"""
            commodity_type = self._identify_commodity_type(response.url)

            # Look for detailed price information
            tables = response.css("table")
            for table in tables:
                rows = table.css("tr")
                for row in rows[1:]:
                    cells = row.css("td")
                    if len(cells) >= 5:
                        item = {
                            "contract_name": cells[0].css("::text").get("").strip(),
                            "opening": cells[1].css("::text").get("").strip(),
                            "high": cells[2].css("::text").get("").strip(),
                            "low": cells[3].css("::text").get("").strip(),
                            "close": cells[4].css("::text").get("").strip(),
                            "settlement": cells[5].css("::text").get("").strip() if len(cells) > 5 else "",
                            "change": cells[6].css("::text").get("").strip() if len(cells) > 6 else "",
                            "volume": cells[7].css("::text").get("").strip() if len(cells) > 7 else "",
                            "open_interest": cells[8].css("::text").get("").strip() if len(cells) > 8 else "",
                            "scraped_at": datetime.now().isoformat(),
                            "source_url": response.url,
                            "category": commodity_type,
                        }

                        self._save_to_sqlite(item)
                        yield item

        async def _parse_analysis_page(self, response: Response):
            """Parse economic analysis pages"""
            # Extract news/articles/market analysis
            articles = response.css("ul li", ".NewsList li", "div.list-item")

            for article in articles:
                title = article.css("a::text").get("").strip()
                link = article.css("a::attr(href)").get("")
                date = article.css("span.date, span.time::text").get("").strip()

                if title and link:
                    full_url = response.urljoin(link) if link.startswith("/") else link

                    item = {
                        "title": title,
                        "url": full_url,
                        "publish_date": date,
                        "type": "market_analysis",
                        "scraped_at": datetime.now().isoformat(),
                        "source_url": response.url,
                        "category": "analysis",
                    }

                    self._save_to_sqlite(item)
                    yield item

                    # Optionally crawl individual articles
                    yield Response(full_url, callback=self._parse_analysis_detail, meta={"item": item})

        async def _parse_analysis_detail(self, response: Response):
            """Parse detailed market analysis article"""
            item = response.meta.get("item", {})

            content = response.css("div.article-content, div.content, tr[id*='TR$']").css("::text").getall()

            item["content"] = "\n".join(c.strip() for c in content if c.strip())

            self._save_to_sqlite(item)
            yield item

        async def _try_extract_table_data(self, table, response):
            """Try to extract structured table data"""
            rows = table.css("tr")
            if len(rows) < 2:
                return

            commodity_type = self._identify_commodity_type(response.url)

            # Check if this looks like futures data
            first_row_cells = rows[0].css("td")
            if len(first_row_cells) >= 3:
                header_texts = [c.css("::text").get("").strip() for c in first_row_cells]

                # Look for typical futures data headers
                numeric_headers = ["开", "高", "低", "收", "结", "量", "仓"]
                has_numeric = any(any(h in ht for h in numeric_headers) for ht in header_texts)

                if has_numeric:
                    for row in rows[1:]:
                        cells = row.css("td")
                        if len(cells) >= 3:
                            item = {}

                            for i, cell in enumerate(cells):
                                value = cell.css("::text").get("").strip()
                                key = f"col_{i}"

                                if value:
                                    try:
                                        value = float(value.replace(",", ""))
                                    except ValueError:
                                        pass

                                item[key] = value

                            if item:
                                item["scraped_at"] = datetime.now().isoformat()
                                item["source_url"] = response.url
                                item["category"] = commodity_type

                                self._save_to_sqlite(item)
                                yield item

        def _identify_commodity_type(self, url: str) -> str:
            """Identify which commodity category the URL belongs to"""
            if "/sdb/" in url.lower() or "大豆" in url:
                return "agriculture"  # Soybeans
            elif "/cb/" in url.lower() or "玉米" in url:
                return "agriculture"  # Corn
            elif "/my/" in url.lower() or "豆粕" in url:
                return "agriculture"  # Meal
            elif "/ls/" in url.lower() or "棕榈" in url:
                return "agriculture"  # Palm oil
            elif "/lp/" in url.lower() or "PP" in url or "聚丙烯" in url:
                return "chemicals"
            elif "/v/" in url.lower() or "PVC" in url:
                return "chemicals"
            elif "/cp/" in url.lower() or "PE" in url:
                return "chemicals"
            elif "/xbyz/" in url.lower() or "苯乙烯" in url:
                return "chemicals"
            elif "/hgy/" in url.lower() or "生猪" in url:
                return "livestock"
            else:
                return "general"

        def _clean_numeric(self, value: str) -> float:
            """Clean and convert Chinese numeric format to float"""
            value = value.strip()

            # Handle Chinese units (no eval — arithmetic is explicit)
            try:
                if "万" in value:
                    return float(value.replace("万", "")) * 10000
                elif "亿" in value:
                    return float(value.replace("亿", "")) * 100000000
                return float(value)
            except ValueError:
                return float(value)

        def _save_to_sqlite(self, item: dict):
            """Save item to SQLite database"""
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

            conn = sqlite3.connect(DB_PATH)

            try:
                # Determine category from item or use default
                category = item.get("category", "unknown")
                category_safe = category.replace("-", "_")

                # Create table
                conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS {category_safe} (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        scraped_at TEXT,
                        source_url TEXT,
                        category TEXT,
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

                # Build column list dynamically
                columns = ["id", "scraped_at", "source_url", "category"]
                json_columns = ["other_data"]

                extra_columns = set(item.keys()) - set(columns + json_columns) - {"id"}
                for col in extra_columns:
                    if col not in columns:
                        columns.append(col)

                # Prepare insert statement
                placeholders = ",".join(["?" for _ in columns])
                cols = ",".join(columns)

                # Prepare values
                values = [item.get(col) for col in columns]

                # Store non-standard columns in JSON
                other_data = {k: v for k, v in item.items() if k not in columns}
                if other_data:
                    values[values.index(None) if None in values else -1] = json.dumps(other_data, ensure_ascii=False)
                else:
                    values[-1] = json.dumps({}, ensure_ascii=False)

                conn.execute(f"INSERT INTO {category_safe} ({cols}) VALUES ({placeholders})", values)
                conn.commit()

            except Exception as e:
                self.logger.error(f"Error saving DCE item: {e}")
            finally:
                conn.close()

        async def on_close(self):
            """Export all data to JSON on close"""
            os.makedirs(OUTPUT_DIR, exist_ok=True)

            categories = ["agriculture", "chemicals", "livestock", "general", "analysis"]

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

    async def run_spider():
        """Helper function to run the legacy page spider"""
        spider = DCESpider()
        result = spider.start()

        print(f"\n{'='*50}")
        print(f"DCE Scraping Complete")
        print(f"{'='*50}")
        print(f"Items scraped: {result.stats.items_scraped}")
        print(f"Duration: {result.stats.elapsed_seconds:.1f}s")
        print(f"Output directory: {OUTPUT_DIR}")

        return result

    def run_dce_pages(limit: int | None = None) -> list[dict]:
        """Legacy page-channel entry (RETAINED, not scheduled).

        Kept for reference: www.dce.com.cn is behind a Ruishu challenge and
        returns 412 / empty shells to plain fetches, so this channel yielded
        rows_written=0 in production. Use run_dce (Sina channel) instead.
        """
        from scrapling import Fetcher

        items: list[dict] = []
        fetcher = Fetcher(auto_match=False, impersonate="chrome")

        category_urls = []
        for cat_list in DCESpider.CATEGORY_URLS.values():
            category_urls.extend(cat_list)

        for url in category_urls:
            try:
                response = fetcher.get(url, timeout=30, stealthy_headers=True)
                if response.status != 200:
                    continue
                spider_tmp = DCESpider()
                commodity_type = spider_tmp._identify_commodity_type(url)
                for table in response.css("table"):
                    rows = table.css("tr")
                    if len(rows) < 2:
                        continue
                    headers = [th.text.strip() for th in rows[0].css("th, td")]
                    for row in rows[1:]:
                        cells = [td.text.strip() for td in row.css("td")]
                        if len(cells) < 4:
                            continue
                        item = {
                            "source_url": url,
                            "category": commodity_type,
                            "scraped_at": datetime.now().isoformat(),
                        }
                        for i, header in enumerate(headers[:len(cells)]):
                            key = header.lower().replace(" ", "_").replace("/", "_") if header else f"col_{i}"
                            value = cells[i] if i < len(cells) else None
                            if value == "-" or value == "":
                                value = None
                            item[key] = value
                        items.append(item)
            except Exception:
                pass

        if limit is not None:
            items = items[:limit]
        return items


if __name__ == "__main__":
    rows = run_dce()
    print(f"dce sina channel: {len(rows)} rows"
          f" (symbols={','.join(DEFAULT_SYMBOLS)})", )
    if rows:
        print("first:", json.dumps(rows[0], ensure_ascii=False))
        print("last: ", json.dumps(rows[-1], ensure_ascii=False))
