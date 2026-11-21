"""relay 通道的单测（openspec: relay-alert-channel 组 2）。

覆盖的都是**判定**而非连通性：单条失败不阻塞、缺项逐个点名、对端拒绝不被记成送达、
判定与 smtp 产物的判定同构。真机连通性在 tasks 4.2（那需要群里有消息）。
"""
from __future__ import annotations

import json

import pytest

from fd_industry_data.alerts_core import notify
from fd_industry_data.alerts_core.delivery import DeliveryNotConfigured, DeliveryReceipt, judge


class FakeResp:
    def __init__(self, body: dict, status: int = 200):
        self._body = json.dumps(body).encode()
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_returning(body: dict, status: int = 200, record: list | None = None):
    """一个 opener：记录每次请求的 payload，回固定的响应。"""

    def _opener(req, timeout=None):
        if record is not None:
            record.append(
                {"url": req.full_url, "payload": json.loads(req.data.decode()),
                 "headers": dict(req.headers), "timeout": timeout}
            )
        return FakeResp(body, status)

    return _opener


def target(to=("ops-group",)):
    class T:
        channel = "relay"
        this_to = to

    T.to = to
    return T()


FD_BOT_ENV = {
    notify.RELAY_URL_ENV: "http://fd-bot.fd-bot.svc.cluster.local:8791/v1/send",
    notify.FD_BOT_BINDING_ENV: "b_test",
    notify.FD_BOT_CHAT_ENV: "wr_group",
}

CELL_ENV = {
    notify.RELAY_URL_ENV: "https://platform.example/api/bots/relay/send",
    notify.RELAY_TOKEN_ENV: "tok",
}


# ── 2.1 两条成功路径 + 形状 ──────────────────────────────────────────────────

def test_fd_bot_path_sends_binding_and_chat():
    seen: list = []
    out = notify.dispatch_relay(
        [{"rule_id": "r1", "subject": "备份失败", "body": "节点 3"}],
        target(), FD_BOT_ENV, opener=opener_returning({"ok": True}, record=seen),
    )
    assert out["sent"] == 1 and out["failed"] == 0
    p = seen[0]["payload"]
    assert p["bindingId"] == "b_test" and p["chatKey"] == "wr_group"
    assert "备份失败" in p["text"] and "节点 3" in p["text"]


def test_cell_path_sends_channel_and_bearer():
    seen: list = []
    out = notify.dispatch_relay(
        [{"rule_id": "r1", "subject": "s", "body": "b"}],
        target(("ops-group",)), CELL_ENV, opener=opener_returning({"ok": True}, record=seen),
    )
    assert out["sent"] == 1
    assert seen[0]["payload"]["channel"] == "ops-group"
    auth = {k.lower(): v for k, v in seen[0]["headers"].items()}
    assert auth.get("authorization") == "Bearer tok"


def test_return_shape_matches_webhook():
    """各线 judge() 读的是这个形状；改了形状等于同时改七条线。"""
    got = notify.dispatch_relay(
        [{"subject": "s", "body": "b"}], target(), FD_BOT_ENV,
        opener=opener_returning({"ok": True}),
    )
    hook = notify.dispatch_webhook(
        [{"subject": "s", "body": "b"}], "http://x", opener=opener_returning({}),
    )
    assert set(got) == set(hook) | {"channel"} or set(got) == set(hook)
    for k in ("sent", "failed", "failures", "skipped"):
        assert type(got[k]) is type(hook[k]), k
    assert got["channel"] == "relay"


# ── 2.2 缺项逐个点名 ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "env, needle",
    [
        ({}, "FD_ALERT_RELAY_URL"),
        ({notify.RELAY_URL_ENV: "http://x/v1/send"}, "FD_ALERT_FD_BOT_BINDING"),
        ({notify.RELAY_URL_ENV: "http://x/v1/send", notify.FD_BOT_BINDING_ENV: "b"}, "FD_ALERT_FD_BOT_CHAT"),
        ({notify.RELAY_URL_ENV: "https://y/api/bots/relay/send"}, "FD_ALERT_RELAY_TOKEN"),
        ({notify.RELAY_URL_ENV: "http://z/unknown"}, "无法从路径判定"),
    ],
)
def test_missing_configuration_names_the_variable(env, needle):
    with pytest.raises(DeliveryNotConfigured) as ei:
        notify.dispatch_relay([{"subject": "s", "body": "b"}], target(), env)
    assert needle in ei.value.reason


# ── 2.3 不再抛「已声明但未实现」──────────────────────────────────────────────

def test_relay_is_no_longer_a_stub():
    out = notify.dispatch_any(
        [{"subject": "s", "body": "b"}], target(), FD_BOT_ENV,
        opener=opener_returning({"ok": True}),
    )
    assert out["channel"] == "relay" and out["sent"] == 1


def test_webhook_path_still_reports_skipped_when_unconfigured():
    """换掉 relay 分支不能改变 webhook 分支的行为。"""
    out = notify.dispatch_webhook([{"subject": "s", "body": "b"}], None)
    assert out["skipped"] is True and out["channel"] == "webhook"
    assert notify.dispatch_webhook([], "http://x")["skipped"] is False


# ── 2.4 对端拒绝不被记成送达 ────────────────────────────────────────────────

@pytest.mark.parametrize("status, body", [(401, {}), (500, {}), (200, {"ok": False, "reason": "unknown-channel"})])
def test_non_2xx_or_not_ok_is_a_failure(status, body):
    out = notify.dispatch_relay(
        [{"rule_id": "r1", "subject": "s", "body": "b"}], target(), CELL_ENV,
        opener=opener_returning(body, status),
    )
    assert out["sent"] == 0 and out["failed"] == 1
    assert out["failures"][0]["rule_id"] == "r1"


def test_failure_carries_the_peer_reason():
    out = notify.dispatch_relay(
        [{"subject": "s", "body": "b"}], target(), CELL_ENV,
        opener=opener_returning({"ok": False, "reason": "rate-limited"}, 200),
    )
    assert "rate-limited" in out["failures"][0]["error"]


# ── 2.5 单条失败不阻塞同批 ──────────────────────────────────────────────────

def test_one_failure_does_not_block_the_batch():
    calls = {"n": 0}

    def flaky(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 2:
            return FakeResp({}, 500)
        return FakeResp({"ok": True})

    out = notify.dispatch_relay(
        [{"rule_id": f"r{i}", "subject": "s", "body": "b"} for i in range(3)],
        target(), CELL_ENV, opener=flaky,
    )
    assert out["sent"] == 2 and out["failed"] == 1
    assert out["failures"][0]["rule_id"] == "r1"


# ── 2.6 超时 ────────────────────────────────────────────────────────────────

def test_timeout_is_passed_and_counts_as_failure():
    def slow(req, timeout=None):
        assert timeout == notify.RELAY_TIMEOUT_SEC
        raise TimeoutError("timed out")

    out = notify.dispatch_relay(
        [{"subject": "s", "body": "b"}], target(), CELL_ENV, opener=slow,
    )
    assert out["sent"] == 0 and out["failed"] == 1


# ── 3.1 判定与 smtp 同构 ────────────────────────────────────────────────────

def _receipt_from(out: dict) -> DeliveryReceipt:
    return DeliveryReceipt(sent=out["sent"], failed=out["failed"], skipped=0)


def test_judge_reads_relay_receipts_identically():
    dead = notify.dispatch_relay(
        [{"subject": "s", "body": "b"}], target(), CELL_ENV,
        opener=opener_returning({}, 500),
    )
    assert judge(_receipt_from(dead), firing=1, strict=True).code == "dead_letter"
    assert judge(_receipt_from(dead), firing=1, strict=True).exit_code == 1

    # 部分失败用真实往返构造：两条消息，第二条 500。
    n = {"i": 0}

    def second_fails(req, timeout=None):
        n["i"] += 1
        return FakeResp({}, 500) if n["i"] == 2 else FakeResp({"ok": True})

    partial = notify.dispatch_relay(
        [{"rule_id": f"r{i}", "subject": "s", "body": "b"} for i in range(2)],
        target(), CELL_ENV, opener=second_fails,
    )
    assert judge(_receipt_from(partial), firing=2).code == "partial_failure"
    assert judge(_receipt_from({"sent": 1, "failed": 0}), firing=1).code == "ok"
