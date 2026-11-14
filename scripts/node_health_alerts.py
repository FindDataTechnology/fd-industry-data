#!/usr/bin/env python
"""集群节点失联告警（2026-11-12 立）。

**为什么要有这条**：2026-11-11 23:08 `liuliangjkiimypbdzxa`（cheap-3）在操作系统层
死掉——ICMP 全丢、SSH（v4/v6）全超时、`tailscale ping` 无回应、apiserver 到
`100.64.0.12:10250` 报 502、四个 condition 全 `NodeStatusUnknown`。它绑着五个负载
（facet 内部面 `100.64.0.12:3200`、search-relay、infra-telemetry、img-registry、
fd-erp-vertical-api），一起变 0/1。**真正的问题不是挂，而是没人知道**：直到 00:22
人工发现，中间 74 分钟零通知。此前全仓只有数据源的告警线，集群节点健康完全没有出口。

**第二条规则（2026-11-13 立）：DiskPressure。** 那天的 `liuliangjkiimypbdzxa`
（与上文同一台机，29.9G 盘 77–80%）`Ready=True` 而 `DiskPressure=True`：
`fd-prod/facet`（单副本 + hostname 硬绑、无 PVC）因 ephemeral-storage 被 `Evicted`、
替代 pod 被 disk-pressure taint 钉成永久 `Pending`，对外停摆约 17 分钟。
**全程一封告警都没发**——因为判定只有 Ready 一条规则，`node_facts()` 根本不读
DiskPressure。而同时两级自动救援也都失效：`image-gc` CronJob 的 pod 被 kubelet 的
**node condition 准入**拒掉（`Pod was rejected: The node had condition: [DiskPressure]`，
加 toleration 无用），宿主看门狗又只在「DiskPressure 且可用量 ≥21%（盘没满却卡死）」
时才动，真低盘时故意放手给 kubelet。**于是"盘快满了"这个前兆只能靠告警。**

五条会静默出错的语义（都单独落测）：

1. **读不到 API ≠ 全部恢复**：取节点列表失败时判 ``insufficient_data``，**不发 resolved**。
   这恰恰是最容易写反的地方——集群 API 挂了正是该报的时刻，写反就变成"安静地宣布恢复"。
   同时以 exit 4 让 CronJob 可见（监控自身失明不能算正常）。
2. **NotReady 与 NodeStatusUnknown 都要报**：前者是 kubelet 报"我不好"，后者是
   "kubelet 不说话了"（本次就是后者）。只看 ``status == "False"`` 会漏掉后一种。
3. **同一段失联只报一次**：状态机按 ``key=节点名`` 去重，持续失联不重复发信、不刷新。
   两条规则各自独立去重（key 前缀不同），所以 Ready 恢复不会顺手把盘压那条也结掉。
4. **DiskPressure 缺失不是"没有压力"**：节点没报这条 condition 时判
   ``insufficient_data``，**不伪造 resolved**；只有明确 ``status == "False"`` 才结案。
5. **死信判据复用 delivery.judge**：firing>0 而一条没送出去 → 判红（strict 开启时），
   缺出口配置 → exit 3，不当成"本轮无告警"。

用法::

    python3 scripts/node_health_alerts.py --dry-run          # 只判定不发送
    python3 scripts/node_health_alerts.py --run              # 正式：写状态/留痕/发通知
    python3 scripts/node_health_alerts.py --nodes-json f.json --dry-run   # 离线复算

退出码：0=正常（含无告警）；1=死信（应送达却 0 送达）；2=脚本自身出错；
3=投递未配置；4=读不到集群节点列表（监控失明）。
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fd_industry_data.alerts_core import (  # noqa: E402
    RuleState, append_events, dispatch, evaluate_threshold, load_state, save_state,
    state_health,
)
from fd_industry_data.alerts_core.delivery import (  # noqa: E402
    DeliveryNotConfigured, DeliveryReceipt, judge, resolve_target, smtp_from_env,
    strict_enabled,
)
from fd_industry_data.alerts_core.state import (  # noqa: E402
    FIRING, INSUFFICIENT, OK, RESOLVED, attach_rule,
)

CONFIG = ROOT / "config" / "node-health-alerts.json"
#: 状态/留痕目录。容器里脚本挂在只读 ConfigMap 上（/src），必须能改到可写卷：
#: 由 ``FD_NODE_ALERT_REPORTS`` 指定，缺省仍是仓库内的 reports/（本地跑不变）。
REPORT_DIR = Path(os.environ.get("FD_NODE_ALERT_REPORTS")
                  or (ROOT / "reports" / "node-health-alerts"))
STATE = REPORT_DIR / "state" / "alert-state.json"
EVENTS = REPORT_DIR / "state" / "events.jsonl"

RULE_ID = "k8s-node-not-ready"
#: 第二条规则：盘压前兆。**与 Ready 独立成规则**，因为二者会同时成立
#: （2026-11-13 实测 Ready=True + DiskPressure=True），合并成一条会互相盖掉。
RULE_ID_DISK = "k8s-node-disk-pressure"
SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")

EXIT_OK = 0
EXIT_DEAD_LETTER = 1
EXIT_SCRIPT_ERROR = 2
EXIT_NOT_CONFIGURED = 3
EXIT_BLIND = 4


# ─── 取数 ─────────────────────────────────────────────────────────────────────

def fetch_nodes(timeout: float = 10.0) -> list[dict] | None:
    """读全部节点。**任何失败都返回 None**——调用方据此判"失明"，不得当成空列表。

    走 SA token + 集群内 API（不用 kubectl，镜像里不必带客户端）；地址取
    ``KUBERNETES_SERVICE_HOST/PORT``，回退 ``kubernetes.default.svc``。
    """
    try:
        token = (SA_DIR / "token").read_text(encoding="utf-8").strip()
        host = os.environ.get("KUBERNETES_SERVICE_HOST") or "kubernetes.default.svc"
        port = os.environ.get("KUBERNETES_SERVICE_PORT") or "443"
        ctx = ssl.create_default_context(cafile=str(SA_DIR / "ca.crt"))
        req = urllib.request.Request(
            f"https://{host}:{port}/api/v1/nodes",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 — 任何异常都意味着"这次看不见"，不是"没节点"
        return None
    items = payload.get("items")
    return items if isinstance(items, list) else None


def condition_of(node: dict, ctype: str) -> dict | None:
    """取任意 condition；缺失返回 None（缺失按"看不清"处理，不按"健康"）。"""
    for c in (node.get("status") or {}).get("conditions") or []:
        if c.get("type") == ctype:
            return c
    return None


def ready_condition(node: dict) -> dict | None:
    """取 Ready condition；缺失返回 None（缺失同样按看不清处理）。"""
    return condition_of(node, "Ready")


def node_facts(node: dict) -> dict:
    """把一个节点压成判定所需的事实。

    除 Ready 之外**必须**带 DiskPressure（2026-11-13 的盲区就在这里）：那天
    ``Ready=True`` + ``DiskPressure=True``，单规则实现全程零告警，直到 facet 被 evict
    才发现。磁盘可用量本身**取不到**（node 对象只给 condition，不给 eviction 阈值与
    真实可用量），所以这里不假装有"百分比"——判据就是 condition 的 True/False/缺失三态。
    """
    meta = node.get("metadata") or {}
    cond = ready_condition(node)
    disk = condition_of(node, "DiskPressure")
    name = meta.get("name") or ""
    facts = {
        "name": name,
        "status": None,
        "reason": "NoReadyCondition",
        "message": "节点没有 Ready condition",
        "since": None,
        "disk_status": (disk or {}).get("status"),
        "disk_reason": (disk or {}).get("reason") or "",
        "disk_message": (disk or {}).get("message") or "",
        "disk_since": (disk or {}).get("lastTransitionTime"),
    }
    if cond is not None:
        facts.update({
            "status": cond.get("status"),
            "reason": cond.get("reason") or "",
            "message": cond.get("message") or "",
            "since": cond.get("lastTransitionTime"),
        })
    return facts


# ─── 判定 ─────────────────────────────────────────────────────────────────────

def _rule_of(rule_key: str) -> str:
    """从状态键反查规则名（``<rule_id>:<node>``）。"""
    return rule_key.split(":", 1)[0] if ":" in rule_key else RULE_ID


def evaluate(nodes: list[dict] | None, states: dict[str, RuleState],
             now: datetime) -> tuple[dict[str, RuleState], list[dict], list[dict]]:
    """按节点逐条判定（Ready 与 DiskPressure 两条规则各自独立）。返回
    ``(新状态, 事件, 判定明细)``。

    ``nodes is None``（读不到 API）→ 每条既有规则判 ``insufficient_data``：
    不改状态、不发 resolved。**这就是"失明不等于恢复"的落点。**
    """
    facts = [node_facts(n) for n in (nodes or [])]
    # load_state() 给的是**纯 dict**（``{rule_id: state_dict}``），而判定内核吃
    # ``RuleState``；在入口统一还原一次，别让两种形状往下游流。
    new_states: dict[str, RuleState] = {
        k: (v if isinstance(v, RuleState) else RuleState.from_dict(v))
        for k, v in states.items()
    }
    all_events: list[dict] = []
    detail: list[dict] = []

    if nodes is None:
        for rule_key in list(new_states):
            rid = _rule_of(rule_key)
            new_states[rule_key], ev, verdict = evaluate_threshold(
                new_states[rule_key], triggered=None, key=rule_key, at=now,
                detail="节点列表读取失败（API/RBAC/网络），本轮不判定")
            all_events += attach_rule(ev, rid)
            detail.append({"rule_id": rid, "node": rule_key.split(":", 1)[-1],
                           "verdict": verdict, "note": "blind"})
        return new_states, all_events, detail

    for f in facts:
        # ── 规则一：Ready ──────────────────────────────────────────────
        rule_key = f"{RULE_ID}:{f['name']}"
        prev = new_states.get(rule_key, RuleState())
        # status 缺失或非 True 都算"不健康"：NotReady 是 kubelet 说不好，
        # NodeStatusUnknown/缺失是 kubelet 不说话了——两种都必须报。
        triggered = f["status"] != "True"
        human = ("Ready" if f["status"] == "True"
                 else f"Ready={f['status'] or 'missing'}（{f['reason'] or 'no-reason'}）")
        new_states[rule_key], ev, verdict = evaluate_threshold(
            prev, triggered=triggered, key=f["name"], at=now,
            value=f["status"], threshold="True",
            detail=f"{f['name']} {human}"
                   + (f" since {f['since']}" if f["since"] else "")
                   + (f"：{f['message']}" if f["message"] else ""))
        all_events += attach_rule(ev, RULE_ID)
        detail.append({"rule_id": RULE_ID, "node": f["name"], "verdict": verdict,
                       "status": f["status"], "reason": f["reason"], "since": f["since"]})

        # ── 规则二：DiskPressure（2026-11-13 补）─────────────────────
        # 三态，别拍成两态：True=firing；False=结案；**缺失=看不清**（不发 resolved）。
        # 节点整机失联时 conditions 会变 Unknown，那时这里就是 None ⇒ 不伪造恢复，
        # 恢复由规则一（Ready）负责报出来。
        dkey = f"{RULE_ID_DISK}:{f['name']}"
        dprev = new_states.get(dkey, RuleState())
        dstate = f["disk_status"]
        dtriggered = None if dstate is None else (dstate == "True")
        new_states[dkey], dev, dverdict = evaluate_threshold(
            dprev, triggered=dtriggered, key=f["name"], at=now,
            value=dstate, threshold="False",
            detail=(f"{f['name']} DiskPressure={dstate or 'missing'}"
                    + (f" since {f['disk_since']}" if f["disk_since"] else "")
                    + (f"：{f['disk_message']}" if f["disk_message"] else "")))
        all_events += attach_rule(dev, RULE_ID_DISK)
        detail.append({"rule_id": RULE_ID_DISK, "node": f["name"], "verdict": dverdict,
                       "disk_status": dstate, "disk_reason": f["disk_reason"],
                       "disk_message": f["disk_message"], "disk_since": f["disk_since"]})

    # 已经不在集群里的节点（被删/改名）：不静默保留 firing，也不伪造 resolved。
    listed = {f["name"] for f in facts}
    for rule_key in states:
        name = rule_key.split(":", 1)[-1]
        if name not in listed:
            detail.append({"rule_id": _rule_of(rule_key), "node": name,
                           "verdict": INSUFFICIENT,
                           "note": "节点不在返回列表中（已删除或改名），保留原状态不做判定"})
    return new_states, all_events, detail


#: 每条规则的通知措辞。**分规则的 hint 不是装饰**：盘压那条的处置方式与"节点失联"
#: 完全不同（失联要救机器；盘压要清盘/抬盘，且清盘任务恰好跑不起来）。
_RULE_TEXT = {
    RULE_ID: {
        "firing": "[节点失联]",
        "resolved": "[节点恢复]",
        "what": "Ready",
        "hint": ("节点不健康时其上的 Pod 会被驱逐或被拒；hostNetwork/local-path 绑定的"
                 "负载无法迁移，需人工处理（云控制台硬重启）"),
    },
    RULE_ID_DISK: {
        "firing": "[节点磁盘压力]",
        "resolved": "[节点磁盘恢复]",
        "what": "DiskPressure",
        "hint": ("DiskPressure=True 时 kubelet 会按 ephemeral-storage 驱逐 Pod，而"
                 "**替代 Pod 会被 disk-pressure taint 钉成永久 Pending**；hostname 硬绑 + "
                 "无 PVC 的单副本负载（如 fd-prod/facet）就此对外停摆。两条自动救援此时也"
                 "失效：image-gc CronJob 的 Pod 被 node condition 准入拒掉（加 toleration 无用），"
                 "宿主看门狗只在『盘没满却卡死』(≥21%) 时才动。处置：先看 `df` 与 "
                 "`du -x -d1`（用 hostPath 只读探针，k8s 侧看不见），腾出空间后 condition "
                 "要在下一次 kubelet 评估才转 False"),
    },
}
_FALLBACK_TEXT = {"firing": "[节点异常]", "resolved": "[节点恢复]",
                  "what": "condition", "hint": ""}


def compose_messages(events: list[dict], detail: list[dict]) -> list[dict]:
    """把事件拼成邮件；正文带该规则的当前事实，便于一眼看全。

    **必须按 ``(规则, 节点)`` 定位明细**：同一节点同时可能有两行（Ready 一行、
    DiskPressure 一行），只用节点名做键会让盘压那条读到 Ready 的事实。
    """
    by_key = {(d.get("rule_id"), d["node"]): d for d in detail}
    msgs: list[dict] = []
    for e in events:
        name = e["key"]
        rule = e.get("rule_id") or RULE_ID
        text = _RULE_TEXT.get(rule, _FALLBACK_TEXT)
        d = by_key.get((rule, name), {})
        resolved = e["kind"] == RESOLVED
        tag = text["resolved"] if resolved else text["firing"]
        if rule == RULE_ID_DISK:
            shown = d.get("disk_status")
            facts = {"disk_pressure": d.get("disk_status"),
                     "reason": d.get("disk_reason"),
                     "since": d.get("disk_since"),
                     "message": d.get("disk_message")}
        else:
            shown = d.get("status")
            facts = {"ready": d.get("status"),
                     "reason": d.get("reason"),
                     "since": d.get("since"),
                     "message": d.get("message")}
        subject = f"{tag} {name} {shown or ''}".strip()
        body = json.dumps({
            "node": name,
            "rule": rule,
            "verdict": e["kind"],
            **facts,
            "detail": e.get("detail"),
            "at": e.get("at"),
            "hint": text["hint"],
        }, ensure_ascii=False, indent=2)
        msgs.append({"rule_id": f"{rule}:{name}", "subject": subject, "body": body})
    return msgs


# ─── 入口 ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--nodes-json", help="离线节点列表（kubectl get nodes -o json 的输出）；"
                                         "缺省走集群内 API")
    ap.add_argument("--dry-run", action="store_true", help="只判定不发送、不写状态")
    ap.add_argument("--run", action="store_true", help="正式跑：写状态/留痕/发通知")
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)

    if args.nodes_json:
        raw = json.loads(Path(args.nodes_json).read_text(encoding="utf-8"))
        nodes = raw.get("items") if isinstance(raw, dict) else raw
        if not isinstance(nodes, list):
            print(json.dumps({"error": "--nodes-json 里没有 items 列表"}, ensure_ascii=False))
            return EXIT_SCRIPT_ERROR
    else:
        nodes = fetch_nodes()

    # 必须在 save_state 之前取：否则本轮刚写的那份会冒充「历史状态」，
    # 读数永远为 true——那就把这个坑从「看得见」变成「看不见」。
    state_info = state_health(STATE)
    states = load_state(STATE)
    new_states, events, detail = evaluate(nodes, states, now)

    msgs = compose_messages(events, detail)
    firing = sum(1 for e in events if e["kind"] == FIRING)

    receipt = DeliveryReceipt()
    verdict_code = None
    if args.run and msgs:
        try:
            cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
            target = resolve_target(cfg, os.environ)
            cfg_smtp = smtp_from_env(os.environ, recipients=target.to)
        except (OSError, ValueError, DeliveryNotConfigured) as exc:
            receipt.reasons.append(str(exc))
            verdict_code = judge(receipt, firing=firing, strict=strict_enabled(os.environ),
                                 not_configured_reason=str(exc))
        else:
            sent = dispatch(msgs, cfg_smtp)
            receipt = DeliveryReceipt(sent=sent.get("sent", 0), failed=sent.get("failed", 0),
                                      skipped=1 if sent.get("skipped") else 0,
                                      reasons=[str(f) for f in sent.get("failures", [])])
            if receipt.skipped:
                verdict_code = judge(receipt, firing=firing,
                                     strict=strict_enabled(os.environ),
                                     not_configured_reason="SMTP 凭据缺失（host/from）")
            else:
                verdict_code = judge(receipt, firing=firing, strict=strict_enabled(os.environ))

    if args.run:
        append_events(EVENTS, events)
        # save_state 只做 json.dumps —— 必须先把 RuleState 落成 dict，
        # 否则运行期 TypeError: Object of type RuleState is not JSON serializable
        # （2026-11-12 首次在集群里跑就撞到这条，单测因为不落盘而没盖住）。
        save_state(STATE, {k: v.to_dict() for k, v in new_states.items()})

    summary = {
        "blind": nodes is None,
        "nodes": len(nodes or []),
        "firing": firing,
        "events": len(events),
        "messages": len(msgs),
        "delivery": receipt.as_dict(),
        "verdict": verdict_code.as_dict() if verdict_code else None,
        "detail": detail,
        # 状态是否跨轮：去重与「回落」通知的前提。emptyDir 下恒为 false——
        # 永远打印、永不静默（详见 ledger.state_health）。
        "state": state_info,
    }
    print(json.dumps(summary, ensure_ascii=False))

    if nodes is None:
        return EXIT_BLIND
    if verdict_code is not None:
        return verdict_code.exit_code
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
