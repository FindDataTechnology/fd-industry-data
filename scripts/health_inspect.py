#!/usr/bin/env python3
"""巡检器 CLI：遥测 → 工单落仓 →（可选）SUBMIT/STATUS 接线萬星 spider-heal。

用法：
  python3 scripts/health_inspect.py --from-pg --submit-new --sync-status
  python3 scripts/health_inspect.py --snapshot /tmp/runs.json --dry-run

环境变量：
  FD_CENTRAL_PG_DSN          中央库 DSN（--from-pg 必填，psql 读取，只读）
  WANXING_SPIDER_HEAL_URL    spider-heal a2a 端点（有默认）
  WANXING_SPIDER_HEAL_KEY    finddata 调用键（SUBMIT/STATUS 必填）
  FD_HEALTH_CONFIG           配置 JSON 路径（可选）

两段式（fix-health-ticket-landing-race）：

  # 段一：落单写盘（不投递）
  python3 scripts/health_inspect.py --from-pg --emit --summary-file ...
  git add -A reports/health-tickets && git commit && git push   # ← 落仓门
  # 段二：投递与回读（工作流同一 job 内 push 之后）
  python3 scripts/health_inspect.py --deliver --summary-file ...

工单必须先落到远端可见，agent 才读得到；同轮既写盘又投递会让 agent 读到
「文件不存在」而拒收（2026-10-06 / 10-11 实测烧单）。缺省（两段都不给）按
旧行为落单+投递，仅供离线/手工使用。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.health import inspector  # noqa: E402
from fd_industry_data.health.config import load_config, load_config_from_db  # noqa: E402

# 门面主机 = wanxing.finddatatech.cloud（unify-wanxing-a2a-face 4.x 后，
# platform.finddatatech.cloud 上的 /api/wanxing 挂载已摘除；打到旧主机只会被平台
# auth 闸拦成 401 "Authentication required"，与密钥无关——2026-11-09 实测）
DEFAULT_URL = (
    "https://wanxing.finddatatech.cloud/api/wanxing/v1/a2a/"
    "packs-kkcie7nlrhluo4likpnn0w-spider-heal"
)
DEFAULT_SLUG = "FindDataTechnology/fd-industry-data"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--from-pg", action="store_true", help="读中央库 crawl_runs（默认）")
    src.add_argument("--snapshot", help="读遥测快照 JSON（测试/离线）")
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--outdir", default=str(REPO / "reports" / "health-tickets"))
    ap.add_argument("--config", default=None)
    ap.add_argument("--emit", action="store_true",
                    help="段一：只落单写盘（不投递）")
    ap.add_argument("--deliver", action="store_true",
                    help="段二：只投递与回读（不落单）")
    ap.add_argument("--submit-new", action="store_true")
    ap.add_argument("--sync-status", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--summary-file", default=None)
    args = ap.parse_args()

    import os

    # 两段式判定：显式给了任一新开关就按新语义；否则沿用旧行为（落单+投递）
    two_phase = args.emit or args.deliver
    do_emit = args.emit or not two_phase
    do_deliver = args.deliver or not two_phase
    if args.emit and args.deliver:          # 互斥：同轮写盘+投递正是烧单根因
        ap.error("--emit 与 --deliver 互斥（工单必须先落仓再投递）")

    repo = Path(args.repo)
    outdir = Path(args.outdir)
    # 配置优先级：--config/FD_HEALTH_CONFIG（文件） > 中央库 health_config 表 > 默认值
    cfg_path = args.config or os.environ.get("FD_HEALTH_CONFIG", "").strip() or None
    if cfg_path:
        config, cfg_src = load_config(cfg_path), f"file:{cfg_path}"
    else:
        config = load_config_from_db(os.environ.get("FD_CENTRAL_PG_DSN", ""))
        cfg_src = "db:health_config" if config else "defaults"
        config = config or load_config(None)
    now = datetime.now(timezone.utc)

    if args.snapshot:
        records = inspector.load_runs_from_snapshot(args.snapshot)
        source_kind = f"snapshot:{args.snapshot}"
    else:
        _parse_errors: list[str] = []
        records = inspector.load_runs_from_psql(
            config, os.environ.get("FD_CENTRAL_PG_DSN", ""), parse_errors=_parse_errors)
        source_kind = "psql"

    runs_by_source = inspector.group_runs(records)
    plan = inspector.plan_tickets(runs_by_source, repo, config, now,
                                  dsn=os.environ.get("FD_CENTRAL_PG_DSN", ""))
    if do_emit:
        stats = inspector.write_plan(plan, repo, outdir, config, now)
        # 自愈收口（harden-crawl-delivery 2.9）：源在开单之后连续 N 次成功即置
        # recovered。只改工单终态，不产生新单，也不触碰 closed（人工门）。
        stats["recovered"] = inspector.reconcile_recovered(runs_by_source, repo, outdir, now, config)
    else:
        # 投递段不落单：统计沿用空账，避免同轮二次出单
        stats = {"written": [], "deferred": plan.deferred,
                 "healthy": plan.healthy,
                 "skipped_unknown_period": plan.skipped_unknown_period,
                 "unregistered_sources": plan.unregistered,
                 "blocked_config_sources": plan.blocked_config}

    summary: dict = {
        "at": now.isoformat(),
        "source": source_kind,
        "config_source": cfg_src,
        "sources_seen": len(runs_by_source),
        "healthy": stats["healthy"],
        "written": stats["written"],
        "deferred": stats["deferred"],
        "skipped_unknown_period": stats["skipped_unknown_period"],
        # 遥测里有运行、但本仓没有 manifest、也不在 excluded_sources 的源。
        # 以前这类源被并进 skipped_unknown_period 一起静默跳过——名单还会漂
        # （90 天口径 flk-law-backfill/polygon，30 天口径 example-stock/yfinance），
        # 说明"静默"不是稳定状态。单列出来，让新面孔在摘要里可见。
        "unregistered_sources": stats.get("unregistered_sources") or [],
        # 缺必需环境变量、runner 不取数的源：可见但不判定（不是代码问题，不催修）
        "blocked_config_sources": stats.get("blocked_config_sources") or [],
        # 在册（manifest 有 schedule、未 parked）但遥测窗内零运行的源。
        # 以前这些源在巡检面上**完全不可见**——年频源一过回看窗就静默消失
        # （add-flower-vertical 6.1）。现在逐条带原因落进 summary：
        # period-longer-than-window（窗内本就不该跑）/ no-run-in-window（调度面故障）。
        "absent_sources": getattr(plan, "absent", []),
        # 自愈收口的工单（2.9）：连续 N 次成功后自动 recovered，可复盘。
        "recovered": stats.get("recovered") or [],
        # 遥测解析异常（2.10）：非遥测行/非 slug 源名单列，不再混进
        # skipped_unknown_period。空列表 = 本轮输入干净。
        "parse_errors": (stats.get("parse_errors") or []) + (locals().get("_parse_errors") or []),
        # 调度状态不一致（7.4）：内容仓与源清单说不到一起的源
        "inventory_mismatches": stats.get("inventory_mismatches") or [],
        "master_switch": config.master_switch,
    }

    if do_deliver and args.submit_new:
        url = os.environ.get("WANXING_SPIDER_HEAL_URL", DEFAULT_URL)
        key = os.environ.get("WANXING_SPIDER_HEAL_KEY", "")
        if not key and not args.dry_run and config.master_switch:
            print("ERROR: WANXING_SPIDER_HEAL_KEY required for --submit-new", file=sys.stderr)
            return 2
        summary["submit"] = inspector.submit_new_tickets(
            outdir, os.environ.get("FD_HEALTH_REPO_SLUG", DEFAULT_SLUG),
            url, key, config, dry_run=args.dry_run,
        )
    if do_deliver and args.sync_status:
        url = os.environ.get("WANXING_SPIDER_HEAL_URL", DEFAULT_URL)
        key = os.environ.get("WANXING_SPIDER_HEAL_KEY", "")
        summary["status"] = inspector.sync_status(outdir, url, key, dry_run=args.dry_run)

    # 投递通道体检（2026-11-14）：逐张工单的 error 判定**不足以**让人发现通道整个断了。
    # 11-13~11-14 三天每次投递都被平台拒（-32032 fetch failed / 502），摘要里只是七张
    # 单各一条 error，看起来像七张单各自的问题，而通道死了三天无人知道。所以这里把它
    # 汇成一条判据，并且**在 outage 时让这一轮变红**——沉默与「没有事件」长得一样。
    lane = inspector.delivery_lane_health(summary.get("submit"), summary.get("status"))
    summary["delivery_lane"] = lane

    text = json.dumps(summary, ensure_ascii=False, indent=2)
    print(text)
    if args.summary_file:
        Path(args.summary_file).write_text(text + "\n", encoding="utf-8")
    if lane["status"] == "outage" and not args.dry_run:
        # 文案刻意给"先分诊"而不是猜一个方向：2026-11-14 的 502 实际出在边缘到上游
        # （cheap-5 掉线 ⇒ wanxing-facade 零端点），而 11-13 的 -32032 出在平台出网。
        # 两种都表现为"每次投递都失败"，混成一句话就会把人送去查错的那一层。
        print(f"::error::投递通道中断：本轮 {lane['attempted']} 次 SUBMIT 全部栽在服务侧错误"
              f"（{'; '.join(lane['reasons'])}），0 张被受理。这不是工单的问题。"
              "先分诊：线内 NodePort 打同一 a2a 路径——401/403=端点在（查边缘与上游 pod 的"
              " endpoints，别加放行）、404=端点没实现、000=上游不可达；再看 platform pod 能否"
              "取到 raw.githubusercontent.com（工单文件在 raw 上）。",
              file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())