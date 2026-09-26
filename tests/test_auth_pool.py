"""Offline unit tests for the session pool (crypto/redact/guard semantics).

Lease/race/TTL semantics run against the real central PG in
scripts/verify_auth_semantics.py (integration); here only the pieces that
need no cluster.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("PLATFORM_SESSION_KEY", "ZyLU9tWJ3mFq0eK8sQc2Vh4aXr7NpBdG")

import pytest

from fd_industry_data import auth


def test_jar_roundtrip_and_redact():
    jar = {"cookies": [{"name": "sid", "value": "SECRETVALUE"}],
           "user_agent": "UA", "password": "hunter2"}
    blob = auth.encrypt_jar(jar)
    assert isinstance(blob, bytes) and b"SECRETVALUE" not in blob
    assert auth.decrypt_jar(blob) == jar
    red = auth.redact_jar(jar)
    assert red["password"] == "***" and red["user_agent"] == "UA"
    dumped = str(red)
    assert "SECRETVALUE" not in dumped and "hunter2" not in dumped


def test_missing_key_refused():
    key = os.environ.pop("PLATFORM_SESSION_KEY")
    try:
        with pytest.raises(RuntimeError, match="PLATFORM_SESSION_KEY"):
            auth.encrypt_jar({})
    finally:
        os.environ["PLATFORM_SESSION_KEY"] = key


def test_status_and_event_vocab_frozen():
    # inherited from legal-auth-broker; changing these breaks the pool contract
    assert auth.STATUSES == ("login_required", "active", "cooldown", "banned", "retired")
    assert "suspect_yield" in auth.EVENT_KINDS and "lease_acquired" in auth.EVENT_KINDS


def test_schema_managed_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("FD_SCHEMA_MANAGED", "1")

    class FakeConn:
        class cur:
            executed = False

            def execute(self, *a):
                self.executed = True

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def cursor(self):
            return self.cur()

    conn = FakeConn()
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        yield conn

    conn.__enter__ = _ctx().__enter__
    conn.__exit__ = _ctx().__exit__
    auth.ensure_schema(conn)
    assert not conn.cur.executed
