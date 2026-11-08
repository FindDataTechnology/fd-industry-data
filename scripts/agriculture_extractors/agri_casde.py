#!/usr/bin/env python3
"""Agricultural wave-1 extractor — agri-casde (CASDE 农产品供需平衡表).

Twenty monthly series, one per (commodity, item) pair, from the CASDE monthly
balance sheets on www.agri.cn/sj/gxxs/.

Caliber rules (all measured against real issues, see
docs/agriculture-series-curation.md §2.1):

* only ``forecast_kind == "current"`` (本月预测) is written. Each sheet carries
  three states side by side (上月预测 / 本月预测 / 实际); mixing them would turn
  one series into a sawtooth of revisions. The other two states stay in the
  payload and are deliberately not registered.
* only the base ``item`` rows are written. The ``_low`` / ``_high`` rows of the
  price-range items are interval endpoints, not series, and price rows
  themselves are forecasts rather than transacted prices — out of scope.
* ``value`` must parse; a missing value produces no row (never zero-filled).
* date = the issue period ``YYYY-MM`` (from the CASDE issue number, not the
  publication day — a report published on the 12th describes the same month).
"""
from __future__ import annotations

from ._common import ENTITY_COUNTRY_CN, fmt, gate_periods, live_items, num

SOURCE = "agri-casde"

_CAT = "农业"

#: (commodity, source item) -> concept code suffix
#:
#: ⚠ 食糖那张表的行标签改过两次名（实测同一批数值 500/1580/16 依次写作
#: 「食糖进口量」(2024-05..11) → 「进口量」(2024-07/08) → 「进口」(2024-09 起)）。
#: 别名共指向一条序列，靠 (code, period) 去重合并，重叠期数值完全一致。
#: 食糖表**没有期末库存**行，只有「结余变化」，故第 4 条取结余变化而非库存。
_SERIES = {
    ("corn", "产量"): "corn.production",
    ("corn", "进口"): "corn.import",
    ("corn", "消费"): "corn.consumption",
    ("corn", "结余变化"): "corn.balance_change",
    ("soybean", "产量"): "soybean.production",
    ("soybean", "进口"): "soybean.import",
    ("soybean", "压榨消费"): "soybean.crush_consumption",
    ("soybean", "结余变化"): "soybean.balance_change",
    ("cotton", "产量"): "cotton.production",
    ("cotton", "进口"): "cotton.import",
    ("cotton", "期末库存"): "cotton.ending_stocks",
    ("cotton", "播种面积"): "cotton.sown_area",
    ("edible_oil", "生产"): "edible_oil.production",
    ("edible_oil", "进口"): "edible_oil.import",
    ("edible_oil", "国内消费"): "edible_oil.consumption",
    ("edible_oil", "结余变化"): "edible_oil.balance_change",
    ("sugar", "食糖产量"): "sugar.production",
    ("sugar", "食糖进口量"): "sugar.import",
    ("sugar", "进口量"): "sugar.import",
    ("sugar", "进口"): "sugar.import",
    ("sugar", "食糖消费量"): "sugar.consumption",
    ("sugar", "消费量"): "sugar.consumption",
    ("sugar", "消费"): "sugar.consumption",
    ("sugar", "结余变化"): "sugar.balance_change",
}

_ZH = {
    "corn": ("玉米", "corn"), "soybean": ("大豆", "soybean"),
    "cotton": ("棉花", "cotton"), "edible_oil": ("食用植物油", "edible oil"),
    "sugar": ("食糖", "sugar"),
}
#: 中文名按源表原词 —— CASDE 表头就是「产量/进口/消费/结余变化…」，
#: 不自造近义词（「消费」≠「国内消费」，只有食用油那张表用后者）
_ITEM_ZH = {
    "production": "产量", "import": "进口", "consumption": "消费",
    "balance_change": "结余变化", "crush_consumption": "压榨消费",
    "ending_stocks": "期末库存", "sown_area": "播种面积",
}
_ITEM_EN = {
    "production": "production", "import": "import", "consumption": "consumption",
    "balance_change": "balance change", "crush_consumption": "crush consumption",
    "ending_stocks": "ending stocks", "sown_area": "sown area",
}
_UNIT = {"sown_area": "千公顷"}

CONCEPTS = []
_seen_suffix: list[str] = []
for _suffix in _SERIES.values():
    if _suffix in _seen_suffix:  # 别名共用同一序列 → 概念只建一次
        continue
    _seen_suffix.append(_suffix)
    _commodity, _m = _suffix.split(".", 1)
    _zh_c, _en_c = _ZH[_commodity]
    CONCEPTS.append((
        f"agriculture.cn.{_suffix}.monthly",
        f"{_zh_c}{_ITEM_ZH.get(_m, _m)}（CASDE 本月预测）",
        f"China {_en_c} {_ITEM_EN.get(_m, _m)}, CASDE monthly forecast",
        _CAT, _UNIT.get(_m, "万吨"), "value", "monthly", "country", SOURCE,
    ))


def extract(cur, notes) -> list:
    rows_raw = live_items(SOURCE, "run_agri_casde", notes, limit=20000)

    # 同一 (commodity,item) 在**一期之内**会出现多个营销年度（实测 2026-08 期：
    # 玉米产量 2025/26=30124（estimate）与 2026/27=30600（forecast）并列）。
    # 只取 basis=forecast 的当期季，否则同序列每月两个点、年份来回跳。
    picked: dict[tuple[str, str], dict] = {}
    skipped_state, skipped_item, bad_value, dup = 0, 0, 0, 0
    for r in rows_raw:
        if r.get("forecast_kind") != "current":
            skipped_state += 1
            continue
        suffix = _SERIES.get((r.get("commodity"), r.get("item")))
        if suffix is None:
            skipped_item += 1
            continue
        if r.get("basis") not in (None, "forecast"):
            dup += 1
            continue
        v = num(r.get("value"))
        if v is None:
            bad_value += 1
            continue
        key = (f"agriculture.cn.{suffix}.monthly", str(r["period"]))
        prev = picked.get(key)
        if prev is not None:
            dup += 1
            # 兜底：同期多季时保留 season 起始年更大的那个（当期主预测季）
            if str(r.get("season") or "") <= str(prev.get("season") or ""):
                continue
        picked[key] = r

    rows = [(code, "country", ENTITY_COUNTRY_CN, period, "month", fmt(num(r["value"])))
            for (code, period), r in picked.items()]

    kept, dropped = gate_periods(rows, 24)
    notes.append(
        f"{SOURCE}: {len(rows)} candidate rows from {len(rows_raw)} payload rows;"
        f" dropped non-current forecast states {skipped_state},"
        f" non-registered items {skipped_item}, other-season/duplicate {dup},"
        f" unparsable values {bad_value}"
    )
    if dropped:
        notes.append(f"{SOURCE}: DROPPED {len(dropped)} rows below trend gate 24")
    periods = sorted({r[3] for r in kept})
    notes.append(
        f"{SOURCE}: {len(kept)} rows over {len(periods)} period(s)"
        f" ({periods[0] if periods else '-'} … {periods[-1] if periods else '-'})"
        f" — HTML 表仅自 No.95(2024-05) 起，更早期次为正文+PDF 未解析"
    )
    return kept
