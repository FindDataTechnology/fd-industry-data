"""通知：邮件通道，单条失败不阻塞同批。

沿用 `agriculture_alerts.py` 的环境变量约定（``FD_ALERT_SMTP_*``），不新增第二套配置面。
``send_email`` 用 ``smtp_factory`` 注入，便于单测在不联网的情况下验证失败隔离。
"""
from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

ENV_KEYS = ("FD_ALERT_SMTP_HOST", "FD_ALERT_SMTP_PORT", "FD_ALERT_SMTP_USER",
            "FD_ALERT_SMTP_PASS", "FD_ALERT_SMTP_FROM", "FD_ALERT_SMTP_TO",
            "FD_ALERT_SMTP_TLS", "FD_ALERT_SMTP_SSL")


@dataclass
class SmtpConfig:
    """SMTP 连接参数。

    ``use_ssl`` 与 ``use_tls`` 是**互斥的两条路**：465 端口是隐式 TLS
    （``smtplib.SMTP_SSL``），587 端口才是 STARTTLS。阿里云 DirectMail
    （本舰队 Logto 用的邮件通道）走 465/SSL，只实现 STARTTLS 会连不上——
    实测配置为 ``smtpdm.aliyun.com:465``。
    """

    host: str
    port: int
    user: str
    password: str
    sender: str
    recipients: list[str]
    use_tls: bool = True
    use_ssl: bool = False


def smtp_config_from_env(env=None, *, recipients=None) -> SmtpConfig | None:
    """从环境读 SMTP 配置；缺关键项返回 None（调用方据此判断「无通道」）。

    ``recipients`` 给定时**替代** env 里的 ``FD_ALERT_SMTP_TO``——接收人属于线路
    声明（版本化配置），env 只作覆盖。混用两者会让「配置里明明写了收件人却因 env
    没设而被判无通道」，2026-11-20 就是这么让十条线一起失声的。
    """
    e = os.environ if env is None else env
    host = (e.get("FD_ALERT_SMTP_HOST") or "").strip()
    sender = (e.get("FD_ALERT_SMTP_FROM") or "").strip()
    if recipients:
        to = [str(x).strip() for x in recipients if str(x).strip()]
    else:
        to = [x.strip() for x in (e.get("FD_ALERT_SMTP_TO") or "").split(",") if x.strip()]
    if not host or not sender or not to:
        return None
    return SmtpConfig(
        host=host,
        port=int(e.get("FD_ALERT_SMTP_PORT") or 587),
        user=(e.get("FD_ALERT_SMTP_USER") or "").strip(),
        password=(e.get("FD_ALERT_SMTP_PASS") or ""),
        sender=sender,
        recipients=to,
        use_tls=(e.get("FD_ALERT_SMTP_TLS") or "1") not in ("0", "false", "False"),
        use_ssl=(e.get("FD_ALERT_SMTP_SSL") or "0") not in ("0", "false", "False"),
    )


def send_email(cfg: SmtpConfig, subject: str, body: str, *, smtp_factory=None) -> None:
    """发一封邮件；失败抛异常，由 ``dispatch`` 逐条兜住。"""
    msg = EmailMessage()
    msg["From"] = cfg.sender
    msg["To"] = ", ".join(cfg.recipients)
    msg["Subject"] = subject
    msg.set_content(body)

    if smtp_factory is not None:
        factory = smtp_factory
    elif cfg.use_ssl:
        factory = lambda host, port: smtplib.SMTP_SSL(host, port, timeout=20)  # noqa: E731
    else:
        factory = lambda host, port: smtplib.SMTP(host, port, timeout=20)  # noqa: E731
    smtp = factory(cfg.host, cfg.port)
    try:
        if cfg.use_tls and not cfg.use_ssl:
            smtp.starttls()
        if cfg.user:
            smtp.login(cfg.user, cfg.password)
        smtp.send_message(msg)
    finally:
        try:
            smtp.quit()
        except Exception:  # noqa: BLE001 — quit 失败不该掩盖发送结果
            pass


def dispatch(messages: list[dict], cfg: SmtpConfig | None, *, smtp_factory=None) -> dict:
    """批量发送，逐条兜住失败。

    ``messages``：``[{"rule_id", "subject", "body"}]``。
    返回 ``{"sent": n, "failed": n, "failures": [...], "skipped": bool}``；
    ``cfg is None`` 时全部记 ``skipped``（无通道不等于成功）。
    """
    if cfg is None:
        return {"sent": 0, "failed": 0, "failures": [], "skipped": True,
                "reason": "no-smtp-config"}
    sent = 0
    failures: list[dict] = []
    for m in messages:
        try:
            send_email(cfg, m["subject"], m["body"], smtp_factory=smtp_factory)
            sent += 1
        except Exception as exc:  # noqa: BLE001 — 单条失败必须不阻塞同批
            failures.append({"rule_id": m.get("rule_id"), "error": str(exc)[:200]})
    return {"sent": sent, "failed": len(failures), "failures": failures, "skipped": False}


# ── webhook 通道（add-mattermost-infrastructure 组 4）────────────────────────
#
# `delivery.CHANNELS` 一直把 `webhook` 列为合法出口，但**这里从来没有实现**，
# 而七条在产告警线全都直接调 `smtp_from_env(...)`、零处读 `target.channel`。
# 结果是：把某线配置改成 `channel: "webhook"`，它照样发一封邮件、回执记 `sent`、
# 判绿——声明被静默忽略，等于没有声明。
#
# 本函数补齐实现。投递形态是 Mattermost 的 incoming webhook：
# `POST <url>` body `{"text": "**<subject>**\n\n<body>"}`。它同时也是
# 企业微信群机器人 / ServerChan 一类的同型端点，所以这里只认 URL、不认厂商。
#
# 用 stdlib 的 urllib 而不是引入 requests：本包没有 HTTP 依赖，为一个 POST
# 加一条依赖不值得，且 urllib 足够表达「超时 + 非 2xx 即失败」。

WEBHOOK_TIMEOUT_SEC = 10


def send_webhook(url: str, subject: str, body: str, *, opener=None) -> None:
    """发一条 webhook 消息；失败抛异常，由 ``dispatch_webhook`` 逐条兜住。

    ``opener`` 是单测注入点（不联网也能验证失败隔离），与 ``smtp_factory`` 同形。
    """
    import json
    import urllib.error
    import urllib.request

    if not url or not url.strip():
        raise ValueError("empty webhook url")
    payload = json.dumps({"text": f"**{subject}**\n\n{body}"}).encode("utf-8")
    req = urllib.request.Request(
        url.strip(),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # 非 2xx 必须当失败：Mattermost 对未知 webhook 回 404，对吊销的回 403，
    # 两者都不该被记成"已送达"。
    open_fn = opener or urllib.request.urlopen
    with open_fn(req, timeout=WEBHOOK_TIMEOUT_SEC) as resp:
        code = getattr(resp, "status", None) or resp.getcode()
        if code and int(code) >= 400:
            raise RuntimeError(f"webhook returned {code}")


def dispatch_webhook(messages: list[dict], url: str | None, *, opener=None) -> dict:
    """批量发 webhook，逐条兜住失败；返回形状与 ``dispatch`` 逐字段同形。"""
    if not url or not str(url).strip():
        return {"sent": 0, "failed": 0, "failures": [], "skipped": True,
                "reason": "no-webhook-url", "channel": "webhook"}
    sent = 0
    failures: list[dict] = []
    for m in messages:
        try:
            send_webhook(url, m.get("subject", ""), m.get("body", ""), opener=opener)
            sent += 1
        except Exception as exc:  # noqa: BLE001 — 单条失败必须不阻塞同批
            failures.append({"rule_id": m.get("rule_id"), "error": str(exc)[:200]})
    return {"sent": sent, "failed": len(failures), "failures": failures,
            "skipped": False, "channel": "webhook"}


# ── 按声明的通道分派（add-mattermost-infrastructure 组 4）───────────────────
#
# 这一步把「声明的出口」变成真的。三条规则：
#
#   1. 声明了哪个通道就走哪个通道 —— MUST NOT 忽略声明、固定走某一种。
#   2. 声明了本实现不具备的通道（当前是 `relay`）→ 抛 DeliveryNotConfigured
#      并点名，MUST NOT 改用别的通道投递再把结局记成成功。
#   3. 实际用过的通道记进产物 —— 读者读到的必须是事实，不是配置里的愿望。

WEBHOOK_URL_ENV = "FD_ALERT_WEBHOOK_URL"


def webhook_url_from_env(env=None) -> str | None:
    """webhook 通道的接收端；未配置返回 None（调用方据此判 not_configured）。"""
    e = os.environ if env is None else env
    url = (e.get(WEBHOOK_URL_ENV) or "").strip()
    return url or None


def dispatch_any(messages: list[dict], target, env=None, *, smtp_factory=None,
                 opener=None) -> dict:
    """按 ``target.channel`` 分派。

    ``target`` 是 ``delivery.DeliveryTarget``。未实现的通道抛
    ``delivery.DeliveryNotConfigured``——各线的 ``judge()`` 已经把它当
    「配置错误 → 判红」处理，所以这里不需要发明新的错误路径。
    """
    from .delivery import DeliveryNotConfigured  # noqa: PLC0415 — 避免循环导入

    e = os.environ if env is None else env
    channel = getattr(target, "channel", "")

    if channel == "smtp":
        # 接收人来自**线路声明**（target.to），env 只作覆盖 —— 这是 alert-delivery
        # spec 的要求，也是 2026-11-20 十条告警线同时失声的根因：dispatch 走的是
        # smtp_config_from_env()，它额外要求 FD_ALERT_SMTP_TO，而十个在产 cronjob
        # 一个都没注入它。于是 resolve_target 校验通过、这里返回 None、
        # dispatch() 记 skipped —— 回执里 {"sent":0,"skipped":true}，判据全对、
        # 出口全断。
        declared = tuple(getattr(target, "to", ()) or ())
        cfg = smtp_config_from_env(e, recipients=declared) if declared else smtp_config_from_env(e)
        if cfg is None:
            # 声明了 smtp 却没有可用配置：接收人要么来自 target（上面已覆盖），
            # 要么来自 env。两处都空就是配置错误，不是「本轮无告警」。
            raise DeliveryNotConfigured(
                "channel=smtp 但既无线路声明的接收人、环境也缺连接要素"
                "（FD_ALERT_SMTP_HOST / FD_ALERT_SMTP_FROM）；"
                "要么在 delivery.to 里写接收人，要么补齐 env"
            )
        out = dispatch(messages, cfg, smtp_factory=smtp_factory)
        out["channel"] = "smtp"
        return out

    if channel == "webhook":
        url = webhook_url_from_env(e)
        if url is None:
            # 配置里声明了 webhook 但环境没给接收端 —— 这是配置错误，
            # 不是"本轮无告警"。抛出去让 judge() 判红。
            raise DeliveryNotConfigured(
                f"channel=webhook 但环境缺 {WEBHOOK_URL_ENV}；"
                "要么补上接收端，要么把该线的 channel 改回 smtp"
            )
        return dispatch_webhook(messages, url, opener=opener)

    if channel == "relay":
        raise DeliveryNotConfigured(
            "channel=relay 已声明但未实现（本期只实现 smtp 与 webhook）；"
            "改用 channel=webhook 指向一个真实的接收端，或改回 smtp。"
            "本实现刻意不回退到别的通道——那会让「改了配置」与「改了配置且生效」"
            "在产物里长得一样。"
        )

    raise DeliveryNotConfigured(
        f"channel={channel!r} 未知；合法值见 delivery.CHANNELS"
    )
