"""Hotel wave-1 unit extractors — offline contracts (star hotel + tourism demand).

``extract()`` calls the units live, so it is not exercised here. The pieces that
silently corrupt data if wrong are pinned instead: region→entity classification
(a wrong province code mis-joins forever), quarter→date mapping (the catalogue's
date column rejects ``2023Q4``), the concept/scope model, and ``granularity``
labels staying inside ``varchar(8)``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import hotel_regions as regions  # noqa: E402
from hotel_extractors import mct_star_hotel as star  # noqa: E402
from hotel_extractors import mct_tourism_demand as tou  # noqa: E402


# ── region / city classification ─────────────────────────────────────────────
@pytest.mark.parametrize("label,code", [
    ("广东", "CN-44"), ("内蒙古", "CN-15"), ("新疆", "CN-65"), ("北京", "CN-11"),
])
def test_provinces_use_gb_t2260_two_digit_codes(label, code):
    assert regions.classify("省", label) == ("region", code)


def test_xinjiang_corps_is_its_own_province_level_region():
    assert regions.classify("省", "新疆兵团") == ("region", "CN-XJBT")


def test_national_row_maps_to_the_country_entity():
    assert regions.classify("全国", "全国") == ("country", "CN")


def test_50_city_aggregate_is_not_the_national_total():
    """50城合计 is a 50-city subtotal (2,209) — never the country row (6,129)."""
    assert regions.classify("50城合计", "50城合计") == ("region", "CN-50CITIES")


def test_city_code_carries_the_upstream_label_instead_of_an_invented_adcode():
    assert regions.classify("城市", "杭州") == ("city", "CN-CITY-杭州")


def test_unknown_labels_raise_instead_of_being_guessed():
    assert regions.classify("省", "火星省") is None
    with pytest.raises(KeyError):
        regions.classify_or_raise("省", "火星省")


# ── star hotel: quarter → date, concept layout ───────────────────────────────
@pytest.mark.parametrize("period,expected", [
    ("2023Q4", "2023-12-31"), ("2024Q1", "2024-03-31"),
    ("2024Q2", "2024-06-30"), ("2024Q3", "2024-09-30"),
])
def test_quarter_labels_become_period_end_dates(period, expected):
    assert star.quarter_end(period) == expected


def test_quarter_end_rejects_unknown_labels():
    with pytest.raises(ValueError):
        star.quarter_end("2024H1")


def test_star_dimension_is_part_of_the_concept_not_the_entity():
    """分省(all) and 分省(三星) must be different concepts on the same entity."""
    all_band = star.concept_code("occupancy", "")
    three_star = star.concept_code("occupancy", "3")
    assert all_band != three_star
    assert all_band.endswith(".all.quarterly")
    assert three_star.endswith(".star3.quarterly")


def test_every_indicator_has_all_six_star_bands():
    codes = {c[0] for c in star.CONCEPTS}
    for ind in star.INDICATORS:
        for dim in ("all", "star1", "star2", "star3", "star4", "star5"):
            assert f"hotel.cn.star_rated.{ind}.{dim}.quarterly" in codes


def test_star_concepts_are_quarterly_and_region_typed():
    for code, _zh, _en, category, unit, measure, freq, etype, src in star.CONCEPTS:
        assert freq == "quarter"
        assert etype == "region"
        assert measure == "value"
        assert category == "行业经营"
        assert src == "mct-star-hotel"
        assert unit


# ── tourism demand: period kinds → granularity (varchar(8)) ──────────────────
@pytest.mark.parametrize("period,kind,expected", [
    ("2025", "fy", "2025"),
    ("2025H1", "ytd_h1", "2025-06-30"),
    ("2025Q1-Q3", "ytd_9m", "2025-09-30"),
    ("2026Q1", "quarter", "2026-03-31"),
])
def test_period_kind_maps_to_a_date_the_catalogue_accepts(period, kind, expected):
    assert tou.period_date(period, kind) == expected


@pytest.mark.parametrize("kind", ["year", "ytd_h1", "ytd_9m", "quarter"])
def test_granularity_labels_fit_varchar_8(kind):
    assert len(kind) <= 8


def test_cumulative_and_full_year_never_collide():
    """Same concept, same year: fy vs ytd_h1 vs ytd_9m differ by granularity+date."""
    keys = {
        (tou.period_date("2025", "fy"), "year"),
        (tou.period_date("2025H1", "ytd_h1"), "ytd_h1"),
        (tou.period_date("2025Q1-Q3", "ytd_9m"), "ytd_9m"),
    }
    assert len(keys) == 3


def test_scope_is_encoded_in_the_concept_code():
    assert tou.concept_code("domestic_trips", "城镇") == \
        "hotel.cn.tourism.domestic_trips.urban"
    assert tou.concept_code("outbound_trips", "内地") == \
        "hotel.cn.tourism.outbound_trips.mainland"


def test_tourism_concepts_are_irregular_country_series_in_published_units():
    for code, _zh, _en, category, unit, measure, freq, etype, src in tou.CONCEPTS:
        assert freq == "irregular"
        assert etype == "country"
        assert category == "旅游需求"
        assert src == "mct-tourism-demand"
        # published units kept verbatim — no conversion to base units
        assert unit in {"亿人次", "万亿元", "万人次", "家", "亿元",
                        "元/间夜", "%", "个"}


def test_pairs_cover_the_scopes_the_releases_actually_carry():
    assert ("domestic_trips", "城镇") in tou.PAIRS
    assert ("domestic_trips", "农村") in tou.PAIRS
    assert ("domestic_spending", "农村") in tou.PAIRS
    assert ("outbound_trips", "内地") in tou.PAIRS
    assert ("foreign_inbound_trips", "外国") in tou.PAIRS
    assert ("hkmotw_inbound_trips", "港澳台") in tou.PAIRS
