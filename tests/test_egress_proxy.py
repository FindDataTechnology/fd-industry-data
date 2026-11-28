"""出口代理接线（2026-11-28 立）。

钉四件事——每一件错了都会让「取数看起来成功但其实没走代理」或者「本地测试被代理打挂」：

1. **ships-dark 必须真直连**：`FD_PROXY_FORWARDER` 未设 ⇒ acquire 返回 None、
   块内 urlopen 走原版。本地/测试不配代理时行为与接线前一致。
2. **acquire 的解析**：`upstream_url/addr_id/provider` 三个字段缺一不可，
   否则没法 release（addr_id 为 None 时 release 是 no-op）。
3. **contextvar 的进出**：块内设、块外还原——不还原就会把代理漏给后续请求。
4. **成功/失败要回写电路**：块内抛异常 ⇒ `fail`，否则 `ok`。
   只测"能取到数"不测回写，等于没测到电路健康度这条。
"""
from __future__ import annotations

import urllib.request

import pytest

from fd_industry_data import egress_proxy


@pytest.fixture(autouse=True)
def _no_forwarder(monkeypatch):
    monkeypatch.delenv("FD_PROXY_FORWARDER", raising=False)
    monkeypatch.delenv("PROXY_CONTROL_TOKEN", raising=False)


def test_ships_dark_acquire_is_direct():
    assert egress_proxy.acquire("ted-notices") == (None, None, None)


def test_ships_dark_context_yields_none_and_keeps_direct(monkeypatch):
    called = {}

    def fake_urlopen(url, data=None, timeout=None, **kw):
        called["url"] = url
        return "resp"

    egress_proxy.install()   # 没打补丁时 urlopen 还是标准库那个，会真发包
    monkeypatch.setattr(egress_proxy, "_ORIGINAL_URLOPEN", fake_urlopen)
    with egress_proxy.via_proxy("ted-notices") as url:
        assert url is None
        assert egress_proxy.current_proxy_url() is None
        assert urllib.request.urlopen("https://example.invalid/") == "resp"
    assert called["url"] == "https://example.invalid/"


def test_acquire_parses_the_contract(monkeypatch):
    monkeypatch.setenv("FD_PROXY_FORWARDER", "http://forwarder:8080")

    def fake_post(path, payload, timeout=5.0):
        assert path == "/acquire" and payload == {"source": "ted-notices"}
        return {"upstream_url": "http://u:p@10.0.0.1:30080", "addr_id": 7, "provider": "gost-mesh"}

    monkeypatch.setattr(egress_proxy, "_post", fake_post)
    assert egress_proxy.acquire("ted-notices") == (
        "http://u:p@10.0.0.1:30080", 7, "gost-mesh")


def test_acquire_without_upstream_url_is_direct(monkeypatch):
    monkeypatch.setenv("FD_PROXY_FORWARDER", "http://forwarder:8080")
    monkeypatch.setattr(egress_proxy, "_post", lambda *a, **k: {"addr_id": None})
    assert egress_proxy.acquire("x") == (None, None, None)


def test_context_sets_then_restores(monkeypatch):
    monkeypatch.setenv("FD_PROXY_FORWARDER", "http://forwarder:8080")
    monkeypatch.setattr(egress_proxy, "_post", lambda p, payload, timeout=5.0: (
        {"upstream_url": "http://u:p@1.2.3.4:30080", "addr_id": 3, "provider": "gost-mesh"}
        if p == "/acquire" else {"status": "ok"}))
    with egress_proxy.via_proxy("ted-notices"):
        assert egress_proxy.current_proxy_url() == "http://u:p@1.2.3.4:30080"
    assert egress_proxy.current_proxy_url() is None


def test_success_releases_ok_and_failure_releases_fail(monkeypatch):
    monkeypatch.setenv("FD_PROXY_FORWARDER", "http://forwarder:8080")
    seen = []

    def fake_post(path, payload, timeout=5.0):
        if path == "/acquire":
            return {"upstream_url": "http://u:p@1.2.3.4:30080", "addr_id": 3, "provider": "gost-mesh"}
        seen.append(payload)
        return {"status": "ok"}

    monkeypatch.setattr(egress_proxy, "_post", fake_post)
    with egress_proxy.via_proxy("ted-notices"):
        pass
    assert seen[-1]["outcome"] == "ok"
    assert seen[-1]["addr_id"] == 3 and seen[-1]["source"] == "ted-notices"
    with pytest.raises(RuntimeError):
        with egress_proxy.via_proxy("ted-notices"):
            raise RuntimeError("boom")
    assert seen[-1]["outcome"] == "fail"


def test_release_is_noop_without_addr_id(monkeypatch):
    monkeypatch.setenv("FD_PROXY_FORWARDER", "http://forwarder:8080")
    called = []
    monkeypatch.setattr(egress_proxy, "_post", lambda *a, **k: called.append(a) or {})
    egress_proxy.release("x", None, None, "ok")
    assert called == []


def test_install_is_idempotent_and_routes_through_proxy(monkeypatch):
    egress_proxy.install()
    patched = urllib.request.urlopen
    egress_proxy.install()
    assert urllib.request.urlopen is patched, "重复 install 不该再包一层"

    # 有代理上下文时，必须用 ProxyHandler 而不是原版 urlopen
    used = {}

    class _Opener:
        def open(self, url, data=None, **kw):
            used["proxied"] = url
            return "via-proxy"

    def fake_build_opener(handler):
        used["proxy_handler"] = handler
        return _Opener()

    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)
    token = egress_proxy._proxy_url.set("http://u:p@1.2.3.4:30080")
    try:
        assert urllib.request.urlopen("https://example.invalid/") == "via-proxy"
    finally:
        egress_proxy._proxy_url.reset(token)
    assert used["proxied"] == "https://example.invalid/"
