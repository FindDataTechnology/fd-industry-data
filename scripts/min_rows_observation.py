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


def _deferred_carry_verdict(deferred: list[dict]) -> bool:
    """顺延条目是否每条都带 `category` 与 `rule`。

    抽成纯函数是为了**能两向测**：既断言"真实顺延里确实带着"，
    也断言"缺字段时必须返回 False"。只测前者的话，把判据硬编码成 True 也照样绿
    ——2026-11-10 实测过这个坑：反向验证时 11 条测试全过，说明根本没测到那一行。
    """
    return all("category" in d and "rule" in d for d in deferred)


def collect_fingerprints(records: list[dict], repo: Path, cfg, now: datetime) -> dict:
    """四项上线指纹：判断"新代码是否真的在跑"。

    为什么不读 `last-run.json`：该文件 2026-11-10 已移出 git（生成态，每次覆写），
    而没有它就判不了部署是否生效——这四项目前只能从**遥测 + manifest** 现算。
    每一项都是旧代码在结构上产不出来的形态：

    1. `unregistered_sources` 字段存在（旧代码没有这个概念，缺 manifest 的源会被
       并进 `skipped_unknown_period` 静默跳过）；
    2. `skipped_unknown_period` 不含 `TCPConnectWrap`（解析器修复前，调用栈续行会被
       补成一条假记录，源名取到那段文字）；
    3. `deferred[]` 每条都带 `category` 与 `rule`（旧代码只记 source+reason，
       "顺延 N 个"看不出严重程度）；
    4. `blocked_config_sources` 字段存在（旧代码不认识 `blocked-config` 状态）。
    """
    import tempfile
    from fd_industry_data.health import inspector

    runs_by = inspector.group_runs(records)
    plan = inspector.plan_tickets(runs_by, repo, cfg, now, dsn="")

    # 顺延条目带判词是 write_plan 的产物（它在写盘时补 category/rule），
    # 所以落一遍临时目录取真实形态；临时目录用完即弃，不碰仓里的工单。
    with tempfile.TemporaryDirectory(prefix="obs-fp-") as td:
        stats = inspector.write_plan(plan, repo, Path(td), cfg, now)
    deferred = stats.get("deferred") or []

    unreg = getattr(plan, "unregistered", None)
    blocked = getattr(plan, "blocked_config", None)
    skipped = plan.skipped_unknown_period or []

    return {
        "unregistered_sources": {
            "present": unreg is not None,
            "value": sorted(unreg or []),
        },
        "skipped_unknown_period_clean": {
            "clean": not any("TCPConnectWrap" in str(s) for s in skipped),
            "value": skipped,
        },
        "deferred_carry_verdict": {
            "ok": _deferred_carry_verdict(deferred),
            "count": len(deferred),
        },
        "blocked_config_sources": {
            "present": blocked is not None,
            "value": sorted(blocked or []),
        },
    }


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

    # 必须加载**生产巡检范围**（config/health-scope.json），否则 excluded_sources 为空：
    # 那些按裁决排除的跨仓 Job（akshare / ccdi-* / flk-law-* / wbgapi …）会被算进
    # unregistered_sources，看起来像"该登记没登记"，其实是早就定了不归本巡检判。
    # 2026-11-10 实测：用默认配置时该项列出 16 个源，用生产 scope 后应为 2 个。
    scope = repo / "config" / "health-scope.json"
    cfg = load_config(scope if scope.is_file() else None)
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
    fps = None
    if records:
        runs_by = inspector.group_runs(records)
        plan = inspector.plan_tickets(runs_by, repo, cfg, now, dsn="")
        healthy = plan.healthy
        fps = collect_fingerprints(records, repo, cfg, now)

    window = f"month(840h)"
    row = build_row(day, healthy, tickets, over, window)

    if args.json:
        print(json.dumps({"day": day, "healthy": healthy, "rule1c_sources": tickets,
                          "over_window_days": over, "fingerprints": fps},
                         ensure_ascii=False, indent=2))
    else:
        print(row)
        if fps:
            print()
            print("四项上线指纹：")
            a = fps["unregistered_sources"]
            b = fps["skipped_unknown_period_clean"]
            c = fps["deferred_carry_verdict"]
            d = fps["blocked_config_sources"]
            print(f"  ① unregistered_sources 字段: {'有 ✓' if a['present'] else '缺 ✗'} {a['value']}")
            print(f"  ② skipped_unknown_period 无调用栈: {'✓' if b['clean'] else '✗'} {b['value']}")
            print(f"  ③ deferred 带 category/rule: {'✓' if c['ok'] else '✗'}（{c['count']} 条）")
            print(f"  ④ blocked_config_sources 字段: {'有 ✓' if d['present'] else '缺 ✗'} {d['value']}")

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
