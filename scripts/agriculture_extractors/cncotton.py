#!/usr/bin/env python3
"""Agricultural wave-1 extractor — cncotton (中国棉花网月度市场报告).

Numbers come from the report PDF's TEXT LAYER (``fd_industry_data.pdftext``,
no OCR anywhere). Real depth: 214 rows over 36 report periods
(2022-08 … 2025-08); the site stopped publishing after 2025-08.

Series selection (measured counts in docs/agriculture-series-curation.md §4.2):

    production      36 periods  -> registered (>= 24)
    ending_stocks   34 periods  -> registered (>= 24)
    cotlook_a_index 18 periods  -> registered as short_window(18)
    import_cum_*     6 periods  -> EXCLUDED (< 12), noted not registered

⚠ Same-named caliber, different source from CASDE: CASDE cotton production is
the Chinese official forecast; cncotton relays USDA's world forecast. Both may
coexist in the store because ``source_used`` is part of the natural key, but a
dashboard must pick ONE as the headline and label the other as relayed.

⚠ ``ending_stocks`` uses the row's ``forecast_season`` (2022/23 …), i.e. each
report carries several seasons; here we keep every (period, season) pair as its
own point on the same series only when the value is the CURRENT season forecast.
To avoid a multi-season sawtooth the extractor keeps the season whose start year
equals the report period's year or the previous one — i.e. the season under
review — and notes how many rows were dropped for being another season.
"""
from __future__ import annotations

from ._common import ENTITY_COUNTRY_CN, fmt, gate_periods, live_items, num

SOURCE = "cncotton"
_CAT = "农业"

CONCEPTS = [
    ("agriculture.cn.cotton.production.cottonnet.monthly",
     "棉花产量（中国棉花网转述 USDA 全球预测）",
     "Cotton production, world forecast as relayed by China Cotton Network",
     _CAT, "万吨", "value", "monthly", "country", SOURCE),
    ("agriculture.cn.cotton.ending_stocks.cottonnet.monthly",
     "棉花期末库存（中国棉花网转述 USDA 全球预测）",
     "Cotton ending stocks, world forecast as relayed by China Cotton Network",
     _CAT, "万吨", "value", "monthly", "country", SOURCE),
    ("agriculture.cn.cotton.cotlook_a.monthly",
     "Cotlook A 指数月均价",
     "Cotlook A index, monthly average",
     _CAT, "美分/磅", "value", "monthly", "country", SOURCE),
]

#: item -> concept suffix
_ITEMS = {
    "production": "production.cottonnet",
    "ending_stocks": "ending_stocks.cottonnet",
}

#: 12-23 期 → short_window（实测 18 期，见 docs/agriculture-series-curation.md §4.2）
SHORT_WINDOW = {"agriculture.cn.cotton.cotlook_a.monthly": 18}


def _season_ok(period: str, season: str | None) -> bool:
    """报告期年 或 其前一年 = 该 season 的起始年 → 这是「当期主预测季」。"""
    if not season or "/" not in season:
        return True
    try:
        start = int(season.split("/")[0])
        year = int(period[:4])
    except ValueError:
        return True
    return start in (year, year - 1)


def extract(cur, notes) -> list:
    raw = live_items(SOURCE, "run_cncotton", notes, limit=400)

    rows: list = []
    off_season = 0
    for r in raw:
        item = r.get("item")
        period = str(r.get("period") or "")
        v = num(r.get("value"))
        if v is None or not period:
            continue

        if item == "cotlook_a_index":
            # sub_period separates 上月月均 / 本月截至日均 → 只取上月月均作为月值，
            # 本月 MTD 是期间内值，混进月序列会造成双点/月。
            if r.get("sub_period") != "prev_month_avg":
                continue
            rows.append(("agriculture.cn.cotton.cotlook_a.monthly", "country",
                         ENTITY_COUNTRY_CN, period, "month", fmt(v)))
            continue

        suffix = _ITEMS.get(item)
        if suffix is None:
            continue
        if not _season_ok(period, r.get("forecast_season")):
            off_season += 1
            continue
        rows.append((f"agriculture.cn.cotton.{suffix}.monthly", "country",
                     ENTITY_COUNTRY_CN, period, "month", fmt(v)))

    # 同一 (code, period) 可能因多季并存出现多次 → 保留首见（报告期主季），
    # 其余计数留痕，绝不合并成均值。
    seen = set()
    deduped = []
    dup = 0
    for row in rows:
        key = (row[0], row[3])
        if key in seen:
            dup += 1
            continue
        seen.add(key)
        deduped.append(row)

    notes.append(
        f"{SOURCE}: {len(raw)} payload row(s) -> {len(deduped)} row(s);"
        f" SKIP {off_season} other-season row(s), {dup} duplicate (code,period),"
        f" PLUS import_cum_volume (6 periods < 12) not registered"
    )

    kept, dropped = gate_periods(deduped, 12)
    if dropped:
        codes = sorted({r[0] for r in dropped})
        notes.append(
            f"{SOURCE}: DROPPED below 12 periods: {codes} ({len(dropped)} rows)"
        )
    for code in sorted({r[0] for r in kept}):
        n = len([r for r in kept if r[0] == code])
        notes.append(f"{SOURCE}: {code} -> {n} period(s)")
    return kept
