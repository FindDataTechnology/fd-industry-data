"""webhook 通道与按声明通道分派（add-mattermost-infrastructure 组 4）。

这些测试钉的是一个具体的生产缺陷的形状：

    `delivery.CHANNELS` 声明了 `("relay", "smtp", "webhook")` 三个出口，
    但 `notify.py` 只有 SMTP 的实现，且七条在产告警线全都直接调
    `smtp_from_env(...)`、零处读 `target.channel`。于是把某线改成
    `channel: "webhook"` 会**照样发一封邮件、回执记 sent、判绿**——
    声明被静默忽略。

所以这里的断言分三类：
  1. webhook 真的能发（形状、请求体、非 2xx 当失败）；
  2. 声明的通道被真正使用（smtp 声明不会走 webhook，反之亦然）；
  3. 声明了未实现的通道（relay）**响亮失败**，不回退。
"""
from __future__ import annotations

import json

import pytest

from fd_industry_data.alerts_core.delivery import DeliveryNotConfigured, DeliveryTarget
from fd_industry_data.alerts_core.notify import (
    WEBHOOK_URL_ENV,
    dispatch_any,
    dispatch_webhook,
    send_webhook,
    webhook_url_from_env,
)

MSGS = [
    {"rule_id": "r1", "subject": "磁盘将满", "body": "cheap-7 剩余 12%"},
    {"rule_id": "r2", "subject": "作业停滞", "body": "backup 已 3h 未跑"},
]


class _Resp:
    def __init__(self, status=200):
        self.status = status

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener_recording(sink, status=200):
    def opener(req, timeout=None):
        sink.append({"url": req.full_url, "body": json.loads(req.data.decode())})
        return _Resp(status)
    return opener


# ── 1. webhook 真的能发 ──────────────────────────────────────────────────────

def test_send_webhook_posts_mattermost_shape():
    sink = []
    send_webhook("https://im.example/hooks/abc", "磁盘将满", "剩 12%", opener=_opener_recording(sink))
    assert len(sink) == 1
    assert sink[0]["url"] == "https://im.example/hooks/abc"
    # Mattermost incoming webhook 的载荷是 {"text": ...}；加粗的 subject 让人在频道里
    # 一眼认出是哪条规则。
    assert sink[0]["body"]["text"] == "**磁盘将满**\n\n剩 12%"


def test_send_webhook_treats_non_2xx_as_failure():
    # 未知 webhook 回 404、被吊销回 403 —— 两者都不能被记成"已送达"。
    with pytest.raises(RuntimeError, match="webhook returned 404"):
        send_webhook("https://im.example/hooks/dead", "s", "b", opener=_opener_recording([], status=404))


def test_send_webhook_refuses_empty_url():
    with pytest.raises(ValueError, match="empty webhook url"):
        send_webhook("   ", "s", "b")


# ── 单条失败不阻塞同批（与 SMTP 通道同一契约）───────────────────────────────

def test_dispatch_webhook_isolates_one_failure():
    calls = []

    def opener(req, timeout=None):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise OSError("connection reset")
        return _Resp(200)

    out = dispatch_webhook(MSGS, "https://im.example/hooks/abc", opener=opener)
    assert out["sent"] == 1
    assert out["failed"] == 1
    assert out["skipped"] is False
    assert out["channel"] == "webhook"
    assert out["failures"][0]["rule_id"] == "r1"
    assert len(calls) == 2, "第 2 条必须仍然被尝试"


def test_dispatch_webhook_without_url_is_skipped_not_success():
    out = dispatch_webhook(MSGS, None)
    assert out["skipped"] is True
    assert out["sent"] == 0
    assert out["reason"] == "no-webhook-url"


def test_webhook_url_from_env():
    assert webhook_url_from_env({WEBHOOK_URL_ENV: " https://x/hooks/1 "}) == "https://x/hooks/1"
    assert webhook_url_from_env({}) is None
    assert webhook_url_from_env({WEBHOOK_URL_ENV: "   "}) is None


# ── 2. 声明的通道被真正使用 ─────────────────────────────────────────────────

def _target(channel):
    return DeliveryTarget(channel=channel, to=("alerts",))


def test_declared_smtp_actually_uses_smtp():
    # 声明 smtp 时必须走 smtp。这里给一个**必然失败**的连接参数（不存在的 host），
    # 于是断言两件事：通道归属是 smtp，且失败被逐条兜住而不是抛出去——
    # 真正的要点是「声明 smtp 时不会跑去走 webhook」。
    out = dispatch_any(
        MSGS, _target("smtp"),
        {"FD_ALERT_SMTP_HOST": "127.0.0.1", "FD_ALERT_SMTP_PORT": "1",
         "FD_ALERT_SMTP_FROM": "a@b.c", "FD_ALERT_SMTP_TO": "x@y.z"},
    )
    assert out["channel"] == "smtp"
    assert out["sent"] == 0
    assert out["failed"] == 2, "两条都试过、都失败，且没有抛出"


def test_declared_webhook_actually_uses_webhook():
    sink = []
    out = dispatch_any(
        MSGS, _target("webhook"),
        {WEBHOOK_URL_ENV: "https://im.example/hooks/abc"},
        opener=_opener_recording(sink),
    )
    assert out["channel"] == "webhook"
    assert out["sent"] == 2
    assert len(sink) == 2, "两条都要真的发出去"


def test_webhook_declared_but_unconfigured_is_loud():
    # 配置里写了 webhook 但环境没给接收端 —— 这是配置错误，不是"本轮无告警"。
    with pytest.raises(DeliveryNotConfigured, match=WEBHOOK_URL_ENV):
        dispatch_any(MSGS, _target("webhook"), {}, opener=_opener_recording([]))


# ── 3. 未实现的通道必须响亮失败，不得回退 ──────────────────────────────────

def test_relay_is_declared_but_unimplemented_and_says_so():
    with pytest.raises(DeliveryNotConfigured, match="relay"):
        dispatch_any(MSGS, _target("relay"), {})


def test_relay_does_not_silently_fall_back_to_smtp():
    # 关键反证：即使环境里 SMTP 完全配好，声明 relay 也必须抛，而不是"顺手发邮件"。
    env = {
        "FD_ALERT_SMTP_HOST": "smtp.example",
        "FD_ALERT_SMTP_FROM": "a@b.c",
        "FD_ALERT_SMTP_TO": "x@y.z",
    }
    with pytest.raises(DeliveryNotConfigured, match="relay"):
        dispatch_any(MSGS, _target("relay"), env)


def test_unknown_channel_is_refused():
    with pytest.raises(DeliveryNotConfigured, match="unknown-channel"):
        dispatch_any(MSGS, _target("unknown-channel"), {})
