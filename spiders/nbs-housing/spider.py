"""nbs-housing — 国家统计局房地产口径数据（70 城房价指数 + 全国房地产市场基本情况）.

Source: 国家统计局「最新发布」列表页 https://www.stats.gov.cn/sj/zxfb/
(HTML tables inside press articles — NOT the data.stats.gov.cn easyquery JSON
API, which the sibling unit spiders/nbs-stats covers; the two shapes differ and
the units are deliberately kept separate).

Live data paths (measured 2026-10-12):
  1. GET  https://www.stats.gov.cn/sj/zxfb/index.html (+ index_1.html …)
        -> 「最新发布」listing, newest first. Article links are relative
           (./YYYYMM/tYYYYMMDD_<id>.html), 67 pages at the time of writing.
  2. Article 「YYYY年M月份70个大中城市商品住宅销售价格变动情况」
        -> the page carries 12 DOM <table> elements = 6 distinct tables in the
           PC container (div.detail-text-content) + the same 6 repeated in the
           mobile container (div.mobile-content). Measured 2026-08 article:
             表1 新建商品住宅销售价格指数      (城市/环比/同比/N月平均, 2 cities per row)
             表2 二手住宅销售价格指数          (same shape)
             表3 新建商品住宅销售价格分类指数（一/二）  (90m2 buckets)
             表4 二手住宅销售价格分类指数（一/二）      (90m2 buckets)
  3. Article 「YYYY年1—N月份全国房地产市场基本情况」
        -> 3 PC tables; 表1 指标/绝对量/同比增长（%） is the national monthly
           series (房地产开发投资 / 施工面积 / 新开工 / 竣工 / 销售 / 待售 /
           到位资金 …), 表2/表3 are 东中西部和东北地区 breakdowns.

Semantics actually implemented (deliberately narrow — no guessing):
  * Only 表1/表2 of the 70-city article are emitted: 城市 + 环比 + 同比 +
    N月平均 map 1:1 onto the designed columns (city/housing_type/period/
    mom/yoy/ytd_avg). The 分类指数 tables (表3/表4) use a different schema
    (area buckets, no cumulative column in the January release) and are
    skipped with a trace rather than flattened into a wrong semantic.
  * Only 表1 of the real-estate article is emitted (national indicators);
    the two regional tables are skipped (the designed columns carry no
    region axis) and the article prose is used only as a fallback when the
    national table cannot be parsed.

Known traps handled here (see README for the measured evidence):
  * City names are whitespace-padded in HTML (「北　　京」 U+3000, 「秦 皇 岛」)
    and are normalised by stripping all whitespace.
  * A missing cell (「—」 / empty) becomes None — never 0.
  * The January release drops the cumulative column, so block width is
    derived from the header (two side-by-side blocks) instead of hardcoded.
  * Column titles are read from the table's own <p> caption; unclassifiable
    tables are skipped with a log line.

No auth, no captcha. Domestic source, direct connection only (never proxied);
bare browser UA measured 200. TLS verify is off per the sibling NBS path
(spiders/nbs-stats) so the unit works regardless of the container trust store.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

from lxml import html as lxml_html
from scrapling.fetchers import FetcherSession

logger = logging.getLogger("nbs-housing")

SOURCE = "nbs-housing"
LIST_URL = "https://www.stats.gov.cn/sj/zxfb/"
MAX_LIST_PAGES = 4  # newest-first listing pages scanned for the two article kinds

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# listing: article links are ./YYYYMM/tYYYYMMDD_<id>.html
_ARTICLE_HREF_RE = re.compile(r"^\.?/?\d{6}/t\d+_\d+\.html$")
_CITY70_TITLE_RE = re.compile(r"70个大中城市商品住宅销售价格")
_MARKET_TITLE_RE = re.compile(r"全国房地产市场基本情况")

# 70-city article titles: 2026年8月份70个大中城市商品住宅销售价格变动情况
_CITY70_PERIOD_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月份")
# real-estate titles: 2026年1—8月份… (cumulative window) / 2025年… (annual)
_MARKET_YTD_PERIOD_RE = re.compile(
    r"(\d{4})\s*年\s*1\s*[—–\-~～至]\s*(\d{1,2})\s*月份"
)
# annual release title: 2025年全国房地产市场基本情况 (year then the phrase directly;
# a 上半年 form would not match and is skipped rather than mislabelled)
_MARKET_ANNUAL_PERIOD_RE = re.compile(r"^(\d{4})\s*年全国房地产市场基本情况")

# a table caption inside the article body: 「表1：2026年8月70个大中城市…」
_TABLE_CAPTION_RE = re.compile(r"表\s*\d+\s*[：:]")

# 表1/表2 header cell -> output field
_METRIC_FIELDS = (("环比", "mom"), ("同比", "yoy"), ("月平均", "ytd_avg"))

# city cells: CJK only after whitespace stripping (北　京 -> 北京)
_CITY_RE = re.compile(r"^[\u4e00-\u9fff]{2,8}$")

# missing-value markers seen in NBS tables; never coerced to 0
_MISSING_CELLS = {"", "—", "－", "–", "-", "‐", "…", "/", "nan", "NaN"}

_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_UNIT_RE = re.compile(r"[（(]\s*(亿元|万平方米)\s*[)）]")


def _decode(body: bytes) -> str:
    """NBS article pages declare UTF-8; sniff defensively for the odd GBK page."""
    for enc in ("utf-8", "gb18030"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _norm(text: str) -> str:
    """Strip ALL whitespace (incl. U+3000) — city cells are padded with it."""
    return re.sub(r"\s+", "", text or "")


def _squash(text: str) -> str:
    """Collapse whitespace to single spaces (for titles/URL labels)."""
    return re.sub(r"\s+", " ", text or "").strip()


def _to_float(text: str) -> float | None:
    """Numeric cell -> float; missing marker / unparsable -> None (never 0)."""
    t = _norm(text).replace(",", "").replace("，", "")
    if t in _MISSING_CELLS:
        return None
    if t.endswith("%"):
        t = t[:-1]
    return float(t) if _NUM_RE.match(t) else None


def _cell_texts(tr) -> list[str]:
    return [_norm(c.text_content()) for c in tr.xpath("./td|./th")]


def _table_caption(table) -> str:
    """Nearest preceding 「表N：…」 caption text for a table element.

    Measured layout: <p>表1：…</p><p></p><div class=ue_table><table>…, so the
    caption is a preceding sibling of the wrapper (or of an ancestor).
    """
    node = table
    for _ in range(5):
        prev = node.getprevious()
        while prev is not None:
            txt = _squash(prev.text_content() or "")
            if _TABLE_CAPTION_RE.search(txt):
                return txt
            prev = prev.getprevious()
        node = node.getparent()
        if node is None:
            break
    return ""


def _pc_tables(doc) -> list:
    """Tables in the PC article container; all tables if that container is gone.

    The PC and mobile containers hold the same tables, so restricting to the
    PC container de-duplicates the DOM; a title-keyed de-dup is applied on
    top as a second guard.
    """
    pc = doc.xpath('//div[contains(@class, "detail-text-content")]//table')
    return pc if pc else doc.xpath("//table")


# ---------------------------------------------------------------------------
# listing discovery
# ---------------------------------------------------------------------------
def _parse_listing(page_html: str) -> list[dict]:
    """Article links + titles from one listing page, in page order."""
    doc = lxml_html.fromstring(page_html)
    out: list[dict] = []
    seen: set[str] = set()
    for a in doc.xpath("//a[@href]"):
        href = (a.get("href") or "").strip()
        if not _ARTICLE_HREF_RE.match(href):
            continue
        title = _squash(a.text_content())
        if not title:
            continue
        url = urljoin(LIST_URL, href)
        if url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "title": title})
    return out


def _pick_articles(entries: list[dict]) -> dict[str, dict]:
    """First (newest) 70-city and real-estate article from a listing slice."""
    picked: dict[str, dict] = {}
    for e in entries:
        if "70city" not in picked and _CITY70_TITLE_RE.search(e["title"]):
            picked["70city"] = e
        if "market" not in picked and _MARKET_TITLE_RE.search(e["title"]):
            picked["market"] = e
        if len(picked) == 2:
            break
    return picked


async def _discover(session, want: set[str]) -> dict[str, dict]:
    """Scan the newest-first listing for the wanted article kinds."""
    found: dict[str, dict] = {}
    for page in range(MAX_LIST_PAGES):
        if want <= set(found):
            break
        url = LIST_URL if page == 0 else urljoin(LIST_URL, f"index_{page}.html")
        try:
            resp = await session.get(url, headers=_HEADERS)
        except Exception as e:  # noqa: BLE001 — listing page failure = log-and-skip
            logger.warning("listing fetch failed %s: %s — skip page", url, e)
            continue
        if resp.status != 200:
            logger.warning("listing status=%s %s — skip page", resp.status, url)
            continue
        page_picks = _pick_articles(_parse_listing(_decode(resp.body)))
        for kind, entry in page_picks.items():
            if kind in want and kind not in found:
                found[kind] = entry
                logger.info("discovered %s: %s (%s)", kind, entry["title"], entry["url"])
    missing = want - set(found)
    if missing:
        logger.warning("no article found for %s within %d listing pages",
                       ",".join(sorted(missing)), MAX_LIST_PAGES)
    return found


# ---------------------------------------------------------------------------
# 70-city price-index article
# ---------------------------------------------------------------------------
def _housing_type(caption: str) -> str | None:
    """Table caption -> 新建商品住宅 / 二手住宅; None = skip (log at caller)."""
    if "分类指数" in caption:  # 表3/表4: area-bucket schema, not this unit's columns
        return None
    if "新建商品住宅销售价格指数" in caption:
        return "新建商品住宅"
    if "二手住宅销售价格指数" in caption:
        return "二手住宅"
    return None


def _parse_city_index_table(table, caption: str, housing_type: str,
                            period: str, report_title: str, report_url: str,
                            scraped_at: str) -> list[dict]:
    """Parse one 表1/表2 (two side-by-side city blocks) into row dicts.

    Block width comes from the header's two 「城市」 cells, so the January
    release (no cumulative column) parses with the same code. Rows whose
    shape or city cell does not fit the measured layout are dropped.
    """
    trs = table.xpath(".//tr")
    if len(trs) < 3:
        return []
    header = _cell_texts(trs[0])
    city_pos = [i for i, h in enumerate(header) if h == "城市"]
    if len(city_pos) != 2:
        logger.warning("70city: table %r header has %d 城市 cells — skip",
                       caption, len(city_pos))
        return []
    width = city_pos[1] - city_pos[0]
    if width < 3 or len(header) != 2 * width:
        logger.warning("70city: table %r unexpected header width — skip: %s",
                       caption, header)
        return []

    # metric column index within a block -> output field
    fields: dict[int, str] = {}
    for i in range(1, width):
        for needle, field in _METRIC_FIELDS:
            if needle in header[i] and field not in fields.values():
                fields[i] = field
                break
    if "mom" not in fields.values():
        logger.warning("70city: table %r has no 环比 column — skip: %s", caption, header)
        return []

    rows: list[dict] = []
    for tr in trs[1:]:
        cells = _cell_texts(tr)
        if len(cells) != 2 * width:  # unit sub-header / note rows
            continue
        for base in (0, width):
            city = cells[base]
            if not _CITY_RE.match(city):
                continue
            row = {
                "city": city,
                "housing_type": housing_type,
                "period": period,
                "mom": None,
                "yoy": None,
                "ytd_avg": None,
                "report_title": report_title,
                "report_url": report_url,
                "source": SOURCE,
                "scraped_at": scraped_at,
            }
            for i, field in fields.items():
                row[field] = _to_float(cells[base + i])
            rows.append(row)
    return rows


def _parse_city70_article(page_html: str, title: str, url: str) -> list[dict]:
    """Parse the whole 70-city article: 表1/表2 only, 分类指数 skipped."""
    m = _CITY70_PERIOD_RE.search(title)
    if not m:
        logger.warning("70city: cannot parse period from title %r — skip article", title)
        return []
    period = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    scraped_at = datetime.now(timezone.utc).isoformat()

    try:
        doc = lxml_html.fromstring(page_html)
    except Exception as e:  # noqa: BLE001
        logger.warning("70city: HTML parse failed: %s", e)
        return []

    rows: list[dict] = []
    seen_tables: set[str] = set()
    for table in _pc_tables(doc):
        caption = _table_caption(table)
        if not caption or caption in seen_tables:
            continue
        seen_tables.add(caption)
        housing_type = _housing_type(caption)
        if housing_type is None:
            logger.info("70city: table %r skipped (schema not in this unit's columns)",
                        caption)
            continue
        parsed = _parse_city_index_table(
            table, caption, housing_type, period, title, url, scraped_at
        )
        if not parsed:
            logger.warning("70city: table %r produced no rows — skip", caption)
            continue
        rows.extend(parsed)
    logger.info("70city: %d rows, period=%s, tables=%d", len(rows), period, len(seen_tables))
    return rows


# ---------------------------------------------------------------------------
# national real-estate monthly article
# ---------------------------------------------------------------------------
def _market_period(title: str) -> str:
    """Title -> period label. Cumulative months -> YYYY-01~YYYY-MM; annual -> YYYY."""
    m = _MARKET_YTD_PERIOD_RE.search(title)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        return f"{year:04d}-01~{year:04d}-{month:02d}"
    m = _MARKET_ANNUAL_PERIOD_RE.match(title)
    if m:
        return m.group(1)
    return ""


def _split_indicator(label: str) -> tuple[str, str]:
    """「房地产开发投资（亿元）」 -> ("房地产开发投资", "亿元")."""
    m = _UNIT_RE.search(label)
    if m:
        return _norm(label[: m.start()]), m.group(1)
    return _norm(label), ""


def _parse_market_table(table, period: str, url: str, scraped_at: str) -> list[dict]:
    """Parse 表1 指标/绝对量/同比增长（%） into national indicator rows.

    Sub-rows (其中：住宅 / 办公楼 / 商业营业用房 …) inherit the current
    section, so the emitted indicator stays unambiguous
    (e.g. 房地产开发投资·住宅). Rows without a parseable absolute value are
    dropped — a missing figure is never written as 0.
    """
    trs = table.xpath(".//tr")
    if len(trs) < 2:
        return []
    header = _cell_texts(trs[0])
    if len(header) != 3 or header[0] != "指标":
        return []
    value_hdr, yoy_hdr = header[1], header[2]
    if "绝对量" not in value_hdr or "增长" not in yoy_hdr:
        return []
    yoy_is_annual = "比上年" in yoy_hdr

    rows: list[dict] = []
    section = ""
    unit = ""
    for tr in trs[1:]:
        cells = _cell_texts(tr)
        if len(cells) != 3:
            continue
        label = cells[0]
        if not label or label.startswith("注"):
            continue
        name, row_unit = _split_indicator(label)
        if row_unit:  # a section header row carries its own unit
            section, unit = name, row_unit
        elif section:
            name = f"{section}·{name.replace('其中：', '')}"
        else:
            continue  # sub-row before any section — cannot attribute it
        value = _to_float(cells[1])
        if value is None:
            logger.warning("market: %s has no absolute value (%r) — row skipped",
                           name, cells[1])
            continue
        rows.append(
            {
                "indicator": name,
                "period": period,
                "value": value,
                "unit": unit,
                "yoy": _to_float(cells[2]),
                "report_url": url,
                "source": SOURCE,
                "scraped_at": scraped_at,
            }
        )
    if rows and yoy_is_annual:
        logger.info("market: annual release (比上年增长), %d rows", len(rows))
    return rows


# headline sentences of the same release, used only when 表1 is unparseable
_PROSE_PATTERNS = (
    ("房地产开发投资", re.compile(r"全国房地产开发投资(\d+(?:\.\d+)?)亿元")),
    ("房屋施工面积", re.compile(r"房屋施工面积(\d+(?:\.\d+)?)万平方米")),
    ("房屋新开工面积", re.compile(r"房屋新开工面积(\d+(?:\.\d+)?)万平方米")),
    ("房屋竣工面积", re.compile(r"房屋竣工面积(\d+(?:\.\d+)?)万平方米")),
    ("新建商品房销售面积", re.compile(r"新建商品房销售面积(\d+(?:\.\d+)?)万平方米")),
    ("新建商品房销售额", re.compile(r"新建商品房销售额(\d+(?:\.\d+)?)亿元")),
    ("房地产开发企业到位资金", re.compile(r"房地产开发企业到位资金(\d+(?:\.\d+)?)亿元")),
)
_PROSE_YOY_RE = re.compile(r"(?:同比)?(?:下降|上涨|增长)(\d+(?:\.\d+)?)%")


def _parse_market_prose(page_html: str, period: str, url: str,
                        scraped_at: str) -> list[dict]:
    """Fallback: core indicators from the release's lead sentences."""
    doc = lxml_html.fromstring(page_html)
    text = _norm("".join(doc.xpath("//div[contains(@class,'detail-text-content')]//p//text()")))
    if not text:
        text = _norm("".join(doc.xpath("//p//text()")))
    rows: list[dict] = []
    for name, pattern in _PROSE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        unit = "亿元" if "亿元" in pattern.pattern else "万平方米"
        yoy_m = _PROSE_YOY_RE.search(text, m.end())
        rows.append(
            {
                "indicator": name,
                "period": period,
                "value": float(m.group(1)),
                "unit": unit,
                "yoy": float(yoy_m.group(1)) if yoy_m else None,
                "report_url": url,
                "source": SOURCE,
                "scraped_at": scraped_at,
            }
        )
    if rows:
        logger.info("market: prose fallback produced %d rows", len(rows))
    return rows


def _parse_market_article(page_html: str, title: str, url: str) -> list[dict]:
    """Parse the national real-estate release: 表1, else prose fallback."""
    period = _market_period(title)
    if not period:
        logger.warning("market: cannot parse period from title %r — skip article", title)
        return []
    scraped_at = datetime.now(timezone.utc).isoformat()
    try:
        doc = lxml_html.fromstring(page_html)
    except Exception as e:  # noqa: BLE001
        logger.warning("market: HTML parse failed: %s", e)
        return []

    rows: list[dict] = []
    for table in _pc_tables(doc):
        parsed = _parse_market_table(table, period, url, scraped_at)
        if parsed:
            rows = parsed
            break
    if not rows:
        logger.warning("market: national table not parsed for %s — trying prose", url)
        rows = _parse_market_prose(page_html, period, url, scraped_at)
    logger.info("market: %d rows, period=%s", len(rows), period)
    return rows


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
async def _collect(limit: int, dataset: str) -> list[dict]:
    want: set[str] = set()
    if dataset in ("all", "70city"):
        want.add("70city")
    if dataset in ("all", "market"):
        want.add("market")
    if not want:
        logger.warning("unknown dataset %r — nothing to fetch", dataset)
        return []

    rows: list[dict] = []
    async with FetcherSession(
        impersonate="chrome120",
        timeout=30,
        verify=False,  # match spiders/nbs-stats: container trust store is not assumed
        retries=1,     # exactly one attempt — no retry storms
    ) as session:
        articles = await _discover(session, want)
        parsed_by_kind: dict[str, list[dict]] = {}
        for kind in ("70city", "market"):
            entry = articles.get(kind)
            if entry is None:
                continue
            try:
                resp = await session.get(entry["url"], headers=_HEADERS)
            except Exception as e:  # noqa: BLE001 — per-article log-and-skip
                logger.warning("article fetch failed %s: %s — skip", entry["url"], e)
                continue
            if resp.status != 200:
                logger.warning("article status=%s %s — skip", resp.status, entry["url"])
                continue
            body = _decode(resp.body)
            if len(body) < 2000:
                logger.warning("article response too short (%d bytes) %s — skip",
                               len(body), entry["url"])
                continue
            try:
                if kind == "70city":
                    parsed = _parse_city70_article(body, entry["title"], entry["url"])
                else:
                    parsed = _parse_market_article(body, entry["title"], entry["url"])
            except Exception as e:  # noqa: BLE001 — one article cannot kill the other
                logger.warning("%s parse failed %s: %s", kind, entry["url"], e)
                continue
            parsed_by_kind[kind] = parsed

        rows = _allocate(parsed_by_kind, want, limit)
    logger.info("nbs-housing: %d rows returned (limit=%d, dataset=%s)",
                len(rows), limit, dataset)
    return rows


def _interleave_by_housing_type(rows: list[dict]) -> list[dict]:
    """Round-robin the 70-city rows across housing types (新建/二手 …).

    Rows arrive table by table (all 70 新建 first, then all 70 二手), so a
    truncated slice would silently show one housing type only. Interleaving
    keeps every type present at any limit without changing the row set.
    """
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        buckets.setdefault(row.get("housing_type") or "", []).append(row)
    if len(buckets) < 2:
        return rows
    out: list[dict] = []
    idx = 0
    while True:
        progressed = False
        for bucket in buckets.values():
            if idx < len(bucket):
                out.append(bucket[idx])
                progressed = True
        if not progressed:
            return out
        idx += 1


def _allocate(parsed: dict[str, list[dict]], want: set[str], limit: int) -> list[dict]:
    """Merge dataset slices under `limit`, giving each side a fair share.

    A single 70-city release carries 140 rows, so a naive concatenation would
    let it consume every small limit and hide the market dataset entirely.
    With both datasets wanted, the market side (the smaller release) gets up
    to half; any share it cannot use flows back to the 70-city side. Each
    side is interleaved so every housing type / section is represented.
    """
    if len(want) < 2:
        kind = next(iter(want)) if want else ""
        return parsed.get(kind, [])[:limit]
    city_rows = _interleave_by_housing_type(parsed.get("70city", []))
    market_rows = parsed.get("market", [])
    market_take = min(len(market_rows), limit // 2)
    city_take = min(len(city_rows), limit - market_take)
    market_take = min(len(market_rows), limit - city_take)
    return city_rows[:city_take] + market_rows[:market_take]


def run_nbs_housing(limit: int = 200, dataset: str = "all") -> list[dict]:
    """Entry point for fd-runner (spiders/nbs-housing).

    Args:
        limit: maximum rows to return (hard cap applied after collection).
        dataset: "all" (default) = 70-city indices + national real-estate
            indicators; "70city" / "market" fetch only that release.

    Returns:
        list of row dicts (schema = manifest columns). Rows are real parsed
        values only: a missing cell is None, never 0, and an unparsable
        table/indicator is skipped with a log line rather than filled in.
    """
    try:  # optional: only present inside the runner
        from fd_industry_data.cancel_event import is_set as cancel_set
    except ImportError:
        cancel_set = lambda: False  # noqa: E731

    cap = max(int(limit), 0)
    if cap == 0:
        return []

    async def _run() -> list[dict]:
        if cancel_set():
            logger.info("cancel requested before start; returning empty")
            return []
        return await _collect(cap, (dataset or "all").strip().lower())

    try:
        return asyncio.run(_run())[:cap]
    except Exception as e:  # noqa: BLE001 — log-and-skip, never retry-loop
        logger.warning("nbs-housing: fetch failed, returning no rows: %s", e)
        return []


if __name__ == "__main__":  # smoke: python3 spiders/nbs-housing/spider.py
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    print(json.dumps(run_nbs_housing(limit=6), ensure_ascii=False, indent=2))
