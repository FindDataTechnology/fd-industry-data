#!/usr/bin/env python3
"""MCT domestic-tourism demand releases — ``mct-tourism-demand`` (wave 1 → observations).

SOURCE = ``mct-tourism-demand``. The unit parses Chinese prose (no tables) and
emits ``(period, period_kind, scope, indicator, value, unit, yoy)``.

## Modeling: scope lives in the concept, granularity stays granularity

The releases mix four period lengths (年度 / 上半年 / 前三季度 / 单季) and six
scopes (全国 / 城镇 / 农村 / 外国 / 港澳台 / 内地). Two rules keep the natural key
honest:

* **scope → concept code** (``…​.domestic_trips.urban``). Making 城镇/农村 into
  entities would invent an entity taxonomy the catalog does not have; the scope
  is fixed per series, so it belongs to the concept.
* **period_kind → ``granularity``** (``year`` / ``ytd_h1`` / ``ytd_9m`` /
  ``quarter``) with ``date`` = period end (``2025`` / ``2025-06-30`` /
  ``2025-09-30`` / ``2026-03-31``). Cumulative releases therefore never collide
  with the full-year row: same concept, same year, different granularity.

``granularity`` is ``varchar(8)``; ``ytd_h1`` / ``ytd_9m`` / ``quarter`` all fit.
Do not lengthen these labels to ``cumulative-h1`` — the insert would be
truncated (same trap the A-share channel hit with ``cumulative-ytd``).

## Numbers are kept verbatim

Values stay in the published unit (``65.22`` × ``亿人次``, ``4.95`` × ``万亿元``).
No unit conversion, no rescaling: a board that wants people multiplies by 1e8 and
must say so. ``yoy`` is dropped here on purpose — the unit's own yoy column is
sparse, and a derived spread computed from two different period kinds would be a
fabricated comparison.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_UNIT = _ROOT / "spiders" / "mct-tourism-demand" / "spider.py"

_SCRIPTS = str(_ROOT / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from hotel_extractors._common import fmt  # noqa: E402

SOURCE = "mct-tourism-demand"
ENTITY_CN = 1

#: indicator → (中文, published unit, 中文说明后缀)
INDICATORS: dict[str, tuple[str, str]] = {
    "domestic_trips": ("国内居民出游人次", "亿人次"),
    "domestic_spending": ("国内居民出游花费", "万亿元"),
    "inbound_trips": ("入境旅游人次", "万人次"),
    "outbound_trips": ("出境旅游人次", "万人次"),
    "foreign_inbound_trips": ("外国入境旅游人次", "万人次"),
    "hkmotw_inbound_trips": ("港澳台入境旅游人次", "万人次"),
    "star_hotels": ("星级饭店数", "家"),
    "star_hotel_revenue": ("星级饭店营业收入", "亿元"),
    "star_hotel_adr": ("星级饭店平均房价", "元/间夜"),
    "star_hotel_occ": ("星级饭店平均出租率", "%"),
    "travel_agencies": ("旅行社数", "家"),
    "a_level_scenic": ("A 级景区数", "个"),
    "scenic_visits": ("A 级景区接待人次", "亿人次"),
    "scenic_revenue": ("A 级景区营业收入", "亿元"),
}

#: scope label → (code fragment, 中文)
SCOPES: dict[str, tuple[str, str]] = {
    "全国": ("national", "全国"),
    "城镇": ("urban", "城镇"),
    "农村": ("rural", "农村"),
    "外国": ("foreign", "外国"),
    "港澳台": ("hkmotw", "港澳台"),
    "内地": ("mainland", "内地"),
}

#: period_kind → granularity (must fit varchar(8))
GRANULARITY = {"fy": "year", "ytd_h1": "ytd_h1", "ytd_9m": "ytd_9m",
               "quarter": "quarter"}


def concept_code(indicator: str, scope: str) -> str:
    return f"hotel.cn.tourism.{indicator}.{SCOPES[scope][0]}"


#: every (indicator, scope) pair the releases actually carry
PAIRS: tuple[tuple[str, str], ...] = tuple(
    sorted(
        [(i, "全国") for i in INDICATORS]
        + [("domestic_trips", s) for s in ("城镇", "农村")]
        + [("domestic_spending", s) for s in ("城镇", "农村")]
        + [("foreign_inbound_trips", "外国"),
           ("hkmotw_inbound_trips", "港澳台"),
           ("outbound_trips", "内地")]
    )
)

CONCEPTS: list[tuple] = [
    (
        concept_code(ind, scope),
        f"中国{INDICATORS[ind][0]}（{SCOPES[scope][1]}）",
        f"CN tourism: {ind.replace('_', ' ')} ({SCOPES[scope][0]})",
        "旅游需求",
        INDICATORS[ind][1],
        "value",
        "irregular",
        "country",
        SOURCE,
    )
    for ind, scope in PAIRS
]

PERIOD_END = {"ytd_h1": "-06-30", "ytd_9m": "-09-30"}
QUARTER_MONTH = {"Q1": "-03-31", "Q2": "-06-30", "Q3": "-09-30", "Q4": "-12-31"}


def period_date(period: str, kind: str) -> str:
    """'2025H1'+ytd_h1 → '2025-06-30'; '2026Q1'+quarter → '2026-03-31'."""
    if kind == "fy":
        return period[:4]
    if kind in PERIOD_END:
        return period[:4] + PERIOD_END[kind]
    if kind == "quarter":
        return period[:4] + QUARTER_MONTH[period[4:]]
    raise ValueError(f"unexpected period_kind: {kind!r} ({period})")


def _load_unit():
    name = "unit_mct_tourism_demand_extract"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _UNIT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def extract(cur, notes) -> list[tuple]:
    unit = _load_unit()
    rows = unit.run_mct_tourism_demand(limit=5000)
    if not rows:
        notes.append(f"{SOURCE}: 单元返回 0 行——上游改版或发放窗口变了，不写空观测")
        return []
    for trace in getattr(unit, "LAST_TRACES", []) or []:
        notes.append(f"{SOURCE}: {trace}")

    out: list[tuple] = []
    seen: set[tuple] = set()
    unknown: dict[tuple[str, str], int] = {}
    for r in rows:
        ind, scope = str(r.get("indicator")), str(r.get("scope"))
        if ind not in INDICATORS or (ind, scope) not in PAIRS:
            unknown[(ind, scope)] = unknown.get((ind, scope), 0) + 1
            continue
        kind = str(r.get("period_kind"))
        if kind not in GRANULARITY:
            unknown[("period_kind", kind)] = unknown.get(("period_kind", kind), 0) + 1
            continue
        code = concept_code(ind, scope)
        date = period_date(str(r["period"]), kind)
        gran = GRANULARITY[kind]
        key = (code, date, gran)
        if key in seen:
            continue
        seen.add(key)
        out.append((code, "country", ENTITY_CN, date, gran, fmt(float(r["value"]))))

    if unknown:
        notes.append(
            f"{SOURCE}: 未登记指标/口径组合，跳过 {sum(unknown.values())} 行: "
            + ", ".join(f"{k[0]}/{k[1]}={v}" for k, v in sorted(unknown.items()))
        )
    return out
