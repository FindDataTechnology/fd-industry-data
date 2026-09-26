#!/usr/bin/env python3
"""Login station runtime (login-station-console): run a source's login unit
inside this pod with an observable headful browser.

Container layout:
  Xvfb :99  ->  x11vnc  ->  websockify(:6080, noVNC web) for the operator
  login unit (spiders/<src>/login.py::login) with DISPLAY=:99, headful

The station reports lifecycle to central `crawl_login_stations`
(launching -> waiting_operator -> completed/failed/timeout) and, on success,
stores the session via the same pool path as login_session.py. The pod stays
alive for a short grace period after completion so the operator can see the
result, then exits (k8s Job deadline is the hard backstop).

Usage: python3 scripts/login_station.py --station <src> <account_alias>
Env: FD_CRAWL_DB_URL, PLATFORM_SESSION_KEY, RUSTFS_*, STATION_ID,
     optional PROXY_URL (identity egress), FD_STATION_GRACE_SECONDS.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fd_industry_data import auth, dispatch  # noqa: E402

STATION_DDL = """
CREATE TABLE IF NOT EXISTS crawl_login_stations (
    id          bigserial PRIMARY KEY,
    identity_id bigint NOT NULL REFERENCES crawl_identities(id) ON DELETE CASCADE,
    source      text NOT NULL,
    account_alias text NOT NULL,
    status      text NOT NULL DEFAULT 'launching'
                CHECK (status IN ('launching','waiting_operator','completed',
                                  'failed','timeout','reclaimed')),
    proxy_url   text,
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    deadline_at timestamptz,
    finished_at timestamptz
);
"""

WS_PORT = 6080


def _report(conn, station_id, status, note=""):
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE crawl_login_stations SET status=%s, note=%s, "
                    "finished_at=CASE WHEN %s IN ('completed','failed','timeout') "
                    "THEN now() ELSE finished_at END WHERE id=%s",
                    (status, note[:500], status, station_id))


def _start_desktop() -> list[subprocess.Popen]:
    procs = [
        subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1280x800x24"]),
        subprocess.Popen(["x11vnc", "-display", ":99", "-forever", "-nopw",
                          "-quiet"]),
        subprocess.Popen(["websockify", "--web", "/usr/share/novnc",
                          str(WS_PORT), "localhost:5900"]),
    ]
    time.sleep(2.5)
    return procs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--station", nargs=2, metavar=("SOURCE", "ACCOUNT"))
    args = ap.parse_args()
    source, account = args.station

    os.environ["DISPLAY"] = ":99"
    if os.environ.get("PROXY_URL"):
        for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            os.environ[k] = os.environ["PROXY_URL"]
        os.environ.setdefault("NO_PROXY", "100.64.0.0/10,10.0.0.0/8,localhost")

    conn = dispatch.connect()
    conn.autocommit = True
    if os.environ.get("FD_SCHEMA_MANAGED", "") != "1":
        with conn, conn.cursor() as cur:
            cur.execute(STATION_DDL)

    station_id = int(os.environ["STATION_ID"])
    deadline = float(os.environ.get("FD_STATION_DEADLINE_SECONDS", "1500"))
    grace = float(os.environ.get("FD_STATION_GRACE_SECONDS", "20"))
    _report(conn, station_id, "waiting_operator",
            "station desktop up; waiting for operator/login unit")

    procs = _start_desktop()
    rc = 1
    try:
        from login_session import load_login_unit  # same dir as this script

        login = load_login_unit(source)
        automation = getattr(login, "automation", "assisted")
        print(f"login_station: running unit {source}/{account} "
              f"(automation={automation})", flush=True)
        jar = login(account)
        session_ref = auth.upload_jar(source, account, jar)
        ident_rows = [r for r in auth.pool_status(conn, source)
                      if r["account_alias"] == account]
        if not ident_rows:
            raise RuntimeError("identity vanished during station login")
        cur = conn.cursor()
        cur.execute("SELECT id FROM crawl_identities WHERE source=%s AND account_alias=%s",
                    (source, account))
        ident_id = cur.fetchone()[0]
        auth.complete_login(conn, ident_id, session_ref, probe_ok=True)
        _report(conn, station_id, "completed",
                f"session stored {session_ref}")
        rc = 0
    except Exception as e:  # noqa: BLE001 - station failures are station events
        import traceback

        traceback.print_exc()
        _report(conn, station_id, "failed", f"{type(e).__name__}: {e}")
    finally:
        print(f"login_station: grace {grace}s before exit", flush=True)
        time.sleep(grace)
        for proc in procs:
            proc.terminate()
    return rc


if __name__ == "__main__":
    sys.exit(main())
