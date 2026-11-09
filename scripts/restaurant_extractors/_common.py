#!/usr/bin/env python3
"""Shared helpers for restaurant-vertical wave-0 extractors.

Every restaurant extractor module (one per source unit) imports from here and
follows the exact write discipline of scripts/extract_batch1.py /
extract_batch2.py (wire-industry-supply-funnel / industry-observation-supply
contract):

* additive only — INSERT ... ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING
  on the natural key (concept_id, entity_type, entity_id, date, granularity,
  source_used); a second run inserts zero rows;
* never interpolate, zero-fill or fabricate periods — every written value is a
  real stored payload value; same (series, period) crawled multiple times ->
  keep the payload with the LATEST scraped_at;
* obs_month governance bucket written at the write site (mirror of
  fd_open_data_mcp.semantic.dates.obs_month_of);
* concepts are born verified=false — the naming gate belongs to the main
  agent (naming_gate_* pattern, three hard fields semantic_code/name_zh/
  name_en);
* trend gate: >= 24 continuous periods; 12-23 periods need a short_window
  declaration (registered separately); below that, exclude the whole series
  and leave a SKIP note.

An extractor module exposes:

    SOURCE = "<unit name>"                      # = manifest name = source_used
    CONCEPTS = [ (code, name_zh, name_en, category, unit, measure,
                  frequency, entity_type, source), ... ]
    def extract(cur, notes) -> list[row]:
        # row = (code, entity_type, entity_id, date, granularity, value_str)
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

# ── obs_month governance bucket (fd_open_data_mcp.semantic.dates mirror) ─────
_BARE_YEAR = re.compile(r"^\d{4}$")
_BARE_MONTH = re.compile(r"^\d{4}-\d{2}$")
_ISO_WEEK = re.compile(r"^\d{4}-W\d{2}$")


def obs_month_of(d: str | None) -> str | None:
    """'YYYY-MM' bucket for any tolerated date shape; None when unmappable."""
    if not d:
        return None
    if _ISO_WEEK.match(d):
        try:
            return date.fromisocalendar(int(d[:4]), int(d[6:8]), 1).strftime("%Y-%m")
        except ValueError:
            return None
    if _BARE_YEAR.match(d):
        return f"{d}-12"
    if _BARE_MONTH.match(d) or (len(d) >= 7 and d[4] == "-" and d[:4].isdigit()
                                and d[5:7].isdigit()):
        return d[:7]
    return None


# ── existing entity ids (no entity rows are created by extractors) ───────────
COUNTRY_CN, COUNTRY_US = 1, 2
ENTITY_WORLD = 300932          # country 'WORLD'


def num(s) -> float | None:
    if s is None:
        return None
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None


def fmt(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s or "0"


def unit_items(cur, source: str) -> list[dict]:
    """All stored payloads of one unit's successful runs (central crawl DB)."""
    cur.execute(
        "SELECT ci.payload FROM crawl_items ci"
        " JOIN crawl_runs r ON r.id = ci.run_id"
        " WHERE r.source = %s AND r.status = 'success'",
        (source,),
    )
    return [r[0] for r in cur.fetchall()]


def dedup_latest(items: list[dict], key) -> dict:
    """Keep, per key, the payload with the latest scraped_at (batch rule)."""
    seen: dict = {}
    for p in items:
        k = key(p)
        if k is None:
            continue
        prev = seen.get(k)
        if prev is None or str(p.get("scraped_at", "")) > str(prev.get("scraped_at", "")):
            seen[k] = p
    return seen


def ensure_unit_concepts(cur, source: str, concepts: list, codes: set) -> dict:
    """Resolve concept ids for this unit's codes, creating missing ones
    (verified=false, ON CONFLICT DO NOTHING). Raise on unresolved codes.

    `concepts` rows are the CONCEPTS tuples defined by the extractor module.
    """
    ids: dict = {}
    if not codes:
        return ids
    cur.execute(
        "SELECT id, code FROM concepts WHERE deprecated = false AND code = ANY(%s)",
        (sorted(codes),),
    )
    for cid, code in cur.fetchall():
        ids[code] = cid
    for c in concepts:
        if c[0] not in codes or c[0] in ids:
            continue
        (code, name_zh, name_en, category, unit, measure, frequency,
         entity_type, src) = c
        cur.execute(
            "INSERT INTO concepts (code, name_en, name_zh, category, unit,"
            " measure, frequency, entity_type, source, verified, deprecated,"
            " concept_code)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,false,false,NULL)"
            " ON CONFLICT ON CONSTRAINT uq_concept_identity DO NOTHING"
            " RETURNING id",
            (code, name_en, name_zh, category, unit or None, measure,
             frequency, entity_type, src),
        )
        row = cur.fetchone()
        if row:
            ids[code] = row[0]
    missing = set(codes) - set(ids)
    if missing:
        raise RuntimeError(f"{source}: concept codes unresolved: {sorted(missing)}")
    return ids


def insert_rows(cur, source_used: str, ids: dict, rows) -> int:
    """Additive observation insert; returns inserted row count.

    Row shape is either 6-tuple ``(code, etype, eid, date, gran, value)`` — the
    unit-level ``source_used`` argument is used — or 7-tuple with a trailing
    per-row source. The 7-tuple form exists for extractors that legitimately
    read more than one upstream unit (e.g. TWSE listed + TPEx OTC monthly
    revenue): every observation must carry the real upstream it came from,
    never the sibling's name.
    """
    inserted = 0
    for row in rows:
        if len(row) == 7:
            code, etype, eid, d, gran, value, row_source = row
        else:
            code, etype, eid, d, gran, value = row
            row_source = source_used
        cur.execute(
            "INSERT INTO semantic_observations"
            " (concept_id, entity_type, entity_id, date,"
            "  granularity, value, unit, source_used, fetched_at, obs_month)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now(),%s)"
            " ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING",
            (ids[code], etype, eid, d, gran, value, None, row_source,
             obs_month_of(d)),
        )
        inserted += cur.rowcount
    return inserted


def gate_periods(rows, min_periods: int = 24, key=lambda r: (r[0], r[2])) -> tuple[list, list]:
    """Split rows into (kept, dropped) by the >= min_periods trend gate.

    `key` groups a row into its series (default: concept code + entity id).
    Dropped series must be reported in the extractor notes (SKIP, with the
    reason and real period count — never silently).
    """
    counts = defaultdict(int)
    for r in rows:
        counts[key(r)] += 1
    kept, dropped = [], []
    for r in rows:
        (kept if counts[key(r)] >= min_periods else dropped).append(r)
    return kept, dropped
