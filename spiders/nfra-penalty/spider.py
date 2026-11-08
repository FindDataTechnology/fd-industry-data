"""NFRA 行政处罚信息 —— 三叶子栏目（总局/局/分局）公示表。

为什么只解析 ``docClob``（HTML 表）而不解析同源 PDF：2026-11-07 抽样三个叶子
栏目共 36 篇文档，``docClob`` **36/36 含且仅含 1 个 ``<table>``**、附件数为 0，
``pdfFileUrl`` 是同一张表的 WPS 渲染件。PDF 的合并单元格（缩进/垂直居中）使
``pdftotext`` 无法还原列——``-bbox-layout`` 实测把 5 列并成 4 列且串行错位，
个别 PDF 直接 SIGABRT。故 PDF 不做结构化解析（若日后正文退化为纯图片，属
2026-10-13「需 OCR 才接」裁决范围，按该裁决重开）。

坑位（侦察簿路 2）：双头 ``Referer`` + ``X-Requested-With``；``pageSize`` 恒 20；
限流返回 HTTP 200 但 ``rptCode != 200``，须退避 ≥90s；服务端搜索参数是空操作，
过滤只能在本地做；931 聚合栏目已坏，只取 4113/4114/4115。
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from fd_industry_data import tabular

BASE = "https://www.nfra.gov.cn"
LISTING_URL = BASE + "/cbircweb/DocInfo/SelectDocByItemIdAndChild"
DETAIL_URL = BASE + "/cbircweb/DocInfo/SelectByDocId"

#: 三个可用的叶子栏目（931 聚合栏目已坏，服务端搜索是空操作）。
LEAVES = {
    4113: "general_bureau",   # 总局
    4114: "bureau",           # 省级/计划单列市监管局
    4115: "branch",           # 监管分局
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    # 双头缺一即 403/空体（侦察簿路 2 实测）
    "Referer": BASE + "/",
    "X-Requested-With": "XMLHttpRequest",
}

PAGE_SIZE = 20          # 服务端无视传入值，恒 20/页
MAX_PAGES_PER_LEAF = 3   # 首爬每叶子 ≤3 页（60 篇 ≈ 最近 1-2 个月全量）
SLEEP_PAGE = 1.2
SLEEP_DOC = 0.6
BACKOFF_SECONDS = 90.0
RETRY_ON_RATE_LIMIT = 2

HEADER_ALIASES = {
    "party": ("当事人名称", "当事人", "被处罚当事人"),
    "penalty_no": ("行政处罚决定书文号", "处罚决定书文号", "决定书文号", "文号"),
    "violation": ("主要违法违规行为", "违法违规行为", "主要违法违规事实", "违法事实"),
    "penalty_content": ("行政处罚内容", "处罚内容", "行政处罚决定"),
    "office": ("作出决定机关", "决定机关", "处罚机关"),
}

#: 处罚决定书文号常只出现在标题括注里（如「…公开表（冀金罚决字〔2026〕68号）」）。
_TITLE_DOC_NO = re.compile(r"[（(]([^（）()]{4,40}?号)[）)]")
#: 「X万元」前后须有「罚」才算罚款（排除没收违法所得/涉案金额）。
_AMOUNT_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*万元")
_PRE_WINDOW = 12
_POST_WINDOW = 3


def _get(url: str, *, timeout: int = 40) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    body = urllib.request.urlopen(req, timeout=timeout).read()
    return body.decode("utf-8", "replace")


def _api(url: str, *, timeout: int = 40) -> dict:
    """JSON 端点；``strict=False`` 处理 docClob 内嵌 Word-HTML 的控制字符。"""
    return json.loads(_get(url, timeout=timeout), strict=False)


def _api_guarded(url: str, *, what: str) -> dict | None:
    """``rptCode != 200`` 与 HTTP 403/429 都视为限流：退避重试一次（HTTP 200 也可能被限流）。"""
    for attempt in range(RETRY_ON_RATE_LIMIT + 1):
        try:
            payload = _api(url)
        except urllib.error.HTTPError as exc:
            if exc.code not in (403, 429) or attempt >= RETRY_ON_RATE_LIMIT:
                raise
            print(
                f"[nfra-penalty] HTTP {exc.code} on {what} — backing off "
                f"{BACKOFF_SECONDS:.0f}s before retry",
                flush=True,
            )
            time.sleep(BACKOFF_SECONDS)
            continue
        code = payload.get("rptCode")
        if code == 200:
            return payload
        if attempt < RETRY_ON_RATE_LIMIT:
            print(
                f"[nfra-penalty] rptCode={code!r} on {what} — backing off "
                f"{BACKOFF_SECONDS:.0f}s before retry",
                flush=True,
            )
            time.sleep(BACKOFF_SECONDS)
            continue
        print(
            f"[nfra-penalty] rptCode={code!r} on {what} — giving up on this leaf/page",
            flush=True,
        )
    return None


def _clean(cell: object) -> str:
    if cell is None:
        return ""
    return re.sub(r"\s+", " ", str(cell)).strip()


def _amount_of(text: str) -> float | None:
    """一行处罚内容里的罚款合计（万元）；无罚款（仅警告/禁业）返回 None。"""
    total = 0.0
    hit = False
    for m in _AMOUNT_RE.finditer(text):
        before = text[max(0, m.start() - _PRE_WINDOW):m.start()]
        after = text[m.end():m.end() + _POST_WINDOW]
        if "罚" not in before and "罚" not in after:
            continue
        try:
            total += float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        hit = True
    return round(total, 4) if hit else None


def _authority_level(office: str) -> str:
    if "总局" in office:
        return "总局"
    if "分局" in office:
        return "分局"
    if "监管局" in office or "局" in office:
        return "局"
    return ""


def _doc_no_from_title(title: str) -> str:
    m = _TITLE_DOC_NO.search(title)
    return _clean(m.group(1)) if m else ""


def _locate_header(grid: list[list[str]]) -> tuple[int, dict[str, int]] | None:
    """定位表头行 → {字段: 列号}；要求同时命中当事人/处罚内容/机关。"""
    for idx, row in enumerate(grid[:6]):
        cells = [_clean(c) for c in row]
        mapping: dict[str, int] = {}
        for field, aliases in HEADER_ALIASES.items():
            for col, cell in enumerate(cells):
                if col in mapping.values() or not cell:
                    continue
                if any(cell == alias or alias in cell for alias in aliases):
                    mapping[field] = col
                    break
        if {"party", "penalty_content", "office"} <= mapping.keys():
            return idx, mapping
    return None


def _rows_from_grid(
    grid: list[list[str]], *, title: str, doc_id: str, published_at: str, url: str, leaf: str
) -> list[dict]:
    located = _locate_header(grid)
    if located is None:
        return []
    header_idx, mapping = located
    rows: list[dict] = []
    for raw in grid[header_idx + 1:]:
        cells = [_clean(c) for c in raw]
        if not any(cells):
            continue
        party = cells[mapping["party"]] if mapping["party"] < len(cells) else ""
        if not party or party in ("当事人名称", "当事人"):
            continue
        content = cells[mapping["penalty_content"]] if mapping["penalty_content"] < len(cells) else ""
        violation = (
            cells[mapping["violation"]] if "violation" in mapping and mapping["violation"] < len(cells) else ""
        )
        office = cells[mapping["office"]] if mapping["office"] < len(cells) else ""
        penalty_no = ""
        if "penalty_no" in mapping and mapping["penalty_no"] < len(cells):
            penalty_no = cells[mapping["penalty_no"]]
        if not penalty_no:
            penalty_no = _doc_no_from_title(title)
        rows.append(
            {
                "penalty_no": penalty_no,
                "party": party,
                "violation": violation,
                "penalty_content": content,
                "amount": _amount_of(content),
                "office": office,
                "authority_level": _authority_level(office),
                "leaf": leaf,
                "doc_date": published_at[:10],
                "doc_url": url,
                "doc_id": doc_id,
                "source": "nfra-penalty",
                "scraped_at": _now(),
            }
        )
    return rows


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def discover(leaf: int, *, max_pages: int = MAX_PAGES_PER_LEAF) -> list[dict]:
    """叶子栏目列表页（newest-first，服务端恒 20/页）。"""
    docs: list[dict] = []
    for page in range(1, max_pages + 1):
        url = f"{LISTING_URL}?itemId={leaf}&pageIndex={page}&pageSize={PAGE_SIZE}"
        payload = _api_guarded(url, what=f"listing {leaf} page {page}")
        if payload is None:
            break
        data = payload.get("data") or {}
        batch = data.get("rows") or []
        for doc in batch:
            doc["_leaf"] = leaf
        docs.extend(batch)
        total = data.get("total") or 0
        if len(batch) < PAGE_SIZE or len(docs) >= total:
            break
        time.sleep(SLEEP_PAGE)
    return docs


def _doc_rows(doc: dict) -> list[dict]:
    doc_id = str(doc.get("docId") or "")
    if not doc_id:
        return []
    payload = _api_guarded(f"{DETAIL_URL}?docId={doc_id}", what=f"detail {doc_id}")
    if payload is None:
        return []
    detail = payload.get("data") or {}
    clob = detail.get("docClob") or ""
    if "<table" not in clob.lower():
        # 无表 → 该篇不结构化（PDF 同源但不还原列，见模块 docstring），留痕后跳过
        print(f"[nfra-penalty] doc {doc_id} carries no HTML table — skipped", flush=True)
        return []
    title = _clean(detail.get("docTitle") or doc.get("docTitle"))
    published_at = _clean(detail.get("publishDate") or doc.get("publishDate"))
    pdf_url = _clean(doc.get("pdfFileUrl") or detail.get("pdfFileUrl") or "")
    url = (BASE + pdf_url) if pdf_url.startswith("/") else pdf_url
    leaf = LEAVES.get(int(doc.get("_leaf") or 0), "")
    try:
        grid = tabular.read_grid(clob.encode("utf-8"))
    except tabular.TabularError as exc:
        print(f"[nfra-penalty] doc {doc_id} table unreadable: {exc}", flush=True)
        return []
    return _rows_from_grid(
        grid,
        title=title,
        doc_id=doc_id,
        published_at=published_at,
        url=url,
        leaf=leaf,
    )


def run_nfra_penalty(limit: int = 300) -> list[dict]:
    """三叶子栏目行政处罚公示表 → 逐当事人一行的处罚记录。

    行粒度为「表内一行 = 一个（组）当事人」，金额为该行处罚内容里**罚款合计**
    （万元，不含没收违法所得）；只有警告/禁业的行 ``amount`` 为 ``None``（绝不用 0 填充）。
    """
    if limit <= 0:
        return []
    wanted = int(limit)
    rows: list[dict] = []
    per_leaf: dict[int, int] = {}
    # 先取三叶子列表再轮转取详情：总局叶子单篇可达 50+ 行，顺序取会被它吃满预算，
    # 分局叶子（唯一带「行政处罚决定书文号」的形态）永远轮不到。
    queues: list[list[dict]] = []
    for leaf in LEAVES:
        try:
            docs = discover(leaf)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            print(f"[nfra-penalty] listing leaf {leaf} failed: {exc}", flush=True)
            docs = []
        per_leaf[leaf] = len(docs)
        queues.append(docs)
    while queues and len(rows) < wanted:
        for queue in queues:
            if not queue:
                continue
            doc = queue.pop(0)
            try:
                rows.extend(_doc_rows(doc))
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                print(f"[nfra-penalty] doc {doc.get('docId')} failed: {exc}", flush=True)
            time.sleep(SLEEP_DOC)
            if len(rows) >= wanted:
                break
        queues = [q for q in queues if q]
    if not rows:
        raise RuntimeError("nfra-penalty: every leaf returned zero penalty rows")
    print(
        f"[nfra-penalty] docs scanned per leaf {per_leaf} → {len(rows)} rows",
        flush=True,
    )
    return rows[:wanted]
