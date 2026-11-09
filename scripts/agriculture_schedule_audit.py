#!/usr/bin/env python3
"""农业线排期选槽与并发审计（add-agriculture-industry-sources 任务 10.3）。

问题：农业单元大多**尚未点亮**（manifest 无 `schedule`），所以「与既有调度零重叠」
不能靠读 manifest 证明——必须把它们**打算用的**时点放进全仓 60 天分钟级模拟里试。

纪律：
* **只搜「几点几分」，不改编排语义**（日/月/星期 字段保留该源的数据可得窗）。
* 不写任何 manifest（点亮是独立批次）。候选与选槽结果只落审计报告。
* 已点亮的农业单元（`moa-hog-price` / `usda-psd`）按 manifest 现状参与模拟；它们
  与既有单元的相交**如实记账但不判本批违规**（老-老重叠口径同
  `schedule_overlap_check.py`）。
* 源已停更（cncotton）或有意挂起（wfp-food-price, parked:true）的单元**不给候选
  时点**——给它们排期等于把「源不发布」伪装成「调度在跑」。

算法：把「既有排期占用的分钟」一次算成集合（起始分钟集 + 运行窗集），候选只需
O(窗口长度) 查询，不再每个候选重跑整窗模拟（首版每候选重算 60 天，跑不完）。

复用 `schedule_overlap_check.py` 的 `_cron_matches` / `DEFAULT_DURATION_MIN` /
`load_schedules()`，不重写 cron 语义。

用法::

    python3 scripts/agriculture_schedule_audit.py --days 60 \\
        --out output/schedule-sim/agriculture-2026-11-08.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from schedule_overlap_check import (  # noqa: E402
    DEFAULT_DURATION_MIN,
    FALLBACK_DURATION_MIN,
    _cron_matches,
    load_schedules,
)

REPO = Path(__file__).resolve().parents[1]
GRID = "UTC (runner chart cluster timezone)"
START = datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)

#: 已点亮：按 manifest 现状参与模拟（不从本表取 schedule）。
LIT = ["moa-hog-price", "usda-psd"]

#: 待点亮候选。**日/月/星期 保留数据可得窗**，其中月频源的真实自由度是
#: 「发布日之后的任意一天」——把数据放一天再取不改变口径，故用 `dom_range`
#: 表达（首版写 `*` 等于天天跑、写死单日又会被中旬的密集排期堵死，两次都是错的）。
CANDIDATES: list[dict] = [
    {"slug": "agri-casde", "data_period": "month",
     "dom_range": (13, 28), "month": "*", "dow": "*",
     "anchor": "CASDE 每月 9—12 日发布 → 13 日之后任一天取"},
    {"slug": "moa-fishery", "data_period": "month",
     "dom_range": (20, 28), "month": "*", "dow": "*",
     "anchor": "渔业局月度产量统计次月中下旬发布 → 20 日之后任一天取"},
    {"slug": "usda-wasde", "data_period": "month",
     "dom_range": (13, 28), "month": "*", "dow": "*",
     "anchor": "USDA WASDE 每月 9—12 日 12:00 ET 发布 → 13 日之后任一天取"},
    {"slug": "moa-variety-approval", "data_period": "irregular",
     "dom_range": None, "dom": "*", "month": "*", "dow": "2",
     "anchor": "品种审定公告不定期，取每周二巡检一次"},
    {"slug": "moa-machinery", "data_period": "year",
     "dom_range": None, "dom": "1", "month": "3", "dow": "*",
     "anchor": "农机年度公报次年一季度发布 → 3 月 1 日起取"},
    {"slug": "fert-cn", "data_period": "month",
     "dom_range": (21, 28), "month": "*", "dow": "*",
     "anchor": "海关月度进出口数据次月中下旬发布 → 21 日之后任一天取"},
]

#: 不给候选的单元与理由（必须留名，不能静默缺席）。
NO_SLOT = {
    "cncotton": "站点已停更（末值 2025-08、434 天未更新），排期等于把「源不发布」伪装成「调度在跑」",
    "wfp-food-price": "manifest parked:true（有意挂起，等 HDX 数据面稳定后再议）",
}


def _duration(unit: dict) -> int:
    return DEFAULT_DURATION_MIN.get(unit["data_period"], FALLBACK_DURATION_MIN)


def _occupy(units: list[dict], days: int) -> tuple[set[int], set[int]]:
    """返回 (起始分钟集, 运行窗分钟集)，单位=自 START 起的分钟偏移。"""
    starts: set[int] = set()
    busy: set[int] = set()
    total = days * 24 * 60
    for unit in units:
        dur = _duration(unit)
        expr = unit["expr"]
        for off in range(total):
            now = START + timedelta(minutes=off)
            if _cron_matches(expr, now):
                starts.add(off)
                busy.update(range(off, min(total, off + dur)))
    return starts, busy


def _fire_offsets(expr: str, days: int) -> list[int]:
    total = days * 24 * 60
    out = []
    for off in range(total):
        if _cron_matches(expr, START + timedelta(minutes=off)):
            out.append(off)
    return out


def _fire_offsets_fast(dom: str, month: str, dow: str,
                       hour: int, minute: int, days: int) -> list[int]:
    """按日直接算触发分钟，不扫全窗（候选搜索用；语义等价于五字段展开）。"""
    dows = None
    if dow != "*":
        names = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
        dows = {names.get(x.strip().lower(), x.strip()) for x in dow.split(",")}
    out: list[int] = []
    for day in range(days):
        now = START + timedelta(days=day)
        if month != "*" and str(now.month) not in {x.strip() for x in month.split(",")}:
            continue
        if dows is not None and str(now.isoweekday() % 7) not in dows:
            continue
        if dom != "*" and str(now.day) not in {x.strip() for x in dom.split(",")}:
            continue
        out.append(day * 1440 + hour * 60 + minute)
    return out


def _collides_fast(dom: str, month: str, dow: str, hour: int, minute: int,
                   dur: int, days: int, starts: set[int], busy: set[int]) -> bool:
    total = days * 24 * 60
    for off in _fire_offsets_fast(dom, month, dow, hour, minute, days):
        if off in starts:
            return True
        if any(m in busy for m in range(off, min(total, off + dur))):
            return True
    return False


def _collides(expr: str, dur: int, days: int,
              starts: set[int], busy: set[int]) -> list[dict]:
    hits: list[dict] = []
    for off in _fire_offsets(expr, days):
        if off in starts:
            hits.append({"minute": (START + timedelta(minutes=off)).isoformat(),
                         "kind": "same-minute-start"})
        window = set(range(off, min(days * 24 * 60, off + dur)))
        if window & busy:
            hits.append({"minute": (START + timedelta(minutes=off)).isoformat(),
                         "kind": "window-overlap"})
    return hits


def _proposed_expr(c: dict, dom: str, hour: int, minute: int) -> str:
    month = c.get("month", "*")
    dow = c.get("dow", "*")
    return f"{minute} {hour} {dom} {month} {dow}"


def _fit(units_occ, c: dict, days: int, dur_override: int = 0) -> dict:
    """在数据可得窗内搜零重叠时点：月频先搜日期（发布日起），再搜时/分。"""
    starts, busy = units_occ
    dur = dur_override or _duration(c)
    doms = ([] if c.get("dom_range") is None
            else [str(d) for d in range(c["dom_range"][0], c["dom_range"][1] + 1)])
    if not doms:
        doms = [c.get("dom", "*")]
    tried = 0
    for dom in doms:
        for hour in range(24):
            for minute in range(60):
                tried += 1
                if not _collides_fast(dom, c.get("month", "*"), c.get("dow", "*"),
                                      hour, minute, dur, days, starts, busy):
                    expr = _proposed_expr(c, dom, hour, minute)
                    return {"slug": c["slug"], "expr": expr, "tried": tried,
                            "status": "free", "anchor": c["anchor"],
                            "duration_min_model": dur}
    return {"slug": c["slug"], "expr": "", "tried": tried,
            "status": "blocked", "anchor": c["anchor"],
            "duration_min_model": dur,
            "reason": f"可得窗内无零重叠时点（{days} 天窗，时长模型 {dur} 分钟）"}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--candidate-duration-min", type=int, default=0,
                    help="候选单元的运行时长（分钟）。0=沿用 DEFAULT_DURATION_MIN"
                         "（月频 30 分钟的保守上界）；给了就用这个值，须在报告里标明来源")
    ap.add_argument("--out", default=str(
        REPO / "output/schedule-sim/agriculture-audit.json"))
    args = ap.parse_args(argv)

    days = args.days
    units = load_schedules()
    base_slugs = {u["slug"] for u in units}
    missing_lit = [s for s in LIT if s not in base_slugs]
    if missing_lit:
        raise SystemExit(f"FATAL 已点亮单元不在排期面：{missing_lit}")

    starts, busy = _occupy(units, days)
    print(f"排期面：既有 {len(units)} 个单元 · {days} 天 · {GRID}")
    print(f"占用：起始分钟 {len(starts)} 个 · 运行窗分钟 {len(busy)} 个"
          f"（占全窗 {len(busy) / (days * 24 * 60):.1%}）")

    # ① 已点亮农业单元与既有调度的相交（如实记账，不判本批违规）
    lit_report = {}
    for slug in LIT:
        unit = next(u for u in units if u["slug"] == slug)
        others = [u for u in units if u["slug"] != slug]
        o_starts, o_busy = _occupy(others, days)
        hits = _collides(unit["expr"], _duration(unit), days, o_starts, o_busy)
        lit_report[slug] = {"expr": unit["expr"], "hits": hits[:10],
                            "hit_count": len(hits)}
        print(f"\n已点亮 {slug:<16} {unit['expr']:<14} 与既有相交 {len(hits)} 处"
              f"（老-老口径，只记账）")

    # ② 候选选槽：选一个就并入占用集，后续候选自动避让（含新-新）
    fitted: list[dict] = []
    for c in CANDIDATES:
        r = _fit((starts, busy), c, days, args.candidate_duration_min)
        fitted.append(r)
        if r["status"] == "free":
            for off in _fire_offsets(r["expr"], days):
                starts.add(off)
                busy.update(range(off, min(days * 24 * 60,
                                           off + r["duration_min_model"])))
            print(f"  ✓ {c['slug']:<22} {r['expr']:<14} (试 {r['tried']} 个时点)")
        else:
            print(f"  ✗ {c['slug']:<22} 无空位：{r['reason']}")

    for slug, why in NO_SLOT.items():
        print(f"  – {slug:<22} 不给时点：{why}")

    # ③ 整批复核：把已选候选单独并入一份占用集，逐条检查零相交
    base_starts, base_busy = _occupy(units, days)
    final: dict[str, int] = {}
    for r in fitted:
        if r["status"] != "free":
            continue
        hits = _collides(r["expr"], args.candidate_duration_min or _duration(
                             next(c for c in CANDIDATES if c["slug"] == r["slug"])),
                         days, base_starts, base_busy)
        final[r["slug"]] = len(hits)
        # 并入，使新-新也在复核范围内
        for off in _fire_offsets(r["expr"], days):
            base_starts.add(off)
            dur = args.candidate_duration_min or _duration(
                next(c for c in CANDIDATES if c["slug"] == r["slug"]))
            base_busy.update(range(off, min(days * 24 * 60, off + dur)))

    violations = sum(final.values())
    blocked = [r["slug"] for r in fitted if r["status"] != "free"]

    report = {
        "generated_at": "2026-11-08",
        "days": days,
        "grid": GRID,
        "method": "五字段 cron 展开 × 时长窗 [start, start+duration)，只移动时/分与发布日期；"
                  "起始分钟集与运行窗集一次预计算后逐候选查询",
        "candidate_duration_model": (f"{args.candidate_duration_min} 分钟（显式给定）"
                                     if args.candidate_duration_min else
                                     "DEFAULT_DURATION_MIN（月频 30 分钟的保守上界）"),
        "units_scheduled_before": len(units),
        "units_scheduled_after": len(units) + len([r for r in fitted if r["status"] == "free"]),
        "lit_units": LIT,
        "lit_current_collisions": lit_report,
        "fitted_slots": fitted,
        "no_slot": NO_SLOT,
        "final_check": {"per_slot_hits": final, "violations": violations,
                        "blocked": blocked},
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n整批复核：新选时点与既有/新-新相交 {violations} 处"
          f" → {'PASS 零重叠' if violations == 0 and not blocked else 'FAIL'}")
    print(f"审计报告：{out}")
    return 0 if (violations == 0 and not blocked) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
