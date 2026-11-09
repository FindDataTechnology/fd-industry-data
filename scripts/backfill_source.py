#!/usr/bin/env python3
"""Queue a one-off backfill for a source that only ever fetches the newest N rows.

Change harden-crawl-delivery 3.5 / D5. Some units are written as "give me the
latest N items"; their declared ``backfill_window`` says how far back a one-off
sweep should reach. The run itself uses the **existing** platform window channel
(``--window-start`` / ``--window-end``, injected as ``FD_WINDOW_START/END`` and
as kwargs when the entry point names them) — this script only computes the
window and queues it through the single sanctioned insert path
(``dispatch.queue_run``). No new runner flag, no new executor.

Dry-run by default: it prints the window it *would* queue. ``--apply`` writes
one ``pending_runs`` row (requested_by=backfill) — that is a production write,
so it is never implicit.

Usage:
    python3 scripts/backfill_source.py cqvip
    python3 scripts/backfill_source.py cqvip --apply --dsn "$FD_CRAWL_DB_URL"
    python3 scripts/backfill_source.py cqvip --months 36 --dry-run
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.dispatch import first_crawl_window  # noqa: E402

WINDOW_RE = re.compile(r"^(\d{1,3})([my])$")
DEFAULT_MONTHS = 12


def parse_window(value) -> int | None:
    """``backfill_window: 24m`` -> 24 months; ``2y`` -> 24. None when malformed."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value if 1 <= value <= 120 else None
    if not isinstance(value, str):
        return None
    m = WINDOW_RE.match(value.strip().lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    months = n * 12 if unit == "y" else n
    return months if 1 <= months <= 120 else None


def manifest_window(source: str, repo: Path = REPO) -> tuple[int | None, str]:
    """(months, note) from the unit's manifest."""
    import yaml

    path = repo / "spiders" / source / "manifest.yaml"
    if not path.is_file():
        return None, "no manifest"
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        return None, f"unparseable manifest: {exc}"
    raw = data.get("backfill_window")
    if raw is None:
        return None, "no backfill_window declared"
    months = parse_window(raw)
    if months is None:
        return None, f"backfill_window {raw!r} is malformed (expected e.g. 24m or 2y)"
    return months, f"declared {raw}"


def plan(source: str, months: int | None, today: date, repo: Path = REPO) -> dict:
    months = months or DEFAULT_MONTHS
    window = first_crawl_window(today, months=months)
    site = "tencent"
    path = repo / "spiders" / source / "manifest.yaml"
    if path.is_file():
        import yaml

        data = yaml.safe_load(path.read_text()) or {}
        site = str(data.get("site") or "tencent")
    return {
        "source": source,
        "site": site,
        "months": months,
        "params": {"window_start": window["window_start"],
                   "window_end": window["window_end"]},
        "requested_by": "backfill",
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("--months", type=int, default=None,
                    help="override the manifest's backfill_window")
    ap.add_argument("--today", default=None, help="YYYY-MM-DD (testing)")
    ap.add_argument("--dsn", default=os.environ.get("FD_CRAWL_DB_URL", ""))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv[1:])

    declared, note = manifest_window(args.source)
    months = args.months or declared
    if months is None and args.months is None:
        print(f"backfill_source: {args.source}: {note}；用 {DEFAULT_MONTHS}m 兜底",
              file=sys.stderr)
    today = date.fromisoformat(args.today) if args.today else date.today()
    job = plan(args.source, months, today)

    print(f"{'would queue' if not args.apply else 'queue'} {job['source']} "
          f"site={job['site']} window={job['params']['window_start']}..{job['params']['window_end']} "
          f"({job['months']} 个月, {note})")
    if not args.apply:
        print("(dry run; pass --apply to write one pending_runs row)")
        return 0
    if not args.dsn:
        print("backfill_source: --apply needs --dsn or FD_CRAWL_DB_URL", file=sys.stderr)
        return 2

    from fd_industry_data.dispatch import connect, queue_run

    conn = connect(args.dsn)
    run_id = queue_run(conn, job["source"], site=job["site"],
                       params=job["params"], requested_by=job["requested_by"])
    print(f"pending_runs id={run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
