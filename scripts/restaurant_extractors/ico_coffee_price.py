#!/usr/bin/env python3
"""Restaurant-vertical wave-1 extractor — ico-coffee-price (ICO indicator prices).

Reads the central crawl DB payloads of successful ``ico-coffee-price`` runs
(one row per trading day, WIDE: five indicator columns) and melts them into
five long-form observation series:

    icip              -> price.coffee.icip.daily                 (I-CIP composite)
    colombian_milds   -> price.coffee.colombian_milds.daily
    other_milds       -> price.coffee.other_milds.daily
    brazilian_naturals-> price.coffee.brazilian_naturals.daily
    robustas          -> price.coffee.robustas.daily

Caliber:

* unit = US cents/lb (upstream table header; payload carries no unit field);
* entity = WORLD(300932), entity_type 'country' — ICO indicator prices are
  world reference prices, not a single country's series;
* granularity 'day', date = the payload's ``day`` (YYYY-MM-DD); weekend/holiday
  rows never exist upstream (the PDF表 leaves them blank), so no gaps are
  fabricated;
* window is whatever the payloads really contain (current month + the online
  archive months the unit could reach, ~2025-09 onward); the extractor never
  extends a series past its last stored day, and ``Limit`` upstream-匿名 window
  caveat (deeper history is behind ICO's subscription wall) stays visible in
  the notes.

Discipline (scripts/restaurant_extractors/_common.py): dedup by (series, day)
keeping the latest scraped_at (the current month is revised intra-day
upstream); trend gate >= 24 periods; below-gate series dropped with a SKIP note.
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

SOURCE = "ico-coffee-price"

# (code, name_zh, name_en, category, unit, measure, frequency, entity_type, source)
CONCEPTS = [
    ("price.coffee.icip.daily",
     "ICO 咖啡综合指示价（I-CIP）",
     "ICO composite indicator price (I-CIP)",
     "价格指数", "us_cents_lb", "price", "daily", "country", SOURCE),
    ("price.coffee.colombian_milds.daily",
     "ICO 哥伦比亚温和咖啡指示价",
     "ICO Colombian Milds indicator price",
     "价格指数", "us_cents_lb", "price", "daily", "country", SOURCE),
    ("price.coffee.other_milds.daily",
     "ICO 其他温和咖啡指示价",
     "ICO Other Milds indicator price",
     "价格指数", "us_cents_lb", "price", "daily", "country", SOURCE),
    ("price.coffee.brazilian_naturals.daily",
     "ICO 巴西天然咖啡指示价",
     "ICO Brazilian Naturals indicator price",
     "价格指数", "us_cents_lb", "price", "daily", "country", SOURCE),
    ("price.coffee.robustas.daily",
     "ICO 罗布斯塔咖啡指示价",
     "ICO Robustas indicator price",
     "价格指数", "us_cents_lb", "price", "daily", "country", SOURCE),
]

# payload column -> concept code (payload is one wide row per trading day)
COLUMN_CONCEPT = {
    "icip": "price.coffee.icip.daily",
    "colombian_milds": "price.coffee.colombian_milds.daily",
    "other_milds": "price.coffee.other_milds.daily",
    "brazilian_naturals": "price.coffee.brazilian_naturals.daily",
    "robustas": "price.coffee.robustas.daily",
}


def extract(cur, notes) -> list[tuple]:
    """Emit (code, entity_type, entity_id, date, granularity, value_str) rows."""
    items = unit_items(cur, SOURCE)
    seen = dedup_latest(items, lambda p: p.get("day"))

    out: list[tuple] = []
    windows: dict[str, list[str]] = {}
    for day, payload in sorted(seen.items()):
        if not day:
            continue
        for column, concept in COLUMN_CONCEPT.items():
            value = num(payload.get(column))
            if value is None or value <= 0:
                continue  # 缺测/异常格跳过，绝不补造
            out.append((concept, "country", ENTITY_WORLD, day, "day", fmt(value)))
            windows.setdefault(concept, []).append(day)

    kept, dropped = gate_periods(out)
    for concept, days in sorted(windows.items()):
        kept_n = sum(1 for r in kept if r[0] == concept)
        notes.append(
            f"{SOURCE}: {concept} {kept_n} 个交易日（{min(days)}..{max(days)}；"
            "unit=us_cents_lb，ICO 世界指标价 → entity WORLD(300932)）"
        )
    dropped_by_concept: dict[str, int] = {}
    for (code, _etype, _eid, _d, _g, _v) in dropped:
        dropped_by_concept[code] = dropped_by_concept.get(code, 0) + 1
    for code, n in sorted(dropped_by_concept.items()):
        notes.append(f"SKIP {SOURCE}: {code} 仅 {n} 期（<24 期门槛），整列剔除不入库")
    notes.append(
        f"{SOURCE}: 窗口上限=匿名开放窗（ICO 载荷 entitlement openFrom=2026-01-01，"
        "归档仅回溯到 2025-09）；1989 年起的深历史在订阅墙内，本抽取器不延展、不伪造"
    )
    return kept
