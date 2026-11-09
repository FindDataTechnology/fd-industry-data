#!/usr/bin/env python3
"""Hotel-vertical — China region/city entity classification (shared by extractors).

The MCT star-rated-hotel report publishes four table shapes and its row labels
are the only area identifiers we get:

===============  ======================================  =====================
``area_kind``    upstream label example                  entity mapping
===============  ======================================  =====================
全国             全国                                    ``country`` CN (id 1)
省               广东 / 内蒙古 / 新疆兵团                ``region``  CN-44 / CN-15 / CN-XJBT
50城合计         50城合计                                ``region``  CN-50CITIES (aggregate)
城市             杭州                                    ``city``    CN-CITY-杭州
===============  ======================================  =====================

Two decisions worth stating because they are not obvious:

1. **Provinces use GB/T 2260 two-digit codes** (``CN-44`` = 广东). The mapping
   below is the standard division, written out once so nothing is invented at
   runtime. 新疆兵团 (XPCC) has no GB code — it is a province-level corps, so it
   gets the explicit ``CN-XJBT`` code and stays ``region``.
2. **Cities use ``CN-CITY-<上游行标签>``** (e.g. ``CN-CITY-杭州``). We do not
   invent adcodes: a wrong 4-digit code would silently mis-join later, while the
   upstream label is exactly what the payload says and is traceable. Re-code to
   GB adcodes is booked as a cleanup item once a vetted adcode source is wired.

``50城合计`` is kept as its own ``region`` entity (``CN-50CITIES``) instead of
being folded into the country row: the 50-city table's "全国" row is the 50-city
subtotal (2,209 in 2024Q3), **not** the national total (6,129). Merging them
would create a silent 3x caliber error.

Cities already in ``entities`` are matched by ``name_zh`` first (北京 is BJS,
上海 SHA, 深圳 SZX, 香港 HKG) so no duplicate city identities are created.
"""
from __future__ import annotations

COUNTRY_CN = 1

#: GB/T 2260 province-level two-digit code, keyed by the label MCT prints
PROVINCE_GB = {
    "北京": "11", "天津": "12", "河北": "13", "山西": "14", "内蒙古": "15",
    "辽宁": "21", "吉林": "22", "黑龙江": "23",
    "上海": "31", "江苏": "32", "浙江": "33", "安徽": "34", "福建": "35",
    "江西": "36", "山东": "37",
    "河南": "41", "湖北": "42", "湖南": "43", "广东": "44", "广西": "45",
    "海南": "46",
    "重庆": "50", "四川": "51", "贵州": "52", "云南": "53", "西藏": "54",
    "陕西": "61", "甘肃": "62", "青海": "63", "宁夏": "64", "新疆": "65",
}
#: province-level units that are not in GB/T 2260
PROVINCE_SPECIAL = {"新疆兵团": "CN-XJBT"}

AGGREGATE_50 = "CN-50CITIES"


def classify(area_kind: str, area: str) -> tuple[str, str] | None:
    """(entity_type, code) for an upstream (area_kind, area) label pair."""
    if area_kind == "全国" and area == "全国":
        return ("country", "CN")
    if area_kind == "50城合计":
        return ("region", AGGREGATE_50)
    if area_kind == "省":
        if area in PROVINCE_SPECIAL:
            return ("region", PROVINCE_SPECIAL[area])
        gb = PROVINCE_GB.get(area)
        if gb is None:
            return None
        return ("region", f"CN-{gb}")
    if area_kind == "城市":
        return ("city", f"CN-CITY-{area}")
    return None


def classify_or_raise(area_kind: str, area: str) -> tuple[str, str]:
    got = classify(area_kind, area)
    if got is None:
        raise KeyError(
            f"未登记的区域标签 (area_kind={area_kind!r}, area={area!r}) —— "
            "上游新增省份/城市必须先落实体，不得按名字硬塞"
        )
    return got


def resolve(cur, entity_type: str, code: str) -> int | None:
    """Existing entity id for (entity_type, code), else by name match for cities.

    City rows are matched on ``name_zh`` first because the catalog predates this
    line and already carries 北京/上海/深圳/香港 under IATA-style codes.
    """
    cur.execute(
        "SELECT id FROM entities WHERE entity_type = %s AND code = %s",
        (entity_type, code),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    if entity_type == "city":
        name = code.removeprefix("CN-CITY-")
        cur.execute(
            "SELECT id FROM entities WHERE entity_type = 'city' AND name_zh = %s"
            " ORDER BY id LIMIT 1", (name,),
        )
        row = cur.fetchone()
        if row:
            return row[0]
    return None


def ensure_entity(cur, entity_type: str, code: str, name_zh: str) -> int:
    """Resolve or create the entity row; returns its id.

    ``ON CONFLICT (entity_type, code) DO NOTHING`` + a re-select keeps this safe
    when two extractors run in the same batch.
    """
    found = resolve(cur, entity_type, code)
    if found is not None:
        return found
    cur.execute(
        "INSERT INTO entities (entity_type, code, name_zh, metadata_json)"
        " VALUES (%s, %s, %s, %s)"
        " ON CONFLICT ON CONSTRAINT uq_entity_type_code DO NOTHING"
        " RETURNING id",
        (entity_type, code, name_zh,
         '{"registry": "add-hotel-vertical", "source_label": "%s"}' % name_zh),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    found = resolve(cur, entity_type, code)
    if found is None:
        raise RuntimeError(f"entity unresolved after insert: {entity_type}/{code}")
    return found
