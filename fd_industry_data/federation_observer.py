"""Federation observer: mirror finished Jobs of legacy lines into crawl_runs.

Runs on the cluster that hosts the legacy CronJobs (xinru-master, scraw ns).
Reads the k8s API with the pod's service account (stdlib urllib only), and
for every finished Job belonging to a watched CronJob writes one crawl_runs
row (kind='federation'). The watermark is max(finished_at) per source, so
the observer is idempotent across ticks and back-fills retained history on
first run. It never touches the legacy workloads themselves — joining the
platform is read-only for the legacy line (crawl-platform design D6).

Usage (CronJob entrypoint): python3 -m fd_industry_data.federation_observer
Env: FED_NAMESPACE (default scraw), FD_CRAWL_DB_URL.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.request

from . import dispatch


def _api(path: str) -> dict:
    """GET a k8s API path using the mounted service account."""
    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
    with open("/var/run/secrets/kubernetes.io/serviceaccount/token") as f:
        token = f.read().strip()
    with open("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt", "rb") as f:
        ctx = ssl.create_default_context(cadata=f.read().decode())
    req = urllib.request.Request(
        f"https://{host}:{port}{path}",
        headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
        return json.load(r)


def cronjob_names(ns: str) -> list[str]:
    items = _api(f"/apis/batch/v1/namespaces/{ns}/cronjobs").get("items", [])
    return sorted(i["metadata"]["name"] for i in items)


def source_for(job: dict, cronjobs: list[str]) -> str | None:
    """Legacy Job -> source name: CronJob owner first, else longest name prefix."""
    meta = job.get("metadata", {})
    for owner in meta.get("ownerReferences", []) or []:
        if owner.get("kind") == "CronJob" and owner.get("name") in cronjobs:
            return owner["name"]
    name = meta.get("name", "")
    matches = [c for c in cronjobs if name == c or name.startswith(c + "-")]
    return max(matches, key=len) if matches else None


def finished_at(job: dict) -> str | None:
    # batch/v1 Job uses `completionTime`; `completionTimestamp` is the
    # CronJob field — mixing them up silently reports nothing.
    return ((job.get("status") or {}).get("completionTime")
            or (job.get("status") or {}).get("startTime"))


def job_outcome(job: dict) -> tuple[str, str | None]:
    """('success'|'failed', error_head) from the Job's conditions."""
    conds = (job.get("status") or {}).get("conditions") or []
    for c in conds:
        if c.get("type") == "Failed":
            head = f"{c.get('reason', 'Failed')}: {c.get('message', '')}"[:200]
            return "failed", head
    return "success", None


def _parse_ts(ts: str):
    """RFC3339 (k8s, 'Z' suffix) or None — py<3.11 isoformat has no Z."""
    from datetime import datetime

    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def observe(ns: str) -> int:
    cronjobs = cronjob_names(ns)
    if not cronjobs:
        print(f"federation_observer: no cronjobs in {ns}; nothing to do")
        return 0
    jobs = _api(f"/apis/batch/v1/namespaces/{ns}/jobs").get("items", [])

    conn = dispatch.connect()
    reported = skipped = 0
    for job in sorted(jobs, key=finished_at):
        src = source_for(job, cronjobs)
        ts = finished_at(job)
        if not src or not ts:
            continue
        status = job.get("status", {})
        if status.get("completionTime") is None:
            continue  # only finished Jobs report
        with conn.cursor() as cur:
            cur.execute(
                "SELECT extract(epoch FROM coalesce(max(finished_at), to_timestamp(0))) "
                "FROM crawl_runs WHERE source=%s AND kind='federation'", (src,))
            watermark = cur.fetchone()[0]
        if _parse_ts(ts).timestamp() <= watermark:
            skipped += 1
            continue
        outcome, error_head = job_outcome(job)
        started = status.get("startTime") or ts
        with conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO crawl_runs
                   (source, kind, status, started_at, finished_at, rows_written,
                    error_head, commit_sha, image_tag)
                   VALUES (%s, 'federation', %s, %s, %s, 0, %s, NULL, %s)""",
                (src, outcome, started, ts, error_head,
                 job["spec"]["template"]["spec"]["containers"][0]["image"][:60]
                 if job.get("spec", {}).get("template", {}).get("spec", {}).get("containers") else None),
            )
        reported += 1
        print(f"federation_observer: {src} {outcome} finished {ts}")
    conn.close()
    print(f"federation_observer: {reported} reported, {skipped} already known "
          f"({len(cronjobs)} cronjobs in {ns})")
    return 0


def main() -> int:
    ns = os.environ.get("FED_NAMESPACE", "scraw")
    return observe(ns)


if __name__ == "__main__":
    sys.exit(main())
