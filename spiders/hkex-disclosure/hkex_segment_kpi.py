"""hkex_segment_kpi — 港股业绩公告「细分 KPI」的白名单口径抽取（task 3.5 一期）。

与 ``hkex_headline.py`` 同一姿态：只在公告 PDF 的**文本层**里读**已登记标签行**，
不做版面重建、不做表格结构推断、不做 OCR、不读正文叙述、不猜任何数字。

口径（与 ``docs/hkex-segment-kpi-caliber.md`` 一致，改动必须同步）：

* **每个指标必须带口径**：报告期（起..止，或时点 as_of）、分部/范围定义（来自
  公告图表标题里的行标签 + 该表脚注原文）、单位、逐字标签行。任一项缺失 →
  ``kpi_<slug>_status='unavailable'`` + 原因码，**不注册数值**。
* **只认白名单标签**：分区标题（``Table turnover rate(5) (times/day)``）与行标签
  （``Overall`` / ``Tai Er (self-operated)`` …）都必须与本模块的 spec 逐字
  （规范化后）相等；脚注号也是匹配键的一部分（同一文档里 ``Table turnover
  rate(2)`` 与 ``Table turnover rate(3)`` 是两张不同的表）。
* **列必须由位置定**：指标值按 PDF 文本层的字符列位落在哪一年列来归属，
  不按"第几个数字"猜；双期表头必须是 ``2026 2025``（本期年份在前），
  四列 ``2026 2025 2025 2024`` 只取前两组（本年 vs 上年）。
* **单位来自表头括号**（``(times/day)`` / ``(RMB)`` / ``(in thousands of RMB)`` /
  ``(RMB ’000)`` / ``(%)``）；金额换算成**原始币种单位**的整数（千元 ×1000），
  百分比按**百分数**存（``7.4`` = 7.4%，不折成 0.074）。
* **绝不从别的数字推算**：同店增长率若没有独立披露的百分比行，就不取
  （原因码 ``no_labelled_line``），不用 2026/2025 同店销售额相除。
* **脚注必须命中**：分区标题带的脚注号 ``(n)`` 必须在表后的**第一段** ``Notes:``
  里逐字找到，且脚注正文必须含 spec 声明的最小证据词（如 ``tables served`` /
  ``150 days``）；否则 ``caliber_note_missing`` / ``caliber_definition_mismatch``。

本模块只覆盖一期登记的两家（06862 海底捞 / 09922 九毛九）。其余公司不猜标签：
runner 侧对没有 spec 的公司写 ``kpi_status='not_applicable'`` +
``kpi_reason='no_phase1_kpi_target'``（显式，不是静默省略）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

__all__ = [
    "STATUS_OK",
    "STATUS_UNAVAILABLE",
    "STATUS_NOT_APPLICABLE",
    "TARGET_CODES",
    "KPI_TARGETS",
    "targets_for",
    "kpi_target_codes",
    "kpi_defaults",
    "extract_kpis",
    "all_unavailable",
]

STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"
STATUS_NOT_APPLICABLE = "not_applicable"

# ── 输出单位词表（payload 的 kpi_<slug>_unit 取值）──────────────────────────
UNIT_TIMES_PER_DAY = "times_per_day"      # 次/天
UNIT_PERCENT = "percent"                  # %（按百分数存，7.4 = 7.4%）
UNIT_CNY = "CNY"                          # 人民币元（原币单位）
UNIT_CNY_PER_PERSON = "CNY_per_person"    # 人民币元/人
UNIT_COUNT = "count"                      # 家/个

UNIT_ZH = {
    UNIT_TIMES_PER_DAY: "次/天",
    UNIT_PERCENT: "%",
    UNIT_CNY: "人民币元",
    UNIT_CNY_PER_PERSON: "人民币元/人",
    UNIT_COUNT: "家",
}

# 表头括号里的单位短语 -> (kind, scale)。规范化：casefold + 去 ’ + 折叠空白。
_UNIT_PHRASES: dict[str, tuple[str, int]] = {
    "times/day": ("times_per_day", 1),
    "times per day": ("times_per_day", 1),
    "%": ("percent", 1),
    "rmb": ("currency", 1),
    "rmb 000": ("currency", 1000),
    "in thousands of rmb": ("currency", 1000),
    "rmb thousand": ("currency", 1000),
}
# 括号内容不是单位、也不该进标签词（表头限定语）。
_IGNORED_PARENS = frozenset({
    "period-on-period",
    "period on period",
    "unaudited",
    "audited",
})

_NUM_RE = re.compile(r"^\(?-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\)?%?$")
_TRAILING_NOTE_RE = re.compile(r"\(\s*\d{1,2}\s*\)\s*$")
_PAREN_RE = re.compile(r"\(([^)]*)\)")
_LEADER_RE = re.compile(r"(?:\s*\.\s*){2,}")
_WORD_RE = re.compile(r"[^0-9a-z%]+")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_NOTES_RE = re.compile(r"^\s*notes?\b", re.I)
_FOOTNOTE_START_RE = re.compile(r"^\s*\((\d{1,2})\)")


# ── spec ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class KpiSpec:
    """一条 KPI 的抽取契约（标签、行、脚注、单位、口径文案）。"""

    slug: str
    kind: str                       # "block"（分区标题 + 行标签）| "inline"（单行标签）
    labels: tuple[str, ...]         # 分区/单行标签（规范化后逐字相等）
    row: str | None                 # block 的行标签（规范化）；inline 为 None
    note: int | None                # 分区标题脚注号；None = 标签自证
    unit: str                       # 输出单位（UNIT_*）
    unit_kind: str                  # times_per_day | percent | currency | count
    scale: int                      # 金额量级（1 或 1000）
    segment: str                    # 分部/范围定义（中文，人可读）
    period_kind: str                # "period"（起..止）| "point"（时点 as_of）
    store_prior: bool = True        # 是否登记同表上年列
    allow_single: bool = False      # 单值列（本年列有值、上年列空白）是否接受
    extra_notes: tuple[int, ...] = ()       # 需一并引用的补充脚注（如同店基数定义）
    must_contain: tuple[str, ...] = ()      # 脚注正文必须含的字面（防脚注错号）
    note_required: bool = True              # 脚注缺失是否判不可得


_HAIDILAO = "06862"
_JIUMAOJIU = "09922"

# 海底捞 2026 中期业绩公告（2026-08-25 披露，期末 2026-06-30）
_HAIDILAO_SPECS: tuple[KpiSpec, ...] = (
    KpiSpec(
        slug="table_turnover_overall",
        kind="block",
        labels=("table turnover rate",),
        row="overall",
        note=5,
        unit=UNIT_TIMES_PER_DAY,
        unit_kind="times_per_day",
        scale=1,
        segment="自营海底捞餐厅整体（表行 Overall，含中国大陆 + 港澳台）",
        period_kind="period",
        must_contain=("tables served",),
    ),
    KpiSpec(
        slug="table_turnover_mainland",
        kind="block",
        labels=("table turnover rate",),
        row="chinese mainland restaurants",
        note=5,
        unit=UNIT_TIMES_PER_DAY,
        unit_kind="times_per_day",
        scale=1,
        segment="中国大陆自营海底捞餐厅（表行 Chinese mainland restaurants）",
        period_kind="period",
        must_contain=("tables served",),
    ),
    KpiSpec(
        slug="table_turnover_hmt",
        kind="block",
        labels=("table turnover rate",),
        row="hong kong macau and taiwan regions",
        note=5,
        unit=UNIT_TIMES_PER_DAY,
        unit_kind="times_per_day",
        scale=1,
        segment="香港、澳门、台湾自营海底捞餐厅（表行 Hong Kong, Macau and Taiwan regions）",
        period_kind="period",
        must_contain=("tables served",),
    ),
    KpiSpec(
        slug="table_turnover_same_store_overall",
        kind="block",
        labels=("average same store table turnover rate",),
        row="overall",
        note=4,
        unit=UNIT_TIMES_PER_DAY,
        unit_kind="times_per_day",
        scale=1,
        segment="自营同店整体（同店基数见脚注 1：两期均开业满 150 天）",
        period_kind="period",
        extra_notes=(1,),
        must_contain=("tables served",),
    ),
    KpiSpec(
        slug="spending_per_guest_overall",
        kind="block",
        labels=("average spending per guest",),
        row="overall",
        note=1,
        unit=UNIT_CNY_PER_PERSON,
        unit_kind="currency",
        scale=1,
        segment="自营海底捞餐厅整体（表行 Overall）",
        period_kind="period",
        must_contain=("guests served",),
    ),
    KpiSpec(
        slug="spending_per_guest_mainland",
        kind="block",
        labels=("average spending per guest",),
        row="chinese mainland restaurants",
        note=1,
        unit=UNIT_CNY_PER_PERSON,
        unit_kind="currency",
        scale=1,
        segment="中国大陆自营海底捞餐厅（表行 Chinese mainland restaurants）",
        period_kind="period",
        must_contain=("guests served",),
    ),
    KpiSpec(
        slug="spending_per_guest_hmt",
        kind="block",
        labels=("average spending per guest",),
        row="hong kong macau and taiwan regions",
        note=1,
        unit=UNIT_CNY_PER_PERSON,
        unit_kind="currency",
        scale=1,
        segment="香港、澳门、台湾自营海底捞餐厅（表行 Hong Kong, Macau and Taiwan regions）",
        period_kind="period",
        must_contain=("guests served",),
    ),
    KpiSpec(
        slug="same_store_sales_overall",
        kind="block",
        labels=("same store sales",),
        row="overall",
        note=2,
        unit=UNIT_CNY,
        unit_kind="currency",
        scale=1000,
        segment="自营同店整体（同店基数见脚注 1）；金额为同店餐厅经营总收入",
        period_kind="period",
        extra_notes=(1,),
        must_contain=("same stores",),
    ),
    KpiSpec(
        slug="same_store_count_overall",
        kind="block",
        labels=("number of same stores",),
        row="overall",
        note=1,
        unit=UNIT_COUNT,
        unit_kind="count",
        scale=1,
        segment="自营同店整体（同店基数：两期均开业满 150 天）",
        period_kind="period",
        allow_single=True,
        must_contain=("150 days",),
    ),
    KpiSpec(
        slug="restaurant_count_self_operated",
        kind="inline",
        labels=("number of self operated haidilao restaurants",),
        row=None,
        note=None,
        unit=UNIT_COUNT,
        unit_kind="count",
        scale=1,
        segment="自营海底捞餐厅（公告摘要行；不含特许经营餐厅）",
        period_kind="point",
        note_required=False,
    ),
    KpiSpec(
        slug="restaurant_count_franchised",
        kind="inline",
        labels=("number of franchised haidilao restaurants",),
        row=None,
        note=None,
        unit=UNIT_COUNT,
        unit_kind="count",
        scale=1,
        segment="特许经营海底捞餐厅（公告摘要行）",
        period_kind="point",
        note_required=False,
    ),
)

# 九毛九 2026 中期业绩公告（2026-08-28 披露，期末 2026-06-30）
_JIUMAOJIU_SPECS: tuple[KpiSpec, ...] = (
    KpiSpec(
        slug="restaurant_count_group",
        kind="inline",
        labels=("number of restaurants",),
        row=None,
        note=1,
        unit=UNIT_COUNT,
        unit_kind="count",
        scale=1,
        segment="集团餐厅总数（公告摘要行；脚注拆分为自营 573 + 特许 33）",
        period_kind="point",
        note_required=False,
    ),
    KpiSpec(
        slug="table_turnover_tai_er",
        kind="block",
        labels=("table turnover rate",),
        row="tai er self operated",
        note=3,
        unit=UNIT_TIMES_PER_DAY,
        unit_kind="times_per_day",
        scale=1,
        segment="太二自营餐厅（表行 Tai Er (self-operated)）",
        period_kind="period",
        must_contain=("tables served",),
    ),
    KpiSpec(
        slug="table_turnover_song_hot_pot",
        kind="block",
        labels=("table turnover rate",),
        row="song hot pot",
        note=3,
        unit=UNIT_TIMES_PER_DAY,
        unit_kind="times_per_day",
        scale=1,
        segment="怂火锅自营餐厅（表行 Song Hot Pot）",
        period_kind="period",
        must_contain=("tables served",),
    ),
    KpiSpec(
        slug="table_turnover_jiu_mao_jiu",
        kind="block",
        labels=("table turnover rate",),
        row="jiu mao jiu self operated",
        note=3,
        unit=UNIT_TIMES_PER_DAY,
        unit_kind="times_per_day",
        scale=1,
        segment="九毛九自营餐厅（表行 Jiu Mao Jiu (self-operated)）",
        period_kind="period",
        must_contain=("tables served",),
    ),
    KpiSpec(
        slug="average_spending_per_customer_tai_er",
        kind="block",
        labels=("average spending per customer",),
        row="tai er self operated",
        note=4,
        unit=UNIT_CNY_PER_PERSON,
        unit_kind="currency",
        scale=1,
        segment="太二自营餐厅（表行 Tai Er (self-operated)）",
        period_kind="period",
        must_contain=("customer traffic",),
    ),
    KpiSpec(
        slug="average_spending_per_customer_song_hot_pot",
        kind="block",
        labels=("average spending per customer",),
        row="song hot pot",
        note=4,
        unit=UNIT_CNY_PER_PERSON,
        unit_kind="currency",
        scale=1,
        segment="怂火锅自营餐厅（表行 Song Hot Pot）",
        period_kind="period",
        must_contain=("customer traffic",),
    ),
    KpiSpec(
        slug="average_spending_per_customer_jiu_mao_jiu",
        kind="block",
        labels=("average spending per customer",),
        row="jiu mao jiu self operated",
        note=4,
        unit=UNIT_CNY_PER_PERSON,
        unit_kind="currency",
        scale=1,
        segment="九毛九自营餐厅（表行 Jiu Mao Jiu (self-operated)）",
        period_kind="period",
        must_contain=("customer traffic",),
    ),
    KpiSpec(
        slug="same_store_sales_tai_er",
        kind="block",
        labels=("same store sales",),
        row="tai er self operated",
        note=5,
        unit=UNIT_CNY,
        unit_kind="currency",
        scale=1000,
        segment="太二自营同店（同店基数：2025 与 2026 上半年均开业满 150 天）",
        period_kind="period",
        must_contain=("150 days",),
    ),
    KpiSpec(
        slug="same_store_sales_song_hot_pot",
        kind="block",
        labels=("same store sales",),
        row="song hot pot",
        note=5,
        unit=UNIT_CNY,
        unit_kind="currency",
        scale=1000,
        segment="怂火锅自营同店（同店基数：2025 与 2026 上半年均开业满 150 天）",
        period_kind="period",
        must_contain=("150 days",),
    ),
    KpiSpec(
        slug="same_store_sales_jiu_mao_jiu",
        kind="block",
        labels=("same store sales",),
        row="jiu mao jiu self operated",
        note=5,
        unit=UNIT_CNY,
        unit_kind="currency",
        scale=1000,
        segment="九毛九自营同店（同店基数：2025 与 2026 上半年均开业满 150 天）",
        period_kind="period",
        must_contain=("150 days",),
    ),
    KpiSpec(
        slug="same_store_sales_change_tai_er",
        kind="block",
        labels=("change in same store average daily sales",),
        row="tai er self operated",
        note=6,
        unit=UNIT_PERCENT,
        unit_kind="percent",
        scale=1,
        segment="太二自营同店日均销售同比（2026 上半年 vs 2025 上半年）",
        period_kind="period",
        store_prior=False,   # 该表第二列属 2025-vs-2024 对照组，不是本期上年列
        extra_notes=(5,),
        must_contain=("average daily revenue",),
    ),
    KpiSpec(
        slug="same_store_sales_change_song_hot_pot",
        kind="block",
        labels=("change in same store average daily sales",),
        row="song hot pot",
        note=6,
        unit=UNIT_PERCENT,
        unit_kind="percent",
        scale=1,
        segment="怂火锅自营同店日均销售同比（2026 上半年 vs 2025 上半年）",
        period_kind="period",
        store_prior=False,
        extra_notes=(5,),
        must_contain=("average daily revenue",),
    ),
    KpiSpec(
        slug="same_store_sales_change_jiu_mao_jiu",
        kind="block",
        labels=("change in same store average daily sales",),
        row="jiu mao jiu self operated",
        note=6,
        unit=UNIT_PERCENT,
        unit_kind="percent",
        scale=1,
        segment="九毛九自营同店日均销售同比（2026 上半年 vs 2025 上半年）",
        period_kind="period",
        store_prior=False,
        extra_notes=(5,),
        must_contain=("average daily revenue",),
    ),
)

KPI_TARGETS: dict[str, tuple[KpiSpec, ...]] = {
    _HAIDILAO: _HAIDILAO_SPECS,
    _JIUMAOJIU: _JIUMAOJIU_SPECS,
}

TARGET_CODES = frozenset(KPI_TARGETS)

# 语言上"明明有、但不是标签行"的目标：显式写不可得，不注册数值。
# 这些字段不参与抽取，只在 payload 上落 status/reason，便于下游一眼看到缺口。
_EXPLICIT_UNAVAILABLE: dict[str, tuple[tuple[str, str], ...]] = {
    _HAIDILAO: (
        ("same_store_sales_growth_overall",
         "no_labelled_line: 公告未以标签行披露同店销售增长率；2026/2025 同店销售额相除"
         "属推算，按纪律不取"),
        ("new_opened_restaurants",
         "narrative_only: 新开餐厅数只出现在业务回顾叙述段（24 家自营 + 14 家特许），"
         "不是标签行/摘要行"),
        ("closed_restaurants",
         "narrative_only: 关停餐厅数只出现在业务回顾叙述段（32 家），不是标签行/摘要行"),
    ),
}


def targets_for(stock_code: str) -> tuple[KpiSpec, ...]:
    """该股票的 KPI spec（未登记 → 空元组，不猜标签）。"""
    return KPI_TARGETS.get(str(stock_code).zfill(5), ())


def kpi_target_codes() -> tuple[str, ...]:
    return tuple(sorted(TARGET_CODES))


def kpi_defaults(stock_code: str) -> dict:
    """每行都带的三个小字段（其余 kpi_<slug>_* 只落在 results 行上）。"""
    code = str(stock_code).zfill(5)
    specs = targets_for(code)
    return {
        "kpi_status": STATUS_NOT_APPLICABLE,
        # 目标公司里非 results 行（月报表/一般公告等）也显式给原因，不留 null——
        # 读到 not_applicable 的人应能一眼区分「这份公告不是业绩公告」与
        # 「这家公司不属于一期范围」。
        "kpi_reason": "ann_type_not_results" if specs else "no_phase1_kpi_target",
        "kpi_targets": [s.slug for s in specs],
    }


# ── 文本规范化 ──────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    s = text.casefold().replace("’", "'").replace("–", "-").replace("—", "-")
    s = _LEADER_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _words(text: str) -> str:
    """标签词序列：去掉非字母数字（含括号/标点），折叠空白。"""
    s = _clean(text)
    s = _WORD_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _paren_unit(inner: str) -> tuple[str, int] | None:
    key = _clean(inner).replace("'", "")
    return _UNIT_PHRASES.get(key)


def _parse_header(raw: str) -> tuple[str, int | None, str | None, int | None]:
    """表头/摘要行 -> ``(label_words, footnote_no, unit_kind, scale)``.

    括号里的单位短语被取出，脚注号被剥掉，其余括号（``(self-operated)`` 等）
    保留到 label 里——标签匹配必须逐字。
    """
    notes = re.findall(r"\(\s*(\d{1,2})\s*\)", raw)
    note = int(notes[0]) if notes else None
    unit_kind: str | None = None
    scale: int | None = None

    def _strip(m: re.Match) -> str:
        nonlocal unit_kind, scale
        inner = m.group(1)
        unit = _paren_unit(inner)
        if unit is not None:
            if unit_kind is None:
                unit_kind, scale = unit
            return " "
        if _clean(inner) in _IGNORED_PARENS:
            return " "
        return m.group(0)

    base = _PAREN_RE.sub(_strip, raw)
    base = re.sub(r"\(\s*\d{1,2}\s*\)", " ", base)
    return _words(base), note, unit_kind, scale


def _has_number(line: str) -> bool:
    for m in re.finditer(r"\S+", line):
        tok = _TRAILING_NOTE_RE.sub("", m.group())
        if tok != "." and _NUM_RE.match(tok):
            return True
    return False


def _is_filler(tok: str) -> bool:
    """点线引导符 / 纯点 token（``.`` / ``......``），不是值也不是标签词。"""
    return bool(re.fullmatch(r"\.+", tok))


def _split_label_values(line: str) -> tuple[str, list[tuple[int, str]]]:
    """行 -> ``(标签前缀, [(x, 原始数字 token), ...])``.

    以"从某个数字 token 起到行尾全是数字/点线"为界：界前是标签（可能含
    ``Tier 1`` 这类词内数字、``(self-operated)`` 这类括号），界后是数值列。
    这样不依赖固定列宽阈值，两列表、四列表、点线引导行都能切。
    """
    toks = [(m.start(), m.group()) for m in re.finditer(r"\S+", line)
            if not _is_filler(m.group())]
    for i, (x, tok) in enumerate(toks):
        if _parse_number(tok) is None:
            continue
        rest = toks[i:]
        if all(_parse_number(t) is not None for _x, t in rest):
            return line[:x], [(xx, t) for xx, t in rest if _parse_number(t) is not None]
    return line, []


def _label_values(line: str) -> tuple[str, int | None]:
    """标签前的纯文本 + 首个数字的列位（无数字 -> None）。"""
    prefix, vals = _split_label_values(line)
    return prefix, (vals[0][0] if vals else None)


def _parse_number(tok: str) -> tuple[str, bool] | None:
    """``(canonical_text, is_integer)``; 非数字 -> None。``(22.9)`` -> ``-22.9``。"""
    t = _TRAILING_NOTE_RE.sub("", tok)
    if t.endswith("%"):
        t = t[:-1]
    neg = t.startswith("(") and t.endswith(")")
    if neg:
        t = t[1:-1]
    if t.startswith("-"):
        neg = True
        t = t[1:]
    t = t.replace(",", "")
    if not re.match(r"^\d+(?:\.\d+)?$", t):
        return None
    integer = "." not in t
    if integer:
        value = str(-int(t) if neg else int(t))
    else:
        value = ("-" if neg else "") + t
    return value, integer


# ── 版面定位 ────────────────────────────────────────────────────────────────

def _pages(text: str) -> list[list[str]]:
    return [chunk.splitlines() for chunk in text.split("\f")]


def _all_labels(specs: tuple[KpiSpec, ...]) -> set[str]:
    return {label for spec in specs for label in spec.labels}


def _year_header(pages, page: int, idx: int, period_year: int):
    """向上找最近一行含 >=2 个年份的表头；要求首列 = 本期年、次列 = 上年。

    窗口 30 行：同店表一张表里有 4 个分区、表头到最后一个分区实测跨 20 行
    （06862 中期公告第 8 页）。"最近的多期年份行"是唯一锚点，不跨表猜。
    """
    lines = pages[page]
    for back in range(1, 31):
        j = idx - back
        if j < 0:
            break
        years = [(m.start(), int(m.group())) for m in _YEAR_RE.finditer(lines[j])]
        if len(years) >= 2:
            if years[0][1] != period_year or years[1][1] != period_year - 1:
                return None
            return years
    return None


def _section_end(pages, page: int, start: int, labels: set[str]) -> int:
    """同页内分区终点：下一段无数字的标签行 / 空行 / ``Notes:``。"""
    lines = pages[page]
    for i in range(start, len(lines)):
        line = lines[i]
        if not line.strip():
            return i
        if _NOTES_RE.match(line):
            return i
        label_raw, first_x = _label_values(line)
        if first_x is None:
            return i
        label, _note, _uk, _sc = _parse_header(label_raw)
        if label in labels:
            return i
    return len(lines)


def _values(line: str) -> list[tuple[int, str]]:
    return _split_label_values(line)[1]


def _column(x: int, year_xs: list[int]) -> int:
    for i in range(len(year_xs) - 1):
        if x < (year_xs[i] + year_xs[i + 1]) / 2:
            return i
    return len(year_xs) - 1


# ── 脚注 ────────────────────────────────────────────────────────────────────

def _notes(pages, page: int, start: int) -> dict[int, str]:
    """表后**第一段** ``Notes:`` 的脚注字典（号 -> 逐字正文）。"""
    lines = pages[page]
    i = start
    while i < len(lines) and not _NOTES_RE.match(lines[i]):
        i += 1
    if i >= len(lines):
        return {}
    out: dict[int, str] = {}
    current: int | None = None
    buf: list[str] = []
    for line in lines[i + 1:]:
        stripped = line.strip()
        m = _FOOTNOTE_START_RE.match(stripped)
        if m:
            if current is not None and buf:
                out[current] = re.sub(r"\s+", " ", " ".join(buf)).strip()
            current = int(m.group(1))
            buf = [stripped[m.end():].strip()]
            continue
        if current is None:
            continue
        if not stripped:
            if buf:
                out[current] = re.sub(r"\s+", " ", " ".join(buf)).strip()
            current, buf = None, []
            continue
        if re.fullmatch(r"\d{1,3}", stripped):   # 页码
            break
        buf.append(stripped)
    if current is not None and buf:
        out[current] = re.sub(r"\s+", " ", " ".join(buf)).strip()
    return out


# ── 期间 ────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _headline_module():
    import importlib.util
    import os
    import sys

    try:
        from . import hkex_headline as module  # 若将来目录改为合法包名
        return module
    except ImportError:
        pass
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hkex_headline.py")
    spec = importlib.util.spec_from_file_location("hkex_disclosure_headline_kpi", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load hkex_headline from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _shift_year(iso: str, years: int) -> str:
    y, m, d = (int(x) for x in iso.split("-"))
    try:
        return date(y + years, m, d).isoformat()
    except ValueError:            # 2/29 -> 2/28
        return date(y + years, m, 28).isoformat()


def _period_start(period_end: str, months: int) -> str:
    y, m, _d = (int(x) for x in period_end.split("-"))
    py, pm = (y + 1, 1) if m == 12 else (y, m + 1)
    total = (py * 12 + pm - 1) - months
    sy, sm = divmod(total, 12)
    return date(sy, sm + 1, 1).isoformat()


# ── 抽取 ────────────────────────────────────────────────────────────────────

def _fmt_evidence(items: list[str]) -> list[str]:
    seen: list[str] = []
    for it in items:
        s = re.sub(r"\s+", " ", it).strip()
        if s and s not in seen:
            seen.append(s)
    return seen[:8]


def _caliber(spec: KpiSpec, period: dict, label: str, row: str | None,
             definition: str | None, prior_period: str | None) -> str:
    unit_text = UNIT_ZH[spec.unit]
    parts = []
    if spec.period_kind == "point":
        parts.append(f"as_of={period['period_end']}")
    else:
        parts.append(f"period={period['period_start']}..{period['period_end']}"
                     f" ({period['months']}M)")
    parts.append(f"segment={spec.segment}")
    parts.append(f"unit={spec.unit} ({unit_text})")
    parts.append(f"label={label}" + (f" :: row={row}" if row else ""))
    if definition:
        parts.append(f"definition={definition}")
    else:
        parts.append("definition=label_self_defining")
    if prior_period:
        parts.append(f"prior_period={prior_period}")
    if spec.scale != 1:
        parts.append(f"source_scale={spec.scale}")
    return "; ".join(parts)


def _ok_field(spec: KpiSpec, value: str, caliber: str,
              prior: str | None, evidence: list[str]) -> dict:
    return {
        f"kpi_{spec.slug}": value,
        f"kpi_{spec.slug}_unit": spec.unit,
        f"kpi_{spec.slug}_caliber": caliber,
        f"kpi_{spec.slug}_status": STATUS_OK,
        f"kpi_{spec.slug}_reason": None,
        f"kpi_{spec.slug}_prior": prior,
        f"kpi_{spec.slug}_evidence": _fmt_evidence(evidence),
    }


def _fail_slug(slug: str, reason: str,
               evidence: list[str] | None = None) -> dict:
    return {
        f"kpi_{slug}": None,
        f"kpi_{slug}_unit": None,
        f"kpi_{slug}_caliber": None,
        f"kpi_{slug}_status": STATUS_UNAVAILABLE,
        f"kpi_{slug}_reason": reason,
        f"kpi_{slug}_prior": None,
        f"kpi_{slug}_evidence": _fmt_evidence(evidence or []),
    }


def _fail_field(spec: KpiSpec, reason: str,
                evidence: list[str] | None = None) -> dict:
    return _fail_slug(spec.slug, reason, evidence)


def all_unavailable(stock_code: str, code: str, detail: str | None = None) -> dict:
    """整份 PDF 不可用时的统一落账（每个目标都显式不可得）。"""
    sid = str(stock_code).zfill(5)
    if not targets_for(sid):
        return kpi_defaults(sid)
    out: dict = {
        "kpi_status": STATUS_UNAVAILABLE,
        "kpi_reason": code if detail is None else f"{code}: {detail}",
        "kpi_targets": [s.slug for s in targets_for(sid)],
    }
    reason = out["kpi_reason"]
    for spec in targets_for(sid):
        out.update(_fail_field(spec, reason))
    # 未被解析的公告：`_EXPLICIT_UNAVAILABLE` 那几条静态原因（例如「只在叙述段」）
    # 是针对**解析过的那一期**的断言，不能挪用到没抽过的行上；这里统一写行级原因。
    for slug, _why in _EXPLICIT_UNAVAILABLE.get(sid, ()):
        out.update(_fail_slug(slug, reason))
    return out


def _find_block_sections(pages, specs: tuple[KpiSpec, ...]) -> list[dict]:
    labels = _all_labels(specs)
    found: dict[tuple[int, int], dict] = {}
    for page, lines in enumerate(pages):
        for i in range(len(lines)):
            if not lines[i].strip():
                continue
            for span in (1, 2):
                if i + span > len(lines):
                    break
                if _has_number(lines[i]):
                    break
                # span=2 只接"纯续行"（下一行也没有数字），例如
                # 「Change in same store average daily sales(6)」+「(period-on-period) (%)」
                if span == 2 and (not lines[i + 1].strip()
                                  or _has_number(lines[i + 1])):
                    break
                raw = " ".join(x.strip() for x in lines[i:i + span] if x.strip())
                label, note, unit_kind, scale = _parse_header(raw)
                if label not in labels:
                    continue
                key = (page, i)
                prev = found.get(key)
                if prev is not None and prev["span"] >= span:
                    continue
                found[key] = {
                    "page": page,
                    "start": i,
                    "span": span,
                    "label": label,
                    "note": note,
                    "unit_kind": unit_kind,
                    "scale": scale,
                    "raw": raw,
                    "end": _section_end(pages, page, i + span, labels),
                }
    return list(found.values())


def _extract_block(spec: KpiSpec, pages, sections: list[dict],
                   period: dict) -> dict:
    label = spec.labels[0]
    hits = [s for s in sections
            if s["label"] in spec.labels and s["note"] == spec.note]
    if not hits:
        return _fail_field(spec, "no_section_line")
    if len(hits) > 1:
        pages_seen = sorted({h["page"] for h in hits})
        if len(pages_seen) > 1:
            return _fail_field(
                spec, "ambiguous_section: same label+footnote on pages "
                + ", ".join(str(p + 1) for p in pages_seen),
                [h["raw"] for h in hits])

    collected: list[dict] = []
    for section in hits:
        if section["unit_kind"] is not None and (
                section["unit_kind"] != spec.unit_kind
                or section["scale"] != spec.scale):
            return _fail_field(
                spec, f"unit_mismatch: header {section['unit_kind']}/{section['scale']} "
                f"!= spec {spec.unit_kind}/{spec.scale}", [section["raw"]])
        if spec.unit_kind == "count" and section["unit_kind"] is not None:
            return _fail_field(spec, "unit_mismatch: count section carries a unit phrase",
                               [section["raw"]])
        years = _year_header(pages, section["page"], section["start"], period["year"])
        if years is None:
            return _fail_field(spec, "column_header_not_found", [section["raw"]])
        year_xs = [x for x, _y in years]

        rows: list[dict] = []
        for i in range(section["start"] + section["span"], section["end"]):
            line = pages[section["page"]][i]
            prefix, _first_x = _label_values(line)
            vals = _values(line)
            if not vals:
                continue
            row_label = _words(prefix)
            if row_label != spec.row:
                continue
            current = [(x, t) for x, t in vals if _column(x, year_xs) == 0]
            prior = [(x, t) for x, t in vals if _column(x, year_xs) == 1]
            rows.append({
                "current": current,
                "prior": prior,
                "line": line,
                "prefix": re.sub(r"\s+", " ", _LEADER_RE.sub(" ", prefix)).strip(),
                "row_label": row_label,
            })

        if not rows:
            return _fail_field(spec, "no_row_label", [section["raw"]])
        if len({tuple(v for _x, v in r["current"]) for r in rows}) > 1:
            return _fail_field(
                spec, "ambiguous_row_label: repeated "
                f"{spec.row!r} rows disagree", [r["line"] for r in rows])

        row = rows[0]
        if len(row["current"]) != 1:
            if not row["current"]:
                reason = "current_column_empty"
                if row["prior"]:
                    reason = "column_position_mismatch: value(s) fall in the prior column"
                return _fail_field(spec, reason, [row["line"]])
            return _fail_field(spec, "ambiguous_current_value", [row["line"]])
        if len(row["prior"]) > 1:
            return _fail_field(spec, "ambiguous_prior_value", [row["line"]])
        if not row["prior"] and not spec.allow_single and spec.store_prior:
            return _fail_field(spec, "prior_column_empty", [row["line"]])

        notes_doc = _notes(pages, section["page"], section["start"] + section["span"])
        definition = None
        note_lines: list[str] = []
        if spec.note_required and spec.note is None:
            return _fail_field(spec, "caliber_note_not_declared", [section["raw"]])
        if spec.note is not None:
            text = notes_doc.get(spec.note)
            if text is None and spec.note_required:
                return _fail_field(spec, "caliber_note_missing",
                                   [section["raw"]])
            note_lines.append(f"({spec.note}) {text}" if text else "")
            parts = [text] if text else []
            for extra in spec.extra_notes:
                extra_text = notes_doc.get(extra)
                if extra_text is None:
                    if spec.note_required:
                        return _fail_field(spec, f"caliber_note_missing: ({extra})",
                                           [section["raw"]])
                    continue
                note_lines.append(f"({extra}) {extra_text}")
                parts.append(extra_text)
            definition = " / ".join(p for p in parts if p) or None
            if spec.must_contain:
                low = (definition or "").casefold()
                missing = [t for t in spec.must_contain if t.casefold() not in low]
                if missing:
                    return _fail_field(
                        spec, "caliber_definition_mismatch: missing "
                        + ", ".join(repr(t) for t in missing),
                        note_lines + [section["raw"]])

        collected.append({
            "section": section,
            "row": row,
            "years": years,
            "definition": definition,
            "note_lines": note_lines,
        })

    first = collected[0]
    section = first["section"]
    row = first["row"]
    tok = row["current"][0][1]
    parsed = _parse_number(tok)
    if parsed is None:
        return _fail_field(spec, "unparsable_value", [row["line"]])
    value, is_integer = parsed
    if spec.unit_kind in ("count", "percent") or spec.scale != 1:
        if not is_integer and spec.unit_kind != "percent":
            return _fail_field(spec, "non_integer_value", [row["line"]])
    if spec.scale != 1:
        value = str(int(value) * spec.scale)

    prior_value = None
    if spec.store_prior and row["prior"]:
        parsed_prior = _parse_number(row["prior"][0][1])
        if parsed_prior is None:
            return _fail_field(spec, "unparsable_value: prior", [row["line"]])
        prior_value = parsed_prior[0]
        if spec.scale != 1:
            prior_value = str(int(prior_value) * spec.scale)

    period_start = None
    prior_period = None
    if spec.period_kind == "period":
        period_start = period["period_start"]
        prior_period = (f"{_shift_year(period['period_start'], -1)}.."
                        f"{_shift_year(period['period_end'], -1)}")
    caliber = _caliber(spec, period, section["raw"], row["prefix"],
                       first["definition"], prior_period)
    evidence = [
        f"[section] {section['raw']}",
        f"[column] {'  '.join(str(y) for _x, y in first['years'])}",
        f"[row] {row['line']}",
    ] + first["note_lines"]
    if period_start:
        evidence.insert(1, f"[period] {period_start}..{period['period_end']}")
    return _ok_field(spec, value, caliber, prior_value, evidence)


def _find_inline(pages, spec: KpiSpec) -> list[dict]:
    """摘要行（标签 + 值同一行，或标签折到下一行、值在下一行）。"""
    hits: list[dict] = []
    for page, lines in enumerate(pages):
        for i in range(len(lines)):
            line = lines[i]
            if not line.strip():
                continue
            prefix, first_x = _label_values(line)
            candidates: list[tuple[int, str, list[str]]] = []
            if first_x is not None:
                candidates.append((1, prefix, [line]))
            elif i + 1 < len(lines):
                nxt_prefix, nxt_x = _label_values(lines[i + 1])
                if nxt_x is not None:
                    candidates.append(
                        (2, f"{line.strip()} {nxt_prefix}".strip(),
                         [line, lines[i + 1]]))
            for span, label_raw, used in candidates:
                label, note, unit_kind, scale = _parse_header(label_raw)
                if label not in spec.labels:
                    continue
                hits.append({
                    "page": page,
                    "start": i,
                    "span": span,
                    "raw": " ".join(x.strip() for x in used if x.strip()),
                    "label_raw": label_raw.strip(),
                    "note": note,
                    "unit_kind": unit_kind,
                    "scale": scale,
                    "lines": used,
                })
    return hits


def _extract_inline(spec: KpiSpec, pages, period: dict) -> dict:
    hits = _find_inline(pages, spec)
    if not hits:
        return _fail_field(spec, "no_labelled_line")
    if len(hits) > 1:
        return _fail_field(
            spec, "ambiguous_labelled_line: matched "
            f"{len(hits)} lines", [h["raw"] for h in hits])
    hit = hits[0]
    if hit["unit_kind"] is not None and (
            hit["unit_kind"] != spec.unit_kind or hit["scale"] != spec.scale):
        return _fail_field(
            spec, f"unit_mismatch: header {hit['unit_kind']}/{hit['scale']} "
            f"!= spec {spec.unit_kind}/{spec.scale}", [hit["raw"]])
    if spec.unit_kind == "count" and hit["unit_kind"] is not None:
        return _fail_field(spec, "unit_mismatch: count line carries a unit phrase",
                           [hit["raw"]])
    years = _year_header(pages, hit["page"], hit["start"], period["year"])
    if years is None:
        return _fail_field(spec, "column_header_not_found", [hit["raw"]])
    year_xs = [x for x, _y in years]
    vals: list[tuple[int, str]] = []
    for line in hit["lines"]:
        vals.extend(_values(line))
    current = [(x, t) for x, t in vals if _column(x, year_xs) == 0]
    prior = [(x, t) for x, t in vals if _column(x, year_xs) == 1]
    if len(current) != 1:
        return _fail_field(spec, "current_column_empty" if not current
                           else "ambiguous_current_value", [hit["raw"]])
    if spec.store_prior and len(prior) > 1:
        return _fail_field(spec, "ambiguous_prior_value", [hit["raw"]])
    tok = current[0][1]
    parsed = _parse_number(tok)
    if parsed is None:
        return _fail_field(spec, "unparsable_value", [hit["raw"]])
    value, is_integer = parsed
    if spec.unit_kind == "count" and not is_integer:
        return _fail_field(spec, "non_integer_value", [hit["raw"]])
    if spec.scale != 1:
        if not is_integer:
            return _fail_field(spec, "non_integer_value", [hit["raw"]])
        value = str(int(value) * spec.scale)

    prior_value = None
    if spec.store_prior and prior:
        parsed_prior = _parse_number(prior[0][1])
        if parsed_prior is None:
            return _fail_field(spec, "unparsable_value: prior", [hit["raw"]])
        prior_value = parsed_prior[0]
        if spec.scale != 1:
            prior_value = str(int(prior_value) * spec.scale)

    notes_doc = _notes(pages, hit["page"], hit["start"] + hit["span"])
    definition = None
    note_lines: list[str] = []
    if spec.note is not None:
        text = notes_doc.get(spec.note)
        if text is None and spec.note_required:
            return _fail_field(spec, "caliber_note_missing", [hit["raw"]])
        if text:
            note_lines.append(f"({spec.note}) {text}")
            definition = text
    if spec.must_contain and spec.note_required:
        low = (definition or "").casefold()
        missing = [t for t in spec.must_contain if t.casefold() not in low]
        if missing:
            return _fail_field(
                spec, "caliber_definition_mismatch: missing "
                + ", ".join(repr(t) for t in missing),
                [hit["raw"]] + note_lines)

    prior_period = None
    if spec.period_kind == "period":
        prior_period = (f"{_shift_year(period['period_start'], -1)}.."
                        f"{_shift_year(period['period_end'], -1)}")
    caliber = _caliber(spec, period, hit["label_raw"], None, definition, prior_period)
    evidence = [f"[line] {hit['raw']}",
                f"[column] {'  '.join(str(y) for _x, y in years)}"] + note_lines
    return _ok_field(spec, value, caliber, prior_value, evidence)


def _explicit_unavailable(stock_code: str) -> dict:
    out: dict = {}
    for slug, why in _EXPLICIT_UNAVAILABLE.get(str(stock_code).zfill(5), ()):
        out.update(_fail_slug(slug, why))
    return out


def extract_kpis(text: str, title: str, stock_code: str) -> dict:
    """从单份业绩公告文本层抽取该公司登记的 KPI；不确定一律显式不可得。"""
    code = str(stock_code).zfill(5)
    specs = targets_for(code)
    if not specs:
        return {"kpi_status": STATUS_NOT_APPLICABLE,
                "kpi_reason": "no_phase1_kpi_target",
                "kpi_targets": []}
    if not text or not text.strip():
        return all_unavailable(code, "no_text_layer")
    if not title:
        return all_unavailable(code, "no_title")

    period_tuple = _headline_module().parse_title_period(title)
    if period_tuple is None:
        return all_unavailable(code, "period_not_stated_in_title")
    months, period_end, _phrase = period_tuple
    period = {
        "months": months,
        "period_end": period_end,
        "period_start": _period_start(period_end, months),
        "year": int(period_end[:4]),
    }
    if not _headline_module()._period_in_text(text, period_end):
        return all_unavailable(code, "period_end_not_in_document")

    pages = _pages(text)
    sections = _find_block_sections(pages, specs)
    out: dict = {"kpi_targets": [s.slug for s in specs]}
    ok_count = 0
    for spec in specs:
        if spec.kind == "block":
            fields = _extract_block(spec, pages, sections, period)
        else:
            fields = _extract_inline(spec, pages, period)
        out.update(fields)
        if fields[f"kpi_{spec.slug}_status"] == STATUS_OK:
            ok_count += 1
    out.update(_explicit_unavailable(code))

    failed = [s.slug for s in specs
              if out[f"kpi_{s.slug}_status"] != STATUS_OK]
    explicit = [slug for slug, _why in _EXPLICIT_UNAVAILABLE.get(code, ())]
    if not failed and not explicit:
        out["kpi_status"] = STATUS_OK
        out["kpi_reason"] = None
    elif ok_count:
        out["kpi_status"] = "partial"
        if failed:
            out["kpi_reason"] = "unavailable: " + ", ".join(failed + explicit)
        else:
            out["kpi_reason"] = "explicit_unavailable: " + ", ".join(explicit)
    else:
        out["kpi_status"] = STATUS_UNAVAILABLE
        out["kpi_reason"] = "no_kpi_landed"
    return out
