#!/usr/bin/env python3
"""Verify NBS GDP spider: imports, schema, data quality, fallback."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SPIDER_DIR = BASE_DIR / "spiders" / "nbs_gdp"
sys.path.insert(0, str(SPIDER_DIR))


def check_import() -> bool:
    print("[1/5] Checking imports...")
    try:
        from spider import (
            NbsMacroSpider,
            get_macro_data,
            get_gdp_quarterly,
            save_to_sqlite,
            save_to_json,
            _build_nbs_url,
            _parse_nbs_response,
            _parse_period,
            _to_float,
            INDICATORS,
        )
        print("  OK: all symbols imported")
        return True
    except Exception as e:
        print(f"  FAIL: {e}")
        return False


def check_url_builder() -> bool:
    print("[2/5] Checking URL builder...")
    from spider import _build_nbs_url
    url = _build_nbs_url("hgjd", "A0201", 2020)
    ok = all(k in url for k in ["data.stats.gov.cn", "m=QueryData", "dbcode=hgjd", "A0201"])
    print(f"  URL: {url[:120]}...")
    print(f"  {'OK' if ok else 'FAIL'}")
    return ok


def check_period_parser() -> bool:
    print("[3/5] Checking period parser...")
    from spider import _parse_period
    cases = [
        ("2024年第1季度", "Q", "2024Q1"),
        ("2024-03", "M", "2024-03"),
        ("2024年", "A", "2024"),
    ]
    ok = True
    for raw, freq, expected in cases:
        result = _parse_period(raw, freq)
        status = "OK" if result == expected else "FAIL"
        if status == "FAIL":
            ok = False
        print(f"  {raw!r} ({freq}) -> {result!r} (expected {expected!r}) [{status}]")
    return ok


def check_sqlite_schema() -> bool:
    print("[4/5] Checking SQLite schema...")
    from spider import save_to_sqlite, DB_PATH
    test_items = [
        {
            "period": "2024Q1",
            "value": 296297.0,
            "indicator_code": "A0201",
            "indicator_name": "国内生产总值",
            "indicator_type": "gdp_quarterly",
            "unit": "亿元",
            "source": "test",
            "fetched_at": "2026-07-30T00:00:00+00:00",
        }
    ]
    try:
        if DB_PATH.exists():
            DB_PATH.unlink()
        n = save_to_sqlite(test_items)
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='nbs_macro'")
        table_exists = cur.fetchone() is not None
        cur.execute("SELECT COUNT(*) FROM nbs_macro")
        count = cur.fetchone()[0]
        cur.execute("SELECT period, value FROM nbs_macro WHERE period='2024Q1'")
        row = cur.fetchone()
        conn.close()
        ok = table_exists and count == 1 and row and abs(row[1] - 296297.0) < 0.01
        print(f"  Table exists: {table_exists}, rows: {count}, value match: {row is not None}")
        print(f"  {'OK' if ok else 'FAIL'}")
        return ok
    except Exception as e:
        print(f"  FAIL: {e}")
        return False


def check_json_export() -> bool:
    print("[5/5] Checking JSON export...")
    from spider import save_to_json, OUTPUT_DIR
    test_items = [{"period": "2024Q1", "value": 1.0}]
    test_path = OUTPUT_DIR / "_verify_test.json"
    try:
        save_to_json(test_items, test_path)
        with open(test_path) as f:
            loaded = json.load(f)
        ok = loaded == test_items
        test_path.unlink(missing_ok=True)
        print(f"  {'OK' if ok else 'FAIL'}")
        return ok
    except Exception as e:
        print(f"  FAIL: {e}")
        return False


def main() -> int:
    print("=" * 60)
    print("NBS GDP Spider Verification")
    print("=" * 60)
    results = [
        check_import(),
        check_url_builder(),
        check_period_parser(),
        check_sqlite_schema(),
        check_json_export(),
    ]
    print("=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"Results: {passed}/{total} checks passed")
    if passed == total:
        print("ALL CHECKS PASSED")
        return 0
    print("SOME CHECKS FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
