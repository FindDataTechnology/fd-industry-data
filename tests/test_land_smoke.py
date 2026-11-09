"""Landing smoke check tests (harden-crawl-delivery 4.1).

The three pre-existing local checks prove a unit *compiles* and matches the
schema. They never proved it can fetch — that only surfaced on the first
scheduled run, a full cycle later. The smoke check calls the entry point with a
small limit and asserts shape.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    path = REPO / "scripts" / "land_source.py"
    spec = importlib.util.spec_from_file_location("land", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


land = _load()

MANIFEST = {
    "functions": [{
        "command": "run_demo",
        "columns": [{"name": "period"}, {"name": "value"}],
    }]
}


def _unit(tmp_path, body):
    d = tmp_path / "spiders" / "demo"
    d.mkdir(parents=True)
    (d / "spider.py").write_text(body)
    return d


GOOD = """
def run_demo(limit=100):
    return [{"period": "2026-10", "value": 1}, {"period": "2026-09", "value": 2}][:limit]
"""

EMPTY = """
def run_demo(limit=100):
    return []
"""

MISSING_COLUMN = """
def run_demo(limit=100):
    return [{"period": "2026-10"}]
"""

RAISES = """
def run_demo(limit=100):
    raise RuntimeError("upstream exploded")
"""

IMPORT_ERROR = """
import definitely_not_a_module  # noqa: F401

def run_demo(limit=100):
    return [{"period": "x", "value": 1}]
"""

WRONG_ENTRY = """
def run_other(limit=100):
    return [{"period": "x", "value": 1}]
"""


def test_good_source_passes(tmp_path):
    unit = _unit(tmp_path, GOOD)
    assert land.smoke_fetch(unit, "demo", MANIFEST) == []


def test_zero_rows_is_rejected(tmp_path):
    unit = _unit(tmp_path, EMPTY)
    out = land.smoke_fetch(unit, "demo", MANIFEST)
    assert out and "returned no rows" in out[0]


def test_missing_declared_column_is_rejected(tmp_path):
    unit = _unit(tmp_path, MISSING_COLUMN)
    out = land.smoke_fetch(unit, "demo", MANIFEST)
    assert out and "declared column(s) absent" in out[0]
    assert "value" in out[0]


def test_exception_during_fetch_is_rejected(tmp_path):
    unit = _unit(tmp_path, RAISES)
    out = land.smoke_fetch(unit, "demo", MANIFEST)
    assert out and "RuntimeError" in out[0]


def test_import_failure_is_rejected(tmp_path):
    unit = _unit(tmp_path, IMPORT_ERROR)
    out = land.smoke_fetch(unit, "demo", MANIFEST)
    assert out and "failed to import" in out[0]


def test_wrong_entry_point_is_rejected(tmp_path):
    unit = _unit(tmp_path, WRONG_ENTRY)
    out = land.smoke_fetch(unit, "demo", MANIFEST)
    assert out and "no callable run_demo" in out[0]


def test_declared_columns_reads_first_function():
    assert land.declared_columns(MANIFEST) == ["period", "value"]
    assert land.declared_columns({}) == []
    assert land.declared_columns({"functions": [{"columns": [{"type": "int"}]}]}) == []


def test_smoke_uses_a_small_limit(tmp_path):
    unit = _unit(tmp_path, """
def run_demo(limit=100):
    assert limit <= 5, f"smoke must not ask for {limit} rows"
    return [{"period": "2026-10", "value": 1}]
""")
    assert land.smoke_fetch(unit, "demo", MANIFEST) == []


def test_smoke_does_not_touch_the_central_db(tmp_path, monkeypatch):
    """冒烟只调入口，不经 runner，所以不写 crawl_runs。"""
    called = []
    import fd_industry_data.writer as writer

    monkeypatch.setattr(writer, "start_run", lambda **k: called.append(k), raising=False)
    monkeypatch.setattr(writer, "report_run", lambda **k: called.append(k), raising=False)
    unit = _unit(tmp_path, GOOD)
    assert land.smoke_fetch(unit, "demo", MANIFEST) == []
    assert called == []
