"""hotel-vertical wave 0 — Eurostat accommodation/tourism series (offline).

The four commands run_eurostat_i55 / tour_occ / tour_annual / tour_cap live in
``spiders/eurostat-api`` (dash-named dir). They are loaded here by file path:
``import spiders.eurostat_api`` cannot work, and the conformance gate forbids
adding an ``eurostat_api`` twin directory (adapter-dash-underscore-pair).

What is asserted offline (no network in CI):
  * the CFG→URL contract, because a wrong dimension shape is what makes
    Eurostat answer 404 "not available for dissemination" (measured 2026-11-08
    for p_adj=N and for the CP11/CP112 accommodation price index);
  * the 12-column row contract and the null-cell skip (never zero-filled);
  * the non-empty assertion that turns upstream parameter drift into a hard
    failure instead of a silent zero-row success;
  * the manifest declarations that make the commands platform-reachable.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "spiders" / "eurostat-api"

# Unique module name: several test modules load a file called spider.py, and a
# plain ``import spider`` collides in sys.modules across files.
_MODULE = "spider_eurostat_api_hotel"
_spec = importlib.util.spec_from_file_location(_MODULE, UNIT / "spider.py")
lane = importlib.util.module_from_spec(_spec)
sys.modules[_MODULE] = lane
_spec.loader.exec_module(lane)

COLUMNS = (
    "dataset", "geo", "freq", "unit", "indic", "nace_r2",
    "s_adj", "p_adj", "period", "value", "scraped_at", "source_url",
)

# id/size reproduce tour_occ_nim's real dimension order, so the stride maths
# exercised here is the same one production walks.
TOUR_OCC_DOC = {
    "class": "dataset",
    "id": ["freq", "c_resid", "unit", "nace_r2", "geo", "time"],
    "size": [1, 2, 2, 1, 1, 2],
    "dimension": {
        "freq": {"category": {"index": {"M": 0}}},
        "c_resid": {"category": {"index": {"TOTAL": 0, "FOR": 1}}},
        "unit": {"category": {"index": {"NR": 0, "PCH_SM": 1}}},
        "nace_r2": {"category": {"index": {"I551-I553": 0}}},
        "geo": {"category": {"index": {"EU27_2020": 0}}},
        "time": {"category": {"index": {"2011-01": 0, "2011-02": 1}}},
    },
    # flat = freq*8 + c_resid*4 + unit*2 + nace*2 + geo*2 + time
    "value": {
        "0": 44178570.0,   # TOTAL / NR    / 2011-01
        "1": 43123456.0,   # TOTAL / NR    / 2011-02
        # 2-3 (PCH_SM) deliberately absent upstream → must be skipped, not 0
        "4": 10339171.0,   # FOR   / NR    / 2011-01
        # 5 FOR/NR/2011-02 absent (partial month)
        "6": 7.1,          # FOR   / PCH_SM/ 2011-01
        "7": None,         # explicit null must also be skipped
    },
}


def test_cfg_datasets_are_the_measured_ones():
    assert lane.SETU_I55_CFG["dataset"] == "sts_setu_m"
    assert lane.TOUR_OCC_M_CFG["dataset"] == "tour_occ_nim"
    assert lane.TOUR_OCC_A_CFG["dataset"] == "tour_occ_ninat"
    assert lane.TOUR_CAP_CFG["dataset"] == "tour_cap_nat"


def test_i55_uses_accommodation_aggregate_not_the_empty_hotel_branch():
    """sts_setu_m serves I55; nace_r2=I551 returned 0 values upstream."""
    assert lane.SETU_I55_CFG["dims"]["nace_r2"] == "I55"
    assert lane.SETU_I55_CFG["dims"]["indic_bt"] == "NETTUR"
    assert lane.SETU_I55_CFG["dims"]["s_adj"] == "SCA"
    assert lane.SETU_I55_CFG["dims"]["unit"] == "I21"


def test_no_cfg_carries_a_p_adj_dimension():
    """A bogus p_adj parameter is what produced the 404s during measurement."""
    for cfg in (lane.SETU_I55_CFG, lane.TOUR_OCC_M_CFG,
                lane.TOUR_OCC_A_CFG, lane.TOUR_CAP_CFG):
        assert "p_adj" not in cfg["dims"]
        assert cfg["colmap"]["p_adj"] is None


def test_tour_occ_asks_for_four_series_in_one_request():
    dims = lane.TOUR_OCC_M_CFG["dims"]
    assert dims["c_resid"] == ["TOTAL", "FOR"]
    assert dims["unit"] == ["NR", "PCH_SM"]
    assert dims["nace_r2"] == "I551-I553"


def test_build_url_repeats_list_valued_dimensions():
    url = lane._build_url(lane.TOUR_OCC_M_CFG)
    assert url.startswith("https://ec.europa.eu/eurostat/api/dissemination/"
                          "statistics/1.0/data/tour_occ_nim?")
    assert "c_resid=TOTAL&c_resid=FOR" in url
    assert "unit=NR&unit=PCH_SM" in url
    assert "geo=EU27_2020" in url
    assert "sinceTimePeriod=1990-01" in url


def test_build_url_pins_the_measured_start_for_each_cfg():
    assert "sinceTimePeriod=2000-01" in lane._build_url(lane.SETU_I55_CFG)
    assert "sinceTimePeriod=1990-01" in lane._build_url(lane.TOUR_OCC_A_CFG)
    assert "sinceTimePeriod=1990-01" in lane._build_url(lane.TOUR_CAP_CFG)


def test_colmap_keys_are_exactly_the_series_defining_dimensions():
    for cfg in (lane.SETU_I55_CFG, lane.TOUR_OCC_M_CFG,
                lane.TOUR_OCC_A_CFG, lane.TOUR_CAP_CFG):
        assert set(cfg["colmap"]) == {"indic", "nace_r2", "s_adj", "p_adj"}


def test_jsonstat_rows_have_the_twelve_column_contract():
    rows = lane._jsonstat_rows(
        TOUR_OCC_DOC, lane.TOUR_OCC_M_CFG["colmap"],
        "tour_occ_nim", "https://example.invalid", "2026-11-08T00:00:00+00:00",
    )
    assert rows
    for row in rows:
        assert set(row) == set(COLUMNS)


def test_jsonstat_rows_skip_absent_and_null_cells_instead_of_zero_filling():
    rows = lane._jsonstat_rows(
        TOUR_OCC_DOC, lane.TOUR_OCC_M_CFG["colmap"],
        "tour_occ_nim", "https://example.invalid", "2026-11-08T00:00:00+00:00",
    )
    got = {(r["indic"], r["unit"], r["period"]): r["value"] for r in rows}
    assert got == {
        ("TOTAL", "NR", "2011-01"): 44178570.0,
        ("TOTAL", "NR", "2011-02"): 43123456.0,
        ("FOR", "NR", "2011-01"): 10339171.0,
        ("FOR", "PCH_SM", "2011-01"): 7.1,
    }
    assert all(r["value"] != 0.0 for r in rows)


def test_jsonstat_rows_map_dimensions_onto_the_flat_columns():
    rows = lane._jsonstat_rows(
        TOUR_OCC_DOC, lane.TOUR_OCC_M_CFG["colmap"],
        "tour_occ_nim", "https://example.invalid", "2026-11-08T00:00:00+00:00",
    )
    first = rows[0]
    assert first["dataset"] == "tour_occ_nim"
    assert first["geo"] == "EU27_2020"
    assert first["freq"] == "M"
    assert first["nace_r2"] == "I551-I553"
    # tour_occ_nim has no seasonal/price adjustment: empty, never a guess
    assert first["s_adj"] == ""
    assert first["p_adj"] == ""
    assert first["source_url"] == "https://example.invalid"


def test_capacity_colmap_carries_accomunit_on_the_indic_column():
    doc = {
        "class": "dataset",
        "id": ["freq", "accomunit", "unit", "nace_r2", "geo", "time"],
        "size": [1, 1, 1, 1, 1, 1],
        "dimension": {
            "freq": {"category": {"index": {"A": 0}}},
            "accomunit": {"category": {"index": {"BEDPL": 0}}},
            "unit": {"category": {"index": {"NR": 0}}},
            "nace_r2": {"category": {"index": {"I551": 0}}},
            "geo": {"category": {"index": {"EU27_2020": 0}}},
            "time": {"category": {"index": {"2005": 0}}},
        },
        "value": {"0": 10339171.0},
    }
    rows = lane._jsonstat_rows(
        doc, lane.TOUR_CAP_CFG["colmap"], "tour_cap_nat",
        "https://example.invalid", "2026-11-08T00:00:00+00:00",
    )
    assert len(rows) == 1
    assert rows[0]["indic"] == "BEDPL"
    assert rows[0]["nace_r2"] == "I551"
    assert rows[0]["period"] == "2005"
    assert rows[0]["value"] == 10339171.0


def test_empty_parse_is_a_hard_failure_not_a_zero_row_success():
    with pytest.raises(RuntimeError, match="parameter drift"):
        lane._assert_nonempty([], "tour_occ_nim", "https://example.invalid")


def test_non_empty_parse_passes_through():
    rows = [{"dataset": "tour_occ_nim"}]
    assert lane._assert_nonempty(rows, "tour_occ_nim", "u") is rows


@pytest.fixture(scope="module")
def manifest():
    return yaml.safe_load((UNIT / "manifest.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("command", [
    "run_eurostat_i55",
    "run_eurostat_tour_occ",
    "run_eurostat_tour_annual",
    "run_eurostat_tour_cap",
])
def test_manifest_declares_each_command_with_a_limit_param(manifest, command):
    entries = [e for e in manifest["functions"] if e.get("command") == command]
    assert len(entries) == 1, f"{command} must be declared exactly once"
    entry = entries[0]
    assert [p["name"] for p in entry["parameters"]] == ["limit"]
    assert [c["name"] for c in entry["columns"]] == list(COLUMNS)


def test_hotel_commands_stay_out_of_the_scheduled_name():
    """The unit itself is scheduled, but only run_eurostat_api is the cron entry.

    The four hotel commands are pulled by name (provider dispatch), so they must
    NOT be folded into _wave_c_rows: that would divide the scheduled --limit
    between eight buckets and starve the restaurant/agriculture shares.
    """
    src = (UNIT / "spider.py").read_text(encoding="utf-8")
    wave_c = src.split("def _wave_c_rows", 1)[1].split("\ndef ", 1)[0]
    for command in ("run_eurostat_i55", "run_eurostat_tour_occ",
                    "run_eurostat_tour_annual", "run_eurostat_tour_cap"):
        assert command not in wave_c, f"{command} must not ride the wave-c rotation"
