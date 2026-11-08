"""告警线自检（openspec: wire-alert-delivery 5.2 / 5.3）。

钉四条：

1. **沉默要能被算出来**：产物目录里最后一次日期超过容忍窗，或者从未产出过。
2. **"整批鉴权失败"才算链断**：个别单失败属业务问题，不该冒充"投递链坏了"。
3. **只在集合变化时发信**：否则自检自己就变成每天一封的噪音源。
4. **清空要发恢复**：线恢复后仍每天沉默着不吭声，等于把"好了"说成"还在坏"。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location(
    "alert_selfcheck", REPO / "scripts" / "alert_selfcheck.py")
selfcheck = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(selfcheck)

TODAY = date(2026, 11, 8)


def _line(repo: Path, rel: str, days_ago: int | None):
    d = repo / rel
    d.mkdir(parents=True, exist_ok=True)
    if days_ago is not None:
        (d / (TODAY - timedelta(days=days_ago)).isoformat()).mkdir()


class _OkSMTP:
    def __init__(self, host, port):
        self.sent = []

    def send_message(self, m):
        self.sent.append(m)

    def starttls(self):
        pass

    def login(self, u, p):
        pass

    def quit(self):
        pass


@pytest.fixture()
def smtp_env(monkeypatch):
    monkeypatch.setenv("FD_ALERT_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("FD_ALERT_SMTP_PORT", "465")
    monkeypatch.setenv("FD_ALERT_SMTP_SSL", "1")
    monkeypatch.setenv("FD_ALERT_SMTP_TLS", "0")
    monkeypatch.setenv("FD_ALERT_SMTP_FROM", "support@finddatatech.cloud")
    monkeypatch.setenv("FD_ALERT_SMTP_TO", "ops@example.com")
    monkeypatch.setenv("FD_ALERT_SMTP_USER", "u")
    monkeypatch.setenv("FD_ALERT_SMTP_PASS", "p")


# ── 1. 沉默判定 ──────────────────────────────────────────────────────


def test_fresh_report_is_not_silent(tmp_path):
    _line(tmp_path, "reports/restaurant-alerts", 0)
    silent = selfcheck.check_silence(tmp_path, TODAY, {"restaurant": ("reports/restaurant-alerts", 2)})
    assert silent == []


def test_stale_report_is_silent(tmp_path):
    _line(tmp_path, "reports/restaurant-alerts", 5)
    silent = selfcheck.check_silence(tmp_path, TODAY, {"restaurant": ("reports/restaurant-alerts", 2)})
    assert silent and silent[0]["kind"] == "stale"
    assert silent[0]["days"] == 5


def test_never_reported_is_silent(tmp_path):
    (tmp_path / "reports/hotel-alerts").mkdir(parents=True)
    silent = selfcheck.check_silence(tmp_path, TODAY, {"hotel": ("reports/hotel-alerts", 2)})
    assert silent[0]["kind"] == "never_reported"


def test_suffixed_date_dirs_are_parsed(tmp_path):
    """`2026-11-08-e2e` 这类目录也要算作当天的产物。"""
    d = tmp_path / "reports/hotel-alerts/2026-11-08-e2e"
    d.mkdir(parents=True)
    silent = selfcheck.check_silence(tmp_path, TODAY, {"hotel": ("reports/hotel-alerts", 2)})
    assert silent == []


# ── 2. 工单链鉴权 ────────────────────────────────────────────────────


def _write_last_run(repo: Path, statuses):
    p = repo / selfcheck.TICKETS_LAST_RUN
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"at": "2026-11-08T00:00:00Z",
                             "submit": [{"http_status": s} for s in statuses]}),
                 encoding="utf-8")


def test_all_401_is_broken(tmp_path):
    _write_last_run(tmp_path, [401, 401, 401])
    assert selfcheck.check_ticket_auth(tmp_path)["broken"] is True


def test_mixed_statuses_are_not_broken(tmp_path):
    _write_last_run(tmp_path, [200, 401])
    assert selfcheck.check_ticket_auth(tmp_path)["broken"] is False


def test_no_submissions_is_not_broken(tmp_path):
    _write_last_run(tmp_path, [])
    assert selfcheck.check_ticket_auth(tmp_path)["broken"] is False


# ── 3/4. 只在变化时发信、清空发恢复 ─────────────────────────────────


def test_first_finding_alerts_then_no_change_is_silent(smtp_env, tmp_path):
    _line(tmp_path, "reports/hotel-alerts", 9)
    state = tmp_path / "state.json"
    report = selfcheck.evaluate(tmp_path, TODAY)

    smtp = _OkSMTP("h", 465)
    r1 = selfcheck.deliver(report, state_path=state, dry_run=False,
                           smtp_factory=lambda h, p: smtp)
    assert r1["sent"] == 1 and len(smtp.sent) == 1

    r2 = selfcheck.deliver(report, state_path=state, dry_run=False,
                           smtp_factory=lambda h, p: _OkSMTP(h, p))
    assert r2["sent"] == 0
    assert r2["reason"] == "无变化"


def test_clearing_sends_a_recovery(smtp_env, tmp_path):
    _line(tmp_path, "reports/hotel-alerts", 9)
    state = tmp_path / "state.json"
    selfcheck.deliver(selfcheck.evaluate(tmp_path, TODAY), state_path=state,
                      dry_run=False, smtp_factory=lambda h, p: _OkSMTP(h, p))

    _line(tmp_path, "reports/hotel-alerts", 0)  # 恢复了
    smtp = _OkSMTP("h", 465)
    r = selfcheck.deliver(selfcheck.evaluate(tmp_path, TODAY), state_path=state,
                          dry_run=False, smtp_factory=lambda h, p: smtp)
    assert r["sent"] == 1
    assert "恢复" in smtp.sent[0]["Subject"]


def test_dry_run_does_not_advance_state(smtp_env, tmp_path):
    _line(tmp_path, "reports/hotel-alerts", 9)
    state = tmp_path / "state.json"
    r = selfcheck.deliver(selfcheck.evaluate(tmp_path, TODAY), state_path=state, dry_run=True)
    assert r["skipped"] == 1
    assert not state.exists()


# ── 5. 已知问题不每天当新问题喊 ──────────────────────────────────────


def test_known_ticket_issue_is_labelled_not_silenced(tmp_path):
    """已知的走廊搬迁仍被检查到，但标成 ticket_auth:route_moved，不算新问题。"""
    _write_last_run(tmp_path, [401, 401])
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config/alert-selfcheck-known.json").write_text(
        json.dumps({"ticket_auth": ["route_moved"]}), encoding="utf-8")
    report = selfcheck.evaluate(tmp_path, TODAY)
    assert "ticket_auth:route_moved" in report["findings"]
    assert "ticket_auth" not in report["findings"]
    assert report["ticket"]["broken"] is True  # 事实仍在，只是不再当新问题


def test_unknown_ticket_failure_still_reported(tmp_path):
    _write_last_run(tmp_path, [401, 401])
    report = selfcheck.evaluate(tmp_path, TODAY)  # 没有 known 配置
    assert "ticket_auth" in report["findings"]


def test_unknown_issue_id_in_config_is_ignored(tmp_path):
    _write_last_run(tmp_path, [401, 401])
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config/alert-selfcheck-known.json").write_text(
        json.dumps({"ticket_auth": ["made-up-id"]}), encoding="utf-8")
    report = selfcheck.evaluate(tmp_path, TODAY)
    assert "ticket_auth" in report["findings"]
