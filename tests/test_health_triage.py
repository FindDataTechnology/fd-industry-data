"""health.triage：确定性分诊 9 条规则路径测试。"""
from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.health import ticket, triage
from fd_industry_data.health.triage import Run, Thresholds, Verdict

NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)
SOURCE = "fred-data"
UNIT = "spiders/fred-data/"
PERIOD = 24.0  # 小时


def _ts(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def _run(status: str, hours_ago: float, *, error: str = "", http_status=None, rows=None) -> Run:
    return Run(
        started_at=_ts(hours_ago),
        status=status,
        rows=rows,
        error=error,
        http_status=http_status,
    )


def _fail(hours_ago: float, error: str = "", **kwargs) -> Run:
    return _run("failed", hours_ago, error=error, **kwargs)


def _ok(hours_ago: float, **kwargs) -> Run:
    return _run("success", hours_ago, rows=10, **kwargs)


def _classify(runs, *, thresholds=Thresholds(), period=PERIOD, now=NOW) -> Verdict:
    return triage.classify(SOURCE, UNIT, period, runs, now, thresholds)


# ---------------------------------------------------------------- 规则 1：健康短路

def test_healthy_recent_success_short_circuits_failures():
    runs = [
        _fail(3, "connection timed out"),
        _fail(2, "connection timed out"),
        _fail(1, "connection timed out"),
        _ok(0.5),
    ]
    verdict = _classify(runs)
    assert verdict.category is None
    assert verdict.needs_human is False
    assert verdict.suspected_anti_bot is False
    assert verdict.suggested_action == ""
    assert "rule1" in verdict.rule_trace and "healthy" in verdict.rule_trace


def test_healthy_when_tail_failures_below_consecutive_threshold():
    runs = [_ok(5), _fail(2, "connection timed out"), _fail(1, "connection timed out")]
    verdict = _classify(runs)
    assert verdict == Verdict(None, False, False, "", verdict.rule_trace)
    assert verdict.category is None and verdict.needs_human is False
    assert "rule1" in verdict.rule_trace


# ---------------------------------------------------------------- 规则 2：证据不足

def test_insufficient_evidence_needs_human():
    verdict = _classify([_fail(1, "connection timed out")])
    assert verdict.category is None
    assert verdict.needs_human is True
    assert verdict.suspected_anti_bot is False
    assert verdict.suggested_action != ""
    assert "rule2" in verdict.rule_trace


def test_insufficient_evidence_without_failures_after_stale_success():
    verdict = _classify([_ok(100)])
    assert verdict.needs_human is True and verdict.category is None
    assert "rule2" in verdict.rule_trace


# ---------------------------------------------------------------- 规则 3：反爬签名

def test_anti_bot_http_status():
    runs = [
        _fail(3, "Forbidden", http_status=403),
        _fail(2, "HTTP 403", http_status=403),
        _fail(1, "request denied", http_status=403),
    ]
    verdict = _classify(runs)
    assert verdict.category == "contract"
    assert verdict.suspected_anti_bot is True
    assert verdict.needs_human is False
    assert verdict.suggested_action == "更换数据表面（官方 API/替代入口）或退役；禁止逆向"
    assert "rule3" in verdict.rule_trace


def test_anti_bot_from_error_signature_without_status():
    runs = [
        _fail(3, "Cloudflare challenge page", http_status=200),
        _fail(2, "captcha wall (瑞数)", http_status=200),
        _fail(1, "人机校验拦截", http_status=200),
    ]
    verdict = _classify(runs)
    assert verdict.category == "contract" and verdict.suspected_anti_bot is True
    assert "rule3" in verdict.rule_trace


def test_anti_bot_priority_over_structure_votes():
    runs = [
        _fail(3, "json decode error", http_status=200),
        _fail(2, "selector not found", http_status=200),
        _fail(1, "access denied by waf", http_status=403),
    ]
    verdict = _classify(runs)
    assert verdict.category == "contract"
    assert verdict.suspected_anti_bot is True
    assert verdict.suggested_action.startswith("更换数据表面")


def test_anti_bot_requires_three_runs_by_default():
    verdict = _classify([_fail(1, "captcha challenge", http_status=403)])
    assert verdict.needs_human is True and verdict.category is None  # 先命中规则 2


# ---------------------------------------------------------------- 规则 4：源死亡

def test_source_dead_without_any_success():
    runs = [
        _fail(3, "DNS lookup failed"),
        _fail(2, "nodename nor servname provided"),
        _fail(1, "could not resolve host"),
    ]
    verdict = _classify(runs)
    assert verdict.category == "source-dead"
    assert verdict.needs_human is False
    assert verdict.suggested_action == "退役评估"
    assert "rule4" in verdict.rule_trace


def test_source_dead_stale_success_beats_network_tokens():
    runs = [
        _ok(300),  # 300h > 7 * 24h：源疑似死亡
        _fail(3, "connection refused"),
        _fail(2, "connection refused"),
        _fail(1, "connection refused"),
    ]
    verdict = _classify(runs)
    assert verdict.category == "source-dead"
    assert "rule4" in verdict.rule_trace


def test_source_dead_vs_network_distinction_by_staleness():
    errors = "connect error: connection refused"
    stale_runs = [
        _fail(3, errors),
        _fail(2, errors),
        _fail(1, errors),
    ]
    assert _classify(stale_runs).category == "source-dead"  # 无 success -> 视为 stale

    fresh_enough_runs = [_ok(100)] + [  # 100h <= 7*24h，尚未到源死亡判定
        _fail(3, errors),
        _fail(2, errors),
        _fail(1, errors),
    ]
    verdict = _classify(fresh_enough_runs)
    assert verdict.category == "network"
    assert "rule5" in verdict.rule_trace


# ---------------------------------------------------------------- 规则 5：网络层

def test_network_timeouts():
    runs = [_ok(100)] + [
        _fail(3, "Read timed out"),
        _fail(2, "connection reset by peer"),
        _fail(1, "ECONNRESET"),
    ]
    verdict = _classify(runs)
    assert verdict.category == "network"
    assert verdict.needs_human is False
    assert verdict.suggested_action == "出口切换/代理复测（不改代码）"
    assert "rule5" in verdict.rule_trace


def test_network_http_5xx():
    runs = [_fail(3, "", http_status=502), _fail(2, "", http_status=503), _fail(1, "", http_status=504)]
    verdict = _classify(runs)
    assert verdict.category == "network"
    assert "rule5" in verdict.rule_trace


# ---------------------------------------------------------------- 规则 6：结构层

def test_structure_parse_errors_with_http_200():
    runs = [_ok(100)] + [
        _fail(3, "json decode error: unexpected token", http_status=200),
        _fail(2, "selector not found", http_status=200),
        _fail(1, "KeyError: 'data'", http_status=200),
    ]
    verdict = _classify(runs)
    assert verdict.category == "structure"
    assert verdict.needs_human is False
    assert verdict.suggested_action == "定向修 parser"
    assert "rule6" in verdict.rule_trace


def test_structure_requires_success_or_absent_http_status():
    runs = [_ok(100)] + [
        _fail(3, "json decode error", http_status=500),
        _fail(2, "json decode error", http_status=500),
        _fail(1, "json decode error", http_status=500),
    ]
    verdict = _classify(runs)
    assert verdict.category != "structure"


# ---------------------------------------------------------------- 规则 7：契约层

def test_contract_parameter_errors():
    runs = [_ok(100)] + [
        _fail(3, "HTTP 400 Bad Request", http_status=400),
        _fail(2, "invalid parameter series_id", http_status=200),
        _fail(1, "endpoint moved", http_status=200),
    ]
    verdict = _classify(runs)
    assert verdict.category == "contract"
    assert verdict.suspected_anti_bot is False
    assert verdict.needs_human is False
    assert verdict.suggested_action == "重新侦察数据表面"
    assert "rule7" in verdict.rule_trace


def test_contract_schema_change():
    runs = [_ok(100)] + [
        _fail(3, "response schema changed", http_status=200),
        _fail(2, "接口版本升级", http_status=200),
        _fail(1, "unexpected redirect", http_status=200),
    ]
    verdict = _classify(runs)
    assert verdict.category == "contract" and "rule7" in verdict.rule_trace


# ---------------------------------------------------------------- 规则 8：分歧/未命中

def test_tie_between_two_categories_needs_human():
    runs = [_ok(100)] + [
        _fail(4, "json decode error", http_status=200),
        _fail(3, "selector not found", http_status=200),
        _fail(2, "invalid parameter", http_status=200),
        _fail(1, "endpoint moved", http_status=200),
    ]
    verdict = _classify(runs)
    assert verdict.category is None
    assert verdict.needs_human is True
    assert "rule8" in verdict.rule_trace and "tie" in verdict.rule_trace


def test_unclassified_majority_needs_human():
    runs = [_ok(100)] + [
        _fail(3, "gizmo exploded"),
        _fail(2, "gizmo exploded"),
        _fail(1, "connection timed out"),
    ]
    verdict = _classify(runs)
    assert verdict.category is None
    assert verdict.needs_human is True
    assert "rule8" in verdict.rule_trace and "unclassified" in verdict.rule_trace


# ---------------------------------------------------------------- 规则 9：兜底

def test_fallback_identical_unknown_pattern():
    runs = [
        _fail(3, "gizmo exploded: flux capacitor overheated"),
        _fail(2, "gizmo exploded: flux capacitor overheated"),
        _fail(1, "gizmo exploded: flux capacitor overheated"),
    ]
    verdict = _classify(runs)
    assert verdict.category == "fallback"
    assert verdict.needs_human is False
    assert verdict.suggested_action == "整段重生成（兜底）"
    assert "rule9" in verdict.rule_trace


def test_fallback_requires_consecutive_threshold():
    runs = [
        _fail(3, "gizmo exploded"),
        _fail(2, "gizmo exploded"),
        _fail(1, "gizmo exploded"),
    ]
    verdict = _classify(runs, thresholds=Thresholds(consecutive_failures=5))
    assert verdict.category is None and verdict.needs_human is True  # 规则 8
    assert "rule9" not in verdict.rule_trace


def test_fallback_requires_identical_fingerprints():
    runs = [
        _fail(3, "gizmo exploded"),
        _fail(2, "sprocket melted"),
        _fail(1, "widget warped"),
    ]
    verdict = _classify(runs)
    assert verdict.needs_human is True and verdict.category is None
    assert "rule9" not in verdict.rule_trace


# ---------------------------------------------------------------- 阈值与通用性质

def test_min_runs_threshold_can_be_overridden():
    verdict = _classify(
        [_fail(1, "connection timed out")],
        thresholds=Thresholds(min_runs_for_verdict=1),
    )
    assert verdict.category == "network"
    assert "rule5" in verdict.rule_trace


def test_every_verdict_has_rule_trace():
    samples = [
        [_ok(0.5)],
        [_fail(1, "connection timed out")],
        [_fail(1, "captcha", http_status=403)],
        [_fail(1, "dns failed")],
        [_fail(1, "gizmo exploded")],
    ]
    for runs in samples:
        verdict = _classify(runs)
        assert verdict.rule_trace


def test_input_order_does_not_matter():
    runs = [
        _ok(100),
        _fail(3, "json decode error", http_status=200),
        _fail(2, "selector not found", http_status=200),
        _fail(1, "KeyError: 'x'", http_status=200),
    ]
    shuffled = list(runs)
    random.Random(7).shuffle(shuffled)
    assert _classify(runs) == _classify(shuffled)


def test_verdict_maps_to_valid_ticket():
    runs = [
        _fail(3, "captcha challenge", http_status=403),
        _fail(2, "captcha challenge", http_status=403),
        _fail(1, "captcha challenge", http_status=403),
    ]
    verdict = _classify(runs)
    doc = ticket.new_ticket(
        SOURCE,
        UNIT,
        evidence={
            "window": {"from": _ts(4), "to": _ts(0)},
            "failing_runs": [{"started_at": run.started_at, "error": run.error} for run in runs],
            "last_success": None,
            "notes": verdict.rule_trace,
        },
        verify_commands=["python3 -m pytest tests/test_fred.py -q"],
        category=verdict.category,
        needs_human=verdict.needs_human,
        suspected_anti_bot=verdict.suspected_anti_bot,
        suggested_action=verdict.suggested_action,
    )
    assert ticket.validate_ticket(doc) == []
    assert doc["category"] == "contract" and doc["suspected_anti_bot"] is True