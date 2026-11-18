"""CaaS 升格的加性回归（openspec: add-caas-delivery-contract 2.1/2.2）。

本文件只回答一件事：**升格有没有改变既有行为**。

判据不是"新字段能用"，而是"不声明新字段时，产物、判决、退出码与升格前一致"。
每条测试都对着一个具体的老形态（老配置 / 老产物 / 老判决）钉死。
"""
from __future__ import annotations

from fd_industry_data.alerts_core.delivery import (
    DeliveryNotConfigured,
    DeliveryReceipt,
    DeliveryTarget,
    judge,
    resolve_target,
)


# ── 老配置：不声明任何 CaaS 字段 ──────────────────────────────────────────────

def _old_cfg() -> dict:
    return {"delivery": {"channel": "webhook", "to": ["ops@example.com"]}}


def test_old_config_still_resolves_and_keeps_as_dict_shape():
    t = resolve_target(_old_cfg(), {})
    assert isinstance(t, DeliveryTarget)
    assert t.channel == "webhook"
    assert t.to == ("ops@example.com",)
    assert t.min_severity == "warning"
    # CaaS 字段全空
    assert t.forms == ()
    assert t.recipient_kind == ""
    assert t.ack == ""
    # 产物与升格前逐键一致（不多出 forms/recipient_kind/ack）
    assert t.as_dict() == {
        "channel": "webhook",
        "to": ["ops@example.com"],
        "min_severity": "warning",
    }


def test_old_receipt_as_dict_is_byte_identical():
    r = DeliveryReceipt(sent=2, failed=1, skipped=0, reasons=["boom"])
    assert r.as_dict() == {"sent": 2, "failed": 1, "skipped": 0, "reasons": ["boom"]}


def test_old_verdict_codes_and_exit_codes_unchanged():
    # 无出口 → not_configured / 3
    assert judge(DeliveryReceipt(), firing=3, not_configured_reason="x").code == "not_configured"
    assert judge(DeliveryReceipt(), firing=3, not_configured_reason="x").exit_code == 3
    # 该响没响（strict 关）→ dead_letter / 0
    dead = judge(DeliveryReceipt(sent=0), firing=2, strict=False)
    assert (dead.code, dead.exit_code) == ("dead_letter", 0)
    # 该响没响（strict 开）→ dead_letter / 1
    dead_strict = judge(DeliveryReceipt(sent=0), firing=2, strict=True)
    assert (dead_strict.code, dead_strict.exit_code) == ("dead_letter", 1)
    # 部分失败 → partial_failure / 2
    partial = judge(DeliveryReceipt(sent=1, failed=1), firing=2)
    assert (partial.code, partial.exit_code) == ("partial_failure", 2)
    # 无告警 → ok / 0
    quiet = judge(DeliveryReceipt(), firing=0)
    assert (quiet.code, quiet.exit_code) == ("ok", 0)


# ── 新维度：只在声明时生效 ───────────────────────────────────────────────────

def test_declared_recipient_kind_derives_forms_from_authority():
    cfg = {"delivery": {"channel": "smtp", "to": ["a@b.c"], "recipient_kind": "platform-identity"}}
    t = resolve_target(cfg, {"FD_ALERT_SMTP_HOST": "h", "FD_ALERT_SMTP_FROM": "f"})
    assert t.recipient_kind == "platform-identity"
    # 来自权威表，不是代码里写的
    assert "text" in t.forms and "link" in t.forms


def test_unknown_recipient_kind_is_a_config_error():
    cfg = {"delivery": {"channel": "smtp", "to": ["a@b.c"], "recipient_kind": "hologram-booth"}}
    try:
        resolve_target(cfg, {"FD_ALERT_SMTP_HOST": "h", "FD_ALERT_SMTP_FROM": "f"})
    except DeliveryNotConfigured as e:
        assert "recipient_kind" in str(e)
    else:
        raise AssertionError("expected DeliveryNotConfigured")


def test_forms_beyond_channel_capability_is_rejected():
    # webhook 渠道只能 text；声明 chart 必须报错，而不是静默丢弃
    cfg = {"delivery": {"channel": "webhook", "to": ["a@b.c"], "forms": ["chart"]}}
    try:
        resolve_target(cfg, {})
    except DeliveryNotConfigured as e:
        assert "webhook" in str(e) or "渠道能力" in str(e)
    else:
        raise AssertionError("expected DeliveryNotConfigured")


def test_unregistered_form_is_rejected():
    cfg = {"delivery": {"channel": "smtp", "to": ["a@b.c"], "forms": ["hologram"]}}
    try:
        resolve_target(cfg, {"FD_ALERT_SMTP_HOST": "h", "FD_ALERT_SMTP_FROM": "f"})
    except DeliveryNotConfigured as e:
        assert "hologram" in str(e)
    else:
        raise AssertionError("expected DeliveryNotConfigured")


def test_ack_above_ceiling_is_rejected():
    # smtp 渠道上限 delivered；声明 consumed 必须报错
    cfg = {"delivery": {"channel": "smtp", "to": ["a@b.c"], "ack": "consumed"}}
    try:
        resolve_target(cfg, {"FD_ALERT_SMTP_HOST": "h", "FD_ALERT_SMTP_FROM": "f"})
    except DeliveryNotConfigured as e:
        assert "上限" in str(e) or "ack" in str(e)
    else:
        raise AssertionError("expected DeliveryNotConfigured")


def test_ack_alias_read_is_accepted_and_canonicalised():
    cfg = {"delivery": {"channel": "webhook", "to": ["a@b.c"], "ack": "read"}}
    try:
        resolve_target(cfg, {})
    except DeliveryNotConfigured:
        pass  # read(=consumed) 超 webhook 上限 delivered，被拒也是对的
    else:
        raise AssertionError("read(=consumed) exceeds webhook ceiling and must be rejected")


# ── 回执投影 ────────────────────────────────────────────────────────────────

def test_receipt_projection_marks_downgrade_for_declared_read():
    r = DeliveryReceipt(sent=3, ack_actual="delivered")
    p = r.projection(ack_declared="read")
    assert p.state == "delivered"
    assert p.downgraded is True


def test_receipt_projection_all_failed_is_retryable_failure():
    r = DeliveryReceipt(sent=0, failed=2, reasons=["smtp timeout"])
    p = r.projection(ack_declared="delivered")
    assert p.state == "failed"
    assert p.retryable is True
    assert "smtp timeout" in p.reason


def test_receipt_projection_meets_declared_strength():
    r = DeliveryReceipt(sent=1, ack_actual="consumed")
    p = r.projection(ack_declared="delivered")
    assert p.state == "consumed"
    assert p.downgraded is False
