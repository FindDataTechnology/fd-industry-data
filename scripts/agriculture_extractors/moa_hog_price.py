#!/usr/bin/env python3
"""Agricultural wave-1 extractor — moa-hog-price (产能季报 + 生猪周价).

Two very different cadences live in one unit; they are kept apart here exactly
as they are in the spider:

**A. Quarterly capacity** (``run_moa_hog_capacity``) — 3 series, date =
``period_end`` (quarter end), granularity ``quarter``:

    agriculture.cn.hog.breeding_sow_inventory.quarterly   能繁母猪存栏  万头
    agriculture.cn.hog.inventory.quarterly                生猪存栏      万头
    agriculture.cn.hog.breeding_sow_vs_target.quarterly   相当于保有量/调控目标 X%  %

The third one is NOT a separate source row: the page prints it inline in the
breeding-sow cell (「3602（相当于调控目标的 96.0%）」) and the spider exposes it as
``target_ratio_pct`` on the same record. It is the threshold layer's primary
criterion (NDRC 猪粮比价 is unobtainable — see the scouting ledger), so it gets
its own concept.

⚠ Caliber break, recorded here and in the curation doc: the wording changed
between years — 2025 年页面写「相当于正常保有量的 103.5%，产能正常，处于绿色区域」,
2026 年写「相当于调控目标的 96.0%」. The unit parses both into the same column, so
the series is continuous, but the denominator changed. A dashboard must say so
and must not compare 2025 and 2026 values as if they were the same ratio.

**B. Weekly hog/pork prices** (``run_moa_hog_price``) — the three concepts
ALREADY EXIST in the store (registered by restaurant wave-0: agri.hog.price.spot
/ agri.pork.price.market / agri.pork.price.wholesale, 15 rows each). They are
REUSED, not re-created: this extractor only appends older weeks under the same
``source_used``. date = the START day of ``date_range`` (「2026-10-26~2026-11-01」
→ 2026-10-26), granularity ``week``. The two monitoring faces (500 个县集贸点 vs
200 个批发市场) stay separate series — never merged.
"""
from __future__ import annotations

from ._common import ENTITY_COUNTRY_CN, fmt, gate_periods, live_items, num

SOURCE = "moa-hog-price"
_CAT = "农业"

CAPACITY_CONCEPTS = [
    ("agriculture.cn.hog.breeding_sow_inventory.quarterly",
     "能繁母猪存栏（季末）", "China breeding sow inventory, quarter-end",
     _CAT, "万头", "value", "quarterly", "country", SOURCE),
    ("agriculture.cn.hog.inventory.quarterly",
     "生猪存栏（季末）", "China hog inventory, quarter-end",
     _CAT, "万头", "value", "quarterly", "country", SOURCE),
    ("agriculture.cn.hog.breeding_sow_vs_target.quarterly",
     "能繁母猪存栏相当于保有量/调控目标（季末）",
     "China breeding sow inventory vs target stock ratio, quarter-end",
     _CAT, "%", "value", "quarterly", "country", SOURCE),
]

#: weekly reuse — concept codes come from restaurant wave-0, we do NOT redefine names
WEEKLY_REUSE = {
    "活猪": "agri.hog.price.spot",
    "猪肉集贸": "agri.pork.price.market",
    "猪肉批发": "agri.pork.price.wholesale",
}

CONCEPTS = CAPACITY_CONCEPTS


def _weekly_date(row: dict) -> str | None:
    rng = (row.get("date_range") or "").strip()
    if "~" in rng:
        start = rng.split("~", 1)[0].strip()
        return start or None
    return None


_QUARTER_MONTHS = {"03": 1, "06": 2, "09": 3, "12": 4}


def _quarter_gaps(periods: list[str]) -> list[str]:
    """季度序列里缺的季（只认 03/06/09/12 四个季末月）。

    缺口 ≠ 数据缺陷：可能是抓取时某季页面失败（spider 的 pages_failed 只写日志），
    也可能是站点当期未发布。这里只负责把它说出来——绝不用前后值补齐。
    """
    qs = []
    for p in sorted(set(periods)):
        y, m = p[:4], p[5:7]
        if m not in _QUARTER_MONTHS:
            continue
        qs.append(int(y) * 4 + _QUARTER_MONTHS[m] - 1)
    if not qs:
        return []
    present = set(qs)
    holes = []
    for q in range(min(qs), max(qs) + 1):
        if q in present:
            continue
        y, k = divmod(q, 4)
        holes.append(f"{y}Q{k + 1}")
    return holes


def extract(cur, notes) -> list:
    rows: list = []

    # ── A. quarterly capacity ────────────────────────────────────────────
    cap = live_items(SOURCE, "run_moa_hog_capacity", notes, limit=120)
    cap_map = {
        "breeding_sow_inventory": "agriculture.cn.hog.breeding_sow_inventory.quarterly",
        "hog_inventory": "agriculture.cn.hog.inventory.quarterly",
    }
    cap_rows = 0
    ratio_rows = 0
    for r in cap:
        code = cap_map.get(r.get("item"))
        v = num(r.get("value"))
        if code and v is not None and r.get("period_end"):
            rows.append((code, "country", ENTITY_COUNTRY_CN,
                         str(r["period_end"]), "quarter", fmt(v)))
            cap_rows += 1
        ratio = num(r.get("target_ratio_pct"))
        if ratio is not None and r.get("period_end"):
            rows.append(("agriculture.cn.hog.breeding_sow_vs_target.quarterly",
                         "country", ENTITY_COUNTRY_CN,
                         str(r["period_end"]), "quarter", fmt(ratio)))
            ratio_rows += 1
    notes.append(
        f"{SOURCE}: capacity {len(cap)} record(s) -> {cap_rows} inventory row(s)"
        f" + {ratio_rows} target-ratio row(s)"
    )

    # ── B. weekly prices (reuse existing concepts) ───────────────────────
    weekly = live_items(SOURCE, "run_moa_hog_price", notes, limit=200, max_pages=6)
    wk_rows = 0
    wk_bad_date = 0
    for r in weekly:
        code = WEEKLY_REUSE.get(r.get("item"))
        v = num(r.get("price"))
        d = _weekly_date(r)
        if not code or v is None:
            continue
        if d is None:
            wk_bad_date += 1
            continue
        rows.append((code, "country", ENTITY_COUNTRY_CN, d, "week", fmt(v)))
        wk_rows += 1
    notes.append(
        f"{SOURCE}: weekly {len(weekly)} payload row(s) -> {wk_rows} observation row(s)"
        f" (skip {wk_bad_date} without a parsable date_range); concepts reused from"
        f" restaurant wave-0, three monitoring faces kept separate"
    )

    # Gate: capacity is quarterly — 20 seasons is the FULL published history
    # (2021Q4 → 2026Q3), far below the 24-period monthly gate by construction.
    # It therefore falls in the 12-23 band and is registered as short_window(20).
    cap_kept = [r for r in rows if r[4] == "quarter"]
    wk_kept = [r for r in rows if r[4] == "week"]
    kept_c, dropped_c = gate_periods(cap_kept, 12)  # 12-23 档：short_window 放行
    if dropped_c:
        codes = sorted({r[0] for r in dropped_c})
        notes.append(
            f"{SOURCE}: capacity series below 12 -> DROPPED: {codes}"
            f" ({len(dropped_c)} row(s))"
        )
    for code in sorted({r[0] for r in kept_c}):
        series = [r for r in kept_c if r[0] == code]
        n = len(series)
        holes = _quarter_gaps([r[3] for r in series])
        notes.append(
            f"{SOURCE}: {code} -> {n} quarter(s) -> short_window({n})"
            + (f"; GAP {', '.join(holes)}（源站该季未印或抓取失败，不补齐）" if holes else "")
        )
    notes.append(
        f"{SOURCE}: weekly series exempt from new-rows gate (existing verified"
        f" concepts, 15 stored + {len(wk_kept)} appended); SHORT_WINDOW not needed"
    )
    return kept_c + wk_kept
