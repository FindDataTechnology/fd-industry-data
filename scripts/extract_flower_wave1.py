#!/usr/bin/env python3
"""Flower-vertical wave-1 — run every extractor under scripts/flower_extractors/.

Same contract as ``scripts/extract_restaurant_wave0.py`` (see
``restaurant_extractors/_common.py`` for the write discipline): one module per
source unit exposing ``SOURCE`` / ``CONCEPTS`` / ``extract(cur, notes)``;
additive ``INSERT ... ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING``;
per-unit SAVEPOINT isolation; concepts born ``verified=false``.

Two things are flower-specific and deliberately loud:

* ``--dry-run`` prints the produced row count per unit **and** the units'
  crawl provenance (``crawl_runs`` success count / newest ``finished_at``), so a
  zero-row result is attributable to "unit never ran" rather than a parser bug;
* the trend gate is NOT applied. Flower data is legitimately short-history
  (dounan rolls a ~2-month window; AIPH is an annual cross-section with mixed
  reference years), so the >=24-period gate would delete every series. The
  board layer instead declares ``short_window`` / cross-section caveats, which
  is where that judgement belongs.

Usage::

    source ~/.config/finddata/dashboards.env
    python3 scripts/extract_flower_wave1.py --dry-run
    python3 scripts/extract_flower_wave1.py            # write (one txn)
    python3 scripts/extract_flower_wave1.py --only aiph-stats
"""
from __future__ import annotations

import argparse
import importlib
import os
import pkgutil
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent))

from restaurant_extractors._common import (  # noqa: E402
    ensure_unit_concepts,
    insert_rows,
)

PACKAGE = "flower_extractors"


def load_extractors() -> dict:
    pkg = importlib.import_module(PACKAGE)
    found = {}
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{PACKAGE}.{info.name}")
        if hasattr(mod, "SOURCE") and hasattr(mod, "extract"):
            found[mod.SOURCE] = mod
    return found


def _provenance(cur, source: str) -> str:
    cur.execute(
        "select count(*), max(finished_at), coalesce(sum(rows_written), 0)"
        " from crawl_runs where source = %s and status = 'success'",
        (source,),
    )
    n, newest, rows = cur.fetchone()
    if not n:
        return "从未成功爬取（单元未部署 / 未跑过）"
    return f"{n} 次成功爬取 · 最近 {newest:%Y-%m-%d %H:%M} · 累计 rows_written={rows}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default=None,
                    help="comma-separated source names (default: all)")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("DSN missing — source ~/.config/finddata/dashboards.env first")

    extractors = load_extractors()
    wanted = set(args.only.split(",")) if args.only else None
    if wanted:
        unknown = wanted - set(extractors)
        if unknown:
            raise SystemExit(f"unknown sources: {sorted(unknown)}; "
                             f"available: {sorted(extractors)}")

    conn = psycopg2.connect(args.dsn)
    failed: list[tuple[str, str]] = []
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("select count(*) from semantic_observations")
            before_total = cur.fetchone()[0]
            cur.execute("select source_used, count(*) from semantic_observations"
                        " group by 1")
            before_by_src = dict(cur.fetchall())

            notes: list[str] = []
            for source, mod in sorted(extractors.items()):
                if wanted and source not in wanted:
                    continue
                print(f"\n== {source}")
                print(f"   爬取底账: {_provenance(cur, source)}")
                if args.dry_run:
                    rows = mod.extract(cur, notes)
                    print(f"   extracted {len(rows)} rows (dry-run)")
                    continue
                sp = "unit_" + source.replace("-", "_").replace(".", "_")
                cur.execute(f"SAVEPOINT {sp}")
                try:
                    rows = mod.extract(cur, notes)
                    codes = {r[0] for r in rows}
                    ids = ensure_unit_concepts(cur, source, mod.CONCEPTS, codes)
                    ins = insert_rows(cur, source, ids, rows)
                    print(f"   extracted {len(rows)} rows, inserted {ins}")
                except Exception as exc:  # noqa: BLE001 — per-unit isolation
                    cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                    failed.append((source, str(exc)[:300]))
                    print(f"   FAILED — rolled back: {exc}")
                cur.execute(f"RELEASE SAVEPOINT {sp}")

            if args.dry_run:
                conn.rollback()
                print("\nnotes:")
                for n in notes:
                    print("  -", n)
                return 0

            cur.execute("select count(*) from semantic_observations")
            after_total = cur.fetchone()[0]
            cur.execute("select source_used, count(*) from semantic_observations"
                        " group by 1")
            after_by_src = dict(cur.fetchall())
            conn.commit()

            print(f"\nsemantic_observations: {before_total} -> {after_total}"
                  f" (inserted {after_total - before_total})")
            touched = {s for s in set(before_by_src) | set(after_by_src)
                       if before_by_src.get(s, 0) != after_by_src.get(s, 0)}
            print("sources changed: " + (", ".join(
                f"{s}:{before_by_src.get(s, 0)}->{after_by_src.get(s, 0)}"
                for s in sorted(touched)) or "NONE"))
            print(f"unexpected sources touched: "
                  f"{sorted(touched - set(extractors)) or 'NONE'}")
            if failed:
                print("\nFAILED UNITS:")
                for s, e in failed:
                    print(f"  - {s}: {e}")
            print("\nnotes:")
            for n in notes:
                print("  -", n)
    finally:
        conn.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
