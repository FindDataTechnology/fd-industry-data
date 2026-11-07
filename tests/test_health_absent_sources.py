"""巡检面对账：在册但遥测窗内零运行的源（add-flower-vertical 6.1）。

钉的是三条**会把静默当成健康**的默认值：

1. **巡检面 ≠ 遥测里出现过的源**。原实现 ``plan_tickets`` 只遍历
   ``runs_by_source``，所以一个源只要在回看窗（30 天）内一次都没跑，就**从巡
   检面上彻底消失**——年频源（AIPH，370 天容忍窗）上线 31 天后必然走到这一步。
   消失比误报更危险：没人会去修一个不在名单上的源。
2. **「窗内本就不该有运行」不能当故障**：``year`` 期的容忍窗 370 天 > 回看窗
   30 天，据此出单是把口径不足伪造成故障。故这类只报告、永不出单。
3. **默认零回归**：``absent_source_policy`` 缺省 ``report``，既有各线行为不变；
   只有显式改成 ``ticket`` 才为「该跑却没跑」的源出 contract 工单。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fd_industry_data.health import inspector, triage  # noqa: E402
from fd_industry_data.health.config import HealthConfig  # noqa: E402

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


def _repo(tmp_path: Path, *, sources: dict[str, dict]) -> Path:
    for name, manifest in sources.items():
        d = tmp_path / "spiders" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return tmp_path


def _run(hours_ago: float, status: str = "success", rows: int = 10) -> triage.Run:
    from datetime import timedelta
    ts = (NOW - timedelta(hours=hours_ago)).isoformat()
    return triage.Run(started_at=ts, status=status, rows=rows, error="")


def test_scheduled_sources_excludes_parked_and_unscheduled(tmp_path):
    repo = _repo(tmp_path, sources={
        "daily-live": {"schedule": "15 0 * * *", "data_period": "day"},
        "yearly-live": {"schedule": "42 0 12 1 *", "data_period": "year"},
        "parked-one": {"schedule": "", "data_period": "day", "parked": True},
        "dormant": {"data_period": "day"},
    })
    got = inspector.scheduled_sources(repo)
    assert set(got) == {"daily-live", "yearly-live"}
    assert got["yearly-live"]["data_period"] == "year"


def test_reconcile_splits_two_reasons(tmp_path):
    repo = _repo(tmp_path, sources={
        "daily-live": {"schedule": "15 0 * * *", "data_period": "day"},
        "yearly-live": {"schedule": "42 0 12 1 *", "data_period": "year"},
    })
    absent = {a["source"]: a["reason"] for a in
              inspector.reconcile_absent({}, repo, HealthConfig())}
    # 30 天回看窗 vs day(72h) / year(8880h) 容忍窗
    assert absent["daily-live"] == "no-run-in-window"
    assert absent["yearly-live"] == "period-longer-than-window"


def test_default_policy_reports_but_emits_nothing(tmp_path):
    repo = _repo(tmp_path, sources={"daily-live": {"schedule": "15 0 * * *", "data_period": "day"}})
    plan = inspector.plan_tickets({}, repo, HealthConfig(), NOW)
    assert plan.emit == [], "默认 report 策略不得出单（零回归）"
    assert [a["source"] for a in plan.absent] == ["daily-live"]


def test_ticket_policy_emits_contract_for_absent_daily_source(tmp_path):
    repo = _repo(tmp_path, sources={"daily-live": {"schedule": "15 0 * * *", "data_period": "day"}})
    cfg = HealthConfig(absent_source_policy="ticket")
    plan = inspector.plan_tickets({}, repo, cfg, NOW)
    assert len(plan.emit) == 1
    emit = plan.emit[0]
    assert emit.source == "daily-live"
    assert emit.verdict.category == "contract"
    assert emit.verdict.needs_human is False
    assert "rule0:scheduled-but-never-ran" in emit.verdict.rule_trace


def test_ticket_policy_never_emits_for_year_source(tmp_path):
    """年频源在 30 天窗内零运行是**正常**，任何策略下都不出单。"""
    repo = _repo(tmp_path, sources={"yearly-live": {"schedule": "42 0 12 1 *", "data_period": "year"}})
    plan = inspector.plan_tickets({}, repo, HealthConfig(absent_source_policy="ticket"), NOW)
    assert plan.emit == []
    reasons = {a["source"]: a["reason"] for a in plan.absent}
    assert reasons["yearly-live"] == "period-longer-than-window"


def test_sources_with_runs_are_unaffected(tmp_path):
    """有遥测的源走原判定链：健康仍健康、静默仍静默（规则 1b 不被规则 0 抢走）。"""
    repo = _repo(tmp_path, sources={
        "fresh": {"schedule": "15 0 * * *", "data_period": "day"},
        "silent": {"schedule": "15 0 * * *", "data_period": "day"},
    })
    runs = {"fresh": [_run(2)], "silent": [_run(24 * 10)]}
    plan = inspector.plan_tickets(runs, repo, HealthConfig(absent_source_policy="ticket"), NOW)
    by_source = {e.source: e for e in plan.emit}
    assert "fresh" not in by_source
    assert by_source["silent"].verdict.category == "contract"
    assert "rule1b" in by_source["silent"].verdict.rule_trace
    assert plan.absent == []


def test_excluded_sources_stay_excluded(tmp_path):
    repo = _repo(tmp_path, sources={"daily-live": {"schedule": "15 0 * * *", "data_period": "day"}})
    cfg = HealthConfig(absent_source_policy="ticket", excluded_sources=("daily-live",))
    plan = inspector.plan_tickets({}, repo, cfg, NOW)
    assert plan.emit == [] and plan.absent == []
