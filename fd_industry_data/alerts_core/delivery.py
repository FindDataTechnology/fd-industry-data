"""告警投递口径：出口声明、送达回执、死信判据（openspec: wire-alert-delivery）。

**为什么单独一个模块**：`notify.py` 只管"把一条消息发出去"，答不了下面三个问题，
而这三件事恰恰是"告警写好了但没人收到"的根因：

1. **这条线声明了出口和接收人吗？** 现状是缺省即"落文件算完成"。
2. **本轮到底送出去几条？** 现状产物里只有 ``sent/skipped`` 计数，firing 与送达没有对照。
3. **该响而一条都没送出去时，这次运行算成功吗？** 现状算成功（邮件 skipped 被当成正常结束）。

本模块只做判定，不做网络。发送仍走 ``notify.dispatch`` / 后续的 relay 实现，
这样"口径"与"通道"分开测：口径必须能离线钉死，通道的连通性另测。

判定规则（spec: alert-delivery）：

- 本轮 ``firing > 0`` 且 ``sent == 0`` → 死信，**判红**（strict 关闭时降级为 warn，
  默认关闭是为了在通道还没接线时不把既有工作流一次性打红）。
- 本轮 ``firing == 0`` → ``sent == 0`` 是正常静默期，判绿。
- 有失败但仍有送达 → 非死信，但进程应非零退出（部分失败要被人看见）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

# 出口渠道标识。relay = 平台 bot relay（POST /api/bots/relay/send），
# smtp = 邮件（既有 FD_ALERT_SMTP_*），webhook = 同型 ServerChan 类端点。
CHANNELS = ("relay", "smtp", "webhook")

STRICT_ENV = "FD_ALERT_DELIVERY_STRICT"


@dataclass(frozen=True)
class DeliveryTarget:
    """一条告警线声明出来的投递目标。"""

    channel: str
    to: tuple[str, ...]
    min_severity: str = "warning"

    def as_dict(self) -> dict[str, Any]:
        return {"channel": self.channel, "to": list(self.to), "min_severity": self.min_severity}


class DeliveryNotConfigured(Exception):
    """配置里没有可用的投递出口。

    这不是"跳过"，是配置错误：调用方 MUST 让它可见（非零退出或 degraded 标记），
    MUST NOT 当成"本轮无告警"。
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def resolve_target(config: Mapping[str, Any], env: Mapping[str, str] | None = None) -> DeliveryTarget:
    """从线路配置读 ``delivery`` 段；缺失或不可用则抛 ``DeliveryNotConfigured``。

    ``env`` 只用于**通道级**凭据是否具备的判定（例如 smtp 需要 host/from/to），
    接收人本身 MUST 写在配置里——把接收人藏在环境变量里，等于没人知道谁会收到。
    """
    env = env or {}
    raw = config.get("delivery")
    if not isinstance(raw, Mapping):
        raise DeliveryNotConfigured(
            "配置缺少 delivery 段（需要 channel / to）；"
            "缺省不等于'无告警'——落文件成功不能算送达"
        )

    channel = str(raw.get("channel") or "").strip().lower()
    if channel not in CHANNELS:
        raise DeliveryNotConfigured(
            f"delivery.channel={channel!r} 不在 {CHANNELS} 之内；"
            "未定型出口不得留空，先定出口再写规则"
        )

    to = tuple(str(x).strip() for x in (raw.get("to") or []) if str(x).strip())
    if not to:
        raise DeliveryNotConfigured("delivery.to 为空：没有接收人，告警等于没有")

    if channel == "smtp":
        missing = [k for k in ("FD_ALERT_SMTP_HOST", "FD_ALERT_SMTP_FROM") if not env.get(k)]
        if missing:
            raise DeliveryNotConfigured(
                f"channel=smtp 但环境缺 {', '.join(missing)}；"
                "要么补齐凭据，要么改用已跑通的 relay"
            )

    severity = str(raw.get("min_severity") or "warning").strip().lower()
    return DeliveryTarget(channel=channel, to=to, min_severity=severity)


@dataclass
class DeliveryReceipt:
    """一轮投递的结局。三个计数互斥，加起来等于本轮的候选消息数。"""

    sent: int = 0
    failed: int = 0
    skipped: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.sent + self.failed + self.skipped

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"sent": self.sent, "failed": self.failed, "skipped": self.skipped}
        if self.reasons:
            out["reasons"] = list(self.reasons)
        return out


@dataclass(frozen=True)
class RunVerdict:
    """一次运行的投递判决。

    ``exit_code``：0=没该响的事；1=该响却一条没送出去（死信）；
    ``2``=有送达但也有失败（部分失败）；``3``=配置错误（没有可用出口）。
    """

    code: str  # ok | dead_letter | partial_failure | not_configured
    exit_code: int
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "exit_code": self.exit_code, "message": self.message}


def judge(
    receipt: DeliveryReceipt,
    *,
    firing: int,
    strict: bool = False,
    not_configured_reason: str | None = None,
) -> RunVerdict:
    """按 spec 判定本轮该不该判红。

    ``firing`` 是本轮处于告警态、且达到投递严重级的消息数（即"应该有人知道"的条数）。
    """
    if not_configured_reason:
        # 配置错误优先于一切：没有出口时，连"本轮无告警"都不可信。
        return RunVerdict(
            code="not_configured",
            exit_code=3,
            message=f"投递未配置：{not_configured_reason}",
        )

    if firing > 0 and receipt.sent == 0:
        if strict:
            return RunVerdict(
                code="dead_letter",
                exit_code=1,
                message=(
                    f"本轮 {firing} 条告警应当送达，实际送达 0 条"
                    f"（failed={receipt.failed} skipped={receipt.skipped}）——判红"
                ),
            )
        return RunVerdict(
            code="dead_letter",
            exit_code=0,
            message=(
                f"本轮 {firing} 条告警应当送达但 0 条送达——已标记死信；"
                f"因 {STRICT_ENV} 未开启故不判红（通道接线后必须开启）"
            ),
        )

    if receipt.failed > 0:
        return RunVerdict(
            code="partial_failure",
            exit_code=2,
            message=(
                f"送达 {receipt.sent} 条，失败 {receipt.failed} 条"
                f"（单条失败不阻塞同批，但批次有失败要非零退出）"
            ),
        )

    if firing == 0:
        return RunVerdict(code="ok", exit_code=0, message="本轮无告警态消息，零送达属正常静默期")

    return RunVerdict(code="ok", exit_code=0, message=f"本轮 {firing} 条告警，送达 {receipt.sent} 条")


def strict_enabled(env: Mapping[str, str] | None = None) -> bool:
    """死信是否判红。默认关闭：通道接线前开启会把既有工作流一次性打红。"""
    env = env or {}
    return str(env.get(STRICT_ENV) or "").strip() in ("1", "true", "True", "yes")


def smtp_from_env(env: Mapping[str, str] | None = None, *,
                  recipients: Iterable[str] | None = None) -> Any:
    """按 env 构造 SMTP 参数，**不要求 env 里有收件人**。

    为什么不用 ``notify.smtp_config_from_env()``：那个函数把 ``FD_ALERT_SMTP_TO``
    也当成必需项，而收件人应当能来自**版本化的线路配置**。两者混用会出现
    「配置里明明写了收件人，却因为 env 没设 TO 被判成无通道」——
    2026-11-08 接花卉/餐饮投递时踩到，回执里是 no-smtp-config，看起来像没配凭据。

    这里只要求连接四要素（host / from / user / pass 中 host 与 from 必需），
    收件人由调用方给；给不出就返回 ``None``（=无通道）。
    """
    from .notify import SmtpConfig  # noqa: PLC0415 — 避免循环导入

    env = os.environ if env is None else env
    host = str(env.get("FD_ALERT_SMTP_HOST") or "").strip()
    sender = str(env.get("FD_ALERT_SMTP_FROM") or "").strip()
    to = [str(x).strip() for x in (recipients or []) if str(x).strip()]
    if not host or not sender or not to:
        return None
    return SmtpConfig(
        host=host,
        port=int(env.get("FD_ALERT_SMTP_PORT") or 587),
        user=str(env.get("FD_ALERT_SMTP_USER") or "").strip(),
        password=str(env.get("FD_ALERT_SMTP_PASS") or ""),
        sender=sender,
        recipients=to,
        use_tls=str(env.get("FD_ALERT_SMTP_TLS") or "1") not in ("0", "false", "False"),
        use_ssl=str(env.get("FD_ALERT_SMTP_SSL") or "0") not in ("0", "false", "False"),
    )


def count_firing(events: Iterable[Mapping[str, Any]], *, min_severity: str = "warning") -> int:
    """数出"应该有人知道"的条数：只认 firing 态，且严重级达到投递门槛。

    严重级顺序：info < warning < critical。``resolved`` 不计入——恢复通知失败
    不构成"该响没响"，但它的失败仍进 ``receipt.failed``。
    """
    order = {"info": 0, "warning": 1, "critical": 2}
    floor = order.get(str(min_severity).lower(), 1)
    n = 0
    for ev in events:
        if str(ev.get("kind") or "") != "firing":
            continue
        if order.get(str(ev.get("severity") or "warning").lower(), 1) < floor:
            continue
        n += 1
    return n
