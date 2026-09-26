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

import json
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
    from . import auth as _auth
    _auth.expire_leases(conn)
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

        ident = None
        jar_path = None
        with conn.cursor() as cur:
            cur.execute("SELECT auth_profile FROM crawl_sources WHERE source=%s", (src,))
            prof = cur.fetchone()
        if prof and prof[0]:
            ident = _auth.lease_identity(conn, src, claimed_by)
            if ident is None:
                dispatch.finish_pending(
                    conn, row["id"], run_id=None, status="failed",
                    error_head="auth pool dry: no active unleased identity")
                print(f"fd-dispatcher: skipped #{row['id']} {src}, auth pool dry")
                done += 1
                continue
            try:
                jar = _auth.fetch_jar(ident["session_ref"]) if ident["session_ref"] else {}
                jar_path = f"/tmp/session-{ident['account_alias']}.json"
                with open(jar_path, "w") as f:
                    json.dump(jar, f)
                env["FD_ACCOUNT"] = ident["account_alias"]
                env["FD_SESSION_JAR_PATH"] = jar_path
                with conn.cursor() as cur:
                    cur.execute("SELECT egress_ref FROM crawl_identities WHERE id=%s",
                                (ident["id"],))
                    erow = cur.fetchone()
                egress = _auth.resolve_egress(conn, erow[0] if erow else None)
                if egress:
                    for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
                        env[k] = egress["proxy_url"]
                    env["FD_EGRESS_REF"] = erow[0]
                print(f"fd-dispatcher: leased identity '{ident['account_alias']}' "
                      f"for {src}" + (f" via {erow[0]}" if egress else ""))
            except Exception as e:  # noqa: BLE001 - jar problems free the lease
                _auth.release_identity(conn, ident["id"], ident["lease_token"],
                                       success=False)
                ident = None
                print(f"fd-dispatcher: session jar unavailable for {src}: {e}",
                      file=sys.stderr)

        try:
            proc = subprocess.run(cmd, env=env)
            run_id, run_status = _lookup_run(conn, row["id"])
            pending_status = {"success": "done", "cancelled": "cancelled"}.get(
                run_status, "failed")
            dispatch.finish_pending(conn, row["id"], run_id=run_id, status=pending_status,
                           error_head=None if run_status in ("success", "cancelled")
                           else f"runner exit {proc.returncode}")
            if ident is not None:
                ok = run_status == "success"
                _auth.release_identity(conn, ident["id"], ident["lease_token"],
                                       success=ok)
                if run_id is not None:
                    with conn.cursor() as cur:
                        cur.execute("SELECT rows_written FROM crawl_runs WHERE id=%s",
                                    (run_id,))
                        r = cur.fetchone()
                    _auth.record_run_outcome(conn, ident["id"],
                                             r[0] if r else 0)
            if jar_path:
                try:
                    os.unlink(jar_path)
                except OSError:
                    pass
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
