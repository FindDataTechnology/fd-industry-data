"""hotel-vertical wave 0 — EU accommodation extractor contract (offline).

The extractor calls the four ``eurostat-api`` commands live, so ``extract()``
itself is not exercised here. What is pinned:

* the six concept definitions satisfy the naming gate's six fields;
* the series→concept map covers three of the four produced ``tour_occ_nim``
  combos and deliberately leaves (FOR, PCH_SM) unmapped with a note;
* the trend gate drops < 12-period series, keeps 12–23 with a declaration
  (never silently), and keeps >= 24 quietly.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from hotel_extractors import eurostat_accommodation as lane  # noqa: E402

SIX_FIELDS = ("name_zh", "name_en", "unit", "frequency", "category")
# CONCEPTS tuple: (code, name_zh, name_en, category, unit, measure,
#                  frequency, entity_type, source)
NAME_ZH, NAME_EN, CATEGORY, UNIT, MEASURE, FREQUENCY = 1, 2, 3, 4, 5, 6
CODES = {c[0] for c in lane.CONCEPTS}


def test_six_concepts_are_defined_exactly_once():
    assert len(lane.CONCEPTS) == 6
    assert len(CODES) == 6


def test_every_concept_fills_the_naming_gate_six_fields():
    for row in lane.CONCEPTS:
        assert len(row) == 9, f"{row[0]}: CONCEPTS tuples are 9-wide"
        for field, value in zip(
            SIX_FIELDS,
            (row[NAME_ZH], row[NAME_EN], row[UNIT], row[FREQUENCY], row[CATEGORY]),
        ):
            assert str(value).strip(), f"{row[0]}: {field} is empty"
        assert row[MEASURE] == "value"
        assert row[FREQUENCY] in ("monthly", "annual")
        assert row[7] == "country"
        assert row[8] == "eurostat-api"


def test_turnover_concept_is_labelled_accommodation_not_hotel():
    """sts_setu_m serves NACE I55 only; I551 (hotels) is empty upstream."""
    row = next(c for c in lane.CONCEPTS if c[0] == lane.CODE_TURNOVER)
    assert "住宿业" in row[NAME_ZH]
    assert "accommodation" in row[NAME_EN]
    assert "hotel" not in row[NAME_EN].lower()


def test_annual_nights_and_capacity_carry_annual_frequency():
    by_code = {c[0]: c for c in lane.CONCEPTS}
    assert by_code[lane.CODE_NIGHTS_ANNUAL][FREQUENCY] == "annual"
    assert by_code[lane.CODE_CAPACITY][FREQUENCY] == "annual"
    assert by_code[lane.CODE_CAPACITY][UNIT] == "bed_places"
    assert by_code[lane.CODE_NIGHTS_ANNUAL][UNIT] == "nights"


def test_yoy_concept_uses_percent_unit():
    by_code = {c[0]: c for c in lane.CONCEPTS}
    assert by_code[lane.CODE_NIGHTS_YOY][UNIT] == "%"


def test_series_map_covers_three_of_four_produced_combos():
    assert set(lane.TOUR_OCC_MAP) == {("TOTAL", "NR"), ("FOR", "NR"), ("TOTAL", "PCH_SM")}
    assert ("FOR", "PCH_SM") not in lane.TOUR_OCC_MAP
    assert set(lane.TOUR_OCC_MAP.values()) <= CODES


def test_trend_gate_drops_series_below_twelve_periods():
    rows = [(lane.CODE_NIGHTS, "country", 1, f"2020-{m:02d}", "month", "1")
            for m in range(1, 12)]
    notes: list[str] = []
    kept = lane._apply_trend_gate(rows, notes)
    assert kept == []
    assert any("不入库" in n and "11期" in n for n in notes)


def test_trend_gate_keeps_twelve_to_twentythree_with_declaration():
    rows = [(lane.CODE_CAPACITY, "country", 1, str(2005 + i), "year", "1")
            for i in range(21)]
    notes: list[str] = []
    kept = lane._apply_trend_gate(rows, notes)
    assert len(kept) == 21
    assert any("short_window" in n and "21 期" in n for n in notes)


def test_trend_gate_keeps_twentyfour_plus_silently():
    rows = [(lane.CODE_NIGHTS, "country", 1, f"20{y:02d}-01", "month", "1")
            for y in range(0, 24)]
    notes: list[str] = []
    kept = lane._apply_trend_gate(rows, notes)
    assert len(kept) == 24
    assert notes == []


def test_entity_id_is_the_eu_aggregate_used_by_the_restaurant_line():
    assert lane.ENTITY_EU == 300990
