#!/usr/bin/env python3
"""Verify federated dispatch semantics against the real central PG.

Covers crawl-platform tasks 3.1-3.3: atomic claim (two racers, one
winner), lease expiry requeue and attempts cap, full chain
(queue -> fd-dispatcher -> fd-runner -> crawl_runs linkage) with an
offline fake source, single-flight skip, and the cancel flag round-trip.
Leaves no rows behind. Requires FD_CRAWL_DB_URL (defaults to the central
ops DB) and creates /tmp/fd-verify-content.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fd_industry_data import dispatch, writer  # noqa: E402

CONTENT = Path("/tmp/fd-verify-content/spiders/fake-ok")
SRC = "fake-ok"


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    return ok


def fresh_pending(conn, *, status="pending", attempts=0, lease_until=None,
                  claimed_by=None):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO pending_runs (source, site, requested_by, status,
               attempts, lease_until, claimed_by)
               VALUES (%s,'tencent','verify',%s,%s,%s,%s) RETURNING id""",
            (SRC, status, attempts, lease_until, claimed_by),
        )
        return cur.fetchone()[0]


def main() -> int:
    url = os.environ.get("FD_CRAWL_DB_URL") or (
        "postgresql://fd:28uxsi3mQyYUOqmf1XMbp9iZ@100.64.0.3:30432/fd_open_data")
    os.environ["FD_CRAWL_DB_URL"] = url  # writer helpers read the env, not args
    ok = True
    conn = dispatch.connect(url)
    conn.autocommit = True  # never hold row locks across sections
    dispatch.ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM pending_runs WHERE requested_by='verify'")
        cur.execute("DELETE FROM crawl_runs WHERE source=%s AND kind='runtime'",
                    (SRC,))
    conn.commit()

    # -- 3.1a atomic claim: two racers, one winner --------------------------
    pid = dispatch.queue_run(conn, SRC, requested_by="verify",
                             known_sources={SRC})
    c1, c2 = dispatch.connect(url), dispatch.connect(url)
    out = []

    def racer(c, tag):
        r = dispatch.claim_next(c, "tencent", f"verify-{tag}")
        out.append((tag, r))

    t1 = threading.Thread(target=racer, args=(c1, "a"))
    t2 = threading.Thread(target=racer, args=(c2, "b"))
    t1.start(); t2.start(); t1.join(); t2.join()
    winners = [r for _, r in out if r]
    ok &= check("claim race: exactly one winner",
                len(winners) == 1 and winners[0]["id"] == pid)
    c1.close(); c2.close()

    # -- 3.1b lease expiry requeues; attempts cap fails ---------------------
    pid2 = fresh_pending(conn, status="claimed", attempts=1,
                         lease_until="2000-01-01", claimed_by="verify-dead")
    requeued = dispatch.expire_leases(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM pending_runs WHERE id=%s", (pid2,))
        ok &= check("expired lease requeued (attempts < max)",
                    requeued >= 1 and cur.fetchone()[0] == "pending")
    pid3 = fresh_pending(conn, status="claimed", attempts=2,
                         lease_until="2000-01-01", claimed_by="verify-dead")
    dispatch.expire_leases(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT status, error_head FROM pending_runs WHERE id=%s", (pid3,))
        row = cur.fetchone()
        ok &= check("attempts cap -> failed", row[0] == "failed"
                    and "exhausted" in row[1])

    # -- 3.2 full chain via the real dispatcher + runner --------------------
    CONTENT.mkdir(parents=True, exist_ok=True)
    (CONTENT / "spider.py").write_text(
        "def run_fake_ok(limit=100):\n"
        "    return [{'hello': 'world', 'n': i} for i in range(min(limit, 3))]\n"
    )
    pid4 = dispatch.queue_run(conn, SRC, requested_by="verify",
                              known_sources={SRC})
    env = {**os.environ, "FD_CRAWL_DB_URL": url,
           "FD_CONTENT_DIR": str(CONTENT.parent),
           "FD_IMAGE_TAG": "verify", "FD_DISPATCH_MAX_RUNS": "3"}
    proc = subprocess.run(
        [sys.executable, "-m", "fd_industry_data.dispatcher_cli"],
        env=env, cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True, text=True, timeout=180)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, run_id FROM pending_runs WHERE id IN (%s,%s)",
            (pid, pid4))
        rows = cur.fetchall()
        cur.execute(
            "SELECT id, status, rows_written, pending_run_id FROM crawl_runs "
            "WHERE source=%s AND kind='runtime' ORDER BY id DESC LIMIT 2",
            (SRC,))
        runs = cur.fetchall()
    ok &= check("dispatcher exit 0", proc.returncode == 0)
    ok &= check("both pending rows closed", all(r[0] != "pending" for r in rows))
    # the first pending row was claimed by the race test then executed too
    ok &= check("run row linked via pending_run_id",
                any(r[3] in (pid, pid4) and r[1] == "success" and r[2] == 3
                    for r in runs))
    if not ok:
        print("--- dispatcher stdout ---\n" + proc.stdout)
        print("--- dispatcher stderr ---\n" + proc.stderr)

    # -- 3.3a single-flight: open run makes dispatcher skip ------------------
    open_id = writer.start_run(source=SRC, commit_sha="verify",
                               image_tag="verify")
    pid5 = fresh_pending(conn)
    subprocess.run(
        [sys.executable, "-m", "fd_industry_data.dispatcher_cli"],
        env=env, cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True, text=True, timeout=120)
    with conn.cursor() as cur:
        cur.execute("SELECT status, error_head FROM pending_runs WHERE id=%s", (pid5,))
        row = cur.fetchone()
        ok &= check("open run -> pending skipped with reason",
                    row[0] == "failed" and "single-flight" in row[1])

    # -- 3.3b cancel flag round-trip -----------------------------------------
    writer.request_cancel(open_id)
    ok &= check("cancel flag visible", writer.cancel_requested(open_id))
    writer.finish_run(open_id, status="cancelled", finished_at=time.time(),
                      rows_written=0)

    # -- cleanup --------------------------------------------------------------
    with conn.cursor() as cur:
        cur.execute("DELETE FROM crawl_items WHERE run_id IN "
                    "(SELECT id FROM crawl_runs WHERE source=%s)", (SRC,))
        cur.execute("DELETE FROM crawl_runs WHERE source=%s", (SRC,))
        cur.execute("DELETE FROM pending_runs WHERE requested_by='verify'")
        cur.execute("SELECT count(*) FROM pending_runs WHERE source=%s", (SRC,))
        ok &= check("cleanup complete", cur.fetchone()[0] == 0)
    conn.commit()
    conn.close()
    print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
