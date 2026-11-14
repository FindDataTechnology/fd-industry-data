"""广场机器面新鲜度告警（2026-11-14 立）。

**要回答的问题**：`agent.finddatatech.cloud` 上那四个机器面（`llms.txt` /
`llms-full.txt` / `catalog.json` / `robots.txt`），今天还是今天的吗？

为什么需要它：这四个文件由 cheap1 主机 cron（`/opt/fd/plaza-refresh.sh`，
`17 4 * * *`）每天重导一次，不是 CI 产出。**那条 cron 死了不会有任何东西报警**：
它的日志落在主机 `/tmp`（重启即丢），出口只有一句 `exit 1`，而这台机上没有 mail。
于是它就退化成 2026-11-05→11-09 那次的形态——快照挂在线上、市场从 82 涨到 114、
AI 客户端读到两周前的目录，**没有任何地方会报错**。那次是靠人肉发现才建的 cron；
这一条是给「cron 本身失效」补的同一判据。

判据（三条，只做断言）：
  · `catalog.json` 的 `generatedAt` 距现在超过 `stale_hours`（默认 36h）→ firing。
    36h ＝ 日跑 24h ＋ 半个周期：一次晚点不叫事，连续漏一轮才叫事。
  · 四个根路径任一非 200 → firing（站点面本身没了，这与"陈旧"是两个故障）。
  · 抓不到 / 解析不出 `generatedAt` → **insufficient_data**，既不判 firing 也不判恢复。
    缺数不是恢复——一次网络抖动不该发出一条"已恢复"。

**解析必须容忍两种 JSON**：`catalog.json` 是美化过的（`"generatedAt": "2026-11-13"`，
冒号后有空格），而任何压缩过的副本是 `"generatedAt":"2026-11-13"`。2026-11-14 在
主机自检里踩过一次：正则只写死后一种，于是那行日志永远打印空值、而声称的校验
从未生效。这里两种情况都必须是用例。

用法：
    python3 scripts/plaza_freshness_alerts.py --dry-run          # 只判定，不发
    python3 scripts/plaza_freshness_alerts.py --run              # 写状态 + 发通知
    python3 scripts/plaza_freshness_alerts.py --from-json /tmp/x.json --dry-run   # 离线

退出码：0=无告警；1=有告警（firing）；3=投递未配置；4=监控失明（抓不到）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
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


CONFIG = ROOT / "config" / "plaza-freshness-alerts.json"
REPORT_DIR = Path(os.environ.get("FD_PLAZA_ALERT_REPORTS")
                  or (ROOT / "reports" / "plaza-freshness-alerts"))
STATE = REPORT_DIR / "state" / "alert-state.json"
EVENTS = REPORT_DIR / "state" / "events.jsonl"

RULE_STALE = "plaza-catalog-stale"
RULE_ROOT_DOWN = "plaza-root-face-down"

EXIT_OK = 0
EXIT_FIRING = 1
EXIT_NOT_CONFIGURED = 3
EXIT_BLIND = 4

DEFAULT_ROOT_FACES = ("/llms.txt", "/llms-full.txt", "/catalog.json", "/robots.txt")
DEFAULT_STALE_HOURS = 36.0

# 冒号后的空白可有可无：美化 JSON 有，压缩过的没有。两种都必须认。
_GENERATED_AT_RE = re.compile(r'"generatedAt"\s*:\s*"([^"]+)"')


def parse_generated_at(text: str) -> date | None:
    """从 catalog.json 文本里抽 `generatedAt`（日期）。抽不到返回 None，不猜今天。"""
    if not text:
        return None
    m = _GENERATED_AT_RE.search(text)
    if not m:
        return None
    raw = m.group(1).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(raw[:len(fmt) + 2 if "%" in fmt else len(raw)], fmt).date()
        except ValueError:
            continue
    # 兜底：只认前 10 位的 ISO 日期，其余形状一律当"解析不出"
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def age_hours(generated: date, now: datetime) -> float:
    """快照日期（UTC 日）到现在的年龄。`generatedAt` 是导出那天的 UTC 日期。"""
    started = datetime(generated.year, generated.month, generated.day, tzinfo=timezone.utc)
    return (now - started).total_seconds() / 3600.0


def fetch(url: str, timeout: float = 20.0) -> tuple[int, str]:
    """(状态码, 文本)。网络错误返回 (0, 错误串)，由调用方判"失明"。"""
    try:
        req = urllib.request.Request(url, headers={"user-agent": "fd-plaza-freshness/1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - 固定 https 面
            return int(getattr(r, "status", 200)), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), ""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def assess(*, catalog_status: int, catalog_text: str, roots: dict[str, int],
           now: datetime, stale_hours: float) -> dict:
    """纯判定：不联网、不发信。返回 {stale, root_down, generated, age_hours, blind}。

    `blind=True` 意味着判不出——既不是"新鲜"也不是"陈旧"，调用方必须让这一态可见。
    """
    if catalog_status != 200 or not catalog_text:
        return {"blind": True, "reason": f"catalog.json 取不到（status={catalog_status}）",
                "stale": None, "root_down": [], "generated": None, "age_hours": None}
    generated = parse_generated_at(catalog_text)
    if generated is None:
        return {"blind": True, "reason": "catalog.json 里读不出 generatedAt",
                "stale": None, "root_down": [], "generated": None, "age_hours": None}
    hours = age_hours(generated, now)
    down = sorted(p for p, code in roots.items() if code != 200)
    return {"blind": False, "reason": "", "stale": hours >= stale_hours,
            "root_down": down, "generated": generated.isoformat(),
            "age_hours": round(hours, 1)}


def compose_messages(events: list[dict], verdict: dict, stale_hours: float) -> list[dict]:
    """把事件渲染成邮件。每条只说事实与下一步，不带内部拓扑。"""
    out = []
    for e in events:
        if e["rule_id"] == RULE_STALE:
            age = verdict.get("age_hours")
            subject = f"[广场] 机器面已 {age}h 未更新（阈值 {stale_hours:.0f}h）"
            body = "\n".join([
                f"catalog.json 的 generatedAt = {verdict.get('generated')}，距今 {age} 小时。",
                "",
                "每日刷新由 cheap1 主机 cron 驱动（/opt/fd/plaza-refresh.sh，17 4 * * *），",
                "它失败时不会有人被通知（日志在主机 /tmp，机器重启即丢）。",
                "",
                "排查顺序：",
                "1) ssh root@cheap1 'tail -50 /tmp/plaza-refresh.log' —— 看导出自检那一行；",
                "2) python3 fd-infra-deploy/tools/check_plaza_export_copy.py —— 主机跑的导出器",
                "   还是不是仓里那份（2026-11-14 实测冻过 4 天，没有任何东西会红）；",
                "3) 手工跑一轮：ssh root@cheap1 'env -i /bin/bash /opt/fd/plaza-refresh.sh'。",
            ])
        else:
            down = "、".join(verdict.get("root_down") or []) or "(未知)"
            subject = f"[广场] 机器面不可达：{down}"
            body = "\n".join([
                f"以下根路径没有回 200：{down}",
                "",
                "这属于站点面本身的问题（不是陈旧）：先看 cheap1 的 Caddy 与站点根是否存在，",
                "再确认 /srv/agent-plaza-site 有没有被上一次原子落位留在半途。",
            ])
        out.append({"rule_id": e["rule_id"], "subject": subject, "body": body})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--from-json", help="离线快照：{catalog_status,catalog_text,roots}；缺省真抓")
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

    base = str((cfg.get("targets") or {}).get("base") or "https://agent.finddatatech.cloud").rstrip("/")
    stale_hours = float((cfg.get("thresholds") or {}).get("stale_hours", DEFAULT_STALE_HOURS))
    faces = tuple((cfg.get("targets") or {}).get("root_faces") or DEFAULT_ROOT_FACES)

    if args.from_json:
        try:
            snap = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(json.dumps({"error": f"--from-json 读不了：{exc}", "blind": True},
                             ensure_ascii=False))
            return EXIT_BLIND
        cat_status = int(snap.get("catalog_status", 200))
        cat_text = str(snap.get("catalog_text") or "")
        roots = {k: int(v) for k, v in (snap.get("roots") or {}).items()}
    else:
        cat_status, cat_text = fetch(f"{base}/catalog.json")
        roots = {}
        for p in faces:
            code, _ = fetch(f"{base}{p}", timeout=15.0)
            roots[p] = code

    verdict = assess(catalog_status=cat_status, catalog_text=cat_text, roots=roots,
                     now=now, stale_hours=stale_hours)

    # 必须在 save_state 之前取：否则本轮刚写的那份会冒充「历史状态」，
    # 读数永远为 true——那就把这个坑从「看得见」变成「看不见」。
    state_info = _state_info(STATE)
    states = load_state(STATE)
    events: list[dict] = []
    detail: list[dict] = []

    # 规则一：陈旧。判不出（blind）时 triggered=None ⇒ insufficient_data，不动状态。
    st = states.get(RULE_STALE, RuleState())
    if verdict["blind"]:
        new_st, ev, word = evaluate_threshold(st, triggered=None, key=f"{now.date()}",
                                              at=now, detail=verdict["reason"])
    else:
        new_st, ev, word = evaluate_threshold(
            st, triggered=bool(verdict["stale"]), key=str(verdict["generated"]), at=now,
            value=verdict["age_hours"], threshold=stale_hours,
            detail=f"generatedAt={verdict['generated']} 距今 {verdict['age_hours']}h")
    states[RULE_STALE] = new_st
    events += attach_rule(ev, RULE_STALE)
    detail.append({"rule_id": RULE_STALE, "verdict": word,
                   "value": verdict.get("age_hours"), "threshold": stale_hours,
                   "generated_at": verdict.get("generated")})

    # 规则二：站点面不可达。blind（抓不到 catalog）不参与这条——那是同一个失明。
    st2 = states.get(RULE_ROOT_DOWN, RuleState())
    if verdict["blind"] and not roots:
        new_st2, ev2, word2 = evaluate_threshold(st2, triggered=None, key=f"{now.date()}",
                                                 at=now, detail="未取得根路径状态")
    else:
        down = verdict["root_down"]
        new_st2, ev2, word2 = evaluate_threshold(
            st2, triggered=bool(down), key=",".join(down) or f"ok:{now.date()}", at=now,
            value=len(down), threshold=0, detail="；".join(down) or "四个根路径全 200")
    states[RULE_ROOT_DOWN] = new_st2
    events += attach_rule(ev2, RULE_ROOT_DOWN)
    detail.append({"rule_id": RULE_ROOT_DOWN, "verdict": word2,
                   "down": verdict["root_down"], "roots": roots})

    msgs = compose_messages(events, verdict, stale_hours)
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
        # 只在「该发的都发出去了」时推进状态（同 ciyuan 那条线的教训：投递没成就推进
        # 状态，等于把告警静默吞掉，补好通道后要等回落才会补发）。
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
        "blind": verdict["blind"],
        "blind_reason": verdict["reason"],
        "generated_at": verdict.get("generated"),
        "age_hours": verdict.get("age_hours"),
        "stale_hours": stale_hours,
        "root_faces": roots,
        "firing": firing,
        "events": len(events),
        "messages": len(msgs),
        "delivery": receipt.as_dict(),
        "verdict": verdict_code.as_dict() if verdict_code else None,
        "detail": detail,
        # 状态是否跨轮：去重与「回落」通知的前提。emptyDir 下恒为 false——
        # 永远打印、永不静默（与另外三条线同一读数）。
        "state": state_info,
    }
    print(json.dumps(summary, ensure_ascii=False))

    if verdict_code is not None:
        return verdict_code.exit_code
    if verdict["blind"]:
        return EXIT_BLIND
    return EXIT_FIRING if firing else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
