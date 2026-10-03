"""确定性分诊：遥测 runs -> category / needs_human 判定（无随机、无 IO）。

输入 ``runs`` 按时间升序（``classify`` 内部仍会按 started_at 防御性重排）。
只考察“尾部连续 failed 段”（最近一次成功之后的连续失败）；更早的失败不参与
分诊。``source`` / ``unit`` 仅随工单携带，规则本身不依赖它们。

规则（优先级 3 > 4 > 5 > 6 > 7 > 9 > 8；规则 1/2 先于以上）：

1. 健康短路：最近一次 success 距今 <= expected_period_hours *
   freshness_multiplier，且尾部连续 failed < consecutive_failures -> 健康
   （Verdict(None, needs_human=False)，不出单）。
2. 证据不足：尾部 failed 样本 < min_runs_for_verdict 且不满足健康 ->
   needs_human=True（不猜分类）。
3. 反爬签名（最高优先，任一尾部失败命中即定，不允许被多数票淹没）：最近失败含
   http_status in {403, 429}，或 error 命中
   waf|challenge|captcha|forbidden|access denied|cloudflare|瑞数|云盾|人机
   -> category="contract", suspected_anti_bot=True，
   action="更换数据表面（官方 API/替代入口）或退役；禁止逆向"。
4. 源死亡：error 命中 dns|nodename|resolve|not found|404|410|gone|refused|domain
   （或 http_status in {404, 410}），且最近 success 距今 > 7 *
   expected_period_hours（或无 success）-> "source-dead"，action="退役评估"。
5. 网络层：error 命中 timeout|timed out|connect|reset|econn|tls|ssl|unreachable|
   502|503|504（或 http_status in {502, 503, 504}）-> "network"，
   action="出口切换/代理复测（不改代码）"。
6. 结构层：error 命中 parse|json decode|keyerror|selector|no element|table|column|
   解码|empty，且 http_status in {200, None} -> "structure"，action="定向修 parser"。
7. 契约层：error 命中 400|401|param|parameter|invalid|endpoint|moved|redirect|
   schema|版本（或 http_status in {400, 401}）-> "contract"，
   action="重新侦察数据表面"。
8. 分歧/未命中：未归类票占多数，或两类票数相同 -> needs_human=True，
   category=None（人工分诊）。
9. 兜底：尾部失败全部为同一未知模式（错误指纹相同）且 >= consecutive_failures
   次完全相同错误指纹 -> "fallback"，action="整段重生成（兜底）"。

判定方式：先做规则 3 的签名短路；否则对尾部连续 failed 段逐 run 按 4~7 顺序取
第一个命中规则，再多数投票（None 未命中作为一票参与）；未命中占多数或平票走
规则 8 转人工（规则 9 优先于规则 8）。``rule_trace`` 记录命中规则路径，供工单
evidence 引用。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

ANTIBOT_ACTION = "更换数据表面（官方 API/替代入口）或退役；禁止逆向"
SOURCE_DEAD_ACTION = "退役评估"
NETWORK_ACTION = "出口切换/代理复测（不改代码）"
STRUCTURE_ACTION = "定向修 parser"
CONTRACT_ACTION = "重新侦察数据表面"
FALLBACK_ACTION = "整段重生成（兜底）"
HUMAN_ACTION = "人工分诊（失败模式分歧/无法归类）"
INSUFFICIENT_ACTION = "补充遥测样本后复检（证据不足）"

_ANTIBOT_HTTP = (403, 429)
_DEAD_HTTP = (404, 410)
_NET_HTTP = (502, 503, 504)
_CONTRACT_HTTP = (400, 401)

_ANTIBOT_RE = re.compile(
    r"waf|challenge|captcha|forbidden|access denied|cloudflare|瑞数|云盾|人机|\b(?:403|429)\b",
    re.IGNORECASE,
)
_DEAD_RE = re.compile(
    r"dns|nodename|resolve|not found|\b(?:404|410)\b|gone|refused|domain",
    re.IGNORECASE,
)
_NET_RE = re.compile(
    r"timeout|timed out|connect|reset|econn|tls|ssl|unreachable|\b(?:502|503|504)\b",
    re.IGNORECASE,
)
_STRUCT_RE = re.compile(
    r"parse|json decode|keyerror|selector|no element|table|column|解码|empty",
    re.IGNORECASE,
)
_CONTRACT_RE = re.compile(
    r"\b(?:400|401)\b|param|parameter|invalid|endpoint|moved|redirect|schema|版本",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Run:
    """单次运行遥测。started_at 为 ISO8601；error 为错误摘要文本（可空）。"""

    started_at: str
    status: str  # "success" | "failed"
    rows: int | None = None
    error: str = ""
    http_status: int | None = None


@dataclass(frozen=True)
class Thresholds:
    """分诊阈值（集中声明，便于 inspector/config 覆盖）。"""

    consecutive_failures: int = 3
    freshness_multiplier: float = 2.5
    min_runs_for_verdict: int = 3


@dataclass(frozen=True)
class Verdict:
    """分诊结论。category=None 且 needs_human=False 表示健康（不出单）。"""

    category: str | None
    needs_human: bool
    suspected_anti_bot: bool
    suggested_action: str
    rule_trace: str


def _parse_ts(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _fingerprint(text: str) -> str:
    """错误指纹：小写、数字折叠为 #、空白归一，用于“完全相同错误”判定。"""
    normalized = re.sub(r"\d+", "#", (text or "").lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _snip(text: str, limit: int = 120) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _fmt_hours(value: float | None) -> str:
    return "none" if value is None else f"{value:.2f}"


def _matches_anti_bot(run: Run) -> bool:
    if run.http_status in _ANTIBOT_HTTP:
        return True
    return bool(_ANTIBOT_RE.search(run.error or ""))


def _trailing_failed(runs: list[Run]) -> list[Run]:
    """尾部连续 failed 段（时间升序返回）。"""
    tail: list[Run] = []
    for run in reversed(runs):
        if run.status == "failed":
            tail.append(run)
        else:
            break
    tail.reverse()
    return tail


def _classify_run(run: Run, stale: bool) -> str | None:
    """对单次失败按 4~7 的顺序取第一个命中；未命中返回 None（规则 3 已短路）。"""
    error = run.error or ""
    if (_DEAD_RE.search(error) or run.http_status in _DEAD_HTTP) and stale:
        return "source-dead"
    if _NET_RE.search(error) or run.http_status in _NET_HTTP:
        return "network"
    if _STRUCT_RE.search(error) and run.http_status in (200, None):
        return "structure"
    if _CONTRACT_RE.search(error) or run.http_status in _CONTRACT_HTTP:
        return "contract"
    return None


_NOTES = {
    "source-dead": (4, SOURCE_DEAD_ACTION),
    "network": (5, NETWORK_ACTION),
    "structure": (6, STRUCTURE_ACTION),
    "contract": (7, CONTRACT_ACTION),
}


def classify(
    source: str,
    unit: str,
    expected_period_hours: float,
    runs: list[Run],
    now: datetime,
    thresholds: Thresholds = Thresholds(),
) -> Verdict:
    """确定性分诊入口。规则与优先级见模块 docstring。"""
    ordered = sorted(runs, key=lambda run: _parse_ts(run.started_at))
    period = float(expected_period_hours)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    last_success = None
    for run in ordered:
        if run.status == "success":
            last_success = run
    last_success_age = (
        None
        if last_success is None
        else (now - _parse_ts(last_success.started_at)).total_seconds() / 3600.0
    )

    tail = _trailing_failed(ordered)
    tail_failed = len(tail)

    # 规则 1：健康短路
    fresh_cutoff = period * thresholds.freshness_multiplier
    fresh = last_success_age is not None and last_success_age <= fresh_cutoff
    if fresh and tail_failed < thresholds.consecutive_failures:
        trace = (
            f"rule1:healthy last_success_age={_fmt_hours(last_success_age)}h"
            f"<={_fmt_hours(fresh_cutoff)}h"
            f" tail_failed={tail_failed}<{thresholds.consecutive_failures}"
        )
        return Verdict(None, False, False, "", trace)

    # 规则 2：证据不足
    if tail_failed < thresholds.min_runs_for_verdict:
        trace = (
            f"rule2:insufficient-evidence tail_failed={tail_failed}"
            f"<min_runs_for_verdict={thresholds.min_runs_for_verdict}"
            f" last_success_age={_fmt_hours(last_success_age)}h"
        )
        return Verdict(None, True, False, INSUFFICIENT_ACTION, trace)

    # 规则 3：反爬签名（最高优先，签名即定）
    for index, run in enumerate(tail):
        if _matches_anti_bot(run):
            trace = (
                f"rule3:anti-bot run#{index + 1}/{tail_failed}"
                f" http_status={run.http_status!r} error={_snip(run.error)!r}"
            )
            return Verdict("contract", False, True, ANTIBOT_ACTION, trace)

    # 规则 4：源死亡的前置条件（最近 success 距今 > 7*expected 或无 success）
    stale = last_success_age is None or last_success_age > 7.0 * period

    labels = [_classify_run(run, stale) for run in tail]

    # 规则 9：兜底（全部未命中的同一未知模式，且达到连续失败阈值）
    if all(label is None for label in labels) and tail_failed >= thresholds.consecutive_failures:
        fingerprints = {_fingerprint(run.error) for run in tail}
        if len(fingerprints) == 1 and next(iter(fingerprints)):
            trace = (
                "rule9:fallback"
                f" identical-unknown-fingerprint x{tail_failed}"
                f" (>=consecutive_failures={thresholds.consecutive_failures})"
                f" fingerprint={_snip(next(iter(fingerprints)))!r}"
            )
            return Verdict("fallback", False, False, FALLBACK_ACTION, trace)

    # 投票（None 未命中作为一票）
    counts: dict[str | None, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    best = max(counts.values())
    winners = [label for label, count in counts.items() if count == best]

    if len(winners) != 1:
        trace = (
            f"rule8:divergent tie labels={counts} tail_failed={tail_failed}"
            f" last_success_age={_fmt_hours(last_success_age)}h"
        )
        return Verdict(None, True, False, HUMAN_ACTION, trace)

    winner = winners[0]
    if winner is None:
        trace = (
            f"rule8:unclassified-majority labels={counts} tail_failed={tail_failed}"
            f" last_success_age={_fmt_hours(last_success_age)}h"
        )
        return Verdict(None, True, False, HUMAN_ACTION, trace)

    rule_no, action = _NOTES[winner]
    trace = (
        f"rule{rule_no}:{winner} votes={counts[winner]}/{tail_failed}"
        f" labels={counts}"
        + (f" stale_success_age={_fmt_hours(last_success_age)}h>{_fmt_hours(7.0 * period)}h"
           if winner == "source-dead" else "")
    )
    return Verdict(winner, False, False, action, trace)