#!/usr/bin/env python3
"""Onboarding funnel: candidates → landed → lit → fresh.

Change harden-crawl-delivery 4.4 established three stages with a real source of
truth and reported the first one as ``unknown`` rather than a number nobody could
re-derive. The structured candidate list (``data-source-scouting/candidates.json``,
built by ``data-source-scouting/scripts/extract_candidates.py``) now supplies that
first stage — and an **absent list still reports ``unknown``**, which stays the
honest degradation (missing input ≠ zero candidates):

  candidates   structured list + status distribution + landed-traceability
  landed       ``spiders/<src>/manifest.yaml`` exists
  lit          landed **and** has a non-empty ``schedule`` (parked/disabled excluded)
  fresh        lit **and** ``cycles_since_last_good == 0`` from central telemetry

Read-only. The funnel answers "of what we decided to acquire, how much is
actually delivering" — the question that matters when deciding whether to add
more sources.

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
# 侦察簿与清单住在 finddata 根（fd-industry-data 是其子目录），不在本仓内
CANDIDATES_JSON = REPO.parent / "data-source-scouting" / "candidates.json"


def candidates_stage(path: Path = CANDIDATES_JSON) -> dict:
    """漏斗第一段：读结构化候选清单。

    fail-honest：清单不存在 / schema 不符 / 内容解析失败，一律如实报 ``unknown``
    并带原因——**绝不退化成 0**。清单存在时给出总数、按状态分布，以及
    「已接但无法对到落仓单元」的计数（accepted_without_landing）——那是
    侦察簿不记落地出处的结构性缺口，漏斗应当让它可见而不是替它圆。
    """
    if not path.exists():
        return {
            "count": None, "state": "unknown",
            "because": f"结构化候选清单不存在：{path.name}"
                       f"（生成：data-source-scouting/scripts/extract_candidates.py）",
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"count": None, "state": "unknown",
                "because": f"候选清单不可解析：{type(exc).__name__}: {exc}"}
    if not isinstance(data, dict) or data.get("schema") != "source-candidates/v1":
        return {"count": None, "state": "unknown",
                "because": "候选清单 schema 不符（期望 source-candidates/v1）"}
    rows = data.get("candidates")
    if not isinstance(rows, list):
        return {"count": None, "state": "unknown", "because": "候选清单缺 candidates 数组"}

    by_status: dict[str, int] = {}
    for row in rows:
        status = row.get("status") if isinstance(row, dict) else None
        if not isinstance(status, str):
            status = "malformed"
        by_status[status] = by_status.get(status, 0) + 1
    accepted = [r for r in rows if isinstance(r, dict) and r.get("status") == "accepted"]
    return {
        "count": len(rows),
        "state": "ok",
        "by_status": by_status,
        "accepted": len(accepted),
        "accepted_with_landing": sum(1 for r in accepted if r.get("landed_slug")),
        "accepted_without_landing": sum(1 for r in accepted if not r.get("landed_slug")),
        "needs_review": sum(1 for r in rows
                            if isinstance(r, dict) and r.get("needs_review")),
        "source_doc_sha256": (data.get("meta") or {}).get("source_doc_sha256"),
        "coverage": (data.get("meta") or {}).get("coverage"),
    }


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
        "candidates": candidates_stage(),
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
    cands = report["candidates"]
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("接入漏斗")
    if cands.get("state") == "ok":
        dist = "，".join(f"{k}={v}" for k, v in sorted(cands["by_status"].items()))
        print(f"  candidates    {cands['count']:>7}   （{dist}）")
        print(f"    ├ 已接      {cands['accepted']:>7}   "
              f"（对到在产单元 {cands['accepted_with_landing']}，"
              f"无落地出处 {cands['accepted_without_landing']}）")
        print(f"    └ 待复核    {cands['needs_review']:>7}   （解析歧义条目）")
    else:
        print(f"  candidates    unknown   （{cands.get('because', '无结构化来源')}）")
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
