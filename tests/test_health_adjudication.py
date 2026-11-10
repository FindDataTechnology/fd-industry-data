"""裁决通道：manual 重复工单升级、卷宗幂等、冻结与裁决台账。全离线。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.health import adjudication, triage  # noqa: E402
from fd_industry_data.health import inspector  # noqa: E402
from fd_industry_data.health.ticket import (  # noqa: E402
    dump_ticket,
    new_ticket,
    set_terminal,
)

NOW = "2026-11-06T03:00:00+00:00"


def _mk_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    tickets = repo / "reports" / "health-tickets"
    tickets.mkdir(parents=True)
    for slug in ("alpha", "beta"):
        d = repo / "spiders" / slug
        d.mkdir(parents=True)
        (d / "manifest.yaml").write_text(yaml.safe_dump({
            "version": "1", "name": slug, "schedule": "0 4 * * *",
            "functions": [{"command": f"run_{slug}", "frequency": "daily"}],
        }))
    return repo, tickets


def _mk_ticket(tickets: Path, source: str, day: str, *, terminal: str | None = "manual",
               note: str = "rule2:insufficient-evidence tail_failed=0",
               last_success: str | None = "2026-10-02 07:05:10.901+00",
               category: str | None = None):
    created = f"{day[:4]}-{day[4:6]}-{day[6:]}T01:00:00+00:00"
    doc = new_ticket(
        source=source, unit=f"spiders/{source}/",
        evidence={"notes": note, "last_success": (
            {"started_at": last_success, "rows": 3} if last_success else None)},
        verify_commands=["echo"],
        category=category, needs_human=category is None,
        created_at=created,
    )
    if terminal:
        # 夹具构造既有状态（closed 只可能来自人工合并）
        set_terminal(doc, terminal, human=(terminal == 'closed'))
    path = tickets / f"{doc['ticket_id']}.yaml"
    path.write_text(dump_ticket(doc), encoding="utf-8")
    return path


# ── 升级触发 ───────────────────────────────────────────────────────────────

def test_second_manual_ticket_escalates(tmp_path):
    repo, tickets = _mk_repo(tmp_path)
    _mk_ticket(tickets, "alpha", "20261101")
    result = adjudication.scan_escalations(tickets, repo, now=NOW)
    assert result["escalated_now"] == []          # 第 1 张不升级
    _mk_ticket(tickets, "alpha", "20261103")
    result = adjudication.scan_escalations(tickets, repo, now=NOW)
    assert result["escalated_now"] == ["alpha"]   # 第 2 张触发升级
    queue = adjudication.load_queue(repo)
    assert queue["threshold"] == 2
    entry = queue["entries"][0]
    assert entry["source"] == "alpha"
    assert entry["status"] == "escalated"
    assert entry["escalated_at"] == NOW
    assert entry["manual_tickets"] == 2
    assert len(entry["manual_ticket_ids"]) == 2
    assert adjudication.case_path(repo, "alpha").is_file()


def test_single_manual_and_other_terminals_do_not_escalate(tmp_path):
    repo, tickets = _mk_repo(tmp_path)
    _mk_ticket(tickets, "beta", "20261101")                    # 1 张 manual
    _mk_ticket(tickets, "beta", "20261102", terminal="closed")  # closed 不算
    result = adjudication.scan_escalations(tickets, repo, now=NOW)
    assert result["escalated_now"] == []
    assert adjudication.load_queue(repo)["entries"] == []


# ── 幂等 ──────────────────────────────────────────────────────────────────

def test_scan_is_idempotent(tmp_path):
    repo, tickets = _mk_repo(tmp_path)
    _mk_ticket(tickets, "alpha", "20261101")
    _mk_ticket(tickets, "alpha", "20261103")
    first = adjudication.scan_escalations(tickets, repo, now=NOW)
    assert first["escalated_now"] == ["alpha"]
    case_text = adjudication.case_path(repo, "alpha").read_text(encoding="utf-8")

    second = adjudication.scan_escalations(tickets, repo, now=NOW)
    assert second["escalated_now"] == []                 # 不重复升级
    assert second["already"] == ["alpha"]
    queue = adjudication.load_queue(repo)
    assert len(queue["entries"]) == 1                    # 不重复建条目
    assert queue["entries"][0]["escalated_at"] == NOW    # escalated_at 不漂移
    assert queue["entries"][0]["manual_tickets"] == 2
    # 卷宗「更新不重建」：路径稳定、内容确定性重建
    assert adjudication.case_path(repo, "alpha").read_text(encoding="utf-8") == case_text


def test_scan_refreshes_case_when_new_manual_lands(tmp_path):
    repo, tickets = _mk_repo(tmp_path)
    _mk_ticket(tickets, "alpha", "20261101")
    _mk_ticket(tickets, "alpha", "20261103")
    adjudication.scan_escalations(tickets, repo, now=NOW)
    _mk_ticket(tickets, "alpha", "20261105")
    adjudication.scan_escalations(tickets, repo, now=NOW)
    entry = adjudication.load_queue(repo)["entries"][0]
    assert entry["manual_tickets"] == 3
    case_text = adjudication.case_path(repo, "alpha").read_text(encoding="utf-8")
    assert "20261105-alpha" in case_text


# ── 卷宗内容 ──────────────────────────────────────────────────────────────

def test_case_file_content_requirements(tmp_path):
    repo, tickets = _mk_repo(tmp_path)
    _mk_ticket(tickets, "alpha", "20261101")
    _mk_ticket(tickets, "alpha", "20261103")
    adjudication.scan_escalations(tickets, repo, now=NOW)
    text = adjudication.case_path(repo, "alpha").read_text(encoding="utf-8")
    assert "# 裁决卷宗：alpha" in text
    assert "20261101-alpha" in text and "20261103-alpha" in text       # 全部工单史
    assert "2026-10-02 07:05:10.901+00" in text                        # 最近一次成功
    assert "rule2:insufficient-evidence x2" in text                    # 根因分类
    for option in ("repair", "park", "retire"):                        # 三选项依据
        assert f"### {option}" in text
    assert "manifest schedule='0 4 * * *'" in text                     # 调度面事实


def test_case_file_marks_unknown_last_success(tmp_path):
    repo, tickets = _mk_repo(tmp_path)
    _mk_ticket(tickets, "alpha", "20261101", last_success=None)
    _mk_ticket(tickets, "alpha", "20261103", last_success=None)
    adjudication.scan_escalations(tickets, repo, now=NOW)
    text = adjudication.case_path(repo, "alpha").read_text(encoding="utf-8")
    assert "最近一次成功运行: unknown" in text


def test_case_uses_manifest_facts_for_unscheduled_source(tmp_path):
    repo, tickets = _mk_repo(tmp_path)
    _mk_ticket(tickets, "gamma", "20261101")
    _mk_ticket(tickets, "gamma", "20261103")
    adjudication.scan_escalations(tickets, repo, now=NOW)
    text = adjudication.case_path(repo, "gamma").read_text(encoding="utf-8")
    assert "manifest 无 schedule" in text


# ── 冻结：升级源不再派发新工单，在途不受影响 ────────────────────────────────

def _plan(*sources: str) -> inspector.Plan:
    return inspector.Plan(
        emit=[inspector.Emit(source=s, unit=f"spiders/{s}/", verdict=None, runs=[])
              for s in sources],
        deferred=[], healthy=0, skipped_unknown_period=[])


def test_apply_freeze_blocks_new_tickets_for_escalated_source(tmp_path):
    repo, tickets = _mk_repo(tmp_path)
    _mk_ticket(tickets, "alpha", "20261101")
    _mk_ticket(tickets, "alpha", "20261103")
    adjudication.scan_escalations(tickets, repo, now=NOW)
    assert adjudication.frozen_sources(repo) == {"alpha"}
    assert adjudication.is_frozen("alpha", repo)

    plan = _plan("alpha", "beta")
    blocked = adjudication.apply_freeze(plan, repo)
    assert blocked == ["alpha"]
    assert [e.source for e in plan.emit] == ["beta"]          # 新单只留未冻结源
    assert plan.deferred == [{
        "source": "alpha", "category": None, "rule": None,
        "reason": adjudication.FREEZE_DEFER_REASON,
    }]


def test_apply_freeze_deferral_carries_verdict(tmp_path):
    """冻结顺延也要带判词（category/rule），与日限额/去重那几条同口径。

    2026-11-11 实测：这条路径原本只写 {source, reason}，导致观察脚本的指纹 ③
    「deferred 带 category/rule」长期 ✗——把"新代码在跑"的自检信号搞成常红。
    """
    repo, tickets = _mk_repo(tmp_path)
    _mk_ticket(tickets, "alpha", "20261101")
    _mk_ticket(tickets, "alpha", "20261103")
    adjudication.scan_escalations(tickets, repo, now=NOW)

    plan = inspector.Plan(
        emit=[inspector.Emit(source="alpha", unit="spiders/alpha/",
                             verdict=triage.Verdict(category="structure", needs_human=False,
                                                    suspected_anti_bot=False,
                                                    suggested_action="x",
                                                    rule_trace="rule1c:success-but-too-few-rows"),
                             runs=[])],
        deferred=[], healthy=0, skipped_unknown_period=[])
    adjudication.apply_freeze(plan, repo)
    entry = plan.deferred[0]
    assert entry["category"] == "structure"
    assert entry["rule"] == "rule1c", "取 rule_trace 冒号前那段，与 write_plan 一致"
    assert entry["reason"] == adjudication.FREEZE_DEFER_REASON


def test_apply_freeze_does_not_touch_inflight_tickets(tmp_path):
    repo, tickets = _mk_repo(tmp_path)
    in_flight = _mk_ticket(tickets, "alpha", "20261101", terminal=None)  # 在途单
    _mk_ticket(tickets, "alpha", "20261103")
    adjudication.scan_escalations(tickets, repo, now=NOW)
    adjudication.apply_freeze(_plan("alpha"), repo)
    # 在途工单文件原样保留（终态/内容不受冻结影响）
    import yaml as _yaml
    doc = _yaml.safe_load(in_flight.read_text(encoding="utf-8"))
    assert doc["terminal"] is None


def test_apply_freeze_noop_without_queue(tmp_path):
    repo, _ = _mk_repo(tmp_path)
    plan = _plan("alpha")
    assert adjudication.apply_freeze(plan, repo) == []
    assert len(plan.emit) == 1


def test_repair_unfreezes_park_and_retire_stay_frozen(tmp_path):
    repo, tickets = _mk_repo(tmp_path)
    for src in ("alpha", "beta"):
        _mk_ticket(tickets, src, "20261101")
        _mk_ticket(tickets, src, "20261103")
    adjudication.scan_escalations(tickets, repo, now=NOW)

    adjudication.record_decision(repo, "alpha", "repair", note="fixed", now=NOW)
    adjudication.record_decision(repo, "beta", "park", note="pilot hold", now=NOW)
    assert adjudication.is_frozen("alpha", repo) is False     # repair 解冻
    assert adjudication.is_frozen("beta", repo) is True       # park 维持冻结


# ── 裁决执行与台账 ─────────────────────────────────────────────────────────

def test_record_decision_park_mutates_manifest(tmp_path):
    repo, tickets = _mk_repo(tmp_path)
    _mk_ticket(tickets, "alpha", "20261101")
    _mk_ticket(tickets, "alpha", "20261103")
    adjudication.scan_escalations(tickets, repo, now=NOW)

    result = adjudication.record_decision(repo, "alpha", "park", note="停滚动出单", now=NOW)
    entry = result["entry"]
    assert entry["status"] == "parked" and entry["decision"] == "park"
    assert entry["decided_at"] == NOW and entry["note"] == "停滚动出单"
    manifest = yaml.safe_load(
        (repo / "spiders" / "alpha" / "manifest.yaml").read_text(encoding="utf-8"))
    assert "schedule" not in manifest          # schedule 置空（摘除）
    assert manifest["parked"] is True          # parked 标记
    assert any("schedule removed" in a for a in result["actions"])


def test_record_decision_retire_prints_commands_without_moving(tmp_path):
    repo, tickets = _mk_repo(tmp_path)
    _mk_ticket(tickets, "alpha", "20261101")
    _mk_ticket(tickets, "alpha", "20261103")
    adjudication.scan_escalations(tickets, repo, now=NOW)

    result = adjudication.record_decision(repo, "alpha", "retire", note="退役", now=NOW)
    assert result["entry"]["status"] == "retired"
    assert (repo / "spiders" / "alpha").is_dir()               # 不移动目录
    manifest = yaml.safe_load(
        (repo / "spiders" / "alpha" / "manifest.yaml").read_text(encoding="utf-8"))
    assert "schedule" not in manifest                          # 摘 schedule
    assert "parked" not in manifest
    assert result["commands"] == [
        "mkdir -p archive/adjudication-20261106",
        "git mv spiders/alpha archive/adjudication-20261106/alpha",
    ]
    assert any("not executed" in a for a in result["actions"])


def test_record_decision_repair_keeps_manifest_and_closes_entry(tmp_path):
    repo, tickets = _mk_repo(tmp_path)
    _mk_ticket(tickets, "alpha", "20261101")
    _mk_ticket(tickets, "alpha", "20261103")
    adjudication.scan_escalations(tickets, repo, now=NOW)
    result = adjudication.record_decision(repo, "alpha", "repair", note="已修", now=NOW)
    assert result["entry"]["status"] == "repaired"
    manifest = yaml.safe_load(
        (repo / "spiders" / "alpha" / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["schedule"] == "0 4 * * *"                 # repair 不动调度
    assert result["commands"] == []


def test_ledger_is_append_only_machine_readable(tmp_path):
    repo, tickets = _mk_repo(tmp_path)
    for src in ("alpha", "beta"):
        _mk_ticket(tickets, src, "20261101")
        _mk_ticket(tickets, src, "20261103")
    adjudication.scan_escalations(tickets, repo, now=NOW)
    adjudication.record_decision(repo, "alpha", "repair", note="r1", now=NOW)
    adjudication.record_decision(repo, "beta", "park", note="p1", now=NOW)
    ledger = adjudication.load_ledger(repo)
    assert len(ledger["entries"]) == 2                          # 历史完整
    assert [e["decision"] for e in ledger["entries"]] == ["repair", "park"]
    first = ledger["entries"][0]
    assert {"at", "source", "decision", "note", "actions", "actor"} <= set(first)
    raw = json.loads(adjudication.ledger_path(repo).read_text(encoding="utf-8"))
    assert raw["entries"][0]["note"] == "r1"


def test_record_decision_rejects_bad_inputs(tmp_path):
    repo, tickets = _mk_repo(tmp_path)
    _mk_ticket(tickets, "alpha", "20261101")
    _mk_ticket(tickets, "alpha", "20261103")
    adjudication.scan_escalations(tickets, repo, now=NOW)

    with pytest.raises(ValueError, match="decision 非法"):
        adjudication.record_decision(repo, "alpha", "delete", now=NOW)
    with pytest.raises(ValueError, match="不在裁决队列"):
        adjudication.record_decision(repo, "ghost", "repair", now=NOW)
    adjudication.record_decision(repo, "alpha", "repair", now=NOW)
    with pytest.raises(ValueError, match="已裁决"):
        adjudication.record_decision(repo, "alpha", "park", now=NOW)  # 不可重复裁决


def test_scan_does_not_re_escalate_decided_source(tmp_path):
    repo, tickets = _mk_repo(tmp_path)
    _mk_ticket(tickets, "alpha", "20261101")
    _mk_ticket(tickets, "alpha", "20261103")
    adjudication.scan_escalations(tickets, repo, now=NOW)
    adjudication.record_decision(repo, "alpha", "repair", now=NOW)
    result = adjudication.scan_escalations(tickets, repo, now=NOW)
    assert result["escalated_now"] == []
    assert "alpha" in result["decided"]
    entry = adjudication.load_queue(repo)["entries"][0]
    assert entry["status"] == "repaired"
