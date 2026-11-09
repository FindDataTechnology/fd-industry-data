"""Telemetry parse-error tests (harden-crawl-delivery 2.10).

The 2026-11-07 production `last-run.json` carried this as the first entry of
`skipped_unknown_period`:

    'at TCPConnectWrap.afterConnect [as oncomplete] (node:net:1638:16)'

A Node stack line became a source id. The loader padded short lines to 8 columns
so any non-table text could become a "record"; then the period lookup failed and
it landed in the unknown-period list, where it read as "a source we can't judge".
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from fd_industry_data.health import inspector
from fd_industry_data.health.config import DEFAULTS


def _fake_psql(monkeypatch, stdout: str, returncode: int = 0):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")
    monkeypatch.setattr(inspector.subprocess, "run", fake_run)


def test_node_stack_line_is_not_a_record(monkeypatch):
    stdout = (
        "at TCPConnectWrap.afterConnect [as oncomplete] (node:net:1638:16)\n"
        "gdacs\tsuccess\t2026-11-09 01:00:00\t5\t\t\n"
    )
    _fake_psql(monkeypatch, stdout)
    errors: list[str] = []
    records = inspector.load_runs_from_psql(DEFAULTS, "postgres://x", parse_errors=errors)
    assert [r["source"] for r in records] == ["gdacs"]
    assert errors, "被丢掉的非遥测行必须留痕"
    assert "TCPConnectWrap" in errors[0]


def test_valid_status_with_garbage_source_is_rejected(monkeypatch):
    """状态列合法但源名是报错文本 —— 只校验状态列会漏掉这一形态。"""
    stdout = "some error happened here\tsuccess\t2026-11-09 01:00:00\t5\t\t\n"
    _fake_psql(monkeypatch, stdout)
    errors: list[str] = []
    records = inspector.load_runs_from_psql(DEFAULTS, "postgres://x", parse_errors=errors)
    assert records == []
    assert any("non-slug source" in e for e in errors)


def test_slug_shapes_are_accepted(monkeypatch):
    stdout = (
        "gdacs\tsuccess\t2026-11-09 01:00:00\t5\t\t\n"
        "education_openalex\tsuccess\t2026-11-09 01:00:00\t5\t\t\n"
        "sse-szse-margin\tfailed\t2026-11-09 01:00:00\t0\tboom\t500\n"
        "blocked-config-x\tblocked-config\t2026-11-09 01:00:00\t0\tmissing env\t\n"
    )
    _fake_psql(monkeypatch, stdout)
    records = inspector.load_runs_from_psql(DEFAULTS, "postgres://x")
    assert len(records) == 4


def test_short_rows_are_padded_but_only_when_telemetry(monkeypatch):
    stdout = "gdacs\tsuccess\t2026-11-09 01:00:00\n"
    _fake_psql(monkeypatch, stdout)
    records = inspector.load_runs_from_psql(DEFAULTS, "postgres://x")
    assert records[0]["watermark"] is None
    assert records[0]["limit"] is None
    assert records[0]["rows"] is None


def test_plan_keeps_garbage_out_of_skipped_unknown_period(tmp_path):
    repo = tmp_path
    (repo / "spiders").mkdir()
    plan = inspector.plan_tickets({"at TCPConnectWrap.afterConnect": []}, repo, DEFAULTS,
                                  inspector.datetime(2026, 11, 9, tzinfo=inspector.timezone.utc))
    assert all("TCPConnectWrap" not in s for s in plan.skipped_unknown_period)
    assert any("TCPConnectWrap" in e for e in plan.parse_errors)
