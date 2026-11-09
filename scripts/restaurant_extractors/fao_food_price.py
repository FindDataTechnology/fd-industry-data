#!/usr/bin/env python3
"""Restaurant-vertical wave-0 extractor — fao-food-price unit (price-chain supply side).

Reads the central crawl DB payloads of successful ``fao-food-price`` runs and
emits semantic observation rows for the 8 ALREADY-REGISTERED FAO food price
index concepts (batch2, 2026-11-06 — this module creates no concepts; the codes
resolve to the existing ids via ``ensure_unit_concepts``):

    monthly CSV (scope=monthly/all)          -> price.food.fao.index
                                                price.food.fao.cereals
                                                price.food.fao.dairy
                                                price.food.fao.meat
                                                price.food.fao.oils
                                                price.food.fao.sugar
    annual XLSX (scope=annual/all)           -> price.food.fao.annual.nominal
                                                price.food.fao.annual.real

Scope discipline:

* Payload long-table rows carry ``index_name`` (official English column name,
  annual series suffixed ``(nominal)``/``(real)``) and the period key in
  ``month`` — ``YYYY-MM`` for the monthly CSV rows, bare ``YYYY`` for the
  annual XLSX rows (spider reuses the column, see spiders/fao-food-price).
* Index names outside the two maps are skipped (unknown future columns are
  never invented into concepts).
* Every value written is a real stored payload value (2014-2016=100 base);
  blank cells are skipped by the spider already, and non-numeric values are
  skipped here — no zero-fill.
* Entity: WORLD (country id 300932) — the FAO index is a global aggregate.
* Backfill note: the first crawl runs only landed the runner's default
  ``limit=100`` slice; the 2026-11-07 full-history re-run (limit=20000,
  scope=all) landed 6x442 monthly rows (1990-01..2026-10) + 2x66 annual rows
  (1961..2026). This extractor reads ALL successful runs, so the semantic
  layer gets the full history regardless of which run carried which slice.

Discipline (scripts/restaurant_extractors/_common.py): additive only
(INSERT ... ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING on the natural key
concept/entity/date/granularity/source_used — existing rows are never
rewritten), dedup by (series, period) keeping the latest scraped_at; trend
gate >= 24 periods per series; below-gate series dropped with an explicit SKIP
note.
"""
from __future__ import annotations

from ._common import (
    ENTITY_WORLD,
    dedup_latest,
    fmt,
    gate_periods,
    num,
    unit_items,
)

SOURCE = "fao-food-price"

# (code, name_zh, name_en, category, unit, measure, frequency, entity_type, source)
# 逐字段复用 scripts/extract_batch2.py CONSTRUCT_CONCEPTS 的既有 8 概念——
# ensure_unit_concepts 按 code 解析到现有 id，此清单不会新建任何概念行。
CONCEPTS = [
    ("price.food.fao.index", "FAO 食品价格指数（月度）",
     "FAO Food Price Index, monthly", "价格指数", "index", "value",
     "monthly", "country", SOURCE),
    ("price.food.fao.cereals", "FAO 谷物价格指数（月度）",
     "FAO Cereals price index, monthly", "价格指数", "index", "value",
     "monthly", "country", SOURCE),
    ("price.food.fao.dairy", "FAO 乳品价格指数（月度）",
     "FAO Dairy price index, monthly", "价格指数", "index", "value",
     "monthly", "country", SOURCE),
    ("price.food.fao.meat", "FAO 肉类价格指数（月度）",
     "FAO Meat price index, monthly", "价格指数", "index", "value",
     "monthly", "country", SOURCE),
    ("price.food.fao.oils", "FAO 植物油价格指数（月度）",
     "FAO Vegetable oils price index, monthly", "价格指数", "index", "value",
     "monthly", "country", SOURCE),
    ("price.food.fao.sugar", "FAO 食糖价格指数（月度）",
     "FAO Sugar price index, monthly", "价格指数", "index", "value",
     "monthly", "country", SOURCE),
    ("price.food.fao.annual.nominal", "FAO 食品价格指数（年度，名义）",
     "FAO Food Price Index, annual nominal", "价格指数", "index", "value",
     "yearly", "country", SOURCE),
    ("price.food.fao.annual.real", "FAO 食品价格指数（年度，实际）",
     "FAO Food Price Index, annual real", "价格指数", "index", "value",
     "yearly", "country", SOURCE),
]

# 官方英文列名 -> 概念码（照搬 scripts/extract_batch2.py FAO_MONTHLY/FAO_ANNUAL）
FAO_MONTHLY = {
    "Food Price Index": "price.food.fao.index",
    "Cereals": "price.food.fao.cereals",
    "Dairy": "price.food.fao.dairy",
    "Meat": "price.food.fao.meat",
    "Oils": "price.food.fao.oils",
    "Sugar": "price.food.fao.sugar",
}
FAO_ANNUAL = {
    "Food Price Index (nominal)": "price.food.fao.annual.nominal",
    "Food Price Index (real)": "price.food.fao.annual.real",
}


def extract(cur, notes) -> list[tuple]:
    """Emit (code, entity_type, entity_id, date, granularity, value_str) rows."""
    items = unit_items(cur, SOURCE)
    # (series, period) 去重：同一期多次抓取保留最新 scraped_at（批次规则）
    seen = dedup_latest(items, lambda p: (p.get("index_name"), p.get("month")))

    out: list[tuple] = []
    windows: dict[str, list[str]] = {}
    skipped_names: dict[str, int] = {}
    for (name, period), p in sorted(seen.items(), key=lambda kv: str(kv[0])):
        if name in FAO_MONTHLY:
            code, gran = FAO_MONTHLY[name], "month"
        elif name in FAO_ANNUAL:
            code, gran = FAO_ANNUAL[name], "year"
        else:
            skipped_names[str(name)] = skipped_names.get(str(name), 0) + 1
            continue
        v = num(p.get("value"))
        if v is None or not period:
            continue
        out.append((code, "country", ENTITY_WORLD, str(period), gran, fmt(v)))
        windows.setdefault(code, []).append(str(period))

    # 趋势门：>= 24 期；低于门槛整列剔除并留 SKIP 注记
    kept, dropped = gate_periods(out)
    for code, periods in sorted(windows.items()):
        kept_n = sum(1 for r in kept if r[0] == code)
        gran = "month" if code in FAO_MONTHLY.values() else "year"
        notes.append(
            f"{SOURCE}: {code} {kept_n} 期"
            f"（{min(periods)}..{max(periods)}；country WORLD(300932)，{gran}，"
            "2014-2016=100 → index）"
        )
    dropped_by_concept: dict[str, int] = {}
    for (code, _etype, _eid, _d, _g, _v) in dropped:
        dropped_by_concept[code] = dropped_by_concept.get(code, 0) + 1
    for code, n in sorted(dropped_by_concept.items()):
        notes.append(f"SKIP {SOURCE}: {code} 仅 {n} 期（<24 期门槛），整列剔除不入库")
    if skipped_names:
        notes.append("SKIP " + SOURCE + ": 未映射 index_name 未入库 "
                     + "; ".join(f"{k}×{n}" for k, n in sorted(skipped_names.items())))
    return kept
