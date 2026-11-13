"""共享取数口在「解析不可信」时的退路：候选 IP + TLS 当判据。

背景（2026-11-14 tencent 集群实测）：``openapi.twse.com.tw`` 系统解析每次返回不同的
无关地址（微软 / Vultr / Dropbox / 其它），直连一律超时；**连 DoH 也不可信**
（``doh.pub`` 先给真地址 203.74.220.1，十分钟后给 162.125.32.5）。但四个真地址从集群
里全部可达：TLS 200、65KB 真 JSON。

所以判据不是「谁解析得更对」，而是**TLS 证书说这个 IP 是不是真身**。四条纪律：

1. **健康时零代价** — 第一次成功就返回，一个候选都不试；
2. **只在连接层失败时重试** — 上游 404 必须原样抛 ``HTTPError``，不能被重试成"慢"；
3. **不降级 TLS** — 钉的是 IP，SNI 与证书校验仍按原域名（否则等于对中间人敞开）；
4. **不内置任何 IP** — 候选只从 ``FD_PIN_HOSTS`` / 显式参数来，否则写死即到期。
"""
from __future__ import annotations

import io
import urllib.error

import pytest

from fd_industry_data import http


@pytest.fixture(autouse=True)
def _no_pins(monkeypatch):
    monkeypatch.delenv("FD_PIN_HOSTS", raising=False)


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_health_path_never_tries_a_candidate_ip(monkeypatch):
    tried = []

    monkeypatch.setattr(http.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp(b'{"ok":1}'))
    monkeypatch.setattr(http, "_pinned_opener", lambda ip: tried.append(ip))

    assert http.fetch_bytes("https://example.test/x") == b'{"ok":1}'
    assert tried == [], "健康的域名不该去试候选地址"


def test_connection_failure_retries_candidates_in_order(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("<urlopen error timed out>")

    class _Opener:
        def __init__(self, ip):
            self.ip = ip

        def open(self, req, timeout=None):
            seen.setdefault("order", []).append(self.ip)
            if self.ip != "203.74.221.3":
                raise OSError("handshake failed against a wrong host")
            return _FakeResp("[]".encode())

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(http, "_pinned_opener", _Opener)
    monkeypatch.setenv("FD_PIN_HOSTS", "openapi.twse.com.tw=203.74.220.1,203.74.221.3")

    out = http.fetch_bytes("https://openapi.twse.com.tw/v1/opendata/t187ap05_L")
    assert out == b"[]"
    assert seen["order"] == ["203.74.220.1", "203.74.221.3"], "候选要按配置顺序试，失败换下一个"


def test_http_error_is_not_retried(monkeypatch):
    tried = []

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(http, "_pinned_opener", lambda ip: tried.append(ip))
    monkeypatch.setenv("FD_PIN_HOSTS", "example.test=1.2.3.4")

    with pytest.raises(urllib.error.HTTPError):
        http.fetch_bytes("https://example.test/gone")
    assert tried == [], "404 说明解析没问题，重试只会把 404 变成慢"


def test_without_candidates_the_original_error_surfaces(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("<urlopen error [Errno 101] Network is unreachable>")

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(urllib.error.URLError):
        http.fetch_bytes("https://openapi.twse.com.tw/v1/opendata/t187ap05_L")


def test_pinned_candidates_only_match_the_exact_host():
    spec = "a.test=1.1.1.1,2.2.2.2; b.test=3.3.3.3"
    assert http.pinned_candidates("a.test", spec) == ["1.1.1.1", "2.2.2.2"]
    assert http.pinned_candidates("A.TEST", spec) == ["1.1.1.1", "2.2.2.2"]
    assert http.pinned_candidates("sub.a.test", spec) == [], "不做后缀匹配，别把别的域钉进来"


def test_pinned_connection_keeps_hostname_for_sni_and_verification(monkeypatch):
    """钉 IP 连接必须把原域名交给 wrap_socket —— SNI 与证书校验都靠它。"""
    import socket as socket_mod

    captured = {}

    class _FakeSock:
        pass

    class _FakeContext:
        def wrap_socket(self, sock, server_hostname=None, **kw):
            captured["server_hostname"] = server_hostname
            return _FakeSock()

    def fake_create_connection(addr, timeout=None, source_address=None):
        captured["addr"] = addr
        return _FakeSock()

    monkeypatch.setattr(socket_mod, "create_connection", fake_create_connection)

    conn = http._PinnedHTTPSConnection("openapi.twse.com.tw", "203.74.220.1", timeout=5)
    conn._context = _FakeContext()
    conn.connect()

    assert captured["addr"][0] == "203.74.220.1", "连接目标是钉住的 IP"
    assert captured["addr"][1] == 443
    assert captured["server_hostname"] == "openapi.twse.com.tw", "SNI 与证书校验必须按原域名"
