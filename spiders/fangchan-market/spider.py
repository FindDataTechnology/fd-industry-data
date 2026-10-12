"""fangchan-market — 中国房地产业协会「中房网」房地产市场监测报告.

Source: 中国房地产业协会 official site 中房网 (www.fangchan.com),
原创频道 https://www.fangchan.com/news/320/

Live data paths (measured 2026-10-13, UTC+8):
  1. GET  http://news.fangchan.com/api/newslist.json
            ?subcolumn=原创&page=1&pagesize=40          (JSON, charset UTF-8)
        -> {"status":…, "total":898, "data":[
              {"title": "9月重点城市新房成交环比企稳，二手房交易同比保持正增长",
               "url":   "http://www.fangchan.com/news/320/2026-10-12/7515241843282744309.html",
               "createtime": "1791773281",   # unix 秒
               "media": "中房网", "zhaiyao": "…", "keyword": "新房,二手房",
               "pic": "https://ess.leju.com/…png"}, …]}
        实测栏目 total：原创 898 / 政策 7011 / 市场 3742；
        **数据/研究/年鉴/快报/测评/报告 六个栏目实测 total=0**，故白名单只列前三。
  2. GET  <url>  详情页（www.fangchan.com）
        -> 正文；实测 2026-10-12 一文 8,989 字，含
           「9月全国重点50城新建商品住宅新增供应面积约1368万平方米，环比大增103.6%」
           「成交面积约1020万平方米，环比增长2.4%，同比下降20.1%」。

**为什么走 JSON API 而不是频道页 HTML**：频道页 `/news/320/` 的主列表
`<ul class="item-news-list"></ul>` 是 JS 填充的空壳（旁边的 `<div class="page">`
同样为空），服务端 HTML 里只有右栏「更多」区块的 8 条时间线；且五种分页路径
（`index_2.html` / `list_2.html` / `page/2.html` / `index_1.html` / `index_p2.html`）
实测全部 404。API 是唯一可靠通道，且条目直接带 `url`，无需二次拼接。
API 不可用时退化为频道页右栏时间线（最多 8 条），并在日志留痕，不静默归零。

无鉴权、无 cookie、无验证码；裸 UA 可达（与乘联会不同，本源**不需要**指纹伪装）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse
from datetime import datetime, timedelta, timezone

from lxml import html as lxml_html
from scrapling.fetchers import FetcherSession

logger = logging.getLogger("fangchan-market")

API_BASE = "http://news.fangchan.com/api/newslist.json"
CHANNEL_URL = "http://www.fangchan.com/news/320/"
CHANNEL_BASE = "http://www.fangchan.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 栏目白名单：仅这三个实测有内容（其余六个 total=0，见模块 docstring）
SUPPORTED_SUBCOLUMNS = ("原创", "政策", "市场")
DEFAULT_SUBCOLUMN = "原创"

# 正文页容器（实测）：详情页正文段落在 div.summary-text-p 内，每段一个 <p>。
# 该容器**不含**标题/作者/日期/来源/分享控件（那些在容器外的 p 里）。
# 若容器缺失，逐级回退到通用候选；仍不够长则返回 None（不猜）。
_BODY_XPATHS = (
    '//div[contains(@class, "summary-text-p")]',
    '//div[contains(@class, "zw-con")]',
    '//div[contains(@class, "article-content")]',
    '//div[contains(@class, "content")]',
    '//div[contains(@class, "detail")]',
    '//article',
)
# 剥离残留的分享/二维码/相关阅读块（这些 iframe/div 带文本，会污染正文）
_BODY_NOISE_XPATHS = (
    './/div[contains(@class, "wechat-share-pic")]',
    './/div[contains(@class, "tubiaofenxian")]',
    './/div[contains(@class, "related")]',
    './/div[contains(@class, "tuijian")]',
)
# 频道页右栏时间线条目（API 失败时的退化通道）
_CHANNEL_ITEM_RE = re.compile(
    r"<a href='(/news/\d+/\d{4}-\d{2}-\d{2}/\d+\.html)'[^>]*class='zw-one'[^>]*>([^<]+)</a>"
)
_MIN_BODY_CHARS = 120


def _squash(text: str) -> str:
    """空白（含 \\xa0 / &emsp; 解码残留）折叠为单空格。"""
    return re.sub(r"[\s\u00a0\u2003]+", " ", text or "").strip()


def _iso_date(unix_seconds: str | int | None) -> str | None:
    """createtime（unix 秒，字符串）→ 'YYYY-MM-DD'；不可解析记 None。"""
    if unix_seconds in (None, "", "0"):
        return None
    try:
        ts = int(unix_seconds)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    # 站点发布时刻为东八区；按 UTC+8 落日期，与站点展示一致
    return datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8))).strftime(
        "%Y-%m-%d"
    )


def _api_url(subcolumn: str, page: int, page_size: int) -> str:
    return API_BASE + "?" + urllib.parse.urlencode(
        {"subcolumn": subcolumn, "page": page, "pagesize": page_size}
    )


def _channel_fallback(page_html: str) -> list[dict]:
    """频道页右栏时间线退化通道（API 不可用时）。最多 8 条，无 total。"""
    items: list[dict] = []
    seen: set[str] = set()
    for href, title in _CHANNEL_ITEM_RE.findall(page_html):
        url = href if href.startswith("http") else CHANNEL_BASE + href
        if url in seen:
            continue
        seen.add(url)
        items.append(
            {
                "title": _squash(title),
                "url": url,
                "publish_date": None,
                "summary": None,
                "keyword": None,
                "pic_url": None,
                "article_id": href.rsplit("/", 1)[-1].removesuffix(".html"),
            }
        )
    return items


def _parse_api_payload(raw: bytes | str) -> tuple[int, list[dict]]:
    """解析 newslist.json：返回 (total, items)。

    非 JSON、status 非成功、或 data 非列表时抛 ValueError 由调用方处置
    （绝不把错误载荷当空列表静默吞掉）。
    """
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"newslist.json is not JSON: {e}") from e
    if not isinstance(payload, dict):
        raise ValueError("newslist.json root is not an object")
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("newslist.json .data is not a list")
    total = payload.get("total")
    try:
        total = int(total)
    except (TypeError, ValueError):
        total = len(data)
    items: list[dict] = []
    for it in data:
        if not isinstance(it, dict):
            continue
        url = (it.get("url") or "").strip()
        title = _squash(it.get("title") or "")
        if not url or not title:
            continue
        items.append(
            {
                "title": title,
                "url": url,
                "publish_date": _iso_date(it.get("createtime")),
                "summary": _squash(it.get("zhaiyao") or "") or None,
                "keyword": _squash(it.get("keyword") or "") or None,
                "pic_url": (it.get("pic") or "").strip() or None,
                "article_id": url.rsplit("/", 1)[-1].removesuffix(".html"),
            }
        )
    return total, items


def _extract_body(page_html: str) -> str | None:
    """取详情页正文纯文本；所有候选容器都过短时返回 None（不猜）。"""
    doc = lxml_html.fromstring(page_html)
    lxml_html.etree.strip_elements(
        doc, "script", "style", "nav", "footer", "header", with_tail=False
    )
    best = ""
    for xp in _BODY_XPATHS:
        nodes = doc.xpath(xp)
        for node in nodes:
            for noise in _BODY_NOISE_XPATHS:
                for el in node.xpath(noise):
                    parent = el.getparent()
                    if parent is not None:
                        parent.remove(el)
            text = _squash(node.text_content())
            if len(text) > len(best):
                best = text
        if len(best) >= _MIN_BODY_CHARS:
            break
    if len(best) < _MIN_BODY_CHARS:
        return None
    return best


async def _fetch(session: FetcherSession, url: str):
    """单次 GET；异常/非 200 返回 None（记 warning，不重试、不绕反爬）。"""
    try:
        resp = await session.get(
            url, headers={"User-Agent": USER_AGENT, "Referer": CHANNEL_URL}
        )
    except Exception as e:  # noqa: BLE001 — per-URL log-and-skip
        logger.warning("fetch failed %s: %s — skip", url, e)
        return None
    if resp.status != 200:
        logger.warning("status=%s %s — skip", resp.status, url)
        return None
    return resp


async def _collect(
    limit: int,
    max_pages: int,
    subcolumn: str,
    page_size: int,
) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    reports_done = 0
    reports_failed = 0
    pages_walked = 0
    scraped_at = datetime.now(timezone.utc).isoformat()
    used_fallback = False

    async with FetcherSession(
        impersonate=None,   # 本源裸 UA 可达（实测）；不伪装
        timeout=25,
        verify=False,       # estate convention
        retries=1,          # exactly one attempt — no retry storms
    ) as session:
        page_no = 1
        total: int | None = None
        while True:
            items: list[dict] = []
            resp = await _fetch(session, _api_url(subcolumn, page_no, page_size))
            if resp is None:
                if page_no == 1:
                    # API 全挂：退化到频道页右栏时间线（最多 8 条）
                    logger.warning(
                        "newslist.json unavailable — falling back to channel timeline"
                    )
                    cresp = await _fetch(session, CHANNEL_URL)
                    if cresp is None:
                        logger.error("channel page also unavailable — returning 0 rows")
                        return rows
                    items = _channel_fallback(
                        cresp.body.decode("utf-8", errors="replace")
                    )
                    used_fallback = True
                    pages_walked += 1
                else:
                    break
            else:
                try:
                    total, items = _parse_api_payload(resp.body)
                except ValueError as e:
                    logger.warning("payload rejected: %s — stop paging", e)
                    break
                pages_walked += 1

            if not items:
                break
            if page_no == 1:
                logger.info(
                    "subcolumn=%s total=%s first page items=%d",
                    subcolumn, total, len(items),
                )

            for item in items:
                if len(rows) >= limit:
                    break
                if item["url"] in seen:
                    continue
                seen.add(item["url"])
                dresp = await _fetch(session, item["url"])
                if dresp is None:
                    reports_failed += 1
                    continue
                body = _extract_body(dresp.body.decode("utf-8", errors="replace"))
                if body is None:
                    logger.warning("no body container for %s — skip", item["url"])
                    reports_failed += 1
                    continue
                reports_done += 1
                rows.append(
                    {
                        "report_title": item["title"],
                        "publish_date": item["publish_date"],
                        "article_id": item["article_id"],
                        "content": body,
                        "summary": item["summary"],
                        "keyword": item["keyword"],
                        "pic_url": item["pic_url"],
                        "source": "fangchan-market",
                        "scraped_at": scraped_at,
                    }
                )

            if len(rows) >= limit:
                break
            page_no += 1
            if page_no > min(max_pages, 1 if used_fallback else max_pages):
                break
            if used_fallback:
                break  # 退化通道无分页概念

    logger.info(
        "fangchan-market: %d rows from %d reports (%d failed) over %d list page(s)%s",
        len(rows), reports_done, reports_failed, pages_walked,
        " [channel fallback]" if used_fallback else "",
    )
    return rows


def run_fangchan_market(
    limit: int = 50,
    max_pages: int = 2,
    subcolumn: str = DEFAULT_SUBCOLUMN,
    page_size: int = 40,
) -> list[dict]:
    """Entry point for fd-runner (spiders/fangchan-market).

    Args:
        limit: maximum rows to return (hard cap; newest report first).
        max_pages: maximum API pages walked for discovery (default 2 = up to
            80 reports); only walked when ``limit`` is not yet met.
        subcolumn: 中房网栏目 — 原创 / 政策 / 市场 (the only three with
            content; the other six columns measured total=0). Unknown values
            are passed through as-is so a future column needs no code change,
            but expect an empty result for the six empty ones.
        page_size: items requested per API page (default 40).

    Returns:
        list of report row dicts (schema = manifest columns). Missing values
        are None; nothing is zero-filled. ``content`` is the report body plain
        text — the unit does not extract numbers (see README).
    """
    try:
        from fd_industry_data.cancel_event import is_set as cancel_set
    except ImportError:  # standalone run outside the repo package
        def cancel_set() -> bool:
            return False

    sub = (subcolumn or DEFAULT_SUBCOLUMN).strip() or DEFAULT_SUBCOLUMN

    async def _run() -> list[dict]:
        if cancel_set():
            logger.info("cancel requested before start; returning empty")
            return []
        return await _collect(
            max(int(limit), 0),
            max(int(max_pages), 1),
            sub,
            max(int(page_size), 1),
        )

    return asyncio.run(_run())[: max(int(limit), 0)]


if __name__ == "__main__":
    for r in run_fangchan_market(limit=3):
        print({k: (v[:80] + "…" if isinstance(v, str) and len(v) > 80 else v)
               for k, v in r.items()})
