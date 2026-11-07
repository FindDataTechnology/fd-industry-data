#!/usr/bin/env python3
"""EU 花卉主面积 — ``eurostat-api`` 的花卉垂直线抽取器（add-flower-vertical 3.3）。

真源：Eurostat ``apro_cpsh1``（Crop production in EU standard humidity），作物码
``N0000`` = "Flowers and ornamental plants (excluding nurseries)"，口径
``MAR_THS_HA`` = **Main area**（主面积，千公顷）。

## 三条口径纪律，每条都有实测依据（2026-11-08 直连 HTTP 200）

1. **只有主面积有值**：``AR_THS_HA``（播种/收获面积）、``HPRD_HUMD_EU_THS_T``
   （产量）、``YLD_HUMD_EU_T_HA``（单产）对该作物**均返回 0 值**。所以本序列
   只能叫「主面积」，**绝不能标成产量**——先按产量口径查过才改判的。
2. **千公顷 → 公顷（×1000）**：协议词表 ``hectare`` 是基本单位，AIPH 面积序列
   也存公顷；不折算会让两个来源的面积在同一张表里差 1000 倍。
3. **EU 三个口径不可相加**：``EU27_2020`` 是现役口径，历史代入的 ``EU`` /
   ``EU28`` 覆盖期不同（同一年的值可能相同）；本抽取器只取 ``EU27_2020``，
   其余两个不取，避免「相加得双倍欧盟」。

## 为什么直接调单元命令而不读 crawl_items

与酒店线同一先例（``scripts/hotel_extractors/eurostat_accommodation.py``）：
该命令**不参与** ``run_eurostat_api`` 的 ``--limit 100`` 轮转（8 geo × 12 年
≈ 96 行，轮转只能落下头 10 行，会存成半截序列），所以抽取器直接以独立
limit 调用单元入口。失败即抛，绝不落成 0 行。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]          # fd-industry-data/
_UNIT = _ROOT / "spiders" / "eurostat-api" / "spider.py"

if str(_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_ROOT / "scripts"))

SOURCE = "eurostat-api"
CODE = "area.flower.all.planted"

#: 概念五元组（与 concepts.yaml 的 PlantedArea 族逐字一致）
CONCEPTS: list[tuple] = [
    (
        CODE,
        "欧盟花卉主面积（含观赏植物，不含苗圃）",
        "EU main area of flowers and ornamental plants (excluding nurseries)",
        "供给能力",
        "hectare",
        "value",
        "annual",
        "country",
        SOURCE,
    ),
]

#: 上游 geo → 库内 country 实体 code（逐字映射，禁止按名相似度猜）
GEOS = ("EU27_2020", "NL", "IT", "DE", "FR", "ES", "PL", "BE")

#: 千公顷 → 公顷
THOUSAND = 1000.0


def _load_unit():
    """按路径加载 ``spiders/eurostat-api/spider.py``（目录名带连字符，不可 import）。"""
    name = "unit_eurostat_api_flower_area"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _UNIT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _entity_ids(cur, codes: set[str]) -> dict[str, int]:
    """country 实体 id（缺失的由 register_flower_entities.py 负责建，不在抽取器里造）。"""
    if not codes:
        return {}
    cur.execute(
        "select code, id from entities where entity_type = 'country' and code = any(%s)",
        (sorted(codes),))
    return {code: eid for code, eid in cur.fetchall()}


def extract(cur, notes: list[str]) -> list[tuple]:
    """返回 ``(code, entity_type, entity_id, date, granularity, value)`` 行。

    上游缺失年份不补、不插值；未注册的 geo 跳过并记 note（不静默丢弃）。
    """
    unit = _load_unit()
    rows = unit.run_eurostat_flower_area(limit=500)
    if not rows:
        raise RuntimeError(
            "eurostat-api flower area: 上游返回 0 行（apro_cpsh1/N0000/MAR_THS_HA）"
            "——实测口径应有 8 geo × 10 年，取不到即视为失败，不落 0 行")

    geos = {r["geo"] for r in rows if r.get("geo")}
    ids = _entity_ids(cur, geos)
    missing = sorted(geos - set(ids))
    if missing:
        notes.append(
            f"eurostat-api: 国别实体未注册，跳过 {missing}（先跑 "
            "scripts/register_flower_entities.py --apply）")

    out: list[tuple] = []
    skipped_dims = 0
    for r in rows:
        eid = ids.get(r.get("geo", ""))
        if eid is None:
            continue
        period = str(r.get("period") or "")
        if len(period) != 4 or not period.isdigit():
            skipped_dims += 1
            continue
        # 只认主面积口径：若上游以后新增其它 strucpro，本抽取器必须拒绝，
        # 否则会把产量/单产写进「面积」概念（口径混淆比缺数更坏）。
        if (r.get("crops"), r.get("strucpro")) != ("N0000", "MAR_THS_HA"):
            skipped_dims += 1
            continue
        value = r.get("value")
        if value is None:
            continue
        out.append((CODE, "country", eid, f"{period}-01-01", "year",
                    round(float(value) * THOUSAND, 3)))

    if skipped_dims:
        notes.append(
            f"eurostat-api: 跳过 {skipped_dims} 行非 (N0000, MAR_THS_HA) 或非年份期次的行"
            "——本概念只装主面积")
    return out
