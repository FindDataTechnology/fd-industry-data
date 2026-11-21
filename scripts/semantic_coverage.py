#!/usr/bin/env python3
"""三联账：每个亮着的源在「抓取 → 抽取 → 观测」三段上的位置
（change measure-semantic-coverage-gap）。

要回答的问题
────────────
一个源**抓到了**，它**能按概念读到**吗？

这两种状态在既有的闸上完全不同形。`health-inspect` 判「源活不活」，全绿；
`check_observation_lag` 判「已有抽取器活着吗」，范围只到有抽取器的那些源。
于是「亮着但根本没有抽取器」的源两边都不在，永远不红——2026-11-21 盘面
实测：125 个亮着的源里 109 个属于这一档。

四档
────
``served``   有抽取器，且存活台账显示跟得上
``frozen``   观测层有行，但**当前没有可跑的抽取器**（历史上抽过，今天没人刷新）
``unwired``  观测层零行且没有抽取器（从未进过观测层）
``lagging``  有抽取器，但台账显示已死 / 末次失败 / 从未跑成
``unknown``  判不了（无成功爬取记录、库不可达、清单目录不可用）

``frozen`` 与 ``unwired`` **不许合并**：前者已有数据（在原值上静止变旧），后者从来
没有过——处置动作不同（找回旧抽取器 vs 新建一个）。``unknown`` 同样独立：那是
「没查成」。2026-11-21 实测三档分别 28 / 63 / 3。

只读：本脚本不写任何控制面表、观测表或爬取表。产物落
``reports/semantic-coverage/<date>.{json,md}``。

用法::

    python3 scripts/semantic_coverage.py                    # 人读（需 DSN）
    python3 scripts/semantic_coverage.py --json             # 机读
    python3 scripts/semantic_coverage.py --check-baseline   # 基线 diff，CI 用
    python3 scripts/semantic_coverage.py --snapshot f.json  # 离线（测试/复现）

退出码：0 无新增问题；1 有新增 ``unwired``（基线外）；2 判不出（缺 DSN/连不上）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
REPORTS = REPO / "reports" / "semantic-coverage"

sys.path.insert(0, str(SCRIPTS))

#: 判定与范围读取都在 check_observation_lag 里（同一套判据，不复制）。
from check_observation_lag import (  # noqa: E402
    DEFAULT_CYCLE_DAYS, KNOWN_GAPS, LAG_CYCLES, MAX_AGE_DAYS,
    classify, extractor_source_names, lit_source_names,
)

#: 与 check_observation_lag.SQL 同形（同一批表、同一口径）。合并进来的差别：
#: 这里不预先限定源，由调用方按亮着的源全集过滤——范围必须由清单定，不能由
#: 「库里有谁」反推。
SQL = """
WITH crawl AS (
    SELECT source,
           max(started_at) AS crawl_ts,
           avg(gap)        AS cycle
      FROM (
        SELECT source, started_at,
               started_at - lag(started_at) OVER (PARTITION BY source ORDER BY started_at) AS gap
          FROM crawl_runs
         WHERE status = 'success'
      ) g
     WHERE gap IS NOT NULL AND gap < interval '30 days'
     GROUP BY source
),
exec AS (
    SELECT DISTINCT ON (src)
           substring(detail from 'src=([a-z0-9.-]+)') AS src,
           status, started_at
      FROM executions
     WHERE detail LIKE 'wave src=%'
     ORDER BY src, started_at DESC
),
obs AS (
    SELECT source_used AS source, max(date) AS data_latest, count(*) AS rows
      FROM semantic_observations
     GROUP BY source_used
)
SELECT cl.source, cl.started_at AS crawl_ts, c.cycle,
       o.data_latest, o.rows,
       e.started_at AS last_run, e.status AS last_status
  FROM (SELECT source, max(started_at) AS started_at FROM crawl_runs
         WHERE status = 'success' GROUP BY source) cl
  LEFT JOIN crawl c ON c.source = cl.source
  LEFT JOIN obs   o ON o.source = cl.source
  LEFT JOIN exec  e ON e.src    = cl.source
"""


def lit_sources() -> dict[str, str]:
    """亮着的源 -> schedule。``schedule`` 非空即亮着（与 onboarding_funnel 的 lit 同口径）。"""
    try:
        import yaml
    except ImportError as exc:  # 判不出，不许当成「没有亮着的源」
        raise RuntimeError(f"缺 pyyaml，读不了清单：{exc}") from exc
    out: dict[str, str] = {}
    for p in sorted((REPO / "spiders").glob("*/manifest.yaml")):
        m = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        sched = (m.get("schedule") or "").strip()
        if sched:
            out[m.get("name") or p.parent.name] = sched
    return out


def extractor_sources() -> set[str]:
    """仓内有常驻调用方的抽取器源（wave 包 + 独立 extract_*.py，见判据处）。"""
    return extractor_source_names(SCRIPTS)


def build_rows(lit: dict[str, str], extractors: set[str], facts: dict[str, dict]) -> list[dict]:
    rows: list[dict] = []
    for source in sorted(lit):
        f = facts.get(source, {})
        level, msg = classify(
            has_extractor=source in extractors,
            crawl_ts=f.get("crawl_ts"),
            data_latest=f.get("data_latest"),
            last_run=f.get("last_run"),
            last_status=f.get("last_status"),
            cycle_days=f.get("cycle_days"),
            obs_rows=f.get("obs_rows"),
        )
        rows.append({
            "source": source,
            "schedule": lit[source],
            "has_extractor": source in extractors,
            "verdict": level,
            "message": msg,
            "known_gap": KNOWN_GAPS.get(source),
            "crawl_ts": f.get("crawl_ts"),
            "last_run": f.get("last_run"),
            "last_status": f.get("last_status"),
            "data_latest": f.get("data_latest"),
            "obs_rows": f.get("obs_rows"),
        })
    return rows


def _parse_dsn_or_fail() -> str | None:
    for k in ("FD_OPEN_DATA_DSN", "FD_OPEN_DATA_MCP_DATABASE_URL", "FD_CRAWL_DB_URL"):
        v = os.environ.get(k)
        if v:
            return re.sub(r"^(postgres(?:ql)?)\+[a-z0-9_]+://", r"\1://", v.strip())
    return None


def fetch_facts() -> dict[str, dict]:
    dsn = _parse_dsn_or_fail()
    if not dsn:
        raise RuntimeError("缺 DSN（FD_OPEN_DATA_DSN / FD_OPEN_DATA_MCP_DATABASE_URL）")
    import psycopg2

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(SQL)
            raw = cur.fetchall()
    finally:
        conn.close()
    facts: dict[str, dict] = {}
    for source, crawl_ts, cycle, data_latest, n_obs, last_run, last_status in raw:
        cycle_days = (cycle.total_seconds() / 86400.0) if cycle is not None else None
        facts[source] = {
            "crawl_ts": crawl_ts, "cycle_days": cycle_days,
            "data_latest": data_latest, "obs_rows": n_obs,
            "last_run": last_run, "last_status": last_status,
        }
    return facts


def load_snapshot(path: Path) -> dict[str, dict]:
    """离线夹具：``{"facts": {source: {...}}}``，字段名与 fetch_facts 一致。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("facts", data)


# ── 基线 ───────────────────────────────────────────────────────────────────
#
# 两档各一份基线：frozen 与 unwired 处置不同，合并计数会让「一个 frozen 修好、
# 一个 unwired 新增」这种一进一出判成无变化。

BASELINE_FROZEN = REPO / "config" / "frozen-baseline.txt"
BASELINE_UNWIRED = REPO / "config" / "unwired-baseline.txt"


def read_baseline(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line)
    return out


def baseline_diff(rows: list[dict], verdict: str = "unwired",
                  path: Path | None = None) -> tuple[list[str], list[str], list[str]]:
    """返回 (新增的该档源, 从基线消失的, 基线里仍存在的)。"""
    current = {r["source"] for r in rows if r["verdict"] == verdict}
    base = read_baseline(path or (BASELINE_FROZEN if verdict == "frozen"
                                  else BASELINE_UNWIRED))
    return sorted(current - base), sorted(base - current), sorted(current & base)


def check_baselines(rows: list[dict]) -> int:
    """两档各 diff 一次；任一档出现基线外的新源即返回 1。"""
    rc = 0
    for verdict, path in (("frozen", BASELINE_FROZEN), ("unwired", BASELINE_UNWIRED)):
        new, gone, kept = baseline_diff(rows, verdict, path)
        print(f"{verdict} 基线（{path.name}）：{len(kept)} 条仍存在"
              f"，新增 {len(new)}，已离开 {len(gone)}")
        for s in new:
            print(f"  ✗ 新增 {verdict}：{s}")
        for s in gone:
            print(f"  · 已离开 {verdict} 基线（请更新 {path.name}）：{s}")
        if new:
            rc = 1
    return rc


# ── 产物 ───────────────────────────────────────────────────────────────────

def summarise(rows: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    return counts


def render_markdown(rows: list[dict], params: dict) -> str:
    counts = summarise(rows)
    lines = [
        "# 语义覆盖三联账",
        "",
        f"- 生成：{params['generated_at']}",
        f"- 亮着单元：{len(rows)}",
        f"- 判定：served {counts.get('served', 0)} / frozen {counts.get('frozen', 0)}"
        f" / unwired {counts.get('unwired', 0)} / lagging {counts.get('lagging', 0)}"
        f" / unknown {counts.get('unknown', 0)}",
        f"- 判据参数：lag_cycles={params['lag_cycles']} max_age_days={params['max_age_days']}"
        f" default_cycle_days={params['default_cycle_days']}",
        "",
        "| 源 | 判定 | 抽取器 | 最近成功爬取 | 末次抽取 | 数据最新期 | 说明 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda x: (x["verdict"], x["source"])):
        mark = {"served": " ", "frozen": "~", "unwired": "!", "lagging": "x",
                "unknown": "?"}[r["verdict"]]
        lines.append(
            f"| {r['source']} | {mark} {r['verdict']} | {'有' if r['has_extractor'] else '—'}"
            f" | {r['crawl_ts'] or '—'} | {r['last_run'] or '—'} | {r['data_latest'] or '—'}"
            f" | {r['message']} |"
        )
    return "\n".join(lines) + "\n"


def _dump(payload: dict) -> str:
    """db 返回的时刻戳是 datetime，``default=str`` 让产物可直接落盘。"""
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def write_reports(rows: list[dict], params: dict) -> tuple[Path, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    stem = params["generated_at"][:10]
    jp = REPORTS / f"{stem}.json"
    mp = REPORTS / f"{stem}.md"
    jp.write_text(_dump({"params": params, "summary": summarise(rows), "sources": rows}),
                  encoding="utf-8")
    mp.write_text(render_markdown(rows, params), encoding="utf-8")
    return jp, mp


def main() -> int:
    ap = argparse.ArgumentParser(prog="semantic_coverage")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--snapshot", type=Path, help="离线夹具（JSON）")
    ap.add_argument("--check-baseline", action="store_true", help="CI：基线 diff")
    ap.add_argument("--write-reports", action="store_true", help="落 reports/")
    args = ap.parse_args()

    try:
        lit = lit_sources()
        extractors = extractor_sources()
    except Exception as exc:  # noqa: BLE001 - 判不出必须报出来，不许静默为空
        print(f"判不出：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if not lit:
        print("判不出：亮着的源全集为空（清单目录不可用？）", file=sys.stderr)
        return 2

    if args.snapshot:
        facts = load_snapshot(args.snapshot)
    else:
        try:
            facts = fetch_facts()
        except Exception as exc:  # noqa: BLE001
            print(f"判不出：{type(exc).__name__}: {exc}", file=sys.stderr)
            return 2

    rows = build_rows(lit, extractors, facts)
    params = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "lag_cycles": LAG_CYCLES,
        "max_age_days": MAX_AGE_DAYS,
        "default_cycle_days": DEFAULT_CYCLE_DAYS,
        "lit_count": len(lit),
        "extractor_count": len(extractors),
    }

    if args.write_reports:
        jp, mp = write_reports(rows, params)
        print(f"产物：{jp}\n产物：{mp}", file=sys.stderr)

    if args.check_baseline:
        return check_baselines(rows)

    if args.json:
        print(_dump({"params": params, "summary": summarise(rows), "sources": rows}))
    else:
        counts = summarise(rows)
        for r in rows:
            if r["verdict"] != "served":
                print(f"{r['verdict']:<8} {r['source']:<26} {r['message']}")
        print(f"\n{len(rows)} 个亮着的源：served {counts.get('served', 0)}"
              f" / frozen {counts.get('frozen', 0)}"
              f" / unwired {counts.get('unwired', 0)} / lagging {counts.get('lagging', 0)}"
              f" / unknown {counts.get('unknown', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
