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
    expire_leases can cap retries.
    """
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
                     bool(data.get("enabled", True))))
    commit = ""
    commit_file = os.environ.get("FD_CONTENT_COMMIT", "")
    if commit_file and os.path.isfile(commit_file):
        try:
            commit = open(commit_file).read().strip()[:12]
        except OSError:
            pass
    with conn, conn.cursor() as cur:
        for source, site, schedule, enabled in rows:
            cur.execute(
                """INSERT INTO crawl_sources (source, site, schedule, enabled, last_commit)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (source) DO UPDATE SET
                     site = EXCLUDED.site, schedule = EXCLUDED.schedule,
                     enabled = EXCLUDED.enabled,
                     last_commit = CASE WHEN EXCLUDED.last_commit = ''
                                   THEN crawl_sources.last_commit ELSE EXCLUDED.last_commit END,
                     updated_at = now()""",
                (source, site, schedule, enabled, commit),
            )
    return len(rows)
