"""节点告警的语义（2026-11-12 立；2026-11-13 加 DiskPressure 规则）。

起因是 2026-11-11 23:08 cheap-3 在 OS 层死掉后 **74 分钟无人知晓**。这组测先钉四条
会静默出错或误判绿的语义——每条都对应一次真实可能发生的错误：

1. **NodeStatusUnknown 必须报**：本次就是这种形态（kubelet 不说话）。只判
   ``status == "False"`` 的写法会把这次漏掉，所以这里先用"Unknown 也 firing"钉死。
2. **读不到 API ≠ 全部恢复**：返回 ``insufficient_data``，**不发 resolved**，
   且进程以 exit 4 暴露"监控失明"。写反的后果是集群 API 挂掉时安静地宣布恢复。
3. **同一段失联只报一次**：持续 NotReady 的第二次巡检不再产生 firing（不重复发信）。
4. **恢复正常要发 resolved**：否则邮箱里只有"坏了"，没有"好了"。

2026-11-13 补上后半部分：那天 `liuliangjkiimypbdzxa` **Ready=True 且 DiskPressure=True**，
facet 被驱逐、替代 pod 永久 Pending，**零告警**——因为当时只有 Ready 一条规则。新增的
盘压规则要钉住五点（每点都对应一个会静默出错的写法）：

5. **Ready=True 不能掩盖 DiskPressure=True**（那天的真实形态，也是本条规则的立项理由）。
6. **DiskPressure 缺失 ≠ 压力解除**：condition 缺失判 ``insufficient_data``，不伪造
   resolved（否则节点整机失联、conditions 变 Unknown 时会发一条假的"磁盘恢复"）。
7. **两条规则各自去重**：Ready 恢复不得顺手把盘压那条结掉。
8. **通知措辞与处置提示要分规则**：盘压那条的处置不是"救机器"，点错方向就白忙。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from fd_industry_data.alerts_core import RuleState
from scripts.node_health_alerts import (  # noqa: PLC2701 — 脚本与测同仓
    RULE_ID, RULE_ID_DISK, compose_messages, evaluate,
)


def _node(name: str, status: str, reason: str = "",
          disk: str | None = None, disk_msg: str = "") -> dict:
    """造节点。``disk=None`` 表示**不报** DiskPressure condition（缺失态）。"""
    conds = [{"type": "Ready", "status": status, "reason": reason or status,
              "lastTransitionTime": "2026-11-11T23:08:17Z"}]
    if disk is not None:
        conds.append({"type": "DiskPressure", "status": disk,
                      "reason": "KubeletHasDiskPressure" if disk == "True" else "KubeletHasNoDiskPressure",
                      "message": disk_msg,
                      "lastTransitionTime": "2026-11-13T12:00:00Z"})
    return {"metadata": {"name": name}, "status": {"conditions": conds}}


def _kinds(events: list[dict], rule: str | None = None) -> list[str]:
    return [e["kind"] for e in events if rule is None or e["rule_id"] == rule]


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


# ─── DiskPressure 规则（2026-11-13）──────────────────────────────────────────

def test_ready_true_with_disk_pressure_fires():
    """**本规则立项的真实形态**：Ready=True 不掩盖 DiskPressure=True。

    2026-11-13 12:00Z `liuliangjkiimypbdzxa` 就是这样：旧实现零告警，直到
    `fd-prod/facet` 被 evicted。这条测就是为了让那种"全绿但盘要满了"必然变红。
    """
    node = _node("liuliangjkiimypbdzxa", "True", disk="True",
                 disk_msg="kubelet has disk pressure")
    states, events, detail = evaluate([node], {}, NOW)

    assert _kinds(events, RULE_ID) == [], "Ready=True 不该报失联"
    assert _kinds(events, RULE_ID_DISK) == ["firing"], "Ready=True + DiskPressure=True 必须报盘压"
    assert states[f"{RULE_ID_DISK}:liuliangjkiimypbdzxa"].state == "firing"
    assert "DiskPressure=True" in events[0]["detail"]

    disk_details = [d for d in detail if d["rule_id"] == RULE_ID_DISK]
    assert disk_details[0]["verdict"] == "firing"
    assert disk_details[0]["disk_status"] == "True"


def test_disk_pressure_false_is_quiet():
    _, events, detail = evaluate([_node("cheap-4", "True", disk="False")], {}, NOW)
    assert events == []
    assert [d["verdict"] for d in detail if d["rule_id"] == RULE_ID_DISK] == ["quiet"]


def test_missing_disk_condition_never_fakes_resolve():
    """condition 缺失＝看不清，不是"压力解除"。

    节点整机失联时 conditions 会变 Unknown/消失，这时若把缺失当 False 就会发出
    一条假的"磁盘恢复"——而真正的告警应该是"节点失联"（由 Ready 规则报）。
    """
    node = _node("cheap-3", "True", disk="True")
    states, events1, _ = evaluate([node], {}, NOW)
    assert _kinds(events1, RULE_ID_DISK) == ["firing"]

    # 第二次：DiskPressure condition 整条消失（模拟整机失联/API 只回部分 condition）
    gone = _node("cheap-3", "True", disk=None)
    new_states, events2, detail2 = evaluate([gone], states, NOW)
    assert events2 == [], "condition 缺失不得产生任何事件（尤其不得发 resolved）"
    assert new_states[f"{RULE_ID_DISK}:cheap-3"].state == "firing", "状态必须原样保留"
    assert [d["verdict"] for d in detail2 if d["rule_id"] == RULE_ID_DISK] == ["insufficient_data"]


def test_disk_pressure_persistent_notifies_once_then_resolves():
    node = _node("cheap-3", "True", disk="True")
    states, e1, _ = evaluate([node], {}, NOW)
    assert _kinds(e1, RULE_ID_DISK) == ["firing"]

    states, e2, _ = evaluate([node], states, NOW)
    assert e2 == [], "持续盘压不得重复发信"

    states, e3, _ = evaluate([_node("cheap-3", "True", disk="False")], states, NOW)
    assert _kinds(e3, RULE_ID_DISK) == ["resolved"]

    _, e4, _ = evaluate([node], states, NOW)
    assert _kinds(e4, RULE_ID_DISK) == ["firing"], "恢复后再压上必须重新报"


def test_ready_recovery_does_not_close_the_disk_episode():
    """两条规则各自去重：Ready 恢复不得顺手把盘压那条结掉。"""
    node = _node("cheap-3", "False", disk="True")
    states, events, _ = evaluate([node], {}, NOW)
    assert _kinds(events, RULE_ID) == ["firing"]
    assert _kinds(events, RULE_ID_DISK) == ["firing"]

    # Ready 回来了，但盘还压着 ⇒ 只有 Ready 那条 resolved
    node2 = _node("cheap-3", "True", disk="True")
    new_states, events2, _ = evaluate([node2], states, NOW)
    assert _kinds(events2, RULE_ID) == ["resolved"]
    assert _kinds(events2, RULE_ID_DISK) == []
    assert new_states[f"{RULE_ID_DISK}:cheap-3"].state == "firing"


def test_disk_alert_wording_and_hint_differ_from_outage():
    node = _node("liuliangjkiimypbdzxa", "True", disk="True",
                 disk_msg="kubelet has disk pressure")
    _, events, detail = evaluate([node], {}, NOW)
    msgs = compose_messages(events, detail)

    assert len(msgs) == 1
    assert msgs[0]["subject"].startswith("[节点磁盘压力]")
    assert msgs[0]["rule_id"] == f"{RULE_ID_DISK}:liuliangjkiimypbdzxa"
    body = msgs[0]["body"]
    # 处置方向必须对：这条不是"救机器"，而是清盘/抬盘，并点名两条自动救援为何失效
    assert "image-gc" in body
    assert "toleration" in body
    assert '"disk_pressure": "True"' in body

    _, events2, detail2 = evaluate([_node("liuliangjkiimypbdzxa", "True", disk="False")],
                                   {f"{RULE_ID_DISK}:liuliangjkiimypbdzxa":
                                    RuleState(state="firing", episode=1,
                                              last_key="liuliangjkiimypbdzxa")},
                                   NOW)
    msgs2 = compose_messages(events2, detail2)
    assert msgs2[0]["subject"].startswith("[节点磁盘恢复]")


def test_disk_message_reads_disk_facts_not_ready_facts():
    """同一节点两行明细时，邮件正文必须取该规则那一行。

    只用节点名做键会让盘压邮件读到 Ready 的事实（旧实现是 ``{d["node"]: d}``）。
    """
    node = _node("cheap-3", "False", "KubeletNotReady", disk="True",
                 disk_msg="kubelet has disk pressure")
    _, events, detail = evaluate([node], {}, NOW)
    msgs = {m["rule_id"]: m for m in compose_messages(events, detail)}

    disk_body = msgs[f"{RULE_ID_DISK}:cheap-3"]["body"]
    ready_body = msgs[f"{RULE_ID}:cheap-3"]["body"]
    assert '"disk_pressure": "True"' in disk_body
    assert '"ready": null' in disk_body or '"ready"' not in disk_body
    assert '"ready": "False"' in ready_body
    assert json.loads(disk_body)["verdict"] == "firing"
    assert json.loads(ready_body)["verdict"] == "firing"
