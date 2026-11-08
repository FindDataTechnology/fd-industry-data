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


def smtp_config_from_env(env=None) -> SmtpConfig | None:
    """从环境读 SMTP 配置；缺关键项返回 None（调用方据此判断「无通道」）。"""
    e = os.environ if env is None else env
    host = (e.get("FD_ALERT_SMTP_HOST") or "").strip()
    sender = (e.get("FD_ALERT_SMTP_FROM") or "").strip()
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
