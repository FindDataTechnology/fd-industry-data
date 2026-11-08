"""告警公共内核的契约（add-banking-source-alerting 1.x）。

钉四条会静默出错的语义：

1. **缺数 ≠ 恢复**：``triggered=None`` 判 ``insufficient_data``，不改状态、不发 resolved。
   否则一次缺数就发出假恢复通知。
2. **同一判据期次不重复**：同 key 持续越线只留一条 firing，不刷新、不重复通知。
3. **方向由调用方给**：内核只认 ``triggered``，不拿比较结果当告警条件
   （踩过：资本充足率 13.77 高于红线 10.5 被判成 firing）。
4. **通知失败隔离**：单条失败不阻塞同批；无通道记 ``skipped`` 而不是成功。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from fd_industry_data.alerts_core import (
    FIRING, INSUFFICIENT, OK, QUIET, RESOLVED, RuleState,
    append_events, compare, dispatch, evaluate_event, evaluate_threshold,
    load_state, read_events, save_state, smtp_config_from_env,
)
from fd_industry_data.alerts_core.notify import SmtpConfig

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


# --- 阈值状态机 ---------------------------------------------------------------

def test_quiet_to_firing_then_same_key_is_silent():
    st = RuleState()
    st, ev, v = evaluate_threshold(st, triggered=True, key="2025-12-31", at=NOW)
    assert (v, len(ev), st.state) == (FIRING, 1, FIRING)
    st, ev, v = evaluate_threshold(st, triggered=True, key="2025-12-31", at=NOW)
    assert (v, len(ev)) == (FIRING, 0), "同一报告期不得重复告警"
    assert st.episode == 1


def test_new_key_while_firing_is_a_new_episode():
    st = RuleState()
    st, _, _ = evaluate_threshold(st, triggered=True, key="2025-06-30", at=NOW)
    st, ev, v = evaluate_threshold(st, triggered=True, key="2025-12-31", at=NOW)
    assert (v, len(ev), st.episode) == (FIRING, 1, 2)


def test_firing_to_ok_emits_resolved_once():
    st = RuleState()
    st, _, _ = evaluate_threshold(st, triggered=True, key="2025-12-31", at=NOW)
    st, ev, v = evaluate_threshold(st, triggered=False, key="2026-03-31", at=NOW)
    assert v == RESOLVED and len(ev) == 1 and st.state == QUIET
    st, ev, v = evaluate_threshold(st, triggered=False, key="2026-06-30", at=NOW)
    assert v == OK and ev == [], "恢复只发一次"


def test_missing_value_is_not_recovery():
    """缺数不是恢复：状态不变、不发 resolved。"""
    st = RuleState()
    st, _, _ = evaluate_threshold(st, triggered=True, key="2025-12-31", at=NOW)
    st, ev, v = evaluate_threshold(st, triggered=None, key="2026-03-31", at=NOW)
    assert v == INSUFFICIENT and ev == [] and st.state == FIRING


def test_missing_value_does_not_mark_quiet_as_firing():
    st = RuleState()
    st, ev, v = evaluate_threshold(st, triggered=None, key="2026-03-31", at=NOW)
    assert (v, ev, st.state) == (INSUFFICIENT, [], QUIET)


def test_compare_direction_and_unknown_operator():
    assert compare(13.77, ">=", 10.5) is True
    assert compare(9.0, ">=", 10.5) is False
    with pytest.raises(ValueError):
        compare(1.0, "~=", 1.0)


# --- 事件规则 -----------------------------------------------------------------

def test_event_emits_once_per_new_key():
    st = RuleState()
    st, ev, v = evaluate_event(st, new_keys=["d1", "d2"], at=NOW)
    assert [e["key"] for e in ev] == ["d1", "d2"] and v == FIRING
    st, ev, _ = evaluate_event(st, new_keys=["d2", "d3"], at=NOW)
    assert [e["key"] for e in ev] == ["d3"]


def test_event_seen_list_is_bounded():
    st = RuleState()
    for i in range(10):
        st, _, _ = evaluate_event(st, new_keys=[f"k{i}"], at=NOW, max_seen=3)
    assert len(st.seen) == 3, "seen 必须有界，否则状态文件无限膨胀"


def test_event_never_resolves():
    st = RuleState()
    st, _, _ = evaluate_event(st, new_keys=["d1"], at=NOW)
    st, ev, _ = evaluate_event(st, new_keys=[], at=NOW)
    assert ev == [], "事件是已发生的事实，不产生 resolved"


# --- 留痕幂等 -----------------------------------------------------------------

def test_ledger_is_idempotent(tmp_path):
    p = tmp_path / "events.jsonl"
    ev = [{"rule_id": "r", "kind": "firing", "key": "2025-12-31", "at": NOW.isoformat()}]
    assert append_events(p, ev) == 1
    assert append_events(p, ev) == 0, "重跑不得长出重复行"
    assert len(read_events(p)) == 1


def test_ledger_tolerates_bad_lines(tmp_path):
    p = tmp_path / "events.jsonl"
    p.write_text('{"rule_id":"r","kind":"firing","key":"k"}\n{not json}\n', encoding="utf-8")
    assert len(read_events(p)) == 1


def test_state_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    save_state(p, {"r:600036": RuleState(state=FIRING, episode=2, last_key="2025-12-31").to_dict()})
    got = load_state(p)
    assert RuleState.from_dict(got["r:600036"]).episode == 2
    assert load_state(tmp_path / "missing.json") == {}


# --- 通知 ---------------------------------------------------------------------

def test_smtp_config_absent_without_required_env():
    assert smtp_config_from_env({}) is None
    assert smtp_config_from_env({"FD_ALERT_SMTP_HOST": "h"}) is None


def test_smtp_config_parses_recipients():
    cfg = smtp_config_from_env({"FD_ALERT_SMTP_HOST": "h", "FD_ALERT_SMTP_FROM": "a@b",
                                "FD_ALERT_SMTP_TO": "x@y, z@w", "FD_ALERT_SMTP_PORT": "465"})
    assert cfg.recipients == ["x@y", "z@w"] and cfg.port == 465


class _Boom:
    def __init__(self, host, port):
        raise RuntimeError("smtp down")


class _Ok:
    def __init__(self, host, port):
        self.sent = 0

    def starttls(self):
        pass

    def login(self, u, p):
        pass

    def send_message(self, m):
        self.sent += 1

    def quit(self):
        pass


def test_dispatch_isolates_failures():
    cfg = SmtpConfig(host="h", port=25, user="", password="", sender="a@b",
                     recipients=["x@y"], use_tls=False)
    msgs = [{"rule_id": "r1", "subject": "s", "body": "b"},
            {"rule_id": "r2", "subject": "s", "body": "b"}]
    calls = {"n": 0}

    def factory(host, port):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Boom(host, port)
        return _Ok(host, port)

    res = dispatch(msgs, cfg, smtp_factory=factory)
    assert res["sent"] == 1 and res["failed"] == 1
    assert res["failures"][0]["rule_id"] == "r1", "失败要指明是哪条"


def test_dispatch_without_config_is_skipped_not_success():
    res = dispatch([{"rule_id": "r", "subject": "s", "body": "b"}], None)
    assert res["skipped"] is True and res["sent"] == 0
