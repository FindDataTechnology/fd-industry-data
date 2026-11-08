"""moa-fishery — 农业农村部渔业渔政管理局 水产品产量.

Source: 农业农村部渔业渔政管理局 (MOA Bureau of Fisheries), 工作动态 column,
https://yyj.moa.gov.cn/gzdt/. 国内直连，禁代理。

两类粒度，**绝不合并**（granularity 区分）:

  1. 月度累计 (``granularity="cumulative_month"``)
     栏目按「1—N月水产品产量」发布文章，正文统计句，例如
     （实测 2026-10-22 发布，2026年1—9月）::

         据对全国20个省（自治区、直辖市）渔业生产统计，2026年1—9月，国内水产品
         总产量5389.68万吨，同比增长4.27%。其中，海水养殖产量2029.73万吨，
         同比增长5.84%；海洋捕捞产量663.85万吨，同比增长1.15%；...

     产出 总产量 + 4 个分项，period = 累计终点月 (``2026-09``)，
     ``cumulative_month`` = N (1..N 的 N)。

  2. 年度公报 (``granularity="year"``)
     《YYYY年全国渔业经济统计公报》（每年 7 月前后发布，实测 2026-07-27 发布
     2025 年公报），正文统计句::

         2025年，全国水产品总产量7652.14万吨，同比增长4.00%。其中，养殖产量
         6325.62万吨，同比增长4.38%；捕捞产量1326.52万吨，同比增长2.23%；...
         海水产品产量3859.94万吨，同比增长4.07%；淡水产品产量3792.20万吨...

     产出 总产量 + 4 个分项，period = ``2025``，``cumulative_month`` = None。
     表1/表2 与正文同源，本单元取正文句子（表头合并单元格多，正文更稳）。

形态: HTML 正文内嵌文本（**不是 API，也不是 PDF**）。列表页每页 20 条，
分页变量 ``countPage``，翻页 URL 为 ``index_N.htm``（首页即栏目根）。

价格序列（水产品价格）本次**未接**：侦察在页面未看到价格数据，标为未验证。

取不到的字段写 None，**不写 0**。只用标准库。
"""
from __future__ import annotations

import logging
import re
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urljoin

logger = logging.getLogger("moa-fishery")

LIST_URL = "https://yyj.moa.gov.cn/gzdt/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = 30
# 列表实测 25 页（每页 20 条）；只在未达 limit 时继续翻，最多走满全部页。
MAX_PAGES = 25
UNIT = "万吨"

_ARTICLE_RE = re.compile(r"t\d{8}_\d+\.htm$")
_HREF_DATE_RE = re.compile(r"t(\d{4})(\d{2})(\d{2})_")
_LIST_ITEM_RE = re.compile(
    r"<li[^>]*>\s*<a\s+[^>]*href=\"([^\"]+)\"[^>]*title=['\"]([^'\"]*)['\"][^>]*>"
    r"(.*?)</a>",
    re.S,
)
_TAG_RE = re.compile(r"<[^>]+>")
_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_COUNT_PAGE_RE = re.compile(r"countPage\s*=\s*(\d+)")

_MONTHLY_TITLE_RE = re.compile(r"(\d{1,2})\s*[—–\-~至]\s*(\d{1,2})\s*月\s*水产品产量")
_ANNUAL_TITLE_RE = re.compile(r"(\d{4})\s*年全国渔业经济统计公报")

# 「2026年1—9月」累计区间；年份可缺（老文章正文只写「1—11月」）
_CUM_PERIOD_RE = re.compile(
    r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*[—–\-~至]\s*(\d{1,2})\s*月"
)
# 涨跌表述：新文章用「同比增长」，老文章用「比上年增长」，都兼容
_YOY_WORD = r"(增长|上升|提高|增加|下降|减少|降低|持平)"
_YOY_LINK = r"(?:同比|比上年同期|比上年|比去年)"
_YOY_TAIL = rf"\s*[，,]?\s*{_YOY_LINK}\s*{_YOY_WORD}?\s*([\d.]+)?\s*%"
_YOY_PHRASE_RE = re.compile(rf"{_YOY_LINK}\s*{_YOY_WORD}?\s*([\d.]+)?\s*%")

# 月度：国内水产品总产量 X 万吨（同比在紧随其后的 25 字符内另取）
_MONTHLY_TOTAL_RE = re.compile(r"国内水产品总产量\s*([\d.]+)\s*万吨")
_MONTHLY_ITEM_RE = re.compile(
    rf"(海水养殖|海洋捕捞|淡水养殖|淡水捕捞)产量\s*([\d.]+)\s*万吨{_YOY_TAIL}"
)
# 年度：YYYY年，全国水产品总产量 X 万吨（同比另取）
_ANNUAL_TOTAL_RE = re.compile(
    r"(\d{4})\s*年\s*[，,]?\s*(?:全国)?水产品总产量\s*([\d.]+)\s*万吨"
)
_ANNUAL_ITEM_RE = re.compile(
    rf"(养殖|捕捞|海水产品|淡水产品)产量\s*([\d.]+)\s*万吨{_YOY_TAIL}"
)

_NEG_WORDS = frozenset({"下降", "减少", "降低"})
_FLAT_WORDS = frozenset({"持平"})


# --------------------------------------------------------------------------
# 文本 / 数值工具
# --------------------------------------------------------------------------
def _decode(body: bytes | str) -> str:
    """页面实测 UTF-8；仍按 utf-8 → gb18030 防御性回退。"""
    if isinstance(body, str):
        return body
    for enc in ("utf-8", "gb18030"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _squash(text: str) -> str:
    """标题类文本：空白折叠为单空格。"""
    return re.sub(r"\s+", " ", text or "").strip()


def _despace(text: str) -> str:
    """正文类文本：去掉全部空白（含 \\u3000），便于跨行句子正则。"""
    return re.sub(r"\s+", "", text or "")


def _visible_text(page_html: str) -> str:
    """去 script/style 后取纯文本（只用于统计句检索，不依赖 DOM 结构）。"""
    text = re.sub(r"<script\b.*?</script>", " ", page_html, flags=re.S | re.I)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.S | re.I)
    return _TAG_RE.sub(" ", text)


def _yoy(word: str | None, num: str | None) -> float | None:
    """同比增长/下降 → 带符号 float；无表述/无数字 → None（不填 0）。"""
    if word in _FLAT_WORDS:
        return 0.0
    if num is None:
        return None
    value = float(num)
    return -value if word in _NEG_WORDS else value


def _yoy_after(text: str, pos: int, span: int = 25) -> float | None:
    """总量句的同比紧跟在「…万吨」之后，取其后 ``span`` 字符内的第一处。

    限窗是为了避免在总量句没有同比时误取下一分项的同比。
    """
    m = _YOY_PHRASE_RE.search(text, pos, pos + span)
    return _yoy(m.group(1), m.group(2)) if m else None


def _article_kind(title: str) -> str | None:
    """按标题判定文章类别：monthly / annual / None（非本单元）。"""
    if _ANNUAL_TITLE_RE.search(title):
        return "annual"
    if _MONTHLY_TITLE_RE.search(title):
        return "monthly"
    return None


# --------------------------------------------------------------------------
# 窗口（平台属有参数）
# --------------------------------------------------------------------------
def _window_bounds(
    window_start: str | None, window_end: str | None
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """``YYYY-MM`` / ``YYYY-MM-DD`` / ``YYYY`` → 闭区间 ((y,m),(y,m))；无窗返回 None。

    非法值抛 ValueError：带窗口的运行必须响亮失败，而不是静默取错区间。
    """
    if window_start is None and window_end is None:
        return None
    if not window_start or not window_end:
        raise ValueError(
            f"moa-fishery: window 参数须成对出现，got start={window_start!r} "
            f"end={window_end!r}"
        )

    def _parse(raw: str, name: str) -> tuple[int, int]:
        text = str(raw).strip()
        m = re.fullmatch(r"(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?", text)
        if not m:
            raise ValueError(
                f"moa-fishery: {name} 须为 YYYY / YYYY-MM / YYYY-MM-DD，got {raw!r}"
            )
        year = int(m.group(1))
        month = int(m.group(2)) if m.group(2) else (12 if name == "window_end" else 1)
        if not 1 <= month <= 12:
            raise ValueError(f"moa-fishery: {name} 月份非法: {raw!r}")
        return year, month

    start = _parse(window_start, "window_start")
    end = _parse(window_end, "window_end")
    if start > end:
        raise ValueError(
            f"moa-fishery: window_start {window_start} 晚于 window_end {window_end}"
        )
    return start, end


def _in_window(
    row: dict, bounds: tuple[tuple[int, int], tuple[int, int]]
) -> bool:
    period = str(row.get("period") or "")
    m = re.fullmatch(r"(\d{4})(?:-(\d{1,2}))?", period)
    if not m:
        return False
    year = int(m.group(1))
    # 年度行覆盖整年 → 按 12 月记；月度行按 period 的终点月。
    month = int(m.group(2)) if m.group(2) else 12
    return bounds[0] <= (year, month) <= bounds[1]


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def _build_opener() -> urllib.request.OpenerDirector:
    """国内直连、禁代理；MOA 为 CFCA 证书，按仓内惯例不校验。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),  # 显式空代理表 = 忽略环境变量，直连
        urllib.request.HTTPSHandler(context=ctx),
    )


def _get(opener: urllib.request.OpenerDirector, url: str) -> str | None:
    """单次 GET；异常/非 200 → warning + None（不重试、不绕反爬）。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:  # noqa: S310
            status = getattr(resp, "status", 200)
            body = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as e:
        logger.warning("fetch failed %s: %s — skip", url, e)
        return None
    if status != 200:
        logger.warning("status=%s %s — skip", status, url)
        return None
    return _decode(body)


# --------------------------------------------------------------------------
# 列表
# --------------------------------------------------------------------------
def _parse_list(page_html: str) -> tuple[list[dict], int]:
    """解析工作动态列表页 → (条目, 总页数)。

    条目: url / title / pub_date。列表按发布时间倒序，顺序即新旧顺序。
    """
    items: list[dict] = []
    seen: set[str] = set()
    for m in _LIST_ITEM_RE.finditer(page_html):
        href, title_attr, inner = m.group(1).strip(), m.group(2), m.group(3)
        if not _ARTICLE_RE.search(href):
            continue
        url = urljoin(LIST_URL, href)
        if url in seen:
            continue
        title = _squash(title_attr) or _squash(_TAG_RE.sub(" ", inner))
        if not title:
            continue
        date_m = _DATE_RE.search(_squash(_TAG_RE.sub(" ", inner)))
        if date_m:
            pub = (int(date_m.group(1)), int(date_m.group(2)), int(date_m.group(3)))
        else:
            href_m = _HREF_DATE_RE.search(href)
            pub = (
                (int(href_m.group(1)), int(href_m.group(2)), int(href_m.group(3)))
                if href_m
                else None
            )
        seen.add(url)
        items.append({"url": url, "title": title, "pub_date": pub})
    total = 1
    count_m = _COUNT_PAGE_RE.search(page_html)
    if count_m:
        total = max(int(count_m.group(1)), 1)
    return items, total


# --------------------------------------------------------------------------
# 正文解析
# --------------------------------------------------------------------------
def _base_row(
    *, item: str, period: str, granularity: str, cumulative_month: int | None,
    value: float, yoy: float | None, title: str, url: str, scraped_at: str,
) -> dict:
    return {
        "period": period,
        "granularity": granularity,
        "cumulative_month": cumulative_month,
        "item": item,
        "value": value,
        "unit": UNIT,
        "yoy_pct": yoy,
        "title": title,
        "source_url": url,
        "scraped_at": scraped_at,
    }


def _parse_monthly(
    page_html: str, article: dict, scraped_at: str
) -> list[dict]:
    """月度累计文章 → 总产量 + 4 个分项行（granularity=cumulative_month）。"""
    text = _despace(_visible_text(page_html))
    total_m = _MONTHLY_TOTAL_RE.search(text)
    if total_m is None:
        logger.warning("monthly: no 总产量 sentence in %s — skip", article["url"])
        return []

    # 区间只在总量句紧前方取（老文章正文是「1—11月」，年份按发布日锚定）
    prefix = text[max(0, total_m.start() - 100): total_m.start()]
    period_m = _CUM_PERIOD_RE.search(prefix)
    pub = article.get("pub_date")
    if period_m is not None:
        year = int(period_m.group(1)) if period_m.group(1) else None
        end_month = int(period_m.group(3))
        if year is None:
            if pub is None:
                logger.warning("monthly: no year and no pub date in %s — skip", article["url"])
                return []
            year = pub[0] - 1 if end_month > pub[1] else pub[0]
    else:
        title_m = _MONTHLY_TITLE_RE.search(article["title"])
        if title_m is None or pub is None:
            logger.warning("monthly: no period in %s — skip", article["url"])
            return []
        end_month = int(title_m.group(2))
        year = pub[0] - 1 if end_month > pub[1] else pub[0]
        logger.warning(
            "monthly: body period missing, anchored %04d-%02d from publish date %s",
            year, end_month, article["url"],
        )
    if not 1 <= end_month <= 12:
        logger.warning("monthly: bad end month %s in %s — skip", end_month, article["url"])
        return []

    period = f"{year:04d}-{end_month:02d}"
    rows = [
        _base_row(
            item="总产量", period=period, granularity="cumulative_month",
            cumulative_month=end_month, value=float(total_m.group(1)),
            yoy=_yoy_after(text, total_m.end()),
            title=article["title"], url=article["url"], scraped_at=scraped_at,
        )
    ]
    for m in _MONTHLY_ITEM_RE.finditer(text):
        rows.append(
            _base_row(
                item=m.group(1), period=period, granularity="cumulative_month",
                cumulative_month=end_month, value=float(m.group(2)),
                yoy=_yoy(m.group(3), m.group(4)),
                title=article["title"], url=article["url"], scraped_at=scraped_at,
            )
        )
    return rows


def _parse_annual(
    page_html: str, article: dict, scraped_at: str
) -> list[dict]:
    """年度公报 → 总产量 + 4 个分项行（granularity=year）。"""
    text = _despace(_visible_text(page_html))
    total_m = _ANNUAL_TOTAL_RE.search(text)
    if total_m is None:
        logger.warning("annual: no 总产量 sentence in %s — skip", article["url"])
        return []
    year = int(total_m.group(1))
    title_m = _ANNUAL_TITLE_RE.search(article["title"])
    if title_m is not None and int(title_m.group(1)) != year:
        logger.warning(
            "annual: title year %s != body year %s in %s — keep body year",
            title_m.group(1), year, article["url"],
        )
    period = f"{year:04d}"
    rows = [
        _base_row(
            item="总产量", period=period, granularity="year",
            cumulative_month=None, value=float(total_m.group(2)),
            yoy=_yoy_after(text, total_m.end()),
            title=article["title"], url=article["url"], scraped_at=scraped_at,
        )
    ]
    for m in _ANNUAL_ITEM_RE.finditer(text):
        rows.append(
            _base_row(
                item=m.group(1), period=period, granularity="year",
                cumulative_month=None, value=float(m.group(2)),
                yoy=_yoy(m.group(3), m.group(4)),
                title=article["title"], url=article["url"], scraped_at=scraped_at,
            )
        )
    return rows


# --------------------------------------------------------------------------
# 采集
# --------------------------------------------------------------------------
def _collect(
    limit: int, bounds: tuple[tuple[int, int], tuple[int, int]] | None
) -> list[dict]:
    rows: list[dict] = []
    seen_rows: set[tuple[str, str, str]] = set()
    seen_urls: set[str] = set()
    scraped_at = datetime.now(timezone.utc).isoformat()

    opener = _build_opener()
    page_html = _get(opener, LIST_URL)
    if page_html is None:
        logger.error("list page unavailable — returning 0 rows")
        return rows

    page_no = 0
    articles_seen = 0
    while True:
        items, total_pages = _parse_list(page_html)
        for article in items:
            if len(rows) >= limit:
                break
            if article["url"] in seen_urls:
                continue
            seen_urls.add(article["url"])
            kind = _article_kind(article["title"])
            if kind is None:
                continue
            articles_seen += 1
            body = _get(opener, article["url"])
            if body is None:
                continue
            parsed = (
                _parse_monthly(body, article, scraped_at)
                if kind == "monthly"
                else _parse_annual(body, article, scraped_at)
            )
            for row in parsed:
                key = (row["granularity"], row["period"], row["item"])
                if key in seen_rows:
                    continue
                if bounds is not None and not _in_window(row, bounds):
                    continue
                seen_rows.add(key)
                if len(rows) < limit:
                    rows.append(row)
        if len(rows) >= limit:
            break
        page_no += 1
        if page_no >= min(total_pages, MAX_PAGES):
            break
        page_html = _get(opener, urljoin(LIST_URL, f"index_{page_no}.htm"))
        if page_html is None:
            break

    logger.info(
        "moa-fishery: %d rows from %d articles (%d list offsets walked)",
        len(rows), articles_seen, page_no,
    )
    return rows


def run_moa_fishery(
    limit: int = 50,
    window_start: str | None = None,
    window_end: str | None = None,
) -> list[dict]:
    """Entry point for fd-runner (spiders/moa-fishery).

    Args:
        limit: maximum rows to return (monthly 5 + annual 5 per report).
        window_start / window_end: optional backfill window, ``YYYY-MM`` /
            ``YYYY-MM-DD`` (platform-owned). Only rows whose period falls in
            the closed window are returned; filtering happens before ``limit``
            truncation.

    Returns:
        list of output dicts (schema = manifest columns), newest article first.
        Missing numbers are None; nothing is zero-filled.
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []
    bounds = _window_bounds(window_start, window_end)

    try:
        from fd_industry_data.cancel_event import is_set as cancel_set
    except ImportError:  # standalone run outside the repo package
        def cancel_set() -> bool:
            return False

    if cancel_set():
        logger.info("cancel requested before start; returning empty")
        return []
    return _collect(limit, bounds)[:limit]


if __name__ == "__main__":
    for record in run_moa_fishery(limit=20):
        print(record)
