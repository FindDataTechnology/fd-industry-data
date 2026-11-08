#!/usr/bin/env python3
"""Agricultural wave-1 extractor — moa-fishery (水产品产量).

Two grains in one unit, never merged:

* ``cumulative_month`` — output accumulated through month N of the year
  (「1—9 月水产品产量」). date = the endpoint month ``YYYY-MM``, granularity
  ``month``. Real coverage is patchy: 18 months across 2024-07 … 2026-09
  (2025-03 与 2025-06 无发布) → 12-23 gate, registered as short_window.
* ``year`` — the annual 《渔业经济统计公报》 figures. Only 5 years
  (2020 / 2021 / 2023 / 2024 / 2025 — 缺 2022 与 2026) → far below the 24-period
  gate → EXCLUDED with an explicit SKIP note (shown on the dashboard as a
  single-period snapshot, never as a trend line).

Nine source items exist; five are registered as monthly series. 「养殖 / 捕捞 /
海水产品 / 淡水产品」 (annual-only aggregates) are not registered this wave.
"""
from __future__ import annotations

from ._common import ENTITY_COUNTRY_CN, fmt, gate_periods, live_items, num

SOURCE = "moa-fishery"
_CAT = "农业"

#: source item -> (concept suffix, name_zh, name_en)
MONTHLY = {
    "总产量": ("output", "水产品总产量", "total aquatic product output"),
    "海水养殖": ("mariculture", "海水养殖产量", "mariculture output"),
    "海洋捕捞": ("capture", "海洋捕捞产量", "marine capture output"),
    "淡水养殖": ("freshwater_culture", "淡水养殖产量", "freshwater culture output"),
    "淡水捕捞": ("freshwater_capture", "淡水捕捞产量", "freshwater capture output"),
}

CONCEPTS = [
    (f"agriculture.cn.fishery.{suffix}.cumulative_month",
     f"{zh}（年内累计）", f"China {en}, cumulative within year",
     _CAT, "万吨", "value", "monthly", "country", SOURCE)
    for suffix, zh, en in MONTHLY.values()
]

#: 12-23 期 → short_window 声明（真实 18 期）
SHORT_WINDOW_PERIODS = 18


def extract(cur, notes) -> list:
    raw = live_items(SOURCE, "run_moa_fishery", notes, limit=2000)

    rows: list = []
    annual_skipped = 0
    bad = 0
    for r in raw:
        gran = r.get("granularity")
        if gran == "year":
            annual_skipped += 1
            continue
        if gran != "cumulative_month":
            bad += 1
            continue
        spec = MONTHLY.get(r.get("item"))
        if spec is None:
            continue
        v = num(r.get("value"))
        if v is None:
            bad += 1
            continue
        rows.append((f"agriculture.cn.fishery.{spec[0]}.cumulative_month",
                     "country", ENTITY_COUNTRY_CN, str(r["period"]), "month", fmt(v)))

    notes.append(
        f"{SOURCE}: {len(raw)} payload row(s) -> {len(rows)} monthly row(s);"
        f" SKIP {annual_skipped} annual row(s) (5 years < 24 gate, shown as"
        f" snapshot only), {bad} unparsable"
    )
    kept, dropped = gate_periods(rows, 12)  # 12-23 档：short_window 放行
    if dropped:
        codes = sorted({r[0] for r in dropped})
        notes.append(
            f"{SOURCE}: DROPPED below 12: {codes} ({len(dropped)} rows) — not registered"
        )
    periods = sorted({r[3] for r in kept})
    notes.append(
        f"{SOURCE}: kept {len(kept)} row(s) over {len(periods)} month(s)"
        f" ({periods[0] if periods else '-'} … {periods[-1] if periods else '-'})"
        f" -> declared short_window({SHORT_WINDOW_PERIODS})"
    )
    return kept
