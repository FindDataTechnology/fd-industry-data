#!/usr/bin/env python3
"""MCT star-rated hotel quarterly survey — ``mct-star-hotel`` (wave 1 → observations).

SOURCE = ``mct-star-hotel``. The unit emits a 14-column long table
``(period, table, area, star, indicator, value, unit, area_kind, ...)``; this
extractor turns it into observations.

## Dimension model (the point of task 2.5)

The two dimensions the spec requires to coexist are carried **differently** so
they can never overwrite each other:

* **地区** is the entity (``country`` CN / ``region`` CN-44… / ``city`` CN-CITY-杭州);
* **星级档** is part of the **concept code** (``…​.all.quarterly`` vs
  ``…​.star3.quarterly``), because a star band is a fixed property of an
  aggregate, not something you can point at with an entity id.

So 分省(OCC, 全部星级) and 分省(OCC, 三星) are different ``concept_id`` on the
same entity — both land, neither replaces the other. Country-total rows
(``area_kind=全国``) stay on entity CN; ``50城合计`` is its own region entity
``CN-50CITIES`` because that table's "全国" row is a **50-city subtotal**
(2,209 in 2024Q3) and not the national total (6,129).

## Data facts that travel with the numbers

* The series is **discontinued**: newest published period is 2024Q3
  (2024-12-26). This is a real停更 sample — the board must show it as
  historical baseline, and drift detection is task 6.4/6.5, not a fake "latest".
* 2024Q1's attachments 1/2 are **mislabelled and byte-identical** audit-rate
  tables; the unit skips them (``audit-completion``). Their absence is not a gap
  in the operating series.
* Full-width ``－`` = not disclosed → row omitted by the unit. Never written as 0.

Quarter label ``2023Q4`` is stored as its **period end date** (``2023-12-31``)
because ``semantic_observations.date`` only accepts YYYY / YYYY-MM / YYYY-MM-DD
shapes; ``granularity='quarter'`` keeps the frequency explicit.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_UNIT = _ROOT / "spiders" / "mct-star-hotel" / "spider.py"

_SCRIPTS = str(_ROOT / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from hotel_extractors._common import fmt  # noqa: E402
from hotel_regions import classify_or_raise, ensure_entity  # noqa: E402

SOURCE = "mct-star-hotel"

#: indicator slug → (中文名, unit). Units are the unit's own header units.
INDICATORS: dict[str, tuple[str, str]] = {
    "count": ("数量", "家"),
    "revenue": ("营业收入", "亿元"),
    "revenue_share_fnb": ("餐饮收入比重", "%"),
    "revenue_share_room": ("客房收入比重", "%"),
    "adr": ("平均房价", "元/间夜"),
    "occupancy": ("平均出租率", "%"),
    "revpar": ("每间可供出租客房收入", "元/间夜"),
    "revenue_per_room": ("每间客房平摊营业收入", "元/间"),
    "adr_yoy": ("平均房价同比", "%"),
    "occupancy_yoy": ("平均出租率同比", "%"),
    "revpar_yoy": ("每间可供出租客房收入同比", "%"),
    "revenue_per_room_yoy": ("每间客房平摊营业收入同比", "%"),
}

#: 星级档 → (code fragment, 中文)
STAR_DIMS: dict[str, tuple[str, str]] = {
    "": ("all", "全部星级"),
    "1": ("star1", "一星"),
    "2": ("star2", "二星"),
    "3": ("star3", "三星"),
    "4": ("star4", "四星"),
    "5": ("star5", "五星"),
}


def concept_code(indicator: str, star: str) -> str:
    return f"hotel.cn.star_rated.{indicator}.{STAR_DIMS[star][0]}.quarterly"


CONCEPTS: list[tuple] = []
for _ind, (_zh, _unit) in INDICATORS.items():
    for _star, (_dim_en, _dim_zh) in STAR_DIMS.items():
        CONCEPTS.append((
            concept_code(_ind, _star),
            f"中国星级饭店 {_zh}（{_dim_zh}，按地区）",
            f"CN star-rated hotel {_ind.replace('_', ' ')}, {_dim_en} bands, "
            "by region (quarterly)",
            "行业经营",
            _unit,
            "value",
            "quarter",
            "region",
            SOURCE,
        ))

QUARTER_END = {"Q1": "-03-31", "Q2": "-06-30", "Q3": "-09-30", "Q4": "-12-31"}


def quarter_end(period: str) -> str:
    """'2023Q4' → '2023-12-31' (date column accepts only numeric shapes)."""
    year, quarter = period[:4], period[4:]
    if quarter not in QUARTER_END:
        raise ValueError(f"unexpected period label: {period!r}")
    return year + QUARTER_END[quarter]


def _load_unit():
    name = "unit_mct_star_hotel_extract"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _UNIT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def extract(cur, notes) -> list[tuple]:
    """Emit ``(code, etype, eid, date, gran, value)`` for every produced row."""
    unit = _load_unit()
    rows = unit.run_mct_star_hotel(limit=20000)
    if not rows:
        notes.append(f"{SOURCE}: 单元返回 0 行——先查上游是否改版，不写空观测")
        return []
    for trace in getattr(unit, "LAST_TRACES", []) or []:
        notes.append(f"{SOURCE}: {trace}")

    out: list[tuple] = []
    seen: dict[tuple, str] = {}
    unmapped: dict[tuple[str, str], int] = {}
    dup = 0
    for r in rows:
        indicator = r.get("indicator")
        star = str(r.get("star") or "")
        if indicator not in INDICATORS or star not in STAR_DIMS:
            unmapped[(str(indicator), star)] = unmapped.get((str(indicator), star), 0) + 1
            continue
        try:
            etype, code = classify_or_raise(str(r.get("area_kind")), str(r.get("area")))
        except KeyError as exc:
            notes.append(f"{SOURCE}: {exc}")
            continue
        eid = ensure_entity(cur, etype, code, str(r.get("area")))
        date = quarter_end(str(r["period"]))
        concept = concept_code(indicator, star)
        key = (concept, etype, eid, date)
        if key in seen:
            dup += 1
            continue  # 同一期同一实体重复出现（两表同源行）→ 取先出现者
        seen[key] = concept
        out.append((concept, etype, eid, date, "quarter", fmt(float(r["value"]))))

    if unmapped:
        notes.append(
            f"{SOURCE}: 未登记指标/星级组合，跳过 {sum(unmapped.values())} 行: "
            + ", ".join(f"{k[0]}/{k[1] or 'all'}={v}" for k, v in sorted(unmapped.items()))
        )
    if dup:
        notes.append(f"{SOURCE}: 同期同实体重复行 {dup} 条已去重（保留先出现者）")
    return out
