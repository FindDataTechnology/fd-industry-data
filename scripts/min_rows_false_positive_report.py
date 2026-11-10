#!/usr/bin/env python3
"""行数下限（rule1c）工单的回看报表：给"观察一周、统计误报率"提供可算的数。

为什么需要它
------------
`declare-health-min-rows` 的任务 3.2 要求"观察一周：统计这 9 个源的误报次数，>20% 的回退"。
在那之前，这件事只能靠人翻 `reports/health-tickets/` 一张张看——于是它永远不会真的被做。

这份报表只做一件事：把「某源出过几张行数不足单」与「出单之后它是否又正常产出过」
放在同一张表里。

一个刻意的措辞边界
------------------
`自愈` 不等于 `误报`。出单后恢复正常有两种可能：①那张单真的误报（阈值太敏感）；
②确实低产，被修好或上游恢复。报表只标出"出单之后出现过达标成功"这个**事实**，
是否算误报由人判断——把两者直接等号是要造假的。

用法
----
    python3 scripts/min_rows_false_positive_report.py --days 7 --snapshot /tmp/tel.json
    FD_CENTRAL_PG_DSN=... python3 scripts/min_rows_false_positive_report.py --days 7

只读：不写工单、不碰数据库（`--from-pg` 走 health 层现成的只读查询）。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TICKETS = REPO / "reports" / "health-tickets"
RULE = "rule1c"


@dataclass
class Ticket:
    source: str
    at: datetime
    category: str | None
    path: str


def load_rule1c_tickets(outdir: Path, since: datetime) -> list[Ticket]:
    """工单目录里 `notes` 命中 rule1c 的单，按时间过滤。

    `notes` 是判词 trace 的落点，也是唯一能区分"行数不足"与别的原因的地方——
    只按 category 过滤会把 structure 类里其它规则的单也算进来。
    """
    import yaml

    out: list[Ticket] = []
    for p in sorted(outdir.glob("*.yaml")):
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError):
            continue
        notes = str(((doc.get("evidence") or {}).get("notes")) or "")
        if RULE not in notes:
            continue
        raw = doc.get("created_at") or ""
        try:
            at = datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at < since:
            continue
        out.append(Ticket(source=str(doc.get("source") or ""), at=at,
                          category=doc.get("category"), path=p.name))
    return out


def qualifying_successes(records: list[dict], floor: int) -> dict[str, list[dict]]:
    """每个源"达到下限的成功"运行（时间升序）。"""
    by: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if r.get("status") not in ("success", "partial"):
            continue
        rows = r.get("rows")
        if isinstance(rows, int) and rows >= floor:
            by[str(r.get("source"))].append(r)
    for runs in by.values():
        runs.sort(key=lambda x: str(x.get("started_at")))
    return by


def resolve_floors(sources, repo: Path = REPO) -> dict[str, int]:
    """每个源的下限：先读 manifest；**暂撤了声明的源回退到 withdrawn.py 记下的原值**。

    为什么需要回退：2026-11-10 撤了 boxofficemojo / cisa / gb-standards 的
    `min_rows_per_run`，manifest 里已经没有那一行。而这三个源正是观察对象——
    不回退的话，报表对它们一律显示「下限 —」「出单后达标 未声明」，
    等于在最需要判定的地方失明（2026-11-11 实测）。
    """
    from fd_industry_data.health import inspector
    from fd_industry_data.health.withdrawn import WITHDRAWN

    withdrawn_floors = {w.source: w.floor for w in WITHDRAWN}
    out: dict[str, int] = {}
    for s in sources:
        f = inspector.min_rows_floor(repo, s)
        if f is None:
            f = withdrawn_floors.get(s)
        if f is not None:
            out[s] = f
    return out


def build_rows(tickets: list[Ticket], records: list[dict], floors: dict[str, int]) -> list[dict]:
    """把工单与遥测对齐：每源出单数、出单后是否又达标。"""
    per_source: dict[str, list[Ticket]] = defaultdict(list)
    for t in tickets:
        per_source[t.source].append(t)

    rows: list[dict] = []
    for source in sorted(per_source):
        floor = floors.get(source)
        ts = sorted(per_source[source], key=lambda t: t.at)
        recovered_after: int | None = None
        if floor is not None:
            good = qualifying_successes(records, floor).get(source, [])
            # 最后一张单之后，是否出现过达标成功
            last_at = ts[-1].at
            recovered_after = sum(
                1 for r in good
                if _parse(str(r.get("started_at"))) > last_at
            ) if good else 0
        rows.append({
            "source": source,
            "tickets": len(ts),
            "last_ticket": ts[-1].at.date().isoformat(),
            "floor": floor,
            "successes_after_last_ticket": recovered_after,
        })
    return rows


def _parse(s: str) -> datetime:
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def render(rows: list[dict], days: int, total: int) -> str:
    lines = [f"近 {days} 天的 rule1c（行数不足）工单回看：共 {total} 张，涉及 {len(rows)} 个源",
             "",
             f"{'source':24} {'单数':>4} {'最近一张':>10} {'下限':>5} {'出单后达标次数':>14}"]
    for r in rows:
        floor = "—" if r["floor"] is None else str(r["floor"])
        after = "未声明" if r["successes_after_last_ticket"] is None else str(
            r["successes_after_last_ticket"])
        lines.append(f"{r['source']:24} {r['tickets']:>4} {r['last_ticket']:>10} "
                     f"{floor:>5} {after:>14}")
    lines += ["",
              "读法：最后一列 >0 表示最后一张单之后该源又达到过下限——",
              "可能是误报（阈值太敏感），也可能是真的低产后被修好/上游恢复。**两者不划等号**，",
              "需要看那张单的判词与当时的行数才能定。这一列只负责把人要看的东西摆在一起。"]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7, help="回看天数（默认 7）")
    ap.add_argument("--snapshot", help="遥测快照 JSON（离线）")
    ap.add_argument("--from-pg", action="store_true", help="读中央库 crawl_runs（只读）")
    ap.add_argument("--outdir", default=str(TICKETS), help="工单目录")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非表格")
    args = ap.parse_args(argv)

    import os
    from fd_industry_data.health import inspector
    from fd_industry_data.health.config import load_config

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    tickets = load_rule1c_tickets(Path(args.outdir), since)

    if args.snapshot:
        records = inspector.load_runs_from_snapshot(args.snapshot)
    elif args.from_pg:
        cfg = load_config(None)
        records = inspector.load_runs_from_psql(cfg, os.environ.get("FD_CENTRAL_PG_DSN", ""))
    else:
        records = []

    sources = {t.source for t in tickets}
    floors = resolve_floors(sources)

    rows = build_rows(tickets, records, floors)
    if args.json:
        print(json.dumps({"days": args.days, "tickets": len(tickets), "rows": rows},
                         ensure_ascii=False, indent=2))
    else:
        print(render(rows, args.days, len(tickets)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
