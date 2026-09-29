"""Federated dispatch site contract (tencent-crawl-fleet-expansion 1.1-1.3):
registry growth to five sites, heartbeat touch on claim ticks, and loud
rejection of unregistered site ids on both enqueue and claim paths."""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data import dispatch
from fd_industry_data.sites import load_sites


def test_packaged_registry_has_five_sites():
    sites = load_sites()
    assert set(sites) == {"tencent", "nbs-workers", "zihan",
                          "xinru-server1", "xinru-server2"}
    assert sites["tencent"]["kind"] == "k8s"
    for sid in ("nbs-workers", "zihan", "xinru-server1", "xinru-server2"):
        assert sites[sid]["kind"] == "docker"


class _RecordingConn:
    """Minimal psycopg2 stand-in: records executes, answers nothing."""

    def __init__(self):
        self.executed = []

    def cursor(self):
        return _RecordingCursor(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _RecordingCursor:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        self._conn.executed.append((sql, params))

    def fetchone(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_claim_rejects_unregistered_site():
    conn = _RecordingConn()
    try:
        dispatch.claim_next(conn, "cheap", "test-dispatch")
    except ValueError as e:
        assert "cheap" in str(e) and "not registered" in str(e)
    else:
        raise AssertionError("expected ValueError for unregistered site")
    assert conn.executed == []  # refused before touching the DB


def test_queue_rejects_unregistered_site():
    conn = _RecordingConn()
    try:
        dispatch.queue_run(conn, "bls", site="cheap")
    except ValueError as e:
        assert "cheap" in str(e) and "not registered" in str(e)
    else:
        raise AssertionError("expected ValueError for unregistered site")
    assert conn.executed == []


def test_heartbeat_touches_last_seen_for_site():
    conn = _RecordingConn()
    dispatch.heartbeat_site(conn, "zihan")
    assert len(conn.executed) == 1
    sql, params = conn.executed[0]
    assert "UPDATE crawl_sites SET last_seen_at" in sql
    assert params == ("zihan",)


# --- schedule-driven enqueue (tencent-crawl-fleet-expansion 4.3) ---

from fd_industry_data.dispatch import cron_matches  # noqa: E402


def test_cron_everyday_daily():
    # "19 4 * * *" — daily 04:19; 04:19 matches, 04:20 does not
    assert cron_matches("19 4 * * *", 19, 4, 29, 9, 1)
    assert not cron_matches("19 4 * * *", 20, 4, 29, 9, 1)


def test_cron_step_minutes():
    # "*/5 * * * *" matches any 5-min boundary
    assert cron_matches("*/5 * * * *", 15, 7, 1, 1, 2)
    assert not cron_matches("*/5 * * * *", 17, 7, 1, 1, 2)


def test_cron_weekday_window():
    # "27 4 * * 1-5" — weekdays only; Wed(2) ok, Sat(5) no
    assert cron_matches("27 4 * * 1-5", 27, 4, 30, 9, 2)
    assert not cron_matches("27 4 * * 1-5", 27, 4, 26, 9, 5)


def test_cron_monthly_day():
    # "37 4 6 * *" — the 6th of any month
    assert cron_matches("37 4 6 * *", 37, 4, 6, 3, 0)
    assert not cron_matches("37 4 6 * *", 37, 4, 7, 3, 0)


def test_cron_dom_dow_or_rule():
    # "0 0 1 * 1" — 1st of month OR any Monday (vixie OR rule)
    assert cron_matches("0 0 1 * 1", 0, 0, 1, 9, 3)   # 1st, not Monday
    assert cron_matches("0 0 1 * 1", 0, 0, 8, 9, 0)   # Monday, not 1st
    assert not cron_matches("0 0 1 * 1", 0, 0, 9, 9, 2)  # neither


def test_cron_bad_expr_never_matches():
    assert not cron_matches("not a cron", 0, 0, 1, 1, 0)
    assert not cron_matches("* * * *", 0, 0, 1, 1, 0)
