#!/usr/bin/env python3
"""Restaurant-vertical wave-0 extractor — dbnomics-api unit (BLS CPI mirror).

Reads the central crawl DB payloads of successful ``dbnomics-api`` runs and
emits semantic observation rows for the two BLS CPI series added 2026-11-07
(restaurant cost-side CPI comparator pair):

    BLS/cu/CUSR0000SEFV  -> price.cpi.us.food_away_from_home  (外食，经外就餐)
    BLS/cu/CUSR0000SAF11 -> price.cpi.us.food_at_home         (在家就餐)

Why the DBnomics mirror: api.bls.gov / download.bls.gov are Akamai-403 to both
this machine and the cluster egress (2026-11-07 probe), so the DBnomics mirror
of the BLS ``cu`` dataset is the only reachable keyless channel.

**Mirror caveat (must stay visible downstream)**: the BLS mirror is frozen at
2025-01 — later months are 不可得 through this channel, not "zero" and not
"missing to be filled". The extractor therefore writes exactly the periods the
payload carries (1952-01..2025-01 for SAF11, 1953-01..2025-01 for SEFV) and
never extends the series.

Series scoping: the incumbent ``ECB/EXR/D.CNY.EUR.SP00.A`` payloads are already
extracted as ``fx.rate.ecb.daily`` (scripts/extract_batch1.py, 507 rows in the
store) — they are filtered out here with a NOTE instead of being double-counted
under a second concept code.

Discipline (scripts/restaurant_extractors/_common.py): additive only, dedup by
(series_code, period) keeping the latest scraped_at; entity = country US(2);
granularity 'month'; trend gate >= 24 periods.
"""
from __future__ import annotations

from ._common import (
    COUNTRY_US,
    dedup_latest,
    fmt,
    gate_periods,
    num,
    unit_items,
)

SOURCE = "dbnomics-api"

# (code, name_zh, name_en, category, unit, measure, frequency, entity_type, source)
CONCEPTS = [
    ("price.cpi.us.food_away_from_home",
     "美国CPI外食",
     "US CPI-U food away from home",
     "价格指数", "index_1982_84_100", "value", "monthly", "country", SOURCE),
    ("price.cpi.us.food_at_home",
     "美国CPI在家就餐",
     "US CPI-U food at home",
     "价格指数", "index_1982_84_100", "value", "monthly", "country", SOURCE),
]

# (provider, dataset, series_code) -> concept code; anything else stays out of scope
SERIES_CONCEPT = {
    ("BLS", "cu", "CUSR0000SEFV"): "price.cpi.us.food_away_from_home",
    ("BLS", "cu", "CUSR0000SAF11"): "price.cpi.us.food_at_home",
}

MIRROR_FROZEN_THROUGH = "2025-01"   # DBnomics 的 BLS 镜像观测到此为止（见模块 docstring）


def extract(cur, notes) -> list[tuple]:
    """Emit (code, entity_type, entity_id, date, granularity, value_str) rows."""
    items = unit_items(cur, SOURCE)
    seen = dedup_latest(
        items,
        lambda p: (p.get("provider"), p.get("dataset"),
                   p.get("series_code"), p.get("period")),
    )

    out: list[tuple] = []
    windows: dict[str, list[str]] = {}
    unknown: dict[tuple, int] = {}
    for (provider, dataset, series, period), payload in sorted(seen.items()):
        concept = SERIES_CONCEPT.get((provider, dataset, series))
        if concept is None:
            unknown[(provider, dataset, series)] = unknown.get(
                (provider, dataset, series), 0) + 1
            continue
        month = (period or "")[:7]
        value = num(payload.get("value"))
        if len(month) != 7 or value is None:
            continue
        out.append((concept, "country", COUNTRY_US, month, "month", fmt(value)))
        windows.setdefault(concept, []).append(month)

    kept, dropped = gate_periods(out)
    for concept, months in sorted(windows.items()):
        kept_n = sum(1 for r in kept if r[0] == concept)
        notes.append(
            f"{SOURCE}: {concept} {kept_n} 期（{min(months)}..{max(months)}；"
            "unit=index_1982_84_100，country US(2)；"
            f"BLS 镜像冻结于 {MIRROR_FROZEN_THROUGH}，其后月份按不可得、不补造）"
        )
    dropped_by_concept: dict[str, int] = {}
    for (code, _etype, _eid, _d, _g, _v) in dropped:
        dropped_by_concept[code] = dropped_by_concept.get(code, 0) + 1
    for code, n in sorted(dropped_by_concept.items()):
        notes.append(f"SKIP {SOURCE}: {code} 仅 {n} 期（<24 期门槛），整列剔除不入库")
    for (provider, dataset, series), n in sorted(unknown.items()):
        notes.append(
            f"{SOURCE}: {provider}/{dataset}/{series} {n} 期不在餐饮波0范围"
            "（ECB/EXR 汇率已由 scripts/extract_batch1.py 抽为 fx.rate.ecb.daily，跳过避免双计）"
        )
    return kept
