#!/usr/bin/env python3
"""nasa-power — NASA POWER 逐点气温/降水（乌鲁木齐 1984 整年）进 semantic_observations。

**这条也是「固定历史数据集」，不是「窗口冻死」**（语义覆盖盘点的 R0b 档）：manifest
写明取固定点（乌鲁木齐 43.8N/87.6E）1984 年整年 T2M/PRECTOTCORR 日值，MERRA-2
finalized、不存在更新的行。366 期全停在 1984 年是**设计**，照常映射。

契约与同族一致（见 `_semextract.py`）：additive、不插值、同 (series, period) 取
`scraped_at` 最新、趋势闸写前硬断言、值域护栏越界即 FATAL。

三条设计：
1. **实体是「点位」**：按 `lat/lon` 建 `site` 型实体、code 用 `POWER-43.8N-87.6E`
   （取一位小数，与 payload 的精度一致）。不用"乌鲁木齐"这种地名做键 —— 地名是人工
   标签，而 lat/lon 才是这条数据的真实坐标。
2. **两个参数各一个概念**（`meteo.t2m.daily` degC / `meteo.precip.daily` mm）。
   它们量纲完全不同，合进一个概念就是把摄氏度当毫米。
3. **值域按参数各自给**：气温 -90..60 °C、降水 0..2000 mm/日。填值哨兵 `-999`
   在 spider 侧已置 null（`num()` 返回 None ⇒ 自然跳过），这里只兜底越界。

Usage:
    python3 extract_nasa_power.py --dsn "$FD_OPEN_DATA_DSN" --dry-run
    python3 extract_nasa_power.py --dsn "$FD_OPEN_DATA_DSN"
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _semextract import (ensure_concepts, fmt, gap_note, latest_run_items,  # noqa: E402
                         num, parse_dsn, write_rows)

SOURCE = "nasa-power"
MIN_PERIODS = 24
MAX_GAP_DAYS = 5                 # 日频
SITE_CODE = "POWER-43.8N-87.6E"

# payload 的 `parameter` -> (concept code, 中文名, 英文名, 单位, 值域下限, 值域上限)
SERIES = {
    "T2M": ("meteo.t2m.daily", "日平均气温（NASA POWER，乌鲁木齐点位）",
            "Daily mean air temperature at 2 m (NASA POWER point)", "degC", -90.0, 60.0),
    "PRECTOTCORR": ("meteo.precip.daily", "日降水量（NASA POWER，乌鲁木齐点位）",
                    "Daily precipitation corrected (NASA POWER point)", "mm", 0.0, 2000.0),
}

NOT_MAPPED = {
    "lat/lon/url": "坐标与抓取 URL 是元数据，不是观测值",
}


def collect(items: list[dict]) -> dict[str, dict[str, dict]]:
    """-> {parameter: {date: payload}}（同一 (parameter, date) 取 scraped_at 最新）"""
    data: dict[str, dict[str, dict]] = {p: {} for p in SERIES}
    for p in items:
        param = (p.get("parameter") or "").strip()
        if param not in SERIES:
            continue
        day = str(p.get("date") or "").strip()
        if not day or num(p.get("value")) is None:
            continue
        slot = data[param]
        prev = slot.get(day)
        if prev is None or str(p.get("scraped_at", "")) > str(prev.get("scraped_at", "")):
            slot[day] = p
    return data


def build_rows(data: dict, notes: list[str], skips: dict):
    """-> [(code, site_code, date, value)]"""
    rows: list[tuple] = []
    for param, (code, zh, _en, unit, lo_b, hi_b) in SERIES.items():
        days = sorted(data[param])
        if not days:
            skips[code] = "本轮 payload 里没有这个参数（不是闸拦的）"
            continue
        if len(days) < MIN_PERIODS:
            skips[code] = f"仅 {len(days)} 期（<{MIN_PERIODS}）—— 让闸按住"
            continue
        gap = gap_note(days, MAX_GAP_DAYS)
        if gap:
            skips[code] = f"序列不连续：{gap}"
            continue
        vals = [num(data[param][d].get("value")) for d in days]
        lo, hi = min(vals), max(vals)
        if not (lo_b <= lo and hi <= hi_b):
            raise SystemExit(f"FATAL: {code} 值域 {lo}..{hi} 越出 [{lo_b},{hi_b}] {unit}"
                             f" —— 参数串列或单位变了")
        for d in days:
            rows.append((code, SITE_CODE, d, fmt(num(data[param][d].get("value")))))
        notes.append(f"{param} {zh}: {len(days)} 期（{days[0]}..{days[-1]}），"
                     f"值 {lo}..{hi} {unit} → {code}")
    return rows


def ensure_site(cur, label: str) -> int:
    cur.execute(
        "INSERT INTO entities (entity_type, code, name_en, name_zh) VALUES ('site',%s,%s,%s)"
        " ON CONFLICT ON CONSTRAINT uq_entity_type_code DO NOTHING",
        (SITE_CODE, label, label))
    cur.execute("SELECT id FROM entities WHERE entity_type='site' AND code=%s", (SITE_CODE,))
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"FATAL: 点位实体没建出来: {SITE_CODE}")
    return row[0]


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
            data = collect(items)
            rows = build_rows(data, notes, skips)

            # 两个参数单位不同 ⇒ 各建各的概念（同 code 配两个 unit 会让 code→id 随机取行）
            per_unit: dict[str, dict] = {}
            for param, (code, zh, en, unit, *_b) in SERIES.items():
                per_unit.setdefault(unit, {})[param] = (code, zh, en)
            id_maps: dict[str, dict[str, int]] = {}
            for unit, series in per_unit.items():
                id_maps[unit] = ensure_concepts(cur, series, source=SOURCE, category="气象",
                                                unit=unit, frequency="daily",
                                                entity_type="site")
            eid = ensure_site(cur, "乌鲁木齐 43.8N/87.6E（NASA POWER 点位）")
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

            unit_of = {SERIES[p][0]: SERIES[p][3] for p in SERIES}
            for unit, id_map in id_maps.items():
                sub = [(c, eid, d, v) for (c, _s, d, v) in rows if unit_of[c] == unit]
                if not sub:
                    continue
                before, after = write_rows(conn, sub, ids=id_map, source=SOURCE,
                                           unit=unit, entity_type="site")
                n_codes = len({c for c, _e, _d, _v in sub})
                print(f"  {unit}：{n_codes} 个概念 {before} -> {after}"
                      f"（inserted {after - before}）")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
