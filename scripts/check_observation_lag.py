#!/usr/bin/env python3
"""观测水位 vs 爬取水位——「抽取器还活着吗」的判据（change
schedule-wave-observation-extraction，design D4）。

要回答的问题
────────────
某个源**爬虫在跑**，但它**抽到观测层**了吗？

为什么不能用现成的两种判据
──────────────────────────
* ``freshness``（``max(fetched_at)`` 对 wall clock）分不清两件事：上游停更
  （``cncotton`` 2025-08 就停发了）会让它常年红，而常年红的闸等于没有闸。
* ``staleness``（组间落后）更糟：参照物是本表内数据自身的最大值，整表停摆时
  参照物和水位一起冻结，lag 恒 0 —— **死门**（pharma 文档 §9 有实测反例）。

正确的判据是**两水位相减**：

    crawl  = max(crawl_runs.started_at)  WHERE source = X AND status = 'success'
    obs    = max(semantic_observations.fetched_at) WHERE source_used = X

* crawl 前进而 obs 不动  ⇒ 抽取器死了（本判据点名的那一档）
* 两者一起冻             ⇒ 上游停了（不是本判据的事，交给新鲜度告警）

``fetched_at`` 而不是 ``date``：``date`` 是数据自身的期间（可能本来就是历史），
``fetched_at`` 是**抽取动作发生的时刻**。写入是 ``ON CONFLICT DO NOTHING``，复跑
插 0 行且不改 ``fetched_at``，所以「抽取器活着」在数据里**只**体现在这个列上。

用法::

    python3 scripts/check_observation_lag.py                 # 人读报告
    python3 scripts/check_observation_lag.py --json          # 机读
    python3 scripts/check_observation_lag.py --self-test     # 反向用例（不连库）

退出码：0 全部跟得上；1 至少一个源滞后；2 判不出（DSN 缺失/连不上/表为空）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

#: 观测水位落后爬取水位超过这个倍数 × 该源的爬取间隔，就报滞后。
#: 取 2 而不是 1：日频源在「今天没跑」和「跑挂了」之间有一步之遥，1 倍会把
#: 一次正常的调度抖动报成故障；2 个周期仍落后则是结构性的。
LAG_CYCLES = 2

#: 没有足够爬取历史算间隔时的兜底（天）。
DEFAULT_CYCLE_DAYS = 1.0

#: 已退役/停用的源（``crawl_sources.enabled = false`` 或 manifest parked）不判：
#: 它们本来就不该再推进，报红没有可执行的动作。
SQL = """
WITH crawl AS (
    SELECT source,
           max(started_at) AS crawl_ts,
           count(*)        AS runs,
           -- 相邻两次的典型间隔（中位数的近似：取最近若干次的平均）
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
crawl_latest AS (
    SELECT source, max(started_at) AS crawl_ts
      FROM crawl_runs
     WHERE status = 'success'
     GROUP BY source
),
obs AS (
    SELECT source_used AS source, max(fetched_at) AS obs_ts, count(*) AS rows
      FROM semantic_observations
     GROUP BY source_used
)
SELECT cl.source,
       cl.crawl_ts,
       o.obs_ts,
       o.rows,
       c.cycle
  FROM crawl_latest cl
  LEFT JOIN obs o ON o.source = cl.source
  LEFT JOIN crawl c ON c.source = cl.source
 ORDER BY cl.source
"""


def verdict(crawl_ts, obs_ts, cycle_days: float | None, *,
            lag_cycles: int = LAG_CYCLES,
            default_cycle_days: float = DEFAULT_CYCLE_DAYS) -> tuple[str, str]:
    """返回 ``(级别, 报文)``；级别 ∈ {"ok", "lag", "never"}。

    ``never`` = 爬过但一条观测都没有。这**不是**「滞后」，是「这条源从未接入
    抽取」——两者要分开报：前者该去查抽取器，后者该去写抽取器。
    """
    if obs_ts is None:
        return "never", "爬取有成功记录，但观测层一条都没有（从未接入抽取）"
    if crawl_ts is None:
        return "ok", "无爬取记录（不判）"
    cycle = (cycle_days or default_cycle_days)
    allowed = cycle * lag_cycles
    # 两列来自不同的表/写入路径：`crawl_runs.started_at` 是 timestamptz，
    # `semantic_observations.fetched_at` 也可能是无时区的（历史行）。相近即弃时区，
    # 免得一次「naive vs aware」把整条判据打成异常。
    if crawl_ts.tzinfo is not None and obs_ts.tzinfo is None:
        crawl_ts = crawl_ts.replace(tzinfo=None)
    elif crawl_ts.tzinfo is None and obs_ts.tzinfo is not None:
        obs_ts = obs_ts.replace(tzinfo=None)
    lag_days = (crawl_ts - obs_ts).total_seconds() / 86400.0
    if lag_days <= allowed:
        return "ok", f"跟得上（滞后 {lag_days:.1f} 天 ≤ 允差 {allowed:.1f} 天）"
    return "lag", (
        f"观测滞后 {lag_days:.1f} 天 > 允差 {allowed:.1f} 天"
        f"（爬取 {crawl_ts:%Y-%m-%d %H:%M} / 观测 {obs_ts:%Y-%m-%d %H:%M}，"
        f"周期约 {cycle:.1f} 天）—— 爬虫在跑而抽取没跟上")


#: 存量豁免：判据仍然点名，但不计为失败。**逐行带理由与日期**，与
#: `scripts/maintenance-roots-baseline.txt` / `scripts/quarantine.txt` 同一套纪律——
#: 豁免是一条有主的账，不是把判据调松。理由过期就该删掉这行。
#:
#: 为什么需要它：这些是**真实缺口**（不是判据误报），但修复不在本 change 的范围里。
#: 把它们删掉=让判据看不见；让它们判红=这条闸永远红（等于没有闸）。豁免是第三条路。
KNOWN_GAPS: dict[str, str] = {
    "sec-edgar-facts":
        "（2026-11-19）抽取器能产出 329（flower）+796（restaurant）行，但公司实体"
        "未注册 ⇒ writer 按 entity_id=None 全部丢弃（notes 里明写「672 条事实的公司"
        "实体未注册，未写」）。修法是注册实体，属独立的一笔，不在本 change 范围",
}


def tracked_sources() -> list[str]:
    """判据的**默认范围**：本仓四个波包里**有 crawl_items 型抽取器**的源。

    只收 ``crawl_items`` 型的源，而不是全部抽取器写的源：live-fetch 型
    （``live_items`` / ``_load_unit``）不排期（design D3），它们的观测落后是
    **已知且有意**的，判它们红等于让这条闸永远清不掉。

    为什么不判全表：全表实测 153 个源里 85 个「从未抽取」、23 个滞后——那些是
    「还没写抽取器」与「抽取器在别处（semantic-extract-*）」的情形。把它们一起
    判红会让这条闸常年红，而**常年红的闸等于没有闸**。
    本判据只管一件事：**已经有定时抽取器的源，抽取器还在跟爬取走吗**。

    全表模式仍在（``--scope all``），用途是盘点而非门禁。
    """
    import sys
    from pathlib import Path
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import wave_extract_split as wes
    report = wes.scan_scripts_dir(scripts_dir)
    names: set[str] = set()
    for mods in report.values():
        for m in mods:
            if m["kind"] == wes.SCHEDULED:
                names.add(m["source"])
    return sorted(names)


def dsn() -> str | None:
    for k in ("FD_OPEN_DATA_DSN", "FD_OPEN_DATA_MCP_DATABASE_URL", "FD_CRAWL_DB_URL"):
        v = os.environ.get(k)
        if v:
            # 生产 Secret 是 SQLAlchemy 形态（postgresql+psycopg2://），psycopg2 不认
            import re
            return re.sub(r"^(postgres(?:ql)?)\+[a-z0-9_]+://", r"\1://", v.strip())
    return None


def run(scope: str = "wave") -> tuple[int, list[dict]]:
    d = dsn()
    if not d:
        print("判不出：没有 FD_OPEN_DATA_DSN / FD_OPEN_DATA_MCP_DATABASE_URL", file=sys.stderr)
        return 2, []
    try:
        import psycopg2
        conn = psycopg2.connect(d)
    except Exception as exc:  # noqa: BLE001
        print(f"判不出：连不上库（{type(exc).__name__}: {exc}）", file=sys.stderr)
        return 2, []
    try:
        with conn.cursor() as cur:
            cur.execute(SQL)
            rows = cur.fetchall()
    finally:
        conn.close()
    if not rows:
        print("判不出：crawl_runs 里没有任何成功记录（库空或连错库）", file=sys.stderr)
        return 2, []

    if scope == "wave":
        allowed_sources = set(tracked_sources())
        rows = [r for r in rows if r[0] in allowed_sources]

    out = []
    bad = 0
    for source, crawl_ts, obs_ts, n_obs, cycle in rows:
        # psycopg2 把 `avg(interval)` 作为 timedelta 返回
        cycle_days = (cycle.total_seconds() / 86400.0) if cycle is not None else None
        level, msg = verdict(crawl_ts, obs_ts, cycle_days)
        exempt = KNOWN_GAPS.get(source)
        if level in ("lag", "never") and not exempt:
            bad += 1
        out.append({"source": source, "level": level, "message": msg,
                    "known_gap": exempt,
                    "crawl_ts": str(crawl_ts) if crawl_ts else None,
                    "obs_ts": str(obs_ts) if obs_ts else None,
                    "obs_rows": n_obs})
    return (1 if bad else 0), out


def self_test() -> int:
    """反向用例：不连库，只验判据本身。

    三例都来自 design D4 的实测反例：
      1. 爬取前进、观测停住 ⇒ lag（dce 2026-11-18 的真实形态：爬取到 11-18、
         观测停在 11-07）；
      2. 两者一起冻 ⇒ ok（cncotton：上游停更，不是抽取器的错）；
      3. 从未抽取 ⇒ never（与 lag 分开报）。
    """
    from datetime import datetime, timedelta

    now = datetime(2026, 11, 18, 12, 0)
    cases = [
        # (crawl, obs, cycle, 期望级别)
        (now, now - timedelta(days=11), 1.0, "lag"),          # dce 实测形态
        (now - timedelta(days=100), now - timedelta(days=100), 1.0, "ok"),  # 一起冻
        (now, None, 1.0, "never"),                             # 从未抽取
        (now, now - timedelta(hours=6), 1.0, "ok"),            # 同日正常
        (now, now - timedelta(days=3), 1.0, "lag"),            # 日频滞后 3 天
        (now, now - timedelta(days=3), 30.0, "ok"),            # 月频滞后 3 天正常
    ]
    failures = []
    for crawl, obs, cycle, want in cases:
        got, msg = verdict(crawl, obs, cycle)
        if got != want:
            failures.append(f"  crawl={crawl} obs={obs} cycle={cycle} "
                            f"→ {got}（期望 {want}）：{msg}")
    if failures:
        print("selftest FAILED:\n" + "\n".join(failures), file=sys.stderr)
        return 1
    print(f"selftest OK（{len(cases)} 例，含 lag/ok/never 三档与月频反例）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="check_observation_lag")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--quiet-ok", action="store_true", help="只打印滞后/从未抽取的源")
    ap.add_argument("--scope", choices=("wave", "all"), default="wave",
                    help="wave=只判本仓四个波包里有抽取器的源（门禁用）；"
                         "all=全表盘点（不做门禁，会有大量「从未抽取」）")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    rc, rows = run(args.scope)
    if args.json:
        print(json.dumps({"exit": rc, "sources": rows}, ensure_ascii=False, indent=2))
        return rc

    shown = [r for r in rows if not (args.quiet_ok and r["level"] == "ok")]
    for r in shown:
        mark = {"ok": "  ", "lag": "✗ ", "never": "? "}[r["level"]]
        suffix = "（存量豁免，不计失败）" if r.get("known_gap") and r["level"] != "ok" else ""
        print(f"{mark}{r['source']:<26} {r['message']}{suffix}")
    n_lag = sum(1 for r in rows if r["level"] == "lag" and not r.get("known_gap"))
    n_never = sum(1 for r in rows if r["level"] == "never" and not r.get("known_gap"))
    n_exempt = sum(1 for r in rows if r["level"] != "ok" and r.get("known_gap"))
    print(f"\n{len(rows)} 个源：滞后 {n_lag} / 从未抽取 {n_never} / 存量豁免 {n_exempt} / 正常 "
          f"{len(rows) - n_lag - n_never - n_exempt}")
    for r in rows:
        if r["level"] != "ok" and r.get("known_gap"):
            print(f"  豁免 {r['source']}: {r['known_gap']}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
