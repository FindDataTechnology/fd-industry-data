#!/usr/bin/env python3
"""Session-pool integration semantics against the real central PG (+ RustFS).

Covers session-pool tasks 1.1/2.x: lease race (one winner), TTL recycle,
banned isolation (single account down does not stop the pool), explicit
auth_failed feedback, zero-yield suspicion, pool-dry pending failure, and
an encrypted jar round-trip through RustFS. Leaves no rows behind.

Env: FD_CRAWL_DB_URL (fd_platform DSN), PLATFORM_SESSION_KEY, RUSTFS_*.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fd_industry_data import auth, dispatch  # noqa: E402

SRC = "auth-verify"
ACCOUNTS = ("acct-a", "acct-b")


def check(ok, name):
    print(("PASS " if ok else "FAIL ") + name)
    return ok


def main() -> int:
    url = os.environ.get("FD_CRAWL_DB_URL")
    if not url:
        print("verify_auth: FD_CRAWL_DB_URL required", file=sys.stderr)
        return 1
    ok = True
    conn = dispatch.connect(url)
    conn.autocommit = True
    auth.ensure_schema(conn)

    with conn.cursor() as cur:
        cur.execute("DELETE FROM crawl_identity_events WHERE identity_id IN "
                    "(SELECT id FROM crawl_identities WHERE source=%s)", (SRC,))
        cur.execute("DELETE FROM crawl_identities WHERE source=%s", (SRC,))

    # -- identity bootstrap + trust chain (login -> probe -> active) --------
    ids = {}
    for acct in ACCOUNTS:
        ident_id = auth.request_login(conn, SRC, acct, automation="auto")
        session_ref = auth.upload_jar(SRC, acct, {"cookies": [{"name": "sid",
                                                   "value": f"secret-{acct}"}]})
        auth.complete_login(conn, ident_id, session_ref, probe_ok=True)
        ids[acct] = ident_id
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM crawl_identities WHERE source=%s "
                    "AND status='active'", (SRC,))
        ok &= check(cur.fetchone()[0] == 2, "dual identities active after login+probe")

    # -- jar roundtrip through RustFS: no plaintext at rest -----------------
    jar = auth.fetch_jar(session_ref)
    ok &= check(jar["cookies"][0]["value"] == "secret-acct-b",
                "encrypted jar round-trips via RustFS")

    # -- banned isolation first: one account down, pool lives ----------------
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE crawl_identities SET status='banned' "
                    "WHERE source=%s AND account_alias='acct-a'", (SRC,))
        auth._event(cur, ids["acct-a"], "banned", "verify ban")
    nxt = auth.lease_identity(conn, SRC, "verify")
    ok &= check(nxt is not None and nxt["account_alias"] == "acct-b",
                "banned isolation: healthy account still leasable")
    auth.release_identity(conn, nxt["id"], nxt["lease_token"])

    # -- lease race on the single remaining identity: one winner -------------
    c1, c2 = dispatch.connect(url), dispatch.connect(url)
    won = []

    def racer(c, tag):
        r = auth.lease_identity(c, SRC, f"racer-{tag}")
        if r:
            won.append(r)

    t1, t2 = threading.Thread(target=racer, args=(c1, "a")), \
        threading.Thread(target=racer, args=(c2, "b"))
    t1.start(); t2.start(); t1.join(); t2.join()
    ok &= check(len(won) == 1, "lease race on one identity: exactly one winner")
    if won:
        auth.release_identity(conn, won[0]["id"], won[0]["lease_token"])
    c1.close(); c2.close()

    # -- explicit auth failure returns identity to login_required ------------
    auth.report_auth_failed(conn, SRC, "acct-b", "verify: 401 未登录")
    status_b = [r for r in auth.pool_status(conn, SRC)
                if r["account_alias"] == "acct-b"][0]["status"]
    ok &= check(status_b == "login_required", "auth_failed -> login_required")
    dry = auth.lease_identity(conn, SRC, "verify")
    ok &= check(dry is None, "pool dry after both identities unusable")

    # -- TTL recycle ----------------------------------------------------------
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE crawl_identities SET status='active', lease_token='x', "
                    "lease_owner='ghost', lease_expires_at=now() - interval '1s' "
                    "WHERE source=%s AND account_alias='acct-b'", (SRC,))
    recycled = auth.expire_leases(conn)
    ok &= check(recycled >= 1, "ttl lease recycled")
    revived = auth.lease_identity(conn, SRC, "verify")
    ok &= check(revived is not None and revived["account_alias"] == "acct-b",
                "recycled identity leasable again")
    auth.release_identity(conn, revived["id"], revived["lease_token"])

    # -- zero-yield suspicion (platform-side inference) -----------------------
    for _ in range(auth.SUSPECT_ZERO_RUNS):
        auth.record_run_outcome(conn, ids["acct-b"], 0)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM crawl_identity_events "
                    "WHERE identity_id=%s AND kind='suspect_yield'", (ids["acct-b"],))
        ok &= check(cur.fetchone()[0] == 1, "suspect_yield fired exactly once")
    auth.record_run_outcome(conn, ids["acct-b"], 5)  # recovery resets
    with conn.cursor() as cur:
        cur.execute("SELECT consecutive_zero_runs FROM crawl_identities WHERE id=%s",
                    (ids["acct-b"],))
        ok &= check(cur.fetchone()[0] == 0, "healthy run resets suspicion counter")

    # -- runner heuristic flags auth-shaped failures ---------------------------
    from fd_industry_data.runner_cli import _looks_like_auth_failure
    ok &= check(_looks_like_auth_failure("HTTP 401 未登录"),
                "heuristic matches 401/未登录")
    ok &= check(not _looks_like_auth_failure("connection timeout"),
                "heuristic ignores unrelated failures")

    # -- cleanup ---------------------------------------------------------------
    with conn.cursor() as cur:
        cur.execute("DELETE FROM crawl_identity_events WHERE identity_id IN "
                    "(SELECT id FROM crawl_identities WHERE source=%s)", (SRC,))
        cur.execute("DELETE FROM crawl_identities WHERE source=%s", (SRC,))
        cur.execute("SELECT count(*) FROM crawl_identities WHERE source=%s", (SRC,))
        ok &= check(cur.fetchone()[0] == 0, "cleanup complete")
    conn.close()
    print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
