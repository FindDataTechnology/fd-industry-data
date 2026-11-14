#!/usr/bin/env python3
"""usgs-nwis — USGS 日流量（站点 01646500，2020 整年）进 semantic_observations。

**这条是「固定历史数据集」，不是「窗口冻死」**（2026-11-14 语义覆盖盘点的 R0b 档）：
manifest 写明取站点 01646500 的 2020 年整年日值，固定历史数据集、不存在更新的行
（MERRA-2/NWIS 的 finalized 归档）。所以 366 期全停在 2020 年是**设计**，照常映射——
`check_semantic_coverage.py` 里它的 manifest 原话已登记进 `HISTORICAL` 表。

契约与同族一致（见 `_semextract.py` 的模块 docstring）：additive、不插值、
同 (series, period) 取 `scraped_at` 最新、趋势闸写前硬断言、值域护栏越界即 FATAL。

三条设计：
1. **实体按 USGS 站点号建 `site` 型**（`01646500`），不用站名做键——站名是自由文本、
   会改；站点号才是稳定标识（同 uk-hpi 用 ONS 区码而不是区名的理由）。
2. **只写 `parameter_cd=00060` 的日流量**（ft3/s）。payload 里还有 `qualifiers`
   （合格标记，如 `A`/`P`/`e`）——那是**数据质量**不是量值，进 `NOT_MAPPED`。
3. 值域 0..1e7 ft3/s：波托马克河实测 2,260–81,800，取两个数量级的余量；越界说明
   单位换了（ft3/s vs m3/s，差 35 倍）而没有任何下游判据会发现。

Usage:
    python3 extract_usgs_nwis.py --dsn "$FD_OPEN_DATA_DSN" --dry-run
    python3 extract_usgs_nwis.py --dsn "$FD_OPEN_DATA_DSN"
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import psycopg2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _semextract import (ensure_concepts, fmt, gap_note, latest_run_items,  # noqa: E402
                         num, parse_dsn, write_rows)

SOURCE = "usgs-nwis"
PARAMETER_CD = "00060"           # 00060 = discharge, cubic feet per second
MIN_PERIODS = 24
MAX_GAP_DAYS = 5                 # 日频（周末也可能缺测，给 5 天）
BOUNDS = (0.0, 1e7)              # ft3/s；越界说明单位换了（m3/s 与 ft3/s 差 35 倍）

# site -> (concept code, 中文名, 英文名)
SERIES = {
    "01646500": ("hydro.usgs.discharge.01646500",
                 "USGS 波托马克河 日平均流量（站点 01646500）",
                 "USGS Potomac River daily mean discharge, site 01646500"),
}

NOT_MAPPED = {
    "qualifiers": "测值合格标记（A=Approved / P=Provisional / e=Estimated）；数据质量不是量值",
}


def collect(items: list[dict]) -> tuple[dict, dict]:
    """-> ({site: {date: payload}}, {site: 站名})"""
    data: dict[str, dict[str, dict]] = {s: {} for s in SERIES}
    names: dict[str, str] = {}
    for p in items:
        site = str(p.get("site") or "").strip()
        if site not in SERIES:
            continue
        if str(p.get("parameter_cd") or PARAMETER_CD).strip() != PARAMETER_CD:
            continue
        day = str(p.get("date") or "").strip()
        if not day or num(p.get("value")) is None:
            continue
        if p.get("site_name"):
            names[site] = str(p["site_name"]).strip()
        slot = data[site]
        prev = slot.get(day)
        if prev is None or str(p.get("scraped_at", "")) > str(prev.get("scraped_at", "")):
            slot[day] = p
    return data, names


def build_rows(data: dict, notes: list[str], skips: dict):
    """-> [(code, site, date, value)]：**site 跟着每行走**，不是全局一个实体。"""
    rows: list[tuple] = []
    lo_b, hi_b = BOUNDS
    for site, (code, zh, _en) in SERIES.items():
        days = sorted(data[site])
        if not days:
            skips[code] = "本轮 payload 里没有这个站点（不是闸拦的）"
            continue
        if len(days) < MIN_PERIODS:
            skips[code] = f"仅 {len(days)} 期（<{MIN_PERIODS}）—— 让闸按住"
            continue
        gap = gap_note(days, MAX_GAP_DAYS)
        if gap:
            skips[code] = f"序列不连续：{gap}"
            continue
        vals = [num(data[site][d].get("value")) for d in days]
        lo, hi = min(vals), max(vals)
        if not (lo_b <= lo and hi <= hi_b):
            raise SystemExit(f"FATAL: {code} 值域 {lo}..{hi} 越出 [{lo_b},{hi_b}]"
                             f" —— 单位可能换了（ft3/s vs m3/s）")
        for d in days:
            rows.append((code, site, d, fmt(num(data[site][d].get("value")))))
        notes.append(f"{site} {zh}: {len(days)} 期（{days[0]}..{days[-1]}），"
                     f"值 {lo}..{hi} → {code}")
    return rows


def ensure_sites(cur, names: dict[str, str]) -> dict[str, int]:
    """按 USGS 站点号建 `site` 型实体（幂等：uq_entity_type_code）。"""
    for site, name in sorted(names.items()):
        cur.execute(
            "INSERT INTO entities (entity_type, code, name_en, name_zh) VALUES ('site',%s,%s,%s)"
            " ON CONFLICT ON CONSTRAINT uq_entity_type_code DO NOTHING",
            (site, name, name))
    ids: dict[str, int] = {}
    cur.execute("SELECT id, code FROM entities WHERE entity_type = 'site' AND code = ANY(%s)",
                (list(names),))
    for eid, code in cur.fetchall():
        ids[code] = eid
    missing = sorted(set(names) - set(ids))
    if missing:
        raise SystemExit(f"FATAL: 站点实体没建出来: {missing}")
    return ids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("DSN missing — 传 --dsn 或 export FD_OPEN_DATA_DSN")

    conn = psycopg2.connect(parse_dsn(args.dsn))
    try:
        with conn.cursor() as cur:
            notes: list[str] = []
            skips: dict[str, str] = {}
            rid, items = latest_run_items(cur, SOURCE)
            if rid is None:
                print(f"{SOURCE}: 没有任何成功运行，无从提取")
                return 0
            data, names = collect(items)
            rows = build_rows(data, notes, skips)
            ids = ensure_concepts(cur, SERIES, source=SOURCE, category="水文",
                                  unit="ft3/s", frequency="daily", entity_type="site")
            eids = ensure_sites(cur, names)
            if args.dry_run:
                conn.rollback()

            print(f"取最近一次成功运行 run_id={rid}（{len(items)} 条 payload）")
            print(f"待写 {len(rows)} 行")
            for n in notes:
                print("  -", n)
            print("not mapped（设计如此）:")
            for k, v in NOT_MAPPED.items():
                print(f"  - {k}: {v}")
            if skips:
                print("skipped:")
                for k, v in skips.items():
                    print(f"  - {k}: {v}")
            if args.dry_run:
                print("\ndry-run: no database writes")
                return 0
            if not rows:
                print("\n本轮无可写行（闸按住），不提交事务")
                return 0

            payload = [(c, eids[site], d, v) for (c, site, d, v) in rows]
            before, after = write_rows(conn, payload, ids=ids, source=SOURCE,
                                       unit="ft3/s", entity_type="site")
            print(f"\nsemantic_observations: {before} -> {after} (inserted {after - before})")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
