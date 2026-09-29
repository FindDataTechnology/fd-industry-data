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
