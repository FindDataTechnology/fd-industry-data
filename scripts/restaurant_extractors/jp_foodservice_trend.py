#!/usr/bin/env python3
"""Restaurant-vertical wave 1 extractor — jp-foodservice-trend (JF 外食 survey).

Reads the central crawl DB payloads of successful ``jp-foodservice-trend`` runs
(month × segment rows, four 前年同月比 columns) and writes four observation
series per segment.

Caliber (this is the part that is easy to get wrong):

* every value is a **ratio to the same month of the previous year**
  (前年同月比), 1.0 = flat — NOT a percent change and NOT an index level.
  Unit is written as ``ratio_yoy`` so a reader cannot mistake 1.0765 for 7.65.
* 全店 basis (all stores, including new openings), per the survey's 全店 sheet.
  This is why the JF series is not comparable with a same-store-sales figure.
* segment IS the entity: the observation natural key has no segment column, so
  the seven upstream rows (全体 + six formats) map to the seven
  ``jp.foodservice.*`` industry entities registered by
  scripts/register_jp_foodservice_segments.py. Without that mapping six of
  seven rows per month would silently collide on the natural key.
* dedup by (month, segment) keeping the latest scraped_at — an overlapping
  rolling window may restate the newest months; the payload's ``edition`` is
  kept only as provenance in the note, never as the date.
"""
from __future__ import annotations

from ._common import dedup_latest, fmt, gate_periods, num, unit_items

SOURCE = "jp-foodservice-trend"

# payload field -> concept code
FIELDS = {
    "sales_yoy": "restaurant.foodservice_jp.sales_yoy",
    "store_count_yoy": "restaurant.foodservice_jp.store_count_yoy",
    "customer_count_yoy": "restaurant.foodservice_jp.customer_count_yoy",
    "spend_per_customer_yoy": "restaurant.foodservice_jp.spend_per_customer_yoy",
}

CONCEPTS = [
    ("restaurant.foodservice_jp.sales_yoy",
     "日本外食 营业额 前年同月比", "Japan foodservice sales, YoY ratio (all stores)",
     "行业景气", "ratio_yoy", "index", "monthly", "industry", SOURCE),
    ("restaurant.foodservice_jp.store_count_yoy",
     "日本外食 店数 前年同月比", "Japan foodservice store count, YoY ratio (all stores)",
     "行业景气", "ratio_yoy", "index", "monthly", "industry", SOURCE),
    ("restaurant.foodservice_jp.customer_count_yoy",
     "日本外食 客流 前年同月比", "Japan foodservice customer count, YoY ratio (all stores)",
     "行业景气", "ratio_yoy", "index", "monthly", "industry", SOURCE),
    ("restaurant.foodservice_jp.spend_per_customer_yoy",
     "日本外食 客单价 前年同月比", "Japan foodservice spend per customer, YoY ratio (all stores)",
     "行业景气", "ratio_yoy", "index", "monthly", "industry", SOURCE),
]

# upstream 全店 segment label -> entities.code (registered out of band)
SEGMENT_CODE = {
    "全体": "jp.foodservice.all",
    "ファーストフード": "jp.foodservice.fast_food",
    "ファミリーレストラン": "jp.foodservice.family_restaurant",
    "パブレストラン/居酒屋": "jp.foodservice.pub_restaurant_izakaya",
    "ディナーレストラン": "jp.foodservice.dinner_restaurant",
    "喫茶": "jp.foodservice.coffee_shop",
    "その他": "jp.foodservice.other",
}

_MIN_PERIODS = 12      # 12-23 = short_window 档；<12 整列剔除
_FULL_WINDOW = 24


def _segment_ids(cur, notes) -> dict:
    cur.execute(
        "SELECT code, id FROM entities WHERE entity_type = 'industry'"
        " AND code = ANY(%s)", (sorted(set(SEGMENT_CODE.values())),))
    found = dict(cur.fetchall())
    missing = sorted(set(SEGMENT_CODE.values()) - set(found))
    if missing:
        notes.append(
            "SKIP write: 以下业态实体未登记（抽取器不建实体，先跑 "
            f"scripts/register_jp_foodservice_segments.py --apply）: {missing}")
    return {label: found[code] for label, code in SEGMENT_CODE.items() if code in found}


def extract(cur, notes) -> list[tuple]:
    items = unit_items(cur, SOURCE)
    seen = dedup_latest(items, lambda p: (p.get("month"), p.get("segment")))
    if not seen:
        notes.append(f"{SOURCE}: 无成功载荷，本次不写观测")
        return []

    eids = _segment_ids(cur, notes)
    if not eids:
        return []

    out: list[tuple] = []
    months, segs, skipped_seg, skipped_val = set(), set(), 0, 0
    for (month, segment), payload in sorted(seen.items()):
        if not month or len(month) != 7 or segment not in SEGMENT_CODE:
            skipped_seg += 1
            continue
        eid = eids.get(segment)
        if eid is None:
            skipped_seg += 1
            continue
        months.add(month)
        segs.add(segment)
        for field, code in FIELDS.items():
            v = num(payload.get(field))
            if v is None:
                skipped_val += 1
                continue
            out.append((code, "industry", eid, month, "month", fmt(v)))

    if skipped_seg or skipped_val:
        notes.append(f"{SOURCE}: 跳过 {skipped_seg} 行未识别业态 / {skipped_val} 个空值")

    kept, dropped = gate_periods(out, min_periods=_MIN_PERIODS)
    window = f"{min(months)}..{max(months)}" if months else "空"
    if dropped:
        notes.append(
            f"SKIP {SOURCE}: {len(dropped)} 行低于 12 期门槛（窗口 {window}，"
            f"{len(months)} 期）——不足 12 期的序列不充趋势")
    band = ("常规（>=24 期）" if len(months) >= _FULL_WINDOW
            else "short_window（12-23 期，registry 须带声明）")
    notes.append(
        f"{SOURCE}: 放行 {len(kept)} 行（{window}，{len(months)} 期 × {len(segs)} 业态；"
        f"unit=ratio_yoy（1.0=持平，非百分比），全店基准；{band}）")
    return kept
