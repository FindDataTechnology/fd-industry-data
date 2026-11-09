#!/usr/bin/env python3
"""Restaurant-vertical wave-1 extractor — fred-cpi (US CPI 对照对续源).

The CPI scissors pair (在外就餐 vs 在家就餐) was already registered and gated:

    price.cpi.us.food_away_from_home   US CPI Food Away From Home (SA)
    price.cpi.us.food_at_home          US CPI Food At Home (SA)

Their only source so far was ``dbnomics-api`` (BLS mirror), which **froze at
2025-01** — api.bls.gov / download.bls.gov are Akamai-403 from every egress we
have. ``fred-cpi`` refreshes the SAME two concepts through FRED's CSV endpoint
(CUSR0000SEFV / CUSR0000SAF11, the very BLS series ids dbnomics carries), so:

* **no new concepts are created here** — ``CONCEPTS`` is empty by design and the
  harness resolves the existing codes; a duplicate concept would split the pair;
* observations are additive per the natural key ``(concept_id, entity_type,
  entity_id, date, granularity, source_used)``: the fred-cpi rows live beside the
  dbnomics rows under their own ``source_used`` — never overwritten, never merged
  (``industry-observation-supply``: 一条观测不得被其他来源合并或覆盖);
* overlap months (1952-01..2025-01) are carried by BOTH sources; that is the
  cross-source check surface, not double counting. Boards must read one source
  per series (see the curation doc §1.2 caveat).

Caliber:

* unit ``index_1982_84_100``, SA (the leading ``S`` in CUSR…); NSA ``CUUR…`` is a
  different caliber and is never mixed in;
* date normalised to ``'YYYY-MM'`` (FRED publishes the first day of the month);
* **missing months are absent, never zero**: FRED carries ``2025-10-01,`` with an
  empty value (BLS did not publish that month) — the spider drops it, so the
  series has a genuine hole at 2025-10. Nothing is interpolated;
* two concepts from one source = the declared 对照对 (same domain, same caliber,
  read together for the scissors spread).
"""
from __future__ import annotations

from ._common import COUNTRY_US, dedup_latest, fmt, gate_periods, num, unit_items

SOURCE = "fred-cpi"

#: No new concepts: both codes already exist (registered by the wave-0 naming
#: gate, source dbnomics-api). Declaring them here again would risk a second
#: concept row with a different (code, measure, unit, frequency) identity.
CONCEPTS: list = []

CODE_BY_SERIES = {
    "CUSR0000SEFV": "price.cpi.us.food_away_from_home",
    "CUSR0000SAF11": "price.cpi.us.food_at_home",
}


def extract(cur, notes) -> list[tuple]:
    """Emit (code, 'country', 2, 'YYYY-MM', 'month', value) for both CPI legs."""
    items = unit_items(cur, SOURCE)
    if not items:
        notes.append(f"{SOURCE}: 中心库无 success payload（单元尚未跑通或库为空）"
                     "——本批跳过，不硬抽")
        return []

    seen = dedup_latest(items, lambda p: (p.get("series_id"), p.get("date")))
    rows: list[tuple] = []
    unknown = set()
    per_series: dict[str, list[str]] = {}

    for (sid, d), p in seen.items():
        code = CODE_BY_SERIES.get(sid)
        if code is None:
            unknown.add(sid)
            continue
        if not d or len(str(d)) < 7:
            continue
        month = str(d)[:7]
        value = num(p.get("value"))
        if value is None:
            continue
        rows.append((code, "country", COUNTRY_US, month, "month", fmt(value)))
        per_series.setdefault(sid, []).append(month)

    if unknown:
        notes.append(f"{SOURCE}: ⚠️ 未映射的 series_id {sorted(unknown)}"
                     "——payload 有新序列但未定概念码，需人工裁定（不静默丢）")

    for sid, months in sorted(per_series.items()):
        months.sort()
        notes.append(f"{SOURCE}: {sid} → {CODE_BY_SERIES[sid]} "
                     f"{months[0]}..{months[-1]}，{len(months)} 期")

    kept, dropped = gate_periods(rows, min_periods=24)
    if dropped:
        notes.append(f"{SOURCE}: 趋势门剔除 {len(dropped)} 行（<24 期）")
    notes.append(
        f"{SOURCE}: 与既有 dbnomics-api 观测**并存**（同概念不同 source_used）——"
        "重叠月 1952-01..2025-01 是两个来源各自的真实值，不做合并/去重；"
        "板面读一条序列时须只取单一 source_used，2025-10 为上游未发布真缺期"
    )
    return kept
