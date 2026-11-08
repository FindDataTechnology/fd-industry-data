"""告警状态机与幂等去重。

**冷却单位是判据期次，不是墙钟。** 银行指标按季/年披露；按小时冷却会让同一期在日巡检里
反复放行（存量三份副本正是这么分叉的）。故本内核用 `key`（判据期次，如 ``2025-12-31``；
事件规则里是文书号）做唯一性：同 `(规则, key)` 最多一条 firing。

两条硬纪律（都会静默出错，故单独立测）：

1. **同 key 不重复**：同一期持续越阈只留一条 firing，不刷新、不重复通知。
2. **判据缺失 ≠ 恢复正常**：``value is None`` 判 ``insufficient_data``，**不改状态、
   不发 resolved**。否则一次缺数就会发出一条假的恢复通知。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

QUIET = "quiet"
FIRING = "firing"

#: 判定结果（对外可见的用词，与 spec 一致）
FIRING_V = "firing"
RESOLVED = "resolved"
OK = "quiet"
INSUFFICIENT = "insufficient_data"
PENDING = "pending"

_OPERATORS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
}


def compare(value: float, operator: str, threshold: float) -> bool:
    """按操作符比较；未知操作符直接抛错（宁可红，不静默当 false）。"""
    fn = _OPERATORS.get(operator)
    if fn is None:
        raise ValueError(f"未知操作符: {operator!r}（允许 {sorted(_OPERATORS)}）")
    return fn(float(value), float(threshold))


@dataclass
class RuleState:
    """单条规则的持久状态。``seen`` 只用于事件规则（已发过的 key，有界）。"""

    state: str = QUIET
    episode: int = 0
    last_key: str | None = None
    seen: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"state": self.state, "episode": self.episode,
                "last_key": self.last_key, "seen": list(self.seen)}

    @classmethod
    def from_dict(cls, d: dict | None) -> "RuleState":
        if not d:
            return cls()
        return cls(state=d.get("state", QUIET), episode=int(d.get("episode", 0)),
                   last_key=d.get("last_key"), seen=list(d.get("seen") or []))


def make_event(kind: str, rule_id: str, *, key: str, at: datetime,
               value=None, threshold=None, detail: str = "") -> dict:
    """构造一条事件。``kind`` ∈ {firing, resolved}。"""
    return {"kind": kind, "rule_id": rule_id, "key": str(key),
            "at": at.astimezone().isoformat() if at.tzinfo else at.isoformat(),
            "value": value, "threshold": threshold, "detail": detail}


def evaluate_threshold(prev: RuleState, *, triggered: bool | None,
                       key: str, at: datetime, value=None, threshold=None,
                       detail: str = "") -> tuple[RuleState, list[dict], str]:
    """阈值规则判定。返回 ``(新状态, 事件[], 判定用词)``。

    ``triggered`` 由调用方给出——**内核不猜业务方向**。这一点是踩出来的：
    早先让内核拿 ``value >= 10.5`` 直接当告警条件，于是「资本充足率 13.77 高于红线 10.5」
    被判成 firing。监管指标多是「不得低于」，告警条件应该是**要求被违反**，
    这个方向属于业务语义，不该藏在通用内核里。

    ``triggered is None``：判据缺失 → ``insufficient_data``，**不改状态、不发 resolved**
    （缺数不是恢复，否则一次缺数就发假恢复通知）。
    """
    if triggered is None:
        return prev, [], INSUFFICIENT

    key = str(key)
    if triggered:
        if prev.state == FIRING and prev.last_key == key:
            return prev, [], FIRING          # 同一期已报过：不重复、不刷新
        new = replace(prev, state=FIRING, episode=prev.episode + 1, last_key=key)
        return new, [make_event(FIRING_V, "", key=key, at=at, value=value,
                                threshold=threshold, detail=detail or "判据被违反")], FIRING

    if prev.state == FIRING:
        new = replace(prev, state=QUIET, last_key=key)
        return new, [make_event(RESOLVED, "", key=key, at=at, value=value,
                                threshold=threshold,
                                detail=detail or "判据回到正常")], RESOLVED

    return replace(prev, last_key=key), [], OK


def evaluate_event(prev: RuleState, *, new_keys: list[str], at: datetime,
                   max_seen: int = 500) -> tuple[RuleState, list[dict], str]:
    """事件规则：每个新 key 一条 firing，**永不 resolved**（事件是发生过的事实）。

    ``seen`` 有界保留最近 ``max_seen`` 个 key；超出后老 key 会被忘掉而可能再报一次——
    故 ``max_seen`` 必须大于「一个巡检周期内可能出现的旧 key 数」，默认 500 足够日频巡检。
    """
    seen = list(prev.seen)
    fresh = [k for k in dict.fromkeys(str(k) for k in new_keys) if k not in seen]
    events: list[dict] = []
    for k in fresh:
        events.append(make_event(FIRING_V, "", key=k, at=at, detail="新增事件"))
    if fresh:
        seen.extend(fresh)
        seen = seen[-max_seen:]
    st = replace(prev, state=FIRING if seen else QUIET, seen=seen)
    return st, events, FIRING if fresh else OK


def attach_rule(events: list[dict], rule_id: str) -> list[dict]:
    """把事件挂到具体规则上（``evaluate_*`` 只负责判定，不知道规则名）。"""
    return [{**e, "rule_id": rule_id} for e in events]
