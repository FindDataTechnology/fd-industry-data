#!/usr/bin/env python3
"""Restaurant-vertical wave-2 extractor — sec-edgar-facts (美股 12 家头条 KPI).

The unit stores two row classes (``kind``): XBRL ``fact`` rows and filing
``event`` rows. This extractor takes the **fact** face and writes quarterly
observations onto the existing company-financial concepts:

    Revenues / RevenueFromContractWithCustomerExcludingAssessedTax /
    SalesRevenueNet            -> financials.revenue
    OperatingIncomeLoss        -> financials.operating_profit
    NetIncomeLoss              -> financials.net_income
    RestaurantRevenue          -> NOT mapped (see notes; segment caliber, 阶段 3.5)

Entity is the company itself: ``entity_type='stock'``, ``entity_id`` = the
universe row id (docs/restaurant-universe.yaml, registered by
scripts/register_restaurant_universe.py). That is the same shape the A-share
``cnreport`` supply uses, so one concept query spans both markets.

Caliber decisions (each one is a place where a wrong default would fabricate data):

* **只取单季窗**：XBRL 同一 tag 对同一 ``end`` 会同时报 3 个月（单季）与
  fiscal-year-to-date（YTD，如 9 个月）两种 ``start``。若两者都写进同一
  (concept, entity, end) 自然键，YTD 值会把单季值挤掉或造成重复——因此只保留
  ``70 <= end-start <= 110`` 天的单季窗，YTD 行显式 SKIP 并计数。
  存量 ``financials.*``（cnreport）同口径（date=报告期末，granularity=quarter）。
* **重述取最新申报**：同一 (tag, start, end) 多条时 spider 已按 filed 取最新；
  抽取端再去重一次（(code, entity, end) 保留 filed 最新），防止 spider 改动放宽。
* **单位只收 USD**：``USD/shares`` / ``shares`` 等不进金额概念（否则量纲污染）。
* **不插值不补季**：公司未申报的季度缺席，不写 0。
* **分部字段（RevenueBySegment）**：companyfacts 的 XBRL 事实不带维度成员，
  本抽取器**不做任何分部推断**——分部口径属 3.5 细分面，须解析 10-Q 正文。
"""
from __future__ import annotations

from ._common import fmt, gate_periods, num, unit_items

SOURCE = "sec-edgar-facts"

#: No new concepts: financials.revenue / operating_profit / net_income already
#: exist (cnreport for A-shares, akshare for others). Reusing them is the point:
#: one concept, two markets, different source_used.
CONCEPTS: list = []

CODE_BY_TAG = {
    "Revenues": "financials.revenue",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "financials.revenue",
    "SalesRevenueNet": "financials.revenue",
    "OperatingIncomeLoss": "financials.operating_profit",
    "NetIncomeLoss": "financials.net_income",
}
UNMAPPED_TAGS = {"RestaurantRevenue",
                 # 含代收税口径；与 Excluding 不同源，不拼接（2026-11-08 增）
                 "RevenueFromContractWithCustomerIncludingAssessedTax"}
QUARTER_MIN_DAYS, QUARTER_MAX_DAYS = 70, 110


def _days(start: str | None, end: str | None) -> int | None:
    from datetime import date

    if not start or not end:
        return None
    try:
        return (date.fromisoformat(str(end)) - date.fromisoformat(str(start))).days
    except ValueError:
        return None


def _entity_ids(cur) -> dict:
    """ticker -> integer entity id for the 12-company US universe."""
    cur.execute(
        "SELECT code, id FROM entities WHERE entity_type='stock'"
        " AND code = ANY(%s)",
        (sorted({"MCD", "SBUX", "YUM", "CMG", "QSR", "DRI", "DPZ", "WEN",
                 "TXRH", "BROS", "WING", "EAT"}),),
    )
    return {code: eid for code, eid in cur.fetchall()}


def extract(cur, notes) -> list[tuple]:
    items = unit_items(cur, SOURCE)
    if not items:
        notes.append(f"{SOURCE}: 中心库无 success payload——本批跳过，不硬抽")
        return []

    ids = _entity_ids(cur)
    missing = sorted({"MCD", "SBUX", "YUM", "CMG", "QSR", "DRI", "DPZ", "WEN",
                      "TXRH", "BROS", "WING", "EAT"} - set(ids))
    if missing:
        notes.append(f"{SOURCE}: ⚠️ 宇宙实体缺 {missing}——这些公司的观测不写"
                     "（实体注册见 scripts/register_restaurant_universe.py）")

    # (concept_code, entity_id, end) -> (filed, value)  latest filing wins
    best: dict[tuple, tuple[str, float]] = {}
    ytd_skipped = 0
    non_usd = 0
    unmapped: set[str] = set()
    no_entity = 0

    for p in items:
        if p.get("kind") != "fact":
            continue
        tag = p.get("tag")
        code = CODE_BY_TAG.get(tag)
        if code is None:
            if tag in UNMAPPED_TAGS:
                unmapped.add(tag)
            continue
        if str(p.get("unit") or "").upper() != "USD":
            non_usd += 1
            continue
        span = _days(p.get("start"), p.get("end"))
        if span is None or not (QUARTER_MIN_DAYS <= span <= QUARTER_MAX_DAYS):
            ytd_skipped += 1
            continue
        eid = ids.get(p.get("ticker"))
        if eid is None:
            no_entity += 1
            continue
        value = num(p.get("value"))
        if value is None:
            continue
        key = (code, eid, str(p["end"]))
        filed = str(p.get("filed") or "")
        prev = best.get(key)
        if prev is None or filed > prev[0]:
            best[key] = (filed, value)

    rows = [(code, "stock", eid, end, "quarter", fmt(value))
            for (code, eid, end), (_, value) in sorted(best.items())]

    if unmapped:
        notes.append(f"{SOURCE}: payload-only（未映射概念）{sorted(unmapped)}"
                     "——分部/餐厅收入属 3.5 细分面，不做推断")
    if ytd_skipped:
        notes.append(f"{SOURCE}: 剔除 {ytd_skipped} 条非单季窗事实（YTD 累计口径，"
                     "与单季混写会造成同键冲突与量级误读）")
    if non_usd:
        notes.append(f"{SOURCE}: 剔除 {non_usd} 条非 USD 单位事实（量纲保护）")
    if no_entity:
        notes.append(f"{SOURCE}: {no_entity} 条事实的公司实体未注册，未写")

    per = {}
    for (code, eid, end) in best:
        per.setdefault(code, []).append(end)
    for code, ends in sorted(per.items()):
        ends.sort()
        notes.append(f"{SOURCE}: {code} {ends[0]}..{ends[-1]}，{len(ends)} 个季度"
                     f"（{len({e for _, e, _ in [(c, i, d) for (c, i, d) in best if c == code]})} 个期末）")

    kept, dropped = gate_periods(rows, min_periods=24)
    if dropped:
        from collections import Counter

        tick = {v: k for k, v in ids.items()}
        short = Counter((r[0], tick.get(r[2], str(r[2]))) for r in dropped)
        notes.append(
            f"{SOURCE}: 趋势门剔除 {len(dropped)} 行（<24 期，逐公司计）——"
            + "; ".join(f"{code}@{t}={n} 期" for (code, t), n in sorted(short.items()))
            + "；这些公司上市/申报历史不足 6 年，等季度累积或按 short_window 另行裁定，"
            "不插值不补季"
        )
    return kept
