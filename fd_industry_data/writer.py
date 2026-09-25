"""Best-effort run reporting into the central crawl_ops schema.

Writes one crawl_runs row per execution plus the raw items into
crawl_items (raw landing zone — semantic normalization is the registry
line's job, not the runtime's). Reporting failures are retried briefly,
then dumped to a local fallback file; they never raise into the crawl.
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
    finished_at timestamptz NOT NULL,
    rows_written integer NOT NULL DEFAULT 0,
    error_head  text,
    commit_sha  text,
    image_tag   text,
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
                cur.execute(_SCHEMA)
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
