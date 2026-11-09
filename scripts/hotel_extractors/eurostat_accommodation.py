#!/usr/bin/env python3
"""EU accommodation & tourism series — ``eurostat-api`` existing lane (wave 0).

SOURCE = ``eurostat-api``. No new crawler: the four datasets were already
reachable from this unit; wave 0 adds four command surfaces
(``run_eurostat_i55`` / ``run_eurostat_tour_occ`` / ``run_eurostat_tour_annual``
/ ``run_eurostat_tour_cap``) and pulls them **by name** instead of folding them
into the unit's scheduled wave-C rotation (that would divide the same
``--limit`` across eight buckets and starve the restaurant/agriculture shares).

## Why this extractor calls the commands instead of reading ``crawl_items``

Every other hotel extractor reads stored payloads of a scheduled lane. These
four series are *not* scheduled: the platform pulls them on demand through
provider dispatch, so there is no ``crawl_runs`` row to read. The extractor
therefore invokes the unit's own entry points — the same functions the golden
replay and the provider use — and records the upstream URL on every row. A
failed fetch raises inside the unit and is reported by the batch runner; it is
never turned into zero rows.

## Series (all measured live 2026-11-08, see docs/hotel-series-curation.md §1.1)

| concept | dataset / dims | periods |
|---|---|---|
| E1 | ``sts_setu_m`` NETTUR/SCA/I21 nace_r2=**I55** | 139 (2015-01→2026-07) |
| E2 | ``tour_occ_nim`` c_resid=TOTAL unit=NR nace_r2=I551-I553 | 188 (2011-01→2026-08) |
| E3 | same, c_resid=FOR | 188 |
| E4 | same, c_resid=TOTAL unit=PCH_SM | 188 |
| E5 | ``tour_cap_nat`` accomunit=BEDPL nace_r2=I551 | 21 (2005→2025) |
| E6 | ``tour_occ_ninat`` c_resid=TOTAL nace_r2=I551 | 21 (1990→2025, gaps) |

## Caliber notes that must travel with the numbers

* **I551 (hotels only) is empty upstream** — E1 is the accommodation aggregate
  (NACE I55), never label it "hotel turnover".
* **No EU accommodation price index exists** (HICP CP11 / CP112 both 404), so
  there is no 价格 leg to pair with E2; the price×volume pair the spec asks for
  is recorded as a missing side (D1/D5), not synthesised.
* **E6 has gap years** (36-year header, 21 published values) — gaps stay gaps.
* E5/E6 are annual with 21 periods → 12–23 band → ``short_window`` declared by
  the naming gate; they are not trend-qualified (>= 24) series.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]  # fd-industry-data/
_UNIT = _ROOT / "spiders" / "eurostat-api" / "spider.py"

if str(_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_ROOT / "scripts"))

from hotel_extractors._common import fmt  # noqa: E402

SOURCE = "eurostat-api"

#: EU27_2020 aggregate — the id the restaurant line already uses for EU series
ENTITY_EU = 300990


def _load_unit():
    """Load ``spiders/eurostat-api/spider.py`` by path (dash dir, not importable)."""
    name = "unit_eurostat_api_hotel_wave0"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _UNIT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


CONCEPTS: list[tuple] = [
    (
        "hotel.eu.turnover.accommodation.monthly",
        "欧盟住宿业营业额指数（NACE I55，2021=100）",
        "EU27 turnover index, NACE I55 accommodation activities "
        "(SCA, 2021=100)",
        "行业景气",
        "index_2021_100",
        "value",
        "monthly",
        "country",
        SOURCE,
    ),
    (
        "hotel.eu.nights.monthly",
        "欧盟旅游住宿过夜数（全部居民）",
        "EU27 nights spent at tourist accommodation establishments, "
        "all residents",
        "旅游需求",
        "nights",
        "value",
        "monthly",
        "country",
        SOURCE,
    ),
    (
        "hotel.eu.nights.foreign.monthly",
        "欧盟旅游住宿过夜数（外国居民）",
        "EU27 nights spent at tourist accommodation establishments, "
        "foreign residents",
        "旅游需求",
        "nights",
        "value",
        "monthly",
        "country",
        SOURCE,
    ),
    (
        "hotel.eu.nights.yoy.monthly",
        "欧盟旅游住宿过夜数同比（全部居民）",
        "EU27 nights spent at tourist accommodation establishments, "
        "all residents, % change on same month previous year",
        "旅游需求",
        "%",
        "value",
        "monthly",
        "country",
        SOURCE,
    ),
    (
        "hotel.eu.capacity.bed_places.annual",
        "欧盟旅游住宿床位容量",
        "EU27 tourist accommodation capacity in bed places",
        "供给能力",
        "bed_places",
        "value",
        "annual",
        "country",
        SOURCE,
    ),
    (
        "hotel.eu.nights.annual",
        "欧盟旅游住宿过夜数（年度，全部居民）",
        "EU27 nights spent at tourist accommodation establishments, "
        "all residents, annual",
        "旅游需求",
        "nights",
        "value",
        "annual",
        "country",
        SOURCE,
    ),
]

CODE_TURNOVER = "hotel.eu.turnover.accommodation.monthly"
CODE_NIGHTS = "hotel.eu.nights.monthly"
CODE_NIGHTS_FOR = "hotel.eu.nights.foreign.monthly"
CODE_NIGHTS_YOY = "hotel.eu.nights.yoy.monthly"
CODE_CAPACITY = "hotel.eu.capacity.bed_places.annual"
CODE_NIGHTS_ANNUAL = "hotel.eu.nights.annual"

#: monthly tour_occ_nim (indic, unit) → concept code. The fourth produced combo
#: (FOR × PCH_SM) has no concept in this wave — it is skipped with a note.
TOUR_OCC_MAP = {
    ("TOTAL", "NR"): CODE_NIGHTS,
    ("FOR", "NR"): CODE_NIGHTS_FOR,
    ("TOTAL", "PCH_SM"): CODE_NIGHTS_YOY,
}

TREND_MIN = 24        # >= 24 periods → trend-qualified
SHORT_MIN = 12        # 12–23 → keep with a short_window declaration


def extract(cur, notes) -> list[tuple]:
    """Fetch the four series and emit ``(code, etype, eid, date, gran, value)``."""
    unit = _load_unit()

    rows: list[tuple] = []
    skipped_combo = 0
    for (indic, unit_code), period, value in _tour_occ(unit):
        code = TOUR_OCC_MAP.get((indic, unit_code))
        if code is None:
            skipped_combo += 1  # FOR×PCH_SM 未注册概念（本波），不静默造数也不静默丢弃
            continue
        rows.append((code, "country", ENTITY_EU, period, "month", fmt(value)))
    if skipped_combo:
        notes.append(
            "eurostat-api: tour_occ_nim 的 (FOR, PCH_SM) 组合上游有值但本波未注册概念"
            f"（跳过 {skipped_combo} 行）—— 要接须先过命名门，不得复用 TOTAL 口径"
        )

    for value, period in _i55(unit):
        rows.append((CODE_TURNOVER, "country", ENTITY_EU, period, "month",
                     fmt(value)))

    for value, period in _annual(unit.run_eurostat_tour_annual, "tour_occ_ninat"):
        rows.append((CODE_NIGHTS_ANNUAL, "country", ENTITY_EU, period, "year",
                     fmt(value)))

    for value, period in _annual(unit.run_eurostat_tour_cap, "tour_cap_nat"):
        rows.append((CODE_CAPACITY, "country", ENTITY_EU, period, "year",
                     fmt(value)))

    return _apply_trend_gate(rows, notes)


def _i55(unit) -> list[tuple[float, str]]:
    out = []
    for r in unit.run_eurostat_i55(limit=1000):
        out.append((r["value"], r["period"]))
    return out


def _tour_occ(unit) -> list[tuple[tuple[str, str], str, float]]:
    """((indic, unit), period, value) triples from the four-series request."""
    out = []
    for r in unit.run_eurostat_tour_occ(limit=5000):
        out.append(((r["indic"], r["unit"]), r["period"], r["value"]))
    return out


def _annual(fn, dataset: str) -> list[tuple[float, str]]:
    return [(r["value"], r["period"]) for r in fn(limit=500)]


def _apply_trend_gate(rows: list[tuple], notes: list[str]) -> list[tuple]:
    """Keep >= 12 periods; drop below that with an explicit note.

    This is the one place the gate is applied for the EU leg. E5/E6 land in the
    12–23 band on purpose (real annual history, not a rolling window) and are
    registered as ``short_window`` by scripts/naming_gate_hotel.py.
    """
    counts: dict[str, int] = {}
    for code, _e, _i, _d, _g, _v in rows:
        counts[code] = counts.get(code, 0) + 1
    dropped = {c for c, n in counts.items() if n < SHORT_MIN}
    if dropped:
        notes.append(
            "eurostat-api: 序列期数 < %d，整条不入库：%s"
            % (SHORT_MIN, ", ".join(f"{c}={counts[c]}期" for c in sorted(dropped)))
        )
    for code in sorted(counts):
        n = counts[code]
        if n >= TREND_MIN:
            continue
        if n >= SHORT_MIN:
            notes.append(
                "eurostat-api: %s 共 %d 期（12–23 档）→ 按 short_window 登记，"
                "不得画趋势线" % (code, n)
            )
    return [r for r in rows if r[0] not in dropped]
