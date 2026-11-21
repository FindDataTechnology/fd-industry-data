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

#: 内存活门：距最近一次成功抽取超过这么多天就报。日更作业留半天缓冲。
#: **为什么用 executions 而不是观测行的时刻戳**：``semantic_observations`` 的写入是
#: ``ON CONFLICT DO NOTHING``——「跑了但上游没新数据」插 0 行、``fetched_at`` 不动。
#: 所以观测行上没有任何痕迹能证明抽取器活着（pharma 文档 §9 记过同一个坑）。
#: ``executions`` 是抽取器自己写的存活台账，每次运行一行，与有没有新数据无关。
MAX_AGE_DAYS = 1.5

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
crawl_latest AS (
    SELECT source, max(started_at) AS crawl_ts
      FROM crawl_runs
     WHERE status = 'success'
     GROUP BY source
),
-- 存活台账：驱动往 detail 里写 `src=<source>`（executions 表没有 source 列，
-- 且一个包含多个源，所以按源而不是按包记）。每个源取最近一行。
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
SELECT cl.source,
       cl.crawl_ts,
       c.cycle,
       o.data_latest,
       o.rows,
       e.started_at AS last_run,
       e.status     AS last_status
  FROM crawl_latest cl
  LEFT JOIN obs  o ON o.source = cl.source
  LEFT JOIN crawl c ON c.source = cl.source
  LEFT JOIN exec  e ON e.src = cl.source
 ORDER BY cl.source
"""


def verdict(crawl_ts, data_latest, last_run, last_status, cycle_days: float | None, *,
            lag_cycles: int = LAG_CYCLES,
            max_age_days: float = MAX_AGE_DAYS,
            default_cycle_days: float = DEFAULT_CYCLE_DAYS) -> tuple[str, str]:
    """返回 ``(级别, 报文)``；级别 ∈ {"ok", "lag", "never", "died", "stale-data"}。

    四问分开答（合起来答过一次，把健康的源判成了滞后）：

    1. **有没有数据** —— 一条观测都没有，但爬取有成功记录 ⇒ ``never``。
    2. **抽取器活着吗** —— ``executions`` 台账里最近一次运行距今多久 ⇒ ``died``。
       这是**唯一**判得了「抽取器停摆」的信号（见 MAX_AGE_DAYS 的注释）。
    3. **上一次跑成功了吗** ⇒ 台账里最近一行是不是 ``failed`` ⇒ ``lag``。
    4. **数据本身旧不旧** —— ``max(date)`` 对 wall clock。这一问**只报事实**，
       从不判红：月频/年频源的期号天生落后 wall clock，判红等于给每一条月频源
       挂一个永远清不掉的红（2026-11-19 实测：15 个源被判红，其中 14 个是
       月频/季频源的正常滞后）。
    """
    if data_latest is None and last_run is None:
        return "never", "爬取有成功记录，但观测层一条都没有，也没有任何抽取台账"

    # ── 第 2 问：抽取器活着吗（主判据）──
    if last_run is not None:
        import datetime as _dt
        ref = crawl_ts or _dt.datetime.now(_dt.timezone.utc)
        if ref.tzinfo is not None and last_run.tzinfo is None:
            ref = ref.replace(tzinfo=None)
        elif ref.tzinfo is None and last_run.tzinfo is not None:
            last_run = last_run.replace(tzinfo=None)
        age = (ref - last_run).total_seconds() / 86400.0
        if age > max_age_days:
            return "died", (
                f"抽取器已 {age:.1f} 天没运行过（末次 {last_run:%Y-%m-%d %H:%M}，"
                f"门限 {max_age_days} 天）—— 排期掉了或作业一直在失败")
        if last_status and str(last_status).lower() != "success":
            return "lag", (
                f"末次抽取状态是 {last_status}（{last_run:%Y-%m-%d %H:%M}）—— 跑了但失败了")
        # 活着且末次成功：数据水位只报事实，不判红
        return "ok", (
            f"抽取器在线（末次 {last_run:%Y-%m-%d %H:%M} 成功）；"
            f"数据最新期 {data_latest}")

    # ── 没有台账（本 change 之前的状态）：退回爬取水位比对，只判「从未抽取」──
    if data_latest is None:
        return "never", "爬取有成功记录，但观测层一条都没有（从未接入抽取）"
    return "ok", f"无存活台账（该源不归 wave 作业管）；数据最新期 {data_latest}"


#: 存量豁免：判据仍然点名，但不计为失败。**逐行带理由与日期**，与
#: `scripts/maintenance-roots-baseline.txt` / `scripts/quarantine.txt` 同一套纪律——
#: 豁免是一条有主的账，不是把判据调松。理由过期就该删掉这行。
#:
#: 为什么需要它：这些是**真实缺口**（不是判据误报），但修复不在本 change 的范围里。
#: 把它们删掉=让判据看不见；让它们判红=这条闸永远红（等于没有闸）。豁免是第三条路。
KNOWN_GAPS: dict[str, str] = {
    # 现在是空的。曾经挂过一条 sec-edgar-facts 的豁免，理由是「公司实体未注册、
    # writer 按 entity_id=None 全部丢弃」——**那是读抽取器 notes 里一句旧提示得出的
    # 结论，没有核库**。2026-11-19 实测：17 个 ticker 实体全部在册，该源有 46 行观测、
    # 16 个实体、覆盖到 2026-09-30。判据当时报 `never` 是因为它查的时刻（11-07）
    # 早于该源首次成功抽取（11-18），不是数据缺失。
    #
    # 留着这段是因为它是一类错误的标本：**豁免的理由必须是查过库的事实，
    # 不能是别的程序打印的一句话**。豁免写错的代价比不写更大——它会让一个健康的源
    # 长期被当成已知缺口，没人再去看它。
}


def tracked_sources(cur) -> list[str]:
    """判据的**范围**：亮着的源全集 ∪ 台账源 ∪ 本仓抽取器源。

    三处来源缺一不可：
    * **亮着的源**（``spiders/*/manifest.yaml`` 的 ``schedule`` 非空）—— 2026-11-21
      补的（change measure-semantic-coverage-gap）：没有它会漏掉 109 个「亮着但
      根本没有抽取器」的源，它们永远不红。
    * **台账**（``executions`` 里 ``wave src=<源>``）—— pod 里跑的时候只有这条路
      可用：挂载卷只带脚本、不带四个抽取器包，扫包会得到 0 个源（2026-11-19 实测，
      判据在集群里「通过」了但一个源都没判，比红更糟）。
    * **本仓包**—— 本地跑的时候能在台账还没写之前就知道该有哪些源（新加的
      抽取器不会因为「还没跑过」而漏判）。

    三者的并集才是完整范围；读不到的一路在 stderr 说明，不许因此静默缩范围。
    """
    import sys
    from pathlib import Path

    names: set[str] = set()
    cur.execute("SELECT DISTINCT substring(detail from 'src=([a-z0-9.-]+)')"
                " FROM executions WHERE detail LIKE 'wave src=%'")
    names |= {r[0] for r in cur.fetchall() if r[0]}

    scripts_dir = Path(os.path.abspath(__file__)).parent

    # 亮着的源全集：判据范围的**第一来源**（change measure-semantic-coverage-gap）。
    # 没有抽取器的源只有这一路能看见；缺它 = 109 个源永远不红。
    lit = lit_source_names(scripts_dir)
    if lit is None:
        print("WARN: 清单目录不可用，「亮着的源」这一路未纳入范围（判据范围缩水）",
              file=sys.stderr)
    else:
        names |= lit

    # 包扫描是**可选**增强：挂载环境里没有包目录，扫不到就跳过（不是错误）
    if (scripts_dir / "wave_extract_split.py").exists():
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        try:
            import wave_extract_split as wes
            report = wes.scan_scripts_dir(scripts_dir)
            for mods in report.values():
                names |= {m["source"] for m in mods if m["kind"] == wes.SCHEDULED}
        except Exception as e:  # noqa: BLE001
            print(f"WARN: 包扫描不可用（{type(e).__name__}: {e}），只用台账定范围",
                  file=sys.stderr)
    return sorted(names)


def dsn() -> str | None:
    for k in ("FD_OPEN_DATA_DSN", "FD_OPEN_DATA_MCP_DATABASE_URL", "FD_CRAWL_DB_URL"):
        v = os.environ.get(k)
        if v:
            # 生产 Secret 是 SQLAlchemy 形态（postgresql+psycopg2://），psycopg2 不认
            import re
            return re.sub(r"^(postgres(?:ql)?)\+[a-z0-9_]+://", r"\1://", v.strip())
    return None


def lit_source_names(scripts_dir=None) -> set[str] | None:
    """亮着的源：``spiders/*/manifest.yaml`` 的 ``schedule`` 非空。

    这是判据范围的**第一来源**（change measure-semantic-coverage-gap）：没有
    抽取器的源只有这一路能看见。与 `onboarding_funnel` 的 `lit` 同口径。
    读不到清单（缺 yaml 或无目录）时返回 ``None`` ——「判不出」不等于「没有源」，
    调用方必须把 ``None`` 与空集分开处理。
    """
    from pathlib import Path

    repo = Path(scripts_dir).parent if scripts_dir else Path(__file__).resolve().parents[1]
    spiders = repo / "spiders"
    if not spiders.is_dir():
        return None
    try:
        import yaml
    except ImportError:
        return None
    out: set[str] = set()
    for p in spiders.glob("*/manifest.yaml"):
        try:
            m = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 - 单个坏清单不拖垮范围，但会少一个源
            continue
        if (m.get("schedule") or "").strip():
            out.add(m.get("name") or p.parent.name)
    return out


def extractor_source_names(scripts_dir=None) -> set[str]:
    """仓内**有常驻调用方**的抽取器源。

    两处来源：
    * 四个波包（``wave_extract_split`` 的 AST 判据，只认 ``crawl_items`` 型——
      它们在 pod 里跑，读库不打上游）；
    * 独立的 ``scripts/extract_*.py``（``SOURCE = ...`` / ``SOURCES = {...}`` /
      ``_SOURCE_OF = {...}``）。2026-11-21 实测：漏掉这一路会把 ``uk-hpi``
      （18,906 行观测）这类源误判成 unwired。

    只读 ``extract_*.py``、不做 AST 深判：这七个脚本各自在 CI 作业里显式调用
    （见各 workflow），比 wave 包更容易确认「有人跑」。
    """
    import re
    import sys
    from pathlib import Path

    scripts = Path(scripts_dir) if scripts_dir else Path(os.path.abspath(__file__)).parent
    out: set[str] = set()
    if (scripts / "wave_extract_split.py").is_file():
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        import wave_extract_split as wes

        report = wes.scan_scripts_dir(scripts)
        out |= {m["source"] for mods in report.values() for m in mods
                if m["kind"] == wes.SCHEDULED}

    for p in sorted(scripts.glob("extract_*.py")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in re.finditer(r'^SOURCE\s*=\s*["\']([a-z0-9_.-]+)["\']', text, re.M):
            out.add(m.group(1))
        for m in re.finditer(r'^(?:SOURCES|_SOURCE_OF)\s*=\s*\{', text, re.M):
            end = text.find("\n}", m.start())
            block = text[m.start():end if end > 0 else m.start() + 6000]
            out |= set(re.findall(r'["\']([a-z0-9_.-]+)["\']\s*:', block))
            out |= set(re.findall(r':\s*["\']([a-z0-9_.-]+)["\']', block))
    return out


def classify(*, has_extractor: bool, crawl_ts, data_latest, last_run, last_status,
             cycle_days, obs_rows=None) -> tuple[str, str]:
    """五档判定（spec semantic-coverage-ledger）。

    ``served``    有抽取器，跟随
    ``lagging``   有抽取器，但台账显示已死 / 末次失败
    ``frozen``    观测层有行，但**当前没有抽取器** —— 历史上抽过，今天没人刷新
    ``unwired``   观测层零行且没有抽取器 —— 从未进过观测层
    ``unknown``   无成功爬取记录（判不出是否在产）

    ``unknown`` 优先级最高：读不到就不许猜。``frozen`` 与 ``unwired`` 分开的理由：
    前者已有数据（会在原值上静止变旧），后者从来没有过——处置动作不同（找旧抽取器
    vs 新建一个）。2026-11-21 实测这两档各 28 / 63 个源。
    """
    if crawl_ts is None:
        return "unknown", "该源没有成功爬取记录（判不出是否在产）"
    if not has_extractor:
        if obs_rows:
            return "frozen", (
                f"观测层有 {obs_rows} 行，但仓内没有当前可跑的抽取器"
                "（历史上抽过，现在没人刷新）")
        return "unwired", "亮着，但仓内没有把其产物归一进观测层的抽取器"
    level, msg = verdict(crawl_ts, data_latest, last_run, last_status, cycle_days)
    return ("served", msg) if level == "ok" else ("lagging", msg)


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
            allowed_sources = (set(tracked_sources(cur))
                               if scope == "wave" else None)
    finally:
        conn.close()
    if not rows:
        print("判不出：crawl_runs 里没有任何成功记录（库空或连错库）", file=sys.stderr)
        return 2, []

    if allowed_sources is not None:
        rows = [r for r in rows if r[0] in allowed_sources]

    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    extractors = extractor_source_names(scripts_dir)
    # 抽取器清单读不到（生产 pod 只挂了 check_observation_lag.py +
    # wave_extract_split.py，没有 extract_*.py）⇒ 每个源都会被判成「无抽取器」，
    # `lagging` 永远不出现，闸变成**永久绿**——比红更糟。
    #
    # 降级而不是失效：范围退回 pod 里本来就有的那两路（台账 ∪ 波包），只用
    # served/lagging/unknown 三档判，并在 stderr 明说少判了什么。这与改动前的
    # 行为等价，不会让上产引入回归；五档（frozen/unwired）只在能读到清单的
    # 环境跑（semantic_coverage.py 的每日作业，它 checkout 全仓）。
    degraded = not extractors
    if degraded:
        print("WARN: 抽取器清单不可用（无 extract_*.py）——本次只判 served/lagging/"
              "unknown，不判 frozen/unwired（五档判据需全仓 checkout）",
              file=sys.stderr)

    out = []
    bad = 0
    seen: set[str] = set()
    for source, crawl_ts, cycle, data_latest, n_obs, last_run, last_status in rows:
        # psycopg2 把 `avg(interval)` 作为 timedelta 返回
        cycle_days = (cycle.total_seconds() / 86400.0) if cycle is not None else None
        level, msg = classify(
            # 降级模式下不把「清单读不到」当成「无抽取器」：判据退回改动前的
            # served/lagging 两档（见上方 degraded 注释）。
            has_extractor=True if degraded else (source in extractors),
            crawl_ts=crawl_ts, data_latest=data_latest,
            last_run=last_run, last_status=last_status, cycle_days=cycle_days,
            obs_rows=n_obs)
        seen.add(source)
        exempt = KNOWN_GAPS.get(source)
        # unwired / frozen 不计入退出码：63+28 个存量会让这条闸永久红
        # （基线 diff 在 semantic_coverage.py --check-baseline 里做）。
        if level == "lagging" and not exempt:
            bad += 1
        out.append({"source": source, "level": level,
                    "message": msg, "known_gap": exempt,
                    "crawl_ts": str(crawl_ts) if crawl_ts else None,
                    "last_run": str(last_run) if last_run else None,
                    "last_status": last_status,
                    "data_latest": str(data_latest) if data_latest else None,
                    "obs_rows": n_obs})

    # 亮着但库里没有一条成功爬取的源：判 unknown，不许因为「SQL 里没有它」而消失。
    # 这一档正是「抓取从未成功过」，与 unwired（抓到了但没抽取器）不同。
    if allowed_sources is not None:
        for source in sorted(allowed_sources - seen):
            level, msg = classify(has_extractor=source in extractors, crawl_ts=None,
                                  data_latest=None, last_run=None, last_status=None,
                                  cycle_days=None)
            out.append({"source": source, "level": level, "message": msg,
                        "known_gap": KNOWN_GAPS.get(source), "crawl_ts": None,
                        "last_run": None, "last_status": None,
                        "data_latest": None, "obs_rows": None})
    return (1 if bad else 0), out


def self_test() -> int:
    """反向用例：不连库，只验判据本身。

    每个例子都对应一次实测（design D4 + 2026-11-19 首轮）：

      * **dce 修复前**：台账里末次是 11-07（11 天前）⇒ died。这是本 change 的存在理由。
      * **dce 修复后**：末次是刚才 ⇒ ok。
      * **fert-cn 假阳性回归**：数据期停在 2026-08（上游月更）、抓取刚跑过 ⇒ ok。
        修复前用 `fetched_at` 判，它被判成滞后 72 天——错的是判据，不是数据。
      * **末次失败** ⇒ lag（跑了但没成，与「没跑」分开报）。
      * **从未抽取** ⇒ never。
      * **无台账的源**（不归 wave 作业管）⇒ ok，不误报。
    """
    from datetime import datetime, timedelta

    now = datetime(2026, 11, 19, 12, 0)
    cases = [
        # (crawl, data_latest, last_run, last_status, cycle, 期望级别)
        (now, "2026-11-06", now - timedelta(days=12), "success", 1.0, "died"),  # dce 修复前
        (now, "2026-11-18", now - timedelta(hours=18), "success", 1.0, "ok"),    # dce 修复后
        (now, "2026-08", now - timedelta(hours=2), "success", 40.0, "ok"),       # fert-cn 假阳性
        (now, "2026-11-18", now - timedelta(hours=6), "failed", 1.0, "lag"),     # 跑了但失败
        (now, None, None, None, 1.0, "never"),                                  # 从未抽取
        (now, "2026-09", None, None, 1.0, "ok"),                                # 无台账（不归本作业）
        (now, None, now - timedelta(days=5), "success", 1.0, "died"),           # 有台账无数据
    ]
    failures = []
    for crawl, latest, run, status, cycle, want in cases:
        got, msg = verdict(crawl, latest, run, status, cycle)
        if got != want:
            failures.append(
                f"  crawl={crawl:%m-%d} latest={latest} last_run={run and run:%m-%d} "
                f"status={status} → {got}（期望 {want}）：{msg}")
    if failures:
        print("selftest FAILED:\n" + "\n".join(failures), file=sys.stderr)
        return 1

    # 五档分类的边界（change measure-semantic-coverage-gap）：
    # 抓取成功 + 无抽取器 + 观测零行 = unwired；观测有行 = frozen（历史抽过）；
    # 无成功爬取 = unknown（不是 unwired）。
    cls_cases = [
        (True, now, None, now - timedelta(hours=1), "success", None, "served"),
        (False, now, None, None, None, None, "unwired"),
        (False, now, None, None, None, 1234, "frozen"),
        (True, now, None, now - timedelta(days=9), "success", 5, "lagging"),
        (False, None, None, None, None, None, "unknown"),
    ]
    for has_ext, crawl, latest, run, status, obs, want in cls_cases:
        got, msg = classify(has_extractor=has_ext, crawl_ts=crawl, data_latest=latest,
                            last_run=run, last_status=status, cycle_days=1.0, obs_rows=obs)
        if got != want:
            failures.append(f"  classify(has_extractor={has_ext}, obs={obs}) → {got}"
                            f"（期望 {want}）：{msg}")
    if failures:
        print("selftest FAILED:\n" + "\n".join(failures), file=sys.stderr)
        return 1
    print(f"selftest OK（{len(cases)} 例 verdict 四档 + fert-cn 假阳性回归"
          f"；{len(cls_cases)} 例 classify served/unwired/frozen/lagging/unknown）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="check_observation_lag")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--quiet-ok", action="store_true", help="只打印滞后/从未抽取的源")
    ap.add_argument("--scope", choices=("wave", "all"), default="wave",
                    help="wave=只判亮着 / 台账 / 抽取器并集里的源（门禁用，含 109 个"
                         "无抽取器的 unwired）；all=全表盘点")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    rc, rows = run(args.scope)
    if args.json:
        print(json.dumps({"exit": rc, "sources": rows}, ensure_ascii=False, indent=2))
        return rc

    shown = [r for r in rows if not (args.quiet_ok and r["level"] == "served")]
    for r in shown:
        mark = {"served": "  ", "lagging": "✗ ", "unwired": "! ", "unknown": "? "}[r["level"]]
        suffix = "（存量豁免，不计失败）" if r.get("known_gap") and r["level"] != "served" else ""
        print(f"{mark}{r['source']:<26} {r['message']}{suffix}")
    counts = {}
    for r in rows:
        counts[r["level"]] = counts.get(r["level"], 0) + 1
    print(f"\n{len(rows)} 个源：served {counts.get('served', 0)}"
          f" / lagging {counts.get('lagging', 0)} / unwired {counts.get('unwired', 0)}"
          f" / unknown {counts.get('unknown', 0)}")
    for r in rows:
        if r["level"] != "served" and r.get("known_gap"):
            print(f"  豁免 {r['source']}: {r['known_gap']}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
