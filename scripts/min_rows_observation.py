#!/usr/bin/env python3
"""一周误报率观察表：每天巡检后记一行。

为什么需要它
------------
`min_rows_per_run` 的阈值是按 90 天历史反解出来的，但"历史反算"不等于"未来不误报"：
分布会漂。所以需要一段真实运行期的记录，且**每天记的不是感觉，是两个可对的数**：

* **rule1c 出单源**：当天因为"行数不足"出的单（工单 `evidence.notes` 命中 `rule1c`）；
* **超窗天数**：每个已声明源"距最近一次跑到的成功"超出其容忍窗多少天
  （0 = 未超窗）。它回答的是"这条源到底是没跑，还是跑了但产出少"。

判读口径（重要，别把它读成误报率本身）
--------------------------------------
* 某源当天出了 rule1c 单、但**超窗天数 = 0** → 它跑了，只是行数低。这是候选误报，
  要看它是否很快恢复；
* 某源超窗天数 > 0 → 它压根没跑（或没跑成），此时"行数不足"是次要症状，
  真问题是调度/抓取没发生——不算阈值误报；
* 「出单后又达标」**不等于**误报：可能是阈值太敏感，也可能真低产后修复或上游恢复。

用法
----
    python3 scripts/min_rows_observation.py --snapshot /tmp/tel.json
    python3 scripts/min_rows_observation.py --snapshot /tmp/tel.json --append docs/min-rows-observation-log.md
    FD_CENTRAL_PG_DSN=... python3 scripts/min_rows_observation.py --from-pg
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TICKETS = REPO / "reports" / "health-tickets"
DEFAULT_LOG = REPO / "docs" / "min-rows-observation-log.md"
CST = timezone(timedelta(hours=8))


def _parse(s: object) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(s).replace(" ", "T"))
    except (ValueError, TypeError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def rule1c_tickets(outdir: Path, day: str) -> list[str]:
    """当天落盘、且判词是 rule1c 的工单源（去重、排序）。"""
    import yaml

    out: set[str] = set()
    for p in sorted(outdir.glob(f"{day.replace('-', '')}-*.yaml")):
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError):
            continue
        notes = str(((doc.get("evidence") or {}).get("notes")) or "")
        if "rule1c" in notes:
            out.add(str(doc.get("source") or "?"))
    return sorted(out)


def over_window_days(records: list[dict], floors: dict[str, int],
                     now: datetime, tolerance_hours: float) -> dict[str, int]:
    """每个已声明源超窗多少天（距最近一次"跑到" > 容忍窗的部分，取整）。"""
    last: dict[str, datetime] = {}
    for r in records:
        if r.get("status") not in ("success", "partial"):
            continue
        at = _parse(r.get("started_at"))
        if at is None:
            continue
        src = str(r.get("source"))
        if src not in last or at > last[src]:
            last[src] = at
    out: dict[str, int] = {}
    for src in floors:
        at = last.get(src)
        if at is None:
            continue
        over = (now - at).total_seconds() / 86400 - tolerance_hours / 24
        if over > 0:
            out[src] = int(over)
    return out


def build_row(day: str, healthy: int | None, tickets: list[str],
              over: dict[str, int], window: str) -> str:
    t_src = ", ".join(f"`{s}`" for s in tickets) or "—"
    o_src = ", ".join(f"`{s}`:{d}" for s, d in sorted(over.items(), key=lambda kv: -kv[1])) or "—"
    return (f"| {day} | {healthy if healthy is not None else '—'} | {len(tickets)} | "
            f"{t_src} | {o_src} | {window} |")


HEADER = """# 行数下限 · 一周误报率观察表

每天巡检后追加一行。判读口径见 `scripts/min_rows_observation.py` 的文档字符串。

**读表要点**：出 rule1c 单且**超窗天数 = 0** 的源，才是"跑了但产出少"的候选误报；
超窗 > 0 的源是"压根没跑"，真因在调度/抓取，不是阈值。

| 日期 | healthy | rule1c 单数 | 出单源 | 超窗天数（源:天） | 容忍窗 |
|---|---|---|---|---|---|
"""


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", help="遥测快照 JSON")
    ap.add_argument("--from-pg", action="store_true", help="读中央库（只读）")
    ap.add_argument("--tickets", default=str(TICKETS), help="工单目录")
    ap.add_argument("--day", help="观察日（YYYY-MM-DD，默认今天 CST）")
    ap.add_argument("--append", nargs="?", const=str(DEFAULT_LOG),
                    help="追加到观察表（默认 docs/min-rows-observation-log.md）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    import os
    from fd_industry_data.health import inspector
    from fd_industry_data.health.config import load_config

    now = datetime.now(CST)
    day = args.day or now.strftime("%Y-%m-%d")

    if args.snapshot:
        records = inspector.load_runs_from_snapshot(args.snapshot)
    elif args.from_pg:
        records = inspector.load_runs_from_psql(load_config(None),
                                                os.environ.get("FD_CENTRAL_PG_DSN", ""))
    else:
        records = []

    repo = REPO
    floors = {}
    for p in sorted((repo / "spiders").glob("*/manifest.yaml")):
        f = inspector.min_rows_floor(repo, p.parent.name)
        if f is not None:
            floors[p.parent.name] = f

    cfg = load_config(None)
    # 已声明源里最常用的是 month 频：容忍窗 = 336h × 2.5 = 840h。逐源按自身周期算，
    # 这里只取一个统一口径做"超窗"基线，避免把 day 频源也按 month 判。
    per_source_window = {}
    for src in floors:
        period = inspector.expected_period_hours(repo, src, cfg)
        hours = (period * cfg.thresholds.freshness_multiplier
                 if period and period != float("inf") else 840.0)
        per_source_window[src] = min(hours, 840.0)

    tickets = rule1c_tickets(Path(args.tickets), day)
    over: dict[str, int] = {}
    for src, tol in per_source_window.items():
        d = over_window_days(records, {src: floors[src]}, now, tol)
        over.update(d)

    # healthy：用真实判词算，而不是读摘要（摘要可能来自别的窗口）
    healthy = None
    if records:
        runs_by = inspector.group_runs(records)
        plan = inspector.plan_tickets(runs_by, repo, cfg, now, dsn="")
        healthy = plan.healthy

    window = f"month(840h)"
    row = build_row(day, healthy, tickets, over, window)

    if args.json:
        print(json.dumps({"day": day, "healthy": healthy, "rule1c_sources": tickets,
                          "over_window_days": over}, ensure_ascii=False, indent=2))
    else:
        print(row)

    if args.append:
        path = Path(args.append)
        if not path.exists():
            path.write_text(HEADER, encoding="utf-8")
        body = path.read_text(encoding="utf-8")
        if f"| {day} |" in body:
            print(f"（{day} 已有记录，未重复追加）", file=sys.stderr)
        else:
            path.write_text(body.rstrip("\n") + "\n" + row + "\n", encoding="utf-8")
            print(f"已追加到 {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
