"""fd-dispatcher: site-local pull loop for pending exception runs.

Runs as a CronJob on each site (tencent first). Per tick: expire stale
leases, then repeatedly claim this site's oldest pending row and execute
it with the same fd-runner entry the scheduled CronJobs use — the runner
reports its own crawl_runs row (linked via FD_PENDING_RUN_ID) and enforces
the cross-mechanism single-flight guard and cooperative cancel itself.

Two execution forms (legal-line-federation 3.1/3.2):
- platform-native sources (kind='platform', manifest-driven): the original
  same-container subprocess running runner_cli — unchanged behavior;
- registered federated members (kind='federated' with a runner declaration
  in crawl_sources: runner_image / runner_command / timeout_seconds /
  runner_env_from): a
  batch/v1 Job created in the local cluster via the in-cluster service
  account (stdlib urllib, same transport as federation_observer). The
  Job's image, command and deadline come from the declaration; the
  dispatcher gates creation on cluster-side single-flight (an active Job
  of the same source -> skipped/failed, same contract as the DB-level
  guard) and honors crawl_runs.cancel_requested by deleting the Job and
  writing the cancel outcome.

Usage (container entrypoint): python3 -m fd_industry_data.dispatcher_cli
Env: FD_DISPATCH_SITE (default tencent), FD_DISPATCH_MAX_RUNS (default 5),
     FD_CRAWL_DB_URL, FD_CONTENT_DIR, FD_CONTENT_COMMIT, FD_IMAGE_TAG,
     FD_DISPATCH_NAMESPACE (target ns for federated Jobs; default the
     pod's own namespace, else scraw), FD_DISPATCH_POLL_SECONDS (Job
     poll interval, default 10).
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import dispatch

_SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"


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


# ---------------------------------------------------------------------------
# k8s execution branch for registered federated members (tasks 3.1/3.2).
# Same in-cluster transport as federation_observer._api — zero new deps.
# ---------------------------------------------------------------------------

def source_runner(conn, src: str) -> dict | None:
    """Runner declaration for a source: kind + image/command/timeout/secrets.

    Reads the registration-seed columns of crawl_sources (kind defaults to
    'platform'; runner_env_from lists secret names for envFrom, NULL = none).
    A not-yet-migrated DB degrades to the platform path so the dispatcher
    image may roll before the alembic migration lands.
    """
    with conn.cursor() as cur:
        try:
            cur.execute(
                "SELECT kind, runner_image, runner_command, timeout_seconds, "
                "runner_env_from FROM crawl_sources WHERE source=%s", (src,))
        except Exception:  # noqa: BLE001 - pre-0007 schema: platform path
            conn.rollback()
            return None
        row = cur.fetchone()
    if row is None:
        return None
    return {"kind": row[0] or "platform", "runner_image": row[1],
            "runner_command": row[2], "timeout_seconds": row[3],
            "runner_env_from": row[4]}


def uses_k8s_branch(decl: dict | None) -> bool:
    """Federated member with a full runner declaration -> in-cluster Job.

    Anything else (platform-native sources, federated rows whose runner
    spec has not landed) keeps the unified runner subprocess path.
    """
    return bool(decl and decl.get("kind") == "federated"
                and decl.get("runner_image") and decl.get("runner_command"))


def _in_cluster_ns() -> str:
    try:
        with open(f"{_SA_DIR}/namespace") as f:
            return f.read().strip() or "scraw"
    except OSError:
        return "scraw"


def _k8s_api(method: str, path: str, body: dict | None = None) -> dict:
    """In-cluster Kubernetes REST call with the mounted service account."""
    import ssl

    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
    with open(f"{_SA_DIR}/token") as f:
        token = f.read().strip()
    with open(f"{_SA_DIR}/ca.crt", "rb") as f:
        ctx = ssl.create_default_context(cadata=f.read().decode())
    req = urllib.request.Request(
        f"https://{host}:{port}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
        return json.load(r)


def list_jobs(ns: str) -> list:
    return _k8s_api("GET", f"/apis/batch/v1/namespaces/{ns}/jobs").get("items", [])


def create_job(ns: str, body: dict) -> dict:
    return _k8s_api("POST", f"/apis/batch/v1/namespaces/{ns}/jobs", body)


def get_job(ns: str, name: str) -> dict:
    return _k8s_api("GET", f"/apis/batch/v1/namespaces/{ns}/jobs/{name}")


def delete_job(ns: str, name: str) -> None:
    try:
        _k8s_api("DELETE", f"/apis/batch/v1/namespaces/{ns}/jobs/{name}")
    except urllib.error.HTTPError as e:
        if e.code != 404:  # already gone: cancel achieved
            raise


def job_state(job: dict) -> tuple[str, str | None]:
    """('active'|'success'|'failed', error_head) from the Job's conditions."""
    conds = (job.get("status") or {}).get("conditions") or []
    for c in conds:
        if c.get("type") == "Failed":
            head = f"{c.get('reason', 'Failed')}: {c.get('message', '')}"[:200]
            return "failed", head
        if c.get("type") == "Complete":
            return "success", None
    return "active", None


def active_job_for(jobs: list, src: str) -> dict | None:
    """Active Job of `src` in the namespace, if any (single-flight gate).

    Matches both chart-scheduled Jobs (CronJob owner reference) and
    dispatcher-created Jobs (<source>-<pending id> naming).
    """
    from .federation_observer import source_for

    for job in jobs:
        if source_for(job, [src]) == src and job_state(job)[0] == "active":
            return job
    return None


def job_body(src: str, pending_id: int, decl: dict, ns: str) -> dict:
    """batch/v1 Job for one declared federated run (<source>-<pending id>).

    The declaration owns image/command/deadline; each runner_env_from secret
    name becomes a per-secret envFrom secretRef on the container (crawl
    credentials such as RustFS that the platform subprocess form gets from
    the dispatcher env). Resource limits are the registration hard gate
    (industry-crawl-gitops exemption precondition) and default to the
    law-line profile. The Job is labeled as dispatcher-managed so the
    federation observer's mirror stays exclusive with the runner's direct
    report.
    """
    name = f"{src}-{pending_id}"
    labels = {"fd-industry/component": "dispatcher-run",
              "fd-industry/source": src[:63],
              "fd-industry/pending-run": str(pending_id),
              "fd-industry/managed-by": "fd-dispatcher"}
    env = [{"name": "PYTHONUNBUFFERED", "value": "1"},
           {"name": "FD_PENDING_RUN_ID", "value": str(pending_id)},
           {"name": "FD_SCHEMA_MANAGED", "value": "1"}]
    db_url = os.environ.get("FD_CRAWL_DB_URL", "")
    if db_url:
        env.append({"name": "FD_CRAWL_DB_URL", "value": db_url})
    env_from = [{"secretRef": {"name": s}}
                for s in (decl.get("runner_env_from") or [])
                if isinstance(s, str) and s.strip()]
    container = {
        "name": "run",
        "image": decl["runner_image"],
        "imagePullPolicy": "IfNotPresent",
        "command": decl["runner_command"],
        "env": env,
        "resources": {
            "requests": {"memory": "256Mi", "cpu": "100m"},
            "limits": {"memory": "2Gi", "cpu": "1"},
        },
    }
    if env_from:
        container["envFrom"] = env_from
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": ns, "labels": labels},
        "spec": {
            "activeDeadlineSeconds": int(decl.get("timeout_seconds") or 3600),
            "backoffLimit": 0,  # one pod attempt; retries belong to the pending row
            "ttlSecondsAfterFinished": 86400,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [container],
                },
            },
        },
    }


def _run_and_cancel(conn, pending_id: int):
    """Linked crawl_runs row (id, status) plus its cancel flag, if reported."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, status, cancel_requested IS NOT NULL FROM crawl_runs "
            "WHERE pending_run_id=%s ORDER BY id DESC LIMIT 1",
            (pending_id,),
        )
        row = cur.fetchone()
        return (row[0], row[1], bool(row[2])) if row else (None, None, False)


def execute_k8s(conn, row: dict, src: str, decl: dict, ns: str, *,
                poll_seconds: int | None = None) -> None:
    """Claim-to-terminal lifecycle for one federated pending row.

    Gate -> create -> poll -> finish: refuse when the source already has an
    active Job in the namespace (cluster-side single-flight, same contract
    as the DB-level guard), create the declared Job (adopting a same-name
    Job left by a prior lapsed attempt), then poll until terminal — closing
    the pending row from the linked crawl_runs report, or deleting the Job
    and writing the cancel outcome when cancel_requested appears.
    """
    poll_seconds = int(poll_seconds
                       or os.environ.get("FD_DISPATCH_POLL_SECONDS", "10"))
    busy = active_job_for(list_jobs(ns), src)
    if busy is not None:
        dispatch.finish_pending(
            conn, row["id"], run_id=None, status="failed",
            error_head=f"skipped: active job in ns {ns} (single-flight)")
        print(f"fd-dispatcher: skipped #{row['id']} {src}, "
              f"active job in {ns} (single-flight)")
        return

    body = job_body(src, row["id"], decl, ns)
    name = body["metadata"]["name"]
    try:
        create_job(ns, body)
    except urllib.error.HTTPError as e:
        if e.code != 409:  # Job of a lapsed prior attempt: adopt and poll it
            raise
        print(f"fd-dispatcher: adopting existing job {name}")

    while True:
        run_id, run_status, cancel = _run_and_cancel(conn, row["id"])
        if cancel:
            delete_job(ns, name)
            dispatch.finish_pending(conn, row["id"], run_id=run_id,
                                    status="cancelled", error_head=None)
            print(f"fd-dispatcher: #{row['id']} {src} cancelled, job {name} deleted")
            return
        state, head = job_state(get_job(ns, name))
        if state != "active":
            run_id, run_status = _lookup_run(conn, row["id"])
            if run_status == "success":
                pending_status = "done"
            elif run_status == "cancelled":
                pending_status = "cancelled"
            elif state == "success":
                pending_status = "done"  # terminal Job state when no report linked
            else:
                pending_status = "failed"
            error_head = None
            if pending_status == "failed":
                error_head = head or f"job {state} without runner report"
            dispatch.finish_pending(conn, row["id"], run_id=run_id,
                                    status=pending_status, error_head=error_head)
            print(f"fd-dispatcher: #{row['id']} {src} -> {pending_status} "
                  f"(job {name}, crawl_runs #{run_id})")
            return
        time.sleep(poll_seconds)


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
    from .sites import load_sites
    if load_sites().get(site, {}).get("kind") == "docker":
        due = dispatch.enqueue_due(conn, site)
        if due:
            print(f"fd-dispatcher: schedule enqueued {len(due)} due run(s): "
                  + ", ".join(due))

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

        decl = source_runner(conn, src)
        if uses_k8s_branch(decl):
            # Federated member: create the declared Job in the local cluster
            # instead of the subprocess form (no auth-pool leasing — declared
            # images carry their own identity).
            ns = os.environ.get("FD_DISPATCH_NAMESPACE") or _in_cluster_ns()
            try:
                execute_k8s(conn, row, src, decl, ns)
            except Exception as e:  # noqa: BLE001 - one bad row must not kill the loop
                dispatch.finish_pending(conn, row["id"], run_id=None, status="failed",
                               error_head=f"dispatcher k8s error: {e}")
                print(f"fd-dispatcher: #{row['id']} {src} dispatcher k8s error: {e}",
                      file=sys.stderr)
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
