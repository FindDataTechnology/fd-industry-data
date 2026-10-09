"""Stale-session probe contract (periodic session-jar probe, 2026-10-09):
classification mirrors the auth broker's classifyProbeResponse, and the tick
probes active identities through their egress — flipping only a proven-dead
session, never a transient failure. Fully offline: jar/egress/probe seams are
injected; the DB is a record/answer stand-in in the test_dispatch_site style.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data import auth, session_probe  # noqa: E402
from fd_industry_data.session_probe import classify_probe  # noqa: E402


# --- classify_probe: the broker's exact ladder -----------------------------

def test_classify_ok_envelope():
    assert classify_probe(200, '{"code":"0"}', {"code": "0"}) == {
        "ok": True, "kind": "ok", "reason": "source accepted session"}


def test_classify_source_code_401_is_login_required():
    v = classify_probe(200, '{"code":"401"}', {"code": "401"})
    assert not v["ok"] and v["kind"] == "login_required"


def test_classify_http_statuses():
    assert classify_probe(401, "", None)["kind"] == "login_required"
    assert classify_probe(403, "", None)["kind"] == "forbidden"
    assert classify_probe(429, "", None)["kind"] == "rate_limited"
    assert classify_probe(503, "", None)["kind"] == "source_error"
    assert classify_probe(302, "", None)["kind"] == "unexpected_status"


def test_classify_ban_marker_beats_login_marker():
    v = classify_probe(200, "已对您的账号进行封禁", None)
    assert v["kind"] == "banned"


def test_classify_login_marker_on_non_json():
    assert classify_probe(200, "<html>请登录</html>", None)["kind"] == "login_required"
    assert classify_probe(200, "captcha required", None)["kind"] == "login_required"


def test_classify_non_json_200_is_unexpected_payload():
    v = classify_probe(200, "<html>maintenance</html>", None)
    assert not v["ok"] and v["kind"] == "unexpected_payload"


def test_classify_envelope_wins_before_keywords():
    # case content may legitimately contain 登录/验证码: code 0 still wins
    v = classify_probe(200, '{"code":"0","data":"登录"}', {"code": "0", "data": "登录"})
    assert v["ok"] and v["kind"] == "ok"


# --- _http_probe header assembly (offline: urlopen faked) ------------------

def test_http_probe_builds_broker_headers(monkeypatch):
    captured = {}

    class _Resp:
        status = 200

        def read(self):
            return b'{"code":"0"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["req"], captured["timeout"] = req, timeout
        return _Resp()

    monkeypatch.setattr(session_probe.urllib.request, "urlopen", fake_urlopen)
    jar = {
        "browser": {"userAgent": "UA-1"},
        "auth": {"headerName": "authorization", "userToken": "tok-1"},
        "storageState": {"cookies": [{"name": "a", "value": "1"},
                                     {"name": "b", "value": "2"},
                                     {"name": "", "value": "skip"}]},
    }
    status, text, payload = session_probe._http_probe(
        session_probe.PROBES["rmfyalk"], jar, None, timeout=7)
    assert (status, payload) == (200, {"code": "0"})
    h = captured["req"].headers
    # urllib capitalizes header names in Request.headers
    assert h["User-agent"] == "UA-1"
    assert h["Authorization"] == "tok-1"
    assert h["Cookie"] == "a=1; b=2"
    assert h["Content-type"] == "application/json;charset=UTF-8"
    assert captured["req"].method == "POST"
    assert captured["timeout"] == 7


def test_http_probe_http_error_status_is_a_verdict(monkeypatch):
    import urllib.error
    import io

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 401, "unauthorized", {}, io.BytesIO(b'{"code":"401"}'))

    monkeypatch.setattr(session_probe.urllib.request, "urlopen", fake_urlopen)
    status, text, payload = session_probe._http_probe(
        session_probe.PROBES["rmfyalk"], {}, None, timeout=7)
    assert status == 401 and payload == {"code": "401"}


# --- probe_stale_identities ------------------------------------------------

class _FakeConn:
    """psycopg2 stand-in: records executes, answers fetchall from a queue."""

    def __init__(self, fetchalls=None):
        self.executed = []  # (single-spaced sql, params)
        self.fetchalls = list(fetchalls or [])

    def cursor(self):
        return _FakeCursor(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        self._conn.executed.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self._conn.fetchalls.pop(0) if self._conn.fetchalls else []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


ROW = (27, "rmfyalk", "acct001", "platform-sessions/rmfyalk/acct001/a.jar",
       "proxy:3")


@pytest.fixture()
def no_jar_io(monkeypatch):
    """Jar fetch + egress resolution are the auth-module seams: fake them."""
    monkeypatch.setattr(auth, "fetch_jar", lambda ref: {"browser": {}})
    monkeypatch.setattr(auth, "resolve_egress",
                        lambda conn, ref: {"proxy_url": "http://u:p@1.2.3.4:8080"})


def test_probe_ok_stamps_last_probe_without_flip(no_jar_io, monkeypatch):
    flipped = []
    monkeypatch.setattr(auth, "report_auth_failed",
                        lambda *a, **kw: flipped.append(a))
    conn = _FakeConn(fetchalls=[[ROW]])
    out = session_probe.probe_stale_identities(
        conn, fetch=lambda d, j, p, t: (200, '{"code":"0"}', {"code": "0"}))

    assert out == [{"id": 27, "source": "rmfyalk", "account_alias": "acct001",
                    "kind": "ok", "reason": "source accepted session"}]
    assert flipped == []
    updates = [s for s, _ in conn.executed if "UPDATE crawl_identities" in s]
    assert updates == ["UPDATE crawl_identities SET last_probe_at=now(), "
                       "updated_at=now() WHERE id=%s"]
    events = [p for s, p in conn.executed if "INSERT INTO crawl_identity_events" in s]
    assert events == [(27, "probe", "ok", None)]


def test_probe_dead_session_flips_via_report_auth_failed(no_jar_io, monkeypatch):
    flipped = []
    monkeypatch.setattr(auth, "report_auth_failed",
                        lambda conn, source, alias, detail:
                        flipped.append((source, alias, detail)))
    conn = _FakeConn(fetchalls=[[ROW]])
    out = session_probe.probe_stale_identities(
        conn, fetch=lambda d, j, p, t: (200, '{"code":"401"}', {"code": "401"}))

    assert out[0]["kind"] == "login_required"
    assert flipped and flipped[0][0] == "rmfyalk" and flipped[0][1] == "acct001"
    # no local status UPDATE: the flip is report_auth_failed's job
    assert not any("UPDATE crawl_identities" in s for s, _ in conn.executed)


def test_probe_transient_failure_stamps_but_does_not_flip(no_jar_io, monkeypatch):
    flipped = []
    monkeypatch.setattr(auth, "report_auth_failed",
                        lambda *a, **kw: flipped.append(a))
    conn = _FakeConn(fetchalls=[[ROW]])
    out = session_probe.probe_stale_identities(
        conn, fetch=lambda d, j, p, t: (503, "", None))

    assert out[0]["kind"] == "source_error"
    assert flipped == []
    updates = [s for s, _ in conn.executed if "UPDATE crawl_identities" in s]
    assert len(updates) == 1 and "last_probe_at=now()" in updates[0]
    events = [p for s, p in conn.executed if "INSERT INTO crawl_identity_events" in s]
    assert events and events[0][1] == "note"


def test_probe_network_error_is_source_error(no_jar_io, monkeypatch):
    def boom(d, j, p, t):
        raise OSError("connect timeout")

    conn = _FakeConn(fetchalls=[[ROW]])
    out = session_probe.probe_stale_identities(conn, fetch=boom)
    assert out[0]["kind"] == "source_error" and "connect timeout" in out[0]["reason"]


def test_probe_skips_sources_without_definition(no_jar_io):
    conn = _FakeConn(fetchalls=[[(9, "other-source", "a1", "ref", None)]])
    assert session_probe.probe_stale_identities(
        conn, fetch=lambda *a: pytest.fail("must not probe")) == []


def test_probe_disabled_by_env(monkeypatch):
    monkeypatch.setenv("FD_PROBE_ENABLED", "0")

    class _Untouchable:
        def cursor(self):
            raise AssertionError("disabled probe must not touch the conn")

    assert session_probe.probe_stale_identities(_Untouchable()) == []


def test_probe_selection_skips_leased_identities():
    """A leased identity is in use by a running crawl: probing it could flip
    it mid-run and clear the lease under the runner (2026-10-09 hardening).
    The guard lives in the selection SQL."""
    conn = _FakeConn(fetchalls=[[]])
    out = session_probe.probe_stale_identities(
        conn, fetch=lambda *a: (200, '{"code":"0"}', {"code": "0"}))
    assert out == []
    sql = conn.executed[0][0]
    assert "lease_token IS NULL" in sql


def test_probe_selection_filters_probeable_sources_in_sql():
    """Non-probeable identities must not occupy the per-tick LIMIT: the
    source list is a SQL predicate, not a post-filter (2026-10-09 — identities
    5/6/23 starved rmfyalk's 27 out of the queue)."""
    conn = _FakeConn(fetchalls=[[]])
    session_probe.probe_stale_identities(
        conn, fetch=lambda *a: (200, '{"code":"0"}', {"code": "0"}))
    sql, params = conn.executed[0]
    assert "source = ANY(%s)" in sql
    assert sorted(params[0]) == ["rmfyalk"]
