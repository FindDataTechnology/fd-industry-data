#!/usr/bin/env python3
"""nbs-stats extractor — 社零餐饮通道（restaurant vertical wave 0）.

Reads successful crawl payloads of source 'nbs-stats' from the central crawl
DB and converts the dg-channel long-format rows (payload carries a `series`
field — the discriminator vs the unit's older article/easyquery payloads,
which carry `indicator`/`category` instead) into semantic_observations rows:

    retail.cn.catering.monthly          ← series catering_monthly
    retail.cn.catering.yoy              ← series catering_yoy
    retail.cn.catering.limitup.monthly  ← series catering_limitup_monthly
    retail.cn.catering.limitup.yoy      ← series catering_limitup_yoy
    retail.cn.social.monthly            ← series social_monthly
    retail.cn.goods.monthly             ← series goods_monthly

social_yoy is crawled too (总额当月同比) but has no concept bound this wave —
payload kept in the crawl store, extraction notes record the skip.

Write discipline is _common.py's (extract_batch1/2 contract): dedup_latest on
(series, period), value must pass num(), trend gate >= 24 periods per series
(dropped series reported in notes with their real period counts), additive
INSERT ... ON CONFLICT DO NOTHING.

Module contract (see _common.py): SOURCE / CONCEPTS / extract(cur, notes).
"""

from __future__ import annotations

import re

from . import _common as C

SOURCE = "nbs-stats"

CATEGORY = "零售与餐饮"
_ENTITY = ("country", C.COUNTRY_CN)

# (code, name_zh, name_en, category, unit, measure, frequency, entity_type, source)
CONCEPTS = [
    ("retail.cn.catering.monthly", "餐饮收入当月值",
     "China catering revenue (monthly)", CATEGORY, "cny_100m", "value",
     "month", "country", SOURCE),
    ("retail.cn.catering.yoy", "餐饮收入当月同比",
     "China catering revenue YoY", CATEGORY, "%", "value",
     "month", "country", SOURCE),
    ("retail.cn.catering.limitup.monthly", "限上单位餐饮收入当月值",
     "China catering revenue above designated size (monthly)", CATEGORY,
     "cny_100m", "value", "month", "country", SOURCE),
    ("retail.cn.catering.limitup.yoy", "限上单位餐饮收入当月同比",
     "China catering revenue above designated size YoY", CATEGORY, "%",
     "value", "month", "country", SOURCE),
    ("retail.cn.social.monthly", "社会消费品零售总额当月值",
     "China total retail sales of consumer goods (monthly)", CATEGORY,
     "cny_100m", "value", "month", "country", SOURCE),
    ("retail.cn.goods.monthly", "商品零售当月值",
     "China retail sales of goods (monthly)", CATEGORY, "cny_100m", "value",
     "month", "country", SOURCE),
]

SERIES_TO_CONCEPT = {
    "catering_monthly": "retail.cn.catering.monthly",
    "catering_yoy": "retail.cn.catering.yoy",
    "catering_limitup_monthly": "retail.cn.catering.limitup.monthly",
    "catering_limitup_yoy": "retail.cn.catering.limitup.yoy",
    "social_monthly": "retail.cn.social.monthly",
    "goods_monthly": "retail.cn.goods.monthly",
}

# crawled but unbound this wave (documented, not silently dropped)
UNBOUND_SERIES = ("social_yoy",)

_PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")


def extract(cur, notes: list) -> list:
    """dg 通道 payload → observation 行（(code, 'country', 1, 'YYYY-MM',
    'month', value_str) 元组列表），过 ≥24 期趋势门槛后返回。"""
    items = C.unit_items(cur, SOURCE)
    # 只取 dg 新通道 payload（有 series 字段）；旧文章稿/宏观 payload 无此字段
    dg = [p for p in items if isinstance(p, dict) and p.get("series")]
    seen = C.dedup_latest(dg, lambda p: (p.get("series"), p.get("period")))

    rows = []
    bad_value: dict = {}
    unbound_seen: set = set()
    for (series, period), p in sorted(seen.items()):
        code = SERIES_TO_CONCEPT.get(series)
        if code is None:
            unbound_seen.add(series)
            continue
        if not period or not _PERIOD_RE.match(str(period)):
            bad_value[series] = bad_value.get(series, 0) + 1
            continue
        v = C.num(p.get("value"))
        if v is None:
            bad_value[series] = bad_value.get(series, 0) + 1
            continue
        rows.append((code, "country", C.COUNTRY_CN, str(period), "month", C.fmt(v)))

    kept, dropped = C.gate_periods(rows)

    # notes：真实窗口与门槛裁决（绝不静默）
    windows: dict[str, list] = {}
    for r in kept:
        windows.setdefault(r[0], []).append(r[3])
    for code in sorted(windows):
        ds = sorted(windows[code])
        notes.append(
            f"{SOURCE}: {code} {len(ds)} 期（{ds[0]}..{ds[-1]}）"
        )
    for code, pts in sorted(_series_points(dropped)):
        notes.append(
            f"{SOURCE}: SKIP {code} 低于 ≥24 期门槛，实存 {pts} 期，整系剔除"
        )
    if unbound_seen:
        notes.append(
            f"{SOURCE}: 随抓未绑概念系列 {sorted(unbound_seen)}（本波无概念码，"
            f"payload 留 crawl 库）"
        )
    if bad_value:
        notes.append(f"{SOURCE}: 非 YYYY-MM 期口径或空值行剔除 {bad_value}")
    notes.append(
        f"{SOURCE}: dg 通道 payload 上游口径为每年 1/2 月无当月值（社零 1-2 月"
        f"不单独发布），窗口内为 3-12 月连续月；rows 总数 {len(rows)}，"
        f"过门槛 {len(kept)}，剔系 {len(dropped)}"
    )
    return kept


def _series_points(dropped: list):
    """dropped rows → [(code, 期数)]（每系一条）。"""
    counts: dict[str, int] = {}
    for r in dropped:
        counts[r[0]] = counts.get(r[0], 0) + 1
    return sorted(counts.items())
