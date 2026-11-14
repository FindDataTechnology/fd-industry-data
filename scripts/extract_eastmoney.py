#!/usr/bin/env python3
"""eastmoney-data — CPI/PPI 物价序列进 semantic_observations（2026-11-14 建）。

与 `extract_batch1.py` / `extract_batch2.py` 同一套回填契约：

* **additive only** —— `INSERT ... ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING`，
  自然键 `(concept_id, entity_type, entity_id, date, granularity, source_used)`；
  第二次运行插入 0 行。
* **不插值、不补零** —— 写入的每个值都是 `crawl_items` 里真实存在的 payload 值。
* **同一 (series, period) 抓到多次取 `scraped_at` 最新那一行**（本源 CronJob 每天一轮，
  实测同一期最多有 3 份 payload 副本）。

三件本脚本专门要解决的事：

1. **趋势闸（≥24 连续期）在写之前硬断言**。每个序列先算期数与连续性，不足 24 期、
   或中间断月，即整条跳过并把理由记进 `SKIP_NOTES`。这正是 `eastmoney-data` 长期
   进不了语义层的门——spider 的 `pageSize` 曾是 20，差一本（`72d974e` 改 30 期）。

2. **单位陷阱**。东财表里「当月」列（`NATIONAL_BASE` / `BASE`）是**上年同月=100 的
   指数**（实测 2026-10 = 100.7），「同比」列（`NATIONAL_SAME` / `BASE_SAME`）才是
   **百分比**（0.7）。两者绝不能塞进同一个 `%` 概念。本脚本只写指数两族
   （`price.cpi.index.monthly` / `price.ppi.index.monthly`，unit=`指数`），并有值域
   护栏：指数族必须落在 [50,200]，越界即 FATAL——它抓的是「上游改了列语义而我们
   照旧映射」这一类静默错。

3. **同比两族故意不写**（见 `NOT_MAPPED`）。册内 `CPI_YOY` / `PPI_YOY` 已被 akshare
   覆盖（519 / 405 行，1986-02 / 1995-08 起），且与本源的 30 期**逐月数值完全相同**。
   `date` 是文本：akshare 写 `2026-10-01`、本源写 `2026-10`，`uq_sem_obs` 视作两个
   不同的键 ⇒ 重复写不会去重，而是在同一个月上多出一个点（实测 `read_cache_range`
   在 2024-01..2026-12 返回 64 点而不是 30 点）。这一条是 2026-11-14 当天实历：
   先按「同比复用册内概念」写了 60 行，读面立刻每月两条，只好按 id 精确删回。

Usage:
    python3 extract_eastmoney.py --dsn "$FD_OPEN_DATA_DSN" --dry-run
    python3 extract_eastmoney.py --dsn "$FD_OPEN_DATA_DSN"
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import psycopg2

SOURCE = "eastmoney-data"
MIN_PERIODS = 24          # 下游趋势闸：≥24 连续期
ENTITY_CN = 1             # entities: country / CN / 中国

# ── obs_month 治理桶（fd_open_data_mcp.semantic.dates 的本地镜像）────────────
_BARE_MONTH = re.compile(r"^\d{4}-\d{2}$")


def obs_month_of(d: str | None) -> str | None:
    if not d:
        return None
    return d[:7] if _BARE_MONTH.match(d) else None


# ── 概念：两个新的 100 基指数概念（册内此前没有任何 100 基 CPI/PPI 序列）──────
# uq_concept_identity 是 (code, entity_type, measure, unit, frequency)，同一个 code
# 允许配不同 unit 存多行，而 extract_* 的 code→id 解析会因同 code 多行而失败——
# 所以指数族用自己的 code，不复用任何 `%` 概念。
CONSTRUCT_CONCEPTS = [
    # (code, name_zh, name_en, category, unit, measure, frequency, entity_type, source)
    ("price.cpi.index.monthly",
     "居民消费价格指数（上年同月=100）",
     "Consumer price index, national (previous year same month = 100)",
     "价格指数", "指数", "value", "monthly", "country", SOURCE),
    ("price.ppi.index.monthly",
     "工业生产者出厂价格指数（上年同月=100）",
     "Producer price index for industrial products, national (same month last year = 100)",
     "价格指数", "指数", "value", "monthly", "country", SOURCE),
]

# (category, family) -> concept_code；**只含当月（指数）两族**
SERIES = {
    ("CPI", "当月"): "price.cpi.index.monthly",
    ("PPI", "当月"): "price.ppi.index.monthly",
}

# 故意不写的族，以及为什么（逐条打印，别让它变成看不见的空洞）
NOT_MAPPED = {
    ("CPI", "同比"): ("册内 `CPI_YOY` 已被 akshare 覆盖 519 行（1986-02 起），"
                     "与本源 30 期数值逐月相同；date 形态不同（2026-10-01 vs 2026-10）"
                     "不会去重，重复写等于每月多一个点"),
    ("PPI", "同比"): ("册内 `PPI_YOY` 已被 akshare 覆盖 405 行（1995-08 起），同上"),
}

# 值域护栏：100 基指数离不开 [50,200]
BOUNDS = (50.0, 200.0)

_NAME_RE = re.compile(r"^(CPI|PPI)\s+(当月|同比)\(全国\)$")

SKIP_NOTES: dict[str, str] = {}


def num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fmt(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s or "0"


def parse_indicator(name: str):
    """'CPI 当月(全国)' -> ('CPI', '当月'); 其它 -> None。"""
    m = _NAME_RE.match((name or "").strip())
    return (m.group(1), m.group(2)) if m else None


def prev_month(period: str) -> str | None:
    m = re.fullmatch(r"(\d{4})-(\d{2})", period or "")
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    return f"{y - 1}-12" if mo == 1 else f"{y}-{mo - 1:02d}"


def continuity_gap(periods: list[str]) -> str | None:
    """升序期列表里第一个断月的位置；连续则 None。"""
    for prev, cur in zip(periods, periods[1:]):
        if prev_month(cur) != prev:
            return f"{prev} 之后直接是 {cur}（缺 {_gap_label(prev, cur)}）"
    return None


def _gap_label(prev: str, cur: str) -> str:
    want, gaps = prev_month(cur), []
    while want and want != prev and len(gaps) < 12:
        gaps.append(want)
        want = prev_month(want)
    return "、".join(reversed(gaps)) or "?"


def unit_items(cur, source: str) -> list[dict]:
    cur.execute(
        "SELECT ci.payload FROM crawl_items ci"
        " JOIN crawl_runs r ON r.id = ci.run_id"
        " WHERE r.source = %s AND r.status = 'success'",
        (source,),
    )
    return [r[0] for r in cur.fetchall()]


def collect(items: list[dict]) -> dict[str, dict[str, dict]]:
    """code -> {period: payload}，同 (code, period) 取 scraped_at 最新。"""
    latest: dict[str, dict[str, dict]] = {c: {} for c in SERIES.values()}
    for p in items:
        key = parse_indicator(p.get("indicator_name"))
        if key not in SERIES:
            continue            # 含 118 条修复前的空壳行（indicator_name 为空）
        period = str(p.get("period") or "").strip()
        if not _BARE_MONTH.match(period):
            continue
        if num(p.get("indicator_value")) is None:
            continue            # 空值不写：不补零
        code = SERIES[key]
        prev = latest[code].get(period)
        if prev is None or str(p.get("scraped_at", "")) > str(prev.get("scraped_at", "")):
            latest[code][period] = p
    return latest


def build_rows(latest: dict[str, dict[str, dict]], notes: list[str]):
    rows: list[tuple] = []
    ranges: dict[str, tuple[float, float]] = {}
    for (cat, fam), code in SERIES.items():
        periods = sorted(latest[code])
        label = f"{cat} {fam}"
        if len(periods) < MIN_PERIODS:
            SKIP_NOTES[label] = (f"仅 {len(periods)} 期（<{MIN_PERIODS} 期门槛），"
                                 f"本批跳过不硬抽")
            continue
        gap = continuity_gap(periods)
        if gap:
            SKIP_NOTES[label] = f"序列不连续：{gap}"
            continue
        values = [num(latest[code][p].get("indicator_value")) for p in periods]
        lo, hi = min(values), max(values)
        blo, bhi = BOUNDS
        if not (blo <= lo and hi <= bhi):
            raise SystemExit(
                f"FATAL: {label}（{code}）值域 {lo}..{hi} 越出 [{blo},{bhi}]"
                f" —— 上游列语义可能变了（指数 vs 百分比），拒绝写入")
        ranges[code] = (lo, hi)
        for p in periods:
            rows.append((code, "country", ENTITY_CN, p, "month",
                         fmt(num(latest[code][p].get("indicator_value")))))
        notes.append(f"{label}: {len(periods)} 期连续（{periods[0]}..{periods[-1]}），"
                     f"值 {lo}..{hi} → {code}")
    return rows, ranges


def ensure_concepts(cur) -> dict[str, int]:
    for (code, name_zh, name_en, category, unit, measure, frequency,
         entity_type, source) in CONSTRUCT_CONCEPTS:
        cur.execute(
            "INSERT INTO concepts (code, name_en, name_zh, category, unit,"
            " measure, frequency, entity_type, source, verified, deprecated,"
            " concept_code)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,false,false,NULL)"
            " ON CONFLICT ON CONSTRAINT uq_concept_identity DO NOTHING",
            (code, name_en, name_zh, category, unit or None, measure,
             frequency, entity_type, source),
        )
    wanted = list(SERIES.values())
    ids: dict[str, int] = {}
    cur.execute(
        "SELECT id, code, unit FROM concepts"
        " WHERE deprecated = false AND code = ANY(%s) ORDER BY id",
        (wanted,),
    )
    for cid, code, unit in cur.fetchall():
        if code in ids:
            # 同 code 多 unit 会让 code→id 解析取决于行序 ⇒ 宁可失败也不猜
            raise SystemExit(
                f"FATAL: concept code {code} 在册内有多行（unit={unit}），拒绝猜一个")
        ids[code] = cid
    missing = sorted(set(wanted) - set(ids))
    if missing:
        raise SystemExit(f"FATAL: {len(missing)} 个 concept code 解析不到: {missing}")
    return ids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("DSN missing — 传 --dsn 或 export FD_OPEN_DATA_DSN")

    conn = psycopg2.connect(args.dsn)
    try:
        with conn.cursor() as cur:
            notes: list[str] = []
            latest = collect(unit_items(cur, SOURCE))
            rows, ranges = build_rows(latest, notes)
            ids = ensure_concepts(cur)
            if args.dry_run:
                conn.rollback()

            print(f"total observation rows to write: {len(rows)}")
            print("\nseries:")
            for n in notes:
                print("  -", n)
            print("\nskipped:")
            for k, v in SKIP_NOTES.items():
                print(f"  - {k}: {v}")
            print("\nnot mapped (设计如此):")
            for (cat, fam), why in NOT_MAPPED.items():
                print(f"  - {cat} {fam}: {why}")
            if args.dry_run:
                print("\ndry-run: no database writes")
                return 0

            with conn:  # 一个事务
                with conn.cursor() as c2:
                    c2.execute("SELECT count(*) FROM semantic_observations")
                    before = c2.fetchone()[0]
                    for (code, etype, eid, d, gran, value) in rows:
                        c2.execute(
                            "INSERT INTO semantic_observations"
                            " (concept_id, entity_type, entity_id, date,"
                            "  granularity, value, unit, source_used, fetched_at,"
                            "  obs_month)"
                            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now(),%s)"
                            " ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING",
                            (ids[code], etype, eid, d, gran, value, None,
                             SOURCE, obs_month_of(d)),
                        )
                    c2.execute("SELECT count(*) FROM semantic_observations")
                    after = c2.fetchone()[0]
            print(f"\nsemantic_observations: {before} -> {after}"
                  f" (inserted {after - before})")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
