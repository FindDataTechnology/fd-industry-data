#!/usr/bin/env python3
"""6.3 水位线接管：农业概念的增量抓取计划（dry-run 留档）。

**要证明的事**：`plan_crawl --since-last` 对农业概念产出的计划**只含新报告期**，
既有期不产生抓取。所以这里不是「跑一次看看」，而是：
① 生成 plan 并落档；② 把 plan 里每个 concept 的 `date_range.start` 与库内该概念的
`max(date)` 逐条对账，**断言 start > 存量最大期**；③ 把 plan 的 wanted 期数写清楚。

**为什么不走 CLI**：`fd-open-data-mcp` 的 CLI 会先 `load_dotenv(override=True)`
读它自己的 `.env.local`（本地隧道地址），把外部传入的 DSN 覆盖掉；本脚本直接构造
`Database(dsn)` 调 `crawl.planner.plan_crawl`，DSN 只从 `FD_OPEN_DATA_DSN` 取。

用法::

    python3 scripts/agri_watermark_plan.py            # 只算不写库（planner 本就只读）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MCP = Path("/Users/chengsishi/finddata/fd-open-data-mcp")
OUT_DIR = Path("/Users/chengsishi/finddata/reports/agriculture-pilot")

#: 农业线关心的概念：通用财务面 + 分部/实物量 + 叙述式经营指标
CONCEPTS = [
    "financials.revenue", "financials.net_income", "financials.operating_cash_flow",
    "financials.total_assets", "financials.operating_cost", "financials.operating_profit",
    "financials.total_profit", "financials.equity", "financials.eps",
    "agri.segment.revenue", "agri.segment.revenue_share", "agri.segment.revenue_yoy",
    "agri.volume.sales",
    "agri.ops.free_cost", "agri.ops.avg_price", "agri.ops.sow_inventory",
    "agri.ops.feed_sales", "agri.ops.seed_area", "agri.ops.planted_area",
    "agri.ops.batch_release",
]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default="cn-report")
    ap.add_argument("--out", default=str(OUT_DIR / "watermark-plan.json"))
    args = ap.parse_args(argv)

    dsn = os.environ.get("FD_OPEN_DATA_DSN")
    if not dsn:
        raise SystemExit("FATAL 缺 FD_OPEN_DATA_DSN（source ~/.config/finddata/dashboards.env）")
    sys.path.insert(0, str(MCP))

    import psycopg2
    from fd_open_data_mcp.crawl.plan import DateRange, EntityScope
    from fd_open_data_mcp.crawl.planner import plan_crawl
    from fd_open_data_mcp.db import Database

    # 概念 id 与存量 max(date)（读数用 psycopg2，不走 ORM，避免方言差异）
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute("select code, id from concepts where code = any(%s)", (CONCEPTS,))
    ids = dict(cur.fetchall())
    # 只圈农业白名单 101 家：entity_scope 留空会把全市场股票都算进去（实测 plan_cells
    # 一度到 52,010），既跑不动也验不了「只含新报告期」。
    wl = json.loads((REPO / "docs" / "agri-report-whitelist.json").read_text(encoding="utf-8"))
    codes = [r["code"] for r in wl["a_share"]]
    cur.execute("select code, id from entities where entity_type='stock' and code = any(%s)", (codes,))
    ent_ids = [i for _c, i in cur.fetchall()]
    # 两个水位必须分开看：plan 的 since_last 是**按源**算的（source_filter 生效），
    # 拿「全源 max」去比会误判（实测 akshare 已到 2026-09-30、cn-report 才到 2024-12-31，
    # 计划从 2025-01-01 起是正确的；第一版用全源 max 比，红得没道理）。
    cur.execute("""
        select k.code, max(o.date) filter (where o.source_used = %s), max(o.date)
        from semantic_observations o join concepts k on k.id = o.concept_id
        where k.code = any(%s) group by 1
    """, (args.source, CONCEPTS))
    rows = cur.fetchall()
    have_max = {r[0]: r[2] for r in rows}                 # 全源（供对照）
    have_max_same = {r[0]: r[1] for r in rows if r[1]}    # 同源（断言用）

    db = Database(dsn)
    # **逐概念**成计划：planner 的 since_last 对一批概念取「各概念 max(date) 的最小值」，
    # 混批会把水位线 youngest 的概念也拖回最早的期（实测混批 start=2025-01-01，
    # 而 financials.revenue 存量已到 2026-09-30 → 已取过的期会被再计划一遍）。
    # 单概念计划才等价于「该概念只抓新报告期」。
    per_concept: dict[str, dict] = {}
    problems: list[str] = []
    for code, cid in sorted(ids.items()):
        session = db.get_session()
        try:
            plan = plan_crawl(
                session, [cid], EntityScope(entity_type="stock", entity_ids=ent_ids),
                DateRange(start=None, end=date.today().isoformat(), frequency=None),
                since_last=True, source_filter=[args.source],
            )
        finally:
            session.close()
        d = plan.model_dump(mode="json")
        start = (d.get("date_range") or {}).get("start")
        mx = have_max_same.get(code)
        unroutable = bool(d.get("unroutable"))
        per_concept[code] = {
            "wanted": not unroutable,
            "start": start,
            "end": (d.get("date_range") or {}).get("end"),
            "existing_max_date_same_source": mx,
            "existing_max_date_all_sources": have_max.get(code),
            "cells": d.get("plan_cells"),
            "unroutable_reason": (d.get("unroutable") or [{}])[0].get("reason") if unroutable else None,
        }
        if unroutable:
            continue      # 没有抓取绑定 = 本来就不是「爬」出来的（分部/叙述式），不算失败
        if not start:
            problems.append(f"{code} 未产出 start")
        elif mx and str(mx)[:10] >= str(start)[:10]:
            problems.append(f"{code} 同源存量 max(date)={mx} ≥ plan start={start}")
    data = {"per_concept": per_concept}
    start = next((v["start"] for v in per_concept.values() if v["wanted"]), None)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generated_for": "awaken-agriculture-report-ingest 6.3 水位线接管 dry-run",
        "source_filter": args.source,
        "concept_count": len(ids),
        "date_range": data.get("date_range"),
        "wanted_concepts": data.get("wanted_concepts"),
        "unroutable": data.get("unroutable"),
        "unmapped": data.get("unmapped"),
        "existing_max_date_same_source": have_max_same,
        "existing_max_date_all_sources": have_max,
        "assertion": {"rule": "plan.start 必须晚于每个概念的库存 max(date)",
                      "problems": problems},
        "per_concept": per_concept,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    wanted = [c for c, v in per_concept.items() if v["wanted"]]
    unrout = [c for c, v in per_concept.items() if not v["wanted"]]
    cells = sum(v["cells"] or 0 for v in per_concept.values())
    print(f"概念 {len(ids)} 个 · 可增量抓取 {len(wanted)} 个 · 不可路由 {len(unrout)} 个 · "
          f"计划格子合计 {cells}")
    for c in wanted:
        v = per_concept[c]
        print(f"  ✓ {c:<30} start={v['start']} 同源存量={v['existing_max_date_same_source']} "
              f"cells={v['cells']}")
    for c in unrout:
        print(f"  — {c:<30} {per_concept[c]['unroutable_reason']}")
    print("同源 %s 存量 max(date) 样例:" % args.source, dict(list(have_max_same.items())[:5]))
    print("全源对照（不同车道，不相加）:", dict(list(have_max.items())[:5]))
    if problems:
        print("断言失败：")
        for p in problems[:10]:
            print("  ✗", p)
        print("写出", out)
        return 1
    print("断言通过：plan 只含新报告期，既有期不产生抓取")
    print("写出", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
