#!/usr/bin/env python3
"""aiph-stats — flower-vertical extractor (semantic_observations feed).

Reads the central crawl payloads written by ``spiders/aiph-stats`` (AIPH/Union
Fleurs international statistical yearbook, area chapter) and emits one planted
area row per (product, country).

Mapping discipline (docs/flower-series-curation.md §3.1):

* **concept = product, entity = country.** The product axis lives in the
  concept code (``area.flower.<product>.planted``); putting it on the entity
  side would collide on ``uq_sem_obs`` the moment one country has two products
  in the same reference year.
* **``ref_year`` is per row** (2005–2024 mixed inside one workbook), so the
  observation date is ``<ref_year>-01-01`` with granularity ``year``: the board
  must label each bar with its own year and must never compute growth across
  differing years.
* rows keep ``yearbook`` (edition) and ``source_sheet`` in the locator notes —
  the *same* country/product can legitimately reappear in a later edition with
  a newer reference year; that is a new observation (different date), not a
  conflict.
* dedup key (product, country, ref_year) keeping the latest ``scraped_at``;
  a NULL ``area_ha`` is skipped, never written as 0.
* only country codes that exist are used (``scripts/register_flower_entities.py``
  registers the 8 missing AIPH countries); unregistered countries are SKIPped
  with a note, never silently dropped.
"""
from __future__ import annotations

from restaurant_extractors._common import (  # 同一份写入纪律
    dedup_latest, fmt, num, unit_items,
)

SOURCE = "aiph-stats"

#: 上游 country 文本 → ISO 3166-1 alpha-2（与库内既有国别码对齐；显式表）
COUNTRY_ISO = {
    "Austria": "AUT", "Belgium": "BEL", "China": "CN", "Chinese Taipei": "TW",
    "Colombia": "CO", "Denmark": "DNK", "Ecuador": "EC", "France": "FR",
    "Germany": "DE", "India": "IN", "Indonesia": "ID", "Ireland": "IRL",
    "Italy": "ITA", "Japan": "JP", "Kenya": "KE", "Korea, Republic of": "KR",
    "Mexico": "MX", "Netherlands": "NL", "Poland": "POL", "Spain": "ESP",
    "Switzerland": "CH", "Thailand": "TH", "Turkey": "TR", "UK": "GB",
    "USA": "US", "Vietnam": "VN",
}

#: 上游 product slug → concept code（9 个全部注册）
PRODUCT_CONCEPT = {
    "rose": "area.flower.rose.planted",
    "carnation": "area.flower.carnation.planted",
    "orchid": "area.flower.orchid.planted",
    "chrysanthemum": "area.flower.chrysanthemum.planted",
    "lily": "area.flower.lily.planted",
    "christmas_tree": "area.flower.christmas_tree.planted",
    "cut_foliage": "area.flower.cut_foliage.planted",
    "flowering_plant": "area.flower.flowering_plant.planted",
    "green_foliage_plant": "area.flower.green_foliage_plant.planted",
}

PRODUCT_ZH = {
    "rose": "玫瑰", "carnation": "康乃馨", "orchid": "兰花",
    "chrysanthemum": "菊花", "lily": "百合", "christmas_tree": "圣诞树",
    "cut_foliage": "切叶", "flowering_plant": "开花植物",
    "green_foliage_plant": "绿植观叶",
}

CONCEPTS = [
    # (code, name_zh, name_en, category, unit, measure, frequency,
    #  entity_type, source)
    (code, f"花卉种植面积·{PRODUCT_ZH[product]}",
     f"Planted area - {product.replace('_', ' ')}", "农业面积", "hectare",
     "area", "annual", "country", SOURCE)
    for product, code in PRODUCT_CONCEPT.items()
]


def _entity_ids(cur, codes: set[str]) -> dict[str, int]:
    if not codes:
        return {}
    cur.execute(
        "SELECT code, id FROM entities"
        " WHERE entity_type = 'country' AND code = ANY(%s)",
        (sorted(codes),),
    )
    return {code: eid for code, eid in cur.fetchall()}


def extract(cur, notes: list[str]) -> list[tuple]:
    """Rows (concept_code, 'country', entity_id, '<year>-01-01', 'year', value)."""
    items = unit_items(cur, SOURCE)
    notes.append(f"crawl payloads read: {len(items)}")
    by_key = dedup_latest(
        items,
        lambda p: (p.get("product"), p.get("country"), p.get("ref_year")),
    )
    notes.append(f"dedup by (product, country, ref_year): {len(items)} -> {len(by_key)}")

    bad_product = sorted({str(p.get("product")) for p in by_key.values()
                          if p.get("product") not in PRODUCT_CONCEPT})
    if bad_product:
        notes.append(f"SKIP 未映射的品类: {bad_product}")
    bad_country = sorted({str(p.get("country")) for p in by_key.values()
                          if p.get("country") not in COUNTRY_ISO})
    if bad_country:
        notes.append(f"SKIP 未映射的国别（补 COUNTRY_ISO 后重跑）: {bad_country}")

    wanted = {COUNTRY_ISO[p["country"]] for p in by_key.values()
              if p.get("country") in COUNTRY_ISO}
    ids = _entity_ids(cur, wanted)
    missing = sorted(wanted - set(ids))
    if missing:
        notes.append(f"SKIP 未注册的国别实体（先跑 register_flower_entities.py）: {missing}")

    rows: list[tuple] = []
    for p in by_key.values():
        code = PRODUCT_CONCEPT.get(p.get("product"))
        eid = ids.get(COUNTRY_ISO.get(p.get("country"), ""))
        year = p.get("ref_year")
        value = num(p.get("area_ha"))
        if not code or not eid or not year or value is None:
            continue
        rows.append((code, "country", eid, f"{int(year)}-01-01", "year",
                     fmt(value)))
    years = sorted({r[3][:4] for r in rows})
    notes.append(f"rows produced: {len(rows)}; ref_years {years[0]}..{years[-1]}"
                 f" ({len(years)} distinct) — 截面不齐年，禁止跨年求增速")
    notes.append("只做面积不做贸易：Trade 表是 Eurostat 二次发布，与既有车道重复")
    return rows
