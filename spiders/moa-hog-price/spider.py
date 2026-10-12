"""moa-hog-price — 农业农村部生猪和猪肉价格周报.

Source: 农业农村部 (Ministry of Agriculture and Rural Affairs) 生猪专题 →
「市场信息发布」栏目（市场动态）, https://www.moa.gov.cn/ztzl/szcpxx/scxxfb/.

Live data paths (measured 2026-10-12, UTC+8):
  1. GET  https://www.moa.gov.cn/ztzl/szcpxx/scxxfb/   (charset UTF-8)
        -> 市场动态 列表, 每页 6 条, 共 4 页 (index_1.htm .. index_3.htm);
           每条 <li> 带标题、相对 href (./202610/t20261012_6488524.htm)
           与 发布时间 (发布时间：2026.10.12).
     NOTE: 该 host 上 http:// 会 301 跳 https://，本单元直接取 https，
     省一跳（实测 301 Location 即 https 同路径）。
  2. GET  正文页 -> 单个正文块 (div.TRS_Editor，位于 div.detail_content 内)，
     含两段「据农业农村部对…监测」句:
       据农业农村部对500个县集贸市场和采集点监测，上周（10月第2周，10月5—11日，下同），
       全国活猪价格为11.07元/公斤，环比跌0.5%；猪肉集贸市场价格为20.98元/公斤，环比跌0.4%。
       据农业农村部对200个批发市场监测调度，上周全国猪肉批发市场均价为15.92元/公斤，
       环比跌1.8%，同比低15.5%。

两段口径不同，**绝不合并**：前段是「500个县集贸市场和采集点」（活猪价、猪肉集贸价），
后段是「200个批发市场」（猪肉批发均价）；每行各自标注 monitor_scope。
若某句的口径标记与该品种的固有口径矛盾（实测 23 期未出现），该行按「口径无法可靠区分」
跳过留痕，不猜不混。

环比/同比为文字表述（「环比跌0.5%」「环比略跌0.4%」「环比持平」「同比低15.5%」），
解析为带符号数值：跌/降/低=-，涨/升/高=+，持平=0.0，取不到=None。
数值缺失一律记 None，**绝不用 0 或占位值填充**。

周次在标题/正文里（「10月第2周」），日期区间在正文里（「10月5—11日」）；两者都提取，
年份按发布时间锚定（跨月/跨年周用月份比较回推，见 _resolve_years）。

Encoding: 实测页面 UTF-8；_decode 仍防御性回退 gb18030，解码异常不致命。
No auth, no captcha, no cookies. TLS 为 CFCA OV (*.moa.gov.cn)；按仓内惯例
verify=False（不假设容器信任库）。国内源：仅直连，从不走代理。
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from lxml import html as lxml_html
from scrapling.fetchers import FetcherSession

logger = logging.getLogger("moa-hog-price")

LIST_URL = "https://www.moa.gov.cn/ztzl/szcpxx/scxxfb/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_ARTICLE_RE = re.compile(r"t\d{8}_\d+\.htm$")
_HREF_DATE_RE = re.compile(r"t(\d{4})(\d{2})(\d{2})_")
_PUB_RE = re.compile(r"(\d{4})[.\-/年](\d{1,2})[.\-/月](\d{1,2})")
_TITLE_FILTER_RE = re.compile(r"生猪|猪肉")
_WEEK_LABEL_RE = re.compile(r"(\d{1,2})月第(\d{1,2})周")
_RANGE_RE = re.compile(
    r"(\d{1,2})月(\d{1,2})日?\s*[—–\-~至]\s*(?:(\d{1,2})月)?(\d{1,2})日"
)
_SENT_RE = re.compile(r"据农业农村部对[^。]*。")
_CLAUSE_SPLIT_RE = re.compile(r"[；;。]")
# 「环比持平」不带百分号，故 % 可选；无数字时 _signed 返回 None（除非持平=0.0）
_MOM_RE = re.compile(
    r"环比\s*(略跌|略涨|下跌|上涨|下降|上升|跌|涨|降|升|持平)\s*([\d.]+)?\s*%?"
)
_YOY_RE = re.compile(
    r"同比\s*(略低|略高|低|高|跌|涨|降|升|持平)\s*([\d.]+)?\s*%?"
)

SCOPE_500 = "500个县集贸市场和采集点"
SCOPE_200 = "200个批发市场"
UNIT = "元/公斤"

# 品种 -> (价格正则, 该品种固有监测口径)
_ITEMS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("活猪", re.compile(r"(?:全国)?(?:活猪|生猪)价格为\s*([\d.]+)\s*元/公斤"), SCOPE_500),
    ("猪肉集贸", re.compile(r"猪肉集贸市场价格为\s*([\d.]+)\s*元/公斤"), SCOPE_500),
    ("猪肉批发", re.compile(r"猪肉批发(?:市场)?均价为\s*([\d.]+)\s*元/公斤"), SCOPE_200),
)

_NEG_WORDS = frozenset({"略跌", "下跌", "下降", "跌", "降", "低", "略低"})
_FLAT_WORDS = frozenset({"持平"})


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
    """标题类文本：空白折叠为单空格。"""
    return re.sub(r"\s+", " ", text or "").strip()


def _despace(text: str) -> str:
    """正文类文本：去掉全部空白（含 \\u3000），便于跨行句子正则。"""
    return re.sub(r"\s+", "", text or "")


def _signed(match: re.Match[str] | None) -> float | None:
    """把「环比跌0.5%」「同比低15.5%」「环比持平」解析成带符号数值。"""
    if match is None:
        return None
    word, num = match.group(1), match.group(2)
    if word in _FLAT_WORDS:
        return 0.0
    if num is None:
        return None
    value = float(num)
    return -value if word in _NEG_WORDS else value


def _resolve_years(
    sm: int, em: int, pub: tuple[int, int, int]
) -> tuple[int, int]:
    """按发布时间锚定周区间的起止年份（跨月/跨年周回推）。

    末段年份以发布月为锚：结束月 > 发布月 说明该周属于上一年（如 1 月初发布
    的 12 月末周）；起始月 > 结束月 说明跨年（如 12月29日—1月4日）。
    """
    py, pm = pub[0], pub[1]
    end_year = py - 1 if em > pm else py
    start_year = end_year - 1 if sm > em else end_year
    return start_year, end_year


def _parse_pubdate(*texts: str) -> tuple[int, int, int] | None:
    """从 meta PubDate / 列表发布时间 / href 内嵌日期中取 (y, m, d)。"""
    for text in texts:
        if not text:
            continue
        m = _PUB_RE.search(text)
        if m:
            return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None


def _parse_list_page(page_html: str) -> tuple[list[dict], int]:
    """解析市场动态列表页：返回 (报告条目, 总页数)。

    条目字段: url / title / pub_text / pub_date。标题按「生猪」「猪肉」过滤，
    非本栏目的其它动态不入列（列表页实测均为本栏目，仍按 brief 显式过滤）。
    """
    doc = lxml_html.fromstring(page_html)
    items: list[dict] = []
    seen: set[str] = set()
    for a in doc.xpath('//a[contains(@class, "content") and @href]'):
        href = (a.get("href") or "").strip()
        if not _ARTICLE_RE.search(href):
            continue
        url = urljoin(LIST_URL, href)
        if url in seen:
            continue
        title = _squash(a.get("title") or "")
        if not title:
            h5 = a.xpath(".//h5")
            title = _squash(h5[0].text_content()) if h5 else ""
        if not title or not _TITLE_FILTER_RE.search(title):
            continue
        pub_node = a.xpath('.//div[contains(@class, "pubTime")]')
        if not pub_node:
            parent = a.getparent()
            pub_node = parent.xpath('.//div[contains(@class, "pubTime")]') if parent is not None else []
        pub_text = _squash(pub_node[0].text_content()) if pub_node else ""
        m = _HREF_DATE_RE.search(href)
        href_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""
        seen.add(url)
        items.append(
            {
                "url": url,
                "title": title,
                "pub_text": pub_text,
                "pub_date": _parse_pubdate(pub_text, href_date),
            }
        )
    pages = 1
    m = re.search(r"var\s+countPage\s*=\s*(\d+)", page_html)
    if m:
        pages = max(int(m.group(1)), 1)
    return items, pages


def _extract_body_sentences(page_html: str) -> list[str]:
    """剥离导航噪音，取正文块里的「据农业农村部对…监测」句。"""
    doc = lxml_html.fromstring(page_html)
    # with_tail=False：保留被删节点之后的文本（正文块内联 <style> 后紧跟正文，
    # 用 getparent().remove() 会连 tail 一起丢掉）
    lxml_html.etree.strip_elements(doc, "script", "style", with_tail=False)
    nodes = doc.xpath('//div[contains(@class, "TRS_Editor")]')
    if not nodes:
        nodes = doc.xpath('//div[contains(@class, "detail_content")]')
    if not nodes:
        logger.warning("no detail body node found; falling back to whole page")
        nodes = [doc]
    sentences: list[str] = []
    for node in nodes:
        text = _despace(node.text_content())
        sentences.extend(_SENT_RE.findall(text))
    return sentences


def _parse_report(
    page_html: str,
    report: dict,
    scraped_at: str,
) -> list[dict]:
    """解析一期周报正文，产出每品种一行（活猪 / 猪肉集贸 / 猪肉批发）。"""
    sentences = _extract_body_sentences(page_html)
    if not sentences:
        logger.warning("no 监测 sentence in %s — skip report", report["url"])
        return []

    # 周次 + 日期区间（正文首段给出，两段口径共用同一监测周）
    week_label: str | None = None
    date_range: str | None = None
    joined = "".join(sentences)
    lm = ln = None
    m = _WEEK_LABEL_RE.search(joined)
    if m:
        lm, ln = int(m.group(1)), int(m.group(2))
    sm = em = sd = ed = None
    m = _RANGE_RE.search(joined)
    if m:
        sm, sd = int(m.group(1)), int(m.group(2))
        em = int(m.group(3)) if m.group(3) else sm
        ed = int(m.group(4))

    pub = report.get("pub_date")
    if lm is not None and pub:
        start_year, end_year = (
            _resolve_years(sm, em, pub) if sm is not None else (pub[0], pub[0])
        )
        if em is not None and lm == em:
            label_year = end_year
        elif sm is not None and lm == sm:
            label_year = start_year
        else:  # 标签月份不在区间内：按发布月回推
            label_year = pub[0] - 1 if lm > pub[1] else pub[0]
        week_label = f"{label_year}年{lm}月第{ln}周"
    elif lm is not None:
        logger.warning("no publish date for %s — week label left year-less", report["url"])
        week_label = f"{lm}月第{ln}周"
    else:
        logger.warning("no week label in %s", report["url"])

    if sm is not None and pub:
        start_year, end_year = _resolve_years(sm, em, pub)
        date_range = f"{start_year:04d}-{sm:02d}-{sd:02d}~{end_year:04d}-{em:02d}-{ed:02d}"
    elif sm is not None:
        logger.warning("no publish date for %s — date_range left unanchored", report["url"])
    else:
        logger.warning("no date range in %s", report["url"])

    rows: list[dict] = []
    for sentence in sentences:
        if SCOPE_200 in sentence:
            sentence_scope: str | None = SCOPE_200
        elif SCOPE_500 in sentence:
            sentence_scope = SCOPE_500
        else:
            sentence_scope = None  # 由品种固有口径兜底
        for clause in _CLAUSE_SPLIT_RE.split(sentence):
            for item, price_re, item_scope in _ITEMS:
                m = price_re.search(clause)
                if not m:
                    continue
                scope = sentence_scope or item_scope
                if sentence_scope is not None and sentence_scope != item_scope:
                    # 口径与品种矛盾：不猜不混，跳过留痕
                    logger.warning(
                        "scope conflict in %s: sentence=%s item=%s (%s) — skip row",
                        report["url"], sentence_scope, item, clause[:60],
                    )
                    continue
                rows.append(
                    {
                        "week_label": week_label,
                        "date_range": date_range,
                        "item": item,
                        "price": float(m.group(1)),
                        "unit": UNIT,
                        "mom_pct": _signed(_MOM_RE.search(clause)),
                        "yoy_pct": _signed(_YOY_RE.search(clause)),
                        "monitor_scope": scope,
                        "report_title": report["title"],
                        "report_url": report["url"],
                        "source": "moa-hog-price",
                        "scraped_at": scraped_at,
                    }
                )
    if not rows:
        logger.warning("no price row parsed from %s — skip report", report["url"])
    return rows


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
    return resp


async def _collect(limit: int, max_pages: int) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    reports_done = 0
    reports_failed = 0
    pages_walked = 0
    scraped_at = datetime.now(timezone.utc).isoformat()

    async with FetcherSession(
        impersonate="chrome120",
        timeout=25,
        verify=False,  # CFCA cert, per estate convention
        retries=1,     # exactly one attempt — no retry storms
    ) as session:
        resp = await _fetch(session, LIST_URL)
        if resp is None:
            logger.error("list page unavailable — returning 0 rows")
            return rows
        items, total_pages = _parse_list_page(_decode(resp.body))
        pages_walked = 1
        logger.info("list page: %d reports, %d pages available", len(items), total_pages)

        # 逐页取（列表页按发布时间倒序），不足 limit 才翻页 —— limit 真实生效
        page_no = 0
        while True:
            for report in items:
                if len(rows) >= limit:
                    break
                rresp = await _fetch(session, report["url"])
                if rresp is None:
                    reports_failed += 1
                    continue
                parsed = _parse_report(_decode(rresp.body), report, scraped_at)
                reports_done += 1
                for row in parsed:
                    key = (row["report_url"], row["item"])
                    if key in seen:
                        continue
                    seen.add(key)
                    if len(rows) < limit:
                        rows.append(row)
            if len(rows) >= limit:
                break
            page_no += 1
            # 已走过第 0 页（栏目根）；索引页为 index_1..index_{total_pages-1}
            if page_no >= min(total_pages, max_pages):
                break
            nresp = await _fetch(session, urljoin(LIST_URL, f"index_{page_no}.htm"))
            if nresp is None:
                break
            items, _ = _parse_list_page(_decode(nresp.body))
            pages_walked += 1
            if not items:
                break

    logger.info(
        "moa-hog-price: %d rows from %d reports (%d failed) over %d list page(s)",
        len(rows), reports_done, reports_failed, pages_walked,
    )
    return rows


def run_moa_hog_price(limit: int = 100, max_pages: int = 2) -> list[dict]:
    """Entry point for fd-runner (spiders/moa-hog-price).

    Args:
        limit: maximum rows to return (<= limit enforced; newest week first).
        max_pages: maximum list pages walked for report discovery (default 2,
            i.e. up to 12 reports); only walked when ``limit`` is not yet met.

    Returns:
        list of price row dicts (schema = manifest columns). Missing numbers
        are None; nothing is zero-filled.
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
        return await _collect(max(int(limit), 0), max(int(max_pages), 1))

    return asyncio.run(_run())[: max(int(limit), 0)]


if __name__ == "__main__":
    for r in run_moa_hog_price(limit=6):
        print(r)
