#!/usr/bin/env python3
"""D-bucket judgement: what to do with sources that are too young to judge.

Change harden-crawl-delivery 3.6. The D bucket (67 sources on 2026-11-09) holds
units with fewer than ~5 successful runs in the lookback — not enough history to
declare a `min_rows_per_run` floor. Forcing a floor there manufactures false
positives (that is exactly what `declare-health-min-rows` had to walk back).

Instead each source lands in one of three columns, and only the first one is a
defect:

  stall                 declared data-period field exists and the watermark has
                        not advanced for >= 2 expected periods → real
  stable-unavailable    no watermark (no declared field / no cadence) but the
                        runs are steady and recent → judgeable only as "alive"
  history-insufficient  fewer than MIN_OK_RUNS successes → nothing to say yet

Read-only. Emits no tickets — it is a report for deciding what to do next.

Usage:
    python3 scripts/d_bucket_judgement.py --dsn "$FD_CRAWL_DB_URL"
    python3 scripts/d_bucket_judgement.py --dsn ... --json --min-ok-runs 5
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.health.watermark import judge, max_period  # noqa: E402

MIN_OK_RUNS = 5
#: 「运行稳定」= 最近一次成功在此周期数内且成功次数达阈。
STABLE_PERIODS = 2

STATS_SQL = """
select source,
       count(*) filter (where status = 'success')                        as ok_runs,
       max(started_at) filter (where status = 'success')                 as last_ok
from crawl_runs
where started_at > now() - interval '{days} days'
group by source
""".strip()

ITEMS_SQL = """
select cr.source, jsonb_agg(ci.payload)
from crawl_items ci join crawl_runs cr on cr.id = ci.run_id
where cr.id in (select max(id) from crawl_runs where status = 'success' group by source)
group by cr.source
""".strip()


def _psql(dsn: str, sql: str) -> list[list[str]]:
    proc = subprocess.run(["psql", dsn, "-At", "-F", "\t", "-c", sql],
                          capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()[:300]}")
    return [line.split("\t") for line in proc.stdout.splitlines() if line.strip()]


def manifests(repo: Path = REPO) -> dict[str, dict]:
    import yaml

    out: dict[str, dict] = {}
    for path in sorted((repo / "spiders").glob("*/manifest.yaml")):
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        out[path.parent.name] = {
            "data_period": str(data.get("data_period") or "").strip().lower() or None,
            "field": (data.get("data_period_field") or None),
            "schedule": str(data.get("schedule") or "").strip() or None,
        }
    return out


def classify_source(source: str, meta: dict, ok_runs: int, last_ok: datetime | None,
                    items: list | None, today: date,
                    min_ok_runs: int = MIN_OK_RUNS) -> dict:
    if ok_runs < min_ok_runs:
        return {"source": source, "column": "history-insufficient",
                "ok_runs": ok_runs, "detail": f"{ok_runs} < {min_ok_runs} successes"}

    field = meta.get("field")
    if field and items is not None:
        wm = max_period(items, field)
        verdict = judge(wm, meta.get("data_period"), today)
        if verdict["state"] == "stall":
            return {"source": source, "column": "stall", "ok_runs": ok_runs,
                    "watermark": verdict.get("period"),
                    "periods_behind": verdict.get("periods_behind"),
                    "detail": verdict.get("reason")}
        if verdict["state"] == "advance":
            return {"source": source, "column": "judgeable-ok", "ok_runs": ok_runs,
                    "watermark": verdict.get("period"),
                    "detail": verdict.get("reason")}

    # 没有可用水位：只能判「还活着」。要求最近一次成功仍然新鲜，
    # 否则它其实是停摆，不该落进「稳定但不可得」。
    if last_ok is None:
        return {"source": source, "column": "history-insufficient", "ok_runs": ok_runs,
                "detail": "no successful run in window"}
    age_days = (datetime.now(timezone.utc) - last_ok).total_seconds() / 86400
    period_days = {"day": 1, "month": 30.44, "year": 365.25}.get(meta.get("data_period") or "")
    # irregular（及其他没有节奏的）用一个固定的存活期判定「还活着」：没有周期
    # 不等于永远新鲜，也不等于永远陈旧。7 天是这类公告/事件源的常见最长静默。
    horizon_days = period_days * STABLE_PERIODS if period_days else 7.0
    fresh = age_days <= horizon_days
    return {
        "source": source,
        "column": "stable-unavailable" if fresh else "stale-unjudgeable",
        "ok_runs": ok_runs,
        "last_ok_age_days": round(age_days, 1),
        "horizon_days": round(horizon_days, 1),
        "detail": ("no watermark; runs steady" if fresh
                   else f"no watermark and last success {age_days:.1f}d old"),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", default=os.environ.get("FD_CRAWL_DB_URL", ""))
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--min-ok-runs", type=int, default=MIN_OK_RUNS)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv[1:])
    if not args.dsn:
        print("d_bucket_judgement: need --dsn or FD_CRAWL_DB_URL", file=sys.stderr)
        return 2
    min_ok_runs = args.min_ok_runs

    stats = {}
    for cols in _psql(args.dsn, STATS_SQL.format(days=args.days)):
        if len(cols) < 3 or not re.match(r"^[a-z0-9][a-z0-9._-]*$", cols[0].strip()):
            continue
        ok = int(cols[1] or 0)
        last = None
        if cols[2].strip():
            last = datetime.fromisoformat(cols[2].strip().replace(" ", "T"))
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
        stats[cols[0].strip()] = (ok, last)

    items = {}
    for cols in _psql(args.dsn, ITEMS_SQL):
        if len(cols) < 2:
            continue
        try:
            payload = json.loads(cols[1])
        except json.JSONDecodeError:
            continue
        items[cols[0].strip()] = payload if isinstance(payload, list) else []

    meta = manifests()
    today = date.today()
    rows = []
    for source, m in sorted(meta.items()):
        if not m.get("schedule"):
            continue  # 未点亮，不在判据范围内
        ok, last = stats.get(source, (0, None))
        rows.append(classify_source(source, m, ok, last, items.get(source), today,
                                    min_ok_runs))

    by_column: dict[str, list[dict]] = {}
    for row in rows:
        by_column.setdefault(row["column"], []).append(row)

    if args.json:
        print(json.dumps({"days": args.days, "min_ok_runs": min_ok_runs,
                          "columns": by_column}, ensure_ascii=False, indent=2))
        return 0

    print(f"D 桶判据（近 {args.days} 天，min_ok_runs={min_ok_runs}，共 {len(rows)} 个在册源）")
    for name in ("stall", "judgeable-ok", "stable-unavailable", "stale-unjudgeable",
                 "history-insufficient"):
        bucket = by_column.get(name) or []
        if not bucket and name == "judgeable-ok":
            continue
        print(f"\n{name}: {len(bucket)}")
        for row in sorted(bucket, key=lambda r: r["source"])[:15]:
            extra = row.get("watermark") or row.get("last_ok_age_days")
            print(f"  {row['source']:<28} ok={row['ok_runs']:<4} {extra if extra is not None else ''}")
        if len(bucket) > 15:
            print(f"  … 另有 {len(bucket) - 15} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
