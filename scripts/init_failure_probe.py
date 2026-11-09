#!/usr/bin/env python3
"""Init-container failures must be visible (harden-crawl-delivery 8.2).

On 2026-11-09 a dead gitee token made every CronJob's ``checkout`` initContainer
fail for ~3 hours. ``crawl_runs`` had **zero** records for it, because the report
contract lives inside the runner and the runner never started. The estate looked
idle, not broken.

This probe closes that hole: it reads the cluster's Job/Pod state and synthesizes
a ``failed`` run per affected source, so the existing health loop can see it.
It is deliberately a **separate, read-mostly** component — it needs the cluster
API, which runners do not have.

Design decisions that matter:

- **One record per (source, job)**: a re-run of this probe must not inflate the
  table. ``--dedupe-window`` skips a source that already has a run starting
  around the Job's start.
- **error_head stays one line** (the DB constraint the ledger keeps re-learning)
  and names the cause class: ``init-crash exit=128 (credential)``.
- **kind="runtime"** so the source's own health math applies; the error class
  explains it was platform-wide, not the source's fault.
- **Dry-run by default**; ``--apply`` writes.

Usage:
    python3 scripts/init_failure_probe.py                 # dry run, prints
    python3 scripts/init_failure_probe.py --apply
    python3 scripts/init_failure_probe.py --since-minutes 60 --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

NAMESPACE = "fd-industry"
CONTEXT = "tencent"
JOB_PREFIX = "fd-industry-"

#: exit code → 一句话原因（git 认证是 128，OOM/kill 是 137）。
_EXIT_CAUSE = {128: "credential", 137: "oom-or-kill", 143: "terminated"}

#: 判定「这条失败还没被记录」的时间容差：Job 起始前后这个窗口内已有该源的运行
#: 记录，就认为已经有人报到了。
DEDUPE_WINDOW = timedelta(minutes=15)


def known_sources(repo: Path = REPO) -> list[str]:
    """Source slugs, longest first — job names are ``fd-industry-<src>-<jobid>``
    and slugs themselves contain hyphens, so longest-match is the only
    unambiguous way to split them."""
    names = [p.parent.name for p in sorted((repo / "spiders").glob("*/manifest.yaml"))]
    return sorted(names, key=len, reverse=True)


def source_from_job(job_name: str, sources: list[str]) -> str | None:
    if not job_name.startswith(JOB_PREFIX):
        return None
    rest = job_name[len(JOB_PREFIX):]
    for slug in sources:
        if rest == slug or rest.startswith(slug + "-"):
            return slug
    # dispatcher / federation-observer 这类平台组件不是 spiders 下的源
    return None


def _kubectl(args: list[str], timeout: int = 60) -> str:
    proc = subprocess.run(["kubectl", "--context", CONTEXT, "-n", NAMESPACE] + args,
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"kubectl failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def failed_pods(since_minutes: int) -> list[dict]:
    """Pods whose init container terminated non-zero, within the window."""
    raw = _kubectl(["get", "pods", "-o", "json"])
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    out: list[dict] = []
    for item in json.loads(raw).get("items", []):
        created = item["metadata"].get("creationTimestamp")
        try:
            ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if ts < cutoff:
            continue
        for status in (item["status"].get("initContainerStatuses") or []):
            terminated = (status.get("state") or {}).get("terminated") or {}
            code = terminated.get("exitCode")
            if code in (None, 0):
                continue
            owner = item["metadata"].get("ownerReferences") or [{}]
            out.append({
                "pod": item["metadata"]["name"],
                "job": (owner[0].get("name") or ""),
                "created": created,
                "exit_code": code,
                "reason": terminated.get("reason") or "",
            })
            break
    return out


def existing_run_starts(dsn: str, sources: list[str]) -> dict[str, list[datetime]]:
    """{source: [started_at…]} so we can skip already-reported failures."""
    if not dsn:
        return {}
    listing = ",".join(f"'{s}'" for s in sources) or "''"
    sql = ("select source, started_at from crawl_runs "
           f"where source in ({listing}) and started_at > now() - interval '2 days'")
    proc = subprocess.run(["psql", dsn, "-At", "-F", "\t", "-c", sql],
                          capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()[:300]}")
    out: dict[str, list[datetime]] = {}
    for line in proc.stdout.splitlines():
        if "\t" not in line:
            continue
        source, ts = line.split("\t", 1)
        try:
            parsed = datetime.fromisoformat(ts.strip().replace(" ", "T"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        out.setdefault(source.strip(), []).append(parsed)
    return out


def plan(failures: list[dict], sources: list[str],
         recorded: dict[str, list[datetime]],
         window: timedelta = DEDUPE_WINDOW) -> list[dict]:
    """Collapse pods into one record per (source, job), skipping reported ones.

    Several pods can belong to one Job (retries); several Jobs can belong to one
    source. The row we want is per source per Job — enough to be visible, not
    enough to flood the table with retry noise.
    """
    seen: set[tuple[str, str]] = set()
    rows: list[dict] = []
    for failure in sorted(failures, key=lambda f: f["created"]):
        source = source_from_job(failure["job"], sources)
        if source is None:
            continue
        key = (source, failure["job"])
        if key in seen:
            continue
        seen.add(key)
        try:
            job_start = datetime.fromisoformat(failure["created"].replace("Z", "+00:00"))
        except ValueError:
            job_start = datetime.now(timezone.utc)
        if any(abs(existing - job_start) <= window
               for existing in recorded.get(source, [])):
            continue
        cause = _EXIT_CAUSE.get(failure["exit_code"], "unknown")
        rows.append({
            "source": source,
            "job": failure["job"],
            "pod": failure["pod"],
            "exit_code": failure["exit_code"],
            "cause": cause,
            "started_at": job_start,
            # 单行，含 exit code 与原因类别（DB 的 error_head 不允许换行）
            "error_head": (f"init-crash exit={failure['exit_code']} ({cause}): "
                           f"initContainer {failure['job']} failed before the runner ran"),
        })
    return rows


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since-minutes", type=int, default=120)
    ap.add_argument("--dsn", default=os.environ.get("FD_CRAWL_DB_URL", ""))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv[1:])

    sources = known_sources()
    failures = failed_pods(args.since_minutes)
    recorded = existing_run_starts(args.dsn, sources) if args.dsn else {}
    rows = plan(failures, sources, recorded)

    if args.json:
        print(json.dumps({"failed_pods": len(failures),
                          "to_record": [{**r, "started_at": r["started_at"].isoformat()}
                                        for r in rows]},
                         ensure_ascii=False, indent=2))
    else:
        print(f"init-failure probe: {len(failures)} failed pod(s) in "
              f"{args.since_minutes}min, {len(rows)} run(s) to synthesize")
        for r in rows:
            print(f"  {r['source']:<26} {r['error_head'][:70]}")

    if not args.apply:
        print("(dry run; --apply writes crawl_runs rows)")
        return 0
    if not args.dsn:
        print("init_failure_probe: --apply needs --dsn or FD_CRAWL_DB_URL", file=sys.stderr)
        return 2

    from fd_industry_data.writer import report_run

    written = 0
    for r in rows:
        ok = report_run(
            source=r["source"], kind="runtime", status="failed",
            started_at=r["started_at"].timestamp(),
            finished_at=r["started_at"].timestamp(), rows_written=0,
            error_head=r["error_head"], commit_sha="", image_tag="unknown",
            items=[], metrics={"error_class": "platform-init",
                               "job": r["job"], "exit_code": r["exit_code"]},
        )
        written += 1 if ok else 0
    print(f"written: {written}/{len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
