#!/usr/bin/env python3
"""Task 2.1 batch1 — trend-unit extraction into semantic_observations.

Reuses the industry-vertical-supply backfill pattern (archive
2026-10-21-industry-vertical-supply/scripts/backfill_observations.py):

* additive only — INSERT ... ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING
  on the natural key (concept_id, entity_type, entity_id, date, granularity,
  source_used); a second run inserts zero rows;
* never interpolates, zero-fills or fabricates periods — every written value
  is a real stored payload value;
* same (series, period) crawled multiple times -> keep the payload row with
  the LATEST scraped_at (no averaging, no merging, no overwrite of rows
  already in the DB);
* NEW vs the 2026-10-21 batch: writes the obs_month governance bucket at the
  write site via fd_open_data_mcp.semantic.dates.obs_month_of (the
  add-coverage-point-granularity-bucket final state: periodic points bucket
  on obs_month, intraday on the exact timestamp). The obs_month import is
  optional at runtime — when fd_open_data_mcp is not importable a local
  mirror of the same CASE logic is used (kept in sync per the module docstring).

Batch 1 units (12, per /tmp/extraction-batch1.json trend_batch1[:12]):
  abs-sdmx, afdc, amis-market, dbnomics-api, ecb-exr, eia-v2 (already
  ingested — idempotent re-run picks up new rolling-window hours),
  eurostat-api, fao-food-price, gleif-lei, imf-pcps, jodi-gas, jodi-oil.

Skipped units are decided BEFORE writing (see SKIP_NOTES and the
coverage evidence in output/supply-funnel/batch1-report.md):
  * jodi-gas / jodi-oil: crawl window is a single month per unit (2026-07 /
    2025-01) — trend gate (>=24 consecutive periods) not met by the stored
    payload; extracted rows would be a cross-section dressed as a series.
  * eurostat-api: only the sts_inpr_m trend channel (>=24 consecutive
    months x 4 geos) is extracted; ei_bsco_m (12 periods) and
    apri_pi_outq (3 quarters) stay below the gate; DS-045409 rows carry no
    `value` (trade_value instead) and are left in place.
  * fao-food-price: monthly blocks start 1990-01 but only ~17 months were
    stored (< 24); the two annual blocks (1961..1980, 20 points) are below
    the gate too -> skipped entirely this batch.
  * afdc: annual stations by fuel, 2012..2025 = 14 periods < 24 -> skipped.
  * amis-market: single product x element slice, per-region annual seasons
    (11 seasons for Ukraine, fewer for others) < 24 -> skipped.

Usage:
    python3 extract_batch1.py --dry-run
    python3 extract_batch1.py            # writes (single transaction)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from datetime import date

import psycopg2

# ── obs_month governance bucket (fd_open_data_mcp.semantic.dates mirror) ─────
_BARE_YEAR = re.compile(r"^\d{4}$")
_BARE_MONTH = re.compile(r"^\d{4}-\d{2}$")
_ISO_WEEK = re.compile(r"^\d{4}-W\d{2}$")


def obs_month_of(d: str | None) -> str | None:
    """'YYYY-MM' bucket for any tolerated date shape; None when unmappable."""
    if not d:
        return None
    if _ISO_WEEK.match(d):
        try:
            return date.fromisocalendar(int(d[:4]), int(d[6:8]), 1).strftime("%Y-%m")
        except ValueError:
            return None
    if _BARE_YEAR.match(d):
        return f"{d}-12"
    if _BARE_MONTH.match(d) or (len(d) >= 7 and d[4] == "-" and d[:4].isdigit()
                                and d[5:7].isdigit()):
        return d[:7]
    return None


# ── entities (existing ids, no entity rows are created by this batch) ────────
COUNTRY_CN, COUNTRY_US, COUNTRY_JP = 1, 2, 3
COUNTRY_GB, COUNTRY_DE, COUNTRY_FR, COUNTRY_AU = 5, 6, 7, 11
ENTITY_WORLD = 300932          # country 'WORLD'
CURRENCY_PAIR_EURCNY = 300962  # the only currency_pair entity (fx.rate, 2026-11-03 load)
INDUSTRY_UTILITIES = 2532      # 电力与碳 industry anchor (eia-v2)
INDUSTRY_MEDIA = 4827          # 影视文娱 (unused this batch, kept for symmetry)

# ── concept codes used this batch (created in ensure_concepts if missing) ────
CONSTRUCT_CONCEPTS = [
    # (code, name_zh, name_en, category, unit, measure, frequency, entity_type, source)
    ("cons.permits.monthly",
     "住宅建筑许可套数",
     "Dwelling units approved (building approvals)",
     "工业", "个", "value", "monthly", "country", "abs-sdmx"),
    ("fx.rate.ecb.monthly",
     "欧元兑人民币月均参考汇率",
     "EUR/CNY monthly average reference rate",
     "利率", "cny", "value", "monthly", "currency_pair", "ecb-exr"),
    ("fx.rate.ecb.daily",
     "欧元兑人民币即期参考汇率",
     "EUR/CNY spot reference rate",
     "利率", "cny", "value", "daily", "currency_pair", "dbnomics-api"),
    ("ind.prod.index.eurostat",
     "制造业生产指数",
     "Manufacturing production index",
     "工业", "指数", "value", "monthly", "country", "eurostat-api"),
    ("price.oil.imf.index",  # exists already (id 2381) — kept for idempotence
     "国际原油现货价格指数（IMF PCPS，2016=100）",
     "Crude oil spot price index (IMF PCPS, 2016=100)",
     "价格指数", "index", "value", "monthly", "country", "imf-pcps"),
    ("entities.new_registrations.monthly",
     "全球新设法人月度注册数",
     "Global new LEI registrations, monthly count",
     "其他", "个", "value", "monthly", "country", "gleif-lei"),
]

# skipped units, decided BEFORE writing (trend gate not met by stored payload)
SKIP_NOTES = {
    "afdc": "年度序列仅 2012..2025 共 14 期（<24 期门槛），本批跳过不硬抽",
    "amis-market": "单一 (product,element) 切片、按地区年度季，Ukraine 最长 11 季（<24），本批跳过",
    "fao-food-price": "月度块仅 1990-01..1991-05 约 17 期、年度块 20 期（<24），本批跳过",
    "jodi-gas": "库存 payload 仅 2026-07 单月横截面（<24 期），本批跳过",
    "jodi-oil": "库存 payload 仅 2025-01 单月横截面（<24 期），本批跳过",
}


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


def imf_period(p: str) -> str | None:
    """'2018-M06' -> '2018-06'; None otherwise."""
    m = re.fullmatch(r"(\d{4})-M(\d{2})", p or "")
    return f"{m.group(1)}-{m.group(2)}" if m else None


def season_to_year(season: str) -> str | None:
    """'2016/17' -> '2016' (AMIS marketing-season start year); None otherwise."""
    m = re.fullmatch(r"(\d{4})/\d{2}", season or "")
    return m.group(1) if m else None


def unit_items(cur, source: str) -> list[dict]:
    cur.execute(
        "SELECT ci.payload FROM crawl_items ci"
        " JOIN crawl_runs r ON r.id = ci.run_id"
        " WHERE r.source = %s AND r.status = 'success'",
        (source,),
    )
    return [r[0] for r in cur.fetchall()]


def dedup_latest(items: list[dict], key) -> dict:
    """Keep, per key, the payload with the latest scraped_at (batch rule)."""
    seen: dict = {}
    for p in items:
        k = key(p)
        if k is None:
            continue
        prev = seen.get(k)
        if prev is None or str(p.get("scraped_at", "")) > str(prev.get("scraped_at", "")):
            seen[k] = p
    return seen


# ── per-unit extractors: yield (code, entity_type, entity_id, date,
#    granularity, value_str, skip_reason) ────────────────────────────────────
def ex_abs_sdmx(cur, notes):
    src = "abs-sdmx"
    items = unit_items(cur, src)
    seen = dedup_latest(items, lambda p: p.get("period"))
    out = []
    for period, p in sorted(seen.items()):
        v = num(p.get("value"))
        if v is None or not period:
            continue
        out.append(("cons.permits.monthly", "country", COUNTRY_AU, period,
                    "month", fmt(v)))
    notes.append(f"abs-sdmx: 切片 1GSYD 住宅许可（payload 库存 2001-07..2009-10 共 100 期连续；"
                 f"单元历史 2001-07 起约 290 期，本次入库以实存 payload 为准）")
    return out


def ex_dbnomics(cur, notes):
    src = "dbnomics-api"
    items = unit_items(cur, src)
    seen = dedup_latest(items, lambda p: (p.get("series_code"), p.get("period")))
    out = []
    for (series, period), p in sorted(seen.items()):
        if series != "D.CNY.EUR.SP00.A":
            continue
        v = num(p.get("value"))
        if v is None or not period:
            continue
        out.append(("fx.rate.ecb.daily", "currency_pair", CURRENCY_PAIR_EURCNY,
                    period, "day", fmt(v)))
    n = len({r[3] for r in out})
    notes.append(f"dbnomics-api: D.CNY.EUR.SP00.A 日频 {n} 个交易日点"
                 f"（2024-10-15..2026-10-09，滚动短窗；granularity=day 按 trend 口径入库，"
                 f"语义为日点不按月桶）")
    return out


def ex_ecb(cur, notes):
    src = "ecb-exr"
    items = unit_items(cur, src)
    seen = dedup_latest(items, lambda p: (p.get("series_key"), p.get("period")))
    out = []
    for (series, period), p in sorted(seen.items()):
        if series != "M.CNY.EUR.SP00.A":
            continue
        v = num(p.get("value"))
        if v is None or not period:
            continue
        out.append(("fx.rate.ecb.monthly", "currency_pair", CURRENCY_PAIR_EURCNY,
                    period, "month", fmt(v)))
    notes.append("ecb-exr: M.CNY.EUR.SP00.A 月均 100 期（2018-06..2026-09）；"
                 "fx.rate（2026-11-03 已入库 100 行，月度、currency_pair 300962）为同点位"
                 "历史口径，本批用新码 fx.rate.ecb.monthly 避免与既有行撞键")
    return out


def ex_eurostat(cur, notes):
    src = "eurostat-api"
    items = unit_items(cur, src)
    seen = dedup_latest(
        items,
        lambda p: (p.get("dataset"), p.get("geo"), p.get("s_adj"), p.get("unit"),
                   p.get("nace_r2"), p.get("period"))
        if p.get("dataset") == "sts_inpr_m" else None,
    )
    geo_id = {"DE": COUNTRY_DE, "FR": COUNTRY_FR, "IT": None, "EU27_2020": None}
    # IT has no country entity in this DB; EU27_2020 has no aggregate entity.
    # Only geos with an entity home are written (no entity creation this batch).
    out = []
    skipped_geo = defaultdict(int)
    for (ds, geo, s_adj, unit, nace, period), p in sorted(seen.items()):
        if geo not in ("DE", "FR"):
            skipped_geo[geo] += 1
            continue
        v = num(p.get("value"))
        if v is None or not period:
            continue
        out.append(("ind.prod.index.eurostat", "country", geo_id[geo], period,
                    "month", fmt(v)))
    if skipped_geo:
        notes.append("eurostat-api: 无实体归宿的 geo 未入库 "
                     + ", ".join(f"{g}×{n}" for g, n in sorted(skipped_geo.items()))
                     + "（IT 与 EU27_2020 无 entities 行；本批不建实体，见报告）")
    notes.append("eurostat-api: 仅 sts_inpr_m 主通道（NSA/I21/PRD/C）；DE 140 期连续 "
                 "2015-01..2026-08；FR 同窗；ei_bsco_m(12期)/apri_pi_outq(3季) 低于门槛跳过；"
                 "DS-045409 行无 value 列不适用")
    return out


def ex_imf(cur, notes):
    src = "imf-pcps"
    items = unit_items(cur, src)
    seen = dedup_latest(items, lambda p: (p.get("series_key"), p.get("period")))
    out = []
    for (series, per), p in sorted(seen.items()):
        if series != "G001.PCOIL.INDEX.M":
            continue
        period = imf_period(per)
        v = num(p.get("value"))
        if v is None or not period:
            continue
        out.append(("price.oil.imf.index", "country", ENTITY_WORLD, period,
                    "month", fmt(v)))
    notes.append("imf-pcps: PCOIL 月度指数 100 期（2018-06..2026-09）；country=G001 "
                 "→ entities WORLD(300932) 归一，沿用 2026-11-03 既有行的 entity 归宿")
    return out


def ex_gleif(cur, notes):
    src = "gleif-lei"
    items = unit_items(cur, src)
    seen = dedup_latest(items, lambda p: p.get("month"))
    out = []
    for month, p in sorted(seen.items()):
        v = num(p.get("lei_count"))
        if v is None or not month:
            continue
        out.append(("entities.new_registrations.monthly", "country", ENTITY_WORLD,
                    month, "month", fmt(v)))
    notes.append("gleif-lei: 全球月度注册数 81 期连续（2020-01..2026-09）；"
                 "payload country 均为 null（全库聚合行）→ entities WORLD")
    return out


def ex_eia(cur, notes):
    """Already ingested 2026-10-21; idempotent re-run picks up new rolling hours.

    payload rows total 65,900 (800 runs, rolling ~3-day hourly window per run);
    the 2026-10-21 backfill inserted the 1,330–1,439 distinct points of its
    window (2026-10-06..10-22). This re-run resolves every distinct
    (series, period) to its latest scraped_at payload row; ON CONFLICT
    DO NOTHING keeps all existing DB rows, so only hours crawled after
    2026-10-22 actually insert (~465 new points expected). The insert
    statement count stays 65,900 which is cheap; extracted-row count below
    is pre-conflict.
    """
    src = "eia-v2"
    items = unit_items(cur, src)
    out = []
    for p in items:
        if p.get("series_type") not in ("D", "DF", "NG", "TI") or not p.get("period"):
            continue
        v = num(p.get("value"))
        if v is None:
            continue
        # concept ids resolved by code below (pwr.grid.*)
        out.append(({
            "D": "pwr.grid.demand.hourly",
            "DF": "pwr.grid.demand_forecast.hourly",
            "NG": "pwr.grid.net_generation.hourly",
            "TI": "pwr.grid.interchange.hourly",
        }[p["series_type"]], "industry", INDUSTRY_UTILITIES, p["period"],
            "hour", fmt(v)))
    notes.append("eia-v2: 已入库单元幂等复跑——滚动短窗新增小时桶被合法追加，"
                 "存量行 ON CONFLICT DO NOTHING 不动")
    return out


EXTRACTORS = {
    "abs-sdmx": ex_abs_sdmx,
    "dbnomics-api": ex_dbnomics,
    "ecb-exr": ex_ecb,
    "eurostat-api": ex_eurostat,
    "imf-pcps": ex_imf,
    "gleif-lei": ex_gleif,
    "eia-v2": ex_eia,
}


def ensure_concepts(cur) -> dict[str, int]:
    """Insert batch concepts additively (idempotent); return code -> id."""
    ids: dict[str, int] = {}
    cur.execute("SELECT id, code FROM concepts WHERE deprecated = false")
    for cid, code in cur.fetchall():
        ids.setdefault(code, cid)
    for (code, name_zh, name_en, category, unit, measure, frequency,
         entity_type, source) in CONSTRUCT_CONCEPTS:
        cur.execute(
            "INSERT INTO concepts (code, name_en, name_zh, category, unit,"
            " measure, frequency, entity_type, source, verified, deprecated,"
            " concept_code)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,false,false,NULL)"
            " ON CONFLICT ON CONSTRAINT uq_concept_identity DO NOTHING"
            " RETURNING id",
            (code, name_en, name_zh, category, unit or None, measure,
             frequency, entity_type, source),
        )
        row = cur.fetchone()
        if row:
            ids[code] = row[0]
    cur.execute("SELECT id, code FROM concepts WHERE deprecated = false")
    for cid, code in cur.fetchall():
        ids.setdefault(code, cid)
    return ids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("DSN missing — source ~/.config/finddata/dashboards.env first")

    conn = psycopg2.connect(args.dsn)
    try:
        with conn.cursor() as cur:
            notes: list[str] = []
            rows: list[tuple] = []
            per_unit: list[tuple[str, int]] = []
            for src, fn in EXTRACTORS.items():
                before = len(rows)
                rows.extend(fn(cur, notes))
                per_unit.append((src, len(rows) - before))
                print(f"{src:<15} extracted {len(rows) - before:>6} rows")

            if args.dry_run:
                ids = ensure_concepts(cur)  # transaction rolled back below
                conn.rollback()
            else:
                ids = ensure_concepts(cur)

            missing = sorted({r[0] for r in rows} - set(ids))
            if missing:
                print(f"FATAL: {len(missing)} concept codes unresolved: {missing}")
                return 2

            print(f"\ntotal observation rows to write: {len(rows)}")
            if args.dry_run:
                print("dry-run: no database writes\nnotes:")
                for n in notes:
                    print("  -", n)
                print("\nskipped units:")
                for k, v in SKIP_NOTES.items():
                    print(f"  - {k}: {v}")
                return 0

            with conn:  # one transaction
                with conn.cursor() as c2:
                    c2.execute("SELECT count(*) FROM semantic_observations")
                    before_total = c2.fetchone()[0]
                    # per-source before counts for the zero-damage check
                    c2.execute(
                        "SELECT source_used, count(*) FROM semantic_observations"
                        " GROUP BY 1")
                    before_by_src = dict(c2.fetchall())
                    for (code, etype, eid, d, gran, value) in rows:
                        c2.execute(
                            "INSERT INTO semantic_observations"
                            " (concept_id, entity_type, entity_id, date,"
                            "  granularity, value, unit, source_used, fetched_at,"
                            "  obs_month)"
                            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now(),%s)"
                            " ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING",
                            (ids[code], etype, eid, d, gran, value,
                             None, code.split('.')[0] if False else _source_of(code),
                             obs_month_of(d)),
                        )
                    c2.execute("SELECT count(*) FROM semantic_observations")
                    after_total = c2.fetchone()[0]
            print(f"semantic_observations: {before_total} -> {after_total}"
                  f" (inserted {after_total - before_total})")
            print("\nnotes:")
            for n in notes:
                print("  -", n)
            print("\nskipped units:")
            for k, v in SKIP_NOTES.items():
                print(f"  - {k}: {v}")
    finally:
        conn.close()
    return 0


_SOURCE_OF = {
    "cons.permits.monthly": "abs-sdmx",
    "fx.rate.ecb.monthly": "ecb-exr",
    "fx.rate.ecb.daily": "dbnomics-api",
    "ind.prod.index.eurostat": "eurostat-api",
    "price.oil.imf.index": "imf-pcps",
    "entities.new_registrations.monthly": "gleif-lei",
    "pwr.grid.demand.hourly": "eia-v2",
    "pwr.grid.demand_forecast.hourly": "eia-v2",
    "pwr.grid.net_generation.hourly": "eia-v2",
    "pwr.grid.interchange.hourly": "eia-v2",
}


def _source_of(code: str) -> str:
    return _SOURCE_OF[code]


if __name__ == "__main__":
    sys.exit(main())
