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

def _max_items() -> int:
    """Landing cap read per call, not import time: the runner lifts this cap
    via FD_WRITER_MAX_ITEMS for backfill-window runs AFTER this module is
    imported (fix-writer-item-cap) — a module-level constant would freeze the
    pre-lift value. 1000 stays the default (existing fleet behavior)."""
    return int(os.environ.get("FD_WRITER_MAX_ITEMS", "1000"))


def _stale_run_seconds() -> float:
    """Age past which a `running` row is treated as a dead executor's leftover.

    Default 8h30m = the longest *legitimate* run this estate can have, read
    from the dispatcher's own orphan policy: FD_ORPHAN_DEFAULT_TIMEOUT (8h)
    for sources that declare no budget + FD_ORPHAN_GRACE_SECONDS (30m) of Job
    startup/schedule jitter. The k8s CronJob activeDeadlineSeconds default
    (7200s) is far below that, so no live run is misclassified. Anything
    older than this cannot still be running: its pod is gone (crash, OOM,
    deadline kill) and finish_run never ran.
    """
    try:
        return float(os.environ.get("FD_STALE_RUN_SECONDS", "30600"))
    except ValueError:
        return 30600.0


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

-- Columns this module's own INSERT/UPDATE statements name must be owned by
-- this DDL, not by a later auth.ensure_schema() ALTER: start_run inserts
-- identity_alias, and on a DB where only auth's DDL adds it (or where
-- FD_SCHEMA_MANAGED=1 and the deployer missed it) the INSERT failed with
-- UndefinedColumn -> run_id None -> single-flight and cancel silently off.
ALTER TABLE crawl_runs ADD COLUMN IF NOT EXISTS metrics jsonb;
ALTER TABLE crawl_runs ADD COLUMN IF NOT EXISTS identity_alias text;
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
    if os.environ.get("FD_SCHEMA_MANAGED", "") == "1":
        _schema_done = True  # schema is deploy-managed; runtime holds no DDL rights
        return
    if not _schema_done:
        cur.execute(_SCHEMA)
        _schema_done = True


def start_run(*, source, kind="runtime", commit_sha, image_tag,
              pending_run_id=None, identity_alias=None) -> int | None:
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
                        pending_run_id, identity_alias)
                       VALUES (%s,%s,'running',to_timestamp(%s),%s,%s,%s,%s)
                       RETURNING id""",
                    (source, kind, time.time(), commit_sha, image_tag,
                     pending_run_id, identity_alias),
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
               error_head=None, items=(), metrics=None,
               source=None, kind="runtime") -> bool:
    """Close a running row with the final status and capped raw items.

    ``metrics`` (optional dict) lands in ``crawl_runs.metrics``. It carries
    run-quality facts that are neither failures nor row counts — today the
    parameter names the entry point dropped, so a request that silently did
    less than asked is visible in the central DB rather than only on a pod's
    stderr (the dispatcher runs the runner as a subprocess and keeps no copy).

    ``source``/``kind`` only feed the local fallback line written when every
    write attempt fails; pass them so the fallback can be replayed against
    ``crawl_runs`` without guessing.

    The invariant is "a run always reaches a terminal state", not "we always
    write to PG": after the retry ladder this appends the same JSON-lines
    fallback ``report_run`` uses, so a total DB outage leaves a durable
    terminal record instead of a run that vanished. The row itself may still
    be stuck at ``running``; ``reap_stale_runs`` (called by ``open_run_for``)
    is what reclaims it so the source is not disabled forever.
    """
    payload = [items[i] for i in range(min(len(items), _max_items()))]
    conn = None
    for delay in (0.0,) + _RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            conn = _connect()
            if conn is None:
                print("writer: FD_CRAWL_DB_URL not set; skipping report", file=sys.stderr)
                break
            with conn, conn.cursor() as cur:
                cur.execute(
                    """UPDATE crawl_runs
                       SET status=%s, finished_at=to_timestamp(%s),
                           rows_written=%s, error_head=%s, metrics=%s
                       WHERE id=%s""",
                    (status, finished_at, rows_written, error_head,
                     json.dumps(metrics, ensure_ascii=False) if metrics else None,
                     run_id),
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
    # Terminal state still has to exist somewhere: same fallback as report_run.
    _fallback(source, kind, status, rows_written, error_head, payload,
              run_id=run_id)
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


def reap_stale_runs(source: str | None = None, *,
                    max_age_seconds: float | None = None) -> int:
    """Close `running` rows older than the stale threshold as `failed`.

    A process that dies between start_run and finish_run leaves its row at
    'running' forever; ``open_run_for`` then reports single-flight `skipped`
    on every later execution, so one crash silently disables that source for
    good (exit 0, no failed run, all monitors green). Age is the only signal
    available at the runtime layer (the pod is gone and the runner has no
    cluster API to check for a live Job), so it is set above every legitimate
    run budget — see ``_stale_run_seconds``.

    Returns the number of rows closed. Never raises: reaping is opportunistic
    housekeeping and must not break the caller's run.
    """
    age = _stale_run_seconds() if max_age_seconds is None else float(max_age_seconds)
    if age <= 0:
        return 0  # explicit off switch
    sql = ("UPDATE crawl_runs "
           "SET status='failed', finished_at=now(), "
           "    error_head=COALESCE(error_head, %s) "
           "WHERE status='running' "
           "  AND started_at < now() - make_interval(secs => %s)")
    params: list = [
        f"stale-run-reaped: no finish_run within {age:.0f}s "
        f"(executor died; single-flight would block this source forever)",
        age,
    ]
    if source is not None:
        sql += " AND source=%s"
        params.append(source)
    try:
        conn = _connect()
    except Exception as e:  # noqa: BLE001
        print(f"writer: stale-run reap skipped: {e}", file=sys.stderr)
        return 0
    if conn is None:
        return 0
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            reaped = cur.rowcount
        if reaped:
            print(f"writer: reaped {reaped} stale 'running' row(s)"
                  + (f" for {source}" if source else ""), file=sys.stderr)
        return reaped
    except Exception as e:  # noqa: BLE001
        print(f"writer: stale-run reap failed: {e}", file=sys.stderr)
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def open_run_for(source: str) -> int | None:
    """Id of the open (`running`) run for a source, if any (single-flight guard).

    Stale rows are reclaimed first: a `running` row past every possible run
    budget is a crashed executor's leftover, not a live run, and must not
    disable the source forever.
    """
    reap_stale_runs(source)
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
               error_head, commit_sha, image_tag, items, metrics=None) -> bool:
    """Insert the run record (and capped raw items); True on success."""
    payload = [items[i] for i in range(min(len(items), _max_items()))]
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
                        rows_written, error_head, commit_sha, image_tag, metrics)
                       VALUES (%s,%s,%s,to_timestamp(%s),to_timestamp(%s),%s,%s,%s,%s,%s)
                       RETURNING id""",
                    (source, kind, status, started_at, finished_at,
                     rows_written, error_head, commit_sha, image_tag,
                     json.dumps(metrics, ensure_ascii=False) if metrics else None),
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


def _fallback(source, kind, status, rows_written, error_head, items,
              run_id=None):
    path = os.environ.get("FD_REPORT_FALLBACK", "/tmp/fd-runner-report.json")
    try:
        with open(path, "a") as f:
            f.write(json.dumps({
                "source": source, "kind": kind, "status": status,
                "rows_written": rows_written, "error_head": error_head,
                "items": items, "run_id": run_id, "ts": time.time(),
            }, ensure_ascii=False, default=str) + "\n")
        print(f"writer: report fallback appended to {path}", file=sys.stderr)
    except OSError as e:
        print(f"writer: fallback write failed: {e}", file=sys.stderr)
