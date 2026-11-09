"""告警投递口径的契约（openspec: wire-alert-delivery §2-3）。

钉四条会静默出错或误判红的语义：

1. **缺配置 ≠ 无告警**：没有 `delivery` 段 → ``not_configured``（exit 3），
   不能当成"本轮静默期"结案。
2. **该响没响要判红**：``firing > 0 && sent == 0`` → 死信；strict 开启时 exit 1。
3. **静默期零送达是正常**：``firing == 0`` → exit 0，否则每天都会红一次。
4. **部分失败非死信但非零**：有人收到也要让批次失败可见（exit 2）。
"""
from __future__ import annotations

import pytest

from fd_industry_data.alerts_core.delivery import (
    DeliveryNotConfigured,
    DeliveryReceipt,
    count_firing,
    judge,
    resolve_target,
    strict_enabled,
)


def _cfg(**delivery):
    return {"version": 1, "delivery": delivery} if delivery else {"version": 1}


# ── 1. 出口与接收人声明 ──────────────────────────────────────────────


def test_missing_delivery_is_not_configured_not_silent_skip():
    with pytest.raises(DeliveryNotConfigured) as e:
        resolve_target(_cfg())
    assert "delivery" in e.value.reason


def test_empty_recipients_rejected():
    with pytest.raises(DeliveryNotConfigured) as e:
        resolve_target(_cfg(channel="relay", to=[]))
    assert "接收人" in e.value.reason


def test_unknown_channel_rejected():
    with pytest.raises(DeliveryNotConfigured):
        resolve_target(_cfg(channel="carrier-pigeon", to=["fd-ops"]))


def test_smtp_requires_credentials_but_relay_does_not():
    cfg = _cfg(channel="smtp", to=["ops@example.com"])
    with pytest.raises(DeliveryNotConfigured) as e:
        resolve_target(cfg, env={})
    assert "FD_ALERT_SMTP_HOST" in e.value.reason

    ok = resolve_target(
        _cfg(channel="smtp", to=["ops@example.com"]),
        env={"FD_ALERT_SMTP_HOST": "smtp.example.com", "FD_ALERT_SMTP_FROM": "a@b.c"},
    )
    assert ok.channel == "smtp"

    relay = resolve_target(_cfg(channel="relay", to=["fd-ops"]), env={})
    assert relay.as_dict() == {"channel": "relay", "to": ["fd-ops"], "min_severity": "warning"}


# ── 2-4. 判决 ────────────────────────────────────────────────────────


def test_firing_with_zero_delivery_is_dead_letter_and_red_when_strict():
    v = judge(DeliveryReceipt(sent=0, failed=0, skipped=2), firing=2, strict=True)
    assert v.code == "dead_letter"
    assert v.exit_code == 1


def test_dead_letter_not_red_by_default_so_existing_flows_survive():
    v = judge(DeliveryReceipt(sent=0, skipped=2), firing=2, strict=False)
    assert v.code == "dead_letter"
    assert v.exit_code == 0
    assert "FD_ALERT_DELIVERY_STRICT" in v.message


def test_quiet_window_with_zero_delivery_is_ok():
    v = judge(DeliveryReceipt(sent=0), firing=0, strict=True)
    assert (v.code, v.exit_code) == ("ok", 0)


def test_partial_failure_is_nonzero_but_not_dead_letter():
    v = judge(DeliveryReceipt(sent=2, failed=1), firing=3, strict=True)
    assert v.code == "partial_failure"
    assert v.exit_code == 2


def test_not_configured_outranks_everything():
    v = judge(DeliveryReceipt(), firing=0, strict=True, not_configured_reason="no delivery")
    assert v.code == "not_configured"
    assert v.exit_code == 3


def test_receipt_totals_are_partitioned():
    r = DeliveryReceipt(sent=1, failed=2, skipped=3)
    assert r.total == 6
    assert r.as_dict() == {"sent": 1, "failed": 2, "skipped": 3}


def test_strict_flag_reads_env():
    assert strict_enabled({"FD_ALERT_DELIVERY_STRICT": "1"}) is True
    assert strict_enabled({"FD_ALERT_DELIVERY_STRICT": "0"}) is False
    assert strict_enabled({}) is False


# ── firing 计数（别把恢复通知算成"该响"）─────────────────────────────


def test_count_firing_ignores_resolved_and_below_severity():
    events = [
        {"kind": "firing", "severity": "critical"},
        {"kind": "firing", "severity": "warning"},
        {"kind": "firing", "severity": "info"},      # 低于门槛
        {"kind": "resolved", "severity": "critical"},  # 恢复不是"该响"
    ]
    assert count_firing(events) == 2
    assert count_firing(events, min_severity="critical") == 1
