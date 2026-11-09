#!/usr/bin/env python3
"""hotel-vertical wave 0 — backfill the EU accommodation/tourism series.

Same contract as ``scripts/extract_restaurant_wave0.py`` (see
``restaurant_extractors/_common.py`` for the write discipline): one module per
source unit exposing ``SOURCE`` / ``CONCEPTS`` / ``extract(cur, notes)``;
additive ``INSERT ... ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING``;
per-unit SAVEPOINT isolation; concepts born ``verified=false``.

Wave 0 has exactly one unit (``eurostat-api``) and it is the odd one: its four
hotel commands are **not scheduled**, so the extractor calls them live instead
of reading ``crawl_items``. Everything else is the standard discipline.

Usage::

    source ~/.config/finddata/dashboards.env
    python3 scripts/extract_hotel_wave0.py --dry-run
    python3 scripts/extract_hotel_wave0.py             # write (one txn)

A second run must insert zero rows (natural key + ON CONFLICT DO NOTHING).
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

from hotel_extractors._common import (  # noqa: E402
    ensure_unit_concepts,
    insert_rows,
)

PACKAGE = "hotel_extractors"

#: Series units this runner owns. Everything else in hotel_extractors/ is the
#: wave-3 company channels and keeps its own entry points (they read stored
#: crawl payloads; these either call a command surface or are pulled live).
SERIES_UNITS = ("eurostat-api", "mct-star-hotel", "mct-tourism-demand")


def load_extractors(wanted: set[str] | None = None) -> dict:
    """{source_name: module} for every series module in the extractor package."""
    pkg = importlib.import_module(PACKAGE)
    found = {}
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{PACKAGE}.{info.name}")
        if not hasattr(mod, "SOURCE") or not hasattr(mod, "extract"):
            continue
        if mod.SOURCE not in SERIES_UNITS:
            continue
        if wanted and mod.SOURCE not in wanted:
            continue
        found[mod.SOURCE] = mod
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default=None,
                    help="comma-separated series units (default: every implemented one)")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("DSN missing — source ~/.config/finddata/dashboards.env first")

    wanted = set(args.only.split(",")) if args.only else None
    if wanted:
        unknown = wanted - set(SERIES_UNITS)
        if unknown:
            raise SystemExit(f"unknown series units: {sorted(unknown)};"
                             f" known: {sorted(SERIES_UNITS)}")
    extractors = load_extractors(wanted)
    if not extractors:
        raise SystemExit("no series extractor selected")
    not_yet = sorted(set(SERIES_UNITS) - set(extractors))
    if not_yet:
        print(f"未实现（本波后续补）: {', '.join(not_yet)}")
    units = tuple(sorted(extractors))

    conn = psycopg2.connect(args.dsn)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM semantic_observations")
            before_total = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM semantic_observations"
                        " WHERE source_used = ANY(%s)", (list(units),))
            before_unit = cur.fetchone()[0]

            notes: list[str] = []
            per_unit: list[tuple[str, int, str]] = []
            failed: list[tuple[str, str]] = []
            new_concepts: set[str] = set()

            for source, mod in sorted(extractors.items()):
                if args.dry_run:
                    rows = mod.extract(cur, notes)
                    per_unit.append((source, len(rows), "dry-run"))
                    print(f"{source:<16} extracted {len(rows):>6} rows (dry-run)")
                    continue
                sp = "unit_" + source.replace("-", "_").replace(".", "_")
                cur.execute(f"SAVEPOINT {sp}")
                try:
                    rows = mod.extract(cur, notes)
                    codes = {r[0] for r in rows}
                    ids = ensure_unit_concepts(cur, source, mod.CONCEPTS, codes)
                    new_concepts |= codes - set(
                        c for c in codes if _already_verified(cur, c))
                    ins = insert_rows(cur, source, ids, rows)
                    per_unit.append((source, len(rows), f"inserted {ins}"))
                    print(f"{source:<16} extracted {len(rows):>6} inserted {ins:>6}")
                except Exception as exc:  # noqa: BLE001 — per-unit isolation
                    cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                    failed.append((source, str(exc)[:300]))
                    print(f"{source:<16} FAILED — rolled back: {exc}")
                cur.execute(f"RELEASE SAVEPOINT {sp}")

            if args.dry_run:
                total = sum(n for _s, n, _k in per_unit)
                print(f"\ndry-run total observation rows: {total}")
                print("\nnotes:")
                for n in notes:
                    print("  -", n)
                conn.rollback()
                return 0

            cur.execute("SELECT count(*) FROM semantic_observations")
            after_total = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM semantic_observations"
                        " WHERE source_used = ANY(%s)", (list(units),))
            after_unit = cur.fetchone()[0]
            conn.commit()

            print(f"\nsemantic_observations: {before_total} -> {after_total}"
                  f" (inserted {after_total - before_total})")
            print(f"source_used={'/'.join(units)}: {before_unit} -> {after_unit}")
            if failed:
                print("FAILED UNITS:")
                for s, e in failed:
                    print(f"  - {s}: {e}")

            cur.execute(
                "SELECT code, source FROM concepts WHERE verified = false"
                " AND deprecated = false AND code = ANY(%s) ORDER BY code",
                (sorted(new_concepts),),
            )
            pending = cur.fetchall()
            print(f"\nconcepts awaiting naming gate ({len(pending)}, verified=false):")
            for code, source in pending:
                print(f"  - [{source}] {code}")
            print("\nnotes:")
            for n in notes:
                print("  -", n)
    finally:
        conn.close()
    return 1 if failed else 0


def _already_verified(cur, code: str) -> bool:
    cur.execute("SELECT verified FROM concepts WHERE code = %s AND NOT deprecated",
                (code,))
    row = cur.fetchone()
    return bool(row and row[0])


if __name__ == "__main__":
    sys.exit(main())
