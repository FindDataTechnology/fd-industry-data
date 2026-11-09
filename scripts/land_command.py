#!/usr/bin/env python3
"""Land ONE named function of a multi-command spider unit into the crawl DB.

Why this exists: ``fd-runner <src>`` only ever calls ``run_<src>`` (that is the
production entry contract). Units that expose several entry points
(``eurostat-api`` has run_eurostat_api / _c20 / _hicp / _i56 / …) therefore have
no local path to land a single command's payload — the platform dispatches
those through the provider's ``command`` channel, which the CLI does not carry.

This helper is a MAIN-AGENT tool, not part of the runtime: it calls the named
function and writes the same crawl_runs/crawl_items records the runner would
(source = unit dir name, kind = runtime, image_tag from FD_IMAGE_TAG), so the
extractors in scripts/restaurant_extractors/ can read the payloads exactly as
they read production ones.

Usage:
    FD_CRAWL_DB_URL=$FD_OPEN_DATA_DSN FD_IMAGE_TAG=local \
      .venv/bin/python scripts/land_command.py eurostat-api run_eurostat_i56 --limit 500
    # declared non-limit params go through --params '{"k": v}'

Never fabricates rows: whatever the entry function returns is what lands.
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import sys
import time

from fd_industry_data import writer


def load_entry(src: str, command: str):
    mod = importlib.import_module(f"spiders.{src}.spider")
    fn = getattr(mod, command, None)
    if fn is None or not callable(fn):
        raise SystemExit(f"FATAL: {src}/spider.py has no callable {command}")
    return fn


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="unit dir name under spiders/ (crawl_runs.source)")
    ap.add_argument("command", help="entry function to call, e.g. run_eurostat_i56")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--params", default=None, help="JSON object of declared params")
    args = ap.parse_args()

    if not os.environ.get("FD_CRAWL_DB_URL"):
        print("FATAL: FD_CRAWL_DB_URL 未设置（source ~/.config/finddata/dashboards.env）")
        return 2

    fn = load_entry(args.source, args.command)
    params = json.loads(args.params) if args.params else {}
    accepted = inspect.signature(fn).parameters
    kwargs = {k: v for k, v in params.items() if k in accepted}
    dropped = set(params) - set(kwargs)
    if dropped:
        print(f"WARN: 入口不接受这些参数，已丢弃: {sorted(dropped)}")
    if "limit" in accepted:
        kwargs["limit"] = args.limit

    os.environ.setdefault("FD_WRITER_MAX_ITEMS", "100000")
    image_tag = os.environ.get("FD_IMAGE_TAG", "local-land-command")
    started = time.time()
    status, rows, err = "success", [], None
    try:
        rows = fn(**kwargs)
    except Exception as exc:  # noqa: BLE001 — the run record must carry the failure
        status, err = "failed", f"{type(exc).__name__}: {exc}"[:500]
        raise
    finally:
        writer.report_run(
            source=args.source, kind="runtime", status=status,
            started_at=started, finished_at=time.time(), rows_written=len(rows),
            error_head=err, commit_sha=os.environ.get("FD_CONTENT_COMMIT", "unknown")[:12],
            image_tag=image_tag, items=rows,
        )
    print(f"{args.source}::{args.command} status={status} rows={len(rows)}")
    if rows:
        print("first:", json.dumps(rows[0], ensure_ascii=False, default=str)[:300])
    return 0


if __name__ == "__main__":
    sys.exit(main())
