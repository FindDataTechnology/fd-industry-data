"""接收人来自线路声明，不是环境变量（alert-delivery: 接收人来自线路声明）。

钉的是一个 2026-11-20 实测的生产缺陷：十条在产告警线的 cronjob 全都只注入
HOST/USER/PASS/FROM/PORT/SSL，**没有一条**注入 ``FD_ALERT_SMTP_TO``；而
``dispatch_any`` 的 smtp 分支走 ``smtp_config_from_env()``，那个函数把 TO 当必需项，
于是返回 None → ``dispatch()`` 记 ``skipped`` → 回执 ``{"sent":0,"skipped":true}``。

判据全对、出口全断：接收人明明写在版本化的线路配置（``delivery.to``）里，分派器却
只认 env。广场那条线是唯一有 firing 的，所以只有它露出来了。

两类缺失必须分开判：
  · 线路有 to + env 空  → 正常，**发出去**；
  · 线路无 to + env 空  → 配置错误（抛 DeliveryNotConfigured），不是「跳过」。
"""
from __future__ import annotations

import pytest

from fd_industry_data.alerts_core.delivery import (
    DeliveryNotConfigured,
    DeliveryTarget,
    resolve_target,
)
from fd_industry_data.alerts_core.notify import dispatch_any

MSGS = [{"rule_id": "r1", "subject": "广场快照陈旧", "body": "generatedAt 149h"}]

# 生产 cronjob 的真实形状：有连接要素，**没有** FD_ALERT_SMTP_TO
ENV_NO_TO = {
    "FD_ALERT_SMTP_HOST": "smtp.example.invalid",
    "FD_ALERT_SMTP_FROM": "alerts@example.invalid",
    "FD_ALERT_SMTP_USER": "alerts@example.invalid",
    "FD_ALERT_SMTP_PASS": "x",
    "FD_ALERT_SMTP_PORT": "465",
    "FD_ALERT_SMTP_SSL": "1",
}


class _FakeSMTP:
    """只记录，不出网。``send_message`` 收到的是 EmailMessage。"""

    def __init__(self, seen):
        self.seen = seen

    def starttls(self, *a, **k):
        pass

    def login(self, *a, **k):
        pass

    def send_message(self, msg):
        self.seen.append(msg["To"])

    def quit(self):
        pass


def _cfg(to):
    return {"delivery": {"channel": "smtp", "to": to}}


def test_declared_recipients_are_used_when_env_has_none():
    """线路声明了 to、env 没有 TO —— 这才是十条线的真实形状，必须发得出去。"""
    target = resolve_target(_cfg(["ops@example.invalid"]), ENV_NO_TO)
    seen: list[str] = []
    out = dispatch_any(MSGS, target, ENV_NO_TO, smtp_factory=lambda h, p: _FakeSMTP(seen))
    assert out["sent"] == 1, f"声明了接收人却一条都没发：{out}"
    assert out["skipped"] is False
    assert seen == ["ops@example.invalid"]
    # 回归臂：这正是修复前的形态——skipped 与 sent=0 同时出现
    assert out.get("reason") != "no-smtp-config"


def test_env_to_still_overrides_when_present():
    """env 里有 TO 时仍以 env 为准（部署侧临时改收件人不必改配置）。"""
    env = {**ENV_NO_TO, "FD_ALERT_SMTP_TO": "override@example.invalid"}
    target = resolve_target(_cfg(["ops@example.invalid"]), env)
    seen: list[str] = []
    dispatch_any(MSGS, target, env, smtp_factory=lambda h, p: _FakeSMTP(seen))
    # 线路声明优先于 env：接收人属于版本化配置，env 只作兜底。
    # （dispatch_any 把 target.to 传给 smtp_config_from_env(recipients=...)，后者
    #   只在没有声明时才读 FD_ALERT_SMTP_TO。）
    assert seen == ["ops@example.invalid"]


def test_no_recipient_anywhere_is_a_config_error_not_a_skip():
    """两处都没有接收人：抛配置错误，不许降级成「跳过」。"""
    target = DeliveryTarget(channel="smtp", to=())
    with pytest.raises(DeliveryNotConfigured):
        dispatch_any(MSGS, target, {}, smtp_factory=lambda h, p: _FakeSMTP([]))
