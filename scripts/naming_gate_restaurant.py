#!/usr/bin/env python3
"""Restaurant-vertical wave-0 — naming quality gate (18 concepts).

Same mechanism as scripts/naming_gate_batch2.py (no new tables, no invented
pipeline):

1. ``concepts`` naming-layer fields (name_zh/name_en/unit/frequency/category)
   + ``verified`` boolean — one SQL transaction; a pre-change snapshot is
   written to output/supply-funnel/naming-before-snapshot-restaurant.json.
2. registry mirror (``registry_entries``, anchor
   ``fd_open_data/concepts/code/<code>``) via the protocol's
   ``PostgresRegistryStore``: upsert → ``set_verified(verified_by="rule:naming-gate")``
   (the store applies its own three-field gate: semantic_code + bilingual names).

Wave-0 specific naming revisions (all adjudicated against the real payloads,
2026-11-07):

* **6 nbs-stats concepts**: frequency was written ``month`` by the extractor;
  every other monthly concept in the store uses ``monthly`` — normalized here
  so the frequency vocabulary stays single-valued (reads/filters rely on it).
* **census-eits**: the landed series is MARTS ``44X72`` = *retail trade and
  food services total*, **not** food-service sales alone. The original code
  ``retail.us.foodservice.monthly`` / name 美国餐饮零售销售额 claimed a
  narrower caliber than the payload carries → renamed to
  ``retail.us.retail_food_services_total.monthly`` /
  美国零售与餐饮服务总销售额. (Restaurant-only MRTS category ``722`` is a
  follow-up, recorded in the wave-0 debt ledger — not silently implied here.)
* **IMF PCPS palm/sugar**: unit is ``index_2016_100`` (a price *index*), so the
  names must say 指数/ index — otherwise a reader takes them for USD prices.
* **short_window**: the census series carries 14 periods (< 24 gate) because
  the rolling window is still accumulating → registered with an explicit
  ``short_window`` declaration in the registry provenance (12-23 gate,
  disposition short_window), same shape as extract_batch2's declarations.

Usage: python3 scripts/naming_gate_restaurant.py [--apply]   (default dry-run)
DSN: source ~/.config/finddata/dashboards.env → $FD_OPEN_DATA_DSN
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg

PROTOCOL_ROOT = "/Users/chengsishi/finddata/fd-open-data-protocol"
OUT_DIR = Path("/Users/chengsishi/finddata/fd-industry-data/output/supply-funnel")
SNAPSHOT_PATH = OUT_DIR / "naming-before-snapshot-restaurant.json"

#: wave-0 concepts produced by scripts/extract_restaurant_wave0.py (18)
WAVE0_CODES = [
    "agri.beef.cold_storage.bone_in.monthly",
    "agri.beef.cold_storage.boneless.monthly",
    "agri.beef.cold_storage.total.monthly",
    "agri.cattle.inventory.us.annual",
    "futures.dce.corn.settle.daily",
    "futures.dce.meal.settle.daily",
    "hicp.eu.catering.monthly",
    "price.cpi.us.food_at_home",
    "price.cpi.us.food_away_from_home",
    "price.palm_oil.imf.monthly",
    "price.sugar.imf.monthly",
    "retail.cn.catering.limitup.monthly",
    "retail.cn.catering.limitup.yoy",
    "retail.cn.catering.monthly",
    "retail.cn.catering.yoy",
    "retail.cn.goods.monthly",
    "retail.cn.social.monthly",
    "retail.us.retail_food_services_total.monthly",   # renamed from retail.us.foodservice.monthly
]

#: wave-1 ICO coffee indicator series (5, extracted 2026-11-07 after 点亮)
WAVE1_CODES = [
    "price.coffee.brazilian_naturals.daily",
    "price.coffee.colombian_milds.daily",
    "price.coffee.icip.daily",
    "price.coffee.other_milds.daily",
    "price.coffee.robustas.daily",
]

#: wave-1 restaurant-rpi 11 条（17 期 → short_window 档；用户裁决「有界回补到 12 期」放行）
RPI_CODES = [
    "rpi.us.cs_capital_expenditures.monthly",
    "rpi.us.cs_customer_traffic.monthly",
    "rpi.us.cs_labor.monthly",
    "rpi.us.cs_same_store_sales.monthly",
    "rpi.us.current_situation.monthly",
    "rpi.us.exp_business_conditions.monthly",
    "rpi.us.exp_capital_expenditures.monthly",
    "rpi.us.exp_same_store_sales.monthly",
    "rpi.us.exp_staffing.monthly",
    "rpi.us.expectations.monthly",
    "rpi.us.restaurant.monthly",
]

ALL_CODES = WAVE0_CODES + WAVE1_CODES + RPI_CODES + [
    # 波1 欧盟腿：NACE I56 营业额指数（sts_setu_m，319 期长序列）
    "services.eu.restaurant_turnover_index.monthly",
    # 波1 日本腿（JF 外食，全店 前年同月比；21 期 → short_window）
    "restaurant.foodservice_jp.sales_yoy",
    "restaurant.foodservice_jp.store_count_yoy",
    "restaurant.foodservice_jp.customer_count_yoy",
    "restaurant.foodservice_jp.spend_per_customer_yoy",
]
RENAMED = {"retail.us.foodservice.monthly": "retail.us.retail_food_services_total.monthly"}

NBS = [
    "retail.cn.catering.limitup.monthly",
    "retail.cn.catering.limitup.yoy",
    "retail.cn.catering.monthly",
    "retail.cn.catering.yoy",
    "retail.cn.goods.monthly",
    "retail.cn.social.monthly",
]

# ── 命名材料（键 = 改名前的库内 code；None/缺省 = 保持库内现值）───────────────
NAMING_PATCH = {
    **{code: {"frequency": "monthly"} for code in NBS},
    "price.palm_oil.imf.monthly": {
        "name_zh": "IMF 棕榈油价格指数",
        "name_en": "Palm oil price index (IMF PCPS)",
    },
    "price.sugar.imf.monthly": {
        "name_zh": "IMF 原糖价格指数",
        "name_en": "Raw sugar price index (IMF PCPS)",
    },
    "retail.us.foodservice.monthly": {
        "code": "retail.us.retail_food_services_total.monthly",
        "name_zh": "美国零售与餐饮服务总销售额",
        "name_en": "US retail trade & food services total sales (MARTS 44X72, NSA)",
    },
}

#: short_window 声明（12-23 期门槛）
#:   census 14 期=滚动窗首跑待攒期；RPI 17 期=单版 5 个月滚动窗 + months_back 有界回补
#:   （用户 2026-11-07 裁决「有界回补到 12 期」，故 17 期即放行并显式带声明）
SW_PERIODS = {
    "retail.us.retail_food_services_total.monthly": 14,
    **{code: 17 for code in RPI_CODES},
    **{code: 21 for code in (
        "restaurant.foodservice_jp.sales_yoy",
        "restaurant.foodservice_jp.store_count_yoy",
        "restaurant.foodservice_jp.customer_count_yoy",
        "restaurant.foodservice_jp.spend_per_customer_yoy",
    )},
}
SW_DECLARED_BY = "adjudication-review-2026-11-07-restaurant-wave0"
SW_DECLARED_IN = "add-restaurant-vertical task 1.2 / 2.3"

FIELDS = ("name_zh", "name_en", "unit", "frequency", "category")
COLUMNS = ("id, code, name_en, name_zh, category, unit, measure, frequency,"
           " entity_type, source, verified")
SNAPSHOT_COLS = ("id", "code", "name_en", "name_zh", "category", "unit",
                 "measure", "frequency", "entity_type", "source", "verified")


def sw_decl(periods: int) -> dict:
    return {"short_window": {"periods": periods, "gate": "12-23",
                             "disposition": "short_window",
                             "decided_by": SW_DECLARED_BY,
                             "declared_in": SW_DECLARED_IN}}


def six_field_complete(row) -> list[str]:
    missing = []
    for col in ("code", "name_zh", "name_en", "unit", "frequency", "category"):
        v = row.get(col)
        if v is None or str(v).strip() == "":
            missing.append(col)
    return missing


def load_rows(cur, codes):
    cur.execute(f"SELECT {COLUMNS} FROM concepts WHERE code = ANY(%s)", (codes,))
    return {r["code"]: r for r in cur.fetchall()}


def snapshot(cur, rows) -> None:
    ids = [r["id"] for r in rows.values()]
    cur.execute(
        f"SELECT {', '.join(SNAPSHOT_COLS)} FROM concepts WHERE id = ANY(%s) ORDER BY id",
        (ids,),
    )
    cols = [d.name for d in cur.description]
    snap = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.execute(
        "SELECT id, source_db, source_table, source_column, native_code, semantic_code,"
        " name_zh, name_en, unit, frequency, domain, provenance, verified, verified_at,"
        " verified_by FROM registry_entries"
        " WHERE source_db='fd_open_data' AND source_table='concepts'"
        " AND native_code = ANY(%s) ORDER BY native_code",
        (list(RENAMED),),
    )
    reg_cols = [d.name for d in cur.description]
    reg = []
    for r in cur.fetchall():
        d = dict(zip(reg_cols, r))
        d["provenance"] = json.loads(json.dumps(d["provenance"]))
        reg.append(d)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(
        json.dumps({"concepts": snap, "registry_mirror": reg},
                   ensure_ascii=False, indent=1, default=str),
        encoding="utf-8",
    )
    print(f"snapshot -> {SNAPSHOT_PATH}"
          f"（concepts {len(snap)} 行 + registry {len(reg)} 行）")


def main() -> int:
    apply_mode = "--apply" in sys.argv
    dsn = os.environ.get("FD_OPEN_DATA_DSN")
    if not dsn:
        print("FATAL: $FD_OPEN_DATA_DSN 未设置（source ~/.config/finddata/dashboards.env）")
        return 2

    conn = psycopg.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    lookup = ALL_CODES + list(RENAMED)
    rows = load_rows(cur, lookup)
    problems, skipped_verified = [], []
    for code in ALL_CODES:
        row = rows.get(code) or rows.get(
            next((old for old, new in RENAMED.items() if new == code), code))
        if row is None:
            problems.append(f"{code} 不存在")
        elif row["verified"]:
            skipped_verified.append(code)   # 幂等：已过门的批次不再重复放行
    if problems:
        print("FATAL 前置断言失败:", *problems, sep="\n  ")
        return 3
    if skipped_verified:
        print(f"跳过已 verified {len(skipped_verified)} 条（幂等）: "
              + ", ".join(skipped_verified))
    active = [c for c in ALL_CODES if c not in set(skipped_verified)]
    if not active:
        print("本批无可放行概念（全部已 verified）——无事可做")
        conn.close()
        return 0

    # 关键锚点行统一以「库内现值 code」为键
    by_code = {}
    for code in active:
        old = next((o for o, n in RENAMED.items() if n == code), code)
        by_code[code] = rows.get(old, rows.get(code))

    # 六字段预检（按 patch 后拟值）
    incomplete, plans = {}, []
    for code, row in by_code.items():
        patch = NAMING_PATCH.get(row["code"], {})
        merged = {**{k: row[k] for k in ("code",) + FIELDS}, **patch}
        missing = six_field_complete(merged)
        if missing:
            incomplete[code] = missing
        plans.append((row, merged, patch))
    if incomplete:
        print("FATAL: 六字段不齐（不得放行）:", incomplete)
        return 6

    # code 唯一性 + registry semantic_code 占用预检
    cur.execute(
        "SELECT code, count(*) FROM concepts WHERE NOT deprecated AND code = ANY(%s)"
        " GROUP BY code HAVING count(*) > 1", (active,))
    if cur.fetchall():
        print("FATAL: 目标 code 在非 deprecated 概念内重复")
        return 4
    cur.execute(
        "SELECT semantic_code, source_db, native_code FROM registry_entries"
        " WHERE semantic_code = ANY(%s) AND source_db <> 'fd_open_data'",
        (active,))
    taken = cur.fetchall()
    if taken:
        print(f"FATAL: semantic_code 已被其他源占用: {taken}")
        return 5

    print("== 命名门计划（18 概念）==")
    for row, merged, patch in plans:
        changes = {k: (row.get(k), v) for k, v in patch.items() if row.get(k) != v}
        sw = (f" [short_window {SW_PERIODS[merged['code']]}期]"
              if merged["code"] in SW_PERIODS else "")
        print(f"  [PASS→verified] id={row['id']} {row['code']} (source={row['source']}){sw}")
        for f, (old, new) in changes.items():
            print(f"      {f}: {old!r} -> {new!r}")
        if not changes:
            print("      (六字段已齐，零字段变更，仅置 verified)")

    if not apply_mode:
        print("\nDRY-RUN：未写库。加 --apply 执行。")
        conn.close()
        return 0

    snapshot(cur, by_code)

    ids = [row["id"] for row, _m, _p in plans]
    try:
        for row, _merged, patch in plans:
            if not patch:
                continue
            sets = ", ".join(f"{f} = %s" for f in patch)
            cur.execute(f"UPDATE concepts SET {sets} WHERE id = %s",
                        (*patch.values(), row["id"]))
        cur.execute("UPDATE concepts SET verified = TRUE WHERE id = ANY(%s)", (ids,))
        conn.commit()
        patched = sum(1 for _r, _m, patch in plans if patch)
        print(f"\nconcepts 已提交：{patched} 行命名修订，{len(ids)} 行 verified=TRUE")
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"FATAL: concepts 更新失败已回滚（零残留）: {exc}")
        conn.close()
        return 7

    # ── registry 镜像 ────────────────────────────────────────────────────────
    sys.path.insert(0, PROTOCOL_ROOT)
    from fd_open_data_protocol.registry_store import PostgresRegistryStore  # noqa: E402
    from fd_open_data_protocol.schema import RegistryEntry  # noqa: E402

    rows_after = load_rows(cur, active)
    entries = []
    for code, row in sorted(rows_after.items()):
        provenance = {"measure": row["measure"], "entity_type": row["entity_type"],
                      "source": row["source"], "code": row["code"]}
        if code in SW_PERIODS:
            provenance.update(sw_decl(SW_PERIODS[code]))
        entries.append(RegistryEntry(
            source_db="fd_open_data", source_table="concepts", source_column="code",
            native_code=row["code"], semantic_code=row["code"],
            name_zh=row["name_zh"], name_en=row["name_en"], unit=row["unit"],
            frequency=row["frequency"], domain=row["category"],
            provenance=provenance, verified=False,
        ))
    store = PostgresRegistryStore(dsn)
    stats = store.upsert_entries(entries)
    print(f"registry upsert: new={stats.new} updated={stats.updated} "
          f"unchanged={stats.unchanged} demoted={stats.demoted}")
    if stats.demoted:
        print("FATAL: 出现命名漂移降级——人工检查 registry_entries")
        conn.close()
        return 9
    anchors = [("fd_open_data", "concepts", "code", e.native_code) for e in entries]
    n = store.set_verified(anchors=anchors, verified_by="rule:naming-gate")
    print(f"registry set_verified: {n} / {len(anchors)} (verified_by=rule:naming-gate)")
    if n != len(anchors):
        print("WARN: 部分锚点未被置 verified，逐条核查")

    # ── 复核 ─────────────────────────────────────────────────────────────────
    rows_final = load_rows(cur, active)
    bad_six = [(c, six_field_complete(r)) for c, r in rows_final.items()
               if six_field_complete(r)]
    bad_v = [c for c, r in rows_final.items() if not r["verified"]]
    print(f"concepts 复核: 六字段不齐={bad_six or '无'}; 未 verified={bad_v or '无'}")
    cur.execute(
        "SELECT native_code, provenance ? 'short_window' AS sw, verified, verified_by"
        " FROM registry_entries WHERE source_db='fd_open_data'"
        " AND source_table='concepts' AND native_code = ANY(%s) ORDER BY native_code",
        (active,))
    reg = cur.fetchall()
    print(f"registry 读路径: {len(reg)} 条锚点")
    for r in reg:
        if r["sw"]:
            print(f"  short_window: {r['native_code']} sw={r['sw']} "
                  f"verified={r['verified']} by={r['verified_by']}")
    cur.execute("SELECT count(*) FROM semantic_observations s JOIN concepts c"
                " ON c.id = s.concept_id WHERE c.code = ANY(%s)", (active,))
    print(f"观测行合计: {cur.fetchone()['count']} 行")
    conn.close()
    print(f"\nDONE：{len(anchors)} 条过命名门（concepts + registry 双置 verified）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
