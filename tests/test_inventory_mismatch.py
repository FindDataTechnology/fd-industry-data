"""Inventory vs manifest mismatch tests (harden-crawl-delivery 7.4).

Measured 2026-11-09: `inmetals` / `metal-com` were parked in git on 11-06 but
`crawl_sources` still carried a daily schedule. Both sides were internally
consistent — git said stopped, the scheduler said due — and nothing ran for four
days. The inspector could not see it because it exempts parked sources.

The check must fire only on states that can actually cause a wrong outcome. A
parked source with no schedule in the inventory is harmless: `enqueue_due`
requires `schedule IS NOT NULL`, so it simply never runs.
"""
from __future__ import annotations

from pathlib import Path

from fd_industry_data.health import inspector


def _repo(tmp_path, **manifests):
    for source, body in manifests.items():
        d = tmp_path / "spiders" / source
        d.mkdir(parents=True)
        (d / "manifest.yaml").write_text(body)
    return tmp_path


def _fake_inventory(monkeypatch, rows):
    """rows: {source: (schedule, enabled_bool)}"""
    import subprocess

    def fake_run(cmd, **kwargs):
        body = "\n".join(f"{s}\t{sched}\t{'t' if en else 'f'}"
                         for s, (sched, en) in rows.items())
        return subprocess.CompletedProcess(cmd, 0, stdout=body, stderr="")

    monkeypatch.setattr(inspector.subprocess, "run", fake_run)


def test_parked_in_git_but_scheduled_in_db_is_flagged(tmp_path, monkeypatch):
    repo = _repo(tmp_path, inmetals="name: inmetals\nparked: true\n")
    _fake_inventory(monkeypatch, {"inmetals": ("51 4 * * *", True)})
    out = inspector.inventory_mismatches("postgres://x", repo)
    assert len(out) == 1
    assert out[0]["kind"] == "parked-but-scheduled"
    assert "51 4 * * *" in out[0]["inventory"]


def test_parked_and_scheduleless_in_db_is_harmless(tmp_path, monkeypatch):
    """没有 schedule 就跑不起来（enqueue_due 要求 schedule IS NOT NULL）→ 不报。"""
    repo = _repo(tmp_path, caa="name: caa\nparked: true\n")
    _fake_inventory(monkeypatch, {"caa": ("", True)})
    assert inspector.inventory_mismatches("postgres://x", repo) == []


def test_scheduled_in_git_but_disabled_in_db_is_flagged(tmp_path, monkeypatch):
    repo = _repo(tmp_path, gdacs='name: gdacs\nschedule: "34 5 * * *"\n')
    _fake_inventory(monkeypatch, {"gdacs": ("34 5 * * *", False)})
    out = inspector.inventory_mismatches("postgres://x", repo)
    assert [o["kind"] for o in out] == ["scheduled-but-disabled-in-db"]


def test_schedule_text_drift_is_flagged(tmp_path, monkeypatch):
    repo = _repo(tmp_path, gdacs='name: gdacs\nschedule: "34 5 * * *"\n')
    _fake_inventory(monkeypatch, {"gdacs": ("0 5 * * *", True)})
    out = inspector.inventory_mismatches("postgres://x", repo)
    assert [o["kind"] for o in out] == ["schedule-drift"]


def test_consistent_pairs_are_silent(tmp_path, monkeypatch):
    repo = _repo(
        tmp_path,
        a='name: a\nschedule: "0 4 * * *"\n',
        b="name: b\nparked: true\n",
    )
    _fake_inventory(monkeypatch, {"a": ("0 4 * * *", True), "b": ("", True)})
    assert inspector.inventory_mismatches("postgres://x", repo) == []


def test_missing_inventory_means_cannot_check_not_clean():
    """没有 DSN 时返回空 —— 调用方必须自己决定怎么表述，不能当成「没问题」。"""
    assert inspector.inventory_mismatches("", Path("/tmp")) == []


def test_psql_failure_degrades_without_raising(tmp_path, monkeypatch, capsys):
    import subprocess

    def fail(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr(inspector.subprocess, "run", fail)
    repo = _repo(tmp_path, a='name: a\nschedule: "0 4 * * *"\n')
    assert inspector.inventory_mismatches("postgres://x", repo) == []
    assert "skipped" in capsys.readouterr().err


def test_unknown_sources_are_not_reported(tmp_path, monkeypatch):
    """清单里没有的源由 unregistered 负责，这里不重复报。"""
    repo = _repo(tmp_path, ghost='name: ghost\nschedule: "0 4 * * *"\n')
    _fake_inventory(monkeypatch, {})
    assert inspector.inventory_mismatches("postgres://x", repo) == []


def test_mismatch_produces_a_ticket_without_shadowing_stronger_verdicts(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from fd_industry_data.health.config import DEFAULTS

    repo = _repo(tmp_path, inmetals="name: inmetals\nparked: true\ndata_period: day\n")
    _fake_inventory(monkeypatch, {"inmetals": ("51 4 * * *", True)})
    plan = inspector.plan_tickets({}, repo, DEFAULTS,
                                  datetime(2026, 11, 9, tzinfo=timezone.utc),
                                  dsn="postgres://x")
    assert plan.inventory_mismatches
    traces = [e.verdict.rule_trace for e in plan.emit if e.source == "inmetals"]
    assert any(t.startswith("inventory:") for t in traces)
