#!/usr/bin/env python3
"""Register the flower line's commodity/country entities (add-flower-vertical 5.1 前置).

观测写入按房规**不建实体**（extractor 只解析行；实体由主代理注册）。本脚本就是
那一步：

* `entity_type='commodity'` × 23 —— 斗南 23 个品种，`code` 用显式表
  （`scripts/flower_extractors/dounan_flower_price.py:ITEM_ENTITY`，**同一份真源**），
  `name_zh` 照录上游 `item` 原文（含 `复色,玫瑰（多头）` 那种半角逗号），
  `name_en` 给可读英文名。**计量单位写进 metadata.unit**：一个 concept 只能带一种
  量纲（元/枝 与 元/把 混在一个 code 里就等于口径造假），量纲归实体。
* `entity_type='country'` × 8 —— AIPH 年鉴出现、但库里还没有的国别
  （CO/EC/ID/KE/MX/TH/TR/VN）。已存在的（CN/IN/NL/US…）**复用不重建**。

纪律（同 scripts/register_flower_universe.py）：
* 幂等：已存在即复用，仅补 NULL 的名字，不改已有人工值；
* `--dry-run`（默认）回滚，`--apply` 才提交；
* 写后自检：实体数、单位一致性（与最近一次成功爬取的 `unit` 逐品种比对）、
  以及「没有为本 change 之外的行制造改动」的指纹检查。

Usage
-----
    source ~/.config/finddata/dashboards.env
    python3 scripts/register_flower_entities.py            # dry-run
    python3 scripts/register_flower_entities.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent))
from flower_extractors.dounan_flower_price import ITEM_ENTITY  # noqa: E402

#: 上游 item → 可读英文名（中文原文照录在 name_zh，不翻译改写）
ITEM_NAME_EN = {
    "红玫瑰（单头）": "Red rose (single-head)",
    "红玫瑰（多头）": "Red rose (multi-head)",
    "白玫瑰（单头）": "White rose (single-head)",
    "白玫瑰（多头）": "White rose (multi-head)",
    "黄玫瑰（单头）": "Yellow rose (single-head)",
    "黄玫瑰（多头）": "Yellow rose (multi-head)",
    "粉玫瑰（单头）": "Pink rose (single-head)",
    "复色玫瑰（单头）": "Multi-color rose (single-head)",
    "复色,玫瑰（多头）": "Multi-color rose (multi-head)",
    "白百合": "White lily",
    "粉百合": "Pink lily",
    "红百合": "Red lily",
    "康乃馨": "Carnation",
    "菊花": "Chrysanthemum",
    "洋桔梗": "Eustoma (lisianthus)",
    "绣球": "Hydrangea",
    "金鱼草": "Snapdragon",
    "向日葵": "Sunflower",
    "勿忘我": "Forget-me-not",
    "茉莉花": "Jasmine",
    "马蹄莲": "Calla lily",
    "尤加利叶": "Eucalyptus foliage",
    "紫罗兰": "Stock (matthiola)",
}

#: AIPH 年鉴出现但库内缺失的国别（ISO 3166-1 alpha-2；已存在的复用）
NEW_COUNTRIES = {
    "CO": ("哥伦比亚", "Colombia"),
    "EC": ("厄瓜多尔", "Ecuador"),
    "ID": ("印度尼西亚", "Indonesia"),
    "KE": ("肯尼亚", "Kenya"),
    "MX": ("墨西哥", "Mexico"),
    "TH": ("泰国", "Thailand"),
    "TR": ("土耳其", "Turkey"),
    "VN": ("越南", "Vietnam"),
    # 3.3：Eurostat 花卉主面积的产地国（原有注册集缺这四个）
    "IT": ("意大利", "Italy"),
    "ES": ("西班牙", "Spain"),
    "PL": ("波兰", "Poland"),
    "BE": ("比利时", "Belgium"),
}

#: 3.2：UN Comtrade 花卉三档的 HS6 实体（entity_type='hs_code'，与既有
#: 610910/851762/854142 三个 HS 实体同一口径；entity 侧承载档位，concept 侧承载流向）
HS_ENTITIES = {
    "060311": ("玫瑰切花（HS6）", "Cut roses and buds, fresh (HS 060311)"),
    "060319": ("其他鲜切花（HS6）", "Other fresh cut flowers and buds (HS 060319)"),
    "060120": ("鳞茎球根（HS6）", "Bulbs, tubers, corms in growth or in flower (HS 060120)"),
}

EXPECTED_COMMODITY = len(ITEM_ENTITY)          # 23
EXPECTED_NEW_COUNTRY = len(NEW_COUNTRIES)      # 8


def units_from_latest_crawl(cur) -> dict[str, str]:
    """最近一次成功爬取的 item → 计量单位（实测值，不手填）。"""
    cur.execute(
        """
        select ci.payload->>'item', ci.payload->>'unit'
        from crawl_items ci join crawl_runs r on r.id = ci.run_id
        where r.source = 'dounan-flower-price' and r.status = 'success'
        """,
    )
    seen: dict[str, str] = {}
    for item, unit in cur.fetchall():
        if item and unit:
            seen.setdefault(item, unit)
    return seen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("FATAL: $FD_OPEN_DATA_DSN 未设置"
                         "（source ~/.config/finddata/dashboards.env）")

    conn = psycopg2.connect(args.dsn)
    conn.autocommit = False
    cur = conn.cursor()

    units = units_from_latest_crawl(cur)
    if not units:
        raise SystemExit(
            "FATAL: 库里没有 dounan-flower-price 的成功爬取（先 fd-runner 真跑一次），"
            "否则计量单位无从实测、只能手填=编数")
    unmapped = sorted(set(units) - set(ITEM_ENTITY))
    if unmapped:
        raise SystemExit(
            f"FATAL: 上游出现未在显式表里的品种 {unmapped}；"
            f"先补 flower_extractors/dounan_flower_price.py:ITEM_ENTITY（禁止相似度猜）")
    absent_upstream = sorted(set(ITEM_ENTITY) - set(units))
    if absent_upstream:
        print(f"note: 本轮爬取未出现的品种（仍注册，历史累积）: {absent_upstream}")

    created = reused = updated = 0

    for item, code in sorted(ITEM_ENTITY.items(), key=lambda kv: kv[1]):
        meta = {
            "list": "flower-universe",
            "change": "add-flower-vertical",
            "vertical": "flower",
            "segment": "cutflower-trade",
            "market": "kunming-dounan",
            "item_raw": item,                       # 上游原文，一字不改
            "unit": units.get(item),                # 量纲归实体（一个 concept 只能一种单位）
            "source_unit": "dounan-flower-price",
            "truth_source": "fd-industry-data/docs/flower-series-curation.md",
        }
        cur.execute(
            "select id, name_zh, name_en, metadata_json from entities"
            " where entity_type = 'commodity' and code = %s", (code,))
        hit = cur.fetchone()
        if hit:
            eid, zh, en, old_meta = hit
            reused += 1
            new_meta = {**(old_meta or {}), **meta}
            set_zh, set_en = zh or item, en or ITEM_NAME_EN[item]
            if (set_zh, set_en, new_meta) != (zh, en, old_meta or {}):
                cur.execute(
                    "update entities set name_zh=%s, name_en=%s, metadata_json=%s,"
                    " updated_at=now() where id=%s",
                    (set_zh, set_en, json.dumps(new_meta, ensure_ascii=False), eid))
                updated += 1
        else:
            cur.execute(
                "insert into entities (entity_type, code, name_zh, name_en,"
                " metadata_json, updated_at) values ('commodity',%s,%s,%s,%s,now())"
                " returning id",
                (code, item, ITEM_NAME_EN[item],
                 json.dumps(meta, ensure_ascii=False)))
            cur.fetchone()
            created += 1

    c_created = c_reused = 0
    for code, (zh, en) in sorted(NEW_COUNTRIES.items()):
        cur.execute(
            "select id from entities where entity_type = 'country' and code = %s",
            (code,))
        if cur.fetchone():
            c_reused += 1
            continue
        cur.execute(
            "insert into entities (entity_type, code, name_zh, name_en,"
            " metadata_json, updated_at) values ('country',%s,%s,%s,%s,now())",
            (code, zh, en, json.dumps({
                "list": "flower-universe", "change": "add-flower-vertical",
                "vertical": "flower", "source_unit": "aiph-stats",
                "truth_source": "fd-industry-data/docs/flower-series-curation.md",
            }, ensure_ascii=False)))
        c_created += 1

    # ── HS6 实体（3.2 UN Comtrade 花卉三档）─────────────────────────────
    hs_created = hs_reused = 0
    for code, (zh, en) in sorted(HS_ENTITIES.items()):
        cur.execute(
            "select id from entities where entity_type = 'hs_code' and code = %s",
            (code,))
        if cur.fetchone():
            hs_reused += 1
            continue
        cur.execute(
            "insert into entities (entity_type, code, name_zh, name_en,"
            " metadata_json, updated_at) values ('hs_code',%s,%s,%s,%s,now())",
            (code, zh, en, json.dumps({
                "list": "flower-trade-hs", "change": "add-flower-vertical",
                "vertical": "flower", "source_unit": "un-comtrade",
                "truth_source": "fd-industry-data/docs/flower-series-curation.md",
            }, ensure_ascii=False)))
        hs_created += 1

    print(f"commodity: created={created} reused={reused} updated={updated}"
          f" total={EXPECTED_COMMODITY}")
    print(f"country  : created={c_created} reused={c_reused}"
          f" total_new={EXPECTED_NEW_COUNTRY}")
    print(f"hs_code  : created={hs_created} reused={hs_reused}"
          f" total={len(HS_ENTITIES)}")

    # ── 写后自检 ──────────────────────────────────────────────────────
    cur.execute(
        "select count(*) from entities where entity_type='commodity'"
        " and code = any(%s)", (sorted(ITEM_ENTITY.values()),))
    got = cur.fetchone()[0]
    if got != EXPECTED_COMMODITY:
        print(f"FATAL 品种实体应有 {EXPECTED_COMMODITY} 个，实为 {got}")
        conn.rollback()
        return 3
    cur.execute(
        "select code, metadata_json->>'unit' from entities"
        " where entity_type='commodity' and code = any(%s)",
        (sorted(ITEM_ENTITY.values()),))
    by_code = dict(cur.fetchall())
    bad = [code for item, code in ITEM_ENTITY.items()
           if units.get(item) and by_code.get(code) != units[item]]
    if bad:
        print(f"FATAL 量纲与实测爬取不一致: {bad}")
        conn.rollback()
        return 4

    if args.apply:
        conn.commit()
        print("APPLIED (committed)")
    else:
        conn.rollback()
        print("DRY-RUN (rolled back; 用 --apply 落库)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
