#!/usr/bin/env python3
"""舰队基线：对在产源逐源顺序执行一次 runner，产成功率基线报告。

在产源名单 = ``spiders/<slug>/manifest.yaml`` 顶层 ``schedule`` 非空的源。
逐源**顺序**（不并发，基线要的是每源干净耗时）调用现有 runner 入口：

    python3 -m fd_industry_data.runner_cli <src> --limit N [--dry-run]

结果行 ``fd-runner: <src> -> <status> rows=<rows> in <t>s`` 被解析回收；
退出码 2/4 映射 missing/no-entry。缺省带 ``--dry-run``（不写中央库遥测，
基线是只读巡检；``--report-db`` 可去掉该开关允许正常上报）。

用法（在 fd-industry-data 仓库根执行）：

  python3 scripts/fleet_baseline.py --dry-run          # 只列将跑的源
  python3 scripts/fleet_baseline.py --limit 20         # 顺序跑全部在产源
  python3 scripts/fleet_baseline.py --only fred-data,bls --limit 50

输出：
  reports/fleet-baseline/<YYYYMMDD>-baseline.json  机读（含逐源结果+汇总）
  reports/fleet-baseline/<YYYYMMDD>-baseline.md    汇总成功率 + 逐源表
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_LIMIT = 20
DEFAULT_TIMEOUT = 900
SPIDERS_DIRNAME = "spiders"

# runner 结果行：fd-runner: <src> -> <status> rows=<rows> in <t>s
_RUNNER_LINE_RE = re.compile(
    r"^fd-runner:\s*(?P<src>\S+)\s*->\s*(?P<status>\S+)\s+rows=(?P<rows>\d+)\s+in\s+(?P<secs>\d+(?:\.\d+)?)s",
    re.MULTILINE,
)


def discover_scheduled_sources(repo) -> list[str]:
    """在产源名单：spiders/*/manifest.yaml 顶层 schedule 非空的源（排序）。"""
    import yaml

    repo = Path(repo)
    roots = repo / SPIDERS_DIRNAME
    found: list[str] = []
    if not roots.is_dir():
        return found
    for unit in sorted(roots.iterdir()):
        manifest = unit / "manifest.yaml"
        if not unit.is_dir() or not manifest.is_file():
            continue
        try:
            doc = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if doc.get("schedule"):  # 非空字符串才算在产排程
            found.append(unit.name)
    return found


def parse_runner_output(stdout: str) -> tuple[str | None, int | None, float | None]:
    """从 runner stdout 解析 (status, rows, seconds)；解析不到返回 (None, None, None)。"""
    matches = list(_RUNNER_LINE_RE.finditer(stdout or ""))
    if not matches:
        return None, None, None
    m = matches[-1]
    return m.group("status"), int(m.group("rows")), float(m.group("secs"))


def classify_exit(exit_code: int) -> str | None:
    """runner 约定退出码 → 结果态；None = 不是可特判的码。"""
    return {2: "missing", 4: "no-entry"}.get(exit_code)


def run_source_subprocess(repo, source: str, limit: int, timeout: int,
                          report_db: bool = False) -> dict:
    """真实执行：子进程调 runner_cli（隔离 chdir/模块加载副作用）。"""
    repo = Path(repo)
    cmd = [sys.executable, "-m", "fd_industry_data.runner_cli", source,
           "--limit", str(limit)]
    if not report_db:
        cmd.append("--dry-run")  # 基线不写中央库遥测
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(repo), env.get("PYTHONPATH", "")) if p)
    env.setdefault("FD_CONTENT_DIR", str(repo / SPIDERS_DIRNAME))
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, cwd=str(repo), env=env, capture_output=True,
            text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"source": source, "status": "timeout", "exit_code": None,
                "rows": None, "seconds": round(time.monotonic() - started, 2),
                "error": f"timeout after {timeout}s"}
    seconds = round(time.monotonic() - started, 2)
    status, rows, _runner_secs = parse_runner_output(proc.stdout)
    if status is None:
        status = classify_exit(proc.returncode) or (
            "success" if proc.returncode == 0 else "failed")
    elif proc.returncode != 0 and status == "success":
        status = classify_exit(proc.returncode) or "failed"
    if rows is None:
        rows = 0
    err_lines = [ln.strip() for ln in (proc.stderr or "").splitlines() if ln.strip()]
    error = err_lines[-1][:300] if err_lines and proc.returncode != 0 else ""
    return {"source": source, "status": status, "exit_code": proc.returncode,
            "rows": rows, "seconds": seconds, "error": error}


def _result_summary(results: list[dict], limit: int, total_seconds: float) -> dict:
    success = sum(1 for r in results if r["status"] == "success")
    failed = len(results) - success
    return {
        "total": len(results),
        "success": success,
        "failed": failed,
        "success_rate": round(success / len(results), 4) if results else None,
        "total_seconds": round(total_seconds, 2),
        "limit": limit,
    }


def render_report_markdown(doc: dict) -> str:
    s = doc["summary"]
    lines = [
        f"# 舰队基线 {doc['date']}",
        "",
        f"- 生成时间(UTC): {doc['generated_at']}",
        f"- runner limit: {s['limit']}  总耗时: {s['total_seconds']}s",
        f"- 在产源: {s['total']}  成功: {s['success']}  失败: {s['failed']}"
        f"  成功率: {s['success_rate']}",
        "",
        "| 源 | 状态 | 产出行数 | 耗时(s) | 错误摘要 |",
        "|---|---|---|---|---|",
    ]
    for r in doc["sources"]:
        err = (r.get("error") or "").replace("|", "\\|").replace("\n", " ")[:120]
        lines.append(
            f"| {r['source']} | {r['status']} | {r.get('rows') if r.get('rows') is not None else '-'} "
            f"| {r.get('seconds') if r.get('seconds') is not None else '-'} | {err or '-'} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_report(repo, results: list[dict], limit: int, total_seconds: float,
                 report_db: bool, outdir: Path | None = None) -> tuple[Path, Path]:
    repo = Path(repo)
    now = datetime.now(timezone.utc)
    date = now.strftime("%Y%m%d")
    outdir = Path(outdir) if outdir else repo / "reports" / "fleet-baseline"
    outdir.mkdir(parents=True, exist_ok=True)
    doc = {
        "date": date,
        "generated_at": now.isoformat(),
        "runner": "python3 -m fd_industry_data.runner_cli <src> --limit N"
                  + ("" if report_db else " --dry-run"),
        "report_db": report_db,
        "summary": _result_summary(results, limit, total_seconds),
        "sources": results,
    }
    jpath = outdir / f"{date}-baseline.json"
    mpath = outdir / f"{date}-baseline.md"
    jpath.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
    mpath.write_text(render_report_markdown(doc), encoding="utf-8")
    return jpath, mpath


def main(argv: list[str] | None = None, run_source=None) -> int:
    """run_source 可注入（单测打桩）：(source, limit) -> result dict。"""
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=str(REPO), help="仓库根（缺省=脚本上一级）")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help=f"每源抓取条数上限（缺省 {DEFAULT_LIMIT}）")
    ap.add_argument("--only", default=None,
                    help="逗号分隔源名单收窄（缺省全部在产源）")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                    help=f"单源超时秒数（缺省 {DEFAULT_TIMEOUT}）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只列出将跑的源，不执行、不写报告")
    ap.add_argument("--report-db", action="store_true",
                    help="允许 runner 正常上报中央库（缺省 --dry-run 只读）")
    ap.add_argument("--outdir", default=None,
                    help="报告目录（缺省 <repo>/reports/fleet-baseline）")
    args = ap.parse_args(argv)
    if args.limit <= 0:
        print("ERROR: --limit must be positive", file=sys.stderr)
        return 2

    repo = Path(args.repo)
    sources = discover_scheduled_sources(repo)
    if args.only:
        wanted = [s.strip() for s in args.only.split(",") if s.strip()]
        known = set(sources)
        unknown = [s for s in wanted if s not in known]
        if unknown:
            print(f"ERROR: unknown/not-scheduled source(s): {', '.join(unknown)}",
                  file=sys.stderr)
            return 2
        sources = [s for s in sources if s in set(wanted)]

    if args.dry_run:
        print(f"scheduled sources ({len(sources)}):")
        for s in sources:
            print(f"  {s}")
        print(f"dry-run: would run {len(sources)} source(s) sequentially "
              f"with --limit {args.limit} (no report written)")
        return 0

    if not sources:
        print("no scheduled sources found; nothing to do")
        return 0

    runner = run_source or (
        lambda src, limit: run_source_subprocess(
            repo, src, limit, timeout=args.timeout, report_db=args.report_db)
    )
    results: list[dict] = []
    total_seconds = 0.0
    for index, src in enumerate(sources, 1):
        result = runner(src, args.limit)
        if not isinstance(result, dict) or "status" not in result:
            result = {"source": src, "status": "failed", "exit_code": None,
                      "rows": 0, "seconds": 0.0,
                      "error": "invalid runner stub result"}
            result["source"] = src
        result.setdefault("source", src)
        results.append(result)
        total_seconds += float(result.get("seconds") or 0.0)
        err = f" err={result['error']}" if result.get("error") else ""
        print(f"[{index}/{len(sources)}] {src} -> {result['status']}"
              f" rows={result.get('rows')} {result.get('seconds')}s{err}")

    jpath, mpath = write_report(repo, results, args.limit, total_seconds,
                                report_db=args.report_db, outdir=args.outdir)
    summary = _result_summary(results, args.limit, total_seconds)
    print(f"baseline written: {jpath}")
    print(f"                  {mpath}")
    print(f"summary: {summary['success']}/{summary['total']} success"
          f" (rate={summary['success_rate']}) in {summary['total_seconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
