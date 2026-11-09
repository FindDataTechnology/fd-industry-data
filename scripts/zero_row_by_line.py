#!/usr/bin/env python3
"""Zero-row rate, split by product line (harden-crawl-delivery 3.1).

The fleet number is misleading on its own. Measured 2026-11-09 over 7 days:

    all lines     937 successes / 200 zero-row  = 21.3%
    industry only 667 successes /  10 zero-row  =  1.3%

Mixing them makes the industry line look like it has a zero-output problem it
does not have, and hides that the absolute loss lives in the law/loader line
(`wbgapi` 14/14 zero, `akshare` 110/140). This script fixes the split so the two
numbers can never be quoted interchangeably again.

Line assignment (deliberately mechanical, so anyone can re-derive it):
  1. ``spiders/<source>/manifest.yaml`` exists          -> industry
  2. the source is in ``config/health-scope.json``'s
     ``excluded_sources`` (registered cross-repo members) -> law-loader
  3. otherwise                                            -> other

Read-only. ``--sql`` prints the single query it runs, so the numbers can be
recomputed without this script.

Usage:
    python3 scripts/zero_row_by_line.py                 # 7 days, table
    python3 scripts/zero_row_by_line.py --days 30 --json
    python3 scripts/zero_row_by_line.py --sql
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

SQL = """
select source,
       count(*) filter (where status = 'success')                        as ok_runs,
       count(*) filter (where status = 'success'
                          and coalesce(rows_written, 0) = 0)             as zero_runs
from crawl_runs
where started_at > now() - interval '{days} days'
group by source
order by zero_runs desc, ok_runs desc
""".strip()

LINES = ("industry", "law-loader", "other")


def load_scope() -> set[str]:
    path = REPO / "config" / "health-scope.json"
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return set()
    return set(doc.get("excluded_sources") or [])


def classify(source: str, scope: set[str], repo: Path = REPO) -> str:
    if (repo / "spiders" / source / "manifest.yaml").is_file():
        return "industry"
    if source in scope:
        return "law-loader"
    return "other"


def run_query(dsn: str, days: int, binary: str = "psql") -> list[tuple[str, int, int]]:
    sql = SQL.format(days=days)
    proc = subprocess.run([binary, dsn, "-At", "-F", "\t", "-c", sql],
                          capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed ({proc.returncode}): {proc.stderr.strip()[:400]}")
    rows: list[tuple[str, int, int]] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        source = cols[0].strip()
        if not re.match(r"^[a-z0-9][a-z0-9._-]*$", source):
            continue  # 非遥测行（2.10 的同一判据）
        rows.append((source, int(cols[1] or 0), int(cols[2] or 0)))
    return rows


def summarise(rows, scope: set[str]) -> dict:
    per_line: dict[str, dict] = {
        name: {"sources": 0, "ok_runs": 0, "zero_runs": 0, "top": []} for name in LINES
    }
    for source, ok, zero in rows:
        line = classify(source, scope)
        bucket = per_line[line]
        bucket["sources"] += 1
        bucket["ok_runs"] += ok
        bucket["zero_runs"] += zero
        if zero:
            bucket["top"].append({"source": source, "ok_runs": ok, "zero_runs": zero})
    for bucket in per_line.values():
        total = bucket["ok_runs"]
        bucket["zero_pct"] = round(100.0 * bucket["zero_runs"] / total, 2) if total else 0.0
        bucket["top"] = sorted(bucket["top"], key=lambda r: -r["zero_runs"])[:8]
    total_ok = sum(b["ok_runs"] for b in per_line.values())
    total_zero = sum(b["zero_runs"] for b in per_line.values())
    return {
        "per_line": per_line,
        "total": {
            "ok_runs": total_ok,
            "zero_runs": total_zero,
            "zero_pct": round(100.0 * total_zero / total_ok, 2) if total_ok else 0.0,
        },
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--dsn", default=os.environ.get("FD_CRAWL_DB_URL", ""))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sql", action="store_true")
    args = ap.parse_args(argv[1:])

    if args.sql:
        print(SQL.format(days=args.days))
        return 0
    if not args.dsn:
        print("zero_row_by_line: need --dsn or FD_CRAWL_DB_URL (or --sql)", file=sys.stderr)
        return 2

    summary = summarise(run_query(args.dsn, args.days), load_scope())
    summary["days"] = args.days
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    print(f"零行率分线（近 {args.days} 天，分母=成功运行）")
    print(f"{'line':<12}{'sources':>8}{'ok_runs':>10}{'zero':>8}{'pct':>8}")
    for name in LINES:
        b = summary["per_line"][name]
        print(f"{name:<12}{b['sources']:>8}{b['ok_runs']:>10}{b['zero_runs']:>8}{b['zero_pct']:>7}%")
    t = summary["total"]
    print(f"{'TOTAL':<12}{'':>8}{t['ok_runs']:>10}{t['zero_runs']:>8}{t['zero_pct']:>7}%")
    for name in LINES:
        top = summary["per_line"][name]["top"]
        if top:
            print(f"\n{name} 零行最多的源：")
            for r in top:
                print(f"  {r['source']:<28} zero={r['zero_runs']:<5} ok={r['ok_runs']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
