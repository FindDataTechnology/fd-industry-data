"""spiders/nfra-stats-xls —— 国家金融监督管理总局 银行业监管统计表（Excel 附件通道）。

**为什么走附件而不是正文**（change `add-banking-industry-sources` 设计决策 D1）：
``SelectByDocId`` 的 ``docClob`` 是 Word-HTML 导出，正文里只有 1 个 ``<img>``、
0 个 ``<table>``（2026-11-07 抽样 10/10 全为此形态）；对应 PDF 也无文本层。
唯一机器可读形态是 ``attachmentInfoVOList`` 里的 **BIFF8 .xls 附件**（无鉴权，
裸请求 206/200）。2026-10-13「扫描件需 OCR 暂不接」裁决的对象是「需 OCR 才能
结构化」的源，附件通道**不需要 OCR**，故不触及裁决原文；若附件通道日后断供，
OCR 三件套仍是唯一解锁路径（裁决保留效力）。

数据表面（实测 2026-11-07）：

- 列表 ``POST/GET /cbircweb/DocInfo/SelectDocByItemIdAndChild?itemId=954&pageIndex=N&pageSize=20``
  —— pageSize 传参被无视、恒 20/页；954 栏目 1,386 条。
- 详情 ``/cbircweb/DocInfo/SelectByDocId?docId=<docId>`` —— 必带双头
  ``Referer: https://www.nfra.gov.cn/`` + ``X-Requested-With: XMLHttpRequest``；
  响应含 ``docClob``（嵌 Word-HTML，含未转义控制字符 → ``json.loads(strict=False)``）
  与 ``attachmentInfoVOList[].urlOtherName``。
- 附件直链 ``https://www.nfra.gov.cn/chinese/docfile/<年>/<uuid>.xls``。

**同一 docId 原位更新**：月度《总资产、总负债（月度）》年度一个 docId（如 1265692），
``publishDate`` 随最新月度刷新、附件 uuid 月度轮换。因此**每次运行都重查详情**
取最新直链，绝不缓存 uuid（spec `原位更新源不缓存附件` 场景）。

表形态两种（解析器按表头自动分派）：

1. **周期列矩阵**（主要监管指标 / 普惠小微 / 普惠涉农 / 总资产负债）：
   ``项目`` 列表头 + ``一季度…四季度`` 或 ``1月…12月`` 周期列，年份取自 ``时间`` 行。
2. **机构交叉表**（分机构类情况表）：``机构`` 行表头 + 各机构列；行首为季度
   （纵向合并、向下继承）+ 指标名。输出 ``org_category`` = 机构名。

比率口径：源表 ``%`` 单元格全部是 **Excel 百分数格式的小数形**（实测：不良贷款率
0.01512=1.512%、拨备覆盖率 2.03137=203.137%、资本充足率 0.15=15%）。故凡指标名含
率/占比/比/比例且单位行含 ``%`` 者，值 ×100 并按 ``%`` 落库，其余按 ``亿元``。
这是「按发布口径归一」，不是数值猜测——README 记录判定依据。

容错：单文档/单表失败跳过留痕；全失败 ``RuntimeError``；空格不出行（绝不用 0 填充）；
``limit`` 真实生效。表格解析一律走 ``fd_industry_data.tabular``（禁止内联手写）。

只依赖标准库 + 共享表格层。
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone

from fd_industry_data.tabular import read_grid

SOURCE = "nfra-stats-xls"
BASE = "https://www.nfra.gov.cn"
LIST_URL = (BASE + "/cbircweb/DocInfo/SelectDocByItemIdAndChild"
            "?itemId=954&pageIndex={page}&pageSize=20")
DETAIL_URL = BASE + "/cbircweb/DocInfo/SelectByDocId?docId={doc_id}"

TIMEOUT_API = 40
TIMEOUT_FILE = 60
MAX_LISTING_PAGES = 8  # 20 条/页 → 160 文档，实测覆盖 2026 全年 + 2025

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": BASE + "/",
    "X-Requested-With": "XMLHttpRequest",
}
FILE_HEADERS = {k: v for k, v in HEADERS.items() if k != "X-Requested-With"}

# 目标表（四组）。标题口径有漂移（2024/25 带「（季度）」后缀、2026 无），
# 故按**关键词组**匹配而非整名相等。
TARGET_TABLES = (
    ("商业银行主要监管指标情况表", "regulatory_metrics"),
    ("商业银行主要指标分机构类情况表", "by_institution"),
    ("总资产、总负债", "balance_sheet"),
    ("普惠型小微企业贷款情况表", "inclusive_small"),
    ("普惠型涉农贷款情况表", "inclusive_agri"),
)

_CN_Q = {"一": 1, "二": 2, "三": 3, "四": 4}
_RATIO_MARKS = ("率", "占比", "比例", "比")
_NUM_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?$")


def _get(url: str, timeout: int, headers: dict) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for {url}")
        return resp.read()


def _api(url: str) -> dict:
    raw = _get(url, TIMEOUT_API, HEADERS).decode("utf-8", "replace")
    return json.loads(raw, strict=False)  # docClob 含未转义控制字符


# ------------------------------------------------------------------ 发现


def _table_kind(title: str) -> str | None:
    for mark, kind in TARGET_TABLES:
        if mark in title:
            return kind
    return None


def discover(max_pages: int = MAX_LISTING_PAGES) -> tuple[list[dict], list[str]]:
    """954 栏目回溯 → 目标表文档（新→旧）+ 留痕。"""
    traces: list[str] = []
    docs: list[dict] = []
    for page in range(1, max_pages + 1):
        try:
            data = _api(LIST_URL.format(page=page))
        except Exception as exc:  # noqa: BLE001
            traces.append(f"list p{page}: {type(exc).__name__}: {exc}"[:160])
            continue
        if data.get("rptCode") != 200:
            traces.append(f"list p{page}: rptCode={data.get('rptCode')}")
            continue
        rows = (data.get("data") or {}).get("rows") or []
        if not rows:
            break
        for row in rows:
            title = (row.get("docTitle") or "").strip()
            kind = _table_kind(title)
            if not kind:
                continue
            docs.append({
                "doc_id": str(row.get("docId")),
                "title": title,
                "kind": kind,
                "published_at": str(row.get("publishDate") or "")[:10],
            })
        if len(rows) < 20:
            break
    if not docs:
        traces.append("no banking table documents found in 954 listing")
    return docs, traces


def _attachments(doc_id: str) -> list[dict]:
    """详情 → 附件直链（每次重查，不缓存 uuid）。"""
    data = _api(DETAIL_URL.format(doc_id=doc_id))
    if data.get("rptCode") != 200:
        raise RuntimeError(f"rptCode={data.get('rptCode')} for docId={doc_id}")
    out = []
    for att in (data.get("data") or {}).get("attachmentInfoVOList") or []:
        path = att.get("urlOtherName") or ""
        if not path.lower().endswith(".xls") and not path.lower().endswith(".xlsx"):
            continue
        out.append({
            "name": att.get("title") or att.get("attachmentName") or "",
            "url": BASE + path if path.startswith("/") else path,
        })
    return out


# ------------------------------------------------------------------ 解析


def _clean(cell: object) -> str:
    return str(cell).replace("\xa0", " ").strip() if cell is not None else ""


def _num(cell: object) -> float | None:
    text = _clean(cell).replace(",", "").replace("，", "")
    if not text or not _NUM_RE.match(text):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _units_of(grid: list[list[str]]) -> set[str]:
    """表头「单位：亿元、%」→ {'亿元','%'}。"""
    for row in grid[:6]:
        for cell in row:
            text = _clean(cell)
            if "单位" in text:
                found = set()
                if "万亿元" in text:
                    found.add("万亿元")
                elif "亿元" in text:
                    found.add("亿元")
                if "%" in text or "％" in text:
                    found.add("%")
                return found
    return set()


def _year_of(grid: list[list[str]], title: str) -> str | None:
    for row in grid[:8]:
        for cell in row:
            m = re.search(r"(\d{4})年", _clean(cell))
            if m and _clean(cell).startswith(m.group(1)):
                return m.group(1)
    m = re.search(r"(\d{4})年", title)
    return m.group(1) if m else None


def _period_of_label(label: str, year: str) -> str | None:
    """周期列标签 → YYYYQn / YYYY-MM。"""
    m = re.search(r"([一二三四])季度", label)
    if m:
        return f"{year}Q{_CN_Q[m.group(1)]}"
    m = re.search(r"(\d{1,2})月", label)
    if m:
        return f"{year}-{int(m.group(1)):02d}"
    m = re.search(r"(上|下)半年", label)
    if m:
        return f"{year}H{'1' if m.group(1) == '上' else '2'}"
    if "全年" in label or "年度" in label:
        return year
    return None


def _is_ratio(indicator: str, units: set[str]) -> bool:
    return "%" in units and any(mark in indicator for mark in _RATIO_MARKS)


def _row(indicator: str, raw: float, units: set[str]) -> tuple[float, str]:
    """比率小数形 → 百分数；其余按亿元。"""
    if _is_ratio(indicator, units):
        return round(raw * 100, 6), "%"
    return raw, ("万亿元" if "万亿元" in units else "亿元")


def _parse_period_matrix(grid: list[list[str]], title: str) -> list[dict]:
    """周期列矩阵：周期标签所在行为表头（下接项目行或直接是数据行）。

    两种表头布局实测并存：
    - 2026 期：``时间|2026年`` 然后 ``项目|一季度|二季度…``（周期在项目行）
    - 2025 期：``时间|一季度|二季度…`` 然后 ``项目``（周期在时间行，项目行空）
    故按「第一个出现周期标签的行」定表头，兼容两者；项目行若无数值自然被跳过。
    """
    units = _units_of(grid)
    year = _year_of(grid, title)
    if year is None:
        raise ValueError("year not found in header or title")

    header_idx = None
    periods: dict[int, str] = {}
    for i, row in enumerate(grid[:14]):
        found = {}
        for j, cell in enumerate(row):
            if j == 0:
                continue
            label = _period_of_label(_clean(cell), year)
            if label:
                found[j] = label
        if found:
            header_idx, periods = i, found
            break
    if header_idx is None:
        raise ValueError("period-matrix header not found")

    out: list[dict] = []
    section = ""
    for row in grid[header_idx + 1:]:
        cells = [_clean(c) for c in row]
        if not cells or not cells[0]:
            continue
        label = cells[0]
        if label.startswith("（") or label.startswith("("):
            # 「（一）信用风险指标」→ 「信用风险指标」（去掉序号括号组）
            section = re.sub(r"^[（(][^）)]*[）)]", "", label).strip() or label
            continue
        if label.startswith("注"):
            break
        values = [(periods[j], _num(row[j])) for j in periods if j < len(row)]
        values = [(p, v) for p, v in values if p and v is not None]
        if not values:
            continue
        indicator = re.sub(r"\s+", "", label)
        for period, raw in values:
            value, unit = _row(indicator, raw, units)
            out.append({
                "table_category": section,
                "org_category": "合计",
                "indicator": indicator,
                "period": period,
                "value": value,
                "unit": unit,
            })
    return out


def _parse_institution_matrix(grid: list[list[str]], title: str) -> list[dict]:
    """机构交叉表：行首季度（纵向合并）+ 指标；列=各机构。"""
    units = _units_of(grid)
    year = _year_of(grid, title)
    header_idx = None
    for i, row in enumerate(grid[:12]):
        if _clean(row[0]) == "机构":
            header_idx = i
            break
    if header_idx is None or year is None:
        raise ValueError("institution-matrix header not found")

    institutions = {}
    for j, cell in enumerate(grid[header_idx]):
        name = _clean(cell).replace("\n", "")
        if j >= 2 and name:
            institutions[j] = name
    if not institutions:
        raise ValueError("no institution columns found")

    out: list[dict] = []
    period: str | None = None
    for row in grid[header_idx + 2:]:
        cells = [_clean(c) for c in row]
        if not cells:
            continue
        if cells[0]:
            p = _period_of_label(cells[0], year)
            if p:
                period = p
        indicator = re.sub(r"\s+", "", cells[1]) if len(cells) > 1 else ""
        if not indicator or period is None or indicator.startswith("注"):
            continue
        matched = False
        for j, org in institutions.items():
            if j >= len(row):
                continue
            raw = _num(row[j])
            if raw is None:
                continue
            matched = True
            value, unit = _row(indicator, raw, units)
            out.append({
                "table_category": "分机构类",
                "org_category": org,
                "indicator": indicator,
                "period": period,
                "value": value,
                "unit": unit,
            })
        if not matched and indicator.startswith("（"):
            continue
    return out


def parse_attachment(payload: bytes, title: str) -> list[dict]:
    """单个 xls 附件 → 行（自动分派两种表形态）。"""
    grid = read_grid(payload)
    if not grid or not grid[0]:
        raise ValueError("empty grid")
    # 分派只看表头首列是否**恰为**「机构」——不能用表名含「机构」判断
    # （月度/普惠表标题是「银行业金融机构…」，会误命中）。
    if any(_clean(r[0]) == "机构" for r in grid[:12] if r):
        return _parse_institution_matrix(grid, title)
    return _parse_period_matrix(grid, title)


# ------------------------------------------------------------------ 编排


def _interleave_by_kind(docs: list[dict]) -> list[dict]:
    """按表族轮转重排（族内保持新→旧）。

    理由：月度资产负债表单表就 300+ 行，若按发布时间顺序直取，小 limit 会被
    这一个族吃满，四组表其余三组饿死（首爬实测：limit=60 全为 balance_sheet）。
    轮转让每个族在新旧顺序下轮流拿到预算。
    """
    buckets: dict[str, list[dict]] = {}
    for doc in docs:
        buckets.setdefault(doc["kind"], []).append(doc)
    out: list[dict] = []
    depth = max((len(v) for v in buckets.values()), default=0)
    for k in range(depth):
        for kind in buckets:  # 族顺序=首次出现顺序（稳定）
            if k < len(buckets[kind]):
                out.append(buckets[kind][k])
    return out


def _collect(limit: int) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    traces: list[str] = []
    rows: list[dict] = []
    docs, traces = discover()
    docs = _interleave_by_kind(docs)

    for doc in docs:
        if len(rows) >= limit:
            traces.append(f"limit reached ({limit}) at {doc['title']}")
            break
        try:
            atts = _attachments(doc["doc_id"])
            if not atts:
                raise ValueError("no xls attachment")
            parsed_all: list[dict] = []
            for att in atts:
                payload = _get(att["url"], TIMEOUT_FILE, FILE_HEADERS)
                parsed_all.extend(parse_attachment(payload, doc["title"]))
            if not parsed_all:
                raise ValueError("attachment parsed to 0 rows")
        except Exception as exc:  # noqa: BLE001 - 单文档失败跳过留痕
            traces.append(f"{doc['title'][:28]}: {type(exc).__name__}: {exc}"[:180])
            continue
        for row in parsed_all:
            rows.append({
                **row,
                "table_kind": doc["kind"],
                "report_title": doc["title"],
                "published_at": doc["published_at"],
                "doc_id": doc["doc_id"],
                "source": SOURCE,
                "scraped_at": now,
            })
            if len(rows) >= limit:
                break

    if traces:
        print(f"[{SOURCE}] traces({len(traces)}): " + " | ".join(traces[:8]))  # noqa: T201
    if not rows:
        raise RuntimeError(f"{SOURCE}: 全部统计表取数失败 — " + "; ".join(traces)[:300])
    return rows[:limit]


def run_nfra_stats_xls(limit: int = 300) -> list[dict]:
    """取 NFRA 银行业监管统计表指标行（新→旧，四组表）。

    Args:
        limit: 返回最大行数（真实生效；逐文档取，取满即止）。

    Returns:
        行字典列表，键见 manifest ``functions[].columns``。

    Raises:
        ValueError: ``limit`` 非法。
        RuntimeError: 全部统计表均未解析出数据。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []
    return _collect(limit)


if __name__ == "__main__":  # 冒烟
    print(json.dumps(run_nfra_stats_xls(limit=30), ensure_ascii=False, indent=2))
