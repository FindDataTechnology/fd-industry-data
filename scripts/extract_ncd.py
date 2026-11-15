#!/usr/bin/env python3
"""chinamoney-ncd-curve — 同业存单(AAA)收益率曲线进 semantic_observations（2026-11-14 建）。

与 `extract_rates.py`（Shibor）、`extract_eastmoney`、`extract_uk_hpi` 同一套回填契约：
additive（`ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING`）、不插值不补零、同
(series, period) 取 `scraped_at` 最新、趋势闸写前硬断言、值域护栏越界即 FATAL、
写 `obs_month` 治理桶。

三件设计选择：

1. **期限维度用「一档一个概念」而不是实体**。这条曲线的 `tenor_days` 是 17 个固定档
   （7…365），不是连续量 —— 17 个档就是 17 条独立的时间序列，与 Shibor 的八档同构。
   若将来要「整条曲线」的形状查询，那是产品侧的聚合，不必在库里先造一个 tenor 实体维度
   （册内 `entities` 里连 `market` 这个类型都还没有）。
2. **17 档全映射，不挑标准档**。上游给的是固定网格，命名也统一成
   `rate.ncd_aaa.<label>`（30/91/182/274/365 天用 `1m/3m/6m/9m/1y`，其余用 `<N>d`）。
   只挑「市场惯例档」省下的不是存储而是信息，而这条曲线的非标准档（46d/61d/76d…）
   恰好是拆借与存单之间的真实定价点。
3. **只写 NCD_AAA 这一条曲线**。同一个 payload 里若有别的 `curve`（评级档），进
   `NOT_MAPPED` 并逐次打印 —— 不同评级的曲线不能塞进同一批概念码。

**注意 2026-11-14 的一个坑**：SQL 里不许写裸 `'%'` 字面量，psycopg2 会把它当占位符
（参数元组少一位 ⇒ `IndexError`），单位一律走绑定参数；单测里 psycopg2 是替身模块，
这条永远测不出来，故 `tests/test_extract_ncd.py` 里有一条专门扫字面量的自证。

Usage:
    python3 extract_ncd.py --dsn "$FD_OPEN_DATA_DSN" --dry-run
    python3 extract_ncd.py --dsn "$FD_OPEN_DATA_DSN"
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import psycopg2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _semextract import (ensure_concepts, fmt, latest_run_items,  # noqa: E402
                         num, obs_month_of, parse_dsn, write_rows)

SOURCE = "chinamoney-ncd-curve"
CURVE = "NCD_AAA"                # 唯一写入的曲线；别的评级档进 NOT_MAPPED
MIN_PERIODS = 24                 # 下游趋势闸：≥24 连续期
ENTITY_CN = 1                    # entities: country / CN / 中国
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# tenor_days -> (concept code, 中文名, 英文名)
SERIES = {
    7: ("rate.ncd_aaa.7d", "同业存单(AAA) 7天", "NCD AAA yield, 7 days"),
    14: ("rate.ncd_aaa.14d", "同业存单(AAA) 14天", "NCD AAA yield, 14 days"),
    21: ("rate.ncd_aaa.21d", "同业存单(AAA) 21天", "NCD AAA yield, 21 days"),
    30: ("rate.ncd_aaa.1m", "同业存单(AAA) 1个月", "NCD AAA yield, 1 month"),
    46: ("rate.ncd_aaa.46d", "同业存单(AAA) 46天", "NCD AAA yield, 46 days"),
    61: ("rate.ncd_aaa.61d", "同业存单(AAA) 61天", "NCD AAA yield, 61 days"),
    76: ("rate.ncd_aaa.76d", "同业存单(AAA) 76天", "NCD AAA yield, 76 days"),
    91: ("rate.ncd_aaa.3m", "同业存单(AAA) 3个月", "NCD AAA yield, 3 months"),
    122: ("rate.ncd_aaa.122d", "同业存单(AAA) 122天", "NCD AAA yield, 122 days"),
    152: ("rate.ncd_aaa.152d", "同业存单(AAA) 152天", "NCD AAA yield, 152 days"),
    182: ("rate.ncd_aaa.6m", "同业存单(AAA) 6个月", "NCD AAA yield, 6 months"),
    213: ("rate.ncd_aaa.213d", "同业存单(AAA) 213天", "NCD AAA yield, 213 days"),
    243: ("rate.ncd_aaa.243d", "同业存单(AAA) 243天", "NCD AAA yield, 243 days"),
    274: ("rate.ncd_aaa.9m", "同业存单(AAA) 9个月", "NCD AAA yield, 9 months"),
    304: ("rate.ncd_aaa.304d", "同业存单(AAA) 304天", "NCD AAA yield, 304 days"),
    335: ("rate.ncd_aaa.335d", "同业存单(AAA) 335天", "NCD AAA yield, 335 days"),
    365: ("rate.ncd_aaa.1y", "同业存单(AAA) 1年", "NCD AAA yield, 1 year"),
}

BOUNDS = (0.0, 20.0)             # 存单收益率的合理值域（%），越界说明列语义变了（% vs bp）
VALUE_FIELD = "yield_pct"






def gap_note(dates: list[str]) -> str | None:
    """日频数据只要求「相邻两期不超过 5 天」（周末 + 一个调休）。"""
    from datetime import date
    worst, where = 0, None
    for a, b in zip(dates, dates[1:]):
        try:
            span = (date.fromisoformat(b) - date.fromisoformat(a)).days
        except ValueError:
            return f"期值不是 YYYY-MM-DD：{a!r}/{b!r}"
        if span > worst:
            worst, where = span, (a, b)
    if worst > 5:
        return f"{where[0]} 与 {where[1]} 相隔 {worst} 天（>5）"
    return None



def collect(items: list[dict]) -> tuple[dict, set[str]]:
    """-> ({concept: {date: payload}}, 见到的其它曲线名集合)"""
    data: dict[str, dict[str, dict]] = {c: {} for c, _, _ in SERIES.values()}
    others: set[str] = set()
    for p in items:
        curve = (p.get("curve") or "").strip()
        if curve != CURVE:
            if curve:
                others.add(curve)
            continue
        try:
            tenor = int(p.get("tenor_days"))
        except (TypeError, ValueError):
            continue
        spec = SERIES.get(tenor)
        if spec is None:
            continue
        day = str(p.get("date") or "").strip()
        if not _DAY.match(day) or num(p.get(VALUE_FIELD)) is None:
            continue
        slot = data[spec[0]]
        prev = slot.get(day)
        if prev is None or str(p.get("scraped_at", "")) > str(prev.get("scraped_at", "")):
            slot[day] = p
    return data, others


def build_rows(data: dict, notes: list[str], skips: dict):
    rows: list[tuple] = []
    lo_b, hi_b = BOUNDS
    for tenor, (code, zh, _en) in SERIES.items():
        days = sorted(data[code])
        if len(days) < MIN_PERIODS:
            skips[code] = f"仅 {len(days)} 个交易日（<{MIN_PERIODS}）—— 让闸按住，攒够自动开始写"
            continue
        gap = gap_note(days)
        if gap:
            skips[code] = f"序列不连续：{gap}"
            continue
        vals = [num(data[code][d].get(VALUE_FIELD)) for d in days]
        lo, hi = min(vals), max(vals)
        if not (lo_b <= lo and hi <= hi_b):
            raise SystemExit(f"FATAL: {code}（{tenor}天）值域 {lo}..{hi} 越出 [{lo_b},{hi_b}]"
                             f" —— 列语义可能变了（% vs bp）")
        for d in days:
            rows.append((code, d, fmt(num(data[code][d].get(VALUE_FIELD)))))
        notes.append(f"{tenor}天 {zh}: {len(days)} 个交易日连续（{days[0]}..{days[-1]}），"
                     f"值 {lo}..{hi} → {code}")
    return rows



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("DSN missing — 传 --dsn 或 export FD_OPEN_DATA_DSN")
    dsn = parse_dsn(args.dsn)

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            notes: list[str] = []
            skips: dict[str, str] = {}
            rid, items = latest_run_items(cur, SOURCE)
            if rid is None:
                print(f"{SOURCE}: 没有任何成功运行，无从提取")
                return 0
            data, others = collect(items)
            rows = build_rows(data, notes, skips)
            ids = ensure_concepts(cur, SERIES, source=SOURCE, category="利率",
                                  unit="%", frequency="daily", entity_type="country")
            if args.dry_run:
                conn.rollback()

            print(f"取最近一次成功运行 run_id={rid}（{len(items)} 条 payload）")
            print(f"待写 {len(rows)} 行")
            for n in notes:
                print("  -", n)
            if others:
                print("not mapped（别的评级曲线，另立概念再写）:")
                for o in sorted(others):
                    print(f"  - {o}")
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

            payload = [(c, ENTITY_CN, d, v) for (c, d, v) in rows]
            before, after = write_rows(conn, payload, ids=ids, source=SOURCE,
                                       unit="%", entity_type="country")
            print(f"\nsemantic_observations: {before} -> {after} (inserted {after - before})")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
