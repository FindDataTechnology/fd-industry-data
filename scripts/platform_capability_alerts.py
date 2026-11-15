"""平台能力端点是否还在（2026-11-14 立，起因见下）。

**要回答的问题**：已经上线、已经验收过的那些能力，今天还在线上吗？

为什么需要它：2026-11-14 19:43 把平台四份滚到 `sha-0119446`——四份同片、Argo Synced、
pod 2/2、线上 tag 与清单一致，**所有既有判据全绿**，而它 `BUILD-INFO.baseCommit`
是从 13:14 的老基线拉的（`git rev-list --count` ＝ 138 个提交）⇒ 会议录音与会议发现面
整条从生产消失：`POST /api/zhiyu/rooms/:id/meetings` 直接 **404**。
23 分钟里没有任何通道说过话，是靠人跑 live 用例才撞见的。

**判据只用状态码，不碰凭据、不 exec、不读 Secret**：
  · 未登录打一个「存在但受门控」的路由 → `401`（或 403/405/400：都在证明路由存在）
  · 打一个不存在的路由 → `404`
所以 **404/410 ＝ 这个能力不在片里了**，这与「没有权限」是完全不同的两件事
（同族于 `all-path-401-cannot-distinguish-absent-from-gated`：先证明路由在，再谈鉴权）。

三条规则（只做断言）：
  R1 端点消失：任一检查点回 404/410 → firing，点名能力 + 给出定位命令。
  R2 端点异常：回 5xx 或其它非预期码 → firing，与「消失」**分开报**（排查方向不同：
     消失多半是滚错片，5xx 多半是服务侧故障）。
  R3 判不出：全部检查点都连不上（status 0）→ insufficient_data（缺数不是恢复）。

刻意**不做的两件事**：
  · 不用它判「功能对不对」——它只证明路由在。行为正确性靠各自的 live 用例。
  · 不给 runner-fleet 那条判 503：那道门未配令牌时**按设计**回 503（惰性拒绝），
    把它判红等于把设计内的状态变成告警；503 只作为事实打印。

用法：
    python3 scripts/platform_capability_alerts.py --dry-run     # 只判定，不发
    python3 scripts/platform_capability_alerts.py --run         # 写状态 + 发通知
    python3 scripts/platform_capability_alerts.py --from-json /tmp/x.json --dry-run

退出码：0=无告警；1=有告警（firing）；3=投递未配置；4=监控失明（全部连不上）。
"""
from __future__ import annotations

import argparse
import json
import os
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
    FIRING, INSUFFICIENT, attach_rule,
)

RULE_GONE = "platform-capability-gone"
RULE_ERROR = "platform-capability-error"

EXIT_OK, EXIT_FIRING, EXIT_NOT_CONFIGURED, EXIT_BLIND = 0, 1, 3, 4

# 「路由在」的可接受码：401/403（受门控）、405（方法不对但路由在）、400（坏请求但路由在）。
OK_STATUSES = (200, 201, 204, 400, 401, 403, 405)
# 「这个能力不在片里」：只有这两个码。别的都不是"消失"。
GONE_STATUSES = (404, 410)

# 每个检查点对应一个**已交付并验收过**的能力（名字里带 openspec change，
# 便于追到规格）。path 用一个不存在的 id 是故意的：鉴权与路由都比资源查找早，
# 所以「受门控」会先回 401，而不是 404。
#   · 只要某条能力的路由整条消失（老基线片），这里立刻 404 ⇒ R1 firing。
CAPABILITY_CHECKS = (
    {
        "name": "会议录音按人派发（add-meeting-capture）",
        "method": "POST",
        "path": "/api/zhiyu/rooms/zy_capability_probe/meetings",
        "hint": "POST /rooms/:id/meetings 整条没了；查闸 `fd-image-coherence-gate.py --live` 的 R7 与 pod 内 grep capture-ingest",
    },
    {
        "name": "会议发现面（add-meeting-discovery）",
        "method": "GET",
        "path": "/api/zhiyu/rooms/zy_capability_probe/meetings/open",
        "hint": "房间级只读面没了；第二个用户将看不见开着的会议",
    },
    {
        "name": "跨用户团队目录（add-agent-team-directory）",
        "method": "GET",
        "path": "/api/teams/directory",
        "hint": "目录读面没了；不同用户之间「相互发现」这一半断掉",
    },
    {
        "name": "语音采集入房（add-speaker-attribution / 机器面）",
        "method": "POST",
        "path": "/api/zhiyu/capture-ingest/rooms/zy_capability_probe/messages",
        "hint": "采集归并产物无法入房；会议录音即使采到了也不会派发",
    },
    {
        "name": "按人投递到房间（add-speaker-attribution / 平台侧）",
        "method": "POST",
        "path": "/api/voice/captures/vc_capability_probe/zhiyu-room",
        "hint": "按人切分后无法跨进程投递；一路一人仍采但进不了房间",
    },
    {
        "name": "runner 舰队桥门（add-fleet-bridge-endpoint-for-agent-runner）",
        "method": "POST",
        "path": "/api/runner-fleet/architect/fleets",
        "hint": "桥门整条没了；runner 承载岗的舰队工具全部不可达",
        # 未配令牌时这道门**按设计**回 503（惰性拒绝），不判红，只记事实。
        "advisory_statuses": (503,),
    },
)


def _state_info(path) -> dict:
    """本轮能否用到持久状态——去重与「回落」通知的前提，永远打印、永不静默。

    **刻意不 import `alerts_core.state_health`**：脚本由 ConfigMap 挂进 `/src`，而
    `fd_industry_data` 来自镜像，两条供给线各走各的。2026-11-14 实测镜像
    `fd-industry-runner:sha-f5a19aa` 里没有那个新符号（`hasattr` → False），
    加了就会 ImportError、一条告警都发不出来。这里只用镜像确有的 `load_state`。
    与 `alerts_core.ledger.state_health` 语义相同，由
    `tests/test_alert_script_image_contract.py` 逐档对齐。
    """
    st = load_state(path)
    parent = Path(path).parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        writable = os.access(parent, os.W_OK)
    except OSError:
        writable = False
    if st:
        note = f"读到 {len(st)} 条规则的历史状态：去重与回落通知本轮生效"
    elif not writable:
        note = ("本轮无历史状态，且状态目录不可写（save_state 也会失败）："
                "去重与回落通知永远不会生效")
    else:
        note = ("本轮无历史状态（首轮，或状态目录不跨轮如 emptyDir）："
                "去重与回落通知本轮不生效——同一读数连续出现即为状态不跨轮")
    return {"path": str(path), "persisted": bool(st), "rules": len(st),
            "dir_writable": writable, "note": note}


CONFIG = ROOT / "config" / "platform-capability-alerts.json"
REPORT_DIR = Path(os.environ.get("FD_CAPABILITY_ALERT_REPORTS") or "/reports/platform-capability-alerts")
STATE = REPORT_DIR / "state" / "alert-state.json"
EVENTS = REPORT_DIR / "state" / "events.jsonl"

DEFAULT_BASE = "https://platform.finddatatech.cloud"
TIMEOUT = 15.0


def fetch_status(method: str, url: str, timeout: float = TIMEOUT) -> tuple[int, str]:
    """(状态码, 错误串)。网络错误返回 0——由调用方判"判不出"，不当成通过。"""
    body = b"{}" if method in ("POST", "PUT", "PATCH") else None
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"content-type": "application/json",
                                          "user-agent": "fd-capability-alerts/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - 固定 https 面
            return int(getattr(r, "status", 200)), ""
    except urllib.error.HTTPError as exc:
        return int(exc.code), ""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def assess(checks: list[dict]) -> dict:
    """纯判定：不联网、不发信。返回 {gone, error, advisory, blind_all, unreachable}。"""
    gone, error, advisory, unreachable = [], [], [], []
    for c in checks:
        status = int(c.get("status") if c.get("status") is not None else 0)
        name = c.get("name") or "?"
        if status == 0:
            unreachable.append({"name": name, "error": c.get("error") or "连不上"})
            continue
        if status in GONE_STATUSES:
            gone.append({"name": name, "status": status, "hint": c.get("hint") or ""})
        elif status in (c.get("advisory_statuses") or ()):
            advisory.append({"name": name, "status": status})
        elif status in OK_STATUSES:
            continue
        else:
            error.append({"name": name, "status": status, "hint": c.get("hint") or ""})
    total = len(checks)
    return {
        "gone": gone,
        "error": error,
        "advisory": advisory,
        "unreachable": unreachable,
        # 全部连不上才是"失明"；部分连得上就照实判其余检查点。
        "blind_all": bool(total) and len(unreachable) == total,
    }


def compose_messages(events: list[dict], verdict: dict) -> list[dict]:
    """把事件渲染成邮件。每条只说事实与下一步，不带内部拓扑。"""
    out = []
    for e in events:
        if e["rule_id"] == RULE_GONE:
            names = "、".join(g["name"] for g in verdict["gone"])
            lines = [f"以下能力的路由在线上回 404/410，说明当前镜像里没有它们：{names}", ""]
            for g in verdict["gone"]:
                lines.append(f"· {g['name']}（HTTP {g['status']}）{('— ' + g['hint']) if g.get('hint') else ''}")
            lines += [
                "",
                "最可能的成因：滚上去的片是从**老基线**拉的（构建成功、四份同片、Argo 绿都不代表内容完整）。",
                "排查顺序：",
                "1) 在 platform pod 内 cat /app/BUILD-INFO.json 看 baseCommit；",
                "2) 与上一片 base 比祖先：`git rev-list --count <新base>..<旧base>`，不为 0 就是丢了那么多提交；",
                "3) 跑 `fd-image-coherence-gate.py --live`（R7 会对跑着的片逐条 grep 能力标记）；",
                "4) 恢复：只把四份的 image 行改回上一片（diff 里非镜像行改动必须为 0）。",
            ]
            out.append({"rule_id": RULE_GONE, "subject": f"[平台] 能力端点消失：{names}",
                        "body": "\n".join(lines)})
        else:
            names = "、".join(f"{x['name']}（HTTP {x['status']}）" for x in verdict["error"])
            out.append({"rule_id": RULE_ERROR,
                        "subject": f"[平台] 能力端点异常：{names}",
                        "body": "\n".join([
                            f"以下端点回的不是 401/404 这类「路由存在」的码：{names}",
                            "",
                            "这与「端点消失」是两个故障：5xx 是服务侧出错（看 platform 日志），",
                            "非预期 4xx 是路由形状变了（谁改了挂载点/前缀）。",
                        ])})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--base", default=None, help="覆盖目标面（默认取配置或内置地址）")
    ap.add_argument("--from-json", help="离线快照：{checks:[{name,method,path,status}]}；缺省真打")
    ap.add_argument("--dry-run", action="store_true", help="只判定不发送、不写状态")
    ap.add_argument("--run", action="store_true", help="正式跑：写状态/留痕/发通知")
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)

    try:
        cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": f"投递声明缺失或不可读（{args.config}）：{exc}"},
                         ensure_ascii=False))
        return EXIT_NOT_CONFIGURED

    base = (args.base
            or (cfg.get("targets") or {}).get("base")
            or DEFAULT_BASE).rstrip("/")

    if args.from_json:
        try:
            snap = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(json.dumps({"error": f"--from-json 读不了：{exc}", "blind": True},
                             ensure_ascii=False))
            return EXIT_BLIND
        checks = [dict(c) for c in (snap.get("checks") or [])]
    else:
        checks = []
        for spec in CAPABILITY_CHECKS:
            status, err = fetch_status(spec["method"], f"{base}{spec['path']}")
            checks.append({**spec, "status": status, "error": err})

    verdict = assess(checks)

    # 必须在 save_state 之前取：否则本轮刚写的那份会冒充「历史状态」。
    state_info = _state_info(STATE)
    states = load_state(STATE)
    events: list[dict] = []
    detail: list[dict] = []

    for rule_id, items, desc in (
        (RULE_GONE, verdict["gone"], "路由消失"),
        (RULE_ERROR, verdict["error"], "端点异常"),
    ):
        st = states.get(rule_id, RuleState())
        if verdict["blind_all"]:
            new_st, ev, word = evaluate_threshold(st, triggered=None, key=f"{now.date()}",
                                                  at=now, detail="全部检查点连不上")
        else:
            rendered = "；".join("%s=%s" % (x["name"], x["status"]) for x in items) or "无"
            key = "；".join("%s@%s" % (x["name"], x["status"]) for x in items) or f"ok:{now.date()}"
            new_st, ev, word = evaluate_threshold(
                st, triggered=bool(items), key=key, at=now,
                value=len(items), threshold=0, detail=f"{desc}：{rendered}")
        states[rule_id] = new_st
        events += attach_rule(ev, rule_id)
        detail.append({"rule_id": rule_id, "verdict": word, "count": len(items),
                       "items": [{"name": x["name"], "status": x["status"]} for x in items]})

    msgs = compose_messages(events, verdict)
    firing = sum(1 for e in events if e["kind"] == FIRING)

    receipt = DeliveryReceipt()
    verdict_code = None
    if args.run and msgs:
        try:
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
            verdict_code = judge(
                receipt, firing=firing, strict=strict_enabled(os.environ),
                not_configured_reason="SMTP 凭据缺失（host/from）" if receipt.skipped else None)

    if args.run:
        # 只在「该发的都发出去了」时推进状态（同 plaza / ciyuan 的教训）。
        delivered = (not msgs) or receipt.sent >= len(msgs)
        if delivered:
            append_events(EVENTS, events)
            save_state(STATE, {k: v.to_dict() for k, v in states.items()})
        else:
            print(json.dumps({"warn": "投递未完成，本轮不推进状态（下一轮重试同一批）",
                              "messages": len(msgs), "sent": receipt.sent,
                              "failed": receipt.failed, "skipped": receipt.skipped},
                             ensure_ascii=False), file=sys.stderr)

    summary = {
        "base": base,
        "blind": verdict["blind_all"],
        "checks": [{"name": c["name"], "status": c["status"]} for c in checks],
        "gone": verdict["gone"],
        "error": verdict["error"],
        "advisory": verdict["advisory"],
        "unreachable": verdict["unreachable"],
        "firing": firing,
        "events": len(events),
        "messages": len(msgs),
        "delivery": receipt.as_dict(),
        "verdict": verdict_code.as_dict() if verdict_code else None,
        "detail": detail,
        "state": state_info,
    }
    print(json.dumps(summary, ensure_ascii=False))

    if verdict_code is not None:
        return verdict_code.exit_code
    if verdict["blind_all"]:
        return EXIT_BLIND
    return EXIT_FIRING if firing else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
