"""Declaration-vs-behaviour tests (harden-crawl-delivery 3.4).

A `data_period_field` that the payload never yields is a promise the code
cannot keep: the watermark reads "unavailable" forever, indistinguishable from
"this source has no period". The check must fire on that, and must NOT fire on
old runs that never recorded a watermark state (they prove nothing).
"""
from __future__ import annotations

import pytest

from fd_industry_data.health import inspector
from fd_industry_data.health.config import DEFAULTS
from fd_industry_data.health.triage import Run


def _repo(tmp_path, source="alpha", field="period"):
    d = tmp_path / "spiders" / source
    d.mkdir(parents=True)
    extra = f"data_period_field: {field}\n" if field else ""
    (d / "manifest.yaml").write_text(
        f"name: {source}\ndata_period: month\n{extra}")
    return tmp_path


def _run(state):
    return Run(started_at="2026-11-09T00:00:00+00:00", status="success", rows=5,
               watermark_state=state)


def test_three_unavailable_runs_flag_a_mismatch(tmp_path):
    repo = _repo(tmp_path)
    runs = {"alpha": [_run("unavailable")] * 3}
    found = inspector.declaration_mismatches(runs, repo)
    assert len(found) == 1
    assert found[0]["field"] == "period"
    assert found[0]["states"] == ["unavailable"] * 3


def test_unknown_counts_as_mismatch_too(tmp_path):
    repo = _repo(tmp_path)
    runs = {"alpha": [_run("unknown"), _run("unknown"), _run("unavailable")]}
    assert len(inspector.declaration_mismatches(runs, repo)) == 1


def test_successful_watermarks_are_fine(tmp_path):
    repo = _repo(tmp_path)
    runs = {"alpha": [_run("ok")] * 3}
    assert inspector.declaration_mismatches(runs, repo) == []


def test_two_bad_runs_are_not_enough(tmp_path):
    repo = _repo(tmp_path)
    runs = {"alpha": [_run("unavailable"), _run("unavailable")]}
    assert inspector.declaration_mismatches(runs, repo) == []


def test_runs_without_a_recorded_state_prove_nothing(tmp_path):
    """旧镜像的 run 没有 watermark_state —— 不累计、不误判。"""
    repo = _repo(tmp_path)
    runs = {"alpha": [_run(None), _run(None), _run(None), _run(None)]}
    assert inspector.declaration_mismatches(runs, repo) == []


def test_mixed_old_and_new_only_counts_the_recorded_ones(tmp_path):
    repo = _repo(tmp_path)
    runs = {"alpha": [_run(None), _run(None), _run("unavailable"), _run("unavailable")]}
    assert inspector.declaration_mismatches(runs, repo) == []  # 只有 2 条可判


def test_a_recent_ok_run_clears_the_streak(tmp_path):
    repo = _repo(tmp_path)
    runs = {"alpha": [_run("unavailable"), _run("unavailable"), _run("ok")]}
    assert inspector.declaration_mismatches(runs, repo) == []


def test_no_declaration_is_not_a_mismatch(tmp_path):
    repo = _repo(tmp_path, field=None)
    runs = {"alpha": [_run("unavailable")] * 3}
    assert inspector.declaration_mismatches(runs, repo) == []


def test_mismatch_produces_a_structure_ticket(tmp_path):
    repo = _repo(tmp_path)
    runs = {"alpha": [_run("unavailable")] * 3}
    plan = inspector.plan_tickets(runs, repo, DEFAULTS,
                                  inspector.datetime(2026, 11, 9, tzinfo=inspector.timezone.utc))
    assert plan.declaration_mismatches
    emitted = [e for e in plan.emit if e.source == "alpha"]
    assert emitted and emitted[0].verdict.category == "structure"
    assert "data_period_field" in emitted[0].verdict.suggested_action
    assert emitted[0].verdict.rule_trace.startswith("declare:field-mismatch")


def test_fixing_the_declaration_clears_it(tmp_path):
    """把声明改回真实键后（下一轮记到 ok），不再出单。"""
    repo = _repo(tmp_path)
    inspector.plan_tickets({"alpha": [_run("unavailable")] * 3}, repo, DEFAULTS,
                           inspector.datetime(2026, 11, 9, tzinfo=inspector.timezone.utc))
    runs_after = {"alpha": [_run("unavailable")] * 3 + [_run("ok")]}
    plan2 = inspector.plan_tickets(runs_after, repo, DEFAULTS,
                                   inspector.datetime(2026, 11, 10, tzinfo=inspector.timezone.utc))
    assert plan2.declaration_mismatches == []
