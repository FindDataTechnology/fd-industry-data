#!/usr/bin/env python3
"""扫描「数据已落地、读面 0 行」的源，量出哪些其实已经具备 ≥24 连续期序列。

**要回答的问题**：`crawl_items` 里的 payload 那么多，哪些源只是**没有映射**，
哪些源是数据形状本身就不进时间序列（榜单、文件、网格、事件流）？

判据刻意保守——本脚本**只做诊断，不做判定**，输出一张候选表供人挑：

* **期字段**：键名像 period/date/month/year/time/day/report_date，且值能被解析成
  一种日历形态（`YYYY`、`YYYY-MM`、`YYYY-MM-DD`、`YYYYQn`、`YYYY-Mnn`、
  `YYYY-Wnn`、ISO 时间戳）。一个 payload 里出现多个候选时取「不同取值数最多」
  且「最长 span」的那个。
* **值字段**：键名像 value/price/index/rate/close/amount/count/volume/number，
  且值可解析成数字。
* **序列键**：除期字段/值字段外，基数 < 200 的字符串字段组合（优先
  indicator_name/series/metric/item/name/symbol/subject 这类具名维度）。
* **闸**：同一序列 ≥24 个不同期即 `pass`；期间无断档即 `contiguous`。

为什么按源抽样而不是全量：`crawl_items` 30 天内 46 万行，全量读只会在本地把内存
撑爆，而「有没有一条 24 期的真序列」在最近 3000 行里就能看出来。

Usage:
    python3 scan_unmapped_series.py --dsn "$FD_OPEN_DATA_DSN"            # 全量扫描
    python3 scan_unmapped_series.py --dsn "$..." --source shibor --json # 单源 / 机读
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date

import psycopg2

MIN_PERIODS = 24
SAMPLE_PER_SOURCE = 3000

PERIOD_KEYS = re.compile(r"(period|^date$|_date$|^month$|^year$|^time$|^day$|"
                         r"report_?date|trade_?date|stat_?date|end_?date|as_?of)",
                         re.I)
VALUE_KEYS = re.compile(r"(value|price|index|rate|close|open|high|low|amount|"
                        r"count|volume|number|total|balance|yield|revenue)", re.I)
NAMED_KEYS = re.compile(r"(indicator|series|metric|item|name|symbol|subject|"
                        r"category|type|region|country|province|city)", re.I)

YEAR = re.compile(r"^(\d{4})$")
MONTH = re.compile(r"^(\d{4})-(\d{2})$")
DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
QUARTER = re.compile(r"^(\d{4})[-/]?Q([1-4])$", re.I)
IMFMONTH = re.compile(r"^(\d{4})-M(\d{2})$", re.I)
WEEK = re.compile(r"^(\d{4})-W(\d{2})$", re.I)
TS = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ]\d{2}:\d{2}")


def period_kind(v: str) -> str | None:
    v = (v or "").strip()
    if YEAR.match(v):
        return "year"
    if MONTH.match(v):
        return "month"
    if DAY.match(v):
        return "day"
    if QUARTER.match(v):
        return "quarter"
    if IMFMONTH.match(v):
        return "month"
    if WEEK.match(v):
        return "week"
    if TS.match(v):
        return "day"
    return None


def to_num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("%", "")
    if not s or s in {"-", "--", "N/A", "null", "None"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def ordinal(kind: str, v: str) -> int | None:
    """把期值映射成**同量纲的天序数**（判连续只做整数相减）。

    一律折成天：年→1 月 1 日、月→当月 1 日、季→季首月 1 日。若按「年×12／月」这种
    混合量纲出数，年频序列的间隔会是 12 而被当成「月频」，阈值全错（该写法当天就被
    自己的用例抓到）。
    """
    v = v.strip()
    try:
        m = YEAR.match(v)
        if m:
            return date(int(m.group(1)), 1, 1).toordinal()
        m = MONTH.match(v) or IMFMONTH.match(v)
        if m:
            return date(int(m.group(1)), int(m.group(2)), 1).toordinal()
        m = DAY.match(v) or TS.match(v)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).toordinal()
        m = QUARTER.match(v)
        if m:
            return date(int(m.group(1)), int(m.group(2)) * 3 - 2, 1).toordinal()
        m = WEEK.match(v)
        if m:
            return date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1).toordinal()
    except ValueError:
        return None
    return None


STEP = {"year": 12, "month": 1, "quarter": 1, "week": 7}   # day 单独按 <=7 天容差


def frequency(kind: str, ords: list[int]) -> tuple[str, bool]:
    """从**实际间隔**推频率并判连续。

    **不能只看期字符串的形状**（2026-11-14 踩到）：`chinamoney-lpr` 的期值是
    `2022-09-20`／`2022-10-20` 这种完整日期，形状判成「日频」，于是 30 天的正常
    月间隔被当成断档 —— 一个真实可用的月频序列被报成「不连续」。正确做法是拿
    间隔的**中位数**当频率：1→日、7→周、28-31→月、90-92→季、365→年，并在该频率上
    判「有没有断」（日频容忍周末/节假日，所以容差取 max(med*1.6, med+2)）。
    """
    if len(ords) < 2:
        return (kind, True)
    gaps = sorted(b - a for a, b in zip(ords, ords[1:]))
    med = gaps[len(gaps) // 2]
    if med <= 2:
        freq = "day"
    elif med <= 10:
        freq = "week"
    elif med <= 62:
        freq = "month"
    elif med <= 120:
        freq = "quarter"
    else:
        freq = "year"
    tol = max(med * 1.6, med + 2)
    return (freq, gaps[-1] <= tol)


def profile(payloads: list[dict]) -> dict | None:
    """在抽样 payload 里找「最好的那条序列」。"""
    kind_votes: Counter = Counter()
    for p in payloads[:200]:
        for k, v in p.items():
            if isinstance(v, str) and PERIOD_KEYS.search(k):
                kind = period_kind(v)
                if kind:
                    kind_votes[(k, kind)] += 1
    if not kind_votes:
        return None
    pkey, kind = kind_votes.most_common(1)[0][0]

    value_keys = sorted({k for p in payloads[:200] for k, v in p.items()
                         if VALUE_KEYS.search(k) and to_num(v) is not None})
    if not value_keys:
        return None
    vkey = value_keys[0]

    # 具名维度：基数 < 200 的字符串字段（期/值字段排除）
    dim_keys: list[str] = []
    for k in payloads[0]:
        if k == pkey or k in value_keys:
            continue
        vals = {p.get(k) for p in payloads[:500] if isinstance(p.get(k), str)}
        if 0 < len(vals) < 200:
            dim_keys.append(k)
    dim_keys.sort(key=lambda k: (not NAMED_KEYS.search(k), k))
    dim = dim_keys[0] if dim_keys else None

    series: dict[str, set] = defaultdict(set)
    for p in payloads:
        raw = p.get(pkey)
        if not isinstance(raw, str) or not period_kind(raw):
            continue
        if to_num(p.get(vkey)) is None:
            continue
        series[str(p.get(dim)) if dim else ""].add(raw.strip())

    best = None
    for label, periods in series.items():
        ords = sorted({o for o in (ordinal(kind, x) for x in periods) if o is not None})
        if not ords:
            continue
        freq, contig = frequency(kind, ords)
        cand = {"series": label, "periods": len(ords),
                "first": min(periods), "last": max(periods),
                "contiguous": contig, "kind": kind, "freq": freq}
        if best is None or (cand["periods"], cand["contiguous"]) > (best["periods"],
                                                                   best["contiguous"]):
            best = cand
    if best is None:
        return None
    best.update({"period_key": pkey, "value_key": vkey, "dim_key": dim,
                 "n_series": len(series), "passes_gate": best["periods"] >= MIN_PERIODS})
    return best


def unmapped_sources(cur) -> list[tuple[str, int]]:
    cur.execute("""
        with landed as (
          select r.source, count(*) as items
            from crawl_items i join crawl_runs r on r.id = i.run_id
           where r.status = 'success' and r.started_at > now() - interval '30 days'
           group by 1)
        select l.source, l.items from landed l
         where not exists (select 1 from semantic_observations o
                            where o.source_used = l.source)
         order by l.items desc
    """)
    return cur.fetchall()


def sample(cur, source: str, limit: int) -> list[dict]:
    """最近的 limit 条 payload。

    **不要用 `row_number() over (order by ...)`**：那会让 PostgreSQL 把该源的全部
    `crawl_items` 排完再筛（71 个源各排一次，实测两分钟没跑完一个）。普通
    `order by … limit` 才能走 top-N heapsort。
    """
    cur.execute("""
        select i.payload
          from crawl_items i join crawl_runs r on r.id = i.run_id
         where r.source = %s and r.status = 'success'
         order by r.started_at desc
         limit %s
    """, (source, limit))
    return [r[0] for r in cur.fetchall() if isinstance(r[0], dict)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    ap.add_argument("--source", default=None, help="只扫含该子串的源")
    ap.add_argument("--limit", type=int, default=SAMPLE_PER_SOURCE)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("DSN missing — 传 --dsn 或 export FD_OPEN_DATA_DSN")

    conn = psycopg2.connect(args.dsn)
    try:
        with conn.cursor() as cur:
            todo = unmapped_sources(cur)
            if args.source:
                todo = [t for t in todo if args.source in t[0]]
            out = []
            for i, (source, items) in enumerate(todo, 1):
                prof = profile(sample(cur, source, args.limit))
                out.append({"source": source, "items": items, "best": prof})
                print(f"  [{i}/{len(todo)}] {source}", file=sys.stderr, flush=True)
    finally:
        conn.close()

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    winners = [o for o in out if o["best"] and o["best"]["passes_gate"]
               and o["best"]["contiguous"]]
    near = [o for o in out if o["best"] and o["best"]["passes_gate"]
            and not o["best"]["contiguous"]]
    rest = [o for o in out if o not in winners and o not in near]

    print(f"读面为 0 行的源：{len(out)} 个（30 天内成功落地，抽样 ≤{args.limit} 行/源）\n")

    def show(title, rows, note=""):
        print(f"== {title}：{len(rows)} 个 =={note}")
        print(f"{'source':<30}{'items':>8}{'期数':>7}{'频率':>7}{'跨度':>22}  {'维度':<14}{'期/值字段'}")
        for o in rows[:args.top]:
            b = o["best"]
            if b is None:
                print(f"{o['source']:<30}{o['items']:>8}{'-':>7}{'-':>7}{'（样本里没找到期/值对）':>22}")
                continue
            span = f"{b['first']}..{b['last']}"
            print(f"{o['source']:<30}{o['items']:>8}{b['periods']:>7}{b.get('freq','-'):>7}"
                  f"{span:>22}  {(b['dim_key'] or '-'):<14}{b['period_key']}/{b['value_key']}")
        print()

    show("已过闸（≥24 期且连续）—— 只缺映射", winners)
    show("期数够但中间断档", near)
    show("形状不进时间序列（榜单/文件/网格/事件）或期数不足", rest)
    print(f"合计：{len(winners)} 个可直接补映射，{len(near)} 个要先看断档原因，"
          f"{len(rest)} 个不属于时间序列。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
