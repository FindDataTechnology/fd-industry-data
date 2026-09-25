"""Best-effort run reporting into the central crawl_ops schema.

Writes one crawl_runs row per execution plus the raw items into
crawl_items (raw landing zone — semantic normalization is the registry
line's job, not the runtime's). Reporting failures are retried briefly,
then dumped to a local fallback file; they never raise into the crawl.

Two-phase runs (start_run/finish_run) insert a `running` row first so
single-flight guards and cancel checks can see in-flight executions;
report_run remains the one-shot path for special-source helpers.
"""
from __future__ import annotations

import json
import os
import sys
import time

_MAX_ITEMS = 1000
_RETRY_DELAYS = (0.5, 1.0, 2.0)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS crawl_runs (
    id          bigserial PRIMARY KEY,
    source      text NOT NULL,
    kind        text NOT NULL DEFAULT 'runtime',
    status      text NOT NULL,
    started_at  timestamptz NOT NULL,
    finished_at timestamptz,
    rows_written integer NOT NULL DEFAULT 0,
    error_head  text,
    commit_sha  text,
    image_tag   text,
    cancel_requested timestamptz,
    pending_run_id bigint,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS crawl_runs_source_idx ON crawl_runs (source, created_at DESC);
CREATE TABLE IF NOT EXISTS crawl_items (
    run_id  bigint NOT NULL REFERENCES crawl_runs (id) ON DELETE CASCADE,
    idx     integer NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (run_id, idx)
);
"""


def _connect():
    import psycopg2

    url = os.environ.get("FD_CRAWL_DB_URL", "")
    url = url.replace("postgresql+psycopg2://", "postgresql://")
    if not url:
        return None
    return psycopg2.connect(url, connect_timeout=8)


_schema_done = False


def _ensure_schema_once(cur) -> None:
    """Run the DDL battery at most once per process.

    The DDL (CREATE INDEX / ALTER) takes strong locks; running it on every
    report call piled up behind any long-lived reader. Tables persist in the
    central DB, so per-process once is enough.
    """
    global _schema_done
    if not _schema_done:
        _ensure_schema_once(cur)
        _schema_done = True


def start_run(*, source, kind="runtime", commit_sha, image_tag,
              pending_run_id=None) -> int | None:
    """Insert a `running` crawl_runs row; returns its id (None on failure).

    Two-phase counterpart of report_run: single-flight guards and cancel
    checks key off this row until finish_run closes it.
    """
    conn = None
    for delay in (0.0,) + _RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            conn = _connect()
            if conn is None:
                return None
            with conn, conn.cursor() as cur:
                _ensure_schema_once(cur)
                cur.execute(
                    """INSERT INTO crawl_runs
                       (source, kind, status, started_at, commit_sha, image_tag,
                        pending_run_id)
                       VALUES (%s,%s,'running',to_timestamp(%s),%s,%s,%s)
                       RETURNING id""",
                    (source, kind, time.time(), commit_sha, image_tag, pending_run_id),
                )
                return cur.fetchone()[0]
        except Exception as e:  # noqa: BLE001 - reporting must never break the crawl
            print(f"writer: start_run attempt failed: {e}", file=sys.stderr)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    return None


def finish_run(run_id: int, *, status, finished_at, rows_written,
               error_head=None, items=()) -> bool:
    """Close a running row with the final status and capped raw items."""
    payload = [items[i] for i in range(min(len(items), _MAX_ITEMS))]
    conn = None
    for delay in (0.0,) + _RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            conn = _connect()
            if conn is None:
                print("writer: FD_CRAWL_DB_URL not set; skipping report", file=sys.stderr)
                return False
            with conn, conn.cursor() as cur:
                cur.execute(
                    """UPDATE crawl_runs
                       SET status=%s, finished_at=to_timestamp(%s),
                           rows_written=%s, error_head=%s
                       WHERE id=%s""",
                    (status, finished_at, rows_written, error_head, run_id),
                )
                if payload:
                    cur.executemany(
                        "INSERT INTO crawl_items (run_id, idx, payload) VALUES (%s,%s,%s)",
                        [(run_id, i, json.dumps(it, ensure_ascii=False, default=str))
                         for i, it in enumerate(payload)],
                    )
            return True
        except Exception as e:  # noqa: BLE001
            print(f"writer: finish_run attempt failed: {e}", file=sys.stderr)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    return False


def request_cancel(run_id: int) -> bool:
    """Set the cancel flag on a running row (idempotent; no-op if finished)."""
    try:
        conn = _connect()
    except Exception:
        return False
    if conn is None:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE crawl_runs SET cancel_requested=now() "
                "WHERE id=%s AND status='running'",
                (run_id,),
            )
            return cur.rowcount > 0
    except Exception:
        return False
    finally:
        conn.close()


def cancel_requested(run_id: int) -> bool:
    """True when the run's cancel flag has been set."""
    try:
        conn = _connect()
    except Exception:
        return False
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT cancel_requested IS NOT NULL FROM crawl_runs WHERE id=%s",
                (run_id,),
            )
            row = cur.fetchone()
            return bool(row and row[0])
    except Exception:
        return False
    finally:
        conn.close()


def open_run_for(source: str) -> int | None:
    """Id of the open (`running`) run for a source, if any (single-flight guard)."""
    try:
        conn = _connect()
    except Exception:
        return None
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM crawl_runs WHERE source=%s AND status='running' "
                "ORDER BY id DESC LIMIT 1",
                (source,),
            )
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return None
    finally:
        conn.close()


def report_run(*, source, kind, status, started_at, finished_at, rows_written,
               error_head, commit_sha, image_tag, items) -> bool:
    """Insert the run record (and capped raw items); True on success."""
    payload = [items[i] for i in range(min(len(items), _MAX_ITEMS))]
    for delay in (0.0,) + _RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        conn = None
        try:
            conn = _connect()
            if conn is None:
                print("writer: FD_CRAWL_DB_URL not set; skipping report", file=sys.stderr)
                return False
            with conn, conn.cursor() as cur:
                _ensure_schema_once(cur)
                cur.execute(
                    """INSERT INTO crawl_runs
                       (source, kind, status, started_at, finished_at,
                        rows_written, error_head, commit_sha, image_tag)
                       VALUES (%s,%s,%s,to_timestamp(%s),to_timestamp(%s),%s,%s,%s,%s)
                       RETURNING id""",
                    (source, kind, status, started_at, finished_at,
                     rows_written, error_head, commit_sha, image_tag),
                )
                run_id = cur.fetchone()[0]
                if payload:
                    cur.executemany(
                        "INSERT INTO crawl_items (run_id, idx, payload) VALUES (%s,%s,%s)",
                        [(run_id, i, json.dumps(it, ensure_ascii=False, default=str))
                         for i, it in enumerate(payload)],
                    )
            return True
        except Exception as e:  # noqa: BLE001 - reporting must never break the crawl
            print(f"writer: report attempt failed: {e}", file=sys.stderr)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    _fallback(source, kind, status, rows_written, error_head, payload)
    return False


def _fallback(source, kind, status, rows_written, error_head, items):
    path = os.environ.get("FD_REPORT_FALLBACK", "/tmp/fd-runner-report.json")
    try:
        with open(path, "a") as f:
            f.write(json.dumps({
                "source": source, "kind": kind, "status": status,
                "rows_written": rows_written, "error_head": error_head,
                "items": items, "ts": time.time(),
            }, ensure_ascii=False, default=str) + "\n")
        print(f"writer: report fallback appended to {path}", file=sys.stderr)
    except OSError as e:
        print(f"writer: fallback write failed: {e}", file=sys.stderr)
