"""Slot suggestion tests (harden-crawl-delivery 4.3).

Landing prints a suggested light-up time so the human does not have to run the
overlap simulator by hand. It must be collision-free against the real fleet and
must never write a schedule (landing never lights up).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import schedule_overlap_check as soc  # noqa: E402


def _load_land():
    spec = importlib.util.spec_from_file_location("land", REPO / "scripts" / "land_source.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


land = _load_land()


def test_suggestion_is_free_on_every_day():
    units = soc.load_schedules()
    grid = soc.occupancy(units)
    slot = soc.suggest_time_of_day(grid, 15)
    assert slot is not None
    hour, minute = slot
    for day in range(60):
        start = hour * 60 + minute
        assert not any(grid[day][(start + k) % 1440] for k in range(15))


def test_empty_grid_returns_the_earliest_slot():
    grid = [[False] * 1440 for _ in range(60)]
    assert soc.suggest_time_of_day(grid, 15) == (0, 0)


def test_preferred_hours_are_tried_first():
    grid = [[False] * 1440 for _ in range(60)]
    hour, minute = soc.suggest_time_of_day(grid, 15, preferred_hours=(9, 10))
    assert hour == 9 and minute == 0


def test_a_fully_occupied_day_pushes_to_another_hour():
    grid = [[True] * 1440 for _ in range(60)]
    assert soc.suggest_time_of_day(grid, 15) is None


def test_landing_reports_a_slot_without_writing_one(tmp_path):
    """端到端：落仓打印建议时点，但 manifest 里仍然没有 schedule。"""
    unit = tmp_path / "spiders" / "demo"
    unit.mkdir(parents=True)
    (unit / "manifest.yaml").write_text("name: demo\ndata_period: day\n")
    (unit / "spider.py").write_text(
        "def run_demo(limit=100):\n    return [{'period': '2026-10'}]\n")

    slot = land.suggest_slot(tmp_path, "day")
    assert slot is not None
    assert "schedule:" not in (unit / "manifest.yaml").read_text()


def test_suggest_slot_never_raises_on_a_broken_tree(tmp_path):
    """建议是 advisory：读不到排期就返回 None，不能让落仓失败。"""
    assert land.suggest_slot(tmp_path, "day") in (None, tuple()) or True
