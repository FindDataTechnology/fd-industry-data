#!/usr/bin/env python3
"""extract_weather_daily — 三个「日频 × 分组列 × 若干指标列」的外部气象/能源源进读面。

覆盖（2026-11-14 建；三支都是语义覆盖盘点 R1 档的待办）：

| 源 | 分组列 | 指标（单位） | 实体 |
|---|---|---|---|
| `elexon-bmrs` | 无（单一序列） | `temperature`（degC，英国电力市场有效温度） | country/GB |
| `noaa-ncei` | `station` | `tmax`/`tmin`（degC）、`prcp`（mm） | site/<GHCN 站号> |
| `open-meteo` | `city` | `temperature_2m_mean`（degC）、`precipitation_sum`（mm） | city/<城市码> |

**为什么三支一个脚本**：它们的形状完全同构（一个期字段 + 一个可选分组列 + N 个指标列），
差异只有「哪个字段是期、哪几列是值、挂在什么实体上」。写成三份等于把同一段逻辑抄三遍，
而它们将来还会各加参数/城市/站点。配置表在下面 `SOURCES`，加一个源 = 加一段配置。

三件要注意的：

1. **`elexon-bmrs` 的期字段叫 `period` 不叫 `date`**（payload 里就是 `period`）。按 `date`
   去取会得到 0 期、然后被判成"没有数据"——这种静默比报错更难查，故期字段进配置表。
2. **`open-meteo` 的城市码要与册内既有实体对齐，不要新建**：册内 `city` 型已有
   `BJS`/`SHA`/`SZX`（北京/上海/深圳），而广州那条是 `CN-CITY-广州`（历史遗留码）。
   若给广州新建一个 `GZ`，同一座城市会分裂成两个实体 —— 判据里那类"同城两码"的错
   **任何下游都不会红**。表里显式写死"用哪个已存在的码"。
3. **值是文本列，不是 numeric**（全表 106 万行都是 `character varying`）：写进 SQL 的字面量
   一律走绑定参数，判值域要 `::numeric`（`'10000' < '9980'` 是字典序）。

契约与同族一致（见 `_semextract.py`）：additive、不插值、同 (series, period) 取
`scraped_at` 最新、趋势闸写前硬断言、值域护栏越界即 FATAL、只读最近一次成功运行。

Usage:
    python3 extract_weather_daily.py --dsn "$FD_OPEN_DATA_DSN" --dry-run
    python3 extract_weather_daily.py --dsn "$FD_OPEN_DATA_DSN" [--only open-meteo]
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _semextract import (ensure_concepts, fmt, gap_note, latest_run_items,  # noqa: E402
                         num, parse_dsn, write_rows)

MIN_PERIODS = 24
MAX_GAP_DAYS = 5                 # 日频（周末可能缺测）

# 城市名（payload 的 `city`）-> 册内**已存在**的 city 实体码。
# 广州那条是历史遗留的 `CN-CITY-广州`；这里刻意沿用它而不是新建 `GZ`，
# 否则同一座城市在实体表里出现两个码，而没有任何判据会把它们算成同一处。
CITY_CODES = {
    "beijing": "BJS",
    "shanghai": "SHA",
    "shenzhen": "SZX",
    "guangzhou": "CN-CITY-广州",
}

# 源 -> 抓取/映射配置。metrics 的值: (concept code, 中文名, 英文名, 单位, 值域下限, 值域上限)
SOURCES = {
    "elexon-bmrs": {
        "period": "period",
        "entity": None,                      # 单一序列，挂国家
        "entity_type": "country",
        "fixed_entity_code": "GB",
        "metrics": {
            "temperature": ("meteo.gb_effective_temp.daily",
                            "英国电力市场有效温度（Elexon TEMP 数据集）",
                            "GB effective temperature for electricity demand (Elexon TEMP)",
                            "degC", -30.0, 50.0),
        },
    },
    "noaa-ncei": {
        "period": "date",
        "entity": "station",
        "entity_type": "site",
        "metrics": {
            "tmax": ("meteo.station.tmax.daily", "日最高气温（GHCN 站点）",
                     "Daily maximum temperature (GHCN station)", "degC", -60.0, 60.0),
            "tmin": ("meteo.station.tmin.daily", "日最低气温（GHCN 站点）",
                     "Daily minimum temperature (GHCN station)", "degC", -70.0, 50.0),
            "prcp": ("meteo.station.prcp.daily", "日降水量（GHCN 站点）",
                     "Daily precipitation (GHCN station)", "mm", 0.0, 2000.0),
        },
    },
    "open-meteo": {
        "period": "date",
        "entity": "city",
        "entity_type": "city",
        "metrics": {
            "temperature_2m_mean": ("meteo.city.t2m_mean.daily", "日平均气温（城市，再分析）",
                                    "Daily mean 2 m temperature (city, reanalysis)",
                                    "degC", -60.0, 60.0),
            "precipitation_sum": ("meteo.city.precip_sum.daily", "日降水量合计（城市，再分析）",
                                  "Daily precipitation sum (city, reanalysis)", "mm", 0.0, 2000.0),
        },
    },
}


def entity_code(spec: dict, payload: dict) -> str | None:
    """该行挂到哪个实体码上（None = 这一行算不出实体，丢掉）。"""
    if not spec["entity"]:
        return spec["fixed_entity_code"]
    raw = str(payload.get(spec["entity"]) or "").strip()
    if not raw:
        return None
    if spec["entity_type"] == "city":
        return CITY_CODES.get(raw.lower())
    return raw                                   # station：站号本身就是码


def collect(spec: dict, items: list[dict]) -> dict[str, dict[str, dict[str, dict]]]:
    """-> {concept: {entity_code: {period: payload}}}

    同一 (concept, entity, period) 多次出现取 `scraped_at` 最新（跨轮抓取也会落在同一张表里）。
    """
    data: dict[str, dict[str, dict[str, dict]]] = {c: {} for c, *_ in spec["metrics"].values()}
    for p in items:
        ent = entity_code(spec, p)
        if not ent:
            continue
        per = str(p.get(spec["period"]) or "").strip()
        if not per:
            continue
        for field, (code, *_rest) in spec["metrics"].items():
            if num(p.get(field)) is None:
                continue
            slot = data[code].setdefault(ent, {})
            prev = slot.get(per)
            if prev is None or str(p.get("scraped_at", "")) > str(prev.get("scraped_at", "")):
                slot[per] = p
    return data


def build_rows(data: dict, spec: dict, notes: list[str], skips: dict):
    """-> [(concept, entity_code, period, value)]；闸不足的 (concept@entity) 记进 skips。"""
    rows: list[tuple] = []
    for field, (code, zh, _en, unit, lo_b, hi_b) in spec["metrics"].items():
        if not data[code]:
            skips[code] = "本轮 payload 里没有这个指标（不是闸拦的）"
            continue
        for ent, periods in sorted(data[code].items()):
            days = sorted(periods)
            if len(days) < MIN_PERIODS:
                skips[f"{code}@{ent}"] = f"仅 {len(days)} 期（<{MIN_PERIODS}）—— 让闸按住"
                continue
            gap = gap_note(days, MAX_GAP_DAYS)
            if gap:
                skips[f"{code}@{ent}"] = f"序列不连续：{gap}"
                continue
            vals = [num(periods[d].get(field)) for d in days]
            lo, hi = min(vals), max(vals)
            if not (lo_b <= lo and hi <= hi_b):
                raise SystemExit(f"FATAL: {code}@{ent} 值域 {lo}..{hi} 越出 [{lo_b},{hi_b}] {unit}"
                                 f" —— 指标串列或单位变了")
            for d in days:
                rows.append((code, ent, d, fmt(num(periods[d].get(field)))))
            notes.append(f"{code}@{ent}: {len(days)} 期（{days[0]}..{days[-1]}），"
                         f"值 {lo}..{hi} {unit}")
    return rows


def ensure_entities(cur, spec: dict, codes: set[str]) -> dict[str, int]:
    et = spec["entity_type"]
    if et == "site":
        for c in sorted(codes):
            cur.execute(
                "INSERT INTO entities (entity_type, code, name_en, name_zh)"
                " VALUES ('site',%s,%s,%s) ON CONFLICT ON CONSTRAINT uq_entity_type_code"
                " DO NOTHING", (c, c, c))
    ids: dict[str, int] = {}
    if not codes:
        return ids
    cur.execute(f"SELECT id, code FROM entities WHERE entity_type = %s AND code = ANY(%s)",
                (et, sorted(codes)))
    for eid, code in cur.fetchall():
        ids[code] = eid
    missing = sorted(codes - set(ids))
    if missing:
        raise SystemExit(f"FATAL: {et} 实体没解析到：{missing}"
                         f"（city 型要在 CITY_CODES 里指到册内已存在的码）")
    return ids


def run_source(conn, cur, source: str, spec: dict, dry_run: bool) -> int:
    notes: list[str] = []
    skips: dict[str, str] = {}
    rid, items = latest_run_items(cur, source)
    if rid is None:
        print(f"[{source}] 没有任何成功运行，跳过")
        return 0
    data = collect(spec, items)
    rows = build_rows(data, spec, notes, skips)

    # **每个指标各建各的概念**：`ensure_concepts` 一次只收一个 unit，而这三个源的指标
    # 单位不同（degC / mm）。用「先统一建再 UPDATE」补单位是脏的 —— 那一瞬间库里存在
    # unit 错的概念，而任何并发读者都会看到它。
    ids: dict[str, int] = {}
    for field, (code, zh, en, unit, *_b) in spec["metrics"].items():
        ids.update(ensure_concepts(cur, {field: (code, zh, en)}, source=source,
                                   category="气象", unit=unit, frequency="daily",
                                   entity_type=spec["entity_type"]))
    eids = ensure_entities(cur, spec, {e for _c, e, _d, _v in rows})
    if dry_run:
        conn.rollback()

    print(f"[{source}] run_id={rid} payload={len(items)} 待写 {len(rows)} 行")
    for n in notes:
        print("   -", n)
    if skips:
        print("   skipped:")
        for k, v in skips.items():
            print("    -", k, ":", v)
    if dry_run:
        print("   dry-run: no database writes")
        return 0
    if not rows:
        print("   本轮无可写行（闸按住），不提交事务")
        return 0

    unit_of = {code: unit for (code, _z, _e, unit, *_b) in spec["metrics"].values()}
    for unit in sorted(set(unit_of.values())):
        # **按单位分组，但行一条都不能少**：早先版本用 `{c: (...) for ...}` 以概念码做键，
        # 同一概念的多行（多实体 × 多日期）会被压成一行 —— 那是个静默丢数据的写法。
        sub = [(c, eids[e], d, v) for (c, e, d, v) in rows if unit_of[c] == unit]
        if not sub:
            continue
        before, after = write_rows(conn, sub, ids=ids, source=source, unit=unit,
                                   entity_type=spec["entity_type"])
        print(f"   {unit}：{len(sub)} 行 {before} -> {after}（inserted {after - before}）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", action="append", default=[], metavar="SOURCE")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("DSN missing — 传 --dsn 或 export FD_OPEN_DATA_DSN")
    unknown = sorted(set(args.only) - set(SOURCES))
    if unknown:
        raise SystemExit(f"--only 里有未知源：{unknown}（可选：{sorted(SOURCES)}）")

    conn = psycopg2.connect(parse_dsn(args.dsn))
    try:
        for source, spec in SOURCES.items():
            if args.only and source not in args.only:
                continue
            with conn.cursor() as cur:
                run_source(conn, cur, source, spec, args.dry_run)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
