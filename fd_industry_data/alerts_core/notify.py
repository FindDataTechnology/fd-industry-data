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

# Mattermost 的 incoming webhook 对正文有硬上限（默认 16383 字符，含 markdown）；
# 超了它回 400，于是**整条消息发不出去**。2026-11-23 在 relay 通道上实撞过同款：
# 27 项作业同时坏的时候正文远超上限，告警全部投递失败，而失败只落在 Job 日志里
# ——备份连续失败三天没人知道，一半原因是告警自己发不出去。
# 截断而不是「拆成多段」：这个通道没有排序保证，多段会被看成几条无关消息；而
# compose() 已把最严重的排在前面。截断处明写省略了多少，别让人以为那就是全部。
WEBHOOK_MAX_TEXT = 12000
_WEBHOOK_TRUNC_NOTE = "\n\n…（正文超出通道上限，以下省略 {n} 字；完整清单看 job 日志）"


def _fit_webhook_text(text: str, limit: int = WEBHOOK_MAX_TEXT) -> str:
    if len(text) <= limit:
        return text
    note = _WEBHOOK_TRUNC_NOTE.format(n=len(text) - limit)
    keep = max(0, limit - len(note))
    return text[:keep].rstrip() + note


def send_webhook(url: str, subject: str, body: str, *, opener=None) -> None:
    """发一条 webhook 消息；失败抛异常，由 ``dispatch_webhook`` 逐条兜住。

    ``opener`` 是单测注入点（不联网也能验证失败隔离），与 ``smtp_factory`` 同形。
    """
    import json
    import urllib.error
    import urllib.request

    if not url or not url.strip():
        raise ValueError("empty webhook url")
    text = _fit_webhook_text(f"**{subject}**\n\n{body}")
    payload = json.dumps({"text": text}).encode("utf-8")
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

# ── relay 通道（relay-alert-channel）─────────────────────────────────────────
#
# `relay` 一直是「配置里写得出、运行时必抛」的通道。这里把它实现掉。
#
# 一个通道、两个后端：**由 URL 的路径决定协议**，不是第二个开关。路径本身已经
# 携带了这个信息，多加一个 FD_ALERT_RELAY_KIND 只会多出一个能与 URL 矛盾的输入，
# 而矛盾的后果是「发到一个后端、按另一个的协议序列化」。
#
#   .../v1/send               → fd-bot   {bindingId, chatKey, text}
#   .../api/bots/relay/send   → cell     {channel, text} + Bearer
#
# 目标名（delivery.to[0]）是不透明的：运维写人类可读的名字，翻译在后端或 env。
# fd-bot 要两个内部标识（bindingId/chatKey），它们进 env 而不是进配置——
# 配置里出现内部 id 就等于把内部寻址泄漏给了写告警规则的人。

RELAY_URL_ENV = "FD_ALERT_RELAY_URL"
RELAY_TOKEN_ENV = "FD_ALERT_RELAY_TOKEN"
FD_BOT_BINDING_ENV = "FD_ALERT_FD_BOT_BINDING"
FD_BOT_CHAT_ENV = "FD_ALERT_FD_BOT_CHAT"

RELAY_TIMEOUT_SEC = 15  # 比 webhook 长：跨集群 + 平台侧排队

#: cell 的 relay 对 `text` 有硬上限（`server/bots.js` 的 MAX_RELAY_CHARS），超了答
#: **400 "Text must be 1-4000 characters"**。2026-11-23 实撞：27 项作业同时坏的时候
#: 正文远超 4000，于是**整条告警投递失败**，而失败只落在 Job 自己的日志里——
#: 备份连续失败 3 天没人知道，一半原因是告警发不出去。
#:
#: 截断而不是「拆成多段」：这个通道没有排序保证，多段会被群里看成几条无关消息；
#: 而告警的价值在前几项（compose() 把最严重的排在前面）。截断处**明写省略了多少**，
#: 别让人以为那就是全部。
CELL_MAX_TEXT = 4000
_RELAY_TRUNC_NOTE = "\n\n…（正文超出通道上限，以下省略 {n} 字；完整清单看 job 日志）"


def _fit_relay_text(text: str, limit: int = CELL_MAX_TEXT) -> str:
    """把 text 压进 limit；超了就在末尾标出省略了多少。"""
    if len(text) <= limit:
        return text
    note = _RELAY_TRUNC_NOTE.format(n=len(text) - limit)
    keep = max(0, limit - len(note))
    return text[:keep].rstrip() + note


def _relay_kind(url: str) -> str:
    """由 URL 路径判定后端。未知路径即配置错误——不猜。"""
    path = url.split("?", 1)[0].rstrip("/")
    if path.endswith("/v1/send"):
        return "fd-bot"
    if path.endswith("/api/bots/relay/send"):
        return "cell"
    raise ValueError(f"无法从路径判定 relay 后端：{url}")


def send_relay(url, payload: dict, env=None, *, opener=None) -> None:
    """发一条 relay 消息；失败抛异常，由 ``dispatch_relay`` 逐条兜住。

    ``payload`` 是 dict 而非字符串：正文里以 ``{`` 开头的告警会被「先试 JSON」
    那类猜测误判成 payload，而告警正文恰好经常是 JSON 摘要。
    """
    import json
    import urllib.request

    e = os.environ if env is None else env
    if not url or not str(url).strip():
        raise ValueError("empty relay url")
    url = str(url).strip()
    kind = _relay_kind(url)

    headers = {"Content-Type": "application/json"}
    if kind == "cell":
        token = (e.get(RELAY_TOKEN_ENV) or "").strip()
        if not token:
            raise ValueError(f"cell relay 缺 {RELAY_TOKEN_ENV}")
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    open_fn = opener or urllib.request.urlopen
    with open_fn(req, timeout=RELAY_TIMEOUT_SEC) as resp:
        code = getattr(resp, "status", None) or resp.getcode()
        raw = resp.read() or b""
        if code and int(code) >= 400:
            raise RuntimeError(f"relay returned {code}: {raw[:200]!r}")
        # 两个后端的响应体不同，但都用 ok 表达成败：只有 ok === true 算送达。
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        if body.get("ok") is not True:
            reason = body.get("reason") or body.get("failure") or body.get("detail") or body
            raise RuntimeError(f"relay refused: {reason}")


def dispatch_relay(messages: list[dict], target, env=None, *, opener=None) -> dict:
    """批量发 relay，逐条兜住失败；返回形状与 ``dispatch`` / ``dispatch_webhook`` 同形。"""
    from .delivery import DeliveryNotConfigured  # noqa: PLC0415 — 避免循环导入

    e = os.environ if env is None else env
    url = (e.get(RELAY_URL_ENV) or "").strip()
    if not url:
        raise DeliveryNotConfigured(
            f"channel=relay 但环境缺 {RELAY_URL_ENV}；"
            "要么指向 fd-bot 的 /v1/send，要么指向 cell 的 /api/bots/relay/send"
        )

    try:
        kind = _relay_kind(url)
    except ValueError as exc:
        raise DeliveryNotConfigured(str(exc)) from exc

    destination = ""
    if kind == "cell":
        to = tuple(getattr(target, "to", ()) or ())
        if not to:
            raise DeliveryNotConfigured("channel=relay(cell) 需要 delivery.to 给出 channel 名")
        destination = str(to[0])
        if not (e.get(RELAY_TOKEN_ENV) or "").strip():
            raise DeliveryNotConfigured(
                f"channel=relay(cell) 但环境缺 {RELAY_TOKEN_ENV}（bearer）"
            )
    else:
        missing = [k for k in (FD_BOT_BINDING_ENV, FD_BOT_CHAT_ENV) if not (e.get(k) or "").strip()]
        if missing:
            raise DeliveryNotConfigured(
                f"channel=relay(fd-bot) 但环境缺 {' / '.join(missing)}；"
                "两个内部标识必须由 env 给出，不得写进线路配置"
            )

    sent = 0
    failures: list[dict] = []
    for m in messages:
        try:
            subject, body = m.get("subject", ""), m.get("body", "")
            # 与 webhook 通道同一种渲染：强调标题 + 正文。告警群是靠扫读的。
            text = f"**{subject}**\n\n{body}" if subject else body
            if kind == "cell":
                text = _fit_relay_text(text, CELL_MAX_TEXT)
                payload = {"channel": destination, "text": text}
            else:
                payload = {
                    "bindingId": (e.get(FD_BOT_BINDING_ENV) or "").strip(),
                    "chatKey": (e.get(FD_BOT_CHAT_ENV) or "").strip(),
                    "text": text,
                }
            send_relay(url, payload, e, opener=opener)
            sent += 1
        except Exception as exc:  # noqa: BLE001 — 单条失败必须不阻塞同批
            failures.append({"rule_id": m.get("rule_id"), "error": str(exc)[:200]})
    return {"sent": sent, "failed": len(failures), "failures": failures,
            "skipped": False, "channel": "relay"}


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
        return dispatch_relay(messages, target, e, opener=opener)

    raise DeliveryNotConfigured(
        f"channel={channel!r} 未知；合法值见 delivery.CHANNELS"
    )
