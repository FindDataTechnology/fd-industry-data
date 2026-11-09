#!/usr/bin/env python3
"""Restaurant-vertical wave-1 extractor — restaurant-rpi (NRA Restaurant Performance Index).

Reads the central crawl DB payloads of successful ``restaurant-rpi`` runs (one
row per observation month, WIDE: 11 index columns) and melts them into eleven
long-form monthly observation series:

    rpi                      -> rpi.us.restaurant.monthly            (headline)
    current_situation_index  -> rpi.us.current_situation.monthly
    expectations_index       -> rpi.us.expectations.monthly
    cs_same_store_sales      -> rpi.us.cs_same_store_sales.monthly
    cs_customer_traffic      -> rpi.us.cs_customer_traffic.monthly
    cs_labor                 -> rpi.us.cs_labor.monthly
    cs_capital_expenditures  -> rpi.us.cs_capital_expenditures.monthly
    exp_same_store_sales     -> rpi.us.exp_same_store_sales.monthly
    exp_staffing             -> rpi.us.exp_staffing.monthly
    exp_capital_expenditures -> rpi.us.exp_capital_expenditures.monthly
    exp_business_conditions  -> rpi.us.exp_business_conditions.monthly

Caliber:

* unit = ``index``; the upstream PDF states 100 = neutral (above 100 expansion,
  below 100 contraction) — a composite index, not a percentage, not a currency
  amount, and **not** the legacy 1995=100 base;
* entity = United States (COUNTRY_US), entity_type 'country' — the NRA RPI is a
  national US restaurant-industry index;
* granularity 'month', date = the payload's ``month`` (YYYY-MM observation
  month). ``report_month`` is the report edition that was crawled (provenance),
  never the observation date;
* one upstream PDF carries a **5-month rolling window**; the unit's
  ``months_back`` bounded backfill (≤18 editions, 404-tolerant) is what makes a
  multi-month window available at all. History still accumulates across crawls,
  and nothing is interpolated or zero-filled;
* dedup by observation month keeping the latest scraped_at (a later edition may
  restate the newest months).

Trend gate (scripts/restaurant_extractors/_common.py + short_window band):

* >= 24 stored months → normal series;
* 12-23 → written, and the concepts carry an explicit ``short_window``
  declaration in the registry provenance, never silently passed off as a long series;
* < 12 → the whole series is dropped with an explicit SKIP note carrying the
  real period count (a 5-point rolling table is not a trend).

The period count is measured from the DB at runtime, so the same code path
starts emitting rows the moment enough editions have accumulated.
"""
from __future__ import annotations

from ._common import COUNTRY_US, dedup_latest, fmt, gate_periods, num, unit_items

SOURCE = "restaurant-rpi"

# (code, name_zh, name_en, category, unit, measure, frequency, entity_type, source)
CONCEPTS = [
    ("rpi.us.restaurant.monthly",
     "美国餐馆业绩指数（RPI）",
     "US Restaurant Performance Index (headline)",
     "行业景气", "index", "value", "monthly", "country", SOURCE),
    ("rpi.us.current_situation.monthly",
     "RPI 现状指数",
     "RPI Current Situation Index",
     "行业景气", "index", "value", "monthly", "country", SOURCE),
    ("rpi.us.expectations.monthly",
     "RPI 预期指数",
     "RPI Expectations Index",
     "行业景气", "index", "value", "monthly", "country", SOURCE),
    ("rpi.us.cs_same_store_sales.monthly",
     "RPI 现状-同店销售",
     "RPI Current Situation: same-store sales",
     "行业景气", "index", "value", "monthly", "country", SOURCE),
    ("rpi.us.cs_customer_traffic.monthly",
     "RPI 现状-客流",
     "RPI Current Situation: customer traffic",
     "行业景气", "index", "value", "monthly", "country", SOURCE),
    ("rpi.us.cs_labor.monthly",
     "RPI 现状-用工",
     "RPI Current Situation: labor",
     "行业景气", "index", "value", "monthly", "country", SOURCE),
    ("rpi.us.cs_capital_expenditures.monthly",
     "RPI 现状-资本支出",
     "RPI Current Situation: capital expenditures",
     "行业景气", "index", "value", "monthly", "country", SOURCE),
    ("rpi.us.exp_same_store_sales.monthly",
     "RPI 预期-同店销售",
     "RPI Expectations: same-store sales",
     "行业景气", "index", "value", "monthly", "country", SOURCE),
    ("rpi.us.exp_staffing.monthly",
     "RPI 预期-用工",
     "RPI Expectations: staffing",
     "行业景气", "index", "value", "monthly", "country", SOURCE),
    ("rpi.us.exp_capital_expenditures.monthly",
     "RPI 预期-资本支出",
     "RPI Expectations: capital expenditures",
     "行业景气", "index", "value", "monthly", "country", SOURCE),
    ("rpi.us.exp_business_conditions.monthly",
     "RPI 预期-经营状况",
     "RPI Expectations: business conditions",
     "行业景气", "index", "value", "monthly", "country", SOURCE),
]

FIELD_CONCEPT = {
    "rpi": "rpi.us.restaurant.monthly",
    "current_situation_index": "rpi.us.current_situation.monthly",
    "expectations_index": "rpi.us.expectations.monthly",
    "cs_same_store_sales": "rpi.us.cs_same_store_sales.monthly",
    "cs_customer_traffic": "rpi.us.cs_customer_traffic.monthly",
    "cs_labor": "rpi.us.cs_labor.monthly",
    "cs_capital_expenditures": "rpi.us.cs_capital_expenditures.monthly",
    "exp_same_store_sales": "rpi.us.exp_same_store_sales.monthly",
    "exp_staffing": "rpi.us.exp_staffing.monthly",
    "exp_capital_expenditures": "rpi.us.exp_capital_expenditures.monthly",
    "exp_business_conditions": "rpi.us.exp_business_conditions.monthly",
}

_MIN_PERIODS = 12      # 12-23 = short_window 档（须在 registry 带声明）；<12 整列剔除
_FULL_WINDOW = 24      # 达到 24 期即为常规长序列


def extract(cur, notes) -> list[tuple]:
    """Emit (code, entity_type, entity_id, date, granularity, value_str) rows."""
    items = unit_items(cur, SOURCE)
    seen = dedup_latest(items, lambda p: p.get("month"))

    out: list[tuple] = []
    months: list[str] = []
    for month, payload in sorted(seen.items()):
        if not month or len(month) != 7:
            continue
        months.append(month)
        for field, concept in FIELD_CONCEPT.items():
            value = num(payload.get(field))
            if value is None:
                continue
            out.append((concept, "country", COUNTRY_US, month, "month", fmt(value)))

    kept, dropped = gate_periods(out, min_periods=_MIN_PERIODS)
    window = f"{min(months)}..{max(months)}" if months else "空"
    if dropped:
        notes.append(
            f"SKIP {SOURCE}: {len(FIELD_CONCEPT)} 条序列暂不入库——库内只有 "
            f"{len(months)} 个观测月（{window}），低于 12 期门槛。"
            "单版报告仅 5 个月滚动窗，靠月度累积 + months_back 有界回补攒期；"
            "绝不拿 5 个点充趋势"
        )
    else:
        band = ("常规（>=24 期）" if len(months) >= _FULL_WINDOW
                else "short_window（12-23 期，registry 须带声明）")
        notes.append(
            f"{SOURCE}: {len(FIELD_CONCEPT)} 条序列放行 {len(kept)} 行"
            f"（{window}，{len(months)} 期；unit=index（100=中性线），US(2)；{band}）"
        )
    return kept
