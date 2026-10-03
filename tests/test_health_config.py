"""中央库 health_config 读取（spider-self-heal-l2 2.1 中央库形态）。全离线（psql 打桩）。"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.health import config as cfg  # noqa: E402


class _P:
    def __init__(self, rc: int, out: str = "", err: str = ""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_db_rows_apply(monkeypatch):
    out = (
        "master_switch\tfalse\n"
        'max_daily_tickets\t3\n'
        "lookback_hours\t720\n"
        'thresholds\t{"consecutive_failures": 5, "freshness_multiplier": 3.0, "min_runs_for_verdict": 4}\n'
        "excluded_sources\t[\"drill-wiring\"]\n"
        'expected_period_overrides\t{"alpha": 12.0}\n'
    )
    monkeypatch.setattr(cfg.subprocess, "run", lambda *a, **k: _P(0, out))
    c = cfg.load_config_from_db("postgres://x")
    assert c is not None
    assert c.master_switch is False
    assert c.max_daily_tickets == 3
    assert c.thresholds.consecutive_failures == 5
    assert c.thresholds.freshness_multiplier == 3.0
    assert c.excluded_sources == ("drill-wiring",)
    assert c.expected_period_overrides == {"alpha": 12.0}


def test_db_failures_return_none(monkeypatch):
    monkeypatch.setattr(cfg.subprocess, "run", lambda *a, **k: _P(2, "", "boom"))
    assert cfg.load_config_from_db("postgres://x") is None
    assert cfg.load_config_from_db("") is None

    def boom(*a, **k):
        raise cfg.subprocess.TimeoutExpired(cmd="psql", timeout=1)

    monkeypatch.setattr(cfg.subprocess, "run", boom)
    assert cfg.load_config_from_db("postgres://x") is None


def test_db_bad_json_or_empty_returns_none(monkeypatch):
    monkeypatch.setattr(cfg.subprocess, "run", lambda *a, **k: _P(0, 'master_switch\tnot-json\n'))
    assert cfg.load_config_from_db("postgres://x") is None
    monkeypatch.setattr(cfg.subprocess, "run", lambda *a, **k: _P(0, ""))
    assert cfg.load_config_from_db("postgres://x") is None


def test_unknown_keys_ignored(monkeypatch):
    out = "master_switch\tfalse\nfoo\tbar\n"
    monkeypatch.setattr(cfg.subprocess, "run", lambda *a, **k: _P(0, out))
    c = cfg.load_config_from_db("postgres://x")
    assert c is not None and c.master_switch is False and c.max_daily_tickets == 5