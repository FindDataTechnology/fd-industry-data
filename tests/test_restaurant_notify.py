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


def test_collect_fired_dedupes_by_rule_and_concept():
    runs = [_run("r1", "c1"), _run("r1", "c1"), _run("r1", "c2"),
            _run("r2", "c1", verdict="quiet")]
    keys = sorted(x["key"] for x in restaurant.collect_fired(runs))
    assert keys == ["r1|c1", "r1|c2"]


def test_first_sighting_alerts_once(smtp_env, tmp_path):
    smtp = _OkSMTP("h", 465)
    receipt = restaurant.deliver_fired(
        restaurant.collect_fired([_run("r1", "c1")]), _cfg(), dry_run=False,
        now=ASOF, state_path=tmp_path / "state.json",
        smtp_factory=lambda h, p: smtp)
    assert receipt["sent"] == 1 and receipt["alerts"] == ["r1|c1"]
    assert len(smtp.sent) == 1


def test_unchanged_set_sends_nothing(smtp_env, tmp_path):
    """同一条越阈第二天不该再发——这是本文件存在的理由。"""
    state = tmp_path / "state.json"
    smtp_a = _OkSMTP("h", 465)
    restaurant.deliver_fired(restaurant.collect_fired([_run("r1", "c1")]), _cfg(),
                             dry_run=False, now=ASOF, state_path=state,
                             smtp_factory=lambda h, p: smtp_a)
    smtp_b = _OkSMTP("h", 465)
    receipt = restaurant.deliver_fired(restaurant.collect_fired([_run("r1", "c1")]), _cfg(),
                                       dry_run=False, now=ASOF, state_path=state,
                                       smtp_factory=lambda h, p: smtp_b)
    assert receipt["sent"] == 0
    assert smtp_b.sent == []


def test_clearing_a_firing_sends_a_recovery(smtp_env, tmp_path):
    state = tmp_path / "state.json"
    restaurant.deliver_fired(restaurant.collect_fired([_run("r1", "c1")]), _cfg(),
                             dry_run=False, now=ASOF, state_path=state,
                             smtp_factory=lambda h, p: _OkSMTP(h, p))
    smtp = _OkSMTP("h", 465)
    receipt = restaurant.deliver_fired([], _cfg(), dry_run=False, now=ASOF,
                                       state_path=state, smtp_factory=lambda h, p: smtp)
    assert receipt["recovers"] == ["r1|c1"]
    assert receipt["sent"] == 1
    assert "恢复" in smtp.sent[0]["Subject"]


def test_no_smtp_config_skips_with_reason(monkeypatch, tmp_path):
    for k in ("FD_ALERT_SMTP_HOST", "FD_ALERT_SMTP_FROM", "FD_ALERT_SMTP_TO"):
        monkeypatch.delenv(k, raising=False)
    receipt = restaurant.deliver_fired(
        restaurant.collect_fired([_run("r1", "c1")]), _cfg(), dry_run=False,
        now=ASOF, state_path=tmp_path / "s.json")
    assert receipt["sent"] == 0
    assert "no-smtp-config" in receipt["reason"]


def test_dry_run_sends_nothing_and_keeps_state_empty(smtp_env, tmp_path):
    state = tmp_path / "s.json"
    receipt = restaurant.deliver_fired(
        restaurant.collect_fired([_run("r1", "c1")]), _cfg(), dry_run=True,
        now=ASOF, state_path=state)
    assert receipt["skipped"] == 1
    assert not state.exists()


def test_single_failure_does_not_block_batch(smtp_env, tmp_path):
    calls = {"n": 0}

    def factory(h, p):
        calls["n"] += 1
        return _BoomSMTP(h, p) if calls["n"] == 1 else _OkSMTP(h, p)

    receipt = restaurant.deliver_fired(
        restaurant.collect_fired([_run("r1", "c1"), _run("r1", "c2")]), _cfg(),
        dry_run=False, now=ASOF, state_path=tmp_path / "s.json", smtp_factory=factory)
    assert receipt["failed"] == 1 and receipt["sent"] == 1


def test_receipt_is_json_serialisable(smtp_env, tmp_path):
    receipt = restaurant.deliver_fired(
        restaurant.collect_fired([_run("r1", "c1")]), _cfg(), dry_run=False,
        now=ASOF, state_path=tmp_path / "s.json",
        smtp_factory=lambda h, p: _OkSMTP(h, p))
    assert json.loads(json.dumps(receipt, default=str))["sent"] == 1
