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

DEFAULT_URL = (
    "https://platform.finddatatech.cloud/api/wanxing/v1/a2a/"
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
    ap.add_argument("--submit-new", action="store_true")
    ap.add_argument("--sync-status", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--summary-file", default=None)
    args = ap.parse_args()

    import os

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
        records = inspector.load_runs_from_psql(config, os.environ.get("FD_CENTRAL_PG_DSN", ""))
        source_kind = "psql"

    runs_by_source = inspector.group_runs(records)
    plan = inspector.plan_tickets(runs_by_source, repo, config, now)
    stats = inspector.write_plan(plan, repo, outdir, config, now)

    summary: dict = {
        "at": now.isoformat(),
        "source": source_kind,
        "config_source": cfg_src,
        "sources_seen": len(runs_by_source),
        "healthy": stats["healthy"],
        "written": stats["written"],
        "deferred": stats["deferred"],
        "skipped_unknown_period": stats["skipped_unknown_period"],
        "master_switch": config.master_switch,
    }

    if args.submit_new:
        url = os.environ.get("WANXING_SPIDER_HEAL_URL", DEFAULT_URL)
        key = os.environ.get("WANXING_SPIDER_HEAL_KEY", "")
        if not key and not args.dry_run and config.master_switch:
            print("ERROR: WANXING_SPIDER_HEAL_KEY required for --submit-new", file=sys.stderr)
            return 2
        summary["submit"] = inspector.submit_new_tickets(
            outdir, os.environ.get("FD_HEALTH_REPO_SLUG", DEFAULT_SLUG),
            url, key, config, dry_run=args.dry_run,
        )
    if args.sync_status:
        url = os.environ.get("WANXING_SPIDER_HEAL_URL", DEFAULT_URL)
        key = os.environ.get("WANXING_SPIDER_HEAL_KEY", "")
        summary["status"] = inspector.sync_status(outdir, url, key)

    text = json.dumps(summary, ensure_ascii=False, indent=2)
    print(text)
    if args.summary_file:
        Path(args.summary_file).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())