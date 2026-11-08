"""事件留痕：JSONL 追加，按 ``(rule_id, kind, key)`` 幂等。

为什么要幂等而不是「反正状态文件记得」：状态文件与事件留痕是两份独立产物，
任一丢失都要能靠另一份重建结论。重跑同一批判据不得在留痕里长出重复行——
否则「这一期报了几次」这种问题永远查不清。
"""
from __future__ import annotations

import json
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


def save_state(path: str | Path, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                 encoding="utf-8")
