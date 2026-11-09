#!/usr/bin/env python3
"""Restaurant-vertical wave 0 extractor — eurostat-api unit (EU HICP catering).

Reads successful eurostat-api crawl payloads from the central crawl DB
(crawl_items ⨝ crawl_runs via _common.unit_items), keeps the HICP
catering-services rows and writes them into semantic_observations following
the exact _common.py write discipline (additive-only, latest-scraped_at dedup,
obs_month bucket, concepts born verified=false, ≥24-period trend gate).

Measured upstream facts (2026-11-07, live dissemination API):
* prc_hicp_inx is DEAD upstream — HTTP 404 "PRC_HICP_INX ... is not available
  for dissemination" for any parameter combo. The live index-level dataset is
  prc_hicp_midx (HICP monthly index); the spider's run_eurostat_hicp crawls it
  with freq=M, unit=I15 (index 2015=100; I05/I96 are older bases), coicop=CP111
  (Catering services; CP11 = Restaurants and hotels), geo=EU27_2020, since
  1996-01 → first actual value 2000-12, 301 monthly values through 2025-12,
  no gaps. EU27_2020 has data (EA20/EA19/EA also do — not needed).
* The STS services-turnover datasets (sts_tsur family) are dead upstream too;
  the EU leg of the restaurant line was re-pointed to the HICP price-index
  view per adjudication (price index ≠ turnover index — different measure,
  noted at spec level).

Entity policy: entity_type 'country'; entity_id is resolved LIVE from
entities (SELECT id, code FROM entities WHERE code IN ('EU27_2020','EA20',
'EA19','EU27_2007') AND entity_type IN ('country','region')), preferring the
geo actually present in the payloads (EU27_2020). semantic_observations.entity_id
is an integer column and extractors never INSERT entities, so while no EU
aggregate entity row exists (measured 2026-11-07: zero rows for all four
codes) this extractor writes ZERO rows and leaves a SKIP note — the main
agent registers the EU27_2020 entity and the next run picks it up
automatically (the code string below is the standing placeholder identity,
not a fabricated integer id).

Module contract (scripts/restaurant_extractors/_common.py):

    SOURCE = "<unit name>"                      # = manifest name = source_used
    CONCEPTS = [ (code, name_zh, name_en, category, unit, measure,
                  frequency, entity_type, source), ... ]
    def extract(cur, notes) -> list[row]:
        # row = (code, entity_type, entity_id, date, granularity, value_str)

Dedup key: (dataset, geo, period) — keep the payload with the latest
scraped_at. Only the CP111 + I15 + EU-aggregate rows are kept; any
member-state or other-coicop/other-unit rows are skipped and counted into
notes (never silently). Trend gate: ≥ 24 periods per series.
"""

from __future__ import annotations

from . import _common
from ._common import dedup_latest, fmt, gate_periods, num, unit_items

SOURCE = "eurostat-api"

# Standing placeholder identity for the EU aggregate (NOT an integer
# entities.id — the id is resolved at runtime; see module docstring).
ENTITY_EU27_2020 = "EU27_2020"

#: registered entities.id for the EU27_2020 aggregate (measured 2026-11-08:
#: entities(300990, 'country', 'EU27_2020', '欧盟（2020 界定）'))
ENTITY_EU_ID = 300990

# Entity codes accepted for the EU aggregate, in preference order (the geo
# actually crawled is EU27_2020; EA20/EA19/EU27_2007 only matter if the
# upstream geo were ever re-pointed).
ENTITY_CODES = ("EU27_2020", "EA20", "EA19", "EU27_2007")

HICP_DATASET = "prc_hicp_midx"
HICP_COICOP = "CP111"
HICP_UNIT = "I15"

# (code, name_zh, name_en, category, unit, measure, frequency, entity_type, source)
CONCEPTS = [
    ("hicp.eu.catering.monthly", "欧盟餐饮服务HICP",
     "EU HICP catering services (CP111) index",
     "价格指数", "index_2015_100", "value", "monthly", "country", SOURCE),
    ("services.eu.restaurant_turnover_index.monthly", "欧盟餐饮营业额指数（NACE I56）",
     "EU27 turnover index, NACE I56 food & beverage service activities",
     "行业景气", "index_2021_100", "value", "monthly", "country", SOURCE),
]


def _resolve_entity_id(cur, geo: str, notes: list) -> int | None:
    """Existing entities row for the crawled EU aggregate, or None.

    Exact task lookup: codes ENTITY_CODES restricted to entity_type
    country/region; the row whose code equals the crawled geo wins. No entity
    rows are ever created here (main agent's job).
    """
    cur.execute(
        "SELECT id, code FROM entities"
        " WHERE code = ANY(%s) AND entity_type IN ('country','region')"
        " ORDER BY id",
        (list(ENTITY_CODES),),
    )
    found = {code: eid for eid, code in cur.fetchall()}
    notes.append(
        f"entity lookup {list(ENTITY_CODES)} (country/region) -> "
        f"{found or 'no rows'}"
    )
    eid = found.get(geo)
    if eid is None:
        notes.append(
            f"SKIP write: entity '{geo}' is not registered in entities"
            " (semantic_observations.entity_id is an integer column and"
            " extractors never INSERT entities) — main agent to register the"
            " EU27_2020 entity; rows will insert automatically on the next run"
        )
    return eid


def _hicp_extract(cur, notes: list) -> list[tuple]:
    """HICP CP111 EU-aggregate monthly index rows (gate-passed), or [].

    Appends human-readable provenance/SKIP notes; never fabricates periods,
    values or entity ids.
    """
    payloads = unit_items(cur, SOURCE)
    notes.append(f"payloads read for source '{SOURCE}': {len(payloads)}")

    hicp = [p for p in payloads if p.get("dataset") == HICP_DATASET]
    skipped_shape = len(payloads) - len(hicp)
    if skipped_shape:
        notes.append(f"non-{HICP_DATASET} payloads ignored: {skipped_shape}")

    # only the CP111 + I15 + EU-aggregate slice; anything else (member states,
    # other coicop/unit codes) is counted into notes, never silently dropped
    kept, skipped = [], {"geo": 0, "coicop": 0, "unit": 0, "value": 0}
    for p in hicp:
        if p.get("geo") != ENTITY_EU27_2020:
            skipped["geo"] += 1
        elif p.get("indic") != HICP_COICOP:
            skipped["coicop"] += 1
        elif p.get("unit") != HICP_UNIT:
            skipped["unit"] += 1
        elif num(p.get("value")) is None:
            skipped["value"] += 1
        else:
            kept.append(p)
    if any(skipped.values()):
        notes.append(
            f"rows outside the CP111+I15+{ENTITY_EU27_2020} slice skipped: "
            + ", ".join(f"{k}={v}" for k, v in skipped.items() if v)
        )

    # dedup by (dataset, geo, time): latest scraped_at wins (batch rule)
    deduped = dedup_latest(
        kept, key=lambda p: (p.get("dataset"), p.get("geo"), p.get("period"))
    )
    superseded = len(kept) - len(deduped)
    if superseded:
        notes.append(f"superseded duplicate periods dropped: {superseded}")

    if not deduped:
        notes.append(f"no usable {HICP_DATASET} payloads; nothing to write")
        return []

    # dedup keys are (dataset, geo, period) tuples — order by the period part
    ordered = sorted(deduped.items(), key=lambda kv: kv[0][2])
    notes.append(
        f"real window: {ordered[0][0][2]} .. {ordered[-1][0][2]}"
        f" ({len(ordered)} months, gaps upstream-null only, no interpolation)"
    )

    # all kept rows share one geo (EU27_2020 slice above); resolve its entity
    geos = {p.get("geo") for _, p in ordered}
    if len(geos) != 1:
        notes.append(f"SKIP write: unexpected mixed geos {sorted(geos)}")
        return []
    geo = geos.pop()
    eid = _resolve_entity_id(cur, geo, notes)
    if eid is None:
        return []

    rows = [
        ("hicp.eu.catering.monthly", "country", eid, key[2], "month",
         fmt(num(payload["value"])))
        for key, payload in ordered
    ]

    # trend gate: >= 24 periods per series (default key: concept code + entity)
    kept_rows, dropped_rows = gate_periods(rows)
    if dropped_rows:
        n_dropped = len({r[3] for r in dropped_rows})
        notes.append(
            f"SKIP series below the 24-period gate: {n_dropped} periods dropped"
        )
    notes.append(
        f"gate: kept {len({(r[0], r[2]) for r in kept_rows})} series / "
        f"{len(kept_rows)} rows"
    )
    return kept_rows


# ── NACE I56 turnover index (restaurant-vertical wave 1 EU leg) ──────────────
#
# User adjudication 2026-11-07 ("补 NACE I56 营业额指数"). This corrects the
# wave-0 note in this module: sts_tsur_m IS 404, but the sibling sts_setu_m
# (Turnover in services) serves I56 fine — verified live 2026-11-08 via the
# spider entry run_eurostat_i56 (NETTUR + SCA + I21, EU27_2020, 319 values
# 2000-01..2026-07, no gaps). Price index ≠ turnover index, so this is a
# separate concept, never merged with HICP.
SETU_DATASET = "sts_setu_m"
SETU_NACE = "I56"
SETU_UNIT = "I21"
SETU_SADJ = "SCA"
SETU_CONCEPT = "services.eu.restaurant_turnover_index.monthly"


def _i56_extract(cur, notes: list) -> list[tuple]:
    payloads = [p for p in unit_items(cur, SOURCE)
                if p.get("dataset") == SETU_DATASET]
    if not payloads:
        notes.append(f"{SETU_DATASET}: 无成功载荷（入口未跑/未点亮），本次无 I56 观测")
        return []

    kept, skipped = [], {"geo": 0, "nace": 0, "unit": 0, "s_adj": 0, "value": 0}
    for p in payloads:
        if p.get("geo") != ENTITY_EU27_2020:
            skipped["geo"] += 1
        elif p.get("nace_r2") != SETU_NACE:
            skipped["nace"] += 1
        elif p.get("unit") != SETU_UNIT:
            skipped["unit"] += 1
        elif p.get("s_adj") != SETU_SADJ:
            skipped["s_adj"] += 1
        elif num(p.get("value")) is None:
            skipped["value"] += 1
        else:
            kept.append(p)
    if any(skipped.values()):
        notes.append(
            f"{SETU_DATASET}: 切片外行跳过 "
            + ", ".join(f"{k}={v}" for k, v in skipped.items() if v)
        )

    deduped = dedup_latest(
        kept, key=lambda p: (p.get("dataset"), p.get("geo"), p.get("period")))
    if not deduped:
        notes.append(f"{SETU_DATASET}: 切片内零可用行，不写观测")
        return []
    ordered = sorted(deduped.items(), key=lambda kv: kv[0][2])

    eid = ENTITY_EU_ID
    cur.execute("SELECT 1 FROM entities WHERE id = %s", (eid,))
    if cur.fetchone() is None:
        notes.append(
            f"SKIP write: entities.id={eid} (EU27_2020) 不存在——抽取器不建实体，"
            "主代理登记后下次运行自动入库"
        )
        return []

    rows = [(SETU_CONCEPT, "country", eid, key[2], "month",
             fmt(num(payload["value"]))) for key, payload in ordered]
    notes.append(
        f"{SETU_DATASET}: 真实窗口 {ordered[0][0][2]} .. {ordered[-1][0][2]}"
        f"（{len(ordered)} 月，unit={SETU_UNIT}（2021=100），"
        f"s_adj={SETU_SADJ}，NACE {SETU_NACE}，EU27_2020）"
    )
    kept_rows, dropped = gate_periods(rows)
    if dropped:
        notes.append(f"SKIP {SETU_DATASET}: {len(dropped)} 行低于 24 期门槛")
    return kept_rows


def extract(cur, notes: list) -> list[tuple]:
    """This unit's observation rows: HICP CP111 (wave 0) + I56 turnover (wave 1)."""
    return _hicp_extract(cur, notes) + _i56_extract(cur, notes)
