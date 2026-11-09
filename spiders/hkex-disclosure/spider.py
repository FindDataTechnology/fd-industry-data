"""hkex-disclosure — HKEXnews 披露易标题搜索（港股公告事件面）.

Source: HKEXnews 披露易 title search.
  https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=zh

本车道属性：金融车道扩展。**2026-10-07 侦察簿边界注记**：裁决理由 = 防重叠；
港股（HKEX 披露易）在平台此前**无既有财报车道**，本扩展不构成与既有财报线的
重叠。本单元只取公告**标题与元数据**（事件面），不解析财报正文全文。

Live data paths (measured 2026-11-07, UTC+8; HTTPS, no auth/cookies/captcha):
  1. GET https://www1.hkexnews.hk/search/prefix.do
         ?callback=callback&lang=ZH&type=A&name=<code>&market=SEHK
     -> ``callback({"more":"1","stockInfo":[{"stockId":199151,"code":"06862",
        "name":"海底撈"}, ...]});``
     ``stockId`` is HKEX's **internal numeric id**, not the ticker. Resolved
     per stock by *exact* ``code`` match (never by position), then cached in a
     process-level dict (``_STOCK_ID_CACHE``). A code with no exact match is a
     hard failure — the universe contract is not silently shrunk.
  2. GET https://www1.hkexnews.hk/search/titleSearchServlet.do
         ?sortDir=0&sortByOptions=DateTime&category=0&market=SEHK
         &stockId=<internal id>&documentType=-1&fromDate=YYYYMMDD
         &toDate=YYYYMMDD&title=&searchType=1&t1code=<all>&t2Gcode=-2
         &t2code=-2&rowRange=<N>&lang=<EN|ZH>
     -> pure JSON (also accepts a ``callback(...)`` wrapper; both handled):
        ``{"result": "<stringified JSON array>", "hasNextRow": false,
           "recordCnt": 36, "loadedRecord": 36, "rowRange": 100, ...}``
     ``result`` is a **string** and needs a second ``json.loads``. Each row:
        ``{NEWS_ID, STOCK_CODE, STOCK_NAME, TITLE, DATE_TIME "DD/MM/YYYY HH:MM",
           LONG_TEXT (official category), FILE_LINK, FILE_TYPE, ...}``.
     Paging is *not* an offset: the servlet has no page number. Its ``rowRange``
     is a max-rows lever, so page N is requested with ``rowRange=100*N`` and the
     rows are merged by ``NEWS_ID``. ``hasNextRow`` staying true after
     ``max_pages`` is a hard failure (no silent partial data).
  3. EN and ZH are **separate document rows** (different NEWS_ID; ZH PDFs end
     ``_c.pdf``). measured 2026-11-07: over the 10-name universe the EN and ZH
     lists are 1:1 in row count, order and DATE_TIME, so ``title`` (EN) and
     ``title_zh`` (ZH) are joined positionally after verifying that alignment;
     when alignment breaks the join falls back to a per-DATE_TIME queue and
     leaves ``title_zh=None`` for unmatched rows (never mis-assigned).

``t1code`` — the brief's URL carried ``t1code=40000``; measured 2026-11-07 that
code restricts the feed to *Financial Statements/ESG Information* only
(海底捞 2026 YTD: 3 rows vs 36 with all types) and would silently drop 盈警 /
月報表 / 一般公告. This unit therefore requests **all** document types
(``T1CODE_ALL = "-1"``; ``-2`` and ``40100`` measured identical to ``-1``).
See README evidence table.

ann_type rules (explicit table, first match in priority order wins; every
matched rule is recorded in ``ann_type_hits``; no inference beyond literals):
  1. profit-warning  盈警 | 盈利警告 | 盈利預警 | 盈利预警 | profit warning
  2. profit-alert    盈喜 | 正面盈利预告 | 正面盈利預告 | positive profit alert
  3. results         中期業績/中期业绩/年度業績/年度业绩/季度業績/季度业绩 |
                     interim results/annual results/quarterly results |
                     年報/年报/年度報告/年度报告/中期報告/中期报告 |
                     interim report/annual report
  4. monthly-returns 月報表/月报表 | monthly return
  5. other           (no literal hit)
HKEX serves **traditional** Chinese, so 盈利預警 (not 盈利警告) is the observed
盈警 spelling and 正面盈利預告 the observed 盈喜 spelling — both are in the
table. Bare ``盈利預告`` / ``PROFIT ALERT`` are deliberately NOT mapped: the
direction (盈警 vs 盈喜) is not in the genre word, and the brief forbids guessing.

字段可得性边界：本单元给标题 + 元数据（事件面），并对 **results 类公告**额外做
**最小化头条抽取**（``headline_*`` 字段）：只从公告 PDF 的文本层里按固定标签行
读「Revenue / 归母利润」两个数，连同报告期、币种、单位、原文行一起入 payload。
这不是「PDF 全文解析」——不做版面重建/表格推断/OCR；标签不在白名单、单位缺失、
同一概念出现两个值、利润行与营收行不在同一张表——一律 ``headline_status=
unavailable`` + 原因码（绝不用正则猜数字）。抽取口径全文见
``docs/hkex-results-headline-caliber.md``，实现在同目录 ``hkex_headline.py``。

**细分 KPI 面（task 3.5 一期）**：同一份 PDF 文本层里再做一层**白名单标签行**抽取
（``kpi_<slug>_*`` 字段），一期只登记 06862 海底捞（11 项）与 09922 九毛九（13 项）
——翻台率、人均消费、同店销售额、同店家数、门店数、同店日均销售同比。每项必须带
**报告期 + 分部/范围 + 单位 + 逐字标签行 + 公告脚注原文**；分区标题 + 脚注号 + 行标签
三者逐字相等才取，数值按 ``-layout`` 字符列位落列，金额千元归一为原币整数，百分比
按百分数存，绝不从别的数字推算（无标签化的同店增长率 → ``no_labelled_line``）。
未登记公司显式写 ``kpi_status=not_applicable`` + ``kpi_reason=no_phase1_kpi_target``。
口径全文见 ``docs/hkex-segment-kpi-caliber.md``，实现在 ``hkex_segment_kpi.py``。
两类数字都**不写 semantic_observations**：业绩期为半年（粒度词表无 ``half``）且
单次窗口内期数 <12，趋势门（``_common.gate_periods``）不允许入库——见
``docs/restaurant-lane-mapping.md`` §1 与单元 README §4/§4.1。可查询路径 =
``crawl_items.payload`` 的 ``headline_*`` / ``kpi_*`` 字段（事件与数字同一条记录）。
一次下载/一次抽文同时喂两个面，不为增值面重复取数。

No auth, no cookies. TLS kept verifiable=False per repo convention (containers
do not ship the CA bundle). 0.4–0.9s jitter between requests, retries=1 — one
attempt, never a retry storm, never a proxy/anti-bot workaround.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from html import unescape
from urllib.parse import urlencode

from scrapling.fetchers import FetcherSession

from fd_industry_data import pdftext, universes

def _load_headline_extractor():
    """加载同目录 ``hkex_headline.py`` 的 ``extract_headline``。

    单元目录名带连字符，不能作为包导入；runner 会把单元目录放进 sys.path，
    但 ``health.guard.load_unit_entry`` 按文件路径加载且**不加** sys.path，
    因此这里用 ``__file__`` 定位显式加载，两条加载路径都能工作。
    """
    import importlib.util
    import os
    import sys

    try:  # 若将来目录改为合法包名，优先走包相对导入
        from .hkex_headline import extract_headline
        return extract_headline
    except ImportError:
        pass
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hkex_headline.py")
    spec = importlib.util.spec_from_file_location("hkex_disclosure_headline", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load headline extractor from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.extract_headline


def _load_kpi_module():
    """加载同目录 ``hkex_segment_kpi.py``（细分 KPI，task 3.5 一期）。

    与 ``_load_headline_extractor`` 同一加载姿态（单元目录名带连字符）。
    """
    import importlib.util
    import os
    import sys

    try:
        from . import hkex_segment_kpi as module
        return module
    except ImportError:
        pass
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "hkex_segment_kpi.py")
    spec = importlib.util.spec_from_file_location("hkex_disclosure_segment_kpi", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load segment KPI extractor from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


extract_headline = _load_headline_extractor()
segment_kpi = _load_kpi_module()

logger = logging.getLogger("hkex-disclosure")

SOURCE = "hkex-disclosure"

BASE_URL = "https://www1.hkexnews.hk"
PREFIX_URL = f"{BASE_URL}/search/prefix.do"
TITLE_SEARCH_URL = f"{BASE_URL}/search/titleSearchServlet.do"
REFERER = f"{BASE_URL}/search/titlesearch.xhtml?lang=zh"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# HKEX dates are published in Hong Kong time (UTC+8); the window is computed in
# HK time so a run at 00:30 HKT still includes that day's announcements.
HK_TZ = timezone(timedelta(hours=8))

# The servlet pages by growing rowRange (no page offset). 100 rows/page step.
ROW_RANGE_STEP = 100
# All document types. See module docstring: t1code=40000 is Financial
# Statements/ESG only (measured 3 vs 36 rows for 06862 in 2026 YTD).
T1CODE_ALL = "-1"

# 默认监控宇宙来自注册表 universes/hk.yaml（vertical-lane-generalization）。
# 本单元 MUST NOT 硬编码公司列表：新增垂直线只登记注册表，不改这里。
# code 是 HKEX 5 位零填充 ticker；name 仅作人读标签，权威名仍取上游
# ``stockInfo.name``。
DEFAULT_REGION = "HK"


@lru_cache(maxsize=1)
def _default_stocks() -> tuple[tuple[str, str], ...]:
    """注册表驱动的默认宇宙（顺序即注册表顺序）。

    注册表不可用时硬失败 —— 不静默回落到内置列表，否则「没配」与「配了空」
    无法区分。见 ``fd_industry_data/universes.py`` 的失败契约。
    """

    return universes.region_pairs(DEFAULT_REGION)

# ---- ann_type 显式规则表（优先级即元组顺序；见模块文档） ----
ANN_TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "profit-warning",
        ("盈警", "盈利警告", "盈利預警", "盈利预警", "profit warning"),
    ),
    (
        "profit-alert",
        ("盈喜", "正面盈利预告", "正面盈利預告", "positive profit alert"),
    ),
    (
        "results",
        (
            "中期業績", "中期业绩", "年度業績", "年度业绩",
            "季度業績", "季度业绩", "interim results", "annual results",
            "quarterly results", "年報", "年报", "年度報告", "年度报告",
            "中期報告", "中期报告", "interim report", "annual report",
        ),
    ),
    (
        "monthly-returns",
        ("月報表", "月报表", "monthly return"),
    ),
)
ANN_TYPE_OTHER = "other"
ANN_TYPE_LABELS = tuple(t for t, _ in ANN_TYPE_RULES) + (ANN_TYPE_OTHER,)

_DT_RE = re.compile(
    r"^(\d{2})/(\d{2})/(\d{4})(?:\s+(\d{2}):(\d{2}))?$"
)
_WS_RE = re.compile(r"\s+")

# Process-level stockId cache: code -> (stockId, upstream_name).
_STOCK_ID_CACHE: dict[str, tuple[int, str | None]] = {}


class SpiderError(RuntimeError):
    """Hard failure for this unit (repo has no shared SpiderError base).

    ``RuntimeError`` is the repo convention (see spiders/samr-food-inspection);
    subclassing keeps call sites / golden replay able to catch it specifically.
    Any raise means: do not return a partial payload, surface the run as failed.
    """


def _decode(body: bytes | str) -> str:
    if isinstance(body, str):
        return body
    return body.decode("utf-8", errors="replace")


def _unwrap_json(text: str) -> dict:
    """Accept pure JSON or a ``callback(...)`` / JSONP wrapper."""
    s = (text or "").strip()
    if not s:
        raise SpiderError("hkex-disclosure: empty response body")
    if not s.startswith("{") and not s.startswith("["):
        # callback({...});  /  jQuery123({...});
        m = re.match(r"^[A-Za-z_$][\w$.]*\s*\((.*)\)\s*;?\s*$", s, re.S)
        if not m:
            raise SpiderError(f"hkex-disclosure: unparseable wrapper: {s[:120]!r}")
        s = m.group(1)
    try:
        payload = json.loads(s)
    except json.JSONDecodeError as e:
        raise SpiderError(f"hkex-disclosure: invalid JSON payload: {e}: {s[:120]!r}") from e
    if not isinstance(payload, dict):
        raise SpiderError(f"hkex-disclosure: payload is {type(payload).__name__}, expected object")
    return payload


def _clean_title(text: str | None) -> str | None:
    """HTML entities + embedded newlines -> one clean line; empty -> None."""
    if not text:
        return None
    cleaned = _WS_RE.sub(" ", unescape(str(text))).strip()
    return cleaned or None


def _parse_release_date(raw: str | None) -> str | None:
    """``DD/MM/YYYY HH:MM`` (HK time) -> ``YYYY-MM-DD``; unparseable -> None."""
    m = _DT_RE.match((raw or "").strip())
    if not m:
        return None
    day, month, year, *_ = m.groups()
    return f"{year}-{month}-{day}"


def _doc_url(file_link: str | None) -> str | None:
    link = (file_link or "").strip()
    if not link:
        return None
    if link.startswith("http://") or link.startswith("https://"):
        return link
    return BASE_URL + ("" if link.startswith("/") else "/") + link


def classify_ann_type(*texts: str | None) -> tuple[str, list[str]]:
    """Apply ANN_TYPE_RULES to the supplied title texts.

    Returns ``(ann_type, ann_type_hits)`` where ``ann_type`` is the first rule
    (in table order) that matched and ``ann_type_hits`` lists every matched
    rule as ``"<ann_type>:<matched literal>"`` in table order. No match -> the
    literal ``other`` with an empty hit list (never a guessed bucket).
    """
    haystack = " ".join(t for t in texts if t).casefold()
    hits: list[str] = []
    chosen: str | None = None
    for ann_type, needles in ANN_TYPE_RULES:
        for needle in needles:
            if needle.casefold() in haystack:
                hits.append(f"{ann_type}:{needle}")
                if chosen is None:
                    chosen = ann_type
                break  # one literal per rule is enough evidence
    return chosen or ANN_TYPE_OTHER, hits


def _normalize_stocks(stocks) -> list[tuple[str, str | None]]:
    """Accept ``"06862"`` / ``("06862", "海底捞")`` / ``["06862", ...]``.

    Codes are zero-padded to the HKEX 5-digit form (``"6862" -> "06862"``) and
    de-duplicated preserving order.
    """
    if stocks is None:
        stocks = _default_stocks()
    if isinstance(stocks, str):
        stocks = [stocks]
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for item in stocks:
        if isinstance(item, str):
            code, name = item, None
        elif isinstance(item, (list, tuple)) and item:
            code = item[0]
            name = item[1] if len(item) > 1 else None
        else:
            raise SpiderError(f"hkex-disclosure: bad stocks entry {item!r}")
        code = str(code).strip().zfill(5)
        if not code.isdigit():
            raise SpiderError(f"hkex-disclosure: stock code {code!r} is not numeric")
        if code in seen:
            continue
        seen.add(code)
        out.append((code, name))
    if not out:
        raise SpiderError("hkex-disclosure: empty stock universe")
    return out


def _window(days_back: int) -> tuple[str, str]:
    """``(fromDate, toDate)`` as YYYYMMDD in HK time, inclusive of today."""
    today = datetime.now(HK_TZ).date()
    days = max(int(days_back), 1)
    return (today - timedelta(days=days)).strftime("%Y%m%d"), today.strftime("%Y%m%d")


async def _fetch(session: FetcherSession, url: str):
    """Single GET; non-200/exception -> SpiderError (fail loud, never partial)."""
    try:
        resp = await session.get(
            url, headers={"User-Agent": USER_AGENT, "Referer": REFERER}
        )
    except Exception as e:  # noqa: BLE001 — surface as unit failure
        raise SpiderError(f"hkex-disclosure: fetch failed {url}: {e}") from e
    if resp.status != 200:
        raise SpiderError(f"hkex-disclosure: status={resp.status} {url}")
    return resp


async def _resolve_stock_id(
    session: FetcherSession, code: str
) -> tuple[int, str | None]:
    """``prefix.do`` -> internal stockId, exact-code match only; cached."""
    cached = _STOCK_ID_CACHE.get(code)
    if cached is not None:
        return cached
    url = PREFIX_URL + "?" + urlencode(
        {"callback": "callback", "lang": "ZH", "type": "A", "name": code, "market": "SEHK"}
    )
    resp = await _fetch(session, url)
    payload = _unwrap_json(_decode(resp.body))
    infos = payload.get("stockInfo") or []
    if not isinstance(infos, list):
        raise SpiderError(f"hkex-disclosure: prefix.do stockInfo is not a list for {code}")
    for info in infos:
        if not isinstance(info, dict):
            continue
        if str(info.get("code", "")).strip() == code and info.get("stockId") is not None:
            stock_id = int(info["stockId"])
            name = info.get("name") or None
            _STOCK_ID_CACHE[code] = (stock_id, name)
            return stock_id, name
    candidates = ", ".join(
        f"{i.get('code')}:{i.get('name')}" for i in infos if isinstance(i, dict)
    ) or "none"
    raise SpiderError(
        f"hkex-disclosure: no exact stockId for {code} in prefix.do (candidates: {candidates})"
    )


def _search_url(stock_id: int, from_date: str, to_date: str, row_range: int, lang: str) -> str:
    query = {
        "sortDir": 0,
        "sortByOptions": "DateTime",
        "category": 0,
        "market": "SEHK",
        "stockId": stock_id,
        "documentType": -1,
        "fromDate": from_date,
        "toDate": to_date,
        "title": "",
        "searchType": 1,
        "t1code": T1CODE_ALL,
        "t2Gcode": -2,
        "t2code": -2,
        "rowRange": row_range,
        "lang": lang,
    }
    return TITLE_SEARCH_URL + "?" + urlencode(query)


async def _search_stock(
    session: FetcherSession,
    stock_id: int,
    from_date: str,
    to_date: str,
    lang: str,
    max_pages: int,
) -> tuple[list[dict], int]:
    """Fetch one stock's announcements for one language.

    Pages by growing ``rowRange`` (100*page). Returns the merged, de-duplicated
    row list plus pages walked. Raises on parse failure, a ``recordCnt`` vs
    delivered-rows shortfall, or ``hasNextRow`` still true at ``max_pages``
    (the last case is the "loop did not converge" red flag — a partial list is
    never returned as if complete).
    """
    merged: dict[str, dict] = {}
    record_cnt = 0
    page = 1
    while True:
        url = _search_url(stock_id, from_date, to_date, ROW_RANGE_STEP * page, lang)
        resp = await _fetch(session, url)
        payload = _unwrap_json(_decode(resp.body))
        raw = payload.get("result")
        if raw is None:
            raise SpiderError(
                f"hkex-disclosure: titleSearchServlet reply has no 'result' "
                f"(stockId={stock_id} lang={lang} page={page})"
            )
        if isinstance(raw, str):
            try:
                rows = json.loads(raw)
            except json.JSONDecodeError as e:
                raise SpiderError(
                    f"hkex-disclosure: 'result' is not JSON "
                    f"(stockId={stock_id} lang={lang} page={page}): {e}"
                ) from e
        else:
            rows = raw
        if not isinstance(rows, list):
            raise SpiderError(
                f"hkex-disclosure: 'result' decoded to {type(rows).__name__}, expected list "
                f"(stockId={stock_id} lang={lang} page={page})"
            )
        try:
            record_cnt = int(payload.get("recordCnt") or 0)
        except (TypeError, ValueError) as e:
            raise SpiderError(
                f"hkex-disclosure: non-numeric recordCnt {payload.get('recordCnt')!r}"
            ) from e
        for row in rows:
            if not isinstance(row, dict):
                raise SpiderError(
                    f"hkex-disclosure: result row is {type(row).__name__}, expected object"
                )
            key = str(row.get("NEWS_ID") or "") or (
                f"{row.get('DATE_TIME')}|{row.get('FILE_LINK')}"
            )
            merged.setdefault(key, row)
        has_next = bool(payload.get("hasNextRow"))
        if not has_next:
            break
        if page >= max(int(max_pages), 1):
            raise SpiderError(
                f"hkex-disclosure: hasNextRow still true after {page} page(s) "
                f"(stockId={stock_id} lang={lang} recordCnt={record_cnt}, "
                f"delivered={len(merged)}) — refusing to return a partial list"
            )
        page += 1
        await asyncio.sleep(0.4)
    if record_cnt and len(merged) < record_cnt:
        raise SpiderError(
            f"hkex-disclosure: short read (stockId={stock_id} lang={lang}): "
            f"recordCnt={record_cnt} but only {len(merged)} distinct row(s) delivered"
        )
    return list(merged.values()), page


def _merge_titles(
    en_rows: list[dict], zh_rows: list[dict]
) -> list[tuple[dict, str | None]]:
    """Pair EN rows with ZH titles without ever mis-assigning.

    Fast path: identical counts + identical DATE_TIME sequence -> positional
    zip (measured 1:1 for all 10 names on 2026-11-07). Fallback: per-DATE_TIME
    FIFO queue; an EN row with no ZH counterpart keeps ``title_zh=None``.
    """
    aligned = len(en_rows) == len(zh_rows) and all(
        str(a.get("DATE_TIME")) == str(b.get("DATE_TIME"))
        for a, b in zip(en_rows, zh_rows)
    )
    if aligned:
        return [(a, b.get("TITLE")) for a, b in zip(en_rows, zh_rows)]

    buckets: dict[str, deque] = defaultdict(deque)
    for b in zh_rows:
        buckets[str(b.get("DATE_TIME"))].append(b.get("TITLE"))
    out: list[tuple[dict, str | None]] = []
    unmatched = 0
    for a in en_rows:
        queue = buckets.get(str(a.get("DATE_TIME")))
        if queue:
            out.append((a, queue.popleft()))
        else:
            out.append((a, None))
            unmatched += 1
    if unmatched:
        logger.warning(
            "hkex-disclosure: EN/ZH not aligned (%d EN vs %d ZH rows); "
            "%d EN row(s) have no ZH title",
            len(en_rows), len(zh_rows), unmatched,
        )
    return out


def _headline_defaults() -> dict:
    """Every row carries the same headline_* keys (manifest schema is uniform)."""
    return {
        "headline_status": "not_applicable",
        "headline_reason": None,
        "headline_period_end": None,
        "headline_period_months": None,
        "headline_period_label": None,
        "headline_currency": None,
        "headline_unit": None,
        "headline_revenue": None,
        "headline_net_income": None,
        "headline_evidence": [],
    }


def _kpi_defaults(stock_code: str) -> dict:
    """每行都带的三个 KPI 面字段；``kpi_<slug>_*`` 只落在被解析的 results 行上。

    非 06862/09922 的公司显式写 ``not_applicable`` + ``no_phase1_kpi_target``
    （不是静默省略，也不是空串冒充）。
    """
    return segment_kpi.kpi_defaults(stock_code)


# results 类公告的期别只从标题字面判定（不猜）：interim/annual/quarterly。
_KIND_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("interim", ("interim", "中期", "six months")),
    ("annual", ("annual", "年度", "year ended", "年報", "年报")),
    ("quarterly", ("quarterly", "季度", "three months", "nine months")),
)
# 业绩公告 vs 正文报告：公告（results/業績）优先，正文报告兜底。
_ANNOUNCEMENT_RE = re.compile(r"results|業績|业绩", re.I)
_REPORT_RE = re.compile(r"annual report|interim report|年報|年报|中期報告|中期报告", re.I)

MAX_PDF_BYTES = 30 * 1024 * 1024


def _results_kind(title: str | None) -> str | None:
    t = (title or "").casefold()
    for kind, markers in _KIND_MARKERS:
        if any(m in t for m in markers):
            return kind
    return None


def _is_results_announcement(title: str | None) -> bool:
    t = title or ""
    return bool(_ANNOUNCEMENT_RE.search(t)) and not _REPORT_RE.search(t)


def _select_headline_rows(rows: list[dict]) -> dict[int, str]:
    """Per (stock, period kind) pick the row to parse.

    Returns ``{id(row): kind}``. The latest *results announcement* wins; when a
    stock has no announcement in the window, the latest report row is parsed
    instead (its title usually states no reporting period, so it yields 不可得
    rather than an older number silently standing in for the latest period).
    """
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("ann_type") != "results":
            continue
        kind = _results_kind(row.get("title"))
        if kind is None:
            continue
        buckets[(row["stock_code"], kind)].append(row)
    chosen: dict[int, str] = {}
    for (_code, kind), group in buckets.items():
        anns = [r for r in group if _is_results_announcement(r.get("title"))]
        pool = anns or group
        pool.sort(key=lambda r: (r["release_date"], bool(_is_results_announcement(r.get("title")))),
                  reverse=True)
        chosen[id(pool[0])] = kind
    return chosen


async def _enrich_headlines(session: FetcherSession, rows: list[dict]) -> None:
    """Attach ``headline_*`` + ``kpi_*`` to the selected results rows; never raises.

    Only the row chosen per (stock, period kind) is fetched/parsed; every other
    results row records ``not_selected`` with a reason naming the chosen event.
    A failed/unparseable PDF is an explicit ``unavailable`` record on that row
    (reason code + the lines that caused it), never an aborted run and never a
    guessed number.

    The segment-KPI face (``kpi_<slug>_*``, task 3.5 一期) is read from the
    **same text layer** already extracted for the headline numbers — one PDF
    fetch, two faces. Companies with no registered KPI spec keep
    ``kpi_status='not_applicable'`` + ``kpi_reason='no_phase1_kpi_target'``.
    """
    chosen = _select_headline_rows(rows)
    chosen_date: dict[tuple[str, str], str] = {
        (r["stock_code"], chosen[id(r)]): r["release_date"] for r in rows if id(r) in chosen
    }

    for row in rows:
        if row.get("ann_type") != "results":
            continue
        code = row["stock_code"]
        kind = chosen.get(id(row))
        if kind is None:
            row_kind = _results_kind(row.get("title")) or "unknown"
            reason = (
                f"not_selected: the {row_kind} results announcement for this stock "
                f"({chosen_date.get((code, row_kind))}) is the parsed one"
            )
            row["headline_status"] = "unavailable"
            row["headline_reason"] = reason
            row.update(segment_kpi.all_unavailable(code, reason))
            continue
        if not row.get("doc_url"):
            row["headline_status"] = "unavailable"
            row["headline_reason"] = "no_doc_url"
            row.update(segment_kpi.all_unavailable(code, "no_doc_url"))
            continue

        try:
            try:
                from fd_industry_data.cancel_event import is_set as cancel_set
                cancelled = cancel_set()
            except ImportError:
                cancelled = False
            if cancelled:
                row["headline_status"] = "unavailable"
                row["headline_reason"] = "cancelled"
                row.update(segment_kpi.all_unavailable(code, "cancelled"))
                continue
            await asyncio.sleep(0.4)
            resp = await _fetch(session, row["doc_url"])
            payload = resp.body
            if isinstance(payload, str):
                payload = payload.encode("utf-8", "replace")
            if len(payload) > MAX_PDF_BYTES:
                row["headline_status"] = "unavailable"
                row["headline_reason"] = f"pdf_too_large: {len(payload)} bytes"
                row.update(segment_kpi.all_unavailable(
                    code, "pdf_too_large", f"{len(payload)} bytes"))
                continue
            if not pdftext.is_pdf(payload):
                row["headline_status"] = "unavailable"
                row["headline_reason"] = "not_a_pdf"
                row.update(segment_kpi.all_unavailable(code, "not_a_pdf"))
                continue
            text = pdftext.extract_text(payload, layout=True)
            title = row.get("title") or ""
            row.update(extract_headline(text, title))
            row.update(segment_kpi.extract_kpis(text, title, code))
            logger.info(
                "hkex-disclosure: headline %s %s -> %s (%s %s); kpi -> %s (%s)",
                code, row["release_date"], row["headline_status"],
                row.get("headline_revenue"), row.get("headline_net_income"),
                row.get("kpi_status"), row.get("kpi_reason"),
            )
        except (SpiderError, pdftext.PdfTextError) as exc:
            row["headline_status"] = "unavailable"
            row["headline_reason"] = f"pdf_unavailable: {str(exc)[:200]}"
            row.update(segment_kpi.all_unavailable(
                code, "pdf_unavailable", str(exc)[:200]))
        except Exception as exc:  # noqa: BLE001 — 头条/细分是增值面，失败落不可得不判红
            logger.warning("hkex-disclosure: headline/kpi enrichment failed for %s %s: %s",
                           code, row.get("release_date"), exc)
            row["headline_status"] = "unavailable"
            row["headline_reason"] = f"internal_error: {type(exc).__name__}: {str(exc)[:160]}"
            row.update(segment_kpi.all_unavailable(
                code, "internal_error", f"{type(exc).__name__}: {str(exc)[:160]}"))


def _build_rows(
    en_rows: list[dict],
    zh_rows: list[dict],
    stock_code: str,
    stock_name: str | None,
    stock_id: int,
    scraped_at: str,
) -> list[dict]:
    rows: list[dict] = []
    for en, zh_title in _merge_titles(en_rows, zh_rows):
        release_date = _parse_release_date(en.get("DATE_TIME"))
        if release_date is None:
            raise SpiderError(
                f"hkex-disclosure: unparseable DATE_TIME {en.get('DATE_TIME')!r} "
                f"for {stock_code} NEWS_ID={en.get('NEWS_ID')}"
            )
        title = _clean_title(en.get("TITLE"))
        title_zh = _clean_title(zh_title)
        ann_type, hits = classify_ann_type(title, title_zh)
        doc_url = _doc_url(en.get("FILE_LINK"))
        if doc_url is None:
            logger.warning(
                "hkex-disclosure: no FILE_LINK for %s NEWS_ID=%s — doc_url=None",
                stock_code, en.get("NEWS_ID"),
            )
        rows.append(
            {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "stock_id": stock_id,
                "release_date": release_date,
                "title": title,
                "title_zh": title_zh,
                "ann_type": ann_type,
                "ann_type_hits": hits,
                "is_profit_warning": ann_type == "profit-warning",
                "doc_url": doc_url,
                "source": SOURCE,
                "scraped_at": scraped_at,
                **_headline_defaults(),
                **_kpi_defaults(stock_code),
            }
        )
    return rows


async def _collect(
    limit: int,
    stocks: list[tuple[str, str | None]],
    days_back: int,
    max_pages: int,
) -> list[dict]:
    from_date, to_date = _window(days_back)
    scraped_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []

    async with FetcherSession(
        impersonate="chrome120",
        timeout=30,
        verify=False,  # repo convention: containers ship no CA bundle
        retries=1,     # exactly one attempt — no retry storms
    ) as session:
        for code, provided_name in stocks:
            stock_id, upstream_name = await _resolve_stock_id(session, code)
            await asyncio.sleep(0.4)
            en_rows, en_pages = await _search_stock(
                session, stock_id, from_date, to_date, "EN", max_pages
            )
            await asyncio.sleep(0.4)
            zh_rows, zh_pages = await _search_stock(
                session, stock_id, from_date, to_date, "ZH", max_pages
            )
            if not en_rows:
                raise SpiderError(
                    f"hkex-disclosure: empty announcement result for {code} "
                    f"{upstream_name or provided_name or ''} in {from_date}..{to_date} "
                    f"— treating an empty feed as a failure (no partial data)"
                )
            if not zh_rows:
                raise SpiderError(
                    f"hkex-disclosure: empty ZH announcement result for {code} "
                    f"({len(en_rows)} EN rows) — title_zh would be silently blank"
                )
            if zh_pages != en_pages:
                logger.info(
                    "hkex-disclosure: %s paged EN=%d ZH=%d", code, en_pages, zh_pages
                )
            built = _build_rows(
                en_rows,
                zh_rows,
                code,
                upstream_name or provided_name,
                stock_id,
                scraped_at,
            )
            rows.extend(built)
            logger.info(
                "hkex-disclosure: %s %s -> %d announcement(s) (%s..%s)",
                code, upstream_name or provided_name or "", len(built),
                from_date, to_date,
            )
            await asyncio.sleep(0.4)

        # 只对最终返回的行做头条抽取（先截断再下载 PDF，避免为丢弃的行付流量）
        rows.sort(key=lambda r: r["release_date"], reverse=True)
        rows = rows[: max(int(limit), 0)]
        await _enrich_headlines(session, rows)

    return rows


def run_hkex_disclosure(
    limit: int = 500,
    stocks=None,
    days_back: int = 90,
    max_pages: int = 5,
) -> list[dict]:
    """Entry point for fd-runner (spiders/hkex-disclosure).

    Args:
        limit: maximum rows returned, newest ``release_date`` first (default 500).
        stocks: monitored universe — iterable of ``"06862"`` or
            ``("06862", "海底捞")`` entries; codes are zero-padded to 5 digits.
            Defaults to the full HK registry (``universes/hk.yaml``); passing an
            empty collection is a hard failure, never a silent fall-back.
        days_back: look-back window in days, inclusive of today (HK time).
        max_pages: max paging rounds per stock/language (each round raises
            ``rowRange`` by 100). ``hasNextRow`` still true at the cap is a
            hard failure rather than a truncated payload.

    Returns:
        list of announcement row dicts (schema = manifest columns). Raises
        ``SpiderError`` on empty result, unparseable payload, stockId miss,
        short read, or non-converging pagination — never a partial payload.
    """
    try:
        from fd_industry_data.cancel_event import is_set as cancel_set
    except ImportError:  # standalone run outside the repo package
        def cancel_set() -> bool:
            return False

    universe = _normalize_stocks(stocks)

    async def _run() -> list[dict]:
        if cancel_set():
            logger.info("cancel requested before start; returning empty")
            return []
        return await _collect(limit, universe, days_back, max_pages)

    return asyncio.run(_run())


if __name__ == "__main__":
    for r in run_hkex_disclosure(limit=5):
        print(r)
