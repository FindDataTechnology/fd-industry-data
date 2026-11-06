#!/usr/bin/env python3
"""Task 2.1/2.2 batch2 — trend + short_window unit extraction into semantic_observations.

Batch 2 of wire-industry-supply-funnel. Same write discipline as batch 1
(scripts/extract_batch1.py, itself following the industry-vertical-supply
backfill pattern):

* additive only — INSERT ... ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING
  on the natural key (concept_id, entity_type, entity_id, date, granularity,
  source_used); a second run inserts zero rows (verified for the four
  already-ingested units chinawuliu-logistics / iea-ev / pboc-monetary /
  moa-hog-price and for every new unit on re-run);
* never interpolates, zero-fills or fabricates periods — every written value
  is a real stored payload value; same (series, period) crawled multiple
  times -> keep the payload row with the LATEST scraped_at;
* obs_month governance bucket written at the write site (same mirror of
  fd_open_data_mcp.semantic.dates.obs_month_of as batch 1);
* per-unit SAVEPOINT isolation: a failing unit rolls back alone and the run
  continues with the next unit (single outer transaction, committed once).

Task 2.2 (short_window registration) — registry_entries provenance jsonb:
* 11 EXISTING registry mirror entries (chinawuliu x3, iea-ev, pboc x5,
  moa x2) get {"short_window": {...}} appended to provenance (verified
  state untouched);
* NEW short-window concepts (moa agri.pork.price.market + mem-disaster x3)
  get a registry mirror entry created WITH the declaration and
  verified=false from birth (naming gate stays with the main agent, who
  will set verified later).

Concept naming follows batch-1 style (dotted lowercase, domain prefix);
mohurd-yearbook indicator names are generic across sources so every mohurd
code carries a `.mohurd` anti-collision suffix (per handoff instruction 5),
with naming evidence recorded in the extractor notes and batch2-report.md.

Units and period counts (verified against stored payloads, 2026-11-06):
  trend     afdc 7 concepts x US annual (CNG 34 / Electric 31 / LNG 30 /
            Biodiesel 26 / Hydrogen 24 / Ethanol-E85 34 / Propane 34 years;
            Methanol 9y and Renewable Diesel 3y below the 24-period gate ->
            excluded);
            fao-food-price 6 monthly indices (167/167/167/167/166/166
            months, stored window 1990-01..2003-11 截尾面) + 2 annual series
            (nominal/real, 1961..2026 = 66 years) x WORLD;
            mohurd-yearbook 29 concepts x CN annual (table 1 8 series 30-45y;
            table 2 3 series 48y; rural 3-1-1 7 series 35y; rural 3-1-2 11
            series 35y; table 3 and 3-1-3 excluded — see notes);
            openalex-works 1 x WORLD monthly 81; socrata-chicago 1 x CHI
            monthly 101; socrata-ny 4 x NYC weekly 104; amis-market 1 x
            WORLD/US annual seasons 27 (Viet Nam has no entity row, Ukraine
            11y / UK 8y below gate);
  short_win chinawuliu-logistics (idempotent, 3 concepts), iea-ev
            (idempotent, 1), pboc-monetary (idempotent, 5), moa-hog-price
            (2 existing idempotent + 1 new concept, 15 weeks),
            mem-disaster (3 new concepts, 13 months).

Usage:
    python3 extract_batch2.py --dry-run
    python3 extract_batch2.py            # writes (single transaction)
DSN: source ~/.config/finddata/dashboards.env then $FD_OPEN_DATA_DSN.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date

import psycopg2

# ── obs_month governance bucket (fd_open_data_mcp.semantic.dates mirror) ─────
_BARE_YEAR = re.compile(r"^\d{4}$")
_BARE_MONTH = re.compile(r"^\d{4}-\d{2}$")
_ISO_WEEK = re.compile(r"^\d{4}-W\d{2}$")


def obs_month_of(d: str | None) -> str | None:
    """'YYYY-MM' bucket for any tolerated date shape; None when unmappable."""
    if not d:
        return None
    if _ISO_WEEK.match(d):
        try:
            return date.fromisocalendar(int(d[:4]), int(d[6:8]), 1).strftime("%Y-%m")
        except ValueError:
            return None
    if _BARE_YEAR.match(d):
        return f"{d}-12"
    if _BARE_MONTH.match(d) or (len(d) >= 7 and d[4] == "-" and d[:4].isdigit()
                                and d[5:7].isdigit()):
        return d[:7]
    return None


# ── entities (existing ids, no entity rows are created by this batch) ────────
COUNTRY_CN, COUNTRY_US = 1, 2
ENTITY_WORLD = 300932          # country 'WORLD'
CITY_NYC, CITY_CHI = 20, 29    # city 'NYC' / 'CHI'
INDUSTRY_CWL = 2670            # 快递物流 industry anchor (code 801170,
                               # used by the existing chinawuliu rows)

# ── short_window registry declaration (task 2.2 fixed wording) ───────────────
SW_DECLARED_BY = "adjudication-review-2026-11-06"
SW_DECLARED_IN = "wire-industry-supply-funnel task 2.2"


def sw_decl(periods: int) -> dict:
    return {"short_window": {"periods": periods, "gate": "12-23",
                             "disposition": "short_window",
                             "decided_by": SW_DECLARED_BY,
                             "declared_in": SW_DECLARED_IN}}


# ── concepts created this batch (verified=false; naming gate = main agent) ───
# (code, name_zh, name_en, category, unit, measure, frequency, entity_type, source)
CONSTRUCT_CONCEPTS = [
    # ── afdc: US alternative fueling stations, annual, unit=个(count) ──
    ("energy.stations.cng", "CNG 加注站数", "CNG fueling stations",
     "能源", "个", "value", "yearly", "country", "afdc"),
    ("energy.stations.electric", "电动加注站数", "Electric charging stations",
     "能源", "个", "value", "yearly", "country", "afdc"),
    ("energy.stations.lng", "LNG 加注站数", "LNG fueling stations",
     "能源", "个", "value", "yearly", "country", "afdc"),
    ("energy.stations.biodiesel", "生物柴油加注站数", "Biodiesel fueling stations",
     "能源", "个", "value", "yearly", "country", "afdc"),
    ("energy.stations.hydrogen", "氢能加注站数", "Hydrogen fueling stations",
     "能源", "个", "value", "yearly", "country", "afdc"),
    ("energy.stations.e85", "乙醇（E85）加注站数", "Ethanol (E85) fueling stations",
     "能源", "个", "value", "yearly", "country", "afdc"),
    ("energy.stations.propane", "丙烷加注站数", "Propane fueling stations",
     "能源", "个", "value", "yearly", "country", "afdc"),
    # ── fao-food-price: FAO food price indices, WORLD ──
    ("price.food.fao.index", "FAO 食品价格指数（月度）",
     "FAO Food Price Index, monthly", "价格指数", "index", "value",
     "monthly", "country", "fao-food-price"),
    ("price.food.fao.cereals", "FAO 谷物价格指数（月度）",
     "FAO Cereals price index, monthly", "价格指数", "index", "value",
     "monthly", "country", "fao-food-price"),
    ("price.food.fao.dairy", "FAO 乳品价格指数（月度）",
     "FAO Dairy price index, monthly", "价格指数", "index", "value",
     "monthly", "country", "fao-food-price"),
    ("price.food.fao.meat", "FAO 肉类价格指数（月度）",
     "FAO Meat price index, monthly", "价格指数", "index", "value",
     "monthly", "country", "fao-food-price"),
    ("price.food.fao.oils", "FAO 植物油价格指数（月度）",
     "FAO Vegetable oils price index, monthly", "价格指数", "index", "value",
     "monthly", "country", "fao-food-price"),
    ("price.food.fao.sugar", "FAO 食糖价格指数（月度）",
     "FAO Sugar price index, monthly", "价格指数", "index", "value",
     "monthly", "country", "fao-food-price"),
    ("price.food.fao.annual.nominal", "FAO 食品价格指数（年度，名义）",
     "FAO Food Price Index, annual nominal", "价格指数", "index", "value",
     "yearly", "country", "fao-food-price"),
    ("price.food.fao.annual.real", "FAO 食品价格指数（年度，实际）",
     "FAO Food Price Index, annual real", "价格指数", "index", "value",
     "yearly", "country", "fao-food-price"),
    # ── mohurd-yearbook: 29 concepts, CN annual, .mohurd 防撞码 ──
    # national table 1 — 城市市政公用设施水平
    ("urban.water.coverage.mohurd", "城市供水普及率",
     "Urban water coverage rate", "城乡建设", "%", "value", "yearly",
     "country", "mohurd-yearbook"),
    ("urban.gas.coverage.mohurd", "城市燃气普及率",
     "Urban gas coverage rate", "城乡建设", "%", "value", "yearly",
     "country", "mohurd-yearbook"),
    ("urban.road.area.per.capita.mohurd", "城市人均道路面积",
     "Urban road area per capita", "城乡建设", "平方米", "value", "yearly",
     "country", "mohurd-yearbook"),
    ("urban.park.green.per.capita.mohurd", "城市人均公园绿地面积",
     "Urban park green land per capita", "城乡建设", "平方米", "value",
     "yearly", "country", "mohurd-yearbook"),
    ("urban.public.toilets.per.10k.mohurd", "城市每万人拥有公厕",
     "Urban public toilets per 10,000 population", "城乡建设", "座", "value",
     "yearly", "country", "mohurd-yearbook"),
    ("urban.green.coverage.rate.mohurd", "城市建成区绿化覆盖率",
     "Urban built-up area green coverage rate", "城乡建设", "%", "value",
     "yearly", "country", "mohurd-yearbook"),
    ("urban.green.land.rate.mohurd", "城市建成区绿地率",
     "Urban built-up area green land rate", "城乡建设", "%", "value",
     "yearly", "country", "mohurd-yearbook"),
    ("urban.sewage.treatment.rate.mohurd", "城市污水处理率",
     "Urban sewage treatment rate", "城乡建设", "%", "value", "yearly",
     "country", "mohurd-yearbook"),
    # national table 2 — 城市市政公用设施建设固定资产投资
    ("urban.facility.investment.mohurd", "城市市政公用设施建设固定资产投资完成额",
     "Fixed-asset investment completed in urban municipal service facilities",
     "城乡建设", "亿元", "value", "yearly", "country", "mohurd-yearbook"),
    ("urban.facility.investment.share.fixedinv.mohurd",
     "城市市政公用设施投资占全社会固定资产投资比重",
     "Urban municipal investment as share of total fixed-asset investment",
     "城乡建设", "%", "value", "yearly", "country", "mohurd-yearbook"),
    ("urban.facility.investment.share.gdp.mohurd",
     "城市市政公用设施投资占国内生产总值比重",
     "Urban municipal investment as share of GDP",
     "城乡建设", "%", "value", "yearly", "country", "mohurd-yearbook"),
    # rural 3-1-1 — 建制镇及住宅基本情况
    ("rural.town.count.mohurd", "建制镇统计个数",
     "Number of designated towns (jitizhen)", "城乡建设", "万个", "value",
     "yearly", "country", "mohurd-yearbook"),
    ("rural.town.builtup.area.mohurd", "建制镇建成区面积",
     "Built-up area of designated towns", "城乡建设", "万公顷", "value",
     "yearly", "country", "mohurd-yearbook"),
    ("rural.town.hukou.population.mohurd", "建制镇建成区户籍人口",
     "Registered (hukou) population in town built-up areas", "城乡建设",
     "亿人", "value", "yearly", "country", "mohurd-yearbook"),
    ("rural.town.construction.investment.mohurd", "建制镇本年建设投入",
     "Construction investment in designated towns, current year", "城乡建设",
     "亿元", "value", "yearly", "country", "mohurd-yearbook"),
    ("rural.town.housing.stock.mohurd", "建制镇年末实有住宅建筑面积",
     "Existing residential floor space in towns, year-end", "城乡建设",
     "亿平方米", "value", "yearly", "country", "mohurd-yearbook"),
    ("rural.town.housing.completions.mohurd", "建制镇本年住宅竣工建筑面积",
     "Residential floor space completed in towns, current year", "城乡建设",
     "亿平方米", "value", "yearly", "country", "mohurd-yearbook"),
    ("rural.town.housing.per.capita.mohurd", "建制镇人均住宅建筑面积",
     "Residential floor space per capita in towns", "城乡建设", "平方米",
     "value", "yearly", "country", "mohurd-yearbook"),
    # rural 3-1-2 — 建制镇市政公用设施情况
    ("rural.town.water.supply.mohurd", "建制镇年供水总量",
     "Annual water supply of designated towns", "城乡建设", "亿立方米",
     "value", "yearly", "country", "mohurd-yearbook"),
    ("rural.town.water.population.mohurd", "建制镇用水人口",
     "Population with access to tap water in towns", "城乡建设", "亿人",
     "value", "yearly", "country", "mohurd-yearbook"),
    ("rural.town.water.coverage.mohurd", "建制镇供水普及率",
     "Water coverage rate of designated towns", "城乡建设", "%", "value",
     "yearly", "country", "mohurd-yearbook"),
    ("rural.town.water.per.capita.mohurd", "建制镇人均日生活用水量",
     "Per capita daily domestic water use in towns", "城乡建设", "升",
     "value", "yearly", "country", "mohurd-yearbook"),
    ("rural.town.road.length.mohurd", "建制镇道路长度",
     "Road length in designated towns", "城乡建设", "万公里", "value",
     "yearly", "country", "mohurd-yearbook"),
    ("rural.town.bridges.mohurd", "建制镇桥梁数",
     "Bridges in designated towns", "城乡建设", "万座", "value", "yearly",
     "country", "mohurd-yearbook"),
    ("rural.town.drainage.length.mohurd", "建制镇排水管道长度",
     "Drainage pipe length in designated towns", "城乡建设", "万公里",
     "value", "yearly", "country", "mohurd-yearbook"),
    ("rural.town.park.green.mohurd", "建制镇公园绿地面积",
     "Park green land in designated towns", "城乡建设", "万公顷", "value",
     "yearly", "country", "mohurd-yearbook"),
    ("rural.town.park.green.per.capita.mohurd", "建制镇人均公园绿地面积",
     "Park green land per capita in towns", "城乡建设", "平方米", "value",
     "yearly", "country", "mohurd-yearbook"),
    ("rural.town.sanitation.vehicles.mohurd", "建制镇环卫专用车辆设备",
     "Sanitation vehicles in designated towns", "城乡建设", "万辆", "value",
     "yearly", "country", "mohurd-yearbook"),
    ("rural.town.public.toilets.mohurd", "建制镇公共厕所数",
     "Public toilets in designated towns", "城乡建设", "万座", "value",
     "yearly", "country", "mohurd-yearbook"),
    # ── openalex-works ──
    ("research.works.monthly", "全球学术作品产出数（月度）",
     "Global scholarly works published, monthly count", "科研", "篇",
     "value", "monthly", "country", "openalex-works"),
    # ── socrata-chicago ──
    ("transit.bus.rides.monthly", "芝加哥公交月度客次",
     "CTA bus monthly ridership, Chicago", "交通", "人次", "value",
     "monthly", "city", "socrata-chicago"),
    # ── socrata-ny: weekly spot prices, NYC ──
    ("price.crude.wti.spot.weekly", "WTI 原油现货价（周度）",
     "WTI crude oil spot price, weekly", "能源", "美元/桶", "value",
     "weekly", "city", "socrata-ny"),
    ("price.crude.brent.spot.weekly", "Brent 原油现货价（周度）",
     "Brent crude oil spot price, weekly", "能源", "美元/桶", "value",
     "weekly", "city", "socrata-ny"),
    ("price.gasoline.nyharbor.spot.weekly", "纽约港汽油现货价（周度）",
     "New York Harbor gasoline spot price, weekly", "能源", "美元/加仑",
     "value", "weekly", "city", "socrata-ny"),
    ("price.diesel.nyharbor.spot.weekly", "纽约港柴油现货价（周度）",
     "New York Harbor diesel spot price, weekly", "能源", "美元/加仑",
     "value", "weekly", "city", "socrata-ny"),
    # ── amis-market ──
    ("agri.cereal.utilization.amis", "谷物总利用量（AMIS 总谷物）",
     "Total cereals utilization (AMIS)", "农业", "百万吨", "value",
     "yearly", "country", "amis-market"),
    # ── moa-hog-price new concept ──
    ("agri.pork.price.market", "猪肉价格（集贸市场）",
     "Pork price at 500-county fair-trade market points", "农业", "元/公斤",
     "value", "weekly", "country", "moa-hog-price"),
    # ── mem-disaster: monthly natural-disaster impact, CN ──
    ("disaster.affected.monthly", "全国自然灾害受灾人次（月度）",
     "People affected by natural disasters nationwide, monthly", "灾害",
     "万人次", "value", "monthly", "country", "mem-disaster"),
    ("disaster.deaths.missing.monthly", "全国自然灾害死亡失踪人数（月度）",
     "Deaths and missing from natural disasters nationwide, monthly", "灾害",
     "人", "value", "monthly", "country", "mem-disaster"),
    ("disaster.econ.loss.monthly", "全国自然灾害直接经济损失（月度）",
     "Direct economic loss from natural disasters nationwide, monthly",
     "灾害", "亿元", "value", "monthly", "country", "mem-disaster"),
]

# concept code -> crawler source (source_used)
_SOURCE_OF = {c[0]: c[8] for c in CONSTRUCT_CONCEPTS}
_SOURCE_OF.update({
    "expr.lpi": "chinawuliu-logistics",
    "expr.ecom_index": "chinawuliu-logistics",
    "expr.freight_index": "chinawuliu-logistics",
    "auto.ev.sales.bev.car": "iea-ev",
    "macro.socialfin.flow": "pboc-monetary",
    "macro.money.m0.level": "pboc-monetary",
    "macro.money.m1.level": "pboc-monetary",
    "macro.money.m2.level": "pboc-monetary",
    "macro.socialfin.stock": "pboc-monetary",
    "agri.hog.price.spot": "moa-hog-price",
    "agri.pork.price.wholesale": "moa-hog-price",
})

# short-window registry declarations: semantic_code -> measured periods
SW_PERIODS = {
    "expr.lpi": 11, "expr.ecom_index": 7, "expr.freight_index": 20,
    "auto.ev.sales.bev.car": 16,
    "macro.socialfin.flow": 21, "macro.money.m0.level": 21,
    "macro.money.m1.level": 21, "macro.money.m2.level": 21,
    "macro.socialfin.stock": 21,
    "agri.hog.price.spot": 15, "agri.pork.price.wholesale": 15,
    "agri.pork.price.market": 15,
    "disaster.affected.monthly": 13,
    "disaster.deaths.missing.monthly": 13,
    "disaster.econ.loss.monthly": 13,
}


def num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fmt(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s or "0"


def unit_items(cur, source: str) -> list[dict]:
    cur.execute(
        "SELECT ci.payload FROM crawl_items ci"
        " JOIN crawl_runs r ON r.id = ci.run_id"
        " WHERE r.source = %s AND r.status = 'success'",
        (source,),
    )
    return [r[0] for r in cur.fetchall()]


def dedup_latest(items: list[dict], key) -> dict:
    """Keep, per key, the payload with the latest scraped_at (batch rule)."""
    seen: dict = {}
    for p in items:
        k = key(p)
        if k is None:
            continue
        prev = seen.get(k)
        if prev is None or str(p.get("scraped_at", "")) > str(prev.get("scraped_at", "")):
            seen[k] = p
    return seen


def season_to_year(season: str) -> str | None:
    """'2016/17' -> '2016' (AMIS marketing-season start year); None otherwise."""
    m = re.fullmatch(r"(\d{4})/\d{2}", season or "")
    return m.group(1) if m else None


# ── per-unit extractors: yield (code, entity_type, entity_id, date,
#    granularity, value_str) ─────────────────────────────────────────────────
AFDC_FUEL_MAP = {
    "CNG": "energy.stations.cng",
    "Electric": "energy.stations.electric",
    "LNG": "energy.stations.lng",
    "Biodiesel": "energy.stations.biodiesel",
    "Hydrogen": "energy.stations.hydrogen",
    "Ethanol (E85)": "energy.stations.e85",
    "Propane": "energy.stations.propane",
}


def ex_afdc(cur, notes):
    src = "afdc"
    items = unit_items(cur, src)
    seen = dedup_latest(items, lambda p: (p.get("fuel"), p.get("year")))
    out = []
    skipped_fuels = defaultdict(int)
    for (fuel, year), p in sorted(seen.items(), key=lambda kv: str(kv[0])):
        code = AFDC_FUEL_MAP.get(fuel)
        if code is None:
            skipped_fuels[fuel] += 1
            continue
        v = num(p.get("stations"))
        if v is None or not year:
            continue
        out.append((code, "country", COUNTRY_US, str(year), "year", fmt(v)))
    if skipped_fuels:
        notes.append("afdc: 低于 24 期门槛的燃料未入库 "
                     + ", ".join(f"{f}×{n}" for f, n in sorted(skipped_fuels.items()))
                     + "（Methanol (M85) 9 年、Renewable Diesel 3 年）")
    notes.append("afdc: 7 燃料×US 年度站数（CNG 34/Electric 31/LNG 30/Biodiesel 26/"
                 "Hydrogen 24/Ethanol(E85) 34/Propane 34 年，1992..2025）；"
                 "yaxis=Number of Stations → 个")
    return out


FAO_MONTHLY = {
    "Food Price Index": "price.food.fao.index",
    "Cereals": "price.food.fao.cereals",
    "Dairy": "price.food.fao.dairy",
    "Meat": "price.food.fao.meat",
    "Oils": "price.food.fao.oils",
    "Sugar": "price.food.fao.sugar",
}
FAO_ANNUAL = {
    "Food Price Index (nominal)": "price.food.fao.annual.nominal",
    "Food Price Index (real)": "price.food.fao.annual.real",
}


def ex_fao(cur, notes):
    src = "fao-food-price"
    items = unit_items(cur, src)
    seen = dedup_latest(items, lambda p: (p.get("index_name"), p.get("month")))
    out = []
    for (name, period), p in sorted(seen.items(), key=lambda kv: str(kv[0])):
        if name in FAO_MONTHLY:
            code, gran = FAO_MONTHLY[name], "month"
        elif name in FAO_ANNUAL:
            code, gran = FAO_ANNUAL[name], "year"
        else:
            continue
        v = num(p.get("value"))
        if v is None or not period:
            continue
        out.append((code, "country", ENTITY_WORLD, str(period), gran, fmt(v)))
    notes.append("fao-food-price: 月度 6 指数（1990-01..2003-11 截尾面 166-167 月，"
                 "以实存 payload 为准）+ 年度 nominal/real 2 面（1961..2026 共 66 年）；"
                 "基期 2014-2016=100 → index")
    return out


# mohurd-yearbook: (dataset, table, indicator) -> code；未列出的指标全部剔除
# （表2 的 增长率(%) 同名三连/增长速度（%）/国内生产总值/财政收入/
#   计量单位：亿元 :100 全社会固定资产投资投资完成额 = 宏观泄漏与同名歧义；
#   表3 与 rural 3-1-3 整表剔除；rural 3-1-1 的 年份/居住人口/非农人口/
#   建成区暂住人口/住宅/市政公用设施 剔除；rural 3-1-2 的 年份/生活用水 剔除）
MOHURD_MAP = {
    ("national", "1", "供水普及率 （%）"): "urban.water.coverage.mohurd",
    ("national", "1", "燃气普及率 （%）"): "urban.gas.coverage.mohurd",
    ("national", "1", "人均道路面积"): "urban.road.area.per.capita.mohurd",
    ("national", "1", "园林绿化人均公园绿地面积"): "urban.park.green.per.capita.mohurd",
    ("national", "1", "每万人拥有公厕"): "urban.public.toilets.per.10k.mohurd",
    ("national", "1", "建成区绿化覆盖率 （%）"): "urban.green.coverage.rate.mohurd",
    ("national", "1", "建成区绿地率 （%）"): "urban.green.land.rate.mohurd",
    ("national", "1", "污水处理率 （%）"): "urban.sewage.treatment.rate.mohurd",
    ("national", "2", "城市市政公用设施建设固定资产投资投资完成额"):
        "urban.facility.investment.mohurd",
    ("national", "2", "占同期全社会固定资产投资比重 (%)"):
        "urban.facility.investment.share.fixedinv.mohurd",
    ("national", "2", "占同期国内生产总值的比重 (%)"):
        "urban.facility.investment.share.gdp.mohurd",
    ("rural", "3-1-1", "建制镇统计个数"): "rural.town.count.mohurd",
    ("rural", "3-1-1", "建成区面积"): "rural.town.builtup.area.mohurd",
    ("rural", "3-1-1", "建成区户籍人口"): "rural.town.hukou.population.mohurd",
    ("rural", "3-1-1", "本年建设投入"): "rural.town.construction.investment.mohurd",
    ("rural", "3-1-1", "年末实有住宅建筑面积"): "rural.town.housing.stock.mohurd",
    ("rural", "3-1-1", "本年住宅竣工建筑面积"): "rural.town.housing.completions.mohurd",
    ("rural", "3-1-1", "人均住宅建筑面积"): "rural.town.housing.per.capita.mohurd",
    ("rural", "3-1-2", "年供水总量"): "rural.town.water.supply.mohurd",
    ("rural", "3-1-2", "用水人口"): "rural.town.water.population.mohurd",
    ("rural", "3-1-2", "供水普及率 (%)"): "rural.town.water.coverage.mohurd",
    ("rural", "3-1-2", "人均日生活用水量"): "rural.town.water.per.capita.mohurd",
    ("rural", "3-1-2", "道路长度"): "rural.town.road.length.mohurd",
    ("rural", "3-1-2", "桥梁数"): "rural.town.bridges.mohurd",
    ("rural", "3-1-2", "排水管道长度"): "rural.town.drainage.length.mohurd",
    ("rural", "3-1-2", "公园绿地面积"): "rural.town.park.green.mohurd",
    ("rural", "3-1-2", "人均公园绿地面积"): "rural.town.park.green.per.capita.mohurd",
    ("rural", "3-1-2", "环卫专用车辆设备"): "rural.town.sanitation.vehicles.mohurd",
    ("rural", "3-1-2", "公共厕所"): "rural.town.public.toilets.mohurd",
}


def ex_mohurd(cur, notes):
    src = "mohurd-yearbook"
    items = unit_items(cur, src)
    seen = dedup_latest(
        items,
        lambda p: (p.get("dataset"), p.get("table"), p.get("indicator"),
                   str(p.get("year")))
        if p.get("dataset") in ("national", "rural") else None,
    )
    out = []
    skipped = defaultdict(int)
    for (ds, tb, ind, year), p in sorted(seen.items(), key=lambda kv: str(kv[0])):
        code = MOHURD_MAP.get((ds, tb, ind))
        if code is None:
            skipped[f"{ds}/{tb}/{ind}"] += 1
            continue
        v = num(p.get("value"))
        if v is None or not year:
            continue
        out.append((code, "country", COUNTRY_CN, str(year), "year", fmt(v)))
    if skipped:
        notes.append("mohurd-yearbook: 未映射指标未入库（去歧义剔除） "
                     + "; ".join(f"{k}×{n}" for k, n in sorted(skipped.items())))
    notes.append(
        "mohurd-yearbook: 29 概念×CN 年度——表1 8 概念（30-45 年，1981..2025）；"
        "表2 3 概念×48 年（1978..2025；剔 增长率(%) 同名三连、增长速度（%）归属不可辨、"
        "国内生产总值/财政收入/全社会固定资产投资完成额 宏观泄漏列）；"
        "rural 3-1-1 7 概念×35 年（剔 年份 列、居住人口 16/非农人口 15/建成区暂住人口 11 "
        "低于 24 期门槛、住宅/市政公用设施 无单位表头碎片）；"
        "rural 3-1-2 11 概念×35 年（剔 年份 列、生活用水 无单位子列）；"
        "表3（序列止于 2004/2005 死窗且与表1 供水普及率等语义重叠）与 "
        "3-1-3（12-13 期 <24）整表剔除；单位取 payload 逐指标实测")
    return out


def ex_openalex(cur, notes):
    src = "openalex-works"
    items = unit_items(cur, src)
    seen = dedup_latest(items, lambda p: p.get("month"))
    out = []
    for month, p in sorted(seen.items()):
        v = num(p.get("works_count"))
        if v is None or not month:
            continue
        out.append(("research.works.monthly", "country", ENTITY_WORLD,
                    month, "month", fmt(v)))
    notes.append("openalex-works: 全球月度 works 计数 81 期（2020-01..2026-09）；"
                 "锚月固定推进窗，81 期 ≥24")
    return out


def ex_socrata_chicago(cur, notes):
    src = "socrata-chicago"
    items = unit_items(cur, src)
    seen = dedup_latest(items, lambda p: p.get("period"))
    out = []
    for period, p in sorted(seen.items()):
        v = num(p.get("bus_rides_total"))
        if v is None or not period:
            continue
        out.append(("transit.bus.rides.monthly", "city", CITY_CHI,
                    period, "month", fmt(v)))
    notes.append("socrata-chicago: CTA 公交月度总客次 101 期（2018-04..2026-08，"
                 "SoQL $limit=100 滚动窗实存期数）；city CHI(29)")
    return out


def ex_socrata_ny(cur, notes):
    src = "socrata-ny"
    items = unit_items(cur, src)
    seen = dedup_latest(items, lambda p: p.get("period"))
    field_map = {
        "wti_crude_spot_usd_bbl": "price.crude.wti.spot.weekly",
        "brent_crude_spot_usd_bbl": "price.crude.brent.spot.weekly",
        "ny_gasoline_spot_usd_gal": "price.gasoline.nyharbor.spot.weekly",
        "ny_diesel_spot_usd_gal": "price.diesel.nyharbor.spot.weekly",
    }
    out = []
    for period, p in sorted(seen.items()):
        if not period:
            continue
        for field, code in field_map.items():
            v = num(p.get(field))
            if v is None:
                continue
            out.append((code, "city", CITY_NYC, period, "week", fmt(v)))
    notes.append("socrata-ny: NYMEX/纽约港能源现货价 4 概念×104 周（2024-11-01.."
                 "2026-10-23，周五）；数据集 k7gz-mn77 → city NYC(20)")
    return out


def ex_amis(cur, notes):
    src = "amis-market"
    items = unit_items(cur, src)
    seen = dedup_latest(
        items,
        lambda p: (p.get("product"), p.get("element"), p.get("region"),
                   p.get("season"))
        if p.get("product") == "TOTAL CEREALS"
        and p.get("element") == "Total Utilization" else None,
    )
    region_map = {"World": ENTITY_WORLD, "United States of America": COUNTRY_US}
    out = []
    skipped = defaultdict(int)
    for (product, element, region, season), p in sorted(seen.items(),
                                                        key=lambda kv: str(kv[0])):
        eid = region_map.get(region)
        if eid is None:
            skipped[region] += 1
            continue
        year = season_to_year(season)
        v = num(p.get("value"))
        if v is None or not year:
            continue
        out.append(("agri.cereal.utilization.amis", "country", eid,
                    year, "year", fmt(v)))
    if skipped:
        notes.append("amis-market: 无实体归宿/低于门槛的 region 未入库 "
                     + ", ".join(f"{r}×{n}" for r, n in sorted(skipped.items()))
                     + "（Viet Nam 无 entities 行；Ukraine 11 季、United Kingdom 8 季 "
                       "低于 24 期门槛；本批不建实体）")
    notes.append("amis-market: TOTAL CEREALS×Total Utilization，WORLD/US 各 27 季"
                 "（2000/01..2026/27，季首年锚），Million tonnes → 百万吨")
    return out


def ex_chinawuliu(cur, notes):
    src = "chinawuliu-logistics"
    items = unit_items(cur, src)
    seen = dedup_latest(items, lambda p: (p.get("channel"), p.get("period")))
    channel_map = {
        "lpi": ("expr.lpi", "month"),
        "e-commerce": ("expr.ecom_index", "month"),
        "weekly-freight": ("expr.freight_index", "day"),
    }
    out = []
    for (channel, period), p in sorted(seen.items(), key=lambda kv: str(kv[0])):
        code, gran = channel_map.get(channel, (None, None))
        if code is None or not period:
            continue
        v = num(p.get("value"))
        if v is None:
            continue
        out.append((code, "industry", INDUSTRY_CWL, period, gran, fmt(v)))
    notes.append("chinawuliu-logistics: 已入库单元幂等复跑（industry entity 2670；"
                 "weekly-freight 沿既有行 granularity=day 的周五日点口径，保键稳定）；"
                 "短窗声明见 registry（lpi 11 / ecom 7 / freight 20 期）")
    return out


def ex_iea_ev(cur, notes):
    src = "iea-ev"
    items = unit_items(cur, src)
    seen = dedup_latest(
        items,
        lambda p: p.get("year")
        if p.get("category") == "Historical" and p.get("parameter") == "EV sales"
        and p.get("powertrain") == "BEV" and p.get("mode") == "Cars"
        and p.get("region") == "World" else None,
    )
    out = []
    for year, p in sorted(seen.items(), key=lambda kv: str(kv[0])):
        v = num(p.get("value"))
        if v is None or not year:
            continue
        out.append(("auto.ev.sales.bev.car", "country", ENTITY_WORLD,
                    str(year), "year", fmt(v)))
    notes.append("iea-ev: 已入库单元幂等复跑——仅 Historical 类（2010..2025 16 期），"
                 "投影行（至 2035）非真实期按处置剔除；WORLD 归宿沿既有行")
    return out


PBOC_MAP = {
    "社会融资规模增量": "macro.socialfin.flow",
    "社会融资规模存量": "macro.socialfin.stock",
    "M0": "macro.money.m0.level",
    "M1": "macro.money.m1.level",
    "M2": "macro.money.m2.level",
}


def ex_pboc(cur, notes):
    src = "pboc-monetary"
    items = unit_items(cur, src)
    seen = dedup_latest(items, lambda p: (p.get("indicator"), p.get("month")))
    out = []
    for (ind, month), p in sorted(seen.items(), key=lambda kv: str(kv[0])):
        code = PBOC_MAP.get(ind)
        if code is None or not month:
            continue
        v = num(p.get("value"))
        if v is None:
            continue
        out.append((code, "country", COUNTRY_CN, month, "month", fmt(v)))
    notes.append("pboc-monetary: 已入库单元幂等复跑——社融增量/存量 + M0/M1/M2 "
                 "两年度滚动窗 21 个月（2025-01..2026-09），存量行 DO NOTHING 不动；"
                 "年末满 24 期后达 trend 线（短窗声明见 registry）")
    return out


MOA_ITEM_MAP = {
    "活猪": "agri.hog.price.spot",
    "猪肉批发": "agri.pork.price.wholesale",
    "猪肉集贸": "agri.pork.price.market",  # new concept this batch
}


def ex_moa(cur, notes):
    src = "moa-hog-price"
    items = unit_items(cur, src)
    seen = dedup_latest(items, lambda p: (p.get("item"), p.get("date_range")))
    out = []
    for (item, date_range), p in sorted(seen.items(), key=lambda kv: str(kv[0])):
        code = MOA_ITEM_MAP.get(item)
        day = (date_range or "")[:10]
        if code is None or not day:
            continue
        v = num(p.get("price"))
        if v is None:
            continue
        out.append((code, "country", COUNTRY_CN, day, "week", fmt(v)))
    notes.append("moa-hog-price: 活猪/猪肉批发 幂等复跑（批前回填已入库 13 周，"
                 "新采集周合法追加）；猪肉集贸 15 周为本次新增概念 "
                 "agri.pork.price.market；date=date_range 周首日（周一）")
    return out


def ex_mem(cur, notes):
    src = "mem-disaster"
    items = unit_items(cur, src)
    seen = dedup_latest(items, lambda p: p.get("month"))
    field_map = {
        "affected_person": "disaster.affected.monthly",
        "deaths_missing": "disaster.deaths.missing.monthly",
        "economic_loss": "disaster.econ.loss.monthly",
    }
    out = []
    nulls = defaultdict(int)
    for month, p in sorted(seen.items()):
        if not month:
            continue
        for field, code in field_map.items():
            v = num(p.get(field))
            if v is None:
                nulls[code] += 1
                continue
            out.append((code, "country", COUNTRY_CN, month, "month", fmt(v)))
    if nulls:
        notes.append("mem-disaster: 缺测字段跳过 "
                     + ", ".join(f"{c}×{n}" for c, n in sorted(nulls.items()))
                     + "（个别月份通报未列死亡失踪数，不造数）")
    notes.append("mem-disaster: 3 新概念×CN 月度 13 期（2025-02..2026-08，个别月份"
                 "无独立月报故非连续，短窗 12-23 带）；单位 万人次/人/亿元 按 summary 口径")
    return out


EXTRACTORS = {
    "afdc": ex_afdc,
    "fao-food-price": ex_fao,
    "mohurd-yearbook": ex_mohurd,
    "openalex-works": ex_openalex,
    "socrata-chicago": ex_socrata_chicago,
    "socrata-ny": ex_socrata_ny,
    "amis-market": ex_amis,
    "chinawuliu-logistics": ex_chinawuliu,
    "iea-ev": ex_iea_ev,
    "pboc-monetary": ex_pboc,
    "moa-hog-price": ex_moa,
    "mem-disaster": ex_mem,
}

UNITS_WITH_NEW_CONCEPTS = {
    "afdc", "fao-food-price", "mohurd-yearbook", "openalex-works",
    "socrata-chicago", "socrata-ny", "amis-market", "moa-hog-price",
    "mem-disaster",
}


def unit_concept_codes(src: str) -> set[str]:
    """Concept codes touched by a unit (new + pre-existing)."""
    rows = EXTRACTORS_ENABLED.get(src, [])
    return {r[0] for r in rows}


EXTRACTORS_ENABLED: dict[str, list[tuple]] = {}


def ensure_unit_concepts(cur, src: str) -> dict[str, int]:
    """Insert this unit's concepts additively (idempotent); return code -> id."""
    codes = sorted({r[0] for r in EXTRACTORS_ENABLED[src]})
    ids: dict[str, int] = {}
    cur.execute("SELECT id, code FROM concepts WHERE code = ANY(%s)"
                " AND deprecated = false", (codes,))
    for cid, code in cur.fetchall():
        ids[code] = cid
    for c in CONSTRUCT_CONCEPTS:
        if c[0] not in codes or c[0] in ids:
            continue
        (code, name_zh, name_en, category, unit, measure, frequency,
         entity_type, source) = c
        cur.execute(
            "INSERT INTO concepts (code, name_en, name_zh, category, unit,"
            " measure, frequency, entity_type, source, verified, deprecated,"
            " concept_code)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,false,false,NULL)"
            " ON CONFLICT ON CONSTRAINT uq_concept_identity DO NOTHING"
            " RETURNING id",
            (code, name_en, name_zh, category, unit or None, measure,
             frequency, entity_type, source),
        )
        row = cur.fetchone()
        if row:
            ids[code] = row[0]
    missing = set(codes) - set(ids)
    if missing:
        raise RuntimeError(f"{src}: concept codes unresolved: {sorted(missing)}")
    return ids


def insert_rows(cur, ids, rows) -> int:
    inserted = 0
    for (code, etype, eid, d, gran, value) in rows:
        cur.execute(
            "INSERT INTO semantic_observations"
            " (concept_id, entity_type, entity_id, date,"
            "  granularity, value, unit, source_used, fetched_at, obs_month)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now(),%s)"
            " ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING",
            (ids[code], etype, eid, d, gran, value, None, _SOURCE_OF[code],
             obs_month_of(d)),
        )
        inserted += cur.rowcount
    return inserted


def register_short_window(cur, notes) -> tuple[int, int]:
    """Task 2.2: append short_window to 11 existing entries; create 4 new
    entries (new short-window concepts) with the declaration, verified=false.

    Returns (updated_existing, created_new)."""
    updated = 0
    created = 0
    existing_codes = [c for c in SW_PERIODS
                      if c not in ("agri.pork.price.market",
                                   "disaster.affected.monthly",
                                   "disaster.deaths.missing.monthly",
                                   "disaster.econ.loss.monthly")]
    new_codes = ["agri.pork.price.market",
                 "disaster.affected.monthly",
                 "disaster.deaths.missing.monthly",
                 "disaster.econ.loss.monthly"]

    # 1. existing entries — append declaration (idempotent: skip if present)
    cur.execute(
        "SELECT id, native_code, provenance FROM registry_entries"
        " WHERE source_db = 'fd_open_data' AND source_table = 'concepts'"
        " AND native_code = ANY(%s)",
        (existing_codes,),
    )
    found: dict[str, tuple] = {}
    for rid, native, prov in cur.fetchall():
        found[native] = (rid, prov or {})
    missing = [c for c in existing_codes if c not in found]
    if missing:
        raise RuntimeError(f"registry entries missing for {missing}")
    for native, (rid, prov) in sorted(found.items()):
        if "short_window" in prov:
            notes.append(f"registry: {native} 已带 short_window 声明，跳过")
            continue
        cur.execute(
            "UPDATE registry_entries"
            " SET provenance = provenance || %s::jsonb, updated_at = now()"
            " WHERE id = %s",
            (json.dumps(sw_decl(SW_PERIODS[native])), rid),
        )
        updated += cur.rowcount

    # 2. new entries for new short-window concepts — born with the
    #    declaration and verified=false
    cur.execute("SELECT code, name_en, name_zh, category, unit, measure,"
                " frequency, entity_type, source FROM concepts WHERE code = ANY(%s)",
                (new_codes,))
    cmeta = {r[0]: r for r in cur.fetchall()}
    for code in new_codes:
        r = cmeta.get(code)
        if r is None:
            raise RuntimeError(f"new short-window concept {code} not found")
        provenance = {"measure": r[5], "entity_type": r[7], "source": r[8],
                      "code": code, **sw_decl(SW_PERIODS[code])}
        cur.execute(
            "INSERT INTO registry_entries"
            " (source_db, source_table, source_column, native_code,"
            "  semantic_code, name_zh, name_en, unit, frequency, domain,"
            "  provenance, verified)"
            " VALUES ('fd_open_data','concepts','code',%s,%s,%s,%s,%s,%s,%s,"
            " %s::jsonb, false)"
            " ON CONFLICT ON CONSTRAINT uq_registry_anchor DO NOTHING",
            (code, code, r[2], r[1], r[4], r[6], r[3],
             json.dumps(provenance, ensure_ascii=False)),
        )
        created += cur.rowcount
    return updated, created


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default=None, help="comma-separated unit list (default all)")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("DSN missing — source ~/.config/finddata/dashboards.env first")

    conn = psycopg2.connect(args.dsn)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            # snapshot existing state (read-only)
            cur.execute("SELECT count(*) FROM semantic_observations")
            before_total = cur.fetchone()[0]
            cur.execute("SELECT source_used, count(*) FROM semantic_observations"
                        " GROUP BY 1")
            before_by_src = dict(cur.fetchall())

            # dry-run: extract everything read-only to report counts
            notes: list[str] = []
            per_unit: list[tuple[str, int, str]] = []
            failed: list[tuple[str, str]] = []
            registry_result: tuple[int, int] | None = None

            wanted = set(args.only.split(",")) if args.only else None
            if args.dry_run:
                for src, fn in EXTRACTORS.items():
                    if wanted and src not in wanted:
                        continue
                    rows = fn(cur, notes)
                    EXTRACTORS_ENABLED[src] = rows
                    per_unit.append((src, len(rows), "dry-run"))
                    print(f"{src:<22} extracted {len(rows):>6} rows (dry-run)")
                total = sum(len(v) for v in EXTRACTORS_ENABLED.values())
                print(f"\ndry-run total observation rows: {total}")
                print("notes:")
                for n in notes:
                    print("  -", n)
                conn.rollback()
                return 0

            # real run — one transaction, per-unit SAVEPOINT isolation
            for src, fn in EXTRACTORS.items():
                if wanted and src not in wanted:
                    continue
                sp = f"unit_{src.replace('-', '_')}"
                cur.execute(f"SAVEPOINT {sp}")
                try:
                    rows = fn(cur, notes)
                    EXTRACTORS_ENABLED[src] = rows
                    ids = ensure_unit_concepts(cur, src)
                    ins = insert_rows(cur, ids, rows)
                    per_unit.append((src, len(rows), f"inserted {ins}"))
                    print(f"{src:<22} extracted {len(rows):>6} inserted {ins:>6}")
                except Exception as exc:  # noqa: BLE001 — unit isolation
                    cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                    failed.append((src, str(exc)[:300]))
                    print(f"{src:<22} FAILED — rolled back: {exc}")
                cur.execute(f"RELEASE SAVEPOINT {sp}")

            # registry short-window declarations (task 2.2)
            cur.execute("SAVEPOINT registry_sw")
            try:
                registry_result = register_short_window(cur, notes)
            except Exception as exc:  # noqa: BLE001
                cur.execute("ROLLBACK TO SAVEPOINT registry_sw")
                failed.append(("registry-short-window", str(exc)[:300]))
                print(f"registry-short-window FAILED — rolled back: {exc}")
            cur.execute("RELEASE SAVEPOINT registry_sw")

            cur.execute("SELECT count(*) FROM semantic_observations")
            after_total = cur.fetchone()[0]
            cur.execute("SELECT source_used, count(*) FROM semantic_observations"
                        " GROUP BY 1")
            after_by_src = dict(cur.fetchall())

            conn.commit()
            print(f"\nsemantic_observations: {before_total} -> {after_total}"
                  f" (inserted {after_total - before_total})")
            if registry_result is not None:
                print(f"registry short_window: updated {registry_result[0]}"
                      f" existing, created {registry_result[1]} new"
                      " (verified=false)")
            if failed:
                print("FAILED UNITS:")
                for s, e in failed:
                    print(f"  - {s}: {e}")
            # zero-damage check: only the 12 batch-2 sources may move
            touched = {s for s in set(before_by_src) | set(after_by_src)
                       if before_by_src.get(s, 0) != after_by_src.get(s, 0)}
            unexpected = sorted(touched - set(EXTRACTORS))
            print("sources changed: "
                  + ", ".join(f"{s}:{before_by_src.get(s, 0)}->{after_by_src.get(s, 0)}"
                              for s in sorted(touched)))
            print(f"unexpected sources touched: {unexpected or 'NONE'}")

            # new concepts created by this run (verified=false)
            cur.execute("SELECT code, source FROM concepts WHERE code = ANY(%s)"
                        " AND verified = false AND deprecated = false"
                        " ORDER BY code",
                        ([c[0] for c in CONSTRUCT_CONCEPTS],))
            print("\nnew concepts (verified=false, naming gate = main agent):")
            for code, source in cur.fetchall():
                print(f"  - {code} ({source})")
            print("\nnotes:")
            for n in notes:
                print("  -", n)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
