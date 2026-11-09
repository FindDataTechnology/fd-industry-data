#!/usr/bin/env python3
"""Onboarding funnel: landed → lit → fresh (candidates: unknown).

Change harden-crawl-delivery 4.4. Three of the four stages have a real source of
truth in this repo; the first one does not, and saying so is the point:

  candidates   scouting lives in a hand-written markdown table
               (``data-source-scouting/INDUSTRY-DATA-SOURCES.md``). No structured
               source exists, so this stage reports ``unknown`` rather than a
               number nobody can re-derive. See the doc for what would fix it.
  landed       ``spiders/<src>/manifest.yaml`` exists
  lit          landed **and** has a non-empty ``schedule`` (parked/disabled excluded)
  fresh        lit **and** ``cycles_since_last_good == 0`` from central telemetry

Read-only. The funnel answers "of what we landed, how much is actually
delivering" — the question that matters when deciding whether to add more
sources.

Usage:
    python3 scripts/onboarding_funnel.py --dsn "$FD_CRAWL_DB_URL"
    python3 scripts/onboarding_funnel.py --dsn ... --json
    python3 scripts/onboarding_funnel.py            # repo stages only (no DB)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.health.freshness import cycles_since_last_good  # noqa: E402

SCOUTING_DOC = "data-source-scouting/INDUSTRY-DATA-SOURCES.md"


def repo_stages(repo: Path = REPO) -> dict:
    import yaml

    landed, lit, stopped = [], [], []
    for path in sorted((repo / "spiders").glob("*/manifest.yaml")):
        src = path.parent.name
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        landed.append(src)
        # 停用与「有没有 schedule」正交：多数 parked 源根本没有 schedule 行，
        # 只统计「有 schedule 的停用源」会漏掉它们（实测漏 27 个）。
        if data.get("parked") is True or data.get("enabled") is False:
            stopped.append(src)
            continue
        scheduled = str(data.get("schedule") or "").strip()
        if not scheduled:
            continue
        lit.append({"source": src, "schedule": scheduled,
                    "schedule_tz": data.get("schedule_tz"),
                    "data_period": data.get("data_period")})
    return {"landed": landed, "lit": lit, "stopped": stopped}


def last_good(dsn: str) -> dict[str, str]:
    sql = ("select source, max(started_at) filter (where status = 'success') "
           "from crawl_runs group by source")
    proc = subprocess.run(["psql", dsn, "-At", "-F", "\t", "-c", sql],
                          capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()[:300]}")
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "\t" not in line:
            continue
        source, ts = line.split("\t", 1)
        if ts.strip():
            out[source.strip()] = ts.strip()
    return out


def _ts(value: str | None):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace(" ", "T"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def fresh_split(lit: list[dict], goods: dict[str, str], now: datetime) -> tuple[list, list, list]:
    """(fresh, at_risk, stale).

    ``fresh`` 是严格口径：**0 个应跑周期未成功**。只欠一期（``cycles == 1``）
    单列成 ``at_risk``——判据层把它当 trace 不出单（D2 阈值 2），漏斗里也不该
    混进「陈旧」，但也不该算「新鲜」。
    """
    fresh, at_risk, stale = [], [], []
    for entry in lit:
        src = entry["source"]
        result = cycles_since_last_good(
            schedule=entry["schedule"], tz=entry.get("schedule_tz"),
            last_good_at=_ts(goods.get(src)), now=now,
        )
        row = {"source": src, "cycles": result.cycles,
               **({"note": result.note} if result.note else {})}
        if result.cycles == 0:
            fresh.append(row)
        elif result.cycles == 1:
            at_risk.append(row)
        else:
            stale.append(row)
    return fresh, at_risk, stale


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", default=os.environ.get("FD_CRAWL_DB_URL", ""))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv[1:])

    stages = repo_stages()
    now = datetime.now(timezone.utc)
    if args.dsn:
        fresh, at_risk, stale = fresh_split(stages["lit"], last_good(args.dsn), now)
        measured = True
    else:
        fresh, at_risk, stale = [], [], []
        measured = False

    report = {
        "candidates": {
            "count": None,
            "state": "unknown",
            "because": (f"候选只存在于手写 markdown（{SCOUTING_DOC}）；"
                        "硬解析会得到不可复核的数，见 docs/onboarding-funnel.md"),
        },
        "landed": len(stages["landed"]),
        "lit": len(stages["lit"]),
        "stopped": len(stages["stopped"]),
        "fresh": len(fresh) if measured else None,
        "at_risk_one_cycle": len(at_risk) if measured else None,
        "stale": len(stale) if measured else None,
        "fresh_pct_of_lit": (round(100.0 * len(fresh) / len(stages["lit"]), 1)
                             if measured and stages["lit"] else None),
        "fresh_sources": [r["source"] for r in fresh],
        "at_risk_sources": [r["source"] for r in at_risk],
        "stale_sources": stale if measured else [],
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("接入漏斗")
    print(f"  candidates    unknown   （{SCOUTING_DOC} 无结构化来源）")
    print(f"  landed        {report['landed']:>7}")
    print(f"  lit           {report['lit']:>7}   （另有 {report['stopped']} 个停用/挂起）")
    if measured:
        print(f"  fresh         {report['fresh']:>7}   "
              f"= lit 的 {report['fresh_pct_of_lit']}%（严格口径：0 个应跑周期未成功）")
        print(f"  at-risk       {report['at_risk_one_cycle']:>7}   （只欠一期；判据层不出单）")
        if stale:
            print(f"  stale         {report['stale']:>7}   "
                  f"（{', '.join(s['source'] for s in stale[:8])}"
                  f"{' …' if len(stale) > 8 else ''}）")
    else:
        print("  fresh         unknown   （未给 --dsn，只看仓库侧）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
