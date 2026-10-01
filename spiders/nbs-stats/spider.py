#!/usr/bin/env python3
"""
National Bureau of Statistics (NBS) Spider - 国家统计局数据爬虫

Target: https://www.stats.gov.cn/ and https://data.stats.gov.cn/
Data Coverage:
  - GDP data (quarterly/annual)
  - CPI/PPI price indices
  - Industrial production statistics
  - Population and employment data
  - Economic indicators by province
  - Circulation-area producer goods market prices (旬度, via /sj/zxfb/ articles)

Architecture:
  - Primary: NBS easyquery API with browser impersonation
  - Anti-bot: Proper headers, rate limiting, cache-busting
  - Output: SQLite DB + JSON export
  - Special: Multiple indicator categories with fallback to akshare
"""

from __future__ import annotations

import asyncio
import calendar
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from scrapling.fetchers import FetcherSession
from scrapling.spiders import Spider, Request, Response

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("nbs_stats")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DB_PATH = DATA_DIR / "nbs_stats.db"

NBS_BASE_URL = "https://data.stats.gov.cn/easyquery.htm"
NBS_REFERER = "https://data.stats.gov.cn/easyquery.htm?cn=A01"

INDICATOR_CATEGORIES = {
    "gdp": {
        "quarterly": {"dbcode": "hgjd", "zbcode": "A0201", "freq": "Q"},
        "annual": {"dbcode": "hgnd", "zbcode": "A0201", "freq": "A"},
        "by_province": {"dbcode": "fsjd", "zbcode": "A0201", "freq": "Q"},
    },
    "price_indices": {
        "cpi_monthly": {"dbcode": "hgyd", "zbcode": "A0901", "freq": "M"},
        "ppi_monthly": {"dbcode": "hgyd", "zbcode": "A0902", "freq": "M"},
    },
    "industrial": {
        "production": {"dbcode": "hgyd", "zbcode": "A0202", "freq": "M"},
        "pmi": {"dbcode": "hgyd", "zbcode": "A0M01", "freq": "M"},
    },
    "population": {
        "annual": {"dbcode": "hgnd", "zbcode": "A0301", "freq": "A"},
    },
    "employment": {
        "urban": {"dbcode": "hgyd", "zbcode": "A0302", "freq": "M"},
    },
}

_UNIT_MAP = {
    "gdp": "亿元",
    "cpi": "%",
    "ppi": "%",
    "production": "%",
    "pmi": "%",
    "population": "万人",
    "employment": "%",
}

_QUARTER_RE = re.compile(r"(\d{4})年第(\d)季度")
_ISO_RE = re.compile(r"(\d{4})-(\d{2})")
_YEAR_RE = re.compile(r"(\d{4})年")


def _parse_period(raw: str, freq: str = "Q") -> str:
    raw = str(raw).strip()
    if freq == "Q":
        m = _QUARTER_RE.match(raw)
        if m:
            return f"{m.group(1)}Q{m.group(2)}"
        m = _ISO_RE.match(raw)
        if m:
            return f"{m.group(1)}Q{((int(m.group(2)) - 1) // 3 + 1)}"
    elif freq == "A":
        m = _YEAR_RE.match(raw)
        if m:
            return m.group(1)
    elif freq == "M":
        m = _ISO_RE.match(raw)
        if m:
            return f"{m.group(1)}-{m.group(2)}"
    return raw


def _to_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        f = float(val)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _build_nbs_url(dbcode: str, zbcode: str, start_period: str) -> str:
    wds = "[]"
    dfwds = json.dumps(
        [
            {"wdcode": "zb", "valuecode": zbcode},
            {"wdcode": "sj", "valuecode": start_period},
        ],
        separators=(",", ":"),
    )
    k1 = str(int(time.time() * 1000))
    return (
        f"{NBS_BASE_URL}?m=QueryData&dbcode={dbcode}"
        f"&rowcode=zb&colcode=sj&wds={wds}&dfwds={dfwds}&k1={k1}"
    )


def _parse_nbs_response(
    text: str, category: str, indicator: str, freq: str = "Q"
) -> list[dict[str, Any]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error("JSON decode error: %s", e)
        return []

    if data.get("returncode") != 200:
        logger.warning("NBS API returncode=%s", data.get("returncode"))
        return []

    returndata = data.get("returndata") or {}
    datanodes = returndata.get("datanodes") or []
    wdnodes = returndata.get("wdnodes") or []

    sj_map: dict[str, str] = {}
    zb_map: dict[str, str] = {}
    for wdnode in wdnodes:
        wdcode = wdnode.get("wdcode", "")
        for entry in wdnode.get("nodes") or []:
            code = entry.get("code", "")
            name = entry.get("name", "")
            if wdcode == "sj":
                sj_map[code] = name
            elif wdcode == "zb":
                zb_map[code] = name

    items: list[dict[str, Any]] = []
    for node in datanodes:
        data_obj = node.get("data") or {}
        if not data_obj.get("hasdata"):
            continue
        value_str = data_obj.get("strdata") or data_obj.get("data")
        if value_str is None or value_str == "":
            continue

        value = _to_float(value_str)
        if value is None:
            continue

        code = node.get("code", "")
        parts = code.split(".")
        if len(parts) < 2:
            continue

        period_raw = parts[-1]
        zb_code = parts[0]
        period = _parse_period(sj_map.get(period_raw, period_raw), freq)
        ind_name = zb_map.get(zb_code, indicator)

        items.append(
            {
                "period": period,
                "value": value,
                "indicator_code": zb_code,
                "indicator_name": ind_name,
                "category": category,
                "indicator_type": indicator,
                "unit": _UNIT_MAP.get(category, ""),
                "frequency": freq,
                "source": "NBS",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    return items


async def _fetch_nbs_indicator(
    category: str, indicator: str, start_year: int = 2010
) -> list[dict[str, Any]]:
    config = INDICATOR_CATEGORIES.get(category, {}).get(indicator)
    if not config:
        logger.warning("No config for %s/%s", category, indicator)
        return []

    freq = config["freq"]
    freq_suffix_map = {"hgjd": "A", "hgyd": "01", "hgnd": "", "fsjd": "A"}
    suffix = freq_suffix_map.get(config["dbcode"], "")
    start_period = f"{start_year}{suffix}" if suffix else str(start_year)

    url = _build_nbs_url(config["dbcode"], config["zbcode"], start_period)
    logger.info("Fetching NBS %s/%s: %s", category, indicator, url[:120])

    try:
        # FetcherSession is a factory context manager in scrapling 0.4.x: the
        # yielded session (not the manager) carries the .get() method.
        async with FetcherSession(impersonate="chrome") as session:
            response = await session.get(
                url,
                headers={
                    "Referer": NBS_REFERER,
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
    except Exception as e:
        logger.warning("NBS fetch failed: %s", e)
        return []

    if response.status != 200:
        logger.warning("NBS API status=%s", response.status)
        return []

    if not response.text or len(response.text) < 100:
        logger.warning("NBS API response too short")
        return []

    return _parse_nbs_response(response.text, category, indicator, freq)


class NbsStatsSpider(Spider):
    name = "nbs_stats"
    start_urls: list[str] = []
    allowed_domains = {"data.stats.gov.cn", "www.stats.gov.cn"}
    concurrent_requests = 1
    download_delay = 2

    categories: list[str] = ["gdp", "price_indices", "industrial"]
    start_year: int = 2010

    def configure_sessions(self, manager):
        manager.add("default", FetcherSession(impersonate="chrome"), default=True)

    async def start_requests(self):
        for category in self.categories:
            cat_config = INDICATOR_CATEGORIES.get(category, {})
            for indicator, config in cat_config.items():
                freq = config["freq"]
                freq_suffix_map = {"hgjd": "A", "hgyd": "01", "hgnd": "", "fsjd": "A"}
                suffix = freq_suffix_map.get(config["dbcode"], "")
                start_period = f"{self.start_year}{suffix}" if suffix else str(self.start_year)
                url = _build_nbs_url(config["dbcode"], config["zbcode"], start_period)
                yield Request(
                    url=url,
                    callback=self.parse,
                    meta={
                        "category": category,
                        "indicator": indicator,
                        "freq": freq,
                    },
                )

    async def parse(self, response: Response):
        category = response.meta.get("category", "")
        indicator = response.meta.get("indicator", "")
        freq = response.meta.get("freq", "Q")

        if response.status == 200 and response.text:
            items = _parse_nbs_response(response.text, category, indicator, freq)
            for item in items:
                yield item
            logger.info("Yielded %d records for %s/%s", len(items), category, indicator)


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nbs_indicators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            category TEXT NOT NULL,
            indicator_type TEXT NOT NULL,
            value REAL,
            indicator_code TEXT,
            indicator_name TEXT,
            unit TEXT,
            frequency TEXT,
            source TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(period, category, indicator_type)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_period ON nbs_indicators(period)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_category ON nbs_indicators(category)
    """)
    conn.commit()
    return conn


def save_to_sqlite(items: list[dict[str, Any]], conn: sqlite3.Connection) -> int:
    inserted = 0
    for item in items:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO nbs_indicators
                (period, category, indicator_type, value, indicator_code,
                 indicator_name, unit, frequency, source, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["period"],
                    item["category"],
                    item["indicator_type"],
                    item["value"],
                    item.get("indicator_code", ""),
                    item.get("indicator_name", ""),
                    item.get("unit", ""),
                    item.get("frequency", ""),
                    item.get("source", ""),
                    item.get("fetched_at", ""),
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.error("SQLite insert error: %s", e)
    conn.commit()
    return inserted


def save_to_json(items: list[dict[str, Any]], json_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


async def get_nbs_data(
    categories: list[str] | None = None,
    start_year: int = 2010,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch comprehensive data from National Bureau of Statistics.

    Args:
        categories: List of category keys. Available:
            - gdp: GDP data (quarterly, annual, by province)
            - price_indices: CPI/PPI monthly data
            - industrial: Industrial production and PMI
            - population: Annual population data
            - employment: Urban employment data
        start_year: Earliest year to include (default: 2010)

    Returns:
        Dict with category keys and list of indicator records.
    """
    if categories is None:
        categories = list(INDICATOR_CATEGORIES.keys())

    conn = init_db()
    results: dict[str, list[dict[str, Any]]] = {}

    logger.info("Starting NBS data fetch for categories: %s", ", ".join(categories))

    for category in categories:
        cat_config = INDICATOR_CATEGORIES.get(category, {})
        category_items: list[dict[str, Any]] = []

        for indicator in cat_config.keys():
            items = await _fetch_nbs_indicator(category, indicator, start_year)
            if items:
                category_items.extend(items)
                logger.info("  %s/%s: %d records", category, indicator, len(items))
            await asyncio.sleep(2)

        if category_items:
            results[category] = category_items
            n_sqlite = save_to_sqlite(category_items, conn)
            json_path = OUTPUT_DIR / f"nbs_{category}.json"
            save_to_json(category_items, json_path)
            logger.info(
                "Saved %d %s records: %d to SQLite, JSON to %s",
                len(category_items),
                category,
                n_sqlite,
                json_path,
            )

    conn.close()
    return results


def get_gdp_data(start_year: int = 2010) -> list[dict[str, Any]]:
    """Fetch GDP data from NBS."""
    results = asyncio.run(get_nbs_data(["gdp"], start_year))
    return results.get("gdp", [])


def get_price_indices(start_year: int = 2010) -> list[dict[str, Any]]:
    """Fetch CPI/PPI data from NBS."""
    results = asyncio.run(get_nbs_data(["price_indices"], start_year))
    return results.get("price_indices", [])


# ---------------------------------------------------------------------------
# Circulation-area producer goods market prices (旬度扩展, 2026-10 added)
#
# 流通领域重要生产资料市场价格变动情况 — published every ten days on
# www.stats.gov.cn /sj/zxfb/ as an article with an inline HTML table
# (~50 products across six categories). This is an INDEPENDENT parsing path:
# it shares zero code with the easyquery hgjd/hgyd paths above and never
# touches them. HTTP goes through scrapling.fetchers.FetcherSession only;
# the source is domestic, so requests are always direct (never proxied).
# ---------------------------------------------------------------------------

CIRC_LIST_URL = "https://www.stats.gov.cn/sj/zxfb/"
CIRC_TITLE_KEYWORD = "流通领域重要生产资料市场价格"
CIRC_MAX_LIST_PAGES = 3
CIRC_MAX_ISSUES = 6

_CIRC_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 期号: modern titles are "2026年9月中旬流通领域…"; older ones carry a
# parenthetical range "（2020年9月1日—10日）". Both are supported.
_CIRC_PERIOD_TEN_RE = re.compile(r"(\d{4})年(\d{1,2})月(上旬|中旬|下旬)")
_CIRC_PERIOD_RANGE_RE = re.compile(
    r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*[—–\-~～至]+\s*(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日"
)
_CIRC_TEN_BOUNDS = {"上旬": (1, 10), "中旬": (11, 20), "下旬": (21, None)}
_CIRC_CATEGORY_NUM_RE = re.compile(r"^[一二三四五六七八九十]+、\s*")
_CIRC_MISSING_CELLS = {"", "—", "－", "–", "-", "‐", "…", "/"}


def _circ_period_from_title(title: str) -> str:
    """Derive the ten-day period label from an article title.

    Returns "YYYY-MM-DD~YYYY-MM-DD" (旬区间) or "" when the title cannot
    be parsed (row stays parseable, period left empty rather than guessed).
    """
    m = _CIRC_PERIOD_RANGE_RE.search(title)
    if m:
        y1, mo1, d1 = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y2 = int(m.group(4)) if m.group(4) else y1
        mo2, d2 = int(m.group(5)), int(m.group(6))
        return f"{y1:04d}-{mo1:02d}-{d1:02d}~{y2:04d}-{mo2:02d}-{d2:02d}"
    m = _CIRC_PERIOD_TEN_RE.search(title)
    if m:
        year, month, ten = int(m.group(1)), int(m.group(2)), m.group(3)
        start_day, end_day = _CIRC_TEN_BOUNDS[ten]
        if end_day is None:  # 下旬 runs to the actual end of the month
            end_day = calendar.monthrange(year, month)[1]
        return (
            f"{year:04d}-{month:02d}-{start_day:02d}"
            f"~{year:04d}-{month:02d}-{end_day:02d}"
        )
    return ""


def _circ_cell_text(cell: Any) -> str:
    return re.sub(r"\s+", " ", (cell.text_content() or "")).strip()


def _circ_is_missing(text: str) -> bool:
    return text in _CIRC_MISSING_CELLS


def _circ_cell_float(text: str) -> float | None:
    if _circ_is_missing(text):
        return None
    return _to_float(text.replace(",", "").replace("，", ""))


def _circ_cell_str(text: str) -> str | None:
    if _circ_is_missing(text):
        return None
    return text


def _parse_circulation_article(
    html_text: str, period: str, scraped_at: str, source_url: str
) -> list[dict[str, Any]]:
    """Parse the inline price table of one article into row dicts.

    Columns are mapped from the actual table header (本期价格/比上期价格涨跌/
    涨跌幅/同比…) so future header additions keep working. The page embeds the
    same table more than once; the densest matching table wins and rows are
    deduplicated on (category, product_name) as a second guard. Cells holding
    「—」/empty become None — no dirty rows are emitted.
    """
    from lxml import html as lxml_html

    try:
        doc = lxml_html.fromstring(html_text)
    except Exception as e:
        logger.warning("circulation article HTML parse failed: %s", e)
        return []

    best_table = None
    best_rows = -1
    best_header: list[str] = []
    for table in doc.xpath("//table"):
        trs = table.xpath(".//tr")
        if not trs:
            continue
        header = [_circ_cell_text(c) for c in trs[0].xpath("./th|./td")]
        if not any("本期价格" in h for h in header):
            continue
        n_data = sum(1 for tr in trs[1:] if len(tr.xpath("./td|./th")) >= 4)
        if n_data > best_rows:
            best_table, best_rows, best_header = table, n_data, header
    if best_table is None:
        logger.warning("circulation article has no price table: %s", source_url)
        return []
    header = best_header

    def col_of(pred) -> int:
        for i, h in enumerate(header):
            if pred(h):
                return i
        return -1

    name_idx = col_of(lambda h: "产品名称" in h or "品名" in h)
    unit_idx = col_of(lambda h: h == "单位" or "单位" in h)
    price_idx = col_of(lambda h: "本期价格" in h)
    change_idx = col_of(lambda h: "比上期" in h and "涨跌" in h and "幅" not in h)
    mom_idx = col_of(lambda h: "涨跌幅" in h or "环比" in h)
    yoy_idx = col_of(lambda h: "同比" in h)
    if name_idx < 0:
        name_idx = 0
    if price_idx < 0:
        logger.warning("circulation table lacks 本期价格 column: %s", source_url)
        return []

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    category = ""
    for tr in best_table.xpath(".//tr")[1:]:
        cells = tr.xpath("./td|./th")
        texts = [_circ_cell_text(c) for c in cells]
        if not any(texts):
            continue
        if len(texts) == 1 or (texts[0] and not any(texts[1:])):
            # category band row, e.g. "一、黑色金属" / single spanning cell
            m = _CIRC_CATEGORY_NUM_RE.match(texts[0])
            if m is not None or len(texts) == 1:
                category = _CIRC_CATEGORY_NUM_RE.sub("", texts[0]).strip()
                continue
        if len(texts) < max(name_idx, price_idx) + 1:
            continue

        product_name = texts[name_idx]
        price = _circ_cell_float(texts[price_idx]) if 0 <= price_idx < len(texts) else None
        change = (
            _circ_cell_float(texts[change_idx])
            if 0 <= change_idx < len(texts)
            else None
        )
        mom = _circ_cell_str(texts[mom_idx]) if 0 <= mom_idx < len(texts) else None
        yoy = _circ_cell_str(texts[yoy_idx]) if 0 <= yoy_idx < len(texts) else None

        if not product_name:
            continue
        if price is None and change is None and mom is None:
            logger.info(
                "circulation row skipped (no parseable values): %s/%s",
                category,
                product_name[:30],
            )
            continue
        key = (category, product_name)
        if key in seen:
            continue
        seen.add(key)

        rows.append(
            {
                "period": period,
                "category": category,
                "product_name": product_name,
                "price": price,
                "price_unit": texts[unit_idx] if 0 <= unit_idx < len(texts) else "",
                "price_change": change,
                "mom_pct": mom,
                "yoy_pct": yoy,
                "scraped_at": scraped_at,
                "source_url": source_url,
            }
        )
    return rows


async def _fetch_circulation_page(session, url: str) -> str:
    """GET one stats.gov.cn page; log-and-skip on any failure (no retries)."""
    try:
        r = await session.get(url, headers=_CIRC_HEADERS)
    except Exception as e:
        logger.warning("circulation fetch failed %s: %s", url, e)
        return ""
    if r.status != 200:
        logger.warning("circulation fetch status=%s %s", r.status, url)
        return ""
    body = r.body
    if not body or len(body) < 500:
        logger.warning("circulation fetch response too short %s", url)
        return ""
    # stats.gov.cn serves UTF-8; Response.text can come back empty under this
    # content-type, so decode the raw body (GBK kept as a legacy fallback).
    for encoding in ("utf-8", "gb18030"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _extract_circ_links(html_text: str) -> list[tuple[str, str]]:
    """Return (article_url, title) pairs whose title matches the keyword."""
    from lxml import html as lxml_html

    try:
        doc = lxml_html.fromstring(html_text)
    except Exception:
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in doc.xpath("//a"):
        title = re.sub(r"\s+", "", a.text_content() or "")
        if CIRC_TITLE_KEYWORD not in title:
            continue
        href = a.get("href") or ""
        if "t" not in href or ".html" not in href:
            continue
        url = urljoin(CIRC_LIST_URL, href)
        if url not in seen:
            seen.add(url)
            out.append((url, re.sub(r"\s+", " ", a.text_content() or "").strip()))
    return out


async def _circulation_prices_async(limit: int) -> list[dict[str, Any]]:
    scraped_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []

    needed = min((limit + 49) // 50 + 1, CIRC_MAX_ISSUES)
    async with FetcherSession(impersonate="chrome120", timeout=25, verify=False) as session:
        candidates: list[tuple[str, str]] = []
        seen_pages: set[str] = set()
        page_url = CIRC_LIST_URL
        for page_no in range(CIRC_MAX_LIST_PAGES):
            if len(candidates) >= needed or len(rows) >= limit:
                break
            if page_url in seen_pages:
                break
            seen_pages.add(page_url)
            listing = await _fetch_circulation_page(session, page_url)
            if not listing:
                return rows[:limit]
            candidates.extend(_extract_circ_links(listing))
            page_url = urljoin(CIRC_LIST_URL, f"index_{page_no + 1}.html")

        if not candidates:
            logger.warning(
                "get_circulation_prices: no %r articles found in %s",
                CIRC_TITLE_KEYWORD,
                CIRC_LIST_URL,
            )
            return []

        for article_url, title in candidates:
            if len(rows) >= limit:
                break
            article = await _fetch_circulation_page(session, article_url)
            if not article:
                continue
            period = _circ_period_from_title(title)
            issue_rows = _parse_circulation_article(
                article, period, scraped_at, article_url
            )
            logger.info(
                "circulation issue %r (%s): %d rows", title, period or "?", len(issue_rows)
            )
            rows.extend(issue_rows)
            await asyncio.sleep(NbsStatsSpider.download_delay)

    return rows[:limit]


def get_circulation_prices(limit: int = 100) -> list[dict[str, Any]]:
    """Fetch 流通领域重要生产资料市场价格变动情况 (ten-day producer goods prices).

    Reads the stats.gov.cn 「最新发布」 (/sj/zxfb/) listing, picks articles
    titled 流通领域重要生产资料市场价格变动情况… (newest first), and parses
    each article's inline HTML table (~50 products, six categories: 黑色金属/
    有色金属/化工产品/能源/农林产品/非金属建材). Missing cells (「—」/empty)
    become None. Returns at most ``limit`` rows, newest issue first; each row
    carries ``scraped_at`` and ``source_url``. Failures are logged and skipped
    (no retries, no proxy — domestic direct connection only).
    """
    if limit <= 0:
        return []
    return asyncio.run(_circulation_prices_async(limit))


def run_nbs_stats(limit: int = 100) -> list[dict]:
    """Entry point for fd-open-data-protocol dispatch (``fd-runner nbs-stats``).

    Merged entry, two independent paths:
      1. Legacy easyquery path (hgjd/hgyd indicators). This source polls the
         NBS easyquery JSON API per (category, indicator): ``NbsStatsSpider``
         has no static ``start_urls`` (query URLs are built with a
         cache-busting timestamp) and its ``parse()`` needs per-request meta,
         so the shared ``run_scrapling_spider`` helper does not fit. This
         drives ``_fetch_nbs_indicator`` directly with the same semantics:
         per-request errors are logged and skipped, the cooperative cancel
         event is honored, and fetching stops once ``limit`` records are
         collected. The whole path is wrapped in try/except so a failure here
         can never break path 2.
      2. Circulation-area producer goods prices (``get_circulation_prices``,
         ten-day stats.gov.cn articles, independent parsing path). Fills the
         row budget left over by path 1; its own failures are likewise
         logged-and-skipped.

    Rows from both paths are concatenated (legacy first) and truncated to
    ``limit``, so the contract "返回 ≤limit 行" always holds.
    """
    from fd_industry_data.cancel_event import is_set as cancel_set

    items: list[dict[str, Any]] = []

    async def fetch_all():
        for category, cat_config in INDICATOR_CATEGORIES.items():
            for indicator in cat_config:
                if cancel_set():
                    logger.info(
                        "cancel requested; stopping early with %d items kept",
                        len(items),
                    )
                    return items
                if len(items) >= limit:
                    return items
                fetched = await _fetch_nbs_indicator(
                    category, indicator, NbsStatsSpider.start_year
                )
                if fetched:
                    items.extend(fetched)
                    logger.info(
                        "run_nbs_stats: %d records for %s/%s",
                        len(fetched),
                        category,
                        indicator,
                    )
                await asyncio.sleep(NbsStatsSpider.download_delay)
        return items

    try:
        legacy_items = asyncio.run(fetch_all())[:limit]
    except Exception as e:  # fail-isolated: legacy path cannot break the merge
        logger.warning("run_nbs_stats: legacy easyquery path failed; skipping: %s", e)
        legacy_items = []

    try:
        circulation_items = get_circulation_prices(limit=max(limit - len(legacy_items), 0))
    except Exception as e:  # fail-isolated: new path cannot break legacy output
        logger.warning("run_nbs_stats: circulation prices path failed; skipping: %s", e)
        circulation_items = []

    return (list(legacy_items) + list(circulation_items))[:limit]


if __name__ == "__main__":
    results = asyncio.run(
        get_nbs_data(
            categories=["gdp", "price_indices", "industrial", "population"],
            start_year=2015,
        )
    )

    print(f"\n{'=' * 70}")
    print(f"NBS Data Fetch Complete")
    print(f"{'=' * 70}")
    print(f"SQLite: {DB_PATH}")
    print(f"JSON files in: {OUTPUT_DIR}")
    print(f"{'=' * 70}")

    for category, items in results.items():
        print(f"\n{category.upper()} ({len(items)} records):")
        if items:
            for item in sorted(items, key=lambda x: x.get("period", ""))[-5:]:
                print(
                    f"  {item['period']:>12s}  {item['value']:>12,.2f}  "
                    f"{item.get('unit', '')}  [{item.get('indicator_type', '')}]"
                )
