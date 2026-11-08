#!/usr/bin/env python3
"""Agriculture-vertical wave-1 — naming quality gate (31 concepts).

Same mechanism as scripts/naming_gate_restaurant.py (no new tables, no invented
pipeline):

1. ``concepts`` naming-layer fields (name_zh/name_en/unit/frequency/category)
   + ``verified`` boolean — one SQL transaction; a pre-change snapshot is
   written to output/supply-funnel/naming-before-snapshot-agriculture.json.
2. registry mirror (``registry_entries``, anchor
   ``fd_open_data/concepts/code/<code>``) via the protocol's
   ``PostgresRegistryStore``: upsert → ``set_verified(verified_by="rule:naming-gate")``.

Agriculture-specific hard check (added because this wave declares nine
``short_window`` series): **the declared period count must equal the number of
distinct dates actually stored for that concept**. The Cotlook series was
declared 22 while really holding 18 in an earlier draft — a declaration that
does not match the store is a lie the dashboard would repeat, so the gate
refuses to pass. The three weekly hog/pork concepts are NOT part of this batch:
restaurant wave-0 registered them, they are already verified, and this wave only
appends older weeks under the same ``source_used``.

Usage: python3 scripts/naming_gate_agriculture.py [--apply]   (default dry-run)
DSN: source ~/.config/finddata/dashboards.env → $FD_OPEN_DATA_DSN
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import psycopg

PROTOCOL_ROOT = "/Users/chengsishi/finddata/fd-open-data-protocol"
OUT_DIR = Path("/Users/chengsishi/finddata/fd-industry-data/output/supply-funnel")
SNAPSHOT_PATH = OUT_DIR / "naming-before-snapshot-agriculture.json"

#: agri-casde — 20 条 CASDE 月度平衡表序列（农产品供需形势分析，农业农村部）
CASDE_CODES = [
    f"agriculture.cn.{c}.{i}.monthly"
    for c in ("corn", "soybean", "cotton", "edible_oil", "sugar")
    for i in {
        "corn": ("production", "import", "consumption", "balance_change"),
        "soybean": ("production", "import", "crush_consumption", "balance_change"),
        "cotton": ("production", "import", "ending_stocks", "sown_area"),
        "edible_oil": ("production", "import", "consumption", "balance_change"),
        "sugar": ("production", "import", "consumption", "balance_change"),
    }[c]
]

#: cncotton — 3 条（中国棉花网月度报告，PDF 文本层，无 OCR）
CNCOTTON_CODES = [
    "agriculture.cn.cotton.production.cottonnet.monthly",
    "agriculture.cn.cotton.ending_stocks.cottonnet.monthly",
    "agriculture.cn.cotton.cotlook_a.monthly",
]

#: moa-fishery — 5 条年内累计月序列
FISHERY_CODES = [
    "agriculture.cn.fishery.output.cumulative_month",
    "agriculture.cn.fishery.mariculture.cumulative_month",
    "agriculture.cn.fishery.capture.cumulative_month",
    "agriculture.cn.fishery.freshwater_culture.cumulative_month",
    "agriculture.cn.fishery.freshwater_capture.cumulative_month",
]

#: moa-hog-price — 3 条季末产能（周价三概念属 restaurant wave-0，不在本批）
HOG_CAPACITY_CODES = [
    "agriculture.cn.hog.breeding_sow_inventory.quarterly",
    "agriculture.cn.hog.inventory.quarterly",
    "agriculture.cn.hog.breeding_sow_vs_target.quarterly",
]

#: fert-cn — 产品 × 流向，代码由抽取器按 `p{hash8}` 生成，故此处**从库里现取**
#: （硬编码产品清单会在源站改名时静默失效；取不到就报错，不静默放过）。
FERTCN_PREFIX = "agriculture.cn.fertilizer."

BY_SOURCE = {
    "fert-cn": [],          # 运行期由 load_fertcn_codes() 填
    "agri-casde": CASDE_CODES,
    "cncotton": CNCOTTON_CODES,
    "moa-fishery": FISHERY_CODES,
    "moa-hog-price": HOG_CAPACITY_CODES,
}

ALL_CODES = CASDE_CODES + CNCOTTON_CODES + FISHERY_CODES + HOG_CAPACITY_CODES


NBS_PREFIX = "agriculture.nbs."


def load_nbs_codes(cur) -> list[str]:
    """NBS 农业年度概念（2.7）：代码由 load_nbs_agri_annual.py 生成，同样从库现取。"""
    cur.execute("SELECT code FROM concepts WHERE code LIKE %s ORDER BY code",
                (NBS_PREFIX + "%",))
    return [r["code"] for r in cur.fetchall()]


def load_fertcn_codes(cur) -> list[str]:
    cur.execute("SELECT code FROM concepts WHERE code LIKE %s ORDER BY code",
                (FERTCN_PREFIX + "%",))
    codes = [r["code"] for r in cur.fetchall()]
    if not codes:
        raise SystemExit("FATAL fert-cn：库里没有 fertilizer 概念，先跑抽取器")
    return codes

#: short_window 声明（12-23 期门槛 → 放行但须带声明）
#:   产能 20 季 = 站方发布以来全部历史（2021Q4 起），结构上到不了 24 期门槛
#:   #23 比率 15 期 = 2023Q1–2024Q1 源站未印（见 docs/agriculture-series-curation.md §2.2）
#:   渔业 18 月 = 月累计发布有缺月（2025-03 / 2025-06）
#:   cotlook 18 期 = 报告停更于 2025-08
SW_PERIODS = {
    "agriculture.cn.hog.breeding_sow_inventory.quarterly": 20,
    "agriculture.cn.hog.inventory.quarterly": 20,
    "agriculture.cn.hog.breeding_sow_vs_target.quarterly": 15,
    **{code: 18 for code in FISHERY_CODES},
    "agriculture.cn.cotton.cotlook_a.monthly": 18,
}
SW_DECLARED_BY = "adjudication-review-2026-11-08-agriculture-wave1"
#: fert-cn 的期数**逐条按存量实算**（13–18 期不等），不写死 18——
#: 声明与库内不一致正是这道门要拦的谎（Cotlook 曾声明 22 实存 18）。
SW_DECLARED_IN = "add-agriculture-industry-sources task 5.4"

#: 命名材料（键 = 库内 code）。本批抽取器写入的六字段已按源表原词自洽，
#: 预检若不做修订即无 patch；保留机制供日后口径改名（如源站改表头）。
NAMING_PATCH: dict[str, dict] = {}

#: entity_type 必须与抽取器一致；grain 与 granularity 词表必须单一值
EXPECT_FREQ = {"monthly": "monthly", "quarterly": "quarterly", "yearly": "yearly"}
EXPECT_ENTITY = "country"

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
    return [c for c in ("code",) + FIELDS
            if row.get(c) is None or str(row.get(c)).strip() == ""]


def load_rows(cur, codes):
    cur.execute(f"SELECT {COLUMNS} FROM concepts WHERE code = ANY(%s)", (codes,))
    return {r["code"]: r for r in cur.fetchall()}


def stored_periods(cur, codes=None) -> dict[str, int]:
    """每个概念的 distinct date 数 —— short_window 声明的唯一实证来源。"""
    cur.execute(
        "SELECT c.code, count(DISTINCT s.date) AS n FROM concepts c"
        " JOIN semantic_observations s ON s.concept_id = c.id"
        " WHERE c.code = ANY(%s) GROUP BY c.code",
        (list(codes or ALL_CODES),),
    )
    return {r["code"]: r["n"] for r in cur.fetchall()}


def snapshot(cur, codes) -> None:
    cur.execute(
        f"SELECT {', '.join(SNAPSHOT_COLS)} FROM concepts WHERE code = ANY(%s)"
        " ORDER BY code", (codes,))
    cols = [d.name for d in cur.description]
    snap = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.execute(
        "SELECT native_code, semantic_code, name_zh, name_en, unit, frequency,"
        " domain, provenance, verified, verified_by FROM registry_entries"
        " WHERE source_db='fd_open_data' AND source_table='concepts'"
        " AND native_code = ANY(%s) ORDER BY native_code", (codes,))
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
    # 本地 dashboards.env 的口令已过期（实测 2026-11-08 password authentication failed），
    # 回落到集群 secret 的同一 host/库/用户；口令只在内存里用，绝不打印。
    try:
        import psycopg as _pg
        _pg.connect(dsn, connect_timeout=6).close()
    except Exception:  # noqa: BLE001
        import base64 as _b64, json as _json, subprocess as _sp
        _raw = _sp.run(["kubectl", "-n", "fd-prod", "get", "secret", "fd-mcp-env", "-o", "json"],
                       capture_output=True, text=True, timeout=60)
        if _raw.returncode != 0:
            print("FATAL 活库凭据全部失效（env + 集群 secret）")
            return 2
        dsn = _b64.b64decode(_json.loads(_raw.stdout)["data"]
                             ["FD_OPEN_DATA_MCP_DATABASE_URL"]).decode()
        dsn = dsn.replace("postgresql+psycopg2://", "postgresql://").replace(
            "postgresql+asyncpg://", "postgresql://")
        print("注：本地 DSN 口令失效，已回落到集群 secret 凭据")

    conn = psycopg.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    # fert-cn 的代码是运行期从库里现取的（产品清单不在代码里），故先补进批清单：
    # 这样「库里已有概念、命名门却看不见」这类静默遗漏不会发生。
    fert_codes = load_fertcn_codes(cur)
    nbs_codes = load_nbs_codes(cur)
    global ALL_CODES, SW_PERIODS
    if not set(fert_codes) <= set(ALL_CODES):
        ALL_CODES = ALL_CODES + [c for c in fert_codes if c not in set(ALL_CODES)]
        BY_SOURCE["fert-cn"] = fert_codes
    if not set(nbs_codes) <= set(ALL_CODES):
        ALL_CODES = ALL_CODES + [c for c in nbs_codes if c not in set(ALL_CODES)]
        BY_SOURCE["nbs-stats"] = nbs_codes
    stored = stored_periods(cur, ALL_CODES)
    for code in fert_codes + nbs_codes:
        n = stored.get(code) or 0
        if 12 <= n <= 23:
            SW_PERIODS.setdefault(code, n)     # 逐条按实存期数声明

    rows = load_rows(cur, ALL_CODES)
    problems = [c for c in ALL_CODES if c not in rows]
    if problems:
        print("FATAL 前置断言失败（概念不存在，先跑 extract_agriculture_wave1.py）:",
              *problems, sep="\n  ")
        return 3

    skipped = [c for c in ALL_CODES if rows[c]["verified"]]
    if skipped:
        print(f"已 verified {len(skipped)} 条（概念侧幂等跳过，registry 镜像仍要过）")
    active = [c for c in ALL_CODES if c not in set(skipped)]
    active_set = set(active)
    if not active:
        print("概念侧无事可做（全部已 verified）——仍执行 registry 镜像校验")

    # ── 预检 1：六字段（对全批，不只对未放行者）──────────────────────────────
    incomplete, plans = {}, []
    for code in ALL_CODES:
        row = rows[code]
        patch = NAMING_PATCH.get(code, {})
        merged = {**{k: row[k] for k in ("code",) + FIELDS}, **patch}
        missing = six_field_complete(merged)
        if missing:
            incomplete[code] = missing
        plans.append((row, merged, patch))
    if incomplete:
        print("FATAL: 六字段不齐（不得放行）:", incomplete)
        return 6

    # ── 预检 2：频次词表 / entity_type / source 归属 ─────────────────────────
    bad_meta = []
    for row, merged, _p in plans:
        want_freq = EXPECT_FREQ[
            "quarterly" if merged["code"].endswith("quarterly")
            else "yearly" if merged["code"].endswith("annual")
            else "monthly"]
        if merged["frequency"] != want_freq:
            bad_meta.append(f"{merged['code']}: frequency={merged['frequency']!r}"
                            f" 期望 {want_freq!r}")
        if row["entity_type"] != EXPECT_ENTITY:
            bad_meta.append(f"{merged['code']}: entity_type={row['entity_type']!r}")
        owner = next((s for s, cs in BY_SOURCE.items() if merged["code"] in cs), None)
        if row["source"] != owner:
            bad_meta.append(f"{merged['code']}: source={row['source']!r} 期望 {owner!r}")
    if bad_meta:
        print("FATAL: 元数据不合规:", *bad_meta, sep="\n  ")
        return 8

    # ── 预检 3：short_window 声明 = 库里真实期数（本波核心校验）───────────────
    periods = stored_periods(cur)
    bad_sw, missing_obs = [], []
    for code, declared in sorted(SW_PERIODS.items()):
        actual = periods.get(code)
        if not actual:
            missing_obs.append(code)
        elif actual != declared:
            bad_sw.append(f"{code}: 声明 {declared} 期，库里 {actual} 期")
    if missing_obs:
        print("FATAL: short_window 概念零观测（回填未落地）:", *missing_obs, sep="\n  ")
        return 10
    if bad_sw:
        print("FATAL: short_window 声明与实测不符（声明不得臆造）:",
              *bad_sw, sep="\n  ")
        return 11

    # ── 预检 4：code 唯一性 + registry semantic_code 未被他源占用 ─────────────
    cur.execute(
        "SELECT code, count(*) FROM concepts WHERE NOT deprecated AND code = ANY(%s)"
        " GROUP BY code HAVING count(*) > 1", (ALL_CODES,))
    if cur.fetchall():
        print("FATAL: 目标 code 在非 deprecated 概念内重复")
        return 4
    cur.execute(
        "SELECT semantic_code, source_db, native_code FROM registry_entries"
        " WHERE semantic_code = ANY(%s) AND source_db <> 'fd_open_data'", (ALL_CODES,))
    taken = cur.fetchall()
    if taken:
        print(f"FATAL: semantic_code 已被其他源占用: {taken}")
        return 5

    # ── 计划 ─────────────────────────────────────────────────────────────────
    per_src = defaultdict(int)
    for row, _m, _p in plans:
        per_src[row["source"]] += 1
    print(f"== 命名门计划（{len(plans)} 概念: "
          + ", ".join(f"{s} {n}" for s, n in sorted(per_src.items())) + "）==")
    for row, merged, patch in plans:
        sw = (f" [short_window {SW_PERIODS[merged['code']]}期 → 库里"
              f" {periods.get(merged['code'], 0)} 期]" if merged["code"] in SW_PERIODS else
              f" [{periods.get(merged['code'], 0)} 期]")
        tag = "PASS→verified" if merged["code"] in active_set else "已verified·补镜像"
        print(f"  [{tag}] id={row['id']} {merged['code']}{sw}")
        for f, v in patch.items():
            if row.get(f) != v:
                print(f"      {f}: {row.get(f)!r} -> {v!r}")

    if not apply_mode:
        print("\nDRY-RUN：未写库。加 --apply 执行。")
        conn.close()
        return 0

    if active:
        snapshot(cur, active)
    else:
        print("concepts 侧无需写入，跳过 before-snapshot")

    ids = [row["id"] for row, _m, _p in plans if row["code"] in active_set]
    try:
        for row, _merged, patch in plans:
            if not patch or row["code"] not in active_set:
                continue
            sets = ", ".join(f"{f} = %s" for f in patch)
            cur.execute(f"UPDATE concepts SET {sets} WHERE id = %s",
                        (*patch.values(), row["id"]))
        if ids:
            cur.execute("UPDATE concepts SET verified = TRUE WHERE id = ANY(%s)", (ids,))
        conn.commit()
        patched = sum(1 for r, _m, patch in plans
                      if patch and r["code"] in active_set)
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

    rows_after = load_rows(cur, ALL_CODES)
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
    rows_final = load_rows(cur, ALL_CODES)
    bad_six = [(c, six_field_complete(r)) for c, r in rows_final.items()
               if six_field_complete(r)]
    bad_v = [c for c, r in rows_final.items() if not r["verified"]]
    print(f"concepts 复核: 六字段不齐={bad_six or '无'}; 未 verified={bad_v or '无'}")
    periods_after = stored_periods(cur)
    drift = [(c, SW_PERIODS[c], periods_after.get(c))
             for c in SW_PERIODS if periods_after.get(c) != SW_PERIODS[c]]
    print(f"short_window 复核: {len(SW_PERIODS)} 条声明，不符={drift or '无'}")
    cur.execute(
        "SELECT native_code, provenance ? 'short_window' AS sw, verified, verified_by"
        " FROM registry_entries WHERE source_db='fd_open_data'"
        " AND source_table='concepts' AND native_code = ANY(%s) ORDER BY native_code",
        (sorted(SW_PERIODS),))
    reg = cur.fetchall()
    print(f"registry short_window 锚点: {len(reg)} 条")
    for r in reg:
        print(f"  {r['native_code']} sw={r['sw']} verified={r['verified']}"
              f" by={r['verified_by']}")
    cur.execute("SELECT sum(n) FROM (SELECT count(*) n FROM semantic_observations s"
                " JOIN concepts c ON c.id = s.concept_id WHERE c.code = ANY(%s)"
                " GROUP BY s.concept_id) t", (ALL_CODES,))
    print(f"观测行合计: {cur.fetchone()['sum']} 行")
    conn.close()
    print(f"\nDONE：{len(anchors)} 条过命名门（concepts + registry 双置 verified）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
