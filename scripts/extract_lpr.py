#!/usr/bin/env python3
"""chinamoney-lpr — 贷款市场报价利率（LPR）进 semantic_observations（2026-11-14 建）。

与 `extract_rates.py`（Shibor）、`extract_ncd.py`、`extract_eastmoney`、`extract_uk_hpi`
同一套回填契约：additive（`ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING`）、不插值
不补零、同 (series, period) 取 `scraped_at` 最新、趋势闸写前硬断言、值域护栏越界即
FATAL、写 `obs_month` 治理桶。

**这条源有一次真实的口径变更，判据必须同时容得下两个时代**（实测，不是推断）：
- `2017-02-17` → `2019-08-19`：**日频**、只有 `LPR1Y`（2017 年 219 个日期、2018 年 250 个）
- `2019-08-20` 起（2019 年 8 月 LPR 改革）：**每月 20 日**报价、`LPR1Y` 与 `LPR5Y` 各一条
  （2020 年起每年 12 个日期）

所以期限不是「固定八档」而是「两条曲线各一条序列」，`curve` 标签就是概念码的来源。
两个时代都写：改革前的 4.31% 与改革后的 4.25% 之间那次跳变是真实的制度切换，若为了
曲线好看的把它截掉，就是把真实历史删掉（`2019-08-20` 那天 1Y 从 4.31 → 4.25 且 5Y
首次出现）。断点在概念的中文名与本文档里写明，不靠数据形状暗示。

**连续性阈值 45 天是有实测依据的**：两条曲线的最大间隔都是 **35 天**（1Y 中位 1 天＝
日频时代、5Y 中位 31 天＝月频），留 10 天余量覆盖「20 日遇周末顺延」（实测
`2026-06-22` 就是顺延日）。用日频的 5 天阈值会把整条月频序列判成不连续——这是
`extract_rates.py` 与本题唯一的实质差别。

**注意**：SQL 里不许写裸 `'%'` 字面量（psycopg2 会当占位符 ⇒ `IndexError`，而单测里
psycopg2 是替身模块、测不出来），单位一律走绑定参数。

Usage:
    python3 extract_lpr.py --dsn "$FD_OPEN_DATA_DSN" --dry-run
    python3 extract_lpr.py --dsn "$FD_OPEN_DATA_DSN"
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import psycopg2

SOURCE = "chinamoney-lpr"
MIN_PERIODS = 24                 # 下游趋势闸：≥24 连续期
MAX_GAP_DAYS = 45                # 实测最大间隔 35 天（月频报价遇周末顺延），留 10 天余量
ENTITY_CN = 1                    # entities: country / CN / 中国
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# payload 的 `curve` 标签 -> (concept code, 中文名, 英文名)
SERIES = {
    "LPR1Y": ("rate.lpr.1y", "贷款市场报价利率（LPR）1年期", "Loan Prime Rate (LPR), 1 year"),
    "LPR5Y": ("rate.lpr.5y", "贷款市场报价利率（LPR）5年期以上",
              "Loan Prime Rate (LPR), over 5 years"),
}

BOUNDS = (0.0, 20.0)             # 贷款利率的合理值域（%），越界说明列语义变了（% vs bp）


def obs_month_of(d: str | None) -> str | None:
    """日频源只认完整日期；`2026-11` 这种月值不给桶（granularity 是 day）。"""
    if not d or not _DAY.match(d):
        return None
    return d[:7]


def num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("%", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fmt(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s or "0"


def gap_note(dates: list[str], max_days: int = MAX_GAP_DAYS) -> str | None:
    """相邻两期不得超过 `max_days` 天。

    日频源用 5 天（周末 + 调休）；本源是**双节奏**（2019-08 前日频、之后月频），
    故用 45 天：实测最大间隔 35 天，全序列没有超过 40 天的间隔。
    """
    from datetime import date
    worst, where = 0, None
    for a, b in zip(dates, dates[1:]):
        try:
            span = (date.fromisoformat(b) - date.fromisoformat(a)).days
        except ValueError:
            return f"期值不是 YYYY-MM-DD：{a!r}/{b!r}"
        if span > worst:
            worst, where = span, (a, b)
    if worst > max_days:
        return f"{where[0]} 与 {where[1]} 相隔 {worst} 天（>{max_days}）"
    return None


def latest_run_items(cur, source: str) -> tuple[int | None, list[dict]]:
    """**只取最近一次成功运行**的 payload，不取历史并集。

    这条源一轮返回**全史快照**（实测一轮 800 行覆盖 2017-02..2026-10），不是每次追加
    新期；历史并集里可能留着早期窗口不一致造成的洞，而洞不会自己长回来 —— 按并集判
    连续会把序列永久按住。
    """
    cur.execute("SELECT id FROM crawl_runs WHERE source = %s AND status = 'success'"
                " ORDER BY started_at DESC LIMIT 1", (source,))
    row = cur.fetchone()
    if not row:
        return None, []
    rid = row[0]
    cur.execute("SELECT payload FROM crawl_items WHERE run_id = %s ORDER BY idx", (rid,))
    return rid, [r[0] for r in cur.fetchall()]


def collect(items: list[dict]) -> tuple[dict, set[str]]:
    """-> ({concept: {date: payload}}, 见到的其它 curve 标签集合)"""
    data: dict[str, dict[str, dict]] = {c: {} for c, _, _ in SERIES.values()}
    others: set[str] = set()
    for p in items:
        curve = (p.get("curve") or "").strip()
        spec = SERIES.get(curve)
        if spec is None:
            if curve:
                others.add(curve)
            continue
        day = str(p.get("date") or "").strip()
        if not _DAY.match(day) or num(p.get("value")) is None:
            continue
        slot = data[spec[0]]
        prev = slot.get(day)
        if prev is None or str(p.get("scraped_at", "")) > str(prev.get("scraped_at", "")):
            slot[day] = p
    return data, others


def build_rows(data: dict, notes: list[str], skips: dict):
    rows: list[tuple] = []
    lo_b, hi_b = BOUNDS
    for curve, (code, zh, _en) in SERIES.items():
        days = sorted(data[code])
        if not days:
            # **「这一轮没有这条曲线」与「期数不够被闸按住」是两件事**（LPR5Y 在
            # 2019-08-20 之前根本不存在）：写成「仅 0 期、让闸按住」会让人以为在等数据
            # 攒够，而实际是口径里本来就没有。事实归档、不当闸的判定。
            skips[code] = "本轮 payload 里没有这条曲线（不是闸拦的）"
            continue
        if len(days) < MIN_PERIODS:
            skips[code] = f"仅 {len(days)} 期（<{MIN_PERIODS}）—— 让闸按住，攒够自动开始写"
            continue
        gap = gap_note(days)
        if gap:
            skips[code] = f"序列不连续：{gap}"
            continue
        vals = [num(data[code][d].get("value")) for d in days]
        lo, hi = min(vals), max(vals)
        if not (lo_b <= lo and hi <= hi_b):
            raise SystemExit(f"FATAL: {code} 值域 {lo}..{hi} 越出 [{lo_b},{hi_b}]"
                             f" —— 列语义可能变了（% vs bp）")
        for d in days:
            rows.append((code, d, fmt(num(data[code][d].get("value")))))
        notes.append(f"{curve} {zh}: {len(days)} 期（{days[0]}..{days[-1]}），值 {lo}..{hi} → {code}")
    return rows


def ensure_concepts(cur) -> dict[str, int]:
    # **`'%'` 不能直接写进 SQL 字面量**：psycopg2 会把裸 `%` 当占位符 ⇒ 参数元组少一位、
    # 报 `IndexError: tuple index out of range`。单位一律走绑定参数。
    for _curve, (code, zh, en) in SERIES.items():
        cur.execute(
            "INSERT INTO concepts (code, name_en, name_zh, category, unit, measure, frequency,"
            " entity_type, source, verified, deprecated, concept_code)"
            " VALUES (%s,%s,%s,'利率',%s,'value','daily','country',%s,false,false,NULL)"
            " ON CONFLICT ON CONSTRAINT uq_concept_identity DO NOTHING",
            (code, en, zh, "%", SOURCE))
    ids: dict[str, int] = {}
    cur.execute("SELECT id, code FROM concepts WHERE deprecated = false AND code = ANY(%s)",
                ([c for c, _, _ in SERIES.values()],))
    for cid, code in cur.fetchall():
        if code in ids:
            raise SystemExit(f"FATAL: concept {code} 册内多行，拒绝猜")
        ids[code] = cid
    missing = sorted({c for c, _, _ in SERIES.values()} - set(ids))
    if missing:
        raise SystemExit(f"FATAL: 解析不到 concept: {missing}")
    return ids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("DSN missing — 传 --dsn 或 export FD_OPEN_DATA_DSN")
    dsn = re.sub(r"^(postgres(?:ql)?)\+[a-z0-9_]+://", r"\1://", args.dsn.strip())

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
            ids = ensure_concepts(cur)
            if args.dry_run:
                conn.rollback()

            print(f"取最近一次成功运行 run_id={rid}（{len(items)} 条 payload）")
            print(f"待写 {len(rows)} 行")
            for n in notes:
                print("  -", n)
            if others:
                print("not mapped（未登记的 curve 标签）:")
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

            with conn:
                with conn.cursor() as c2:
                    c2.execute("SELECT count(*) FROM semantic_observations")
                    before = c2.fetchone()[0]
                    from psycopg2.extras import execute_values
                    payload = [(ids[c], ENTITY_CN, d, v, "%", SOURCE, obs_month_of(d))
                               for (c, d, v) in rows]
                    execute_values(
                        c2,
                        "INSERT INTO semantic_observations"
                        " (concept_id, entity_type, entity_id, date, granularity,"
                        "  value, unit, source_used, fetched_at, obs_month) VALUES %s"
                        " ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING",
                        payload,
                        template="(%s,'country',%s,%s,'day',%s,%s,%s,now(),%s)")
                    c2.execute("SELECT count(*) FROM semantic_observations")
                    after = c2.fetchone()[0]
            print(f"\nsemantic_observations: {before} -> {after} (inserted {after - before})")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
