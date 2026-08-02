#!/usr/bin/env python3
"""Verify NBS GDP spider functionality.

Usage:
    cd /Users/chengsishi/finddata/fd-industry-data
    uv run python scripts/verify_nbs_gdp.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SPIDER_DIR = BASE_DIR / "spiders" / "nbs_gdp"
DB_PATH = SPIDER_DIR / "data" / "nbs_macro.db"
JSON_GDP_PATH = SPIDER_DIR / "output" / "nbs_gdp.json"
JSON_MACRO_PATH = SPIDER_DIR / "output" / "nbs_macro.json"


def check_files() -> bool:
    print("=== File Check ===")
    all_ok = True
    for path, label in [
        (SPIDER_DIR / "spider.py", "Spider module"),
        (BASE_DIR / "manifests" / "nbs-gdp.yaml", "Manifest YAML"),
    ]:
        exists = path.exists()
        status = "OK" if exists else "MISSING"
        print(f"  [{status}] {label}: {path}")
        if not exists:
            all_ok = False
    return all_ok


def check_sqlite() -> bool:
    print("\n=== SQLite Check ===")
    if not DB_PATH.exists():
        print(f"  [SKIP] Database not found: {DB_PATH}")
        print("  Run the spider first: uv run python spiders/nbs_gdp/spider.py")
        return False

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM nbs_macro")
    count = cur.fetchone()[0]
    print(f"  [OK] Total records: {count}")

    cur.execute("SELECT DISTINCT indicator_type FROM nbs_macro")
    types = [row[0] for row in cur.fetchall()]
    print(f"  [OK] Indicator types: {', '.join(types)}")

    cur.execute("SELECT DISTINCT source FROM nbs_macro")
    sources = [row[0] for row in cur.fetchall()]
    print(f"  [OK] Sources: {', '.join(sources)}")

    cur.execute("SELECT MIN(period), MAX(period) FROM nbs_macro WHERE indicator_type='gdp_quarterly'")
    row = cur.fetchone()
    if row[0]:
        print(f"  [OK] GDP period range: {row[0]} - {row[1]}")

    conn.close()
    return count > 0


def check_json() -> bool:
    print("\n=== JSON Check ===")
    ok = True
    for path, label in [(JSON_GDP_PATH, "GDP JSON"), (JSON_MACRO_PATH, "Macro JSON")]:
        if not path.exists():
            print(f"  [SKIP] {label} not found: {path}")
            ok = False
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        print(f"  [OK] {label}: {len(data)} records")
        if data:
            sample = data[0]
            print(f"       Sample keys: {', '.join(sample.keys())}")
    return ok


def run_spider() -> bool:
    print("\n=== Running Spider ===")
    sys.path.insert(0, str(SPIDER_DIR))
    try:
        from spider import get_macro_data
        results = get_macro_data(["gdp_quarterly"], start_year=2020)
        print(f"  [OK] Spider returned {len(results)} records")
        if results:
            sample = results[0]
            print(f"  [OK] Sample record: {json.dumps(sample, ensure_ascii=False, indent=2)}")
        return len(results) > 0
    except Exception as e:
        print(f"  [FAIL] Spider error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main() -> int:
    print("NBS GDP Spider Verification")
    print("=" * 60)

    check_files()
    spider_ok = run_spider()
    db_ok = check_sqlite()
    json_ok = check_json()

    print("\n" + "=" * 60)
    if spider_ok and db_ok:
        print("VERIFICATION PASSED")
        return 0
    else:
        print("VERIFICATION FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
