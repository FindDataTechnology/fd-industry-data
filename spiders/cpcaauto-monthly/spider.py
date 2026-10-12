"""cpcaauto-monthly — 乘联会（乘用车市场信息联席分会）乘用车市场月度分析.

Source: 中国汽车流通协会乘用车市场信息联席分会 (CPCA),
车市解读频道 https://www.cpcaauto.com/news.php?types=csjd

Live data paths (measured 2026-10-13, UTC+8):
  1. GET  https://www.cpcaauto.com/news.php?types=csjd            (charset UTF-8)
        -> 车市解读列表, 每页 20 条 <li class="q">:
             <span>2026-10-12 </span>
             <a href="newslist.php?types=csjd&id=4350">&nbsp;&nbsp;【月度分析】…</a>
           分页参数 page=N，实测共 84 页（尾页链接可见）。
      NOTE: 列表**必须**走 news.php。newslist.php?types=csjd 不带 id 时返回
      PHP 的 MySQL 报错壳（`Table 'cpca.ecs_books_csjd' doesn't exist` /
      `You have an error in your SQL syntax ... near ''`），不可用作列表。
      带 anid 的变体（news.php?types=csjd&anid=120）实测 0 条，同样不可用。
  2. GET  https://www.cpcaauto.com/newslist.php?types=csjd&id=<N>
        -> <div class="read_content">
             <div class="tit">标题</div>
             <div class="view"><span>发布时间：2026-10-10 17:16:43</span>…
                              <a href="/upload/bookpic/….rar">下载</a></div>
             <div class="text">正文（含 <font>/<strong>/<br>/&nbsp;）</div>
      实测 2026年9月月度分析正文 11,322 字，含
        「9月全国乘用车市场零售161.8万辆，同比下降28.0%，环比增长5.0%」
        「新能源零售渗透率攀升至67.0%」「出口91.7万辆，同比增长73.7%」。

反爬：裸 UA 返回 33 字节「请使用浏览器访问本站」，必须 impersonate='chrome'
（FetcherSession 默认值即 chrome，仍显式写出以示契约）。仓内已有 4 个单元
使用同一机制（carbon-monitor、chinawuliu-logistics、sc-gd-stats、nbs_gdp）。

正文形态是行业分析叙述文，不是结构化表：本单元**不做数值抽取的强约定**，
只把正文文本、报告类别、发布日与关键元数据落列；具体数值由下游按需要解析。
口径锚定靠 report_title（含「YYYY年M月份」）+ publish_date，不靠正文文字。

No auth, no captcha, no cookies.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

from lxml import html as lxml_html
from scrapling.fetchers import FetcherSession

logger = logging.getLogger("cpcaauto-monthly")

# 同源礼貌间隔：实测无间隔连发时站点返回 **200 空壳**（正文 0 字），
# 单条被跳过；加 0.4s 间隔后同批 id 全部可解析（仓内同约定见 cninfo-operating）。
SAME_HOST_GAP_S = 0.4

BASE = "https://www.cpcaauto.com/"
LIST_URL = urljoin(BASE, "news.php?types=csjd")
DETAIL_TPL = urljoin(BASE, "newslist.php?types=csjd&id={id}")
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 列表条目: <a href="newslist.php?types=csjd&id=4350">…</a>
_ITEM_HREF_RE = re.compile(r"newslist\.php\?types=csjd&(?:amp;)?id=(\d+)")
# 发布日: <span>2026-10-12 </span>
_ITEM_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
# 正文发布时刻: 发布时间：2026-10-10 17:16:43
_PUB_RE = re.compile(
    r"发布时间[：:]\s*(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{2}):(\d{2}):(\d{2}))?"
)
# 正文里的 YYYY年M月份 口径锚（月度分析标题形态）
_PERIOD_TITLE_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")
# 报告类别：标题前缀方括号【月度分析】/【周度分析】/【新能源】/…
_CATEGORY_RE = re.compile(r"^【\s*([^】]{2,12})\s*】")
# 列表标题里的前导 &nbsp;（&nbsp; 解码后为 \xa0）
_LEAD_NBSP_RE = re.compile(r"^[\s\u00a0]+")
# 附件（rar/pdf/doc 等）——尽力而为地抽取，仅作溯源，永不下载不解析
_ATTACH_RE = re.compile(
    r'href="([^"]+\.(?:rar|zip|pdf|doc|docx|xls|xlsx))"', re.IGNORECASE
)
# 正文最短长度：实测最短的报告（快讯类）也 > 200 字；低于此视为空壳页
_MIN_BODY_CHARS = 80

# 已知类别（按标题前缀）。未命中的记「其它」并保留原文，不丢数据。
KNOWN_CATEGORIES = (
    "月度分析",
    "周度分析",
    "新能源",
    "深度分析",
    "联合发布",
    "预测",
    "信息发布",
)


def _squash(text: str) -> str:
    """空白（含 \\xa0）折叠为单空格。"""
    return re.sub(r"[\s\u00a0]+", " ", text or "").strip()


def _category_of(title: str) -> str:
    """从标题的【…】前缀取报告类别；无前缀记「其它」。"""
    m = _CATEGORY_RE.match(title)
    if not m:
        return "其它"
    return m.group(1).strip()


def _period_of(title: str, publish_date: str | None) -> str | None:
    """从标题取数据期间（YYYY-MM）。

    标题形如「2026年9月份全国乘用车市场分析」「车市扫描(20260928-0930)」。
    优先取「YYYY年M月」；取不到时退回发布月的上一月（月度分析多在次月发布）。
    两者都取不到记 None，不猜。
    """
    m = _PERIOD_TITLE_RE.search(title)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    m = re.search(r"\((\d{4})(\d{2})\d{2}", title)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    if publish_date and len(publish_date) >= 7:
        y, mo = int(publish_date[:4]), int(publish_date[5:7])
        mo -= 1
        if mo == 0:
            y, mo = y - 1, 12
        return f"{y:04d}-{mo:02d}"
    return None


def _parse_list_page(page_html: str) -> list[dict]:
    """解析车市解读列表页：返回 [{article_id, title, publish_date, url}]。

    条目结构（实测）::

        <li class="q">
          <span>2026-10-12 </span>
          <a href="newslist.php?types=csjd&id=4350">&nbsp;&nbsp;【月度分析】…</a>
        </li>
    """
    doc = lxml_html.fromstring(page_html)
    items: list[dict] = []
    seen: set[str] = set()
    for a in doc.xpath('//a[contains(@href, "newslist.php")]'):
        href = (a.get("href") or "").strip()
        m = _ITEM_HREF_RE.search(href)
        if not m:
            continue
        article_id = m.group(1)
        if article_id in seen:
            continue
        title = _squash(_LEAD_NBSP_RE.sub("", a.text_content()))
        if not title:
            continue
        date_text = ""
        prev = a.getprevious()
        # <span>2026-10-12 </span> 紧邻 <a>；用前兄弟节点取，退化为父节点文本
        if prev is not None and prev.tag == "span":
            date_text = _squash(prev.text_content())
        if not date_text:
            parent = a.getparent()
            if parent is not None:
                span = parent.xpath(".//span")
                if span:
                    date_text = _squash(span[0].text_content())
        dm = _ITEM_DATE_RE.search(date_text)
        publish_date = (
            f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}" if dm else None
        )
        seen.add(article_id)
        items.append(
            {
                "article_id": article_id,
                "title": title,
                "publish_date": publish_date,
                "url": DETAIL_TPL.format(id=article_id),
            }
        )
    return items


def _max_page(page_html: str) -> int:
    """从分页区取最大页号（尾页链接 `…&page=84`）。取不到时按 1 处理。"""
    nums = [int(n) for n in re.findall(r"[?&]page=(\d+)", page_html)]
    return max(nums) if nums else 1


def _parse_detail(page_html: str) -> dict | None:
    """解析正文页：返回 {title, pub_dt, body, attach_url}；无正文块返回 None。

    正文容器 ``div.read_content`` 内::

        <div class="tit">标题</div>
        <div class="view"><span>发布时间：2026-10-10 17:16:43</span>…
                         <a href="/upload/bookpic/….rar">下载</a></div>
        <div class="text">正文</div>
    """
    doc = lxml_html.fromstring(page_html)
    # with_tail=False：保留被删节点之后的文本
    lxml_html.etree.strip_elements(doc, "script", "style", with_tail=False)
    nodes = doc.xpath('//div[contains(@class, "read_content")]')
    if not nodes:
        logger.warning("no div.read_content — not an article page?")
        return None
    node = nodes[0]
    tit = node.xpath('.//div[contains(@class, "tit")]')
    title = _squash(tit[0].text_content()) if tit else ""
    view = node.xpath('.//div[contains(@class, "view")]')
    view_text = _squash(view[0].text_content()) if view else ""
    pm = _PUB_RE.search(view_text)
    pub_dt: str | None = None
    if pm:
        pub_dt = f"{pm.group(1)}-{pm.group(2)}-{pm.group(3)}"
        if pm.group(4):
            pub_dt += f" {pm.group(4)}:{pm.group(5)}:{pm.group(6)}"
    text_nodes = node.xpath('.//div[contains(@class, "text")]')
    body = _squash(text_nodes[0].text_content()) if text_nodes else ""
    attach_url = None
    am = _ATTACH_RE.search(page_html)
    if am:
        attach_url = urljoin(BASE, am.group(1))
    return {"title": title, "pub_dt": pub_dt, "body": body, "attach_url": attach_url}


async def _fetch(session: FetcherSession, url: str):
    """单次 GET；异常/非 200 返回 None（记 warning，不重试、不绕反爬）。"""
    try:
        resp = await session.get(url, headers={"User-Agent": USER_AGENT})
    except Exception as e:  # noqa: BLE001 — per-URL log-and-skip
        logger.warning("fetch failed %s: %s — skip", url, e)
        return None
    if resp.status != 200:
        logger.warning("status=%s %s — skip", resp.status, url)
        return None
    body = resp.body
    if isinstance(body, bytes) and b"\xe8\xaf\xb7\xe4\xbd\xbf\xe7\x94\xa8" in body[:400]:
        # 「请使用浏览器访问本站」拦截页 —— 有正文的页面不可能这么短
        logger.warning("browser-check page for %s — skip", url)
        return None
    return resp


def _throttle(last_at: float) -> float:
    """同源节流：保证两次请求间隔 >= SAME_HOST_GAP_S，返回新时间戳。"""
    elapsed = time.monotonic() - last_at
    if 0 <= elapsed < SAME_HOST_GAP_S:
        time.sleep(SAME_HOST_GAP_S - elapsed)
    return time.monotonic()


async def _collect(limit: int, max_pages: int, category: str | None) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    reports_done = 0
    reports_failed = 0
    pages_walked = 0
    scraped_at = datetime.now(timezone.utc).isoformat()
    category_filter = (category or "").strip() or None

    async with FetcherSession(
        impersonate="chrome",  # 裸 UA 被 33 字节拦截页挡下（实测）
        timeout=30,
        verify=False,  # estate convention: 不假设容器信任库
        retries=1,     # exactly one attempt — no retry storms
    ) as session:
        gate = time.monotonic()
        resp = await _fetch(session, LIST_URL)
        if resp is None:
            logger.error("list page unavailable — returning 0 rows")
            return rows
        page_html = resp.body.decode("utf-8", errors="replace")
        items = _parse_list_page(page_html)
        total_pages = _max_page(page_html)
        pages_walked = 1
        logger.info(
            "list page: %d items, %d pages available", len(items), total_pages
        )

        page_no = 1
        while True:
            for item in items:
                if len(rows) >= limit:
                    break
                if item["article_id"] in seen:
                    continue
                seen.add(item["article_id"])
                gate = _throttle(gate)
                rresp = await _fetch(session, item["url"])
                if rresp is None:
                    reports_failed += 1
                    continue
                parsed = _parse_detail(rresp.body.decode("utf-8", errors="replace"))
                if parsed is None:
                    reports_failed += 1
                    continue
                reports_done += 1
                title = parsed["title"] or item["title"]
                if len(parsed["body"]) < _MIN_BODY_CHARS:
                    logger.warning(
                        "body too short (%d chars) for %s — skip",
                        len(parsed["body"]), item["url"],
                    )
                    continue
                cat = _category_of(title)
                if category_filter and cat != category_filter:
                    continue
                publish_date = parsed["pub_dt"] or item["publish_date"]
                rows.append(
                    {
                        "report_title": title,
                        "category": cat,
                        "period": _period_of(title, publish_date),
                        "publish_date": publish_date,
                        "article_id": item["article_id"],
                        "content": parsed["body"],
                        "attach_url": parsed["attach_url"],
                        "source": "cpcaauto-monthly",
                        "scraped_at": scraped_at,
                    }
                )
            if len(rows) >= limit:
                break
            page_no += 1
            if page_no > min(total_pages, max_pages):
                break
            gate = _throttle(gate)
            nresp = await _fetch(session, f"{LIST_URL}&page={page_no}")
            if nresp is None:
                break
            items = _parse_list_page(nresp.body.decode("utf-8", errors="replace"))
            pages_walked += 1
            if not items:
                break

    logger.info(
        "cpcaauto-monthly: %d rows from %d reports (%d failed) over %d list page(s)",
        len(rows), reports_done, reports_failed, pages_walked,
    )
    return rows


def run_cpcaauto_monthly(
    limit: int = 50,
    max_pages: int = 3,
    category: str | None = None,
) -> list[dict]:
    """Entry point for fd-runner (spiders/cpcaauto-monthly).

    Args:
        limit: maximum rows to return (hard cap; newest report first).
        max_pages: maximum list pages walked for discovery (default 3 =
            up to 60 reports); only walked when ``limit`` is not yet met.
        category: keep only this 报告类别 (月度分析/周度分析/新能源/深度分析/
            联合发布/预测/…); empty or None = all.

    Returns:
        list of report row dicts (schema = manifest columns). Missing values
        are None; nothing is zero-filled. ``content`` is the report body
        plain text (whitespace-collapsed), not parsed into numbers — the
        decision is recorded in README (行业分析叙述文，不强抽数值).
    """
    try:
        from fd_industry_data.cancel_event import is_set as cancel_set
    except ImportError:  # standalone run outside the repo package
        def cancel_set() -> bool:
            return False

    async def _run() -> list[dict]:
        if cancel_set():
            logger.info("cancel requested before start; returning empty")
            return []
        return await _collect(
            max(int(limit), 0), max(int(max_pages), 1), category
        )

    return asyncio.run(_run())[: max(int(limit), 0)]


if __name__ == "__main__":
    for r in run_cpcaauto_monthly(limit=3):
        print({k: (v[:80] + "…" if isinstance(v, str) and len(v) > 80 else v)
               for k, v in r.items()})
