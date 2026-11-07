#!/usr/bin/env python3
"""dounan-flower-price — flower-vertical extractor (semantic_observations feed).

Reads the central crawl payloads written by ``spiders/dounan-flower-price``
(run through ``fd-runner``: real crawl_runs + crawl_items rows, same as a
dispatcher pod) and emits one daily average-price row per flower variety.

Mapping discipline (docs/flower-series-curation.md §1):

* **concept = caliber, entity = variety.** One concept per measure
  (``price.flower.dounan.avg`` / ``.high`` / ``.low``) and 23 commodity
  entities — never the other way round: putting 元/枝 与 元/把 varieties under
  different codes would fork the caliber, and putting varieties into the
  concept would collide on ``uq_sem_obs`` for the same date.
* the item -> entity-code table is **explicit** (no string-similarity
  guessing): upstream ships both ``复色玫瑰（单头）`` and ``复色,玫瑰（多头）``.
* ``计量单位`` rides in the entity metadata (``unit``), because a single
  concept cannot carry two dimensions; the board never aggregates across
  entities.
* never interpolate / zero-fill: a variety absent from a day's report simply
  has no row, and a missing high/low/avg stays missing (all-None rows are
  dropped by the spider already).
* dedup key (variety, price_date), keep the payload with the latest
  ``scraped_at``; the upstream window rolls (~2 months) so re-crawls overlap.
* additive only: INSERT ... ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING.
"""
from __future__ import annotations

from restaurant_extractors._common import (  # 同一份写入纪律，勿另抄一份
    dedup_latest, fmt, num, unit_items,
)

SOURCE = "dounan-flower-price"

#: 上游 `item` 原文 → 实体码（显式表，禁止按相似度猜）。
#: 前 12 个上板（docs/flower-series-curation.md §1），其余注册但不上板。
ITEM_ENTITY = {
    "红玫瑰（单头）": "dounan-red-rose-single",
    "红玫瑰（多头）": "dounan-red-rose-multi",
    "白玫瑰（单头）": "dounan-white-rose-single",
    "黄玫瑰（单头）": "dounan-yellow-rose-single",
    "粉玫瑰（单头）": "dounan-pink-rose-single",
    "复色玫瑰（单头）": "dounan-multi-color-rose-single",
    "白百合": "dounan-white-lily",
    "粉百合": "dounan-pink-lily",
    "红百合": "dounan-red-lily",
    "康乃馨": "dounan-carnation",
    "菊花": "dounan-chrysanthemum",
    "洋桔梗": "dounan-eustoma",
    "绣球": "dounan-hydrangea",
    "金鱼草": "dounan-snapdragon",
    "向日葵": "dounan-sunflower",
    "勿忘我": "dounan-forget-me-not",
    "茉莉花": "dounan-jasmine",
    "马蹄莲": "dounan-calla-lily",
    "尤加利叶": "dounan-eucalyptus",
    "紫罗兰": "dounan-stock-flower",
    "白玫瑰（多头）": "dounan-white-rose-multi",
    "黄玫瑰（多头）": "dounan-yellow-rose-multi",
    "复色,玫瑰（多头）": "dounan-multi-color-rose-multi",  # 上游原文含半角逗号
}

#: 上游 payload 字段 → (concept code, 中文名)。键必须是**载荷里的真实字段名**
#: （price_avg / price_high / price_low），不是口径简称——第一版写成 avg/high/low
#: 时 p.get("avg") 恒为 None，静默产出 0 行。
MEASURES = {
    "price_avg": ("price.flower.dounan.avg", "斗南花卉日价·均价"),
    "price_high": ("price.flower.dounan.high", "斗南花卉日价·最高价"),
    "price_low": ("price.flower.dounan.low", "斗南花卉日价·最低价"),
}

CONCEPTS = [
    # (code, name_zh, name_en, category, unit, measure, frequency,
    #  entity_type, source)
    ("price.flower.dounan.avg", "斗南花卉日价·均价",
     "Dounan flower daily average price", "农产品价格", "currency_cny",
     "price", "daily", "commodity", SOURCE),
    ("price.flower.dounan.high", "斗南花卉日价·最高价",
     "Dounan flower daily high price", "农产品价格", "currency_cny",
     "price", "daily", "commodity", SOURCE),
    ("price.flower.dounan.low", "斗南花卉日价·最低价",
     "Dounan flower daily low price", "农产品价格", "currency_cny",
     "price", "daily", "commodity", SOURCE),
]


def _entity_ids(cur, codes: set[str]) -> dict[str, int]:
    if not codes:
        return {}
    cur.execute(
        "SELECT code, id FROM entities"
        " WHERE entity_type = 'commodity' AND code = ANY(%s)",
        (sorted(codes),),
    )
    return {code: eid for code, eid in cur.fetchall()}


def extract(cur, notes: list[str]) -> list[tuple]:
    """Rows (concept_code, 'commodity', entity_id, date, 'day', value)."""
    items = unit_items(cur, SOURCE)
    notes.append(f"crawl payloads read: {len(items)}")
    by_key = dedup_latest(
        items,
        lambda p: (p.get("item"), p.get("price_date")),
    )
    notes.append(f"dedup by (item, price_date): {len(items)} -> {len(by_key)}")

    unknown = sorted({str(p.get("item")) for p in by_key.values()
                      if p.get("item") not in ITEM_ENTITY})
    if unknown:
        notes.append(f"SKIP 未在显式表中的品种（不猜映射）: {unknown}")

    wanted = {ITEM_ENTITY[p["item"]] for p in by_key.values()
              if p.get("item") in ITEM_ENTITY}
    ids = _entity_ids(cur, wanted)
    missing = sorted(wanted - set(ids))
    if missing:
        notes.append(f"SKIP 未注册的品种实体（先跑 scripts/register_flower_entities.py）: {missing}")

    rows: list[tuple] = []
    for p in by_key.values():
        item = p.get("item")
        eid = ids.get(ITEM_ENTITY.get(item, ""))
        date = p.get("price_date")
        if not eid or not date:
            continue
        for measure, (code, _zh) in MEASURES.items():
            value = num(p.get(measure))
            if value is None:
                continue  # 缺价不补
            rows.append((code, "commodity", eid, str(date), "day", fmt(value)))
    notes.append(f"rows produced: {len(rows)} (3 measures x variety-days)")
    return rows
