"""marketing-signals 的出口接线（2026-11-28 立）。

改的是**装配点**（`_default_fetchers`），所以测的也必须是装配点：spider 被调用时
到底有没有处在代理上下文里。只测 `egress_proxy` 本身，测不到"接线没接上"这种错。

两条臂：
  · 配了 forwarder ⇒ spider 执行期间 `current_proxy_url()` 非空（真走代理）
  · 没配 forwarder ⇒ 为空（ships-dark，本地/测试行为不变）
"""
from __future__ import annotations

import pytest

from fd_industry_data import egress_proxy, marketing_signal_server


class _SpySpider:
    def __init__(self, seen: list):
        self._seen = seen

    def run_ted_notices(self, limit: int = 20, date=None):
        self._seen.append(egress_proxy.current_proxy_url())
        return []

    def run_worldbank_procnotices(self, limit: int = 20):
        self._seen.append(egress_proxy.current_proxy_url())
        return []

    def run_nfra_penalty(self, limit: int = 20):
        self._seen.append(egress_proxy.current_proxy_url())
        return []


@pytest.fixture
def spy(monkeypatch):
    seen: list = []
    monkeypatch.setattr(marketing_signal_server, "_load_spider",
                        lambda name, rel: _SpySpider(seen))
    monkeypatch.delenv("FD_PROXY_FORWARDER", raising=False)
    return seen


def test_without_forwarder_spiders_run_direct(spy):
    f = marketing_signal_server._default_fetchers()
    f["ted-notices"](3)
    assert spy == [None], "没配 forwarder 时不该凭空出现代理"


def test_with_forwarder_spiders_run_inside_proxy_context(spy, monkeypatch):
    monkeypatch.setenv("FD_PROXY_FORWARDER", "http://forwarder:8080")
    monkeypatch.setattr(egress_proxy, "_post", lambda p, payload, timeout=5.0: (
        {"upstream_url": "http://u:p@1.2.3.4:30080", "addr_id": 3, "provider": "gost-mesh"}
        if p == "/acquire" else {"status": "ok"}))

    f = marketing_signal_server._default_fetchers()
    for name in ("ted-notices", "worldbank-procnotices", "nfra-penalty"):
        f[name](3)
    assert spy == ["http://u:p@1.2.3.4:30080"] * 3
    # 出了装配的调用，上下文必须还原
    assert egress_proxy.current_proxy_url() is None
