"""Authenticated-crawling session pool (session-pool).

Identity pool + lease semantics + encrypted session jars, inherited from
legal-auth-broker's proven design and re-platformed:

- identities live in central `crawl_identities` (five-state machine,
  events audited in `crawl_identity_events`)
- leasing is table-native (SKIP LOCKED, same pattern as pending_runs)
  — no new resident service
- jars are Fernet-encrypted client-side and stored on RustFS
  (`platform-sessions` bucket); the object store is an untrusted pipe
- failure feedback is dual-path: explicit auth events from the runner +
  consecutive-zero-yield suspicion from the platform

Env: FD_CRAWL_DB_URL (as everywhere), PLATFORM_SESSION_KEY (Fernet key),
     RUSTFS_ENDPOINT/RUSTFS_ACCESS_KEY/RUSTFS_SECRET_KEY (jar store).
"""
from __future__ import annotations

import base64
import json
import os
import secrets
from typing import Any

STATUSES = ("login_required", "active", "cooldown", "banned", "retired")
EVENT_KINDS = ("login", "probe", "small_batch", "lease_acquired", "lease_released",
               "lease_expired", "auth_failed", "suspect_yield", "banned", "note")
SUSPECT_ZERO_RUNS = 3

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS crawl_identities (
    id             bigserial PRIMARY KEY,
    source         text NOT NULL,
    account_alias  text NOT NULL,
    status         text NOT NULL DEFAULT 'login_required'
                   CHECK (status IN ('login_required','active','cooldown',
                                     'banned','retired')),
    automation     text NOT NULL DEFAULT 'assisted'
                   CHECK (automation IN ('auto','assisted')),
    egress_ref     text,
    credentials_secret_ref text,
    session_ref    text,
    lease_owner    text,
    lease_token    text,
    lease_expires_at timestamptz,
    last_login_at  timestamptz,
    last_probe_at  timestamptz,
    last_success_at timestamptz,
    consecutive_zero_runs integer NOT NULL DEFAULT 0,
    failure_count  integer NOT NULL DEFAULT 0,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, account_alias)
);
CREATE INDEX IF NOT EXISTS crawl_identities_pool_idx
    ON crawl_identities (source, status);
CREATE INDEX IF NOT EXISTS crawl_identities_lease_idx
    ON crawl_identities (lease_expires_at) WHERE lease_token IS NOT NULL;

CREATE TABLE IF NOT EXISTS crawl_identity_events (
    id          bigserial PRIMARY KEY,
    identity_id bigint NOT NULL REFERENCES crawl_identities(id) ON DELETE CASCADE,
    kind        text NOT NULL
                CHECK (kind IN ('login','probe','small_batch','lease_acquired',
                                'lease_released','lease_expired','auth_failed',
                                'suspect_yield','banned','note')),
    detail      text,
    lease_token text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS crawl_identity_events_identity_idx
    ON crawl_identity_events (identity_id, created_at DESC);

ALTER TABLE crawl_sources ADD COLUMN IF NOT EXISTS auth_profile text;
ALTER TABLE crawl_runs ADD COLUMN IF NOT EXISTS identity_alias text;
"""

GRANTS = """
GRANT SELECT, INSERT, UPDATE, DELETE ON crawl_identities, crawl_identity_events
    TO fd_platform;
GRANT USAGE, SELECT ON SEQUENCE crawl_identities_id_seq, crawl_identity_events_id_seq
    TO fd_platform;
"""


def ensure_schema(conn) -> None:
    """Idempotent DDL; deploy-side runs it (runtime skips via FD_SCHEMA_MANAGED)."""
    if os.environ.get("FD_SCHEMA_MANAGED", "") == "1":
        return
    with conn, conn.cursor() as cur:
        cur.execute(SCHEMA_DDL)


# ── session jars ────────────────────────────────────────────────────────

def _fernet():
    from cryptography.fernet import Fernet

    key = os.environ.get("PLATFORM_SESSION_KEY", "")
    if not key:
        raise RuntimeError("PLATFORM_SESSION_KEY not set (Fernet key)")
    return Fernet(key if key.endswith("=") else base64.urlsafe_b64encode(
        key.encode()).decode())


def encrypt_jar(jar: dict) -> bytes:
    return _fernet().encrypt(json.dumps(jar, ensure_ascii=False).encode())


def decrypt_jar(blob: bytes) -> dict:
    return json.loads(_fernet().decrypt(blob).decode())


_REDACT_KEYS = ("password", "token", "secret", "cookie_value",
                "authorization", "value")  # jar-context only: value=cookie value


def redact_jar(jar: dict) -> dict:
    """Log-safe shape: values of sensitive keys become '***'."""
    out = {}
    for k, v in jar.items():
        if any(r in k.lower() for r in _REDACT_KEYS):
            out[k] = "***"
        elif isinstance(v, dict):
            out[k] = redact_jar(v)
        elif isinstance(v, list):
            out[k] = [redact_jar(i) if isinstance(i, dict) else i for i in v]
        else:
            out[k] = v
    return out


def _minio_client():
    from minio import Minio

    endpoint = os.environ.get("RUSTFS_ENDPOINT", "")
    if not endpoint:
        raise RuntimeError("RUSTFS_ENDPOINT not set")
    host = endpoint.split("://", 1)[-1]
    return Minio(host, access_key=os.environ.get("RUSTFS_ACCESS_KEY", ""),
                 secret_key=os.environ.get("RUSTFS_SECRET_KEY", ""),
                 secure=endpoint.startswith("https"))


BUCKET = "platform-sessions"


def upload_jar(source: str, account_alias: str, jar: dict) -> str:
    """Encrypt + upload; returns the object key."""
    key = f"{source}/{account_alias}/{secrets.token_hex(6)}.jar"
    c = _minio_client()
    if not c.bucket_exists(BUCKET):
        c.make_bucket(BUCKET)
    from io import BytesIO

    blob = encrypt_jar(jar)
    c.put_object(BUCKET, key, BytesIO(blob), length=len(blob),
                 content_type="application/octet-stream")
    return f"{BUCKET}/{key}"


def fetch_jar(session_ref: str) -> dict:
    bucket, _, key = session_ref.partition("/")
    resp = _minio_client().get_object(bucket, key)
    try:
        return decrypt_jar(resp.read())
    finally:
        resp.close()
        resp.release_conn()


# ── identity pool ───────────────────────────────────────────────────────

def _event(cur, identity_id: int, kind: str, detail: str = "",
           lease_token: str | None = None) -> None:
    cur.execute(
        "INSERT INTO crawl_identity_events (identity_id, kind, detail, lease_token) "
        "VALUES (%s,%s,%s,%s)", (identity_id, kind, detail[:500], lease_token))


def request_login(conn, source: str, account_alias: str, *,
                  automation: str = "assisted",
                  credentials_secret_ref: str | None = None) -> int:
    """Ensure an identity exists in login_required (idempotent)."""
    with conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO crawl_identities (source, account_alias, automation,
                 credentials_secret_ref)
               VALUES (%s,%s,%s,%s)
               ON CONFLICT (source, account_alias) DO UPDATE SET
                 status = 'login_required', lease_owner = NULL, lease_token = NULL,
                 lease_expires_at = NULL, updated_at = now()""",
            (source, account_alias, automation, credentials_secret_ref))
        cur.execute("SELECT id FROM crawl_identities WHERE source=%s AND account_alias=%s",
                    (source, account_alias))
        ident_id = cur.fetchone()[0]
        _event(cur, ident_id, "note", "login requested")
        return ident_id


def lease_identity(conn, source: str, owner: str, ttl_seconds: int = 3600):
    """Atomically lease the best active identity; None when the pool is dry.

    Preference: healthy (no consecutive zero runs) first, then least recently
    successful. SKIP LOCKED keeps concurrent dispatchers safe.
    """
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH candidate AS (
                SELECT id FROM crawl_identities
                WHERE source = %s AND status = 'active' AND lease_token IS NULL
                ORDER BY consecutive_zero_runs ASC, last_success_at ASC NULLS FIRST
                FOR UPDATE SKIP LOCKED LIMIT 1
            )
            UPDATE crawl_identities i
            SET lease_owner = %s, lease_token = %s,
                lease_expires_at = now() + make_interval(secs => %s),
                updated_at = now()
            FROM candidate WHERE i.id = candidate.id
            RETURNING i.id, i.account_alias, i.session_ref, i.lease_token
            """,
            (source, owner, secrets.token_hex(12), ttl_seconds))
        row = cur.fetchone()
        if row is None:
            return None
        _event(cur, row[0], "lease_acquired", f"owner={owner}", row[3])
        return {"id": row[0], "account_alias": row[1],
                "session_ref": row[2], "lease_token": row[3]}


def release_identity(conn, identity_id: int, lease_token: str, *,
                     success: bool | None = None) -> None:
    """Release a lease; a successful use refreshes health clocks."""
    with conn, conn.cursor() as cur:
        if success is True:
            cur.execute(
                "UPDATE crawl_identities SET lease_owner=NULL, lease_token=NULL, "
                "lease_expires_at=NULL, last_success_at=now(), "
                "consecutive_zero_runs=0, failure_count=0, updated_at=now() "
                "WHERE id=%s AND lease_token=%s", (identity_id, lease_token))
        elif success is False:
            cur.execute(
                "UPDATE crawl_identities SET lease_owner=NULL, lease_token=NULL, "
                "lease_expires_at=NULL, failure_count=failure_count+1, updated_at=now() "
                "WHERE id=%s AND lease_token=%s", (identity_id, lease_token))
        else:
            cur.execute(
                "UPDATE crawl_identities SET lease_owner=NULL, lease_token=NULL, "
                "lease_expires_at=NULL, updated_at=now() "
                "WHERE id=%s AND lease_token=%s", (identity_id, lease_token))
        _event(cur, identity_id, "lease_released", f"success={success}", lease_token)


def expire_leases(conn) -> int:
    """Recycle TTL-expired leases (abandoned executions)."""
    with conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, lease_token FROM crawl_identities "
            "WHERE lease_token IS NOT NULL AND lease_expires_at < now()")
        rows = cur.fetchall()
        for identity_id, token in rows:
            cur.execute(
                "UPDATE crawl_identities SET lease_owner=NULL, lease_token=NULL, "
                "lease_expires_at=NULL, updated_at=now() WHERE id=%s", (identity_id,))
            _event(cur, identity_id, "lease_expired", "ttl exceeded", token)
        return len(rows)


def complete_login(conn, identity_id: int, session_ref: str, *,
                   probe_ok: bool = True) -> None:
    """login(+probe) trust step: login_required -> active."""
    with conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE crawl_identities SET session_ref=%s, last_login_at=now(), "
            "last_probe_at=now(), status=%s, failure_count=0, updated_at=now() "
            "WHERE id=%s",
            (session_ref, "active" if probe_ok else "login_required", identity_id))
        _event(cur, identity_id, "login", f"session stored probe={probe_ok}")
        if probe_ok:
            _event(cur, identity_id, "probe", "ok")


def record_run_outcome(conn, identity_id: int, rows_written: int) -> None:
    """Per-run health tracking; zero yields accumulate to a suspicion event."""
    with conn, conn.cursor() as cur:
        if rows_written > 0:
            cur.execute("UPDATE crawl_identities SET consecutive_zero_runs=0, "
                        "updated_at=now() WHERE id=%s", (identity_id,))
            return
        cur.execute(
            "UPDATE crawl_identities SET consecutive_zero_runs=consecutive_zero_runs+1, "
            "updated_at=now() WHERE id=%s RETURNING consecutive_zero_runs", (identity_id,))
        n = cur.fetchone()[0]
        if n == SUSPECT_ZERO_RUNS:
            _event(cur, identity_id, "suspect_yield",
                   f"{n} consecutive zero-yield runs (no auth event reported)")
            _event(cur, identity_id, "note", "suspicion raised; manual login check advised")


def report_auth_failed(conn, source: str, account_alias: str, detail: str) -> None:
    """Explicit failure path: identity -> login_required + queue for re-login."""
    with conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM crawl_identities WHERE source=%s AND account_alias=%s",
                    (source, account_alias))
        row = cur.fetchone()
        if row is None:
            return
        cur.execute("UPDATE crawl_identities SET status='login_required', "
                    "lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL, "
                    "updated_at=now() WHERE id=%s", (row[0],))
        _event(cur, row[0], "auth_failed", detail)


def pool_status(conn, source: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, account_alias, status, automation, "
                    "last_login_at, last_success_at, consecutive_zero_runs, "
                    "lease_owner IS NOT NULL AS leased "
                    "FROM crawl_identities WHERE source=%s ORDER BY id", (source,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
