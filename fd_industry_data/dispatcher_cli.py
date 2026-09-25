"""fd-dispatcher: site-local pull loop for pending exception runs.

Runs as a CronJob on each site (tencent first). Per tick: expire stale
leases, then repeatedly claim this site's oldest pending row and execute
it with the same fd-runner entry the scheduled CronJobs use — the runner
reports its own crawl_runs row (linked via FD_PENDING_RUN_ID) and enforces
the cross-mechanism single-flight guard and cooperative cancel itself.

Usage (container entrypoint): python3 -m fd_industry_data.dispatcher_cli
Env: FD_DISPATCH_SITE (default tencent), FD_DISPATCH_MAX_RUNS (default 5),
     FD_CRAWL_DB_URL, FD_CONTENT_DIR, FD_CONTENT_COMMIT, FD_IMAGE_TAG.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys

from . import dispatch


def _lookup_run(conn, pending_id: int):
    """The crawl_runs row a completed execution wrote for this pending id."""
    with conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, status FROM crawl_runs WHERE pending_run_id=%s "
            "ORDER BY id DESC LIMIT 1",
            (pending_id,),
        )
        row = cur.fetchone()
        return (row[0], row[1]) if row else (None, None)


def main() -> int:
    site = os.environ.get("FD_DISPATCH_SITE", "tencent")
    max_runs = int(os.environ.get("FD_DISPATCH_MAX_RUNS", "5"))
    claimed_by = f"{socket.gethostname()}-dispatch"

    conn = dispatch.connect()
    dispatch.ensure_schema(conn)
    content_dir = os.environ.get("FD_CONTENT_DIR") or os.path.join(
        os.getcwd(), "spiders")
    if os.path.isdir(content_dir):
        n = dispatch.sync_sources(conn, content_dir)
        print(f"fd-dispatcher: inventory synced, {n} source(s)")
    expired = dispatch.expire_leases(conn)
    if expired:
        print(f"fd-dispatcher: expired {expired} stale lease(s)")
    dispatch.heartbeat_site(conn, site)

    done = 0
    while done < max_runs:
        row = dispatch.claim_next(conn, site, claimed_by)
        if row is None:
            break
        src, params = row["source"], row["params"] or {}
        limit = params.get("limit") or 100
        print(f"fd-dispatcher: claimed #{row['id']} {src} (attempt {row['attempts']})")

        open_row = None
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM crawl_runs WHERE source=%s AND status='running' "
                "ORDER BY id DESC LIMIT 1",
                (src,),
            )
            open_row = cur.fetchone()

        if open_row:
            dispatch.finish_pending(conn, row["id"], run_id=open_row[0], status="failed",
                           error_head=f"skipped: run #{open_row[0]} still open (single-flight)")
            print(f"fd-dispatcher: skipped #{row['id']} {src}, run #{open_row[0]} open")
            done += 1
            continue

        env = {**os.environ, "FD_PENDING_RUN_ID": str(row["id"])}
        cmd = [sys.executable, "-m", "fd_industry_data.runner_cli", src,
               "--limit", str(limit)]
        try:
            proc = subprocess.run(cmd, env=env)
            run_id, run_status = _lookup_run(conn, row["id"])
            pending_status = {"success": "done", "cancelled": "cancelled"}.get(
                run_status, "failed")
            dispatch.finish_pending(conn, row["id"], run_id=run_id, status=pending_status,
                           error_head=None if run_status in ("success", "cancelled")
                           else f"runner exit {proc.returncode}")
            print(f"fd-dispatcher: #{row['id']} {src} -> {pending_status} "
                  f"(crawl_runs #{run_id})")
        except Exception as e:  # noqa: BLE001 - one bad row must not kill the loop
            dispatch.finish_pending(conn, row["id"], run_id=None, status="failed",
                           error_head=f"dispatcher error: {e}")
            print(f"fd-dispatcher: #{row['id']} {src} dispatcher error: {e}",
                  file=sys.stderr)
        done += 1

    dispatch.heartbeat_site(conn, site)
    conn.close()
    print(f"fd-dispatcher: {site} tick complete, {done} run(s) handled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
