"""Login-complete trigger (login-triggered enqueue, 2026-10-09): a completed
human login must queue the run it was made for — the next schedule tick may be
hours away, past the session's expiry. Fake-conn contract tests in the style of
test_dispatch_site: no DB, no network."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data import dispatch  # noqa: E402


class _RecordingConn:
    """psycopg2 stand-in: records executes, answers from fetch queues.

    fetchone/fetchall pop their next answer in call order; empty queues answer
    None / [] (the idle case).
    """

    def __init__(self, fetches=None, fetchalls=None):
        self.executed = []  # (single-spaced sql, params)
        self.fetches = list(fetches or [])
        self.fetchalls = list(fetchalls or [])

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
        self._conn.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._conn.fetches.pop(0) if self._conn.fetches else None

    def fetchall(self):
        return self._conn.fetchalls.pop(0) if self._conn.fetchalls else []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


FEDERATED = ("rmfyalk-case-crawl", "xinru-server1", "federated",
             ["node", "bin/rmfyalk-crawl.mjs"], None)
# platform row that never synced: no last_commit -> not runnable, must be skipped
PLATFORM_NO_COMMIT = ("rmfyalk-web", "xinru-server1", "platform", None, None)


def test_enqueue_for_auth_profile_queues_only_runnable_source():
    conn = _RecordingConn(fetchalls=[[FEDERATED, PLATFORM_NO_COMMIT]],
                          fetches=[None, None, (101,)])
    out = dispatch.enqueue_for_auth_profile(conn, "rmfyalk")

    assert out == ["rmfyalk-case-crawl"]
    inserts = [(s, p) for s, p in conn.executed if "INSERT INTO pending_runs" in s]
    assert len(inserts) == 1
    assert inserts[0][1] == ("rmfyalk-case-crawl", "xinru-server1", "{}",
                             "login-complete")
    # the candidate read is keyed by the auth_profile, not the source name
    assert conn.executed[0][1] == ("rmfyalk",)
    assert "auth_profile = %s" in conn.executed[0][0]


def test_enqueue_for_auth_profile_open_pending_row_dedupes():
    conn = _RecordingConn(fetchalls=[[FEDERATED]], fetches=[(7,)])
    assert dispatch.enqueue_for_auth_profile(conn, "rmfyalk") == []
    assert not any("INSERT INTO pending_runs" in s for s, _ in conn.executed)


def test_enqueue_for_auth_profile_running_crawl_run_dedupes():
    conn = _RecordingConn(fetchalls=[[FEDERATED]], fetches=[None, (9,)])
    assert dispatch.enqueue_for_auth_profile(conn, "rmfyalk") == []
    assert not any("INSERT INTO pending_runs" in s for s, _ in conn.executed)


def test_enqueue_for_auth_profile_one_bad_row_does_not_abort_the_rest(capsys):
    bad = ("rmfyalk-ghost", "no-such-site", "federated", ["node", "x.mjs"], None)
    good = ("rmfyalk-case-crawl", "xinru-server1", "federated",
            ["node", "bin/rmfyalk-crawl.mjs"], None)
    conn = _RecordingConn(fetchalls=[[bad, good]],
                          fetches=[None, None, None, None, (102,)])
    out = dispatch.enqueue_for_auth_profile(conn, "rmfyalk")

    assert out == ["rmfyalk-case-crawl"]  # the good row still landed
    inserts = [p for s, p in conn.executed if "INSERT INTO pending_runs" in s]
    assert inserts == [("rmfyalk-case-crawl", "xinru-server1", "{}",
                        "login-complete")]
    assert "no-such-site" in capsys.readouterr().err
