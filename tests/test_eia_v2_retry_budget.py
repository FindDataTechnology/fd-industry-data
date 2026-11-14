"""eia-v2 的重试预算（2026-11-14）。

这组测钉三件会让这条线**静默漏小时**的事，起因是当晚每小时的任务连续
`BackoffLimitExceeded`、rows=0，而集群里实测同一个端点同一个 key：

    W73h → 200 / 1.1s      W24h → 200 / 56.8s      W06h → 90s 仍未返回

1. **上游正常耗时能到 50-60 秒**，旧配置 30s 超时 ⇒ 一半的请求被判成失败。
   超时必须是"比已知正常耗时更宽"的数，否则重试只是在同一段拥堵里打转。
2. **重试次数要够**：一次超时/504 是瞬时，2 次尝试（=1 次重试）在 60 秒级耗时下等于没有。
3. **退避要递增**：固定 2s 会把三次尝试挤在一起；上游慢是"渐好"，不是"立刻好"。

同时钉住"不该重试的仍然不重试"：4xx（除 408/429）立即失败——那是配置/凭据问题，
重试只会放大。
"""
from __future__ import annotations

import importlib.util
import io
import urllib.error
from pathlib import Path

import pytest

SPIDER = Path(__file__).resolve().parents[1] / "spiders" / "eia-v2" / "spider.py"


def _load():
    spec = importlib.util.spec_from_file_location("eia_v2_spider", SPIDER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture()
def spider(monkeypatch):
    monkeypatch.setenv("EIA_API_KEY", "test-key")
    monkeypatch.setattr("time.sleep", lambda *_: None)
    return _load()


class _Resp(io.BytesIO):
    status = 200


def test_timeout_is_wider_than_the_measured_normal_latency(spider):
    # 实测正常耗时到 56.8s —— 超时必须显著更宽，否则"成功的那一半"会被判成失败。
    assert spider.TIMEOUT >= 90, f"TIMEOUT={spider.TIMEOUT}s，低于实测正常耗时上限"


def test_timeout_is_env_overridable(spider, monkeypatch):
    monkeypatch.setenv("FD_EIA_TIMEOUT_S", "45")
    mod = _load()
    assert mod.TIMEOUT == 45.0, "运维要能不重建镜像就调超时"


def test_retries_cover_a_call_that_times_out_then_succeeds(spider, monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("The read operation timed out")
        return _Resp(b'{"response": {"total": "1", "data": [{"period": "2026-11-14T00"}]}}')

    monkeypatch.setattr(spider.urllib.request, "urlopen", fake_urlopen)
    payload = spider._request([("frequency", "hourly")], "k")
    assert calls["n"] == 2
    assert payload["response"]["total"] == "1"


def test_retries_stop_after_the_budget_with_a_stated_count(spider, monkeypatch):
    calls = {"n": 0}

    def always_timeout(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 504, "Gateway Timeout", {}, io.BytesIO(b""))

    monkeypatch.setattr(spider.urllib.request, "urlopen", always_timeout)
    with pytest.raises(RuntimeError) as exc:
        spider._request([("frequency", "hourly")], "k")
    assert calls["n"] == spider.RETRY_ATTEMPTS == 3, "504 是瞬时故障，必须用满 3 次尝试"
    assert "3 次尝试" in str(exc.value)


def test_4xx_still_fails_fast_without_retrying(spider, monkeypatch):
    calls = {"n": 0}

    def bad_request(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                     io.BytesIO(b"parameter out of range: length"))

    monkeypatch.setattr(spider.urllib.request, "urlopen", bad_request)
    with pytest.raises(RuntimeError):
        spider._request([("frequency", "hourly")], "k")
    assert calls["n"] == 1, "4xx 是配置问题，重试只会放大"


def test_backoff_escalates_between_attempts(spider, monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(spider.time, "sleep", lambda s: slept.append(s))

    def always_timeout(req, timeout=None):
        raise TimeoutError("slow")

    monkeypatch.setattr(spider.urllib.request, "urlopen", always_timeout)
    with pytest.raises(RuntimeError):
        spider._request([("frequency", "hourly")], "k")
    assert slept == [2.0, 4.0], f"退避必须递增（实际 {slept}）"
