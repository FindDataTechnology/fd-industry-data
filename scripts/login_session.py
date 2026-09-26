#!/usr/bin/env python3
"""Drive a source's login unit and store the session into the platform pool.

The login unit lives in the content repo at spiders/<src>/login.py and
exposes `login(account_alias) -> dict` (the jar). This driver:

  1. runs the unit (headed local playwright by default; set BROWSER_CDP_URL
     to drive the cluster's browserless/chrome instead),
  2. encrypts the jar and uploads it to RustFS,
  3. records the identity + login/probe events centrally (active).

Human-assisted units open a window and wait for the operator to finish.

Usage:
  python3 scripts/login_session.py <src> <account_alias> [--automation auto|assisted]
Env: FD_CRAWL_DB_URL, PLATFORM_SESSION_KEY, RUSTFS_* (as the pool requires).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fd_industry_data import auth, dispatch  # noqa: E402


def load_login_unit(src: str):
    root = Path(os.environ.get("FD_CONTENT_DIR")
                or Path(__file__).resolve().parents[1] / "spiders")
    path = root / src / "login.py"
    if not path.is_file():
        raise SystemExit(f"login_session: no login unit at {path}")
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"fd_login_{src}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    fn = getattr(mod, "login", None)
    if not callable(fn):
        raise SystemExit(f"login_session: {path} exposes no login(account) callable")
    return fn


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("account_alias")
    ap.add_argument("--automation", choices=("auto", "assisted"), default=None)
    args = ap.parse_args()

    login = load_login_unit(args.source)
    unit_auto = getattr(login, "automation", None)
    automation = args.automation or unit_auto or "assisted"
    print(f"login_session: running {args.source}/{args.account_alias} "
          f"(automation={automation})")
    jar = login(args.account_alias)
    if not isinstance(jar, dict):
        raise SystemExit("login_session: unit must return a jar dict")

    print("login_session: session captured; redacted view:",
          auth.redact_jar(jar))
    session_ref = auth.upload_jar(args.source, args.account_alias, jar)
    conn = dispatch.connect()
    conn.autocommit = True
    ident_id = auth.request_login(conn, args.source, args.account_alias,
                                  automation=automation)
    auth.complete_login(conn, ident_id, session_ref, probe_ok=True)
    status = [r for r in auth.pool_status(conn, args.source)
              if r["account_alias"] == args.account_alias][0]
    print(f"login_session: identity active "
          f"(id={ident_id}, session={session_ref}, status={status['status']})")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
