"""robots.txt 检查：抓不到当放行，明确 Disallow 才拒（审计 05 §6）。

背景：README 写着「All spiders respect ``robots.txt``」，但共享取数路径
（``fd_industry_data/http.py`` 的 throttle、``runner_cli`` 的调度）此前都不读 robots，
各单元直接打 ``Fetcher.get``——那句话是不成立的文档承诺。这里补上能力。

**失败语义最要紧，别搞反**（RFC 9309）：
* ``robots.txt`` 取不到（404 / 网络不可达 / 解析失败）→ **放行**。
  没有 robots.txt 等于没有限制；把"抓不到"当成"禁止"会让整批源在 CI/新环境里
  全线停摆，而那不是合规，那是故障。
* 明确 Disallow 该路径 → 拒绝。
* 按 host 缓存：一个源一次跑会请求同一 host 几十上百次，不缓存会把 robots.txt
  本身变成负担。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data import http as http_mod  # noqa: E402


ROBOTS_DISALLOW = b"User-agent: *\nDisallow: /data/\n"
ROBOTS_ALLOW = b"User-agent: *\nAllow: /\n"


def _fake_urlopen(payload=None, exc=None):
    calls = {"n": 0}

    class _Resp:
        def __init__(self, data):
            self._d = data

        def read(self):
            return self._d

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(url, timeout=None):
        calls["n"] += 1
        if exc is not None:
            raise exc
        return _Resp(payload)

    return opener, calls


@pytest.fixture(autouse=True)
def _clear_cache():
    http_mod._ROBOTS_CACHE.clear()
    yield
    http_mod._ROBOTS_CACHE.clear()


def test_disallowed_path_is_rejected(monkeypatch):
    opener, _ = _fake_urlopen(ROBOTS_DISALLOW)
    monkeypatch.setattr(http_mod.urllib.request, "urlopen", opener)
    assert http_mod.allowed_by_robots("https://example.com/data/list") is False


def test_allowed_path_passes(monkeypatch):
    opener, _ = _fake_urlopen(ROBOTS_DISALLOW)
    monkeypatch.setattr(http_mod.urllib.request, "urlopen", opener)
    assert http_mod.allowed_by_robots("https://example.com/public/page") is True


def test_missing_robots_txt_means_allowed(monkeypatch):
    """取不到 robots.txt → 放行（RFC 9309）。**这是最容易被写反的一条。**"""
    opener, _ = _fake_urlopen(exc=OSError("404 / connection refused"))
    monkeypatch.setattr(http_mod.urllib.request, "urlopen", opener)
    assert http_mod.allowed_by_robots("https://example.com/anything") is True, (
        "把「抓不到 robots.txt」当成「禁止」——那会让整批源在新环境里全线停摆"
    )


def test_robots_txt_is_fetched_once_per_host(monkeypatch):
    """同一 host 只取一次 robots.txt（按 host 缓存）。"""
    opener, calls = _fake_urlopen(ROBOTS_DISALLOW)
    monkeypatch.setattr(http_mod.urllib.request, "urlopen", opener)
    for path in ("/a", "/b", "/c", "/data/x"):
        http_mod.allowed_by_robots(f"https://example.com{path}")
    assert calls["n"] == 1, f"同一 host 取了 {calls['n']} 次 robots.txt —— 没缓存"


def test_non_http_scheme_is_allowed_without_fetch(monkeypatch):
    opener, calls = _fake_urlopen(ROBOTS_DISALLOW)
    monkeypatch.setattr(http_mod.urllib.request, "urlopen", opener)
    assert http_mod.allowed_by_robots("file:///tmp/x.html") is True
    assert calls["n"] == 0, "非 http(s) 不该去取 robots.txt"
