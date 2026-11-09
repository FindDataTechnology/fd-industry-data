#!/usr/bin/env python3
"""Restaurant-vertical wave-0 extractor — usda-quickstats unit (cattle + beef cold storage).

Reads the central crawl DB payloads of successful ``usda-quickstats`` runs and
emits two semantic observation series (wave-0 spec, entity COUNTRY_US=2,
category 农业, source usda-quickstats):

    CATTLE, INCL CALVES - INVENTORY  (SURVEY / FIRST OF JAN)
        -> agri.us.cattle.inventory.annual   head, granularity 'year'
           date = the year string; obs_month buckets to YYYY-12 upstream.
           Note: there is no ``CATTLE - INVENTORY`` short_desc upstream, and
           ``commodity_desc=CATTLE, INCL CALVES`` is a bad-request filter —
           the payloads come from the proven ``commodity_desc=CATTLE`` face,
           so the exact short_desc match below is the series selector.
    BEEF, COLD STORAGE, FROZEN - STOCKS, MEASURED IN LB
        -> agri.us.beef.coldstorage.monthly  lb, granularity 'month'
           month-end stocks; the observation month is the payload's real
           ``reference_period`` ("END OF JAN" ...) plus ``year`` — the
           payload carries no other month field (no weekend_date), so this
           is the only month source and rows without a parsable month are
           skipped, never fabricated.

Scope discipline (exact short_desc filtering):

* The BONE-IN / BONELESS cold-storage split rows are a different short_desc
  and are excluded — counted and reported in notes, never merged into the
  total series.
* Cattle payloads under the same short_desc also carry ``END OF DEC``
  (CENSUS vintage, several rows/year, class breakdown not in the payload)
  and ``FIRST OF JUL`` (mid-year survey) rows; only ``FIRST OF JAN`` +
  ``SURVEY`` + NATIONAL + HEAD is the January-1 herd series. The others are
  excluded with a note, never merged.
* Every value written is a real stored payload value; missing ``(D)/(Z)/(S)/
  (NA)`` markers arrive as NULL ``value`` from the spider and are skipped
  (not zero-filled).
* dedup by (short_desc, date) keeping the latest scraped_at; trend gate
  >= 24 periods per series; below-gate series are dropped with an explicit
  SKIP note.
"""
from __future__ import annotations

from ._common import (
    COUNTRY_US,
    dedup_latest,
    fmt,
    gate_periods,
    num,
    unit_items,
)

SOURCE = "usda-quickstats"

# (code, name_zh, name_en, category, unit, measure, frequency, entity_type, source)
CONCEPTS = [
    ("agri.us.cattle.inventory.annual",
     "美国牛存栏数",
     "US cattle inventory (Jan 1, all cattle incl calves)",
     "农业", "head", "value", "yearly", "country", SOURCE),
    ("agri.us.beef.coldstorage.monthly",
     "美国牛肉冷库冻存库存",
     "US frozen beef cold storage stocks (month-end)",
     "农业", "lb", "value", "monthly", "country", SOURCE),
]

CATTLE_SHORT_DESC = "CATTLE, INCL CALVES - INVENTORY"
BEEF_SHORT_DESC = "BEEF, COLD STORAGE, FROZEN - STOCKS, MEASURED IN LB"
BEEF_SPLIT_PREFIX = "BEEF, BONE-"

MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1)}


def _month_of(reference_period: str | None, year) -> str | None:
    """'END OF SEP' + 2026 -> '2026-09'（仅 END OF <MON> 形态；无月不造月）。"""
    if not reference_period or year is None:
        return None
    parts = str(reference_period).strip().upper().split()
    if len(parts) != 2 or parts[0] != "END" or parts[1] not in MONTHS:
        return None
    return f"{int(year):04d}-{MONTHS[parts[1]]:02d}"


def extract(cur, notes) -> list[tuple]:
    """Emit (code, entity_type, entity_id, date, granularity, value_str) rows."""
    items = unit_items(cur, SOURCE)

    # 先按 (short_desc, date) 收集候选 payload，再以最新 scraped_at 去重
    candidates: dict[tuple, dict] = {}
    skipped_split = 0            # BONE-IN / BONELESS 拆分行（按 short_desc 精确过滤掉）
    cattle_other_period: dict[str, int] = {}   # 同 short_desc 下非 FIRST OF JAN 行
    skipped_no_month = 0
    skipped_no_value = 0

    for payload in items:
        short_desc = payload.get("short_desc")
        if short_desc == BEEF_SHORT_DESC:
            if payload.get("agg_level") != "NATIONAL" or payload.get("unit") != "LB":
                continue
            date_s = _month_of(payload.get("reference_period"), payload.get("year"))
            if date_s is None:
                skipped_no_month += 1
                continue
            if num(payload.get("value")) is None:
                skipped_no_value += 1
                continue
            candidates[("beef", date_s)] = payload
        elif isinstance(short_desc, str) and short_desc.startswith(BEEF_SPLIT_PREFIX):
            skipped_split += 1
        elif short_desc == CATTLE_SHORT_DESC:
            if payload.get("agg_level") != "NATIONAL" or payload.get("unit") != "HEAD":
                continue
            if payload.get("reference_period") != "FIRST OF JAN":
                period = str(payload.get("reference_period") or "?")
                cattle_other_period[period] = cattle_other_period.get(period, 0) + 1
                continue
            if payload.get("source_desc") != "SURVEY":
                cattle_other_period["FIRST OF JAN/NON-SURVEY"] = (
                    cattle_other_period.get("FIRST OF JAN/NON-SURVEY", 0) + 1)
                continue
            year = payload.get("year")
            if year is None:
                skipped_no_month += 1
                continue
            date_s = f"{int(year):04d}"
            if num(payload.get("value")) is None:
                skipped_no_value += 1
                continue
            candidates[("cattle", date_s)] = payload

    out: list[tuple] = []
    windows: dict[str, list[str]] = {}
    for (series, date_s), payload in sorted(candidates.items()):
        if series == "beef":
            concept = "agri.us.beef.coldstorage.monthly"
            granularity = "month"
        else:
            concept = "agri.us.cattle.inventory.annual"
            granularity = "year"
        value = num(payload.get("value"))
        out.append((concept, "country", COUNTRY_US, date_s, granularity, fmt(value)))
        windows.setdefault(concept, []).append(date_s)

    kept, dropped = gate_periods(out)
    for concept, periods in sorted(windows.items()):
        kept_n = sum(1 for r in kept if r[0] == concept)
        notes.append(
            f"{SOURCE}: {concept} {kept_n} 期"
            f"（{min(periods)}..{max(periods)}；country US(2)，"
            "NATIONAL 口径，(short_desc,date) 去重取最新 scraped_at）"
        )
    dropped_by_concept: dict[str, int] = {}
    for (code, _etype, _eid, _d, _g, _v) in dropped:
        dropped_by_concept[code] = dropped_by_concept.get(code, 0) + 1
    for code, n in sorted(dropped_by_concept.items()):
        notes.append(f"SKIP {SOURCE}: {code} 仅 {n} 期（<24 期门槛），整列剔除不入库")
    if skipped_split:
        notes.append(
            f"SKIP {SOURCE}: BEEF, BONE-IN/BONELESS, COLD STORAGE, FROZEN - STOCKS "
            f"拆分行 {skipped_split} 条按 short_desc 精确过滤，未并入总量序列")
    if cattle_other_period:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(cattle_other_period.items()))
        notes.append(
            f"SKIP {SOURCE}: {CATTLE_SHORT_DESC} 非 1 月 1 日 SURVEY 口径行 "
            f"{sum(cattle_other_period.values())} 条（{detail}）未入年度序列"
            "——END OF DEC 为 CENSUS 版本同 short_desc 多行、分类维度未落 payload，"
            "FIRST OF JUL 为年中调查，均不与 Jan-1 序列混并")
    if skipped_no_month:
        notes.append(
            f"SKIP {SOURCE}: {skipped_no_month} 行缺可解析月份（reference_period 非 "
            "END OF <MON> / 缺 year），按不造月纪律剔除")
    if skipped_no_value:
        notes.append(
            f"SKIP {SOURCE}: {skipped_no_value} 行 value 为 (D)/(Z)/(S)/(NA) 等屏蔽符，"
            "跳过不补零")
    return kept
