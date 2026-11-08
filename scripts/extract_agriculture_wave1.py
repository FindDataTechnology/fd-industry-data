#!/usr/bin/env python3
"""Agricultural vertical wave-1 — run every extractor under scripts/agriculture_extractors/.

Same shape as scripts/extract_restaurant_wave0.py (per-unit SAVEPOINT isolation,
additive inserts, concepts born verified=false, notes for every skip/drop).

Difference: agriculture W1 units are **not deployed yet**, so the extractors read
data through ``live_items`` — they run the unit's entry function in-process
(LOCAL LIVE BACKFILL, stated in notes) instead of reading crawl_items payloads.
Every written observation still carries the real ``source_used``.

Usage:

    source ~/.config/finddata/dashboards.env
    python3 scripts/extract_agriculture_wave1.py --dry-run          # read-only counts
    python3 scripts/extract_agriculture_wave1.py                    # write (one txn)
    python3 scripts/extract_agriculture_wave1.py --only agri-casde,cncotton
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

from agriculture_extractors._common import (  # noqa: E402
    ensure_unit_concepts,
    insert_rows,
)

PACKAGE = "agriculture_extractors"


def load_extractors() -> dict:
    pkg = importlib.import_module(PACKAGE)
    found = {}
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{PACKAGE}.{info.name}")
        if not hasattr(mod, "SOURCE") or not hasattr(mod, "extract"):
            continue
        found[mod.SOURCE] = mod
    return found


def _already_verified(cur, code: str) -> bool:
    cur.execute("SELECT verified FROM concepts WHERE code = %s AND NOT deprecated", (code,))
    row = cur.fetchone()
    return bool(row and row[0])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default=None, help="comma-separated source names")
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
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM semantic_observations")
            before_total = cur.fetchone()[0]
            cur.execute("SELECT source_used, count(*) FROM semantic_observations GROUP BY 1")
            before_by_src = dict(cur.fetchall())

            notes: list[str] = []
            per_unit: list[tuple[str, int, str]] = []
            failed: list[tuple[str, str]] = []
            new_concepts: set[str] = set()

            for source, mod in sorted(extractors.items()):
                if wanted and source not in wanted:
                    continue
                if args.dry_run:
                    # dry-run 也按单元隔离：一个单元的站点抖动不该吞掉其余单元的
                    # 盘点结果（写库路径有 SAVEPOINT，只读路径此前是裸调用）
                    sp = "unit_" + source.replace("-", "_").replace(".", "_")
                    cur.execute(f"SAVEPOINT {sp}")
                    try:
                        rows = mod.extract(cur, notes)
                        per_unit.append((source, len(rows), "dry-run"))
                        print(f"{source:<18} extracted {len(rows):>6} rows (dry-run)")
                    except Exception as exc:  # noqa: BLE001 — per-unit isolation
                        cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                        failed.append((source, str(exc)[:300]))
                        print(f"{source:<18} FAILED (dry-run): {exc}")
                    cur.execute(f"RELEASE SAVEPOINT {sp}")
                    continue
                sp = "unit_" + source.replace("-", "_").replace(".", "_")
                cur.execute(f"SAVEPOINT {sp}")
                try:
                    rows = mod.extract(cur, notes)
                    codes = {r[0] for r in rows}
                    ids = ensure_unit_concepts(cur, source, mod.CONCEPTS, codes)
                    new_concepts |= codes - {c for c in codes if _already_verified(cur, c)}
                    ins = insert_rows(cur, source, ids, rows)
                    per_unit.append((source, len(rows), f"inserted {ins}"))
                    print(f"{source:<18} extracted {len(rows):>6} inserted {ins:>6}")
                except Exception as exc:  # noqa: BLE001 — per-unit isolation
                    cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                    failed.append((source, str(exc)[:300]))
                    print(f"{source:<18} FAILED — rolled back: {exc}")
                cur.execute(f"RELEASE SAVEPOINT {sp}")

            if args.dry_run:
                print(f"\ndry-run total observation rows: {sum(n for _s, n, _k in per_unit)}")
                print("\nnotes:")
                for n in notes:
                    print("  -", n)
                conn.rollback()
                return 0

            cur.execute("SELECT count(*) FROM semantic_observations")
            after_total = cur.fetchone()[0]
            cur.execute("SELECT source_used, count(*) FROM semantic_observations GROUP BY 1")
            after_by_src = dict(cur.fetchall())
            conn.commit()

            print(f"\nsemantic_observations: {before_total} -> {after_total}"
                  f" (inserted {after_total - before_total})")
            if failed:
                print("FAILED UNITS:")
                for s, e in failed:
                    print(f"  - {s}: {e}")
            touched = {s for s in set(before_by_src) | set(after_by_src)
                       if before_by_src.get(s, 0) != after_by_src.get(s, 0)}
            print("sources changed: "
                  + (", ".join(f"{s}:{before_by_src.get(s, 0)}->{after_by_src.get(s, 0)}"
                               for s in sorted(touched)) or "NONE"))
            print(f"unexpected sources touched: "
                  f"{sorted(touched - set(extractors)) or 'NONE'}")

            cur.execute(
                "SELECT code, source FROM concepts WHERE verified = false"
                " AND deprecated = false AND code = ANY(%s) ORDER BY source, code",
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


if __name__ == "__main__":
    sys.exit(main())
