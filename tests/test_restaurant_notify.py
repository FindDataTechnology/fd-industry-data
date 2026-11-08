"""餐饮/酒店线投递的边界语义（openspec: wire-alert-delivery §3）。

这两条线共用 `restaurant_alerts.py`，但**没有花卉那样的冷却状态机**——`fired` 每次全量重算。
所以投递必须自己判边界，否则同一条越阈每天发一遍。钉四条：

1. **只在集合变化时发**：新增 → 告警；消失 → 恢复；未变化 → 一条不发。
2. **无通道 = skipped + 原因**，不是成功。
3. **单条失败不阻塞同批**，逐条落回执（调用方据此 exit 4）。
4. **dry-run 不发、也不推进状态**。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location(
    "restaurant_alerts", REPO / "scripts" / "restaurant_alerts.py")
restaurant = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(restaurant)

ASOF = date(2026, 11, 8)


def _run(rule, concept, verdict="fired"):
    return {"rule": rule, "path": f"threshold-{rule}.json",
            "evidence": [{"concept": concept, "verdict": verdict,
                          "trigger_date": "2026-11-06", "trigger_value": 12.5,
                          "threshold": 10, "comparator": "gt"}]}


class _OkSMTP:
    def __init__(self, host, port):
        self.sent = []

    def starttls(self):
        pass

    def login(self, u, p):
        pass

    def send_message(self, m):
        self.sent.append(m)

    def quit(self):
        pass


class _BoomSMTP(_OkSMTP):
    def send_message(self, m):
        raise OSError("boom")


@pytest.fixture()
def smtp_env(monkeypatch):
    monkeypatch.setenv("FD_ALERT_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("FD_ALERT_SMTP_PORT", "465")
    monkeypatch.setenv("FD_ALERT_SMTP_SSL", "1")
    monkeypatch.setenv("FD_ALERT_SMTP_TLS", "0")
    monkeypatch.setenv("FD_ALERT_SMTP_FROM", "support@finddatatech.cloud")
    monkeypatch.setenv("FD_ALERT_SMTP_USER", "u")
    monkeypatch.setenv("FD_ALERT_SMTP_PASS", "p")
    monkeypatch.delenv("FD_ALERT_SMTP_TO", raising=False)


def _cfg():
    return {"label": "餐饮线", "delivery": {"channel": "smtp", "to": ["ops@example.com"]}}


def test_collect_states_dedupes_and_keeps_three_states():
    runs = [_run("r1", "c1"), _run("r1", "c1"), _run("r1", "c2"),
            _run("r2", "c1", verdict="quiet"), _run("r3", "c1", verdict="n/a")]
    states = restaurant.collect_rule_states(runs)
    # 去重后仍保留三种状态：fired / quiet / unknown
    assert sorted(states) == ["r1|c1", "r1|c2", "r2|c1", "r3|c1"]
    assert states["r1|c1"]["state"] == "fired"
    assert states["r2|c1"]["state"] == "quiet"
    assert states["r3|c1"]["state"] == "unknown"


def test_first_sighting_alerts_once(smtp_env, tmp_path):
    smtp = _OkSMTP("h", 465)
    receipt = restaurant.deliver_transitions(
        restaurant.collect_rule_states([_run("r1", "c1")]), _cfg(), dry_run=False,
        now=ASOF, state_path=tmp_path / "state.json",
        smtp_factory=lambda h, p: smtp)
    assert receipt["sent"] == 1 and receipt["alerts"] == ["r1|c1"]
    assert len(smtp.sent) == 1


def test_unchanged_set_sends_nothing(smtp_env, tmp_path):
    """同一条越阈第二天不该再发——这是本文件存在的理由。"""
    state = tmp_path / "state.json"
    smtp_a = _OkSMTP("h", 465)
    restaurant.deliver_transitions(restaurant.collect_rule_states([_run("r1", "c1")]), _cfg(),
                             dry_run=False, now=ASOF, state_path=state,
                             smtp_factory=lambda h, p: smtp_a)
    smtp_b = _OkSMTP("h", 465)
    receipt = restaurant.deliver_transitions(restaurant.collect_rule_states([_run("r1", "c1")]), _cfg(),
                                       dry_run=False, now=ASOF, state_path=state,
                                       smtp_factory=lambda h, p: smtp_b)
    assert receipt["sent"] == 0
    assert smtp_b.sent == []


def test_clearing_a_firing_sends_a_recovery(smtp_env, tmp_path):
    state = tmp_path / "state.json"
    restaurant.deliver_transitions(restaurant.collect_rule_states([_run("r1", "c1")]), _cfg(),
                             dry_run=False, now=ASOF, state_path=state,
                             smtp_factory=lambda h, p: _OkSMTP(h, p))
    smtp = _OkSMTP("h", 465)
    receipt = restaurant.deliver_transitions(
        restaurant.collect_rule_states([_run("r1", "c1", verdict="quiet")]), _cfg(),
        dry_run=False, now=ASOF, state_path=state, smtp_factory=lambda h, p: smtp)
    assert receipt["recovers"] == ["r1|c1"]
    assert receipt["sent"] == 1
    assert "恢复" in smtp.sent[0]["Subject"]


def test_missing_data_is_not_a_recovery(smtp_env, tmp_path):
    """缺数（n/a）不是恢复：不发恢复邮件，且旧状态原样保留。"""
    state = tmp_path / "state.json"
    restaurant.deliver_transitions(restaurant.collect_rule_states([_run("r1", "c1")]), _cfg(),
                                   dry_run=False, now=ASOF, state_path=state,
                                   smtp_factory=lambda h, p: _OkSMTP(h, p))
    smtp = _OkSMTP("h", 465)
    receipt = restaurant.deliver_transitions(
        restaurant.collect_rule_states([_run("r1", "c1", verdict="n/a")]), _cfg(),
        dry_run=False, now=ASOF, state_path=state, smtp_factory=lambda h, p: smtp)
    assert receipt["recovers"] == []
    assert receipt["sent"] == 0
    assert receipt["unknown"] == ["r1|c1"]
    # 状态仍是 fired，所以下一次真恢复还能发得出
    assert json.loads(state.read_text())["rules"]["r1|c1"]["state"] == "fired"


def test_empty_evidence_is_not_a_recovery(smtp_env, tmp_path):
    """本轮什么都没评到，不能当成"全部恢复"。"""
    state = tmp_path / "state.json"
    restaurant.deliver_transitions(restaurant.collect_rule_states([_run("r1", "c1")]), _cfg(),
                                   dry_run=False, now=ASOF, state_path=state,
                                   smtp_factory=lambda h, p: _OkSMTP(h, p))
    receipt = restaurant.deliver_transitions({}, _cfg(), dry_run=False, now=ASOF,
                                             state_path=state,
                                             smtp_factory=lambda h, p: _OkSMTP(h, p))
    assert receipt["recovers"] == [] and receipt["sent"] == 0


def test_no_smtp_config_skips_with_reason(monkeypatch, tmp_path):
    for k in ("FD_ALERT_SMTP_HOST", "FD_ALERT_SMTP_FROM", "FD_ALERT_SMTP_TO"):
        monkeypatch.delenv(k, raising=False)
    receipt = restaurant.deliver_transitions(
        restaurant.collect_rule_states([_run("r1", "c1")]), _cfg(), dry_run=False,
        now=ASOF, state_path=tmp_path / "s.json")
    assert receipt["sent"] == 0
    assert "no-smtp-config" in receipt["reason"]


def test_dry_run_sends_nothing_and_keeps_state_empty(smtp_env, tmp_path):
    state = tmp_path / "s.json"
    receipt = restaurant.deliver_transitions(
        restaurant.collect_rule_states([_run("r1", "c1")]), _cfg(), dry_run=True,
        now=ASOF, state_path=state)
    assert receipt["skipped"] == 1
    assert not state.exists()


def test_single_failure_does_not_block_batch(smtp_env, tmp_path):
    calls = {"n": 0}

    def factory(h, p):
        calls["n"] += 1
        return _BoomSMTP(h, p) if calls["n"] == 1 else _OkSMTP(h, p)

    receipt = restaurant.deliver_transitions(
        restaurant.collect_rule_states([_run("r1", "c1"), _run("r1", "c2")]), _cfg(),
        dry_run=False, now=ASOF, state_path=tmp_path / "s.json", smtp_factory=factory)
    assert receipt["failed"] == 1 and receipt["sent"] == 1


def test_coverage_flags_a_mute_line(smtp_env, tmp_path):
    """全部规则 n/a = 这条线结构上不可能告警，必须显性化（mute），而不是安静跑绿。"""
    receipt = restaurant.deliver_transitions(
        restaurant.collect_rule_states([_run("r1", "c1", verdict="n/a"),
                                        _run("r2", "c2", verdict="n/a")]), _cfg(),
        dry_run=True, now=ASOF, state_path=tmp_path / "s.json")
    cov = receipt["coverage"]
    assert cov == {"rules": 2, "evaluable": 0, "unknown": 2, "mute": True}


def test_coverage_not_mute_when_any_rule_evaluates(smtp_env, tmp_path):
    receipt = restaurant.deliver_transitions(
        restaurant.collect_rule_states([_run("r1", "c1"),
                                        _run("r2", "c2", verdict="n/a")]), _cfg(),
        dry_run=True, now=ASOF, state_path=tmp_path / "s.json")
    assert receipt["coverage"]["mute"] is False
    assert receipt["coverage"]["evaluable"] == 1


def test_receipt_is_json_serialisable(smtp_env, tmp_path):
    receipt = restaurant.deliver_transitions(
        restaurant.collect_rule_states([_run("r1", "c1")]), _cfg(), dry_run=False,
        now=ASOF, state_path=tmp_path / "s.json",
        smtp_factory=lambda h, p: _OkSMTP(h, p))
    assert json.loads(json.dumps(receipt, default=str))["sent"] == 1


# ── 5. 历史越阈不是当期告警 ──────────────────────────────────────────


def _run_ev(rule, concept, verdict="fired", current=None, latest="2026-10"):
    ev = {"concept": concept, "verdict": verdict, "trigger_date": "2022-12",
          "trigger_value": -14.1, "threshold": 0.0, "comparator": "lt",
          "latest_period": latest}
    if current is not None:
        ev["current_fired"] = current
    return {"rule": rule, "path": f"threshold-{rule}.json", "evidence": [ev]}


def test_historical_crossing_is_stale_not_fired():
    """96 个月回放窗里"曾经越阈"不等于"现在越阈"——否则旧越阈永不恢复。"""
    states = restaurant.collect_rule_states([_run_ev("r1", "c1", current=False)])
    assert states["r1|c1"]["state"] == "stale"


def test_current_crossing_stays_fired():
    states = restaurant.collect_rule_states([_run_ev("r1", "c1", current=True)])
    assert states["r1|c1"]["state"] == "fired"


def test_stale_does_not_alert_and_recovers_a_previous_fired(smtp_env, tmp_path):
    state = tmp_path / "s.json"
    # 先让它处于 fired
    restaurant.deliver_transitions(
        restaurant.collect_rule_states([_run_ev("r1", "c1", current=True)]), _cfg(),
        dry_run=False, now=ASOF, state_path=state,
        smtp_factory=lambda h, p: _OkSMTP(h, p))
    # 下一轮变成历史越阈：应发"恢复"，不发新告警
    smtp = _OkSMTP("h", 465)
    receipt = restaurant.deliver_transitions(
        restaurant.collect_rule_states([_run_ev("r1", "c1", current=False)]), _cfg(),
        dry_run=False, now=ASOF, state_path=state, smtp_factory=lambda h, p: smtp)
    assert receipt["alerts"] == []
    assert receipt["recovers"] == ["r1|c1"]
    assert "恢复" in smtp.sent[0]["Subject"]


def test_stale_alone_sends_nothing(smtp_env, tmp_path):
    """从未告知过的旧越阈，不该在首次接线时被当成新告警发出去。"""
    receipt = restaurant.deliver_transitions(
        restaurant.collect_rule_states([_run_ev("r1", "c1", current=False)]), _cfg(),
        dry_run=False, now=ASOF, state_path=tmp_path / "s.json",
        smtp_factory=lambda h, p: _OkSMTP(h, p))
    assert receipt["sent"] == 0
