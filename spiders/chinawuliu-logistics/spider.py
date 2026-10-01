"""chinawuliu-logistics — 中国物流与采购联合会「信息统计-统计数据」栏目爬虫.

三个口径（brief 指定，channel 枚举）:
  1. weekly-freight  中国公路物流运价周指数报告（周度；文章页 meta description 机器可读，直接含当期数值）
  2. lpi             中国物流业景气指数 LPI（月度；meta 优先、正文兜底）
  3. e-commerce      中国电商物流指数（月度；meta 优先、正文兜底）

端点: http://www.chinawuliu.com.cn/xsyj/tjsj/（栏目列表，index_2..index_N.shtml 翻页）
鉴权: 免鉴权。国内源，直连（不走代理）。
HTTP 一律走 scrapling.fetchers.FetcherSession（curl_cffi 引擎，impersonate=chrome120）。

容错纪律: 非 200 / 空响应 → 记录后跳过，不连环重试；meta 与正文都抽不到数值 →
该行不产出（宁缺勿脏）。只发实测有效参数（列表翻页仅 index_N.shtml，文章 URL 仅 .shtml）。
首爬只回补 12 个月（BACKFILL_DAYS）。
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import re
from datetime import datetime, timedelta, timezone

from scrapling.fetchers import FetcherSession

logger = logging.getLogger("chinawuliu-logistics")

BASE_URL = "http://www.chinawuliu.com.cn"
LIST_URL = BASE_URL + "/xsyj/tjsj/"
SOURCE_URL = LIST_URL

MAX_LIST_PAGES = 12      # 实测 index_2..index_11 有效，index_12 起 404（留 1 页做自然止损）
BACKFILL_DAYS = 370      # 首爬只回补 12 个月（brief 约定）
ARTICLE_DELAY_S = 0.25   # 抓正文间隔，礼貌限速（非重试）

HDRS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 列表条目: <li><a href="/xsyj/YYYYMM/DD/ID.shtml" title="标题">标题</a><span class="time">YYYY/MM/DD HH:MM</span></li>
_LIST_ITEM_RE = re.compile(
    r'<li><a href="(?P<url>/xsyj/\d{6}/\d{2}/\d+\.shtml)" title="(?P<title>[^"]*)"'
    r'[^>]*>[^<]*</a><span class="time">(?P<date>\d{4}/\d{2}/\d{2})',
    re.S,
)
_META_DESC_RE = re.compile(r'<meta\s+name="description"\s+content="(?P<desc>.*?)"\s*/?>', re.S)
_PUB_DATE_RE = re.compile(r"发布时间：(\d{4}-\d{2}-\d{2})")
# 正文容器（div.text.mb-50）到右侧栏之间的区段，作为 meta 缺失/截断时的兜底
_BODY_ZONE_RE = re.compile(r'class="text mb-50">(.*?)<div class="col-sm-4', re.S)
_TAG_RE = re.compile(r"<[^>]+>")

_FALL_WORDS = {"回落", "下降", "下跌"}
_WEEKLY_PERIOD_RE = re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})")
_MONTHLY_PERIOD_RE = re.compile(r"(\d{4})年(\d{1,2})月份")

_CHANNELS: dict[str, dict] = {
    # 周指数: 标题形如「中国公路物流运价周指数报告（2026.8.21)」
    "weekly-freight": {
        "match": lambda t: "公路物流运价周指数" in t,
        "index_name": "中国公路物流运价指数",
        "value": re.compile(r"中国公路物流运价指数为([\d,]+(?:\.\d+)?)点"),
        "mom": re.compile(r"比上周(回落|下降|下跌|回升|上涨|上升)([\d,]+(?:\.\d+)?)%"),
        "mom_unit": "%",
    },
    # LPI: 标题形如「2026年8月份中国物流业景气指数为50.9%」
    "lpi": {
        "match": lambda t: "物流业景气指数为" in t,
        "index_name": "中国物流业景气指数",
        "value": re.compile(r"中国物流业景气指数为([\d,]+(?:\.\d+)?)%"),
        "mom": re.compile(r"较上月(回落|下降|下跌|回升|上涨|上升)([\d,]+(?:\.\d+)?)个百分点"),
        "mom_unit": "pp",
    },
    # 电商物流指数: 标题形如「2026年8月份电商物流指数为111.5点」
    "e-commerce": {
        "match": lambda t: "电商物流指数为" in t,
        "index_name": "中国电商物流指数",
        "value": re.compile(r"中国电商物流指数为([\d,]+(?:\.\d+)?)点"),
        "mom": re.compile(r"环比(回落|下降|下跌|回升|上涨|上升)([\d,]+(?:\.\d+)?)点"),
        "mom_unit": "点",
    },
}
_YOY_RE = re.compile(r"(?:比|较)去年同期(回落|下降|下跌|增长|回升|上涨|上升)([\d,]+(?:\.\d+)?)")


def _classify(title: str) -> str | None:
    for channel, spec in _CHANNELS.items():
        if spec["match"](title):
            return channel
    return None


def _parse_listing(page_html: str) -> list[tuple[str, str, str]]:
    """返回 (相对 URL, 标题, YYYY-MM-DD 发布日) 列表，按页面顺序去重。"""
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for m in _LIST_ITEM_RE.finditer(page_html):
        url = m.group("url")
        if url in seen:
            continue
        seen.add(url)
        out.append((url, _html.unescape(m.group("title")), m.group("date").replace("/", "-")))
    return out


def _publish_date(page_html: str, listing_date: str) -> str:
    m = _PUB_DATE_RE.search(page_html)
    return m.group(1) if m else listing_date


def _period(channel: str, title: str, pub_date: str) -> str:
    """期号: 周指数取标题中的报告日 (YYYY-MM-DD)，月度指数取标题年月 (YYYY-MM)。"""
    if channel == "weekly-freight":
        m = _WEEKLY_PERIOD_RE.search(title)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        return pub_date
    m = _MONTHLY_PERIOD_RE.search(title)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    return pub_date[:7]


def _extract_article(channel: str, page_html: str) -> tuple[float, str | None, str | None] | None:
    """优先 meta description，正文兜底；两者都抽不到数值 → None（不写脏行）。

    返回 (value, yoy, mom)。
    """
    meta_m = _META_DESC_RE.search(page_html)
    meta_text = _html.unescape(meta_m.group("desc")) if meta_m else ""
    body_m = _BODY_ZONE_RE.search(page_html)
    body_text = _TAG_RE.sub(" ", body_m.group(1)) if body_m else ""
    if not meta_text and not body_text:
        return None

    spec = _CHANNELS[channel]
    for text in (meta_text, body_text):
        vm = spec["value"].search(text)
        if not vm:
            continue
        value = float(vm.group(1).replace(",", ""))
        yoy = mom = None
        mm = spec["mom"].search(text)
        if mm:
            sign = "-" if mm.group(1) in _FALL_WORDS else "+"
            mom = f"{sign}{mm.group(2)}{spec['mom_unit']}"
        ym = _YOY_RE.search(text)
        if ym:
            sign = "-" if ym.group(1) in _FALL_WORDS else "+"
            yoy = f"{sign}{ym.group(2)}{spec['mom_unit']}"
        return value, yoy, mom
    return None


async def _fetch(session, url: str) -> str | None:
    """GET 并返回 HTML 文本；非 200 / 空响应 / 异常 → 记录后返回 None（log-and-skip）。"""
    try:
        resp = await session.get(url, headers=HDRS)
    except Exception as exc:  # noqa: BLE001 — 网络层失败按单页跳过
        logger.warning("fetch failed %s: %s — skip", url, exc)
        return None
    status = getattr(resp, "status", 0)
    if status != 200:
        logger.warning("non-200 (%s) %s — skip", status, url)
        return None
    text = resp.html_content or ""
    if not text.strip():
        logger.warning("empty body %s — skip", url)
        return None
    return text


async def _collect(limit: int) -> list[dict]:
    rows: list[dict] = []
    cutoff = (datetime.now() - timedelta(days=BACKFILL_DAYS)).date()

    # retries=1：单次尝试即放弃（log-and-skip），不制造重试风暴
    async with FetcherSession(impersonate="chrome120", timeout=25, verify=False, retries=1) as session:
        for page in range(1, MAX_LIST_PAGES + 1):
            list_url = LIST_URL if page == 1 else f"{LIST_URL}index_{page}.shtml"
            list_html = await _fetch(session, list_url)
            if list_html is None:
                if page == 1:
                    logger.error("listing page 1 unreachable — abort run")
                    return rows
                continue  # 翻页失败记录后跳过该页
            items = _parse_listing(list_html)
            if not items:
                logger.info("listing page %s has no items — stop paging", page)
                break

            expired = 0
            for rel_url, title, listing_date in items:
                channel = _classify(title)
                if channel is None:
                    continue  # PMI/仓储/大宗商品等非三口径文章
                try:
                    adate = datetime.strptime(listing_date, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if adate < cutoff:
                    expired += 1
                    continue  # 超出 12 个月回补窗口
                if len(rows) >= limit:
                    return rows

                art_html = await _fetch(session, BASE_URL + rel_url)
                if art_html is None:
                    continue
                extracted = _extract_article(channel, art_html)
                if extracted is None:
                    logger.info("no numeric value extracted for %s — skip dirty row", rel_url)
                    continue
                value, yoy, mom = extracted
                pub_date = _publish_date(art_html, listing_date)
                rows.append(
                    {
                        "period": _period(channel, title, pub_date),
                        "channel": channel,
                        "index_name": _CHANNELS[channel]["index_name"],
                        "value": value,
                        "yoy": yoy,
                        "mom": mom,
                        "title": title,
                        "url": BASE_URL + rel_url,
                        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "source_url": SOURCE_URL,
                    }
                )
                await asyncio.sleep(ARTICLE_DELAY_S)

            if expired == len(items):
                logger.info("page %s fully before backfill cutoff %s — stop paging", page, cutoff)
                break

    return rows[:limit]


def run_chinawuliu_logistics(limit: int = 100) -> list[dict]:
    """fd-runner 入口：抓取中物联统计数据栏三口径指数，返回 ≤limit 行。"""
    root = logging.getLogger()
    if not root.handlers and not logger.handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return asyncio.run(_collect(max(1, int(limit))))


if __name__ == "__main__":
    for row in run_chinawuliu_logistics(limit=10):
        print(row)
