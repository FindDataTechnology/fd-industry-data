"""Egress allocator unit tests (login-station-console 2.1).

Runs against the real central PG (allocator SQL is PG-specific); cleans up
its identities afterward. Pool policy: two identities of one source must
receive different proxies; explicit rebind changes/keeps per policy.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fd_industry_data import auth, dispatch  # noqa: E402


def _dsn():
    url = os.environ.get("FD_CRAWL_DB_URL")
    if not url:
        import pytest

        pytest.skip("FD_CRAWL_DB_URL not set; central-PG test")
    return url


def test_dual_accounts_get_distinct_proxies():
    conn = dispatch.connect(_dsn())
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DELETE FROM crawl_identity_events WHERE identity_id IN "
                    "(SELECT id FROM crawl_identities WHERE source='egress-t')")
        cur.execute("DELETE FROM crawl_identities WHERE source='egress-t'")
    try:
        auth.request_login(conn, "egress-t", "a1")
        auth.request_login(conn, "egress-t", "a2")
        e1 = auth.assign_egress(conn, "egress-t", "a1")
        e2 = auth.assign_egress(conn, "egress-t", "a2")
        assert e1 and e2 and e1 != e2, (e1, e2)
        # idempotent: assigning again keeps the same binding
        assert auth.assign_egress(conn, "egress-t", "a1") == e1
        # resolve returns a usable proxy URL
        resolved = auth.resolve_egress(conn, e1)
        assert resolved and resolved["proxy_url"].startswith("http")
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM crawl_identity_events WHERE identity_id IN "
                        "(SELECT id FROM crawl_identities WHERE source='egress-t')")
            cur.execute("DELETE FROM crawl_identities WHERE source='egress-t'")
    conn.close()
