#!/usr/bin/env python3
"""check_supply_ledger.py — supply-funnel 处置台账一致性校验（stdlib-only）。

校验三件事：
1. 在产名单与台账 units 一一对应——缺席（在产但台账没有）与多出（台账有但不在产）都报错并列明细；
   在产口径 = spiders/*/manifest.yaml 中存在以 ``schedule:`` 开头的行（与
   ``grep -l '^schedule:' fd-industry-data/spiders/*/manifest.yaml`` 等价，含 schedule: null）。
2. 台账结构合法——每个 unit 必填 disposition/evidence/note/decided_by/decided_at，
   disposition ∈ {trend, short_window, exclude, disclose_only, pending}。
3. counts 汇总（若存在）与逐单元实际计数一致。

退出码：0 = 一致；1 = 不一致或结构错误。
"""
import json
import os
import sys

VALID_DISPOSITIONS = {"trend", "short_window", "exclude", "disclose_only", "pending"}
REQUIRED_FIELDS = ("disposition", "evidence", "note", "decided_by", "decided_at")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPIDERS_DIR = os.path.join(BASE_DIR, "spiders")
DEFAULT_LEDGER = os.path.join(BASE_DIR, "output", "supply-funnel", "dispositions.json")


def active_units(spiders_dir):
    """在产单元集合：manifest 含以 'schedule:' 开头的行（含 schedule: null）。"""
    units = set()
    if not os.path.isdir(spiders_dir):
        return units
    for name in sorted(os.listdir(spiders_dir)):
        manifest = os.path.join(spiders_dir, name, "manifest.yaml")
        if not os.path.isfile(manifest):
            continue
        with open(manifest, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("schedule:"):
                    units.add(name)
                    break
    return units


def main():
    ledger_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LEDGER
    errors = []

    try:
        with open(ledger_path, "r", encoding="utf-8") as fh:
            ledger = json.load(fh)
    except (OSError, ValueError) as exc:
        print("FAIL: cannot read ledger {}: {}".format(ledger_path, exc))
        return 1

    units = ledger.get("units")
    if not isinstance(units, dict) or not units:
        print("FAIL: ledger has no non-empty 'units' object")
        return 1

    # 结构校验
    for name, entry in sorted(units.items()):
        if not isinstance(entry, dict):
            errors.append("structure: unit {!r}: entry is not an object".format(name))
            continue
        for field in REQUIRED_FIELDS:
            if field not in entry or entry[field] in (None, ""):
                errors.append(
                    "structure: unit {!r}: missing/empty required field {!r}".format(name, field)
                )
        disp = entry.get("disposition")
        if disp not in VALID_DISPOSITIONS:
            errors.append(
                "structure: unit {!r}: invalid disposition {!r} (must be one of {})".format(
                    name, disp, sorted(VALID_DISPOSITIONS)
                )
            )

    # 在产名单 vs 台账 一一对应
    production = active_units(SPIDERS_DIR)
    if not production:
        errors.append("production: no scheduled manifests found under {}".format(SPIDERS_DIR))

    ledger_names = set(units)
    missing = sorted(production - ledger_names)   # 在产但台账缺席
    extra = sorted(ledger_names - production)     # 台账有但不在产

    if missing:
        errors.append(
            "coverage: {} unit(s) in production but MISSING from ledger:\n  - ".format(len(missing))
            + "\n  - ".join(missing)
        )
    if extra:
        errors.append(
            "coverage: {} unit(s) in ledger but NOT in production:\n  - ".format(len(extra))
            + "\n  - ".join(extra)
        )

    # counts 汇总一致性
    counts = ledger.get("counts")
    if isinstance(counts, dict):
        actual = {}
        for entry in units.values():
            if isinstance(entry, dict):
                disp = entry.get("disposition")
                actual[disp] = actual.get(disp, 0) + 1
        non_disp_keys = set(counts) - VALID_DISPOSITIONS
        for key in sorted(non_disp_keys):
            if key != "total":
                errors.append("counts: unexpected non-disposition key {!r}".format(key))
        for disp in sorted(VALID_DISPOSITIONS):
            declared = counts.get(disp)
            observed = actual.get(disp, 0)
            if declared != observed:
                errors.append(
                    "counts: disposition {!r}: ledger says {}, actual {}".format(
                        disp, declared, observed
                    )
                )
        if counts.get("total") != len(units):
            errors.append(
                "counts: ledger says total={}, actual {}".format(counts.get("total"), len(units))
            )
    elif "counts" in ledger:
        errors.append("structure: 'counts' present but not an object")

    # 汇报
    print("ledger: {}".format(ledger_path))
    print("production units (spiders/* with ^schedule:): {}".format(len(production)))
    print("ledger units: {}".format(len(units)))
    tally = {}
    for entry in units.values():
        if isinstance(entry, dict):
            tally[entry.get("disposition")] = tally.get(entry.get("disposition"), 0) + 1
    for disp in sorted(VALID_DISPOSITIONS):
        print("  {:<14} {}".format(disp + ":", tally.get(disp, 0)))

    if errors:
        print("\nFAIL: {} problem(s):".format(len(errors)))
        for err in errors:
            print(" - " + err)
        return 1

    print("\nOK: ledger matches production unit set; structure and counts valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
