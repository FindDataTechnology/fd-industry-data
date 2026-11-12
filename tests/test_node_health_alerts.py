"""节点失联告警的语义（2026-11-12）。

起因是 2026-11-11 23:08 cheap-3 在 OS 层死掉后 **74 分钟无人知晓**。这组测钉四条
会静默出错或误判绿的语义——每条都对应一次真实可能发生的错误：

1. **NodeStatusUnknown 必须报**：本次就是这种形态（kubelet 不说话）。只判
   ``status == "False"`` 的写法会把这次漏掉，所以这里先用"Unknown 也 firing"钉死。
2. **读不到 API ≠ 全部恢复**：返回 ``insufficient_data``，**不发 resolved**，
   且进程以 exit 4 暴露"监控失明"。写反的后果是集群 API 挂掉时安静地宣布恢复。
3. **同一段失联只报一次**：持续 NotReady 的第二次巡检不再产生 firing（不重复发信）。
4. **恢复正常要发 resolved**：否则邮箱里只有"坏了"，没有"好了"。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scripts.node_health_alerts import (  # noqa: PLC2701 — 脚本与测同仓
    RULE_ID, compose_messages, evaluate,
)


def _node(name: str, status: str, reason: str = "") -> dict:
    return {
        "metadata": {"name": name},
        "status": {"conditions": [
            {"type": "Ready", "status": status, "reason": reason or status,
             "lastTransitionTime": "2026-11-11T23:08:17Z"},
        ]},
    }


NOW = datetime(2026, 11, 12, 0, 30, tzinfo=timezone.utc)


def test_node_status_unknown_is_firing():
    """2026-11-11 的真实形态：kubelet 不说话（Unknown），不是 NotReady。"""
    states, events, detail = evaluate([_node("cheap-3", "Unknown", "NodeStatusUnknown")], {}, NOW)
    assert [e["kind"] for e in events] == ["firing"]
    assert events[0]["rule_id"] == RULE_ID
    assert "NodeStatusUnknown" in events[0]["detail"]
    assert states[f"{RULE_ID}:cheap-3"].state == "firing"


def test_not_ready_is_firing_and_healthy_is_quiet():
    _, events, _ = evaluate([_node("cheap-4", "False", "KubeletNotReady")], {}, NOW)
    assert [e["kind"] for e in events] == ["firing"]

    _, quiet_events, detail = evaluate([_node("cheap-5", "True")], {}, NOW)
    assert quiet_events == []
    assert detail[0]["verdict"] == "quiet"


def test_blind_cluster_never_emits_resolved():
    """读不到节点列表时不能宣布恢复——这是本文件最重要的一条。"""
    states, events, detail = evaluate([_node("cheap-3", "Unknown")], {}, NOW)  # 先进入 firing
    assert states[f"{RULE_ID}:cheap-3"].state == "firing"

    new_states, events2, detail2 = evaluate(None, states, NOW)
    assert events2 == [], "失明不得产生任何事件（尤其不得发 resolved）"
    assert new_states[f"{RULE_ID}:cheap-3"].state == "firing", "状态必须原样保留"
    assert detail2[0]["verdict"] == "insufficient_data"
    assert detail2[0]["note"] == "blind"


def test_persistent_outage_notifies_once_then_resolves():
    states, events1, _ = evaluate([_node("cheap-3", "Unknown")], {}, NOW)
    assert len(events1) == 1

    states, events2, _ = evaluate([_node("cheap-3", "Unknown")], states, NOW)
    assert events2 == [], "持续失联不得重复发信"

    states, events3, _ = evaluate([_node("cheap-3", "True")], states, NOW)
    assert [e["kind"] for e in events3] == ["resolved"]

    # 恢复之后再挂：必须重新 firing（不是永久哑掉）
    _, events4, _ = evaluate([_node("cheap-3", "Unknown")], states, NOW)
    assert [e["kind"] for e in events4] == ["firing"]


def test_missing_ready_condition_is_treated_as_unhealthy():
    node = {"metadata": {"name": "cheap-9"}, "status": {"conditions": []}}
    _, events, detail = evaluate([node], {}, NOW)
    assert [e["kind"] for e in events] == ["firing"]
    assert detail[0]["reason"] == "NoReadyCondition"


def test_removed_node_keeps_state_without_fake_resolve():
    states, _, _ = evaluate([_node("cheap-3", "Unknown")], {}, NOW)
    new_states, events, detail = evaluate([_node("cheap-4", "True")], states, NOW)
    assert events == [], "节点从列表消失不等于恢复"
    assert new_states[f"{RULE_ID}:cheap-3"].state == "firing"


def test_message_subject_distinguishes_firing_and_recovery():
    states, events, detail = evaluate([_node("cheap-3", "Unknown", "NodeStatusUnknown")], {}, NOW)
    msgs = compose_messages(events, detail)
    assert msgs[0]["subject"].startswith("[节点失联]")
    assert "cheap-3" in msgs[0]["subject"]

    _, events2, detail2 = evaluate([_node("cheap-3", "True")], states, NOW)
    msgs2 = compose_messages(events2, detail2)
    assert msgs2[0]["subject"].startswith("[节点恢复]")


def test_state_round_trips_through_json(tmp_path):
    """落盘/复读必须是 JSON 可序列化的 dict，且复读后判定接得上。

    2026-11-12 首次在集群里跑就撞到 ``TypeError: Object of type RuleState is not
    JSON serializable``——单测当时不落盘，所以没盖住。这条把它钉死：
    存 ``to_dict()``、读回 ``from_dict()``，并且"已报过的失联"复读后不再重复发信。
    """
    import json

    from fd_industry_data.alerts_core import RuleState, load_state, save_state

    states, _, _ = evaluate([_node("cheap-3", "Unknown")], {}, NOW)
    path = tmp_path / "state.json"
    save_state(path, {k: v.to_dict() for k, v in states.items()})

    raw = json.loads(path.read_text(encoding="utf-8"))          # 可序列化
    assert raw[f"{RULE_ID}:cheap-3"]["state"] == "firing"

    reloaded = load_state(path)                                  # 纯 dict 回来
    _, events, _ = evaluate([_node("cheap-3", "Unknown")], reloaded, NOW)
    assert events == [], "复读后仍处于 firing，不该重复发信"
