#!/usr/bin/env python3
"""uk-hpi — 英国房价指数进 semantic_observations（2026-11-14 建）。

与 `extract_batch1/2`、`extract_eastmoney` 同一套回填契约：additive
（`ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING`）、不插值不补零、同
(series, period) 取 `scraped_at` 最新、写 `obs_month` 治理桶。

**这份数据是当天刚从截断里救出来的**：`uk-hpi` 的 manifest 原先没声明 `limit`，
于是取了 chart 缺省的 100 行 —— 而它的上游是 Land Registry 全量 CSV（区域 × 月长表，
文件顺序＝区域字母序、区内日期升序）⇒ 库内只有**第 1 个区域的 100 个月**
（2004-01..2012-04，`region` 去重数 = 1），窗口永不前进。补 `limit: 20000` 后一轮
落了 9,453 行 / 27 个区域 / 380 个月（1995-01..2026-08）。是
`fd-infra-deploy/tools/check_crawl_row_cap.py` 的 R2 点名的。

两件设计选择：

1. **实体按 ONS 区码建 `region`**（`E06000008` 这类），`entity_type='region'` 册内已有
   33 行先例（`CN-11` 那种）。区域名只作 `name_en`/`metadata.source_label`，**不用名字
   做键**——CSV 里的 `RegionName` 有重名与拼写变体，区码才是稳定标识。
2. **只写两条**：`index`（Land Registry 的房价指数）与 `average_price`（英镑）。
   `sales_volume`（成交量）**故意不写**，与 eastmoney 的 `NOT_MAPPED` 同一个纪律：
   同一份 payload 里能写的东西不必都写，写进来的每一条都要有人能解释它的量纲。
   想加时按下面 `SERIES` 加一行即可。

Usage:
    python3 extract_uk_hpi.py --dsn "$FD_OPEN_DATA_DSN" --dry-run
    python3 extract_uk_hpi.py --dsn "$FD_OPEN_DATA_DSN"
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import psycopg2

SOURCE = "uk-hpi"
MIN_PERIODS = 24                 # 下游趋势闸：≥24 连续期
_MONTH = re.compile(r"^\d{4}-\d{2}$")

# concept code -> (payload 值字段, 单位, 中文名, 英文名, 值域下限, 值域上限)
SERIES = {
    "price.house_uk.index.monthly": (
        "index", "指数", "英国房价指数（Land Registry，按区域）",
        "UK House Price Index by local authority (Land Registry)", 1.0, 5000.0),
    "price.house_uk.average_price.monthly": (
        "average_price", "GBP", "英国平均房价（按区域）",
        "UK average house price by local authority (Land Registry)", 5000.0, 5e7),
}

# 故意不写的字段，逐次打印
NOT_MAPPED = {
    "sales_volume": "成交量（套）；量纲与「指数/均价」不同类，等有人真要用再单独给概念",
}


def obs_month_of(d: str | None) -> str | None:
    if not d:
        return None
    return d[:7] if _MONTH.match(d) else None


def num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
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


def prev_month(period: str) -> str | None:
    m = re.fullmatch(r"(\d{4})-(\d{2})", period or "")
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    return f"{y - 1}-12" if mo == 1 else f"{y}-{mo - 1:02d}"


def continuity_gap(periods: list[str]) -> str | None:
    for a, b in zip(periods, periods[1:]):
        if prev_month(b) != a:
            return f"{a} 之后直接是 {b}"
    return None


def unit_items(cur, source: str) -> list[dict]:
    cur.execute(
        "SELECT ci.payload FROM crawl_items ci JOIN crawl_runs r ON r.id = ci.run_id"
        " WHERE r.source = %s AND r.status = 'success'", (source,))
    return [r[0] for r in cur.fetchall()]


def collect(items: list[dict]) -> tuple[dict, dict]:
    """-> ({(area_code, region) 集合}, {concept: {area_code: {month: payload}}})"""
    regions: dict[str, str] = {}
    data: dict[str, dict[str, dict[str, dict]]] = {c: {} for c in SERIES}
    for p in items:
        code = (p.get("area_code") or "").strip()
        month = str(p.get("month") or "").strip()
        if not code or not _MONTH.match(month):
            continue
        regions[code] = (p.get("region") or code).strip()
        for concept, (field, *_rest) in SERIES.items():
            if num(p.get(field)) is None:
                continue
            slot = data[concept].setdefault(code, {})
            prev = slot.get(month)
            if prev is None or str(p.get("scraped_at", "")) > str(prev.get("scraped_at", "")):
                slot[month] = p
    return regions, data


def build_rows(data: dict, notes: list[str], skips: dict):
    rows: list[tuple] = []
    for concept, (field, _unit, _zh, _en, lo_b, hi_b) in SERIES.items():
        for code, months in sorted(data[concept].items()):
            periods = sorted(months)
            if len(periods) < MIN_PERIODS:
                skips[f"{concept}@{code}"] = f"仅 {len(periods)} 期（<{MIN_PERIODS}）"
                continue
            gap = continuity_gap(periods)
            if gap:
                skips[f"{concept}@{code}"] = f"序列不连续：{gap}"
                continue
            vals = [num(months[m].get(field)) for m in periods]
            lo, hi = min(vals), max(vals)
            if not (lo_b <= lo and hi <= hi_b):
                raise SystemExit(
                    f"FATAL: {concept}@{code} 值域 {lo}..{hi} 越出 [{lo_b},{hi_b}]"
                    f" —— 列语义可能变了（指数 vs 均价），拒绝写入")
            for m in periods:
                rows.append((concept, code, m, fmt(num(months[m].get(field)))))
        notes.append(f"{concept}: {len(data[concept])} 个区域通过闸")
    return rows


def ensure_concepts(cur) -> dict[str, int]:
    for concept, (field, unit, zh, en, *_r) in SERIES.items():
        cur.execute(
            "INSERT INTO concepts (code, name_en, name_zh, category, unit, measure,"
            " frequency, entity_type, source, verified, deprecated, concept_code)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,false,false,NULL)"
            " ON CONFLICT ON CONSTRAINT uq_concept_identity DO NOTHING",
            (concept, en, zh, "价格指数", unit, "value", "monthly", "region", SOURCE))
    ids: dict[str, int] = {}
    cur.execute("SELECT id, code FROM concepts WHERE deprecated = false AND code = ANY(%s)",
                (list(SERIES),))
    for cid, code in cur.fetchall():
        if code in ids:
            raise SystemExit(f"FATAL: concept {code} 册内多行，拒绝猜")
        ids[code] = cid
    missing = sorted(set(SERIES) - set(ids))
    if missing:
        raise SystemExit(f"FATAL: 解析不到 concept: {missing}")
    return ids


def ensure_regions(cur, regions: dict[str, str]) -> dict[str, int]:
    """按 ONS 区码建 region 实体（幂等：uq_entity_type_code）。"""
    for code, name in sorted(regions.items()):
        cur.execute(
            "INSERT INTO entities (entity_type, code, name_en, name_zh, metadata_json)"
            " VALUES ('region', %s, %s, %s, %s::jsonb)"
            " ON CONFLICT ON CONSTRAINT uq_entity_type_code DO NOTHING",
            (code, name, name,
             '{"registry": "uk-hpi", "source_label": %s}' % _json_str(name)))
    ids: dict[str, int] = {}
    cur.execute("SELECT id, code FROM entities WHERE entity_type = 'region' AND code = ANY(%s)",
                (list(regions),))
    for eid, code in cur.fetchall():
        ids[code] = eid
    missing = sorted(set(regions) - set(ids))
    if missing:
        raise SystemExit(f"FATAL: {len(missing)} 个区域实体没建出来: {missing[:5]}")
    return ids


def _json_str(s: str) -> str:
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


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
            regions, data = collect(unit_items(cur, SOURCE))
            rows = build_rows(data, notes, skips)
            cids = ensure_concepts(cur)
            eids = ensure_regions(cur, regions)
            if args.dry_run:
                conn.rollback()

            print(f"区域 {len(regions)} 个；待写 {len(rows)} 行")
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

            with conn:
                with conn.cursor() as c2:
                    c2.execute("SELECT count(*) FROM semantic_observations")
                    before = c2.fetchone()[0]
                    # **批量写**：这条源一轮近 1.9 万行，逐条 execute 是逐次往返，
                    # 实测 6 分钟还没提交完（生产作业的 activeDeadlineSeconds 会被它顶爆）。
                    # execute_values 每批 1000 行，约 20 次往返。
                    from psycopg2.extras import execute_values
                    payload = [(cids[c], eids[a], m, v, SERIES[c][1], SOURCE, obs_month_of(m))
                               for (c, a, m, v) in rows]
                    template = "(%s,'region',%s,%s,'month',%s,%s,%s,now(),%s)"
                    for i in range(0, len(payload), 1000):
                        execute_values(
                            c2,
                            "INSERT INTO semantic_observations"
                            " (concept_id, entity_type, entity_id, date, granularity,"
                            "  value, unit, source_used, fetched_at, obs_month)"
                            " VALUES %s"
                            " ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING",
                            payload[i:i + 1000], template=template)
                    c2.execute("SELECT count(*) FROM semantic_observations")
                    after = c2.fetchone()[0]
            print(f"\nsemantic_observations: {before} -> {after}"
                  f" (inserted {after - before})")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
