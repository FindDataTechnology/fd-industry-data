"""事件留痕：JSONL 追加，按 ``(rule_id, kind, key)`` 幂等。

为什么要幂等而不是「反正状态文件记得」：状态文件与事件留痕是两份独立产物，
任一丢失都要能靠另一份重建结论。重跑同一批判据不得在留痕里长出重复行——
否则「这一期报了几次」这种问题永远查不清。
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def event_key(e: dict) -> tuple[str, str, str]:
    """事件的幂等键。"""
    return (str(e.get("rule_id") or ""), str(e.get("kind") or ""), str(e.get("key") or ""))


def read_events(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # 坏行跳过但不阻塞：留痕坏了不该让巡检崩
    return out


def append_events(path: str | Path, events: list[dict]) -> int:
    """追加事件，跳过已存在的 ``(rule_id, kind, key)``。返回真正写入的条数。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = {event_key(e) for e in read_events(p)}
    written = 0
    with p.open("a", encoding="utf-8") as fh:
        for e in events:
            k = event_key(e)
            if k in existing:
                continue
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
            existing.add(k)
            written += 1
    return written


def load_state(path: str | Path) -> dict:
    """读状态文件（``{rule_id: state_dict}``）；不存在或损坏时返回空。"""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def state_health(path: str | Path) -> dict:
    """本轮**能不能用到持久状态**——去重与「回落」通知的前提，永远打印、永不静默。

    线上四条告警线的 `/reports` 是 `emptyDir: {}`（pod 级目录，Pod 结束即消失）⇒
    每轮 `load_state()` 都返回 `{}` ⇒ `state.py` 里的去重分支（`prev.state == FIRING`
    ⇒ 不重复）与 RESOLVED 分支**都不生效**：一条持续越阈的规则每轮重发一封，
    「回落」则永远不发。2026-11-14 查出（`openspec/changes/ciyuan-consumer-surface`
    任务 3.7），当日已裁定**不挂 PVC**。理由的第一条是决定性的：告警链路挂节点本地卷，
    那台机一挂这四条线自己就 Pending，而它们存在的第一目的正是报告节点故障。

    裁定留下的增量就是这一项——**别让「去重没生效」只能靠人事后推断**。
    读数怎么用：同一条线若长期 `persisted: false` 而它又反复 firing，就是
    「状态目录不跨轮」的直接证据，不必再去翻清单；出现一次 `persisted: true`
    即说明该目录确实跨 Pod 存活。
    """
    p = Path(path)
    state = load_state(p)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        writable = os.access(p.parent, os.W_OK)
    except OSError:
        writable = False
    if state:
        note = f"读到 {len(state)} 条规则的历史状态：去重与回落通知本轮生效"
    elif not writable:
        note = ("本轮无历史状态，且状态目录不可写（save_state 也会失败）："
                "去重与回落通知永远不会生效")
    else:
        note = ("本轮无历史状态（首轮，或状态目录不跨轮如 emptyDir）："
                "去重与回落通知本轮不生效——同一读数连续出现即为状态不跨轮")
    return {"path": str(p), "persisted": bool(state), "rules": len(state),
            "dir_writable": writable, "note": note}


def save_state(path: str | Path, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                 encoding="utf-8")
