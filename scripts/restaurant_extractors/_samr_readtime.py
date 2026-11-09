#!/usr/bin/env python3
"""Restaurant-vertical wave-1 — samr-food-inspection **读时统计**（不写观测）。

本模块**故意不以** ``SOURCE``/``CONCEPTS``/``extract`` 的形态出现，因此
``scripts/extract_restaurant_wave0.py`` 的自动装载会跳过它（下划线前缀 + 缺契约
三件）。原因是一条已裁定的契约边界（tasks.md 2.3）：

    samr 是**事件流**（一条公告 = 一个事件），不是上游发布的数值序列。
    「每月通告数」是抽取端自己算出来的聚合，上游从未发布该指标 →
    按 industry-observation-supply「观测=上游发布的点，抽取不派生」的纪律，
    **不得写进 semantic_observations**（写过一次，2026-11-07 已回滚 69 行 + 2 概念）。

消费方式（两条，均为读时）：

* 波3 板面「抽检统计」：``counts_by_month(cur)`` 现算现画，带 as_of 与口径注记；
* 波4 事件级预警：直接读 crawl_items 的 ``result_category`` /
  ``restaurant_related`` 事件行（见 ``events(cur)``），幂等由事件 url 保证。

口径见 ``fd-industry-data/docs/samr-restaurant-caliber.md``：计数口径 = **公告条数**
（不是批次内不合格样品数、不是抽检总批次），缺失月不补 0，`restaurant_related`
是标注列不是过滤条件。
"""
from __future__ import annotations

import re
from collections import Counter

from ._common import dedup_latest, unit_items

SOURCE = "samr-food-inspection"   # 仅用于读该单元的 crawl_items（不产生写入形态）
RESULT_CATEGORIES = ("unqualified", "qualified", "other")
_MONTH_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")


def events(cur) -> dict:
    """Deduped event rows of the unit: {url: payload} (latest scraped_at wins)."""
    return dedup_latest(unit_items(cur, SOURCE), lambda p: p.get("url"))


def month_of(payload) -> str | None:
    """notice_date[:7] when it is a real YYYY-MM; anything else is unusable."""
    d = payload.get("notice_date")
    if not isinstance(d, str) or len(d) < 7:
        return None
    month = d[:7]
    return month if _MONTH_RE.match(month) else None


def gap_months(months: list[str]) -> int:
    """Calendar months between min and max that the series does NOT contain."""
    if not months:
        return 0
    lo, hi = min(months), max(months)
    span = ((int(hi[:4]) - int(lo[:4])) * 12
            + (int(hi[5:7]) - int(lo[5:7])) + 1)
    return span - len(set(months))


def counts_by_month(cur) -> dict:
    """Read-time aggregates for the board / alerts (nothing is persisted).

    Returns::

        {
          "as_of": <max scraped_at seen>,
          "events": {"payloads": n, "deduped": n,
                     "result_category": {unqualified, qualified, other},
                     "restaurant_related_true": n},
          "months": [{"month": "YYYY-MM", "unqualified": k,
                      "restaurant_related": j}, ...],   # 只含真实出现过的月
          "window": {"unqualified": (lo, hi, n_months, gaps),
                     "restaurant_related": (lo, hi, n_months, gaps)},
          "notes": [...]
        }
    """
    items = unit_items(cur, SOURCE)
    notes: list[str] = []
    if not items:
        return {"as_of": None, "events": {}, "months": [], "window": {},
                "notes": [f"{SOURCE}: 中心库无 success payload（单元尚未跑通或库为空）"]}

    seen = events(cur)
    cats = Counter(p.get("result_category") for p in seen.values())
    unqualified = [p for p in seen.values()
                   if p.get("result_category") == "unqualified"]

    count_uq: Counter = Counter()
    count_rr: Counter = Counter()
    no_month = 0
    for payload in unqualified:
        month = month_of(payload)
        if month is None:
            no_month += 1
            continue
        count_uq[month] += 1
        if payload.get("restaurant_related") is True:
            count_rr[month] += 1

    as_of = max((str(p.get("scraped_at", "")) for p in seen.values()), default=None)
    months = [
        {"month": m, "unqualified": count_uq.get(m, 0),
         "restaurant_related": count_rr.get(m, 0)}
        for m in sorted(set(count_uq) | set(count_rr))
    ]

    def window(counter: Counter):
        ms = sorted(counter)
        if not ms:
            return None
        return (ms[0], ms[-1], len(ms), gap_months(ms))

    notes.append(
        f"{SOURCE}: payload {len(items)} 条 → 去重(按 url) {len(seen)} 条；"
        "result_category " + " / ".join(
            f"{c}={cats.get(c, 0)}" for c in RESULT_CATEGORIES)
        + f"；unqualified {len(unqualified)} 条，其中 restaurant_related=true "
        f"{sum(1 for p in unqualified if p.get('restaurant_related') is True)} 条"
        + (f"；{no_month} 条 unqualified 公告无可用 notice_date 未计数（不猜月）"
           if no_month else "")
    )
    unknown = {c: n for c, n in cats.items() if c not in RESULT_CATEGORIES}
    if unknown:
        notes.append(f"{SOURCE}: ⚠️ 出现未定义 result_category {unknown}"
                     "——spider 的 TITLE_RULES/BODY_RULES 需复核")
    for key, counter in (("unqualified", count_uq),
                         ("restaurant_related", count_rr)):
        w = window(counter)
        if w:
            notes.append(f"{SOURCE}: {key} 真实窗口 {w[0]}..{w[1]}，{w[2]} 个月，"
                         f"日历缺口 {w[3]} 个月（缺失月=未入库公告，非 0）")
        else:
            notes.append(f"{SOURCE}: {key} 无可用月度数据")

    return {
        "as_of": as_of,
        "events": {
            "payloads": len(items),
            "deduped": len(seen),
            "result_category": {c: cats.get(c, 0) for c in RESULT_CATEGORIES},
            "restaurant_related_true": sum(
                1 for p in seen.values() if p.get("restaurant_related") is True),
        },
        "months": months,
        "window": {"unqualified": window(count_uq),
                   "restaurant_related": window(count_rr)},
        "notes": notes,
    }
