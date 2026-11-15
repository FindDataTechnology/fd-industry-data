"""sse-szse-market-stats 两臂取数口回归（2026-11-15 立）。

当日的实况反转：2026-11-14 给 manifest 挂 ``egress_secret``（laptop 住宅出口）
后，来源从「直连被 WAF 拦」变成「代理被拦、直连反而通」——出口封锁是**双向
间歇**的。而「直连优先、网络级失败才落代理」一直只是 manifest 注释里的承诺，
spider 代码从未实现：envFrom 注入 ``HTTPS_PROXY`` 后 urllib 全量走代理、零回落，
只配 egress_secret 的日子比不配更糟。

四个判定（缺一个「承诺为真」就不成立）：

1. **直连臂必须显式空 opener**：否则 env 里有代理时「直连」根本不存在；
2. **网络级失败要真的落到代理臂**：直连失败 + 代理成功 ⇒ 拿到数据；
3. **HTTPError 不换路**：请求已到达服务器，4xx/5xx 换出口没有意义，
   也不该对同一台服务器重复打；
4. **无代理 env 时跳过代理臂**：不把同一个请求原样打两遍。
"""
import importlib.util
import json
import sys
import urllib.error
from pathlib import Path

import pytest

SPIDER = Path(__file__).resolve().parents[1] / "spiders" / "sse-szse-market-stats" / "spider.py"


def _load():
    spec = importlib.util.spec_from_file_location("sse_szse_spider", SPIDER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _OK:
    """成功返回 JSON 正文的假响应。"""

    def read(self):
        return b'{"ok": 1}'

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Boom:
    """模拟连接层失败（超时/重置/TLS 断）。"""

    def __init__(self, exc):
        self._exc = exc

    def __enter__(self):
        raise self._exc

    def __exit__(self, *exc):
        return False


class _HTTPErrorBoom:
    def __enter__(self):
        raise urllib.error.HTTPError(
            "https://www.szse.cn/x", 403, "Forbidden", hdrs=None, fp=None
        )

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = 0

    def open(self, req, timeout=None):
        self.calls += 1
        if isinstance(self.behavior, Exception):
            raise self.behavior
        return self.behavior


def _calls(built, name):
    for n, o in built:
        if n == name:
            return o.calls
    return 0


def _patch(monkeypatch, direct=None, proxy=None, proxies_seen=True):
    mod = _load()
    built = []

    def fake_build_opener(*handlers):
        # 空 ProxyHandler ⇒ 直连臂；其余 ⇒ 代理臂
        is_direct = any(getattr(h, "handlers", []) == [] or type(h).__name__ == "ProxyHandler" for h in handlers) and len(handlers) == 1
        opener = _FakeOpener(direct if is_direct else proxy)
        built.append(("direct" if is_direct else "proxy", opener))
        return opener

    monkeypatch.setattr(mod.urllib.request, "build_opener", fake_build_opener)
    monkeypatch.setattr(
        mod.urllib.request, "getproxies", lambda: {"https": "http://p:1"} if proxies_seen else {}
    )
    return mod, built


def test_direct_ok_short_circuits_proxy(monkeypatch):
    mod, built = _patch(monkeypatch, direct=_OK())
    assert mod._http_get_json("https://www.szse.cn/x", {}) == {"ok": 1}
    assert _calls(built, "direct") == 1 and _calls(built, "proxy") == 0


def test_direct_network_fail_falls_back_to_proxy(monkeypatch):
    # 直连臂 TLS 断（当日真实形态）→ 代理臂 200
    mod, built = _patch(
        monkeypatch,
        direct=_Boom(TimeoutError("timed out")),
        proxy=_OK(),
    )
    assert mod._http_get_json("https://www.szse.cn/x", {}) == {"ok": 1}
    assert [name for name, _ in built] == ["direct", "proxy"]


def test_http_error_raises_immediately_no_fallback(monkeypatch):
    # 403 说明已到达服务器 —— 换出口没有意义，也不重复打
    mod, built = _patch(monkeypatch, direct=_HTTPErrorBoom(), proxy=_OK())
    with pytest.raises(RuntimeError, match="HTTPError"):
        mod._http_get_json("https://www.szse.cn/x", {})
    assert _calls(built, "proxy") == 0


def test_no_proxy_env_skips_proxy_arm(monkeypatch):
    # 没挂 egress_secret 的环境不该把同一请求原样打两遍
    mod, built = _patch(monkeypatch, direct=_Boom(ConnectionError("refused")), proxies_seen=False)
    with pytest.raises(RuntimeError, match="直连与代理均失败"):
        mod._http_get_json("https://www.szse.cn/x", {})
    assert [name for name, _ in built] == ["direct"]


def test_both_arms_fail_aggregates_attempts(monkeypatch):
    mod, built = _patch(
        monkeypatch,
        direct=_Boom(TimeoutError("direct timed out")),
        proxy=_Boom(urllib.error.URLError("proxy ssl eof")),
    )
    with pytest.raises(RuntimeError, match="direct TimeoutError.*proxy URLError"):
        mod._http_get_json("https://www.szse.cn/x", {})
    assert [name for name, _ in built] == ["direct", "proxy"]


def test_non_json_body_is_red_not_retried(monkeypatch):
    # 非 JSON 是端点级失败：换出口救不了，不该走代理臂
    class _HTML:
        def read(self):
            return b"<html>gateway fallback</html>"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    mod, built = _patch(monkeypatch, direct=_HTML(), proxy=_OK())
    with pytest.raises(RuntimeError, match="非合法 JSON"):
        mod._http_get_json("https://www.szse.cn/x", {})
    assert _calls(built, "proxy") == 0
