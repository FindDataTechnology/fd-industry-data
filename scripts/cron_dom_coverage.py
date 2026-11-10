#!/usr/bin/env python3
"""`schedule` 里写固定日号（29/30/31）的源，按**真实日历**展开一年，算出漏触发与误报率。

为什么需要它
------------
8 个源把 `schedule` 写成 `* 4 29 * *` / `* 4 30 * *` / `* 4 31 * *`。这些日子在某些
月份**不存在**，CronJob 不补偿——那个月一次都不跑，且没有任何信号。

口径（全部按真实日历，不用"每月 30 天"的名义日历）
--------------------------------------------------
* 时区：生产 CronJob 未声明 `timeZone`，按控制器本地 **CST** 解释（见
  `industry-cron-runs-in-cst-not-utc`）。日期是本地日期。
* dom 超过当月天数 → **该月 0 次触发**（Vixie cron 语义，不滚动到最后一天）。
* 误报率定义：把该源按"月频"判超期（容忍窗 = 336h × 2.5 = 840h = 35 天）时，
  一年里有多少天的"距上次触发"超过容忍窗。这些天巡检会误报"源静默/超期"。
  当前 `data_period: irregular` 使巡检直接跳过，所以现网误报为 0、**但检出也为 0**。

用法
----
    python3 scripts/cron_dom_coverage.py            # 表格
    python3 scripts/cron_dom_coverage.py --year 2027
    python3 scripts/cron_dom_coverage.py --json
"""

from __future__ import annotations

import argparse
import calendar
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
SPIDERS = REPO / "spiders"

#: `month` 频的容忍窗（小时）= 336 × 2.5，见 health.inspector.data_period_tolerance_hours
MONTH_TOLERANCE_DAYS = 35


def dom_schedules(spiders: Path = SPIDERS) -> list[tuple[str, str, int, str | None]]:
    """(source, cron, dom, data_period) —— 只取 dom 是固定数字且 >= 29 的。"""
    out: list[tuple[str, str, int, str | None]] = []
    for f in sorted(spiders.glob("*/manifest.yaml")):
        try:
            d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError):
            continue
        parts = str(d.get("schedule") or "").split()
        if len(parts) < 5 or not parts[2].isdigit():
            continue
        dom = int(parts[2])
        if dom >= 29:
            out.append((f.parent.name, " ".join(parts), dom, d.get("data_period")))
    return out


def expand_year(dom: int, year: int) -> list[date]:
    """该 cron 在 year 里真实触发的日期（每月一次，dom 不存在的月跳过）。"""
    out: list[date] = []
    for m in range(1, 13):
        if dom <= calendar.monthrange(year, m)[1]:
            out.append(date(year, m, dom))
    return out


def analyse(dom: int, year: int, tolerance_days: int = MONTH_TOLERANCE_DAYS) -> dict:
    """一年内的名额分布、最大间隔、以及按"月频"判超期时的误报天数。"""
    triggers = expand_year(dom, year)
    missing_months = [m for m in range(1, 13) if dom > calendar.monthrange(year, m)[1]]
    # 逐日算"距上次触发多少天"；年初无上次触发时按第一个触发日回推
    gaps_over: list[date] = []
    # 真实的最大间隔 = 相邻触发日之差的最大值。不要在逐日循环里取 max(since)：
    # 那样只会量到"触发日的前一天"（59 天间隔会报成 58）。
    max_gap = max(((b - a).days for a, b in zip(triggers, triggers[1:])), default=0)
    d = date(year, 1, 1)
    last = None
    trig_set = set(triggers)
    while d.year == year:
        if d in trig_set:
            # 当天触发 → 距上次归零，**且不能算超期**。
            # 早先版本在"判定超期"之后才更新 last，于是触发日本身也被计入
            # （dom=29 时 03-29 明明触发了，却因"距上次 59 天"被算成超窗），
            # 每个超窗间隔多算 1 天：dom=29 多 1、dom=31 多 5。
            last = d
            d += timedelta(days=1)
            continue
        if last is None:
            since = (triggers[0] - d).days if triggers and d < triggers[0] else 0
        else:
            since = (d - last).days
        if since > tolerance_days:
            gaps_over.append(d)
        d += timedelta(days=1)
    days_in_year = 366 if calendar.isleap(year) else 365
    return {
        "triggers": len(triggers),
        "missing_months": missing_months,
        "max_gap_days": max_gap,
        "days_over_tolerance": len(gaps_over),
        "days_in_year": days_in_year,
        "fp_rate": len(gaps_over) / days_in_year,
        "feb_triggers": 1 if dom <= calendar.monthrange(year, 2)[1] else 0,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--tolerance-days", type=int, default=MONTH_TOLERANCE_DAYS)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rows = []
    for source, cron, dom, period in dom_schedules():
        a = analyse(dom, args.year, args.tolerance_days)
        rows.append({"source": source, "cron": cron, "dom": dom,
                     "data_period": period, **a})

    if args.json:
        print(json.dumps({"year": args.year, "tolerance_days": args.tolerance_days,
                          "rows": rows}, ensure_ascii=False, indent=2))
        return 0

    print(f"{args.year} 年，按真实日历展开（容忍窗 {args.tolerance_days} 天 = month 频）\n")
    print(f"{'source':18} {'cron':14} {'dom':>3} {'触发/年':>7} {'2月':>4} "
          f"{'最大间隔':>8} {'超窗天数':>8} {'误报率':>7}")
    for r in sorted(rows, key=lambda x: (-x["dom"], x["source"])):
        print(f"{r['source']:18} {r['cron']:14} {r['dom']:>3} "
              f"{r['triggers']:>7} {r['feb_triggers']:>4} "
              f"{r['max_gap_days']:>7}d {r['days_over_tolerance']:>8} "
              f"{r['fp_rate']*100:>6.1f}%")
    worst = max(rows, key=lambda r: r["fp_rate"])
    print(f"\n最严重：{worst['source']}（dom={worst['dom']}）"
          f" 一年只触发 {worst['triggers']} 次，最大间隔 {worst['max_gap_days']} 天，"
          f"按 month 频判会有 {worst['fp_rate']*100:.1f}% 的天数误报")
    print("\n注：现网这 8 个源都是 `data_period: irregular`，巡检直接跳过超期判定——")
    print("    所以当前误报为 0，**但检出也为 0**（两个月没跑与一年跑两次在巡检面上同形）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
