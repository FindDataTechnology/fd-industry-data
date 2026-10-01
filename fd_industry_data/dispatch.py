"""Federated dispatch library (crawl-platform).

Owns the central control tables in the same schema as crawl_runs
(fd_open_data @ central PG, public schema):

- crawl_sites      — run-site registry (seeded from fd_industry_data/sites.yaml)
- pending_runs     — pull-based exception triggers; dispatchers claim atomically
- crawl_runs.cancel_requested — graceful-cancel flag checked by the runner

`queue_run` is the single insert path for exception runs (trigger-now,
backfill, temporary override): it validates the site against the registry
and rejects sources missing from the caller's inventory. Reporting
failures never break the crawl; queue failures raise to the caller.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .sites import DEFAULT_SITE, load_sites

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS crawl_sites (
    id           text PRIMARY KEY,
    description  text NOT NULL DEFAULT '',
    kind         text NOT NULL DEFAULT 'docker',
    enabled      boolean NOT NULL DEFAULT true,
    last_seen_at timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pending_runs (
    id           bigserial PRIMARY KEY,
    source       text NOT NULL,
    site         text NOT NULL DEFAULT 'tencent'
                 REFERENCES crawl_sites (id),
    params       jsonb NOT NULL DEFAULT '{}'::jsonb,
    requested_by text NOT NULL DEFAULT 'console',
    status       text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'claimed', 'done',
                                   'failed', 'cancelled')),
    claimed_by   text,
    claimed_at   timestamptz,
    lease_until  timestamptz,
    attempts     integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 2,
    run_id       bigint,
    error_head   text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz
);
CREATE INDEX IF NOT EXISTS pending_runs_site_idx
    ON pending_runs (site, status, created_at);

ALTER TABLE crawl_runs ADD COLUMN IF NOT EXISTS cancel_requested timestamptz;
ALTER TABLE crawl_runs ADD COLUMN IF NOT EXISTS pending_run_id bigint;
ALTER TABLE crawl_runs ALTER COLUMN finished_at DROP NOT NULL;

CREATE TABLE IF NOT EXISTS crawl_sources (
    source     text PRIMARY KEY,
    site       text REFERENCES crawl_sites (id),
    schedule   text,
    enabled    boolean NOT NULL DEFAULT true,
    last_commit text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
"""

SEED_SITE = """
INSERT INTO crawl_sites (id, description, kind)
VALUES (%s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    description = EXCLUDED.description,
    kind = EXCLUDED.kind;
"""


def connect(url: str | None = None):
    """psycopg2 connection to the central ops DB (FD_CRAWL_DB_URL by default)."""
    import psycopg2

    url = url or os.environ.get("FD_CRAWL_DB_URL", "")
    url = url.replace("postgresql+psycopg2://", "postgresql://")
    if not url:
        raise RuntimeError("FD_CRAWL_DB_URL not set")
    return psycopg2.connect(url, connect_timeout=8)


def ensure_schema(conn) -> None:
    """Idempotent DDL + seed sites from the packaged registry (same style as writer)."""
    if os.environ.get("FD_SCHEMA_MANAGED", "") == "1":
        return  # deploy-managed schema; the runtime role has DML rights only
    sites = [
        (s["id"], s.get("description", ""), s.get("kind", "docker"))
        for s in load_sites().values()
    ]
    with conn, conn.cursor() as cur:
        cur.execute(SCHEMA_DDL)
        for row in sorted(sites):
            cur.execute(SEED_SITE, row)


def queue_run(conn, source: str, *, site: str = DEFAULT_SITE,
              params: dict[str, Any] | None = None,
              requested_by: str = "console",
              known_sources: Iterable[str] | None = None) -> int:
    """Insert one pending exception run; returns its id.

    Single insert path for every trigger source (console, MCP, dispatcher
    helpers). Rejects unregistered sites (registry + FK) and sources that
    are missing from the caller's inventory when one is provided.
    """
    sites = load_sites()
    if site not in sites:
        raise ValueError(
            f"site {site!r} is not registered; known sites: "
            + ", ".join(sorted(sites))
        )
    if known_sources is not None and source not in known_sources:
        raise ValueError(f"source {source!r} is not registered in the source inventory")

    import json

    with conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO pending_runs (source, site, params, requested_by)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (source, site, json.dumps(params or {}, ensure_ascii=False), requested_by),
        )
        return cur.fetchone()[0]


def claim_next(conn, site: str, claimed_by: str, lease_seconds: int = 1800):
    """Atomically claim the oldest pending row for a site; None when idle.

    Uses FOR UPDATE SKIP LOCKED so concurrent dispatchers of the same site
    never claim the same row; attempts increments on every claim so
    expire_leases can cap retries. Unregistered site ids are refused loudly
    (same contract as queue_run) instead of silently returning idle.
    """
    sites = load_sites()
    if site not in sites:
        raise ValueError(
            f"site {site!r} is not registered; known sites: "
            + ", ".join(sorted(sites))
        )
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH next AS (
                SELECT id FROM pending_runs
                WHERE site = %s AND status = 'pending'
                ORDER BY created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE pending_runs p
            SET status = 'claimed', claimed_by = %s, claimed_at = now(),
                lease_until = now() + make_interval(secs => %s),
                attempts = p.attempts + 1
            FROM next
            WHERE p.id = next.id
            RETURNING p.id, p.source, p.params, p.attempts, p.max_attempts
            """,
            (site, claimed_by, lease_seconds),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {"id": row[0], "source": row[1], "params": row[2] or {},
                "attempts": row[3], "max_attempts": row[4]}


def expire_leases(conn) -> int:
    """Requeue claimed rows whose lease lapsed; fail them past max_attempts."""
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE pending_runs
            SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'pending' END,
                error_head = CASE WHEN attempts >= max_attempts
                             THEN 'lease expired; attempts exhausted'
                             ELSE 'lease expired; requeued' END,
                lease_until = NULL, claimed_by = NULL, claimed_at = NULL
            WHERE status = 'claimed' AND lease_until < now()
            """
        )
        return cur.rowcount


def finish_pending(conn, pending_id: int, *, run_id: int | None, status: str,
                   error_head: str | None = None) -> None:
    """Close a claimed row: done/failed/cancelled per the run outcome."""
    with conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE pending_runs
               SET status = %s, run_id = %s, finished_at = now(), error_head = %s
               WHERE id = %s""",
            (status, run_id, error_head, pending_id),
        )


def heartbeat_site(conn, site: str) -> None:
    """Touch crawl_sites.last_seen_at so the console can show site staleness."""
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE crawl_sites SET last_seen_at = now() WHERE id = %s", (site,))


# ---------------------------------------------------------------------------
# Schedule-driven enqueue for docker sites. k8s-site sources get their cadence
# from AppSet CronJobs; docker sites have no cluster scheduler, so the local
# dispatcher tick evaluates crawl_sources.schedule itself and queues due runs.
# ---------------------------------------------------------------------------

def _cron_field(field: str, value: int, lo: int, hi: int) -> bool:
    """One 5-field cron field: '*', 'n', '*/s', 'a-b', comma lists of those."""
    if field == "*":
        return True
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(part)
            if step != 1:  # '5/10' == '5-hi/10' in vixie cron
                end = hi
        if start <= value <= end and (value - start) % step == 0:
            return True
    return False


def cron_matches(expr: str, minute: int, hour: int, dom: int, month: int,
                 dow: int) -> bool:
    """Vixie-style match with the standard dom/dow OR rule (either restricted
    field matching satisfies the day)."""
    f = expr.split()
    if len(f) != 5:
        return False
    if not (_cron_field(f[0], minute, 0, 59) and _cron_field(f[1], hour, 0, 23)
            and _cron_field(f[3], month, 1, 12)):
        return False
    dom_f, dow_f = f[2], f[4]
    dom_restricted, dow_restricted = dom_f != "*", dow_f != "*"
    dom_ok = _cron_field(dom_f, dom, 1, 31)
    # Python weekday (Mon=0) -> cron dow (Sun=0)
    dow_ok = _cron_field(dow_f, (dow + 1) % 7, 0, 6)
    if dom_restricted and dow_restricted:
        return dom_ok or dow_ok
    return dom_ok and dow_ok


def enqueue_due(conn, site: str, window_minutes: int = 15) -> list[str]:
    """Queue one run per due scheduled source of a docker site.

    Due = the schedule matched any 5-minute boundary within the grace window
    AND no open/recent run covers it (pending/claimed row, or a crawl_runs
    row newer than the window start). Returns the enqueued source names.
    Queueing happens OUTSIDE the read transaction — queue_run opens its own,
    and psycopg2 forbids re-entering a connection's transaction block.
    """
    now = datetime.now(timezone.utc)
    due: list[str] = []
    with conn, conn.cursor() as cur:
        cur.execute(
            "SELECT source, schedule FROM crawl_sources "
            "WHERE site = %s AND enabled AND schedule IS NOT NULL", (site,))
        candidates = cur.fetchall() or []
        open_rows: dict[str, int] = {}
        cur.execute(
            "SELECT source, count(*) FROM pending_runs "
            "WHERE site = %s AND status IN ('pending', 'claimed') "
            "GROUP BY source", (site,))
        open_rows = dict(cur.fetchall() or [])
        cur.execute(
            "SELECT source, max(started_at) FROM crawl_runs "
            "WHERE started_at > %s GROUP BY source",
            (now - timedelta(minutes=window_minutes),))
        recent = dict(cur.fetchall() or [])
    for source, schedule in candidates:
        hit = False
        for back in range(0, window_minutes + 1, 5):
            t = now - timedelta(minutes=back)
            if cron_matches(schedule, t.minute, t.hour, t.day, t.month,
                            t.weekday()):
                hit = True
                break
        if not hit:
            continue
        if open_rows.get(source, 0) > 0:
            continue
        if source in recent:
            continue
        due.append(source)
    enqueued: list[str] = []
    for source in due:
        queue_run(conn, source, site=site,
                  requested_by="schedule-tick",
                  known_sources=None)
        enqueued.append(source)
    return enqueued


def sync_sources(conn, content_dir: str) -> int:
    """Upsert the source inventory from the checked-out manifests.

    The console cannot read the content repo; the dispatcher (which always
    has a fresh checkout) mirrors spiders/*/manifest.yaml into crawl_sources
    so the platform keeps its pull-only shape: every view is DB-derived.
    """
    import yaml
    from pathlib import Path

    rows = []
    for m in sorted(Path(content_dir).glob("*/manifest.yaml")):
        try:
            data = yaml.safe_load(m.read_text()) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict) or not data.get("name"):
            continue
        site = data.get("site") or DEFAULT_SITE
        rows.append((str(data["name"]), site, data.get("schedule") or None,
                     bool(data.get("enabled", True)),
                     (data.get("auth_profile") or None)))
    commit = ""
    commit_file = os.environ.get("FD_CONTENT_COMMIT", "")
    if commit_file and os.path.isfile(commit_file):
        try:
            commit = open(commit_file).read().strip()[:12]
        except OSError:
            pass
    with conn, conn.cursor() as cur:
        for source, site, schedule, enabled, auth_profile in rows:
            cur.execute(
                """INSERT INTO crawl_sources (source, site, schedule, enabled,
                     auth_profile, last_commit)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (source) DO UPDATE SET
                     site = EXCLUDED.site, schedule = EXCLUDED.schedule,
                     enabled = EXCLUDED.enabled,
                     auth_profile = EXCLUDED.auth_profile,
                     last_commit = CASE WHEN EXCLUDED.last_commit = ''
                                   THEN crawl_sources.last_commit ELSE EXCLUDED.last_commit END,
                     updated_at = now()""",
                (source, site, schedule, enabled, auth_profile, commit),
            )
    return len(rows)
