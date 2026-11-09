"""PBoC 金融机构信贷收支统计 —— 九张月表（本外币/外汇/人民币 × 机构口径）。

数据表面：调查统计司「统计数据」→「<年>年统计数据」→「金融机构信贷收支统计」栏目页。
栏目页**只承载最新一个月**的九张表（每表一组 htm/xlsx/pdf 三链接），按月**原位替换**。

坑位（侦察簿路 3，逐条落实位置）：

1. **月份表头数字截零**：表头 `2026.01 … 2026.09` 之后是 `2026.1`——那个 `2026.1`
   **是 10 月**（小数尾零被截），不是 1 月。按小数位宽度还原：一位小数 → ×10，
   两位 → 原值。错解会把 10 月数据落在 1 月并污染整条序列。
2. `sharedStrings` 中文带 `\\xa0`（另有全角空格 `\\u3000`）中英双行：`一、各项存款\\xa0Total Deposits`。
   指标名取「第一个含中文的段」，`\\xa0`/`\\u3000`/`·` 是分段符。
3. 首 5 行是标题/单位/英文副标题噪音，表头行按「含『项目』且含月份标签」定位，不写死行号。
4. 行尾 `注：…` 脚注段无值 → 天然被「无有效值」规则跳过。
5. 栏目在线深度止于 2016（更早有栏无表）；本单元默认只取**当前年度**栏目页
   （其余年度栏目页各自只留该年末快照，逐月序列靠本单元按月累积）。

为什么不解析 htm/pdf：同表三形态（htm 正文表、xlsx、pdf），xlsx 是唯一有
「全部月份列 + 机器可读单元格」的形态，且走共享层 `tabular`（禁内联解析）。
"""
from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from fd_industry_data import tabular

BASE = "https://www.pbc.gov.cn"
STATS_INDEX = BASE + "/diaochatongjisi/116219/116319/index.html"
COLUMN_NAME = "金融机构信贷收支统计"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}
SLEEP = 0.5
UNIT = "亿元"

#: 九张表的标题 → (table_key, currency, institution_scope)。
TABLE_MAP = (
    ("金融机构本外币信贷收支表", "financial_institutions_fx", "本外币", "全部金融机构"),
    ("存款类金融机构本外币信贷收支表", "depository_institutions_fx", "本外币", "存款类金融机构"),
    ("金融机构外汇信贷收支表", "financial_institutions_fx_only", "外汇", "全部金融机构"),
    ("存款类金融机构外汇信贷收支表", "depository_institutions_fx_only", "外汇", "存款类金融机构"),
    ("金融机构人民币信贷收支表", "financial_institutions_rmb", "人民币", "全部金融机构"),
    ("存款类金融机构人民币信贷收支表", "depository_institutions_rmb", "人民币", "存款类金融机构"),
    ("中资全国性大型银行人民币信贷收支表", "large_national_banks_rmb", "人民币", "中资全国性大型银行"),
    ("中资全国性四家大型银行人民币信贷收支表", "four_large_state_banks_rmb", "人民币", "中资全国性四家大型银行"),
    ("中资全国性中小型银行人民币信贷收支表", "small_medium_national_banks_rmb", "人民币", "中资全国性中小型银行"),
)

_A_TAG = re.compile(r"<a\b[^>]*href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>", re.S)
_YEAR_LABEL = re.compile(r"(20\d\d)年统计数据")
_MONTH_CELL = re.compile(r"^(20\d\d)\.(\d{1,2})$")
_CREATE_DATE = re.compile(r'name=["\']createDate["\']\s+content=["\']([^"\']+)["\']')
_SEGMENT_SPLIT = re.compile(r"[\u00a0\u3000·]+")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_SIDE_SOURCE = "来源方项目"
_SIDE_USE = "运用方项目"
_LEVEL_PATTERNS = (
    (re.compile(r"^[一二三四五六七八九十]+、"), 1),
    (re.compile(r"^[（(][一二三四五六七八九十]+[）)]"), 2),
    (re.compile(r"^\d+[.、]"), 3),
    (re.compile(r"^[（(]\d+[）)]"), 4),
)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _fetch(url: str, *, timeout: int = 45) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    return urllib.request.urlopen(req, timeout=timeout).read()


def _text(url: str) -> str:
    return _fetch(url).decode("utf-8", "replace")


def _parse_month(label: str) -> tuple[int, int] | None:
    """表头月份标签 → (年, 月)；处理「2026.1 = 10 月」的尾零截断（坑位 1）。"""
    m = _MONTH_CELL.match(label.replace(" ", ""))
    if not m:
        return None
    year = int(m.group(1))
    frac = m.group(2)
    month = int(frac) * 10 if len(frac) == 1 else int(frac)
    if not 1 <= month <= 12:
        month = int(frac)
    return (year, month) if 1 <= month <= 12 else None


def _indicator_name(cell: str) -> str:
    """``一、各项存款\\xa0Total Deposits`` → ``一、各项存款``（坑位 2）。"""
    for segment in _SEGMENT_SPLIT.split(cell or ""):
        segment = segment.strip()
        if segment and _CJK.search(segment):
            return segment
    return ""


def _level_of(name: str) -> int:
    for pattern, level in _LEVEL_PATTERNS:
        if pattern.match(name):
            return level
    return 0


def _to_float(cell: str) -> float | None:
    text = (cell or "").replace(",", "").replace(" ", "").strip()
    if not text or not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def column_url() -> tuple[str, int]:
    """从「统计数据」索引定位**最新年度**的信贷收支栏目页（年度滚动不写死 URL）。"""
    html = _text(STATS_INDEX)
    current_year = 0
    found: dict[int, str] = {}
    for href, label in _A_TAG.findall(html):
        text = re.sub(r"<[^>]+>", "", label).strip()
        year_hit = _YEAR_LABEL.search(text)
        if year_hit:
            current_year = int(year_hit.group(1))
            continue
        if text == COLUMN_NAME and current_year:
            found.setdefault(current_year, href)
    if not found:
        raise RuntimeError("pboc-credit-funds: 统计数据索引里找不到「金融机构信贷收支统计」栏目")
    year = max(found)
    href = found[year]
    return (BASE + href if href.startswith("/") else href), year


def discover(column: str) -> tuple[list[dict], str]:
    """栏目页 → (九张表, 栏目页 createDate 作为发布日)。"""
    html = _text(column)
    published = ""
    hit = _CREATE_DATE.search(html)
    if hit:
        published = hit.group(1).strip()[:10]
    tables: list[dict] = []
    for block in re.split(r"<table[^>]*class=['\"]a2015['\"]", html)[1:]:
        title_hit = re.search(r"<div class=['\"]titp20['\"]\s*>(.*?)</div>", block, re.S)
        if not title_hit:
            continue
        title = re.sub(r"<br\s*/?>", " ", title_hit.group(1))
        title = re.sub(r"<[^>]+>", "", title)
        title_cn = re.split(r"[\u4e00-\u9fff]", title, maxsplit=0)
        title_cn = (title.split("Sources")[0]).strip()
        xlsx = re.search(r"href=['\"]([^'\"]+\.xlsx)['\"]", block)
        if not xlsx or not title_cn:
            continue
        meta = next((m for m in TABLE_MAP if m[0] == title_cn), None)
        if meta is None:
            print(f"[pboc-credit-funds] unrecognised table title: {title_cn!r}", flush=True)
            continue
        tables.append(
            {
                "title": title_cn,
                "table_key": meta[1],
                "currency": meta[2],
                "institution_scope": meta[3],
                "url": BASE + xlsx.group(1) if xlsx.group(1).startswith("/") else xlsx.group(1),
            }
        )
    return tables, published


def _table_rows(table: dict, payload: bytes, published: str) -> list[dict]:
    grid = tabular.read_grid(payload)
    header_idx = None
    months: dict[int, str] = {}
    for idx, row in enumerate(grid[:14]):
        if not row:
            continue
        first = (row[0] or "").strip()
        parsed = {}
        for col, cell in enumerate(row[1:], start=1):
            period = _parse_month((cell or "").strip())
            if period:
                parsed[col] = f"{period[0]:04d}-{period[1]:02d}"
        if "项目" in first and parsed:
            header_idx, months = idx, parsed
            break
    if header_idx is None:
        raise ValueError(f"month header row not found in {table['table_key']}")
    rows: list[dict] = []
    side = ""
    for raw in grid[header_idx + 1:]:
        if not raw:
            continue
        label = raw[0] or ""
        if _SIDE_SOURCE in label:
            side = "source"
            continue
        if _SIDE_USE in label:
            side = "use"
            continue
        name = _indicator_name(label)
        if not name or name.startswith("注"):
            continue
        level = _level_of(name)
        for col, period in months.items():
            if col >= len(raw):
                continue
            value = _to_float(raw[col])
            if value is None:
                continue
            rows.append(
                {
                    "table_key": table["table_key"],
                    "table_title": table["title"],
                    "currency": table["currency"],
                    "institution_scope": table["institution_scope"],
                    "side": side,
                    "indicator": name,
                    "indicator_level": level,
                    "period": period,
                    "value": value,
                    "unit": UNIT,
                    "published_at": published,
                    "source": "pboc-credit-funds",
                    "scraped_at": _now(),
                }
            )
    return rows


def _interleave(groups: list[list[dict]]) -> list[dict]:
    """九张表轮流取一行：截断时仍覆盖全部表，而不是把预算全给第一张表。"""
    out: list[dict] = []
    cursors = [0] * len(groups)
    remaining = sum(len(g) for g in groups)
    while remaining:
        for i, group in enumerate(groups):
            if cursors[i] < len(group):
                out.append(group[cursors[i]])
                cursors[i] += 1
                remaining -= 1
    return out


def run_pboc_credit_funds(limit: int = 4000) -> list[dict]:
    """九张信贷收支月表 → 逐（表, 指标, 月份）一行的观测记录，单位亿元。

    栏目页按月原位替换，故每次运行取到的是最新一期全部月份列；逐月序列由平台积累。
    """
    if limit <= 0:
        return []
    column, year = column_url()
    time.sleep(SLEEP)
    tables, published = discover(column)
    if not tables:
        raise RuntimeError(f"pboc-credit-funds: {year} 栏目页未发现任何信贷收支表")
    groups: list[list[dict]] = []
    for table in tables:
        try:
            groups.append(_table_rows(table, _fetch(table["url"]), published))
        except (urllib.error.URLError, TimeoutError, ValueError, tabular.TabularError) as exc:
            print(f"[pboc-credit-funds] {table['table_key']} failed: {exc}", flush=True)
        time.sleep(SLEEP)
    rows = _interleave([g for g in groups if g])
    if not rows:
        raise RuntimeError("pboc-credit-funds: 九张表全部无数据")
    print(
        f"[pboc-credit-funds] {year} 栏目 {len(tables)} 表 → {len(rows)} 行 "
        f"(published {published})",
        flush=True,
    )
    return rows[: int(limit)]
