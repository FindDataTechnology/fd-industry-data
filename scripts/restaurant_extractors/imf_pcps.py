#!/usr/bin/env python3
"""Restaurant-vertical wave-0 extractor — imf-pcps unit (IMF PCPS monthly).

Reads the central crawl DB payloads of successful ``imf-pcps`` runs and emits
semantic observation rows for the two restaurant-wave series added 2026-11-07:

    G001.PPOIL.INDEX.M -> price.palm_oil.imf.monthly  (World palm oil price index)
    G001.PSUGA.INDEX.M -> price.sugar.imf.monthly     (World raw sugar price index)

Series scoping (deliberate):

* ONLY the two new keys are extracted here. The incumbent seed
  ``G001.PCOIL.INDEX.M`` is already extracted by scripts/extract_batch1.py
  (``ex_imf`` -> concept ``price.oil.imf.index``, entity WORLD(300932), first
  loaded 2026-11-03); re-extracting it under this restaurant module would
  double-count the same stored payloads under a second concept code, so PCOIL
  rows are filtered out and a NOTE is emitted instead.

Unit adjudication (2026-11-07, DSD dataflow IMF.RES/PCPS latest?references=all
+ observed magnitudes):

* DATA_TRANSFORMATION ``INDEX`` (CL_PCPS_DATA_TRANSFORMATION 4.0.0) = "Index";
* the 12 monthly obs of calendar 2016 average to exactly 100.0 for both
  PPOIL and PSUGA (base year 2016=100);
* PSUGA DSD description itself reads "Sugar Index, 2016 = 100"; PPOIL's
  underlying quote basis is US$/metric-ton (Malaysia futures) but the INDEX
  transformation rescales it — observed range 49.5 (1992-M01) .. 177.1
  (2026-M09) rules out a US$/mt reading.
  => unit = ``index_2016_100`` for both concepts.

Discipline (scripts/restaurant_extractors/_common.py):

* additive only, dedup by (series_key, TIME_PERIOD) keeping the payload with
  the latest scraped_at; months normalized 'YYYY-Mmm' -> 'YYYY-MM';
* entity: country G001 (World) -> ENTITY_WORLD(300932), entity_type 'country',
  granularity 'month';
* trend gate >= 24 periods per concept; below-gate series are dropped with an
  explicit SKIP note (never silently).
"""
from __future__ import annotations

import re

from ._common import (
    ENTITY_WORLD,
    dedup_latest,
    fmt,
    gate_periods,
    num,
    unit_items,
)

SOURCE = "imf-pcps"

# (code, name_zh, name_en, category, unit, measure, frequency, entity_type, source)
CONCEPTS = [
    ("price.palm_oil.imf.monthly",
     "IMF棕榈油价格",
     "Palm oil price (IMF PCPS)",
     "价格指数", "index_2016_100", "value", "monthly", "country", SOURCE),
    ("price.sugar.imf.monthly",
     "IMF原糖价格",
     "Sugar (raw) price (IMF PCPS)",
     "价格指数", "index_2016_100", "value", "monthly", "country", SOURCE),
]

# restaurant-wave series only — PCOIL belongs to extract_batch1 (price.oil.imf.index)
SERIES_CONCEPT = {
    "G001.PPOIL.INDEX.M": "price.palm_oil.imf.monthly",
    "G001.PSUGA.INDEX.M": "price.sugar.imf.monthly",
}

_MONTH = re.compile(r"(\d{4})-M(\d{2})")


def month_of(period: str | None) -> str | None:
    """'2020-M01' -> '2020-01'; None otherwise (never fabricated)."""
    m = _MONTH.fullmatch(period or "")
    return f"{m.group(1)}-{m.group(2)}" if m else None


def extract(cur, notes) -> list[tuple]:
    """Emit (code, entity_type, entity_id, date, granularity, value_str) rows."""
    items = unit_items(cur, SOURCE)
    seen = dedup_latest(items, lambda p: (p.get("series_key"), p.get("period")))

    out: list[tuple] = []
    windows: dict[str, list[str]] = {}
    for (series, per), payload in sorted(seen.items()):
        concept = SERIES_CONCEPT.get(series)
        if concept is None:
            continue  # PCOIL and any future non-restaurant keys stay out of scope
        month = month_of(per)
        value = num(payload.get("value"))
        if not month or value is None:
            continue
        out.append((concept, "country", ENTITY_WORLD, month, "month", fmt(value)))
        windows.setdefault(concept, []).append(month)

    kept, dropped = gate_periods(out)  # >= 24 periods per (concept, entity)
    for concept, months in sorted(windows.items()):
        kept_n = sum(1 for r in kept if r[0] == concept)
        notes.append(
            f"{SOURCE}: {concept} {kept_n} 期"
            f"（{min(months)}..{max(months)}；unit=index_2016_100，"
            "World=G001 → entities WORLD(300932)）"
        )
    dropped_by_concept: dict[str, int] = {}
    for (code, _etype, _eid, _d, _g, _v) in dropped:
        dropped_by_concept[code] = dropped_by_concept.get(code, 0) + 1
    for code, n in sorted(dropped_by_concept.items()):
        notes.append(f"SKIP {SOURCE}: {code} 仅 {n} 期（<24 期门槛），整列剔除不入库")
    notes.append(
        f"{SOURCE}: 现役种子 G001.PCOIL.INDEX.M 不在本抽取器范围——"
        "scripts/extract_batch1.py ex_imf 已抽为 price.oil.imf.index"
        "（entity WORLD(300932)，2026-11-03 起在库），此处跳过避免同源双计"
    )
    return kept
