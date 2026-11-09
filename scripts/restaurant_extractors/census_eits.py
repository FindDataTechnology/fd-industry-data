#!/usr/bin/env python3
"""Restaurant-vertical wave-0 extractor — census-eits (US MARTS 零售与餐饮服务总销售额).

口径提示（2026-11-07 命名门校准）：MARTS ``44X72`` 是「零售贸易 + 餐饮服务」总计，
**不是**单餐饮口径；餐饮单项 = MRTS 类别 ``722``，尚未落库（见波0挂账）。概念码
因此定为 ``retail.us.retail_food_services_total.monthly``，名字照实说全。

Unit: spiders/census-eits/ (US Census EITS, rolling NSA window since 2026-11-07).
Consumes only the food-services slice of the stored payloads:

    program == "mrts" and category_code == "44X72"
    and data_type_code == "SM" and seasonally_adj == "no"

i.e. the monthly not-seasonally-adjusted sales of US "retail trade & food
services" total (MARTS, millions of USD). Everything else the unit stores
(mrts E_*/MPC* codes, the whole mwts/42 wholesale block) stays un-extracted.

Discipline (scripts/restaurant_extractors/_common.py):

* dedup by period keeping the payload with the latest scraped_at — upstream
  annual benchmark revisions rewrite historical months (measured 2026-10:
  mwts 2020-01 revised between runs), so the freshest payload wins;
* never interpolate/zero-fill — only real stored payload periods are written;
* trend gate >= 24 periods: the rolling window starts at months_back=13
  (minus the ~3-month upstream NSA lag => ~10-13 periods on the first run),
  so the series is born a short_window — rows are still produced normally
  and the shortfall is declared in notes (actual period count written);
  the main agent registers the short_window series.
"""
from __future__ import annotations

from ._common import (COUNTRY_US, dedup_latest, fmt, num, unit_items)

SOURCE = "census-eits"

# (code, name_zh, name_en, category, unit, measure, frequency, entity_type, source)
CONCEPTS = [
    ("retail.us.retail_food_services_total.monthly",
     "美国零售与餐饮服务总销售额",
     "US retail trade & food services total sales (MARTS 44X72, NSA)",
     "零售与餐饮",
     "usd_million",
     "value",
     "monthly",
     "country",
     "census-eits"),
]

_MIN_PERIODS = 24  # trend gate (batch discipline); first runs declare short_window


def extract(cur, notes) -> list[tuple]:
    """Extract the 44X72 SM NSA monthly series into observation rows.

    Row = (code, entity_type, entity_id, date, granularity, value_str) with
    date = 'YYYY-MM' observation month, granularity = 'month'.
    """
    items = unit_items(cur, SOURCE)

    def payload_key(p):
        if (p.get("program") != "mrts"
                or p.get("category_code") != "44X72"
                or p.get("data_type_code") != "SM"
                or p.get("seasonally_adj") != "no"):
            return None
        period = p.get("time")
        if (not isinstance(period, str) or len(period) != 7
                or not period[:4].isdigit() or period[4] != "-"
                or not period[5:7].isdigit()):
            return None
        return period

    seen = dedup_latest(items, payload_key)
    dropped_value = 0
    rows: list[tuple] = []
    for period in sorted(seen):
        v = num(seen[period].get("cell_value"))
        if v is None:
            dropped_value += 1
            continue
        rows.append((CONCEPTS[0][0], "country", COUNTRY_US, period, "month",
                     fmt(v)))

    periods = len({r[3] for r in rows})
    if rows:
        lo, hi = min(seen), max(seen)
        note = (f"census-eits: mrts 44X72 SM NSA 滚动窗 {periods} 期"
                f"（{lo}..{hi}），dedup 后入库以实存 payload 为准")
        if dropped_value:
            note += f"；{dropped_value} 期 cell_value 不可解析未入库"
        if periods < _MIN_PERIODS:
            note += (f"；实际期数 {periods} < {_MIN_PERIODS} 期门槛 → short_window"
                     "（首跑待攒期，滚动窗按月累积，主代理注册 short_window）")
        notes.append(note)
    else:
        notes.append("census-eits: 中心库无 mrts 44X72 SM NSA 实存 payload"
                     "（单元尚无 success run 或窗口为空）——本批跳过，不硬抽")
    return rows
