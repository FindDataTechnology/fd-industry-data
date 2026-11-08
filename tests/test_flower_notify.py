"""花卉线投递语义（openspec: wire-alert-delivery §3）。

钉四条会"看起来发了其实没发"的语义：

1. **无通道 = skipped + 原因**，不是成功。
2. **dry-run 不发**，回执写明原因。
3. **单条失败不阻塞同批**，失败逐条落回执（调用方据此 exit 4）。
4. **有告警但零送达 → 非零退出**（由 `rules` 命令把 receipt.failed 映射成 4）。

用假 SMTP 工厂，不联网。
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

spec = importlib.util.spec_from_file_location(
    "flower_alerts", REPO / "scripts" / "flower_alerts.py")
flower = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flower)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


def _item(name="eu_flower_area_yoy_negative", action="alert"):
    return {
        "name": name,
        "action": action,
        "result": {"value": -2.263, "unit": "percent", "period": "2024", "entity": None},
        "rule": {"citation": "Eurostat apro_cpsh1", "citation_version": "2026-11-08"},
    }


class _OkSMTP:
    def __init__(self, host, port):
        self.sent = []

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        self.sent.append(msg)

    def quit(self):
        pass


class _BoomSMTP(_OkSMTP):
    def send_message(self, msg):
        raise OSError("connection reset by peer")


@pytest.fixture()
def smtp_env(monkeypatch):
    monkeypatch.setenv("FD_ALERT_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("FD_ALERT_SMTP_PORT", "465")
    monkeypatch.setenv("FD_ALERT_SMTP_SSL", "1")
    monkeypatch.setenv("FD_ALERT_SMTP_TLS", "0")
    monkeypatch.setenv("FD_ALERT_SMTP_FROM", "support@finddatatech.cloud")
    monkeypatch.setenv("FD_ALERT_SMTP_TO", "1253774197@qq.com")
    monkeypatch.setenv("FD_ALERT_SMTP_USER", "u")
    monkeypatch.setenv("FD_ALERT_SMTP_PASS", "p")


def _cfg():
    return {"delivery": {"channel": "smtp", "to": ["1253774197@qq.com"]}}


# ── 1. 无通道 ────────────────────────────────────────────────────────


def test_no_smtp_config_skips_with_reason(monkeypatch):
    for k in ("FD_ALERT_SMTP_HOST", "FD_ALERT_SMTP_FROM", "FD_ALERT_SMTP_TO"):
        monkeypatch.delenv(k, raising=False)
    receipt = flower.deliver_notifications([_item()], _cfg(), dry_run=False, now=NOW)
    assert receipt["sent"] == 0
    assert receipt["skipped"] == 1
    assert "no-smtp-config" in receipt["reason"]


# ── 2. dry-run ───────────────────────────────────────────────────────


def test_dry_run_sends_nothing(smtp_env):
    receipt = flower.deliver_notifications([_item()], _cfg(), dry_run=True, now=NOW)
    assert (receipt["sent"], receipt["skipped"]) == (0, 1)
    assert "dry-run" in receipt["reason"]


def test_no_items_is_a_clean_noop(smtp_env):
    receipt = flower.deliver_notifications([], _cfg(), dry_run=False, now=NOW)
    assert receipt == {"sent": 0, "failed": 0, "skipped": 0, "messages": []}


# ── 3. 发送与失败隔离 ────────────────────────────────────────────────


def test_both_alert_and_recover_are_delivered(smtp_env):
    smtp = _OkSMTP("h", 465)
    receipt = flower.deliver_notifications(
        [_item("r1", "alert"), _item("r2", "recover")], _cfg(),
        dry_run=False, now=NOW, smtp_factory=lambda h, p: smtp)
    assert receipt["sent"] == 2
    assert len(smtp.sent) == 2
    subjects = [m["Subject"] for m in smtp.sent]
    assert any("越阈" in s for s in subjects)
    assert any("恢复" in s for s in subjects)


def test_recipients_come_from_env_override_first(smtp_env, monkeypatch):
    monkeypatch.setenv("FD_ALERT_SMTP_TO", "override@example.com")
    smtp = _OkSMTP("h", 465)
    flower.deliver_notifications([_item()], _cfg(), dry_run=False, now=NOW,
                                 smtp_factory=lambda h, p: smtp)
    assert smtp.sent[0]["To"] == "override@example.com"


def test_single_failure_does_not_block_the_batch(smtp_env):
    calls = {"n": 0}

    def factory(host, port):
        calls["n"] += 1
        return _BoomSMTP(host, port) if calls["n"] == 1 else _OkSMTP(host, port)

    receipt = flower.deliver_notifications(
        [_item("r1"), _item("r2")], _cfg(), dry_run=False, now=NOW, smtp_factory=factory)
    assert receipt["failed"] == 1
    assert receipt["sent"] == 1
    assert [m["status"] for m in receipt["messages"]] == ["failed", "sent"]


# ── 4. 回执字段形状（报告里能被别人读）──────────────────────────────


def test_receipt_is_json_serialisable(smtp_env):
    import json

    smtp = _OkSMTP("h", 465)
    receipt = flower.deliver_notifications([_item()], _cfg(), dry_run=False,
                                           now=NOW, smtp_factory=lambda h, p: smtp)
    assert json.loads(json.dumps(receipt))["sent"] == 1
