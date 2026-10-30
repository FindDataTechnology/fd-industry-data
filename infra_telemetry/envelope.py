"""统一响应信封 + 通道失败归因 + 凭据脱敏。

契约（spec: infra-telemetry-surface R3 新鲜度与失败归因 / R4 零凭据零端点）：
- 成功返回 SHALL 带 ``as_of``（UTC ISO8601）与逐通道状态列表；
- 任一上游通道故障 SHALL 显式 ``reason ∈ REASONS`` + 通道名，SHALL NOT
  伪装成"全绿"或空集——ok=False 时 items 仍带健康通道的真实读数（部分可读）；
- 所有自由文本（错误 detail、error_head 等）出信封前 SHALL 过 ``scrub()``。

纯 stdlib（与 health 包同路径约束：无驱动依赖）。
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any

REASONS = frozenset(
    {"upstream_unreachable", "timeout", "unauthorized", "not_configured"}
)

# 通道失败信号：各通道 fetcher 只 raise 这一种，工具层捕获入信封
class ChannelError(Exception):
    def __init__(self, channel: str, reason: str, detail: str = ""):
        assert reason in REASONS, f"unknown failure reason: {reason!r}"
        self.channel = channel
        self.reason = reason
        self.detail = detail
        super().__init__(f"{channel}: {reason}: {detail[:200]}")


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def channel_status(errors: dict[str, ChannelError], probed: list[str]) -> list[dict]:
    """被探测通道全列出：成功记 ok，失败带 reason（通道名+脱敏 detail）。"""
    out = []
    for name in probed:
        err = errors.get(name)
        if err is None:
            out.append({"channel": name, "status": "ok"})
        else:
            out.append(
                {
                    "channel": name,
                    "status": "failed",
                    "reason": err.reason,
                    "detail": scrub(err.detail),
                }
            )
    return out


def envelope(
    tool: str,
    items: list[dict],
    summary: dict[str, Any],
    *,
    channels: list[dict],
    as_of: str | None = None,
) -> dict:
    """终形态。ok=False 当且仅当存在 failed 通道（spec R3：禁假绿）。"""
    ok = not any(c.get("status") == "failed" for c in channels)
    return {
        "tool": tool,
        "ok": ok,
        "as_of": as_of or utc_now_iso(),
        "channels": channels,
        "summary": summary,
        "items": items,
    }


# ── 凭据脱敏（R4）──────────────────────────────────────────────────────────
# 保守名单：provider token 前缀 / Bearer / URL userinfo / 引号内敏感键值 / DSN。
# 长 base64 不盲扫（会误杀合法标识名），改为"键名驱动"。
_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(?:ghp|github_pat|gho|ghu|ghs)_[A-Za-z0-9]{6,}"),
    re.compile(r"\bsha256_[A-Za-z0-9]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]{8,}=*"),
    re.compile(r"(?i)\b(?:postgres(?:ql|gres)|mysql|mongodb(?:\+srv)?|redis|amqp)://[^/\s]*@"),
    re.compile(
        r"""(?i)(["']?(?:token|secret|password|passwd|api[_-]?key|authorization|access[_-]?key|kubeconfig|dsn)["']?\s*[:=]\s*)(["'])[^"']*\2"""
    ),
    re.compile(r"(?i)([?&](?:token|password|passwd|secret|key|access_token)=)[^&\"'\s]+"),
    # 裸 key=value / key: value 形态（无引号包裹的 password=SuperSecret123 等）
    re.compile(
        r"(?i)(\b(?:token|passwd|password|secret|api[_-]?key|access[_-]?key|access_token|authorization|kubeconfig|dsn)\s*[=:]\s*[\"']?)[^\"'\s,}]+"
    ),
]


def scrub(text: Any) -> Any:
    """对任意值递归脱敏；非字符串原样返回（结构内字符串全扫）。"""
    if isinstance(text, str):
        out = text
        for pat in _PATTERNS:
            out = pat.sub(_redact(pat), out)
        return out
    if isinstance(text, dict):
        return {k: scrub(v) for k, v in text.items()}
    if isinstance(text, (list, tuple)):
        return [scrub(v) for v in text]
    return text


def _redact(pat: re.Pattern):
    def repl(m: re.Match) -> str:
        full = m.group(0)
        # 带捕获前缀的模式保留前缀，值替换
        if m.lastindex:
            return m.group(1) + "[REDACTED]"
        return "[REDACTED]"

    return repl
