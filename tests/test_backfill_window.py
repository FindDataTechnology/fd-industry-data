"""Backfill window tests (harden-crawl-delivery 3.5).

The mechanism must reuse the existing window channel — no new runner flag, no
new executor — and must never queue a run unless asked with --apply.
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load_script():
    path = REPO / "scripts" / "backfill_source.py"
    spec = importlib.util.spec_from_file_location("backfill", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


backfill = _load_script()


def test_parse_window_accepts_months_and_years():
    assert backfill.parse_window("24m") == 24
    assert backfill.parse_window("2y") == 24
    assert backfill.parse_window("1y") == 12
    assert backfill.parse_window(18) == 18


def test_parse_window_rejects_malformed_and_out_of_range():
    assert backfill.parse_window("forever") is None
    assert backfill.parse_window("0m") is None
    assert backfill.parse_window("500m") is None
    assert backfill.parse_window("2w") is None
    assert backfill.parse_window(None) is None
    assert backfill.parse_window(True) is None


def test_manifest_window_reads_declaration(tmp_path):
    d = tmp_path / "spiders" / "alpha"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text("name: alpha\nbackfill_window: 36m\n")
    months, note = backfill.manifest_window("alpha", tmp_path)
    assert months == 36 and "36m" in note


def test_manifest_window_reports_absence(tmp_path):
    d = tmp_path / "spiders" / "alpha"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text("name: alpha\n")
    months, note = backfill.manifest_window("alpha", tmp_path)
    assert months is None and "no backfill_window" in note


def test_plan_uses_the_existing_window_channel(tmp_path):
    d = tmp_path / "spiders" / "alpha"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text("name: alpha\nsite: zihan\nschedule: \"0 4 * * *\"\n")
    job = backfill.plan("alpha", 12, date(2026, 11, 6), tmp_path)
    # 12 个月 → 起点是去年同月的 1 号；终点是点亮日
    assert job["params"] == {"window_start": "2025-11-01", "window_end": "2026-11-06"}
    assert job["site"] == "zihan"
    assert job["requested_by"] == "backfill"


def test_plan_defaults_to_twelve_months():
    job = backfill.plan("whatever", None, date(2026, 11, 6), Path("/nonexistent"))
    assert job["months"] == 12


def test_plan_ignores_docker_ambiguity_and_keeps_site(tmp_path):
    d = tmp_path / "spiders" / "alpha"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text("name: alpha\nsite: xinru-server2\n")
    assert backfill.plan("alpha", 6, date(2026, 11, 6), tmp_path)["site"] == "xinru-server2"


def test_dry_run_is_the_default_and_writes_nothing(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(backfill, "queue_run", lambda *a, **k: called.append(1), raising=False)
    rc = backfill.main(["backfill_source.py", "no-such-source", "--today", "2026-11-06"])
    assert rc == 0
    assert called == []
    assert "dry run" in capsys.readouterr().out


def test_apply_without_dsn_refuses(monkeypatch):
    monkeypatch.delenv("FD_CRAWL_DB_URL", raising=False)
    rc = backfill.main(["backfill_source.py", "no-such-source", "--apply",
                        "--today", "2026-11-06"])
    assert rc == 2
