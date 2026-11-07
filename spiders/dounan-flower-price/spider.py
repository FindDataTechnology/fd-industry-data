"""dounan-flower-price — 昆明市农业农村局「价格行情」斗南花卉市场日价.

Source: 昆明市农业农村局 (Kunming Municipal Bureau of Agriculture and Rural
Affairs) 农业农村工作 → 价格行情, https://nyncj.km.gov.cn/nygz/jgxq/.
发布主体标注：昆明斗南国际花卉产业园开发有限公司。

Live data paths (measured 2026-11-08, UTC+8):
  1. GET  https://nyncj.km.gov.cn/nygz/jgxq/index.shtml      (第 1 页, UTF-8)
        -> 归档列表，每条 <li class="clearfix"> 内 <a href="/c/YYYY-MM-DD/<id>.shtml">
           标题 </a> <span>YYYY-MM-DD</span>（span 为发布日期）。
     NOTE: 该栏目分页命名不规则——**第 1 页是 index.shtml，第 2 页起才是
     index_2.shtml**；index_1.shtml 实测 404。实测可见页 index.shtml + index_2..index_10
     （index_11 起 404）。
  2. GET  正文页 /c/YYYY-MM-DD/<id>.shtml
        -> 恰 1 张表、25 行：第 1 行为跨列表题、第 2 行为表头
           「品名 | 计量单位 | 最高价 | 最低价 | 均价」，其后 23 行为品种报价。
        实测样例：金鱼草|元/枝|1.15|0.94|1.05、红玫瑰（单头）|元/枝|0.35|0.22|0.27。

口径要点：
  * **价格日期取自标题**（「2026年11月5日花卉价格信息」），不是列表页的发布日期
    ——该源 T+1 发布（11-05 的价格于 11-06 见报）。两者都保留（price_date /
    publish_date），绝不互相替代。
  * **历史只有滚动窗**：实测 2026-11-08 时最老可见 2026-09-09，约 2 个月。
    因此本单元按「即接即累积」纪律交付：能回补多少回补多少，不宣称长期历史。
  * 上游偶有跳日（42 篇覆盖 09-09~11-05 约 58 天）。缺日**不补零、不内插**，
    如实缺测由 series_state 兜底。
  * 数值缺失一律记 None（如「-」「—」「暂无」），**绝不用 0 或占位值填充**；
    均价不自行由最高/最低价推算（上游给，不派生）。

No auth, no captcha, no cookies, no WAF (实测 200)。政府站证书为国内 CA，
按仓内既有惯例 verify=False（不假设容器信任库）。国内源：仅直连，从不走代理。

Window params: 入口声明 window_start / window_end（runner 注入 kwargs，同时以
FD_WINDOW_START / FD_WINDOW_END 环境变量暴露），按 price_date 过滤；
**窗口过滤先于 limit 截断**（否则回补区间会被最新 N 行吃掉）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone

from scrapling.fetchers import FetcherSession

logger = logging.getLogger("dounan-flower-price")

LIST_BASE = "https://nyncj.km.gov.cn/nygz/jgxq/"
SOURCE = "dounan-flower-price"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_ARTICLE_HREF_RE = re.compile(r'href="(/c/(\d{4}-\d{2}-\d{2})/(\d+)\.shtml)"')
_ITEM_TITLE_RE = re.compile(r"斗南花卉市场\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s*花卉价格信息")
_HEADER_FIRST = "品名"

# 上游用这些符号表示"无报价"；一律 None，不填 0
_NULL_TOKENS = frozenset({"", "-", "—", "–", "暂无", "/", "无"})

# 表头固定五列，顺序实测从未变过；若变则整篇跳过留痕（不猜列序）
_EXPECTED_COLS = ("品名", "计量单位", "最高价", "最低价", "均价")


def _decode(body: bytes | str) -> str:
    """页面实测 UTF-8；仍按 utf-8 → gb18030 顺序防御性解码。"""
    if isinstance(body, str):
        return body
    for enc in ("utf-8", "gb18030"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _num(text: str) -> float | None:
    """解析价格数值；不可解析一律 None（绝不 0 填充）。"""
    raw = _squash(text)
    if raw in _NULL_TOKENS:
        return None
    cleaned = raw.replace(",", "").replace("，", "").replace("元", "")
    match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    if match is None:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _cells(row_html: str) -> list[str]:
    """取一行的所有单元格文本（td/th 混排按出现顺序）。"""
    import html as html_mod

    out: list[str] = []
    for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.S):
        text = re.sub(r"<[^>]+>", "", cell)
        out.append(_squash(html_mod.unescape(text)))
    return out


def _parse_list_page(page_html: str) -> list[dict]:
    """列表页 -> [{url, title, publish_date}]，保持页面顺序（发布倒序）。

    只保留斗南花卉价格文章；`<li>` 内的 `<span>` 是发布日期，与标题里的
    价格日期不同，两者分别保留。
    """
    items: list[dict] = []
    for li in re.findall(r"<li[^>]*>(.*?)</li>", page_html, re.S):
        href_match = _ARTICLE_HREF_RE.search(li)
        if href_match is None:
            continue
        title_match = re.search(r"<a[^>]*>(.*?)</a>", li, re.S)
        if title_match is None:
            continue
        import html as html_mod

        title = _squash(html_mod.unescape(re.sub(r"<[^>]+>", "", title_match.group(1))))
        if _ITEM_TITLE_RE.search(title) is None:
            continue
        span_match = re.search(r"<span[^>]*>(.*?)</span>", li, re.S)
        publish_date = (
            _squash(html_mod.unescape(re.sub(r"<[^>]+>", "", span_match.group(1))))
            if span_match
            else href_match.group(2)
        )
        items.append(
            {
                "url": "https://nyncj.km.gov.cn" + href_match.group(1),
                "title": title,
                "publish_date": publish_date,
                "url_date": href_match.group(2),
            }
        )
    return items


def _parse_article(page_html: str, meta: dict, scraped_at: str) -> list[dict]:
    """正文页 -> 品种报价行；表头列序不符合预期则整篇跳过。"""
    import html as html_mod

    title_match = _ITEM_TITLE_RE.search(meta["title"])
    if title_match is None:
        logger.warning("title has no price date: %r — skip", meta["title"])
        return []
    year, month, day = (int(title_match.group(i)) for i in (1, 2, 3))
    price_date = f"{year:04d}-{month:02d}-{day:02d}"

    table_match = re.search(r"<table.*?</table>", page_html, re.S)
    if table_match is None:
        logger.warning("no table in %s — skip", meta["url"])
        return []

    rows_html = re.findall(r"<tr.*?</tr>", table_match.group(0), re.S)
    header_seen = False
    out: list[dict] = []
    for row_html in rows_html:
        cells = _cells(row_html)
        if len(cells) != 5:
            continue  # 跨列表题行（1 列）等
        if cells[0] == _HEADER_FIRST:
            header_seen = True
            if tuple(cells) != _EXPECTED_COLS:
                logger.warning(
                    "unexpected header %r in %s — skip article", cells, meta["url"]
                )
                return []
            continue
        if not header_seen:
            continue
        item = cells[0].replace(" ", "")
        if not item:
            continue
        high, low, avg = _num(cells[2]), _num(cells[3]), _num(cells[4])
        if high is None and low is None and avg is None:
            # 三价全缺：该品种本日无有效报价，记行但全 None（不填 0）
            logger.info("all prices missing for %s on %s", item, price_date)
        out.append(
            {
                "price_date": price_date,
                "publish_date": meta["publish_date"],
                "item": item,
                "unit": html_mod.unescape(cells[1]),
                "price_high": high,
                "price_low": low,
                "price_avg": avg,
                "report_title": meta["title"],
                "report_url": meta["url"],
                "source": SOURCE,
                "scraped_at": scraped_at,
            }
        )
    if not out:
        logger.warning("no price row parsed from %s — skip", meta["url"])
    return out


def _page_url(page_no: int) -> str:
    """第 1 页是 index.shtml；第 2 页起是 index_<n>.shtml（index_1 实测 404）。"""
    if page_no <= 1:
        return LIST_BASE + "index.shtml"
    return LIST_BASE + f"index_{page_no}.shtml"


async def _fetch(session: FetcherSession, url: str):
    """单次 GET；异常/非 200 返回 None（记 warning，不重试风暴）。"""
    try:
        resp = await session.get(url, headers={"User-Agent": USER_AGENT})
    except Exception as e:  # noqa: BLE001 — per-URL log-and-skip
        logger.warning("fetch failed %s: %s — skip", url, e)
        return None
    if resp.status != 200:
        logger.warning("status=%s %s — skip", resp.status, url)
        return None
    return resp


def _in_window(price_date: str, window_start: str | None, window_end: str | None) -> bool:
    if window_start and price_date < window_start:
        return False
    if window_end and price_date > window_end:
        return False
    return True


async def _collect(
    limit: int,
    max_pages: int,
    window_start: str | None,
    window_end: str | None,
) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    reports_done = 0
    reports_failed = 0
    pages_walked = 0
    scraped_at = datetime.now(timezone.utc).isoformat()

    async with FetcherSession(
        impersonate="chrome120",
        timeout=25,
        verify=False,  # 政府站国内 CA，按仓内惯例不假设容器信任库
        retries=1,     # 恰好一次尝试——不造重试风暴
    ) as session:
        page_no = 1
        while page_no <= max_pages:
            page_resp = await _fetch(session, _page_url(page_no))
            if page_resp is None:
                break
            pages_walked += 1
            articles = _parse_list_page(_decode(page_resp.body))
            logger.info("list page %d: %d flower reports", page_no, len(articles))
            if not articles:
                # 空页 = 已过最老页（该栏目跳日多，但空页即到底）
                break
            stop = False
            for meta in articles:
                article_resp = await _fetch(session, meta["url"])
                if article_resp is None:
                    reports_failed += 1
                    continue
                parsed = _parse_article(_decode(article_resp.body), meta, scraped_at)
                reports_done += 1
                for row in parsed:
                    if not _in_window(row["price_date"], window_start, window_end):
                        continue
                    key = (row["price_date"], row["item"])
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(row)
                # 列表按发布倒序；一旦整篇都早于窗口下界，后续页只会更老
                if parsed and window_start:
                    newest_in_article = max(r["price_date"] for r in parsed)
                    if newest_in_article < window_start:
                        stop = True
                        break
                if len(rows) >= limit:
                    stop = True
                    break
            if stop:
                break
            page_no += 1

    rows.sort(key=lambda r: (r["price_date"], r["item"]), reverse=True)
    logger.info(
        "dounan-flower-price: %d rows from %d reports (%d failed) over %d list page(s)",
        len(rows), reports_done, reports_failed, pages_walked,
    )
    return rows


def run_dounan_flower_price(
    limit: int = 500,
    max_pages: int = 10,
    window_start: str | None = None,
    window_end: str | None = None,
) -> list[dict]:
    """Entry point for fd-runner (spiders/dounan-flower-price).

    Args:
        limit: maximum rows to return (<= limit enforced; newest price_date first).
            23 items × ~42 reports ≈ 966 rows for the full visible window, so the
            default 500 truncates the oldest days; pass a larger limit for a full
            backfill.
        max_pages: maximum list pages walked (default 10 = the whole visible
            window; the channel currently ends at index_10 — index_11 is 404).
        window_start: inclusive lower bound on price_date (YYYY-MM-DD). Injected
            by the runner's first-crawl backfill window; also read from
            FD_WINDOW_START.
        window_end: inclusive upper bound on price_date (YYYY-MM-DD). Likewise
            FD_WINDOW_END.

    Returns:
        list of price row dicts (schema = manifest columns), newest date first.
        Missing prices are None; nothing is zero-filled, nothing is derived.
    """
    try:
        from fd_industry_data.cancel_event import is_set as cancel_set
    except ImportError:  # standalone run outside the repo package

        def cancel_set() -> bool:
            return False

    window_start = window_start or os.environ.get("FD_WINDOW_START") or None
    window_end = window_end or os.environ.get("FD_WINDOW_END") or None

    async def _run() -> list[dict]:
        if cancel_set():
            logger.info("cancel requested before start; returning empty")
            return []
        return await _collect(
            max(int(limit), 0),
            max(int(max_pages), 1),
            window_start,
            window_end,
        )

    return asyncio.run(_run())[: max(int(limit), 0)]


if __name__ == "__main__":
    for record in run_dounan_flower_price(limit=5):
        print(record)
