#!/usr/bin/env python3
"""dce — restaurant-vertical wave-0 extractor (semantic_observations feed).

Reads the central crawl payloads written by the dce unit's Sina daily-kline
channel (spiders/dce/spider.py: run_dce), dedups them by (symbol, date)
keeping the latest scraped_at, and emits one daily settlement-price row per
symbol:

    M0 (豆粕主力连续) -> concept futures.dce.meal.settle.daily, entity DCE.M0
    C0 (玉米主力连续) -> concept futures.dce.corn.settle.daily, entity DCE.C0

Discipline (scripts/restaurant_extractors/_common.py contract):

* additive only — INSERT ... ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING;
  a second run over the same payloads inserts zero rows;
* never interpolates or zero-fills: payload rows with settle == 0 (Sina's
  "not relayed" sentinel, ~1589 scattered days for M0 up to 2024-09-25,
  measured 2026-11-07) are SKIPPED, never written as a 0 price;
* entities are NOT created here — DCE.M0 / DCE.C0 (entity_type 'contract')
  are registered by the main agent before the formal run. When an entity
  code is missing at extract time its rows are still produced (entity_id
  None) with a note, and the writer drops them; rerun after registration;
* trend gate: >= 24 daily periods per series (M0/C0 have ~3700 with a
  settlement value, so the gate only exists to guard a future thin symbol);
* dedup key (symbol, date); gate key (concept code, entity id).
"""

from __future__ import annotations

from ._common import dedup_latest, fmt, gate_periods, num, unit_items

SOURCE = "dce"

# entity codes registered by the main agent (entity_type 'contract');
# extract never inserts entity rows itself.
SYMBOL_ENTITY = {
    "M0": "DCE.M0",
    "C0": "DCE.C0",
}

# symbol -> concept code (the settle price series each symbol feeds)
SYMBOL_CONCEPT = {
    "M0": "futures.dce.meal.settle.daily",
    "C0": "futures.dce.corn.settle.daily",
}

CONCEPTS = [
    # (code, name_zh, name_en, category, unit, measure, frequency,
    #  entity_type, source)
    ("futures.dce.meal.settle.daily",
     "豆粕主力连续结算价",
     "DCE soybean meal main-continuous settlement",
     "商品期货", "cny_per_ton", "price", "daily", "contract", "dce"),
    ("futures.dce.corn.settle.daily",
     "玉米主力连续结算价",
     "DCE corn main-continuous settlement",
     "商品期货", "cny_per_ton", "price", "daily", "contract", "dce"),
]


def _resolve_entity_ids(cur, codes: set[str]) -> dict[str, int]:
    """entity code -> entities.id for codes that exist (no creation here)."""
    if not codes:
        return {}
    cur.execute(
        "SELECT code, id FROM entities"
        " WHERE entity_type = 'contract' AND code = ANY(%s)",
        (sorted(codes),),
    )
    return {code: eid for code, eid in cur.fetchall()}


def extract(cur, notes: list[str]) -> list[tuple]:
    """Return rows (code, entity_type, entity_id, date, granularity, value).

    Appends human-readable notes about dedup, skipped sentinel rows,
    unregistered entities and gate drops — never silently.
    """
    items = unit_items(cur, SOURCE)
    notes.append(f"crawl payloads read: {len(items)}")

    deduped = dedup_latest(
        items,
        key=lambda p: (str(p.get("symbol")), str(p.get("date")))
        if p.get("symbol") and p.get("date") else None,
    )
    notes.append(f"after (symbol, date) dedup keep-latest: {len(deduped)}")

    missing_entities = [
        code for code in SYMBOL_ENTITY.values() if code not in _resolve_entity_ids(
            cur, set(SYMBOL_ENTITY.values()))
    ]
    entity_ids = _resolve_entity_ids(cur, set(SYMBOL_ENTITY.values()))
    if missing_entities:
        notes.append(
            "entity rows not registered yet (main agent owns registration,"
            f" extractor does not create them): {sorted(missing_entities)}"
            " — their rows are produced with entity_id=None and dropped by"
            " the writer until registered")

    raw_rows: list[tuple] = []
    skipped_sentinel = 0
    unknown_symbols: dict[str, int] = {}
    for (symbol, _date), payload in sorted(deduped.items()):
        concept = SYMBOL_CONCEPT.get(symbol)
        if concept is None:
            unknown_symbols[symbol] = unknown_symbols.get(symbol, 0) + 1
            continue
        settle = num(payload.get("settle"))
        if settle is None or settle <= 0:
            skipped_sentinel += 1  # 0 = Sina "not relayed" sentinel
            continue
        date = str(payload.get("date"))
        entity_code = SYMBOL_ENTITY[symbol]
        raw_rows.append((
            concept, "contract", entity_ids.get(entity_code),
            date, "day", fmt(settle),
        ))
    if skipped_sentinel:
        notes.append(
            f"skipped settle==0/None sentinel rows (kept in payload, never"
            f" written as a price): {skipped_sentinel}")
    if unknown_symbols:
        notes.append(
            "skipped payloads for symbols without a registered concept"
            f" (no concept row exists): "
            + ", ".join(f"{s} x{n}" for s, n in sorted(unknown_symbols.items())))

    kept, dropped = gate_periods(raw_rows)
    if dropped:
        per_series: dict[tuple, int] = {}
        for r in dropped:
            per_series[(r[0], r[2])] = per_series.get((r[0], r[2]), 0) + 1
        for (code, eid), n in sorted(per_series.items()):
            notes.append(
                f"SKIP series concept={code} entity_id={eid}: only {n}"
                f" usable periods (<24 trend gate) — excluded entirely")
    notes.append(f"rows after >=24-period gate: {len(kept)}")
    return kept
