#!/usr/bin/env python3
"""chinamoney-shibor — Shibor 定盘利率进 semantic_observations（2026-11-14 建）。

**这份脚本的价值不在今天写了多少行，而在它「攒够就自动开始写」**：Shibor 目前只攒到
15 个交易日，而趋势闸是 ≥24 连续期 —— 所以本轮实跑应当是「跳过」，等它自然跨过闸
（约再 9 个交易日）由每日作业自动开始写入，不需要有人回来改代码。

同族契约（与 `extract_batch1/2`、`extract_eastmoney`、`extract_uk_hpi` 相同）：
additive（`ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING`）、不插值不补零、同
(series, period) 取 `scraped_at` 最新、趋势闸写前硬断言、写 `obs_month` 治理桶。

两件设计选择：

1. **一档期限一个概念**。Shibor 的期限是**固定的 8 档**（O/N、1W、2W、1M、3M、6M、
   9M、1Y），这在金融数据里是标准切法，不需要「期限」这种实体维度。对照：同批的
   `chinamoney-ncd-curve` 的 `tenor_days` 是**连续值**（7..365）、`chinabond-yield-curve`
   是「多条曲线 × 多期限」，两者都要先定「期限算概念还是算实体」，本脚本不碰。
2. **只写 Shibor 八档，不写 FR/FDR**。同一个 payload 里还有 `FR001/FR007/FR014` 与
   `FDR001/FDR007/FDR014`（银行间质押式回购定盘利率），它们是**另一族**（回购 vs 拆借），
   量纲相同但语义不同；混进 `rate.shibor.*` 会让「Shibor 3M」这个查询返回回购利率。

Usage:
    python3 extract_rates.py --dsn "$FD_OPEN_DATA_DSN" --dry-run
    python3 extract_rates.py --dsn "$FD_OPEN_DATA_DSN"
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

SOURCE = "chinamoney-shibor"
MIN_PERIODS = 24                 # 下游趋势闸：≥24 连续期
ENTITY_CN = 1                    # entities: country / CN / 中国
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# payload 的 `curve` 标签 -> (concept code, 中文名, 英文名)
SERIES = {
    "ShiborO/N": ("rate.shibor.on", "上海银行间同业拆放利率 隔夜", "SHIBOR overnight"),
    "Shibor1W": ("rate.shibor.1w", "上海银行间同业拆放利率 1周", "SHIBOR 1 week"),
    "Shibor2W": ("rate.shibor.2w", "上海银行间同业拆放利率 2周", "SHIBOR 2 weeks"),
    "Shibor1M": ("rate.shibor.1m", "上海银行间同业拆放利率 1月", "SHIBOR 1 month"),
    "Shibor3M": ("rate.shibor.3m", "上海银行间同业拆放利率 3月", "SHIBOR 3 months"),
    "Shibor6M": ("rate.shibor.6m", "上海银行间同业拆放利率 6月", "SHIBOR 6 months"),
    "Shibor9M": ("rate.shibor.9m", "上海银行间同业拆放利率 9月", "SHIBOR 9 months"),
    "Shibor1Y": ("rate.shibor.1y", "上海银行间同业拆放利率 1年", "SHIBOR 1 year"),
}

NOT_MAPPED = {
    "FR001/FR007/FR014": "银行间质押式回购定盘利率（存款类机构全市场）；与拆借利率是两个族",
    "FDR001/FDR007/FDR014": "银银间回购定盘利率（存款类机构本币）；同上，另立一族",
}

BOUNDS = (0.0, 20.0)             # 拆借利率的合理值域（%），越界说明列语义变了






def prev_day(d: str) -> str | None:
    from datetime import date, timedelta
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", d or "")
    if not m:
        return None
    try:
        return (date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                - timedelta(days=1)).isoformat()
    except ValueError:
        return None


def gap_note(dates: list[str]) -> str | None:
    """日频数据只要求「相邻两期不超过 5 天」（周末 + 一个调休）。"""
    from datetime import date
    worst, where = 0, None
    for a, b in zip(dates, dates[1:]):
        try:
            da = date.fromisoformat(a)
            db = date.fromisoformat(b)
        except ValueError:
            return f"期值不是 YYYY-MM-DD：{a!r}/{b!r}"
        span = (db - da).days
        if span > worst:
            worst, where = span, (a, b)
    if worst > 5:
        return f"{where[0]} 与 {where[1]} 相隔 {worst} 天（>5）"
    return None



def collect(items: list[dict]) -> dict[str, dict[str, dict]]:
    data: dict[str, dict[str, dict]] = {c: {} for c, _, _ in SERIES.values()}
    for p in items:
        spec = SERIES.get((p.get("curve") or "").strip())
        if spec is None:
            continue
        day = str(p.get("date") or "").strip()
        if not _DAY.match(day) or num(p.get("value")) is None:
            continue
        slot = data[spec[0]]
        prev = slot.get(day)
        if prev is None or str(p.get("scraped_at", "")) > str(prev.get("scraped_at", "")):
            slot[day] = p
    return data


def build_rows(data: dict, notes: list[str], skips: dict):
    rows: list[tuple] = []
    lo_b, hi_b = BOUNDS
    for curve, (code, zh, _en) in SERIES.items():
        days = sorted(data[code])
        if len(days) < MIN_PERIODS:
            skips[curve] = f"仅 {len(days)} 个交易日（<{MIN_PERIODS}）—— 让闸按住，攒够自动开始写"
            continue
        gap = gap_note(days)
        if gap:
            skips[curve] = f"序列不连续：{gap}"
            continue
        vals = [num(data[code][d].get("value")) for d in days]
        lo, hi = min(vals), max(vals)
        if not (lo_b <= lo and hi <= hi_b):
            raise SystemExit(f"FATAL: {curve} 值域 {lo}..{hi} 越出 [{lo_b},{hi_b}]"
                             f" —— 列语义可能变了（% vs bp）")
        for d in days:
            rows.append((code, d, fmt(num(data[code][d].get("value")))))
        notes.append(f"{curve}: {len(days)} 个交易日连续（{days[0]}..{days[-1]}），"
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
            print(f"取最近一次成功运行 run_id={rid}（{len(items)} 条 payload）")
            rows = build_rows(collect(items), notes, skips)
            ids = ensure_concepts(cur, SERIES, source=SOURCE, category="利率",
                                  unit="%", frequency="daily", entity_type="country")
            if args.dry_run:
                conn.rollback()

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

            payload = [(c, ENTITY_CN, d, v) for (c, d, v) in rows]
            before, after = write_rows(conn, payload, ids=ids, source=SOURCE,
                                       unit="%", entity_type="country")
            print(f"\nsemantic_observations: {before} -> {after} (inserted {after - before})")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
