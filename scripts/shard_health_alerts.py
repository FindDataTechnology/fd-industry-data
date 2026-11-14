#!/usr/bin/env python
"""PG 分片探活：对每个 postgres_fdw 分片跑一次最小查询。

用法::

    python3 shard_health_alerts.py --dry-run
    python3 shard_health_alerts.py --run
    python3 shard_health_alerts.py --dry-run --table ft_sem_obs_xinru3   # 只测一个

退出码：0=正常；1=死信（该送达却 0 送达）；2=脚本自身出错；3=投递未配置。
**主库连不上也算故障**（firing），不是"数据不足所以跳过"——那正是最该报的时刻。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from fd_industry_data.alerts_core import (
    RuleState, append_events, dispatch, evaluate_threshold, load_state, save_state,
)
from fd_industry_data.alerts_core.delivery import (
    DeliveryNotConfigured, DeliveryReceipt, judge, resolve_target, smtp_from_env,
    strict_enabled,
)
from fd_industry_data.alerts_core.state import FIRING, RESOLVED, attach_rule

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


RULE_ID = "pg-shard-reachable"
REPORT_DIR = Path(os.environ.get("FD_NODE_ALERT_REPORTS") or "/reports/shard-health")
STATE = REPORT_DIR / "state" / "alert-state.json"
EVENTS = REPORT_DIR / "state" / "events.jsonl"
CONFIG = Path("/src/shard-health-alerts.json")

# 分片 → 代表表。表本身可能为空（返回 0 行也算通），关键是要真的走一次
# foreign scan：连接失败会在这里抛，而不是等到某个策略跑到一半。
SHARDS = {
    "shard_xinru2": "ft_sem_obs_xinru2",
    "shard_xinru3": "ft_sem_obs_xinru3",
}

EXIT_OK = 0
EXIT_DEAD_LETTER = 1
EXIT_SCRIPT_ERROR = 2
EXIT_NOT_CONFIGURED = 3


def probe(table: str, timeout_ms: int = 15000) -> tuple[bool, str]:
    """跑 `SELECT 1 FROM <ft> LIMIT 1`。返回 (ok, 说明)。

    单语句超时用 ``statement_timeout`` 压住：分片半死（TCP 通但不应答）时
    不能让探测自己挂住，否则一轮 15 分钟都跑不完。
    """
    import psycopg2  # 延迟导入：主库都连不上时要能报出脚本级错误

    dsn = os.environ["FD_OPEN_DATA_MCP_DATABASE_URL"].replace(
        "postgresql+psycopg2://", "postgresql://")
    try:
        with psycopg2.connect(dsn, connect_timeout=10) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(f"SET statement_timeout = {timeout_ms}")
                cur.execute(f"SELECT 1 FROM {table} LIMIT 1")
                cur.fetchall()
        return True, "ok"
    except Exception as e:  # noqa: BLE001 — 任何异常都是"这一刻不通"
        return False, f"{type(e).__name__}: {e}".replace("\n", " ")[:220]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", help="只测这一个分片表")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)
    if not args.table:
        shards = dict(SHARDS)
    elif args.table in SHARDS.values():
        shards = {k: v for k, v in SHARDS.items() if v == args.table}
    elif re.fullmatch(r"ft_[a-z0-9_]+", args.table):
        # 允许任意 ft_* 作为一次性探针（负例验证用：不存在的表必然报错，
        # 走的是与"分片不可达"完全相同的 ok=False 分支）。
        shards = {f"probe:{args.table}": args.table}
    else:
        print(json.dumps({"error": f"bad --table {args.table!r}（须为已登记分片或 ft_*）"},
                         ensure_ascii=False))
        return EXIT_SCRIPT_ERROR

    # 必须在 save_state 之前取：否则本轮刚写的那份会冒充「历史状态」，
    # 读数永远为 true——那就把这个坑从「看得见」变成「看不见」。
    state_info = _state_info(STATE)
    states = load_state(STATE)
    new_states: dict[str, RuleState] = {}
    events: list[dict] = []
    detail: list[dict] = []
    msgs: list[dict] = []

    for name, table in shards.items():
        ok, why = probe(table)
        key = f"{RULE_ID}:{name}"
        prev = states.get(key)
        prev_state = prev if isinstance(prev, RuleState) else (
            RuleState.from_dict(prev) if prev else RuleState())
        new_states[key], ev, verdict = evaluate_threshold(
            prev_state, triggered=not ok, key=name, at=now,
            value="reachable" if ok else "unreachable", threshold="reachable",
            detail=f"{name}（{table}）" + (" 可达" if ok else f" 不可达：{why}"))
        events += attach_rule(ev, RULE_ID)
        detail.append({"shard": name, "ok": ok, "why": why, "verdict": verdict})
        for e in ev:
            if e["kind"] not in (FIRING, RESOLVED):
                continue
            msgs.append({
                "rule_id": f"{RULE_ID}:{name}",
                "subject": f"[PG分片{'可达' if e['kind'] == RESOLVED else '不可达'}] {name}",
                "body": json.dumps({
                    "shard": name, "table": table, "ok": ok, "error": why,
                    "event": e,
                    "hint": ("分片不可达最常见的根因是宿主机盘满（PG 进入 recovery）。"
                             "先登该机看 df：shard_xinru2=100.64.0.6、"
                             "shard_xinru3=100.64.0.7；两块 39G 盘上备份与迁移前"
                             "旧 PG 数据占了 24G，会复发。"),
                }, ensure_ascii=False, indent=2),
            })

    firing = sum(1 for e in events if e["kind"] == FIRING)
    receipt = DeliveryReceipt()
    verdict_code = None
    if args.run and msgs:
        try:
            cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
            target = resolve_target(cfg, os.environ)
            cfg_smtp = smtp_from_env(os.environ, recipients=target.to)
        except (OSError, ValueError, DeliveryNotConfigured) as exc:
            receipt.reasons.append(str(exc))
            verdict_code = judge(receipt, firing=firing, strict=strict_enabled(os.environ),
                                 not_configured_reason=str(exc))
        else:
            if cfg_smtp is None:
                verdict_code = judge(receipt, firing=firing,
                                     strict=strict_enabled(os.environ),
                                     not_configured_reason="SMTP 凭据缺失（host/from）")
            else:
                sent = dispatch(msgs, cfg_smtp)
                receipt = DeliveryReceipt(sent=sent.get("sent", 0),
                                          failed=sent.get("failed", 0),
                                          skipped=1 if sent.get("skipped") else 0,
                                          reasons=[str(f) for f in sent.get("failures", [])])
                verdict_code = judge(receipt, firing=firing,
                                     strict=strict_enabled(os.environ))

    if args.run:
        append_events(EVENTS, events)
        save_state(STATE, {k: v.to_dict() for k, v in new_states.items()})

    print(json.dumps({
        "firing": firing, "messages": len(msgs),
        "delivery": receipt.as_dict(),
        "verdict": verdict_code.as_dict() if verdict_code else None,
        "detail": detail,
        # 状态是否跨轮：去重与「回落」通知的前提。emptyDir 下恒为 false——
        # 永远打印、永不静默（与另外三条线同一读数）。
        "state": state_info,
    }, ensure_ascii=False))
    if verdict_code is not None:
        return verdict_code.exit_code
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
