#!/usr/bin/env python3
"""批次六排期选槽器（add-banking-industry-sources 任务 7.1 的配套工具）。

问题：60 天分钟级模拟里新单元与既有单元的运行窗口有 426 处相交（duplicates 已是 0，
但 window-overlaps>0）。根因不是"同一分钟启动"，而是既有单元按 data_period 占
15/30/60 分钟窗口，20 个新单元随手挑的分钟正好落在别人的窗口里。

纪律：**只移动"几点几分"，不动日集合语义**。每个 unit 保留它自己的
`dom / month / dow` 字段（发布日、星期几），只在时/分上搜索第一个与所有其它单元
（既有 + 已排定的新单元）零窗口相交的时点。搜索顺序按该单元数据可得时间窗优先。

用法：
  python3 scripts/batch6_schedule_fit.py --dry-run --durations reports/batch6-durations-YYYYMMDD.json
  python3 scripts/batch6_schedule_fit.py --apply   --durations reports/batch6-durations-YYYYMMDD.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from schedule_overlap_check import (  # noqa: E402
    DEFAULT_DURATION_MIN,
    FALLBACK_DURATION_MIN,
    _cron_matches,
    _expand_field,
    load_schedules,
)

REPO = Path(__file__).resolve().parents[1]
DAYS = 60
BASE = datetime(2026, 11, 8, tzinfo=timezone.utc)

BATCH6 = [
    "nfra-stats-xls", "nfra-penalty", "pboc-credit-funds", "pboc-loan-usage",
    "chinamoney-shibor", "chinamoney-lpr", "chinamoney-ncd-curve",
    "shclearing-monthly", "chinabond-monthly", "chinabond-yield-curve",
    "shclearing-capital-notes", "cba-wealth-index", "chinawealth-reports",
    "yindeng-npl-transfer", "eastmoney-disclosure-plan",
    "ratings-ccxi", "dagong-ratings", "lh-ratings", "dfjc-ratings", "xsj-ratings",
]

#: 候选时点的优先级（UTC 小时）。窗内找不到才放宽到全天。
PREFERRED_HOURS: dict[str, tuple[int, ...]] = {
    "nfra-stats-xls": (2, 3, 4, 5, 6),
    "nfra-penalty": (13, 14, 15, 16),
    "pboc-credit-funds": (11, 12, 13),
    "pboc-loan-usage": (11, 12, 13, 15),
    "chinamoney-shibor": (11, 12, 13, 14),
    "chinamoney-lpr": (11, 12, 13, 14),
    "chinamoney-ncd-curve": (12, 13, 14, 15),
    "shclearing-monthly": (13, 14, 15, 16),
    "chinabond-monthly": (13, 14, 15, 16),
    "chinabond-yield-curve": (13, 14, 15, 16),
    "shclearing-capital-notes": (13, 14, 15, 16),
    "cba-wealth-index": (14, 15, 16, 17),
    "chinawealth-reports": (14, 15, 16, 17),
    "yindeng-npl-transfer": (8, 9, 10, 11),
    "eastmoney-disclosure-plan": (10, 11, 12, 13),
    "ratings-ccxi": (9, 10, 11, 12),
    "dagong-ratings": (9, 10, 11, 12),
    "lh-ratings": (9, 10, 11, 12),
    "dfjc-ratings": (9, 10, 11, 12),
    "xsj-ratings": (9, 10, 11, 12),
}

SCHEDULE_RE = re.compile(r"^schedule:\s*\"([^\"]+)\"(.*)$", re.M)


def _duration(unit: dict, overrides: dict[str, int]) -> int:
    if unit["slug"] in overrides:
        return max(1, int(overrides[unit["slug"]]))
    return DEFAULT_DURATION_MIN.get(unit["data_period"], FALLBACK_DURATION_MIN)


def _matching_days(expr: str) -> list[int]:
    """该 cron 的日集合在 60 天网格里的 day-offset 列表（与时分无关）。"""
    minute, hour, dom, month, dow = expr.split()
    probe = f"0 0 {dom} {month} {dow}"
    out = []
    for day in range(DAYS):
        dt = BASE + timedelta(days=day)
        if _cron_matches(probe, dt):
            out.append(day)
    return out


def _fields(expr: str) -> tuple[set[int], set[int], list[int]]:
    """把 cron 拆成（分钟集, 小时集, 60 天内的 day-offset 列表）。"""
    minute, hour, dom, month, dow = expr.split()
    days = []
    probe = f"0 0 {dom} {month} {dow}"
    for day in range(DAYS):
        if _cron_matches(probe, BASE + timedelta(days=day)):
            days.append(day)
    return _expand_field(minute, 0, 59), _expand_field(hour, 0, 23), days


def _occupy(occ: list[list[bool]], expr: str, dur: int) -> None:
    minutes, hours, days = _fields(expr)
    for day in days:
        row = occ[day]
        for hour in hours:
            for minute in minutes:
                start = hour * 60 + minute
                for k in range(dur):
                    row[(start + k) % 1440] = True


def _fits(occ: list[list[bool]], expr: str, dur: int) -> bool:
    minutes, hours, days = _fields(expr)
    for day in days:
        row = occ[day]
        for hour in hours:
            for minute in minutes:
                start = hour * 60 + minute
                for k in range(dur):
                    if row[(start + k) % 1440]:
                        return False
    return True


def _candidates(slug: str) -> list[tuple[int, int]]:
    """候选 (hour, minute)：先语义窗，再全天。"""
    pref = PREFERRED_HOURS.get(slug, tuple(range(24)))
    out = [(h, m) for h in pref for m in range(60)]
    out += [(h, m) for h in range(24) if h not in pref for m in range(60)]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--durations", default="")
    args = ap.parse_args()

    overrides: dict[str, int] = {}
    if args.durations:
        overrides = json.loads(Path(args.durations).read_text())

    units = load_schedules()
    by_slug = {u["slug"]: u for u in units}
    old = [u for u in units if u["slug"] not in BATCH6]
    occ = [[False] * 1440 for _ in range(DAYS)]
    for unit in old:
        _occupy(occ, unit["expr"], _duration(unit, overrides))
    print(f"既有单元 {len(old)} 个；占用分钟点 {sum(sum(1 for v in r if v) for r in occ)}", flush=True)

    chosen: dict[str, str] = {}
    failures: list[str] = []
    for slug in BATCH6:
        unit = by_slug.get(slug)
        if not unit:
            failures.append(f"{slug}: manifest 缺失")
            continue
        minute_f, hour_f, dom, month, dow = unit["expr"].split()
        dur = _duration(unit, overrides)
        placed = None
        for hour, minute in _candidates(slug):
            expr = f"{minute} {hour} {dom} {month} {dow}"
            if _fits(occ, expr, dur):
                placed = expr
                break
        if not placed:
            failures.append(f"{slug}: 无可用槽（dur={dur}）")
            continue
        chosen[slug] = placed
        _occupy(occ, placed, dur)
        pref = PREFERRED_HOURS.get(slug, ())
        tag = "" if int(placed.split()[1]) in pref else "  (偏离语义窗)"
        print(f"  {slug:26s} {unit['expr']:22s} → {placed:22s} dur={dur}{tag}", flush=True)

    if failures:
        print("失败：" + "; ".join(failures), flush=True)
        return 1

    if args.apply:
        for slug, expr in chosen.items():
            mp = REPO / "spiders" / slug / "manifest.yaml"
            text = mp.read_text()
            new_text, n = SCHEDULE_RE.subn(lambda m: f'schedule: "{expr}"{m.group(2)}', text, count=1)
            if n != 1:
                print(f"!! {slug}: schedule 行未找到/不唯一", flush=True)
                return 1
            mp.write_text(new_text)
        print(f"已写入 {len(chosen)} 个 manifest", flush=True)
    else:
        print("dry-run：未写盘（加 --apply 落盘）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
