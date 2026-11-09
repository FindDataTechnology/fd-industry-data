"""samr-food-inspection — 市场监管总局食品安全监督抽检公告/通告.

Source: 国家市场监督管理总局 (SAMR) 政务公开 → 法定主动公开内容 →
食品抽检司栏目, https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/spcjs/.

Live data paths (measured 2026-11-07, UTC+8; 国内源，仅直连，从不走代理):
  1. GET  https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/spcjs/   (UTF-8, 200)
        -> 栏目壳页。正文列表由博达 jPaas 前端脚本从 API 拉取渲染，壳页里带有
           完整调用参数（<script ... webId/tplSetId/pageType/tagId/pageId
           queryData="{'parseType':...}">）。
  2. GET  https://www.samr.gov.cn/api-gateway/jpaas-publish-server/front/page/build/unit
           ?parseType=bulidstatic&webId=..&tplSetId=..&pageType=column
           &tagId=内容区域&editType=null&pageId=..
           &paramJson={"pageNo":1,"pageSize":20}      (JSON: {code,data.html})
        -> 列表 HTML 片段, 每页 20 条, 共 228 条 (rows=20 → 12 页)；
           每条: <li class="nav04Left02_content"><a href="..." title="标题">…</a></li>
                 <li class="nav04Left02_contenttime">2026-10-12</li>
           分页参数必须放在 paramJson 里（pageNo=2 放在 URL 参数上无效，实测返回第 1 页）。
  3. GET  正文页 https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/spcjs/art/<年>/art_<hash>.html
        -> 服务端整页渲染 (pagetype=1)，正文块 div.Three_xilan_07（标题 + 文号 + 段落），
           附件链接在 ul.contentLeft0102box（a/@title 形如
           「附件9 餐饮食品监督抽检不合格产品信息.xlsx」——批次通报的分类覆盖面只出现在附件名里）。
           页头另有 meta ArticleTitle / PubDate / Description 可作兜底。

字段级口径（任务 2.4，README 有同款规则表；矛盾时跳过留痕，不猜）:
  result_category:
    - unqualified  抽检结果类·不合格：标题或正文命中「不合格」（如 关于N批次食品抽检
                   不合格情况的通报/通告）——总局栏目只公开发布不合格结果，这是绝对主力;
    - qualified    抽检结果类·合格：全部合格/均未检出类通告（防御性分类，2022 年以来
                   栏目内未见实例）;
    - other        非抽检结果类：快检/补充检验方法公告、非食用物质名录、废止公告、
                   召回监管公告、对人大代表建议的答复、季度/年度抽检情况综合统计通告等;
    判定次序：标题规则优先（体裁由标题决定），标题判不出才用正文规则兜底；
    标题与正文同时给出且都在抽检结果类（unqualified/qualified）内又相互矛盾 →
    跳过该条并记 warning（留痕），绝不猜一个。
  restaurant_related: 标题 + 正文 + 附件名中命中 餐饮|食堂|餐馆（餐饮食品/餐饮具/
    餐饮服务的前缀族；附件名是分类覆盖面的唯一载体，必须纳入）。纯标注列，不过滤。
  notice_date: 列表页 contenttime 的 YYYY-MM-DD（锚定日期）；缺失时回退正文 meta
    PubDate 的日期部分，再缺失 = None。
  body_digest: 正文块（div.Three_xilan_07）去空白文本前 200 字符；正文块缺失时回退
    meta Description，再缺失 = None。

No auth, no captcha, no cookies. TLS 为 CFCA EV (*.samr.gov.cn)；按仓内惯例
verify=False（不假设容器信任库）。请求间 0.5–1.0s 抖动限速，retries=1 单次尝试。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlencode

from lxml import html as lxml_html
from scrapling.fetchers import FetcherSession

logger = logging.getLogger("samr-food-inspection")

COLUMN_URL = "https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/spcjs/"
SITE_BASE = "https://www.samr.gov.cn"
PAGE_SIZE = 20  # 站点分页原生每页条数（rows="20"）

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 壳页 AuthorizedRead 脚本标签里的 jPaas 调用参数（每次请求实时解析，不硬编码 pageId）；
# 注意 queryData 属性值是单引号 JSON（'key':'value' 带冒号），属性另有 key="value" 形式
_SHELL_ATTR_RES = {
    "api_url": re.compile(r'<script[^>]+src="[^"]*unitbuild\.js[^"]*"[^>]*\burl="([^"]+)"'),
    "web_id": re.compile(r'\bwebId="([0-9a-f]{32})"'),
    "tpl_set_id": re.compile(r"\btplSetId'?\s*[:=]\s*'?([0-9a-f]{32})"),
    "page_id": re.compile(r"\bpageId'?\s*[:=]\s*'?([0-9a-f]{32})"),
    "tag_id": re.compile(r"\btagId'?\s*[:=]\s*'?([^'\"&]+)"),
}

# 列表条目：链接行（日期在紧随其后的 contenttime 行，_parse_list_fragment 里配对）
_ITEM_XPATH = (
    '//li[contains(@class,"nav04Left02_content")]/a[@href]'
)
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

# ---- 字段级口径：标题规则（体裁由标题决定，首条命中即返回） ----
TITLE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # 检验/检测方法类公告（标准解读范畴）
    ("other", re.compile(r"快速检测|补充检验方法")),
    # 名录/废止/工作规范/答复/核查处置/召回等非抽检结果类
    ("other", re.compile(r"非食用物质名录|废止|工作规范|管理规定|建议的答复|核查处置|召回")),
    # 具体批次抽检不合格通报/通告（总局栏目只公开发布不合格结果）
    ("unqualified", re.compile(r"不合格")),
    # 全部合格类（防御性，栏目内未见实例）
    ("qualified", re.compile(r"抽检[^。]{0,20}合格")),
    # 季度/年度「监督抽检情况」综合统计通告（含合格率统计，非批次结果）
    ("other", re.compile(r"抽检情况")),
)
# ---- 字段级口径：正文规则（仅当标题判不出时兜底） ----
BODY_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("other", re.compile(r"(?:季度|年度|半年|上半年)[^。]{0,12}抽检|抽检情况")),
    ("qualified", re.compile(r"全部合格|均未检出")),
    ("unqualified", re.compile(r"检出[^。]{0,14}批次[^。]{0,8}不合格|不合格食品|不合格产品信息")),
)
# ---- 字段级口径：餐饮相关标注关键词（标题+正文+附件名） ----
RESTAURANT_RE = re.compile(r"餐饮|食堂|餐馆")

BODY_DIGEST_LEN = 200
RESULT_CATEGORIES = frozenset({"unqualified", "qualified", "other"})


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
    """正文类文本：去掉全部空白（含 \\u3000），便于跨行正则。"""
    return re.sub(r"\s+", "", text or "")


def _parse_list_fragment(fragment_html: str) -> list[dict]:
    """解析一页列表 HTML 片段：返回 [{url, title, list_date}]（保持页面顺序）。

    日期行紧跟在链接行之后，两者按兄弟节点配对；链接行 title 属性缺失时取
    链接文本。条目按 URL 去重（站点偶发跨页重复）。
    """
    doc = lxml_html.fromstring(fragment_html)
    items: list[dict] = []
    seen: set[str] = set()
    for a in doc.xpath(_ITEM_XPATH):
        href = (a.get("href") or "").strip()
        if "/art/" not in href:
            continue
        url = urljoin(SITE_BASE, href)
        if url in seen:
            continue
        seen.add(url)
        title = _squash(a.get("title") or a.text_content())
        # 日期在紧随的兄弟 <li class="...contenttime">
        date_text = ""
        li = a.getparent()
        if li is not None:
            sib = li.getnext()
            if sib is not None and "contenttime" in (sib.get("class") or ""):
                date_text = _squash(sib.text_content())
        items.append({"url": url, "title": title, "list_date": date_text})
    return items


def _parse_shell_attrs(shell_html: str) -> dict:
    """从栏目壳页提取 jPaas 列表 API 的调用参数（webId/pageId/tplSetId/tagId/url）。"""
    attrs: dict[str, str] = {}
    for key, rx in _SHELL_ATTR_RES.items():
        m = rx.search(shell_html)
        if not m:
            return {}
        attrs[key] = m.group(1)
    return attrs


def _build_list_api_url(attrs: dict, page_no: int) -> str:
    param_json = f'{{"pageNo":{int(page_no)},"pageSize":{PAGE_SIZE}}}'
    query = {
        "parseType": "bulidstatic",
        "webId": attrs["web_id"],
        "tplSetId": attrs["tpl_set_id"],
        "pageType": "column",
        "tagId": attrs["tag_id"],
        "editType": "null",
        "pageId": attrs["page_id"],
        "paramJson": param_json,
    }
    return urljoin(SITE_BASE, attrs["api_url"]) + "?" + urlencode(query)


def _extract_article(page_html: str) -> dict:
    """解析正文页：body 块文本、附件名列表、meta 兜底（标题/日期/摘要）。"""
    doc = lxml_html.fromstring(page_html)
    meta_title = (doc.xpath('//meta[@name="ArticleTitle"]/@content') or [""])[0]
    meta_pub = (doc.xpath('//meta[@name="PubDate"]/@content') or [""])[0]
    meta_desc = (doc.xpath('//meta[@name="Description"]/@content') or [""])[0]
    body_nodes = doc.xpath('//div[contains(@class,"Three_xilan_07")]')
    body_text = _despace(body_nodes[0].text_content()) if body_nodes else ""
    attach_titles = [
        _despace(a.get("title") or a.text_content() or "")
        for a in doc.xpath(
            '//a[contains(@href,".xlsx") or contains(@href,".docx")'
            ' or contains(@href,".doc") or contains(@href,".pdf")]'
        )
    ]
    return {
        "meta_title": _squash(meta_title),
        "meta_pub": _squash(meta_pub),
        "meta_desc": _squash(meta_desc),
        "body_text": body_text,
        "attach_titles": [t for t in attach_titles if t],
    }


def classify_title(title: str) -> str | None:
    """标题规则（首条命中即返回）：体裁由标题决定。"""
    for cat, rx in TITLE_RULES:
        if rx.search(title or ""):
            return cat
    return None


def classify_body(body_text: str) -> str | None:
    """正文规则兜底（仅当标题判不出时使用；正文 = 正文块 + 附件名）。"""
    for cat, rx in BODY_RULES:
        if rx.search(body_text or ""):
            return cat
    return None


def _resolve_notice_date(list_date: str, meta_pub: str) -> str | None:
    """notice_date：列表页日期优先（锚定），缺失时回退正文 meta PubDate 日期部分。"""
    for text in (list_date, meta_pub):
        m = _DATE_RE.search(text or "")
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def _build_row(page_html: str, item: dict, scraped_at: str) -> dict | None:
    """一条公告 → 一行。矛盾跳过留痕；缺字段记 None，不猜不填充。"""
    article = _extract_article(page_html)
    title = item["title"] or article["meta_title"]
    if not title:
        logger.warning("no title for %s — skip row", item["url"])
        return None

    body_text = article["body_text"]
    digest_source = body_text or _despace(article["meta_desc"])
    body_digest = digest_source[:BODY_DIGEST_LEN] if digest_source else None
    if not article["body_text"]:
        logger.warning(
            "no Three_xilan_07 body block in %s — digest falls back to meta Description",
            item["url"],
        )

    full_text = body_text + "".join(article["attach_titles"])
    title_cat = classify_title(title)
    body_cat = classify_body(full_text)
    # 口径矛盾（双方都给出抽检结果类结论且相左）→ 跳过留痕，不猜。
    # title=other 是体裁结论，与正文谈论抽检不合格不构成矛盾。
    if (
        title_cat in ("unqualified", "qualified")
        and body_cat in ("unqualified", "qualified")
        and title_cat != body_cat
    ):
        logger.warning(
            "classification conflict for %s: title=%s body=%s — skip row (留痕)",
            item["url"], title_cat, body_cat,
        )
        return None
    result_category = title_cat or body_cat
    if result_category is None:
        logger.warning("no classification signal for %s — result_category=None", item["url"])

    restaurant_related = bool(RESTAURANT_RE.search(title) or RESTAURANT_RE.search(full_text))

    notice_date = _resolve_notice_date(item["list_date"], article["meta_pub"])
    if notice_date is None:
        logger.warning("no notice date for %s — notice_date=None", item["url"])
    if item["list_date"] and article["meta_pub"]:
        meta_date = _resolve_notice_date("", article["meta_pub"])
        if meta_date and notice_date and meta_date != notice_date:
            logger.warning(
                "date drift for %s: list=%s meta=%s — keeping list date (anchored)",
                item["url"], item["list_date"], meta_date,
            )

    return {
        "notice_date": notice_date,
        "title": title,
        "url": item["url"],
        "result_category": result_category,
        "restaurant_related": restaurant_related,
        "body_digest": body_digest,
        "source": "samr-food-inspection",
        "scraped_at": scraped_at,
    }


async def _fetch(session: FetcherSession, url: str):
    """单次 GET；异常/非 200 返回 None（记 warning，不重试、不绕反爬）。"""
    try:
        resp = await session.get(url, headers={"User-Agent": USER_AGENT})
    except Exception as e:  # noqa: BLE001 — per-URL log-and-skip
        logger.warning("fetch failed %s: %s", url, e)
        return None
    if resp.status != 200:
        logger.warning("status=%s %s", resp.status, url)
        return None
    return resp


async def _collect(limit: int, max_pages: int) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    notices_done = 0
    notices_failed = 0
    pages_walked = 0
    scraped_at = datetime.now(timezone.utc).isoformat()

    async with FetcherSession(
        impersonate="chrome120",
        timeout=30,
        verify=False,  # CFCA EV cert, per estate convention
        retries=1,     # exactly one attempt — no retry storms
    ) as session:
        # 1) 栏目壳页 → 解析 jPaas 列表 API 参数
        resp = await _fetch(session, COLUMN_URL)
        if resp is None:
            raise RuntimeError(
                f"samr-food-inspection: column shell unavailable: {COLUMN_URL}"
            )
        attrs = _parse_shell_attrs(_decode(resp.body))
        if not attrs:
            raise RuntimeError(
                "samr-food-inspection: cannot extract jPaas queryData "
                f"(webId/pageId/tagId) from column shell {COLUMN_URL}"
            )
        await asyncio.sleep(0.6)

        # 2) 逐页取列表（发布时间倒序），凑满 limit 为止
        page_no = 1
        candidates: list[dict] = []
        while len(candidates) < limit and page_no <= max(int(max_pages), 1):
            api_url = _build_list_api_url(attrs, page_no)
            resp = await _fetch(session, api_url)
            if resp is None:
                logger.warning("list API page %d unavailable — stop paging", page_no)
                break
            try:
                payload = json.loads(_decode(resp.body))
                fragment = (payload.get("data") or {}).get("html") or ""
            except Exception as e:  # noqa: BLE001 — 解析失败按无此页处理
                logger.warning("bad list API payload page %d: %s", page_no, e)
                break
            items = _parse_list_fragment(fragment)
            pages_walked += 1
            if not items:
                break
            for it in items:
                if it["url"] not in seen:
                    seen.add(it["url"])
                    candidates.append(it)
            page_no += 1
            if page_no <= max(int(max_pages), 1):
                await asyncio.sleep(0.6)

        # 3) 逐条取正文 → 一行（请求间 0.5–0.9s 抖动限速）
        for item in candidates[: max(limit, 0)]:
            if len(rows) >= limit:
                break
            resp = await _fetch(session, item["url"])
            await asyncio.sleep(0.5 + 0.1 * (notices_done % 5))
            if resp is None:
                notices_failed += 1
                continue
            row = _build_row(_decode(resp.body), item, scraped_at)
            if row is not None:
                rows.append(row)
            notices_done += 1

    logger.info(
        "samr-food-inspection: %d rows from %d notices (%d failed) over %d list page(s)",
        len(rows), notices_done, notices_failed, pages_walked,
    )
    return rows


def run_samr_food_inspection(limit: int = 20, max_pages: int = 2) -> list[dict]:
    """Entry point for fd-runner (spiders/samr-food-inspection).

    Args:
        limit: maximum rows to return (one row per announcement, newest first).
        max_pages: maximum list pages walked for announcement discovery
            (default 2, i.e. up to 40 candidates); only walked while
            ``limit`` is not yet met.

    Returns:
        list of announcement row dicts (schema = manifest columns).
        Missing fields are None; nothing is zero-filled. Column shell or
        jPaas queryData unavailable raises RuntimeError (hard failure);
        a single article page failing is skipped with a warning.
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
    for r in run_samr_food_inspection(limit=6):
        print(r)
