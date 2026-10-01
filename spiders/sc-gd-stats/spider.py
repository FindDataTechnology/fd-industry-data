"""sc-gd-stats — 四川省/广东省统计局「月度主要指标」(one unit, two provincial sources).

Sources (domestic, direct connection only — no proxy):
- Sichuan: https://tjj.sc.gov.cn/scstjj/c112117/list.shtml
  (NOTE: the bare directory https://tjj.sc.gov.cn/scstjj/c112117/ returns 403 from
  the site WAF while list.shtml and article pages fetch fine; the WAF also rejects
  plain requests/curl TLS — chrome-impersonated curl_cffi via scrapling works.)
- Guangdong: https://stats.gd.gov.cn/gmjjzyzb/

Each article publishes one regular HTML table; one output row per indicator row.
Guangdong marks unavailable absolutes with「—」→ value=None, growth kept (hard
tolerance rule: never emit dirty numeric rows).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from scrapling.fetchers import FetcherSession

logger = logging.getLogger("sc-gd-stats")

HDRS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

_LINK_RE = re.compile(r"<a\s[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.S)
_TABLE_RE = re.compile(r"<table.*?</table>", re.S)
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_PERIOD_RE = re.compile(r"(\d{4})年(?:1\s*[-—–―]\s*)?(\d{1,2})月")
_EQ100_RE = re.compile(r"[=＝]\s*100")
# ordered longest-first so 亿元 wins over 元, 万吨 over 吨 ...
_UNIT_TOKENS = (
    "亿立方米", "亿千瓦时", "万千瓦时", "万千升", "万辆", "万台", "亿吨",
    "亿元", "万吨", "万元", "吨", "元", "%",
)
_MISS = {"", "-", "—", "－", "――", "…", "⋯", "null", "none"}

_SOURCES = [
    {
        "province": "sichuan",
        "label": "四川省统计局·最新发布（月度主要指标）",
        "source_url": "https://tjj.sc.gov.cn/scstjj/c112117/list.shtml",
        "base_url": "https://tjj.sc.gov.cn",
        "title_re": re.compile(r"四川省国民经济主要指标数据"),
        "n_cols": 5,  # 指标 | 当月值 | 当月同比增长% | 1-N月累计值 | 累计增长%
        "https_only": False,
    },
    {
        "province": "guangdong",
        "label": "广东省统计局·国民经济主要指标",
        "source_url": "https://stats.gd.gov.cn/gmjjzyzb/",
        "base_url": "https://stats.gd.gov.cn",
        "title_re": re.compile(r"广东主要统计指标"),
        "n_cols": 3,  # 指标 | 1-N月值 | 增长%
        # 实测：stats.gd.gov.cn 的 WAF 会掐断带外部 referer（scrapling 默认注入
        # google referer）或 http-scheme 的请求 → 必须带本站 referer 并强制 https
        "https_only": True,
    },
]


def _clean(fragment: str) -> str:
    text = fragment.replace("&ensp;", " ").replace("&nbsp;", " ").replace("&#160;", " ")
    text = _TAG_RE.sub("", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return _WS_RE.sub(" ", text).strip()


def _num(cell: str) -> float | None:
    text = cell.replace(",", "").replace("，", "").strip()
    if not text or text.lower() in _MISS or not re.search(r"\d", text):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _growth(cell: str) -> str | None:
    text = cell.strip()
    if not text or text in _MISS or not re.search(r"\d", text):
        return None
    return text


def _unit(indicator: str) -> str:
    if _EQ100_RE.search(indicator):
        return "上年同期=100"
    for token in _UNIT_TOKENS:
        if token in indicator:
            return token
    return ""


def _period_from_title(title: str) -> str | None:
    m = _PERIOD_RE.search(title)
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        return None
    return f"{year:04d}-{month:02d}"


def _request_url(province: dict, url: str) -> str:
    if province["https_only"] and url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url


async def _get_text(session, province: dict, url: str) -> str:
    url = _request_url(province, url)
    headers = {**HDRS, "Referer": province["base_url"] + "/"}
    resp = await session.get(url, headers=headers)
    if resp.status != 200:
        raise RuntimeError(f"HTTP {resp.status} for {url}")
    body = resp.body
    text = body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) else str(resp.text)
    if not text.strip():
        raise RuntimeError(f"empty body for {url}")
    return text


def _pick_article(list_html: str, title_re: re.Pattern, base_url: str) -> tuple[str, str] | None:
    for href, inner in _LINK_RE.findall(list_html):
        text = _clean(inner)
        if title_re.search(text):
            return urljoin(base_url, href), text
    return None


# monthly cadence → the newest ~13 issues cover the rolling 12-month window
MAX_ISSUES_PER_RUN = 13


def _pick_articles(list_html: str, title_re: re.Pattern, base_url: str,
                   max_n: int = MAX_ISSUES_PER_RUN) -> list[tuple[str, str]]:
    picked: list[tuple[str, str]] = []
    for href, inner in _LINK_RE.findall(list_html):
        text = _clean(inner)
        if title_re.search(text):
            picked.append((urljoin(base_url, href), text))
            if len(picked) >= max_n:
                break
    return picked


def _largest_table(html: str) -> str | None:
    tables = _TABLE_RE.findall(html)
    if not tables:
        return None
    return max(tables, key=len)


async def _fetch_issue(session, province: dict, article_url: str,
                       listing_title: str) -> list[dict]:
    article_url = _request_url(province, article_url)
    article_html = await _get_text(session, province, article_url)

    table = _largest_table(article_html)
    if table is None:
        raise RuntimeError(f"[{province['province']}] no <table> in article {article_url}")

    # prefer the article <title> (stable, carries 期号); fall back to anchor text
    m = re.search(r"<title>(.*?)</title>", article_html, re.S)
    title = _clean(m.group(1)) if m else listing_title
    period = _period_from_title(title)
    if period is None:
        raise RuntimeError(f"[{province['province']}] cannot parse 期号 from title: {title!r}")

    n_cols = province["n_cols"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict] = []
    for tr_idx, tr in enumerate(_ROW_RE.findall(table)):
        cells = [_clean(c) for c in _CELL_RE.findall(tr)]
        if len(cells) < n_cols:
            if cells:  # footnote / note rows carry fewer cells (colspan)
                logger.info("[%s] skip tr#%d colspan note row: %s",
                            province["province"], tr_idx, (cells[0] or "")[:40])
            continue
        indicator = cells[0].replace("　", " ").strip()
        compact = indicator.replace(" ", "")
        if compact in {"指标", "指標"}:  # header row
            continue
        if indicator.startswith("注"):  # footnote row
            continue
        rest = cells[1:n_cols]
        if compact.endswith("：") and not any(re.search(r"\d", c) for c in rest):
            # pure section marker (分经济类型： / 按产业分： ...) — no data, not an indicator
            logger.info("[%s] skip tr#%d section marker %r", province["province"], tr_idx, indicator)
            continue
        if province["province"] == "sichuan":
            value, growth_yoy = _num(rest[0]), _growth(rest[1])
            value_cum, growth_cum = _num(rest[2]), _growth(rest[3])
        else:  # guangdong: the single value column is the 1-N月 value
            value, growth_yoy, value_cum, growth_cum = _num(rest[0]), _growth(rest[1]), None, None
        rows.append({
            "province": province["province"],
            "period": period,
            "indicator": indicator,
            "value": value,
            "unit": _unit(indicator),
            "growth_yoy": growth_yoy,
            "value_cum": value_cum,
            "growth_cum": growth_cum,
            "row_index": tr_idx,
            "title": title,
            "url": article_url,
            "scraped_at": now,
            "source_url": province["source_url"],
        })
    logger.info("[%s] %s: %d indicator rows (period %s)",
                province["province"], title, len(rows), period)
    return rows


async def _fetch_province(session, province: dict) -> list[dict]:
    list_html = await _get_text(session, province, province["source_url"])
    issues = _pick_articles(list_html, province["title_re"], province["base_url"])
    if not issues:
        raise RuntimeError(f"[{province['province']}] no monthly-indicator article found in listing")

    rows: list[dict] = []
    seen_periods: set[str] = set()
    for article_url, listing_title in issues:
        try:
            issue_rows = await _fetch_issue(session, province, article_url, listing_title)
        except Exception as exc:  # noqa: BLE001 — log-and-skip per issue, keep the rest
            logger.error("[%s] issue %s failed, skipped: %s: %s",
                         province["province"], article_url, type(exc).__name__, exc)
            continue
        if issue_rows and issue_rows[0]["period"] in seen_periods:
            continue  # same 期号 surfaced by several listing variants
        if issue_rows:
            seen_periods.add(issue_rows[0]["period"])
        rows.extend(issue_rows)
    if not rows:
        raise RuntimeError(f"[{province['province']}] all {len(issues)} listing issues failed to parse")
    return rows


async def _run(limit: int) -> list[dict]:
    collected: list[dict] = []
    async with FetcherSession(impersonate="chrome120", timeout=30, verify=False) as session:
        for province in _SOURCES:
            try:
                collected.extend(await _fetch_province(session, province))
            except Exception as exc:  # noqa: BLE001 — contract: log-and-skip, no retry storms
                logger.error("[%s] fetch/parse failed, skipped: %s: %s",
                             province["province"], type(exc).__name__, exc)
    return collected[:limit]


def run_sc_gd_stats(limit: int = 100) -> list[dict]:
    """Fetch the latest monthly key-indicator tables for Sichuan + Guangdong.

    One row per published indicator row; `province` distinguishes the two sources.
    Returns at most `limit` rows.
    """
    if limit is None or limit <= 0:
        limit = 100
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_run(limit))
    # invoked from inside a running event loop (e.g. embedded runner) — offload
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _run(limit)).result()
