#!/usr/bin/env python3
"""Schedule overlap simulator — 60-day minute-level concurrency check.

Methodology per socrata-cadence-alignment (2026-10-13) D3: real overlap is NOT
"same minute field" — it is the actual intersection of all five cron fields
(* / ranges / steps expanded) joined with the day-of-week/day-of-month filter,
plus each source's runtime duration window (a source occupies [start,
start+duration) after firing). Rough same-minute equality produced 10 false
alarms in the 76-source audit; this simulator reproduces the corrected
methodology.

Semantics for the banking batch gate (add-banking-industry-sources 7.1):
- ``duplicates``: any minute where two sources START in the same minute and at
  least one of them is in the NEW set → violation (spec: 与既有调度零重叠).
- ``window-overlaps``: runtime windows [start, start+duration) of two sources
  intersect and at least one is new → violation. Old-old overlaps are counted
  and reported but not violations (pre-existing, audited twice already).

Read-only over manifests; writes its JSON report only to the path given by
--out (default output/schedule-sim/report.json). Exits 1 on any violation
involving a new unit, 0 otherwise.

Usage:
  python3 scripts/schedule_overlap_check.py                       # all units
  python3 scripts/schedule_overlap_check.py \
      --new nfra-stats-xls,nfra-penalty,... \
      --durations durations.json   # {"slug": minutes}, optional overrides
  python3 scripts/schedule_overlap_check.py --days 60 --out /tmp/report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]

# Conservative duration model when no measurement is supplied: minutes a run
# occupies. Replace with measured first-crawl durations via --durations.
DEFAULT_DURATION_MIN = {"day": 15, "month": 30, "year": 60, "irregular": 15}
FALLBACK_DURATION_MIN = 15


def _expand_field(field: str, lo: int, hi: int) -> set[int]:
    """Expand one cron field (supports * , - / and names for dow)."""
    names = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
    out: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
        if part == "*":
            out.update(range(lo, hi + 1, step))
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a, b = int(names.get(a.lower(), a)), int(names.get(b.lower(), b))
            out.update(range(a, b + 1, step))
            continue
        v = int(names.get(part.lower(), part))
        if step != 1:  # "5/10" style
            out.update(range(v, hi + 1, step))
        else:
            out.add(v)
    return out


def _cron_matches(expr: str, dt: datetime) -> bool:
    minute, hour, dom, month, dow = expr.split()
    # Vixie cron: dom OR dow when both are restricted; our fleet uses only one.
    dom_all, dow_all = dom == "*", dow == "*"
    ok_dom = dt.day in _expand_field(dom, 1, 31)
    ok_dow = dt.weekday() in {d % 7 for d in _expand_field(dow, 0, 6)}
    day_ok = (ok_dom and ok_dow) if not (dom_all or dow_all) else (ok_dom and ok_dow)
    return (
        dt.minute in _expand_field(minute, 0, 59)
        and dt.hour in _expand_field(hour, 0, 23)
        and dt.month in _expand_field(month, 1, 12)
        and day_ok
    )


def load_schedules() -> list[dict]:
    units: list[dict] = []
    for mp in sorted((REPO / "spiders").glob("*/manifest.yaml")):
        doc = yaml.safe_load(mp.read_text()) or {}
        expr = str(doc.get("schedule") or "").strip()
        if not expr:
            continue
        units.append({
            "slug": mp.parent.name,
            "expr": " ".join(expr.split()),
            "data_period": doc.get("data_period") or "irregular",
        })
    return units


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--new", default="", help="comma-separated new slugs (empty = none)")
    ap.add_argument("--durations", default="", help="JSON file {slug: minutes}")
    ap.add_argument("--out", default=str(REPO / "output/schedule-sim/report.json"))
    args = ap.parse_args(argv[1:])

    new = {s.strip() for s in args.new.split(",") if s.strip()}
    durations: dict[str, int] = {}
    if args.durations:
        durations = json.loads(Path(args.durations).read_text())

    units = load_schedules()
    start = datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)
    # Simulate in the CronJob's cluster timezone (UTC for the runner chart);
    # all fleet schedules are written against that grid (fix-docker-schedule-grid).
    active: list[tuple[datetime, str]] = []  # (window_end, slug) running
    duplicates: list[dict] = []
    overlaps: list[dict] = []
    start_minutes = 0
    for minute_offset in range(args.days * 24 * 60):
        now = start + timedelta(minutes=minute_offset)
        fired = [u for u in units if _cron_matches(u["expr"], now)]
        start_minutes += len(fired)
        for u in fired:
            dur = durations.get(u["slug"],
                                DEFAULT_DURATION_MIN.get(u["data_period"],
                                                         FALLBACK_DURATION_MIN))
            # duplicate start check
            for other in fired:
                if other["slug"] <= u["slug"]:
                    continue
                if new & {u["slug"], other["slug"]}:
                    duplicates.append({"minute": now.isoformat(),
                                       "a": u["slug"], "b": other["slug"]})
            # running-window overlap check
            still = [w for w in active if w[0] > now]
            for w_end, w_slug in still:
                if w_slug == u["slug"]:
                    continue
                if new & {u["slug"], w_slug}:
                    overlaps.append({"start": now.isoformat(), "a": u["slug"],
                                     "b": w_slug})
            active = still + [(now + timedelta(minutes=dur), u["slug"]) for u in fired]

    viol = duplicates + overlaps
    report = {
        "days": args.days,
        "grid": "UTC (runner chart cluster timezone)",
        "units_total": len(units),
        "units_scheduled": len(units),
        "new_units": sorted(new),
        "start_events": start_minutes,
        "new_duplicates_same_minute": duplicates,
        "new_window_overlaps": overlaps,
        "duplicates": "NONE" if not viol else f"{len(viol)} violations",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"schedule_overlap_check: {len(units)} scheduled units, "
          f"{args.days}d minute-level sim, {start_minutes} start events, "
          f"new-involved duplicates={len(duplicates)}, "
          f"new-involved window overlaps={len(overlaps)}")
    print(f"report -> {out}")
    for v in viol[:10]:
        print("  violation:", v)
    return 1 if viol else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
