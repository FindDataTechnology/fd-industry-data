#!/usr/bin/env python3
"""A-share hotel operating-data channel — history backfill + explicit unavailable.

SOURCE = ``cninfo-operating`` (existing lane; this module adds no crawler).

## What the A-share market actually discloses (measured, not assumed)

Evidence: ``openspec/changes/add-hotel-vertical/reports/
3.5-ashare-disclosure-evidence.md`` (2026-11-08). Only one issuer ever filed a
stand-alone operating brief: Jinjiang Hotels (600754). Two briefs exist, and
they are NOT the same caliber:

* 2023-09-27 — 《有限服务型连锁酒店 2023 年 7、8 月部分经营数据简报》
  （art_code ``AN202309261599895864``）. The brief contains four tables, one per
  month: 表一/表二 = 中国境内 7 月 / 8 月，表三/表四 = 中国境外 7 月 / 8 月。
  Each table is a **single-month** level.
* 2023-12-22 — 《... 2023 年 1-11 月部分经营数据简报》
  （art_code ``AN202312221614813250``）. Two tables: 表一 = 中国境内 1-11 月，
  表二 = 中国境外 1-11 月。 Each table is **year-to-date cumulative** (年初至今
  累计), NOT a quarter and NOT a single month.

Every table splits the portfolio into three disclosed grades — 中端酒店 /
经济型酒店 / 平 均 (overall average). Each grade is written as its own
observation; nothing is merged, and the disclosed 平均 row is never recomputed
as a simple mean of the two tiers (it is room-count weighted, so the recomputed
mean differs from the published figure).

## Caliber rules encoded here (each is a place where a wrong default fabricates)

1. **Period comes from the table header, not from the payload's ``period``.**
   The spider sets ``period`` to the announcement month minus one when the
   title carries no clear period; for the 7、8 月 brief that yields ``2023-08``
   and would smear the July table into August. The header labels (``1-11 月``,
   ``7 月``, ``8 月``) are authoritative.
2. **``1-11 月`` is cumulative YTD.** Such rows are written with
   ``granularity='cumulative-ytd'``; the concept metadata also carries the
   literal token ``cumulative-ytd`` in both ``measure`` and ``frequency`` (the
   YTD concept family), and every written grade row is echoed to the extractor
   notes with the same token. A YTD value is never presented as a single
   quarter or a single month.
3. **``7 月`` / ``8 月`` tables are single months** and use
   ``granularity='month'``. They are not forced into the YTD caliber.
4. **Only the current-period column is written.** The 2022 comparative columns
   and the ``同比增减`` column are disclosed for comparison, not as this
   brief's observation, and are skipped (not approximated, not written).
5. **Units follow the source label.** Domestic tables print ``（元/间）`` ->
   ``cny_per_room``. The Dec overseas table prints ``（欧元/间）`` ->
   ``eur_per_room``. The Sept overseas tables print ``（元/间）`` — an
   inconsistency in that announcement (same overseas portfolio, same value
   magnitude as the Dec EUR table). Those rows are still recorded under the
   overseas EUR caliber, and the label discrepancy is written into the notes;
   the values themselves are never altered.
6. **No trend gate.** The >= 24-period gate would drop every row (two briefs is
   the whole A-share history). The spec's answer for current periods is an
   explicit unavailability statement, not a padded series. ``gate_periods`` is
   therefore deliberately not applied here.
7. **No quarterly-report body parsing.** Quarterly reports are financial
   statements; this line must not parse them. Current-period RevPAR is resolved
   by the ``cn-report`` / ``fd-cn-report`` lane, and this module only exposes
   the unavailability record pointing there.

## Unavailability

``UNAVAILABLE`` is machine-readable: the five other A-share hotel issuers
(600258 首旅 / 601007 金陵 / 000428 华天 / 301073 君亭 / 000610 *ST西旅) never
filed a stand-alone operating brief, and Jinjiang stopped after 2023 (2024-01
onward only quarterly reports and IR notices). Read paths call
``unavailable_for()`` / ``availability()`` and render the reason instead of an
empty value.

Usage (never writes without ``--write``)::

    set -a && source ~/.config/finddata/dashboards.env && set +a
    .venv/bin/python -m hotel_extractors.ashare_operating          # dry-run
    .venv/bin/python -m hotel_extractors.ashare_operating --write  # add rows
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from hotel_extractors._common import (  # noqa: E402
    dedup_latest,
    ensure_unit_concepts,
    insert_rows,
    unit_items,
)

SOURCE = "cninfo-operating"

#: A-share hotel monitoring universe (registered by task 3.6,
#: scripts/register_hotel_universe.py). Only 600754 has any payload.
CODES = ("600754", "600258", "601007", "000428", "301073", "000610")
CODE_ZH = {
    "600754": "锦江酒店",
    "600258": "首旅酒店",
    "601007": "金陵饭店",
    "000428": "华天酒店",
    "301073": "君亭酒店",
    "000610": "*ST西旅",
}

# ── concepts: 3 metric families x 2 scopes x 3 grades x 2 calibers ──────────
# Dimensions must live in the concept code because observations key on
# (concept, entity, date, granularity) with entity fixed to the issuer stock.
_METRICS = (
    ("revpar", "RevPAR", "RevPAR"),
    ("adr", "平均房价", "ADR"),
    ("occ", "平均出租率", "Occupancy"),
)
_SCOPES = (
    ("domestic", "中国境内", "China domestic"),
    ("overseas", "中国境外", "overseas"),
)
_GRADES = (
    ("midscale", "中端", "midscale"),
    ("economy", "经济型", "economy"),
    ("overall", "整体平均", "overall average"),
)
# caliber token -> (zh, en, measure, frequency, granularity)
#
# granularity 受 DB 列宽约束：``semantic_observations.granularity`` 是
# ``varchar(8)``（实测写 ``cumulative-ytd`` 会 StringDataRightTruncation），
# 所以粒度层用 ``ytd``，完整的 ``cumulative-ytd`` 语义留在 concepts 的
# measure/frequency 与每条 note 里。
_CALIBERS = {
    "ytd": ("年初至今累计", "cumulative YTD", "cumulative-ytd",
            "cumulative-ytd", "ytd"),
    "monthly": ("单月", "single month", "level", "monthly", "month"),
}


def _unit(metric: str, scope: str) -> str:
    if metric == "occ":
        return "%"
    return "cny_per_room" if scope == "domestic" else "eur_per_room"


def _concept_code(metric: str, scope: str, grade: str, caliber: str) -> str:
    return f"hotel.ashare.{metric}.{scope}.{grade}.{caliber}"


CONCEPTS: list[tuple] = [
    (
        _concept_code(metric, scope, grade, caliber),
        f"A股酒店 {zh_metric} {zh_scope} {zh_grade}（{zh_caliber}）",
        f"A-share hotel {en_metric}, {en_scope}, {en_grade} ({en_caliber})",
        "行业经营",
        _unit(metric, scope),
        measure,
        frequency,
        "stock",
        SOURCE,
    )
    for metric, zh_metric, en_metric in _METRICS
    for scope, zh_scope, en_scope in _SCOPES
    for grade, zh_grade, en_grade in _GRADES
    for caliber, (zh_caliber, en_caliber, measure, frequency, _gran) in
    _CALIBERS.items()
]

CONCEPT_SET = frozenset(c[0] for c in CONCEPTS)

# ── machine-readable unavailable matrix ─────────────────────────────────────
_EVIDENCE = ("openspec/changes/add-hotel-vertical/reports/"
             "3.5-ashare-disclosure-evidence.md")
_CN_REPORT_FALLBACK = ("当前期 RevPAR/OCC/ADR 指向 cn-report / fd-cn-report "
                       "财报线；本线只做引用与不可得声明，不解析季报正文")

UNAVAILABLE: tuple[dict, ...] = (
    {
        "status": "unavailable",
        "entity_code": "600258",
        "entity_name": "首旅酒店",
        "periods": "all",
        "reason": ("从未单独披露经营数据：近 ~450 条公告（约 2021 起）无任何"
                   "「经营数据」类公告；唯一相近的《关于公司经营情况等相关事项"
                   "的公告》属 IR 表述，无结构化表"),
        "evidence": _EVIDENCE,
        "recovery": _CN_REPORT_FALLBACK,
    },
    {
        "status": "unavailable",
        "entity_code": "601007",
        "entity_name": "金陵饭店",
        "periods": "all",
        "reason": ("从未单独披露经营数据：近期公告检索唯一命中为「非经营性资金"
                   "占用」，属关键词假阳性"),
        "evidence": _EVIDENCE,
        "recovery": _CN_REPORT_FALLBACK,
    },
    {
        "status": "unavailable",
        "entity_code": "000428",
        "entity_name": "华天酒店",
        "periods": "all",
        "reason": ("从未单独披露经营数据：近期公告检索唯一命中为「非经营性资金"
                   "占用」，属关键词假阳性"),
        "evidence": _EVIDENCE,
        "recovery": _CN_REPORT_FALLBACK,
    },
    {
        "status": "unavailable",
        "entity_code": "301073",
        "entity_name": "君亭酒店",
        "periods": "all",
        "reason": ("从未单独披露经营数据：近期公告检索唯一命中为「非经营性资金"
                   "占用」，属关键词假阳性"),
        "evidence": _EVIDENCE,
        "recovery": _CN_REPORT_FALLBACK,
    },
    {
        "status": "unavailable",
        "entity_code": "000610",
        "entity_name": "*ST西旅",
        "periods": "all",
        "reason": ("从未单独披露经营数据：近期公告检索唯一命中为「非经营性资金"
                   "占用」，属关键词假阳性"),
        "evidence": _EVIDENCE,
        "recovery": _CN_REPORT_FALLBACK,
    },
    {
        "status": "unavailable",
        "entity_code": "600754",
        "entity_name": "锦江酒店",
        "periods": ">=2024-01",
        "reason": ("2024 年起停发《有限服务型连锁酒店部分经营数据简报》，"
                   "近 150 条公告仅《季度报告》《业绩说明会公告》；RevPAR "
                   "只存在于季报正文，本线不解析"),
        "evidence": _EVIDENCE,
        "recovery": _CN_REPORT_FALLBACK,
    },
)


def unavailable_for(entity_code: str, period: str) -> dict | None:
    """Unavailability record for ``(company, YYYY-MM)``, or ``None``.

    ``periods`` semantics: ``"all"`` = never disclosed; ``">=YYYY-MM"`` = open
    ended from that period on. Read paths MUST render the returned reason
    instead of an empty value / zero.
    """
    for rec in UNAVAILABLE:
        if rec["entity_code"] != str(entity_code):
            continue
        spec = rec["periods"]
        if spec == "all":
            return rec
        if spec.startswith(">=") and str(period) >= spec[2:]:
            return rec
    return None


def availability(entity_code: str, period: str) -> dict:
    """Read-path status: explicit unavailable, or "query stored observations".

    For an available company/period this deliberately does not claim a value
    exists — it only says the channel may hold one (e.g. Jinjiang 2023). The
    caller then queries semantic_observations.
    """
    rec = unavailable_for(entity_code, period)
    if rec is not None:
        return dict(rec, period=period)
    return {
        "status": "query_observations",
        "entity_code": str(entity_code),
        "entity_name": CODE_ZH.get(str(entity_code), ""),
        "period": period,
        "reason": ("该期可能由历史简报覆盖（锦江 2023 年 7、8 月与 1-11 月）；"
                   "无存储观测时按缺测呈现，不以其他期或同业值填充"),
        "evidence": _EVIDENCE,
    }


# ── parsing ────────────────────────────────────────────────────────────────
_TABLE_SPLIT_RE = re.compile(r"(?=表[一二三四五六七八九十]+)")
_TABLE_MARK_RE = re.compile(r"^表([一二三四五六七八九十]+)")
# row shapes seen in the real payloads:
#   "中端酒店 278.70 245.30 69.66 55.30 194.14 135.65 43.12"
#   "经济型酒店 184.05 ..."
#   "平 均 253.15 ..."            (the source prints a space inside 平均)
_ROW_RE = re.compile(
    r"(中端酒店|经济型酒店|平\s*均)\s+"
    r"((?:\d[\d,]*\.\d+\s+){6}-?\d[\d,]*\.\d+)"
)
_NUM_RE = re.compile(r"-?\d[\d,]*\.\d+")
_YTD_LABEL_RE = re.compile(r"(\d{1,2})\s*[-–—]\s*(\d{1,2})\s*月")
_MONTH_LABEL_RE = re.compile(r"(?<![\d\-–—])(\d{1,2})\s*月")
_UNIT_LABEL_RE = re.compile(r"（([^（）]{1,4}?)/\s*间）")
_DOC_YEAR_RE = re.compile(r"(20\d{2})\s*年")

_GRADE = {
    "中端酒店": ("midscale", "中端"),
    "经济型酒店": ("economy", "经济型"),
    "平均": ("overall", "整体平均"),
}
_GRADE_ZH = {key: zh for key, zh in _GRADE.values()}
_UNIT_LABEL_CURRENCY = {"元": "cny", "欧元": "eur"}


def _clean_num(s: str) -> str:
    """Keep the disclosed precision (e.g. '278.70'), only drop thousands commas."""
    return s.replace(",", "")


def _doc_year(title: str, content: str) -> int | None:
    for text in (title or "", content or ""):
        m = _DOC_YEAR_RE.search(text)
        if m:
            return int(m.group(1))
    return None


def _table_period(block: str) -> tuple[str, str, str] | None:
    """(caliber, date_month 'MM', original label) from the table header.

    YTD is checked first: ``1-11 月`` also contains a bare ``11 月``.
    """
    m = _YTD_LABEL_RE.search(block)
    if m:
        start, end = int(m.group(1)), int(m.group(2))
        if 1 <= start <= end <= 12:
            return "ytd", f"{end:02d}", m.group(0).strip()
        return None
    m = _MONTH_LABEL_RE.search(block)
    if m:
        month = int(m.group(1))
        if 1 <= month <= 12:
            return "monthly", f"{month:02d}", m.group(0).strip()
    return None


def _table_scope(block: str) -> tuple[str, str] | None:
    """('domestic'|'overseas', zh label) from the table's own header.

    Only the head of the block is inspected: the *next* section heading can sit
    at the tail of this block, so a whole-block search would mix scopes.
    """
    head = block[:300]
    if "中国境内" in head or "境内酒店分类" in head:
        return "domestic", "中国境内"
    if "境外" in head:
        return "overseas", "中国境外"
    return None


def parse_brief(content: str, title: str, notes: list | None = None) -> list[dict]:
    """Parse one 经营数据简报 body into per-(table, grade) records.

    Returns one dict per source table row::

        {"table": "表一", "scope": "domestic", "grade": "midscale",
         "caliber": "ytd", "date": "2023-11", "period_label": "1-11 月",
         "unit_label": "元", "adr": "278.70", "occ": "69.66",
         "revpar": "194.14"}

    Values are kept as the exact strings disclosed in the source (2 decimals,
    commas stripped). A table that cannot be resolved is NOT guessed: a note
    names the table and the header column that blocked parsing, and no record
    is produced for it.
    """
    notes = notes if notes is not None else []
    year = _doc_year(title, content)
    if year is None:
        notes.append(f"{SOURCE}: 原文不可解析——正文/标题无 20XX 年，无法定位"
                     "报告年份（卡在文档年份）")
        return []

    records: list[dict] = []
    for block in _TABLE_SPLIT_RE.split(content or ""):
        mark = _TABLE_MARK_RE.match(block)
        if not mark:
            continue
        table = f"表{mark.group(1)}"

        scope = _table_scope(block)
        if scope is None:
            notes.append(f"{SOURCE}: {table} 原文不可解析——表头无「中国境内/"
                         "境外」标识（卡在表头范围列）")
            continue
        scope_key, scope_zh = scope

        period = _table_period(block)
        if period is None:
            notes.append(f"{SOURCE}: {table}（{scope_zh}）原文不可解析——表头"
                         "无期次标签（卡在期间列，如「1-11 月」「7 月」）")
            continue
        caliber, month, period_label = period

        unit_m = _UNIT_LABEL_RE.search(block[:300])
        if unit_m is None:
            notes.append(f"{SOURCE}: {table}（{scope_zh}）原文不可解析——表头"
                         "单位栏非「X/间」（卡在单位列）")
            continue
        unit_label = unit_m.group(1)
        label_currency = _UNIT_LABEL_CURRENCY.get(unit_label)
        if label_currency is None:
            notes.append(f"{SOURCE}: {table}（{scope_zh}）原文不可解析——未知"
                         f"单位标签「{unit_label}/间」（卡在单位列）")
            continue
        expected_currency = "cny" if scope_key == "domestic" else "eur"
        if label_currency != expected_currency:
            notes.append(
                f"{SOURCE}: {table}（{scope_zh}）原文单位栏印作「{unit_label}/间」，"
                f"与{scope_zh}口径的 {expected_currency.upper()} 不一致；"
                "本条按境外欧元口径归属概念，数值未改，单位标签差异留痕"
                "（2023-12 简报同口径表头为「欧元/间」）"
            )

        # 标签比对必须归一化：正文里 "平 均" 带全角/半角空格，未归一化的集合差
        # 会把「已解析成功的行」误报成「原文不可解析」，那种证据比缺失更坏。
        found_labels = {re.sub(r"\s+", "", m)
                        for m in re.findall(r"(中端酒店|经济型酒店|平\s*均)", block)}
        matched_labels: set[str] = set()
        for row in _ROW_RE.finditer(block):
            raw_label = re.sub(r"\s+", "", row.group(1))
            grade_key, grade_zh = _GRADE[raw_label]
            numbers = _NUM_RE.findall(row.group(2))
            if len(numbers) != 7:
                notes.append(
                    f"{SOURCE}: {table}（{scope_zh}/{grade_zh}）原文不可解析——"
                    f"行内数字 {len(numbers)} 个，期望 7 列（平均房价 2 + 出租率 2"
                    " + RevPAR 2 + 同比 1）"
                )
                continue
            matched_labels.add(raw_label)
            records.append({
                "table": table,
                "scope": scope_key,
                "grade": grade_key,
                "caliber": caliber,
                "date": f"{year}-{month}",
                "period_label": period_label,
                "unit_label": unit_label,
                "adr": _clean_num(numbers[0]),     # 平均房价 2023 当期列
                "occ": _clean_num(numbers[2]),     # 平均出租率 2023 当期列
                "revpar": _clean_num(numbers[4]),  # RevPAR 2023 当期列
            })

        unmatched = found_labels - matched_labels
        if unmatched:
            names = "、".join(sorted(re.sub(r"\s+", "", u) for u in unmatched))
            notes.append(f"{SOURCE}: {table}（{scope_zh}）原文不可解析——{names} "
                         "行的列结构不匹配（卡在列数/行标签），该行不写值")
        if not found_labels:
            notes.append(f"{SOURCE}: {table}（{scope_zh}）未识别到任何分档行"
                         "（中端酒店/经济型酒店/平均），不猜数")

    return records


# ── DB-facing extraction ────────────────────────────────────────────────────
def _entity_ids(cur) -> dict:
    cur.execute(
        "SELECT code, id FROM entities WHERE entity_type='stock' AND code = ANY(%s)",
        (list(CODES),),
    )
    return {code: eid for code, eid in cur.fetchall()}


def extract(cur, notes) -> list[tuple]:
    """Read stored cninfo-operating payloads and emit A-share hotel observations.

    Only ``category='经营简报'`` payloads whose ``sec_code`` is one of the six
    registered A-share hotel issuers are considered. Row shape matches
    scripts/restaurant_extractors/_common.py insert_rows' 6-tuple.
    """
    items = unit_items(cur, SOURCE)
    if not items:
        notes.append(f"{SOURCE}: 中心库无 success payload——本批跳过，不硬抽")
        return []

    relevant = [
        p for p in items
        if p.get("category") == "经营简报"
        and str(p.get("sec_code") or "") in CODES
    ]
    if not relevant:
        notes.append(f"{SOURCE}: 库内 {len(items)} 条 success payload 中无酒店"
                     "「经营简报」载荷——先跑 2023 历史窗口落库")
        return []

    ids = _entity_ids(cur)
    missing = sorted(set(CODES) - set(ids))
    if missing:
        notes.append(f"{SOURCE}: ⚠️ 宇宙实体缺 {missing}——这些公司不写观测"
                     "（实体注册见 scripts/register_hotel_universe.py）")

    latest = dedup_latest(
        relevant,
        key=lambda p: (p.get("sec_code"), p.get("art_code")
                       or p.get("announcement_title")),
    )

    rows: list[tuple] = []
    seen_companies: set[str] = set()
    for (code, art), payload in sorted(latest.items()):
        eid = ids.get(code)
        if eid is None:
            continue
        seen_companies.add(code)
        title = str(payload.get("announcement_title") or "")
        content = str(payload.get("content") or "")
        records = parse_brief(content, title, notes)
        if not records:
            notes.append(f"{SOURCE}: {code} {art} 未解析出任何分档行——见上条"
                         "「原文不可解析」证据，不写值")
            continue

        for rec in records:
            caliber = rec["caliber"]
            granularity = _CALIBERS[caliber][4]
            for metric in ("adr", "occ", "revpar"):
                code_c = _concept_code(metric, rec["scope"], rec["grade"], caliber)
                rows.append((code_c, "stock", eid, rec["date"], granularity,
                             rec[metric]))

            scope_zh = "中国境内" if rec["scope"] == "domestic" else "中国境外"
            zh_grade = _GRADE_ZH[rec["grade"]]
            if caliber == "ytd":
                caliber_note = (f"原文期次「{rec['period_label']}」= 年初至今累计"
                                " cumulative-ytd（非单季、非单月）")
            else:
                caliber_note = f"原文期次「{rec['period_label']}」= 单月 month"
            suffix = rec["unit_label"] + "/间"
            notes.append(
                f"{SOURCE}: {code} {rec['table']} {scope_zh}/{zh_grade} "
                f"{rec['date']} {caliber_note}；ADR={rec['adr']}{suffix} "
                f"OCC={rec['occ']}% RevPAR={rec['revpar']}{suffix}"
            )

        notes.append(f"{SOURCE}: {code} {art} 解析 {len(records)} 个（表×分档）"
                     f"行 → {len(records) * 3} 条观测")

    no_payload = sorted(set(CODES) - seen_companies)
    if no_payload:
        notes.append(
            f"{SOURCE}: {no_payload} 无任何「经营简报」载荷——按 UNAVAILABLE "
            "显式不可得呈现（首旅/金陵/华天/君亭/西旅从未单独披露），不以空值"
            "或同业值填充"
        )
    if rows:
        notes.append(
            f"{SOURCE}: 本通道为 2 份历史简报回补（{len(rows)} 条观测），"
            "不足 24 期趋势门；按 spec 以显式不可得口径呈现，不做趋势序列，"
            "不插值不补期"
        )
    return rows


# ── CLI ─────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    import argparse
    import os

    ap = argparse.ArgumentParser(
        description="A-share hotel operating data (Jinjiang 2023 backfill). "
                    "Dry-run by default; nothing is written without --write.")
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    ap.add_argument("--write", action="store_true",
                    help="create concepts and insert observations (one txn)")
    args = ap.parse_args(argv)

    if not args.dsn:
        print("FATAL: 未设置 --dsn 或 FD_OPEN_DATA_DSN"
              "（source ~/.config/finddata/dashboards.env）")
        return 2

    import psycopg2

    con = psycopg2.connect(args.dsn)
    try:
        cur = con.cursor()
        notes: list = []
        rows = extract(cur, notes)
        for n in notes:
            print(f"NOTE {n}")
        print(f"rows={len(rows)}")

        code_by_id: dict = {}
        if rows:
            cur.execute(
                "SELECT id, code FROM entities WHERE id = ANY(%s)",
                (sorted({r[2] for r in rows}),),
            )
            code_by_id = {eid: code for eid, code in cur.fetchall()}
        for concept, _etype, eid, date, gran, value in rows:
            print(f"  {code_by_id.get(eid, eid):>7} {date} {gran:<15} "
                  f"{value:>8}  {concept}")

        if not args.write:
            con.rollback()
            print("DRY-RUN: 未写库（需要写库请加 --write）")
            return 0

        ids = ensure_unit_concepts(cur, SOURCE, CONCEPTS,
                                   {r[0] for r in rows})
        inserted = insert_rows(cur, SOURCE, ids, rows)
        con.commit()
        print(f"WROTE inserted={inserted} (additive; re-run inserts 0)")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
