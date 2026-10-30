"""① gitops_sync 通道单测（fake fetcher 注入 + monkeypatch urlopen，纯本地假数据，不联网）。

覆盖 spec 场景（infra-telemetry-surface R2 第①类 / R3 失败归因 / R4 零凭据零端点）：
- 逐条目可见：健康/异常/未知 app 全列，summary 计数正确，as_of 在场
- 拔通道三态：env 未设 → not_configured；HTTP 401 → unauthorized；urllib 超时 → timeout
  （三态均 ok=False + items 空 + note，禁空集假绿）
- 脱敏：condition message 含 token=xxx → item detail 出 [REDACTED] 无原值
- 未知态不猜绿：缺 status 段 → health="unknown" 计入 summary.unknown，不并入全绿
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from infra_telemetry import gitops

BASE_URL = "http://argocd.invalid.test"
TOKEN = "SECRET-ARGO-TOKEN-913"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """通道 env 一律先拔干净，测试自管——本机真实 env 不得影响判定。"""
    monkeypatch.delenv(gitops.BASE_URL_ENV, raising=False)
    monkeypatch.delenv(gitops.TOKEN_ENV, raising=False)


def fake_fetcher(payload, calls=None):
    def fetch(url, *, channel, headers=None, timeout=20):
        if calls is not None:
            calls.append({"url": url, "headers": dict(headers or {}), "timeout": timeout})
        return payload
    return fetch


def frozen_as_of():
    """monkeypatch 模块内 utc_now_iso，同 test_infra_telemetry.py 风格。"""
    orig = gitops.utc_now_iso
    gitops.utc_now_iso = lambda: "2026-10-31T00:00:00+00:00"
    return orig


# ── 1. 逐条目可见 + summary 计数 ─────────────────────────────────────────────

PAYLOAD_3_APPS = {
    "items": [
        {
            "metadata": {"name": "payments-api"},
            "status": {
                "sync": {"status": "Synced"},
                "health": {"status": "Healthy"},
                "operationState": {"phase": "Succeeded", "finishedAt": "2026-10-30T09:00:00Z"},
                "conditions": [],
            },
        },
        {
            "metadata": {"name": "web-ui"},
            "status": {
                "sync": {"status": "OutOfSync"},
                "health": {"status": "Syncing"},
                "operationState": {"phase": "Running"},
                "conditions": [
                    {"type": "ApplicationNotSynced", "message": "spec differs from Git",
                     "lastTransitionTime": "2026-10-30T12:00:00Z"}
                ],
            },
        },
        {
            "metadata": {"name": "mystery"},
            "status": {"sync": {"status": "Unknown"}, "health": {"status": "Unknown"}},
        },
    ]
}


def test_every_app_visible_and_counts(monkeypatch):
    monkeypatch.setenv(gitops.BASE_URL_ENV, BASE_URL + "/")  # 带尾斜杠，验证拼接
    monkeypatch.setenv(gitops.TOKEN_ENV, TOKEN)
    calls: list = []
    orig = frozen_as_of()
    try:
        out = gitops.collect(fetcher=fake_fetcher(PAYLOAD_3_APPS, calls))
    finally:
        gitops.utc_now_iso = orig

    assert out["tool"] == "gitops_sync"
    assert out["ok"] is True
    assert out["as_of"] == "2026-10-31T00:00:00+00:00"
    assert out["channels"] == [{"channel": "argocd", "status": "ok"}]

    # 正常项也在：三条全列，不挑异常
    assert [i["app"] for i in out["items"]] == ["payments-api", "web-ui", "mystery"]
    by = {i["app"]: i for i in out["items"]}
    assert by["payments-api"]["anomalous"] is False
    assert by["payments-api"]["operation_state"] == "Succeeded"
    assert by["payments-api"]["last_transition"] == "2026-10-30T09:00:00Z"  # operationState 回落
    assert by["web-ui"]["sync_status"] == "OutOfSync" and by["web-ui"]["anomalous"] is True
    assert by["web-ui"]["last_transition"] == "2026-10-30T12:00:00Z"  # condition 最大时间
    assert by["mystery"]["health_status"] == "Unknown" and by["mystery"]["anomalous"] is True

    s = out["summary"]
    assert s["apps"] == 3 and s["out_of_sync"] == 1 and s["degraded"] == 0 and s["unknown"] == 1

    # 请求侧：标准端点 + Bearer；返回侧：零凭据零端点（R4）
    assert calls[0]["url"] == BASE_URL + "/api/v2/applications"
    assert calls[0]["headers"]["Authorization"] == "Bearer " + TOKEN
    blob = json.dumps({"items": out["items"], "summary": out["summary"], "channels": out["channels"]},
                      ensure_ascii=False)
    assert "argocd.invalid.test" not in blob
    assert TOKEN not in blob


# ── 2. 拔通道三态：not_configured / unauthorized / timeout ──────────────────

def _assert_failed_envelope(out, reason):
    assert out["ok"] is False
    assert out["items"] == []  # 失败不谎报数据
    ch = out["channels"][0]
    assert ch["channel"] == "argocd" and ch["status"] == "failed"
    assert ch["reason"] == reason
    assert out["summary"]["note"] == "channel failed — no data claimed"
    assert out["summary"]["apps"] == 0


def test_env_unset_not_configured(monkeypatch):
    calls: list = []
    out = gitops.collect(fetcher=fake_fetcher(PAYLOAD_3_APPS, calls))
    assert calls == []  # base env 未设绝不发请求、不猜端点
    _assert_failed_envelope(out, "not_configured")
    assert "FD_TELEMETRY_ARGOCD_URL" in out["channels"][0]["detail"]


def test_http_401_is_unauthorized(monkeypatch):
    """走真 io_.http_json 归因链：urlopen 抛 HTTPError 401 → unauthorized。"""
    monkeypatch.setenv(gitops.BASE_URL_ENV, BASE_URL)

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    out = gitops.collect()
    _assert_failed_envelope(out, "unauthorized")
    assert "401" in out["channels"][0]["detail"]


def test_urllib_timeout_is_timeout(monkeypatch):
    """urlopen 抛 URLError("timed out") → timeout 归因，禁伪装空集全绿。"""
    monkeypatch.setenv(gitops.BASE_URL_ENV, BASE_URL)

    def boom(req, timeout=None):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    out = gitops.collect(timeout=5)
    _assert_failed_envelope(out, "timeout")


# ── 3. 脱敏：condition message 含凭据形态 ────────────────────────────────────

def test_condition_detail_scrubbed(monkeypatch):
    monkeypatch.setenv(gitops.BASE_URL_ENV, BASE_URL)
    payload = {"items": [{
        "metadata": {"name": "cart"},
        "status": {
            "sync": {"status": "OutOfSync"},
            "health": {"status": "Degraded"},
            "operationState": {"phase": "Failed"},
            "conditions": [
                {"type": "ComparisonError",
                 "message": "normalizer failed token=abc123xyz while syncing",
                 "lastTransitionTime": "2026-10-30T18:00:00Z"},
            ],
        },
    }]}
    out = gitops.collect(fetcher=fake_fetcher(payload))
    item = out["items"][0]
    assert item["anomalous"] is True
    assert "[REDACTED]" in item["detail"]
    assert "abc123xyz" not in json.dumps(out, ensure_ascii=False)
    assert out["summary"]["degraded"] == 1


# ── 4. 未知态不猜绿：结构缺失 → unknown 计数、ok=True 可查 ──────────────────

def test_missing_status_not_green(monkeypatch):
    monkeypatch.setenv(gitops.BASE_URL_ENV, BASE_URL)
    payload = {"items": [
        {"metadata": {"name": "steady"},
         "status": {"sync": {"status": "Synced"}, "health": {"status": "Healthy"}}},
        {"metadata": {"name": "ghost"}},  # 整个 status 段缺失 → 不猜绿
    ]}
    out = gitops.collect(fetcher=fake_fetcher(payload))
    assert out["ok"] is True  # 通道可读，是结构未知而非通道故障
    by = {i["app"]: i for i in out["items"]}
    ghost = by["ghost"]
    assert ghost["health_status"] == "unknown"
    assert ghost["sync_status"] == "unknown"
    assert ghost["anomalous"] is True  # unknown 绝不并入"全绿"
    assert out["summary"]["unknown"] == 1
    assert out["summary"]["apps"] == 2
    assert by["steady"]["anomalous"] is False


def test_register_tool_name():
    registered = {}

    class FakeMCP:
        def tool(self, name=None, description=None):
            def deco(fn):
                registered[name] = fn
                return fn
            return deco

    gitops.register(FakeMCP())
    assert list(registered) == ["gitops_sync"]
