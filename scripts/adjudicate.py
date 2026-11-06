#!/usr/bin/env python3
"""裁决 CLI：manual 重复工单升级通道的人工操作面。

用法（在 fd-industry-data 仓库根执行）：

  python3 scripts/adjudicate.py scan                 # 扫描工单语料，升级+卷宗+队列（幂等）
  python3 scripts/adjudicate.py list                 # 列裁决队列
  python3 scripts/adjudicate.py list --json          # 机读输出
  python3 scripts/adjudicate.py summary              # 队列摘要 markdown（GHA job summary 用）
  python3 scripts/adjudicate.py decide <src> repair  --note "已换数据面，解除冻结"
  python3 scripts/adjudicate.py decide <src> park    --note "上游间歇不可用，停滚动出单"
  python3 scripts/adjudicate.py decide <src> retire  --note "长期无成功，退役归档"

裁决语义：
- repair  解冻该源的新工单派发并关闭队列项；
- park    该源 manifest 摘除 schedule 并标记 parked: true（单元目录原地保留）；
- retire  manifest 摘 schedule，并**只打印** git mv 命令清单（把
          spiders/<src>/ 归档到 archive/adjudication-<date>/）——移动由
          主代理/人工统一执行，本命令不移动任何目录。

台账 output/adjudication/ledger.json 为 append-only entries 列表
（时间/源/结论/依据 note/执行动作），机读。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.health import adjudication  # noqa: E402


def _load_queue_or_die(repo: Path) -> dict:
    return adjudication.load_queue(repo)


def cmd_scan(args) -> int:
    repo = Path(args.repo)
    tickets = Path(args.tickets)
    result = adjudication.scan_escalations(tickets, repo, threshold=args.threshold)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["escalated_now"]:
        print(f"escalated now: {', '.join(result['escalated_now'])}")
    else:
        print("escalated now: (none)")
    print(f"queue size: {result['queue_size']} at {result['queue_path']}")
    return 0


def cmd_list(args) -> int:
    repo = Path(args.repo)
    queue = _load_queue_or_die(repo)
    entries = queue["entries"]
    if args.json:
        print(json.dumps(queue, ensure_ascii=False, indent=2))
        return 0
    threshold = queue.get("threshold", adjudication.ESCALATION_THRESHOLD)
    print(f"adjudication queue ({len(entries)} entries, threshold=manual>={threshold})")
    if not entries:
        print("  (empty — run `python3 scripts/adjudicate.py scan` first)")
        return 0
    for e in entries:
        decided = ""
        if e.get("decision"):
            decided = f" decision={e['decision']} decided_at={e.get('decided_at')}"
            if e.get("note"):
                decided += f" note={e['note']!r}"
        print(
            f"  {e['source']:<28} {e.get('status', '?'):<10} "
            f"manual={e.get('manual_tickets', '?')} "
            f"escalated_at={e.get('escalated_at')}{decided}"
        )
    return 0


def cmd_summary(args) -> int:
    print(adjudication.render_summary_markdown(Path(args.repo)), end="")
    return 0


def cmd_decide(args) -> int:
    repo = Path(args.repo)
    try:
        result = adjudication.record_decision(
            repo, args.source, args.decision, note=args.note or "",
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    entry = result["entry"]
    print(f"decision recorded: {args.source} -> {entry['status']}")
    for action in result["actions"]:
        print(f"  action: {action}")
    if result["commands"]:
        print("git mv commands to execute (NOT executed here; 移动由主代理/人工统一执行):")
        for cmd in result["commands"]:
            print(f"  {cmd}")
    ledger = adjudication.load_ledger(repo)
    print(f"ledger entries: {len(ledger['entries'])} at {adjudication.ledger_path(repo)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=str(REPO), help="仓库根（缺省=脚本上一级）")
    ap.add_argument("--tickets", default=None,
                    help="工单目录（scan 用；缺省 <repo>/reports/health-tickets）")
    ap.add_argument("--threshold", type=int,
                    default=adjudication.ESCALATION_THRESHOLD,
                    help=f"升级阈值（缺省 {adjudication.ESCALATION_THRESHOLD}）")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("scan", help="扫描工单语料，维护升级队列与卷宗（幂等）")
    sub.add_parser("list", help="列裁决队列")
    sub.add_parser("summary", help="队列摘要 markdown（job summary 用）")
    p_list = sub.choices["list"]
    p_list.add_argument("--json", action="store_true", help="机读 JSON 输出")

    p_decide = sub.add_parser("decide", help="执行裁决并落台账")
    p_decide.add_argument("source", help="数据源 id（须已在裁决队列）")
    p_decide.add_argument("decision", choices=adjudication.DECISIONS)
    p_decide.add_argument("--note", default="", help="裁决依据（写入台账）")

    args = ap.parse_args(argv)
    if args.tickets is None:
        args.tickets = str(Path(args.repo) / "reports" / "health-tickets")
    # 子命令级 --repo/--tickets 落在主解析器上，argparse 已统一处理
    if args.command == "scan":
        return cmd_scan(args)
    if args.command == "list":
        return cmd_list(args)
    if args.command == "summary":
        return cmd_summary(args)
    if args.command == "decide":
        return cmd_decide(args)
    ap.error(f"unknown command: {args.command}")
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
