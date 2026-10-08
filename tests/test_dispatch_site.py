"""Federated dispatch site contract (tencent-crawl-fleet-expansion 1.1-1.3,
legal-line-federation 1.2): registry growth to six sites (xinru-master is
the legal-line reserve host), heartbeat touch on claim ticks, loud
rejection of unregistered site ids on both enqueue and claim paths, and
federated-row exclusion in manifest sync."""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data import dispatch
from fd_industry_data.sites import load_sites


def test_packaged_registry_has_six_sites():
    sites = load_sites()
    assert set(sites) == {"tencent", "nbs-workers", "zihan",
                          "xinru-server1", "xinru-server2", "xinru-master"}
    assert sites["tencent"]["kind"] == "k8s"
    # legal-line reserve host (legal-line-federation exemption, hard-gated
    # by resource limits) is a k8s site like tencent
    assert sites["xinru-master"]["kind"] == "k8s"
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


# --- enqueue_due transaction regression (real PG; recursive with-conn crashed
#     every scheduled tick on the docker workers before this) ---

def test_enqueue_due_no_recursive_reentry():
    import os
    import pytest
    url = os.environ.get("FD_CRAWL_DB_URL")
    if not url:
        pytest.skip("FD_CRAWL_DB_URL not set; central-PG test")
    from fd_industry_data import dispatch
    conn = dispatch.connect(url)
    conn.autocommit = False
    try:
        # must not raise ProgrammingError (recursive tx re-entry)
        dispatch.enqueue_due(conn, "zihan", window_minutes=15)
    finally:
        conn.close()


# --- manifest sync must not touch registered federated rows
#     (legal-line-federation 1.3: sync owns platform rows only) ---

class _QueuedConn:
    """psycopg2 stand-in: records executes, answers fetchone from a queue."""

    def __init__(self, fetches=None):
        self.executed = []  # (single-spaced sql, params)
        self.fetches = list(fetches or [])

    def cursor(self):
        return _QueuedCursor(self)

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _QueuedCursor:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        self._conn.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._conn.fetches.pop(0) if self._conn.fetches else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _manifest_dir(tmp_path, sources):
    root = tmp_path / "spiders"
    for name, body in sources.items():
        d = root / name
        d.mkdir(parents=True)
        (d / "manifest.yaml").write_text(body)
    return root


def test_sync_sources_skips_federated_rows(tmp_path):
    content = _manifest_dir(tmp_path, {
        "flk-law-crawl": 'name: flk-law-crawl\nschedule: "0 3 * * *"\n',
        "bls": "name: bls\n",
    })
    # bls (sorted first) is not registered yet; flk-law-crawl is a
    # registered federated member
    conn = _QueuedConn(fetches=[(None,), ("federated",)])
    n = dispatch.sync_sources(conn, str(content))
    inserts = [(s, p) for s, p in conn.executed if "INSERT INTO crawl_sources" in s]
    assert len(inserts) == 1  # only the platform row is written
    assert inserts[0][1][0] == "bls"
    kind_checks = [p for s, p in conn.executed if s.startswith("SELECT kind FROM crawl_sources")]
    assert kind_checks == [("bls",), ("flk-law-crawl",)]
    assert n == 1  # return value counts synced (platform) rows only


def test_sync_sources_updates_platform_rows(tmp_path):
    content = _manifest_dir(tmp_path, {"bls": 'name: bls\nschedule: "*/5 * * * *"\n'})
    conn = _QueuedConn(fetches=[(None,)])
    n = dispatch.sync_sources(conn, str(content))
    assert n == 1
    synced = [p for s, p in conn.executed if "INSERT INTO crawl_sources" in s]
    assert synced == [("bls", "tencent", "*/5 * * * *", True, None, "")]


# --- per-source schedule timezone (rmfyalk night scheduling, 2026-10-08) ---
# The fleet's schedules are calibrated in UTC, so a source may not silently
# shift them; schedule_tz is opt-in per source and unknown tz degrades to UTC.

def test_schedule_now_utc_is_the_default():
    from datetime import datetime, timezone
    from fd_industry_data.dispatch import _schedule_now

    now = datetime(2026, 10, 8, 3, 10, tzinfo=timezone.utc)
    assert _schedule_now(None, now) == now          # NULL tz -> unchanged
    assert _schedule_now("", now) == now


def test_schedule_now_shifts_to_asia_shanghai():
    from datetime import datetime, timezone
    from fd_industry_data.dispatch import _schedule_now

    # UTC 19:10 == 03:10 next day in Shanghai
    now = datetime(2026, 10, 8, 19, 10, tzinfo=timezone.utc)
    local = _schedule_now("Asia/Shanghai", now)
    assert local.hour == 3 and local.minute == 10
    assert local.day == 9


def test_schedule_now_unknown_tz_degrades_to_utc_never_raises():
    from datetime import datetime, timezone
    from fd_industry_data.dispatch import _schedule_now

    now = datetime(2026, 10, 8, 3, 10, tzinfo=timezone.utc)
    assert _schedule_now("Mars/Olympus", now) == now


def test_enqueue_due_matches_in_the_source_timezone():
    """A source with schedule_tz='Asia/Shanghai' and schedule '10 3 * * *'
    is due at 03:10 Shanghai (19:10 UTC) — and NOT at 03:10 UTC (which would
    be 11:10 Shanghai, i.e. broad daylight)."""
    from datetime import datetime, timezone
    from fd_industry_data import dispatch

    class _Cur:
        def __init__(self, rows): self.rows, self.q = rows, []
        def execute(self, sql, args=None): self.q.append((sql, args))
        def fetchall(self): return self.rows.pop(0) if self.rows else []
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Conn:
        def __init__(self, rows): self.cur = _Cur(rows)
        def cursor(self): return self.cur
        def __enter__(self): return self
        def __exit__(self, *a): return False

    captured = {}

    def fake_now(tz=None):
        return captured["now"]

    orig = dispatch.datetime
    try:
        # 19:10 UTC = 03:10 Shanghai -> due
        captured["now"] = datetime(2026, 10, 8, 19, 10, tzinfo=timezone.utc)
        dispatch.datetime = type("D", (), {"now": staticmethod(fake_now),
                                           "timedelta": __import__("datetime").timedelta,
                                           "timezone": timezone})()
        queued = []
        dispatch.queue_run = lambda conn, src, **kw: queued.append(src)
        conn = _Conn([[("rmfyalk-case-crawl", "10 3 * * *", "Asia/Shanghai")], [], []])
        assert dispatch.enqueue_due(conn, "xinru-server1") == ["rmfyalk-case-crawl"]

        # 03:10 UTC = 11:10 Shanghai -> NOT due (the old UTC-only behaviour)
        captured["now"] = datetime(2026, 10, 8, 3, 10, tzinfo=timezone.utc)
        queued.clear()
        conn = _Conn([[("rmfyalk-case-crawl", "10 3 * * *", "Asia/Shanghai")], [], []])
        assert dispatch.enqueue_due(conn, "xinru-server1") == []

        # no tz -> unchanged UTC semantics (the whole fleet relies on it)
        captured["now"] = datetime(2026, 10, 8, 3, 10, tzinfo=timezone.utc)
        conn = _Conn([[("legacy-src", "10 3 * * *", None)], [], []])
        assert dispatch.enqueue_due(conn, "xinru-server1") == ["legacy-src"]
    finally:
        dispatch.datetime = orig
