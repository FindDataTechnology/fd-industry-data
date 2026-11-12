#!/usr/bin/env python
"""集群节点失联告警（2026-11-12 立）。

**为什么要有这条**：2026-11-11 23:08 `liuliangjkiimypbdzxa`（cheap-3）在操作系统层
死掉——ICMP 全丢、SSH（v4/v6）全超时、`tailscale ping` 无回应、apiserver 到
`100.64.0.12:10250` 报 502、四个 condition 全 `NodeStatusUnknown`。它绑着五个负载
（facet 内部面 `100.64.0.12:3200`、search-relay、infra-telemetry、img-registry、
fd-erp-vertical-api），一起变 0/1。**真正的问题不是挂，而是没人知道**：直到 00:22
人工发现，中间 74 分钟零通知。此前全仓只有数据源的告警线，集群节点健康完全没有出口。

四条会静默出错的语义（都单独落测）：

1. **读不到 API ≠ 全部恢复**：取节点列表失败时判 ``insufficient_data``，**不发 resolved**。
   这恰恰是最容易写反的地方——集群 API 挂了正是该报的时刻，写反就变成"安静地宣布恢复"。
   同时以 exit 4 让 CronJob 可见（监控自身失明不能算正常）。
2. **NotReady 与 NodeStatusUnknown 都要报**：前者是 kubelet 报"我不好"，后者是
   "kubelet 不说话了"（本次就是后者）。只看 ``status == "False"`` 会漏掉后一种。
3. **同一段失联只报一次**：状态机按 `key=节点名` 去重，持续失联不重复发信、不刷新。
4. **死信判据复用 delivery.judge**：firing>0 而一条没送出去 → 判红（strict 开启时），
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


def ready_condition(node: dict) -> dict | None:
    """取 Ready condition；缺失返回 None（缺失同样按看不清处理）。"""
    for c in (node.get("status") or {}).get("conditions") or []:
        if c.get("type") == "Ready":
            return c
    return None


def node_facts(node: dict) -> dict:
    """把一个节点压成判定所需的事实。"""
    meta = node.get("metadata") or {}
    cond = ready_condition(node)
    name = meta.get("name") or ""
    if cond is None:
        return {"name": name, "status": None, "reason": "NoReadyCondition",
                "message": "节点没有 Ready condition", "since": None}
    return {
        "name": name,
        "status": cond.get("status"),
        "reason": cond.get("reason") or "",
        "message": cond.get("message") or "",
        "since": cond.get("lastTransitionTime"),
    }


# ─── 判定 ─────────────────────────────────────────────────────────────────────

def evaluate(nodes: list[dict] | None, states: dict[str, RuleState],
             now: datetime) -> tuple[dict[str, RuleState], list[dict], list[dict]]:
    """按节点逐条判定。返回 ``(新状态, 事件, 判定明细)``。

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
            new_states[rule_key], ev, verdict = evaluate_threshold(
                new_states[rule_key], triggered=None, key=rule_key, at=now,
                detail="节点列表读取失败（API/RBAC/网络），本轮不判定")
            all_events += attach_rule(ev, RULE_ID)
            detail.append({"node": rule_key.split(":", 1)[-1], "verdict": verdict,
                           "note": "blind"})
        return new_states, all_events, detail

    for f in facts:
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
        detail.append({"node": f["name"], "verdict": verdict, "status": f["status"],
                       "reason": f["reason"], "since": f["since"]})

    # 已经不在集群里的节点（被删/改名）：不静默保留 firing，也不伪造 resolved。
    listed = {f["name"] for f in facts}
    for rule_key in states:
        name = rule_key.split(":", 1)[-1]
        if name not in listed:
            detail.append({"node": name, "verdict": INSUFFICIENT,
                           "note": "节点不在返回列表中（已删除或改名），保留原状态不做判定"})
    return new_states, all_events, detail


def compose_messages(events: list[dict], detail: list[dict]) -> list[dict]:
    """把事件拼成邮件；正文带全部节点的当前状态，便于一眼看全。"""
    by_node = {d["node"]: d for d in detail}
    msgs: list[dict] = []
    for e in events:
        name = e["key"]
        d = by_node.get(name, {})
        resolved = e["kind"] == RESOLVED
        tag = "[节点恢复]" if resolved else "[节点失联]"
        subject = f"{tag} {name} {d.get('status') or ''}".strip()
        body = json.dumps({
            "node": name,
            "verdict": e["kind"],
            "ready": d.get("status"),
            "reason": d.get("reason"),
            "since": d.get("since"),
            "detail": e.get("detail"),
            "at": e.get("at"),
            "hint": ("节点不健康时其上的 Pod 会被驱逐或被拒；hostNetwork/local-path 绑定的"
                     "负载无法迁移，需人工处理（云控制台硬重启）"),
        }, ensure_ascii=False, indent=2)
        msgs.append({"rule_id": f"{RULE_ID}:{name}", "subject": subject, "body": body})
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
    }
    print(json.dumps(summary, ensure_ascii=False))

    if nodes is None:
        return EXIT_BLIND
    if verdict_code is not None:
        return verdict_code.exit_code
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
