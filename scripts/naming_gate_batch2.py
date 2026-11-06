#!/usr/bin/env python3
"""wire-industry-supply-funnel 任务 3.1/3.2 批 2 — 命名门批量补齐（批次 2 新 55）。

照搬批 1 机制（scripts/naming_gate_batch1.py，同款真实链路，不发明新表）：
1. concepts 表命名层字段（name_zh/name_en/unit/frequency/category）+ verified 布尔
   —— SQL 单事务；快照写 output/supply-funnel/naming-before-snapshot-batch2.json
   （55 行 concepts 全命名层字段 + 4 行 registry 镜像全列），出错回滚事务或用快照还原。
2. registry 镜像（fd_open_data.registry_entries，anchor=fd_open_data/concepts/code/<code>）
   —— 复用 fd-open-data-protocol 的 PostgresRegistryStore（批 1 同款）：
   upsert 55 条（不带 verified）→ set_verified(anchors=…) 由 store 的三字段门
   （semantic_code+双语名）+ 本脚本六字段预检共同把关。verified_by=rule:naming-gate。

批 2 特有：
- 4 个短窗概念（agri.pork.price.market、disaster.*.monthly ×3）的镜像行已在
  extract_batch2.py 建条（verified=false、provenance 带 short_window 声明）。
  store 的 upsert 对 provenance 是整列替换（EXCLUDED.provenance），故本脚本对这
  4 条**透传库内现有 provenance**（保 short_window 不丢）；其余 51 条为全新锚点，
  provenance 按 batch 1 惯例 {measure, entity_type, source, code}。
- 预检断言按本批调整：semantic_code 撞码检查排除「本批自身概念的原生镜像锚」。

留门：无——55 条全部有真实量纲依据（payload 逐指标 unit 实测 / README 归一口径），
零单位硬造；缺单位清单为空（同批 1 先例）。

用法：python3 naming_gate_batch2.py [--apply]   （缺省 dry-run，只打印将执行的变更）
DSN：source ~/.config/finddata/dashboards.env 后 $FD_OPEN_DATA_DSN。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg

PROTOCOL_ROOT = "/Users/chengsishi/finddata/fd-open-data-protocol"
OUT_DIR = Path("/Users/chengsishi/finddata/fd-industry-data/output/supply-funnel")
SNAPSHOT_PATH = OUT_DIR / "naming-before-snapshot-batch2.json"

#: 批 2 新概念（extract_batch2.py CONSTRUCT_CONCEPTS，id 2421..2475 连续段，全部 verified=false）
ALL_IDS = list(range(2421, 2476))
assert len(ALL_IDS) == 55

#: 过门放行名单（55，全批）。批 2 无留门：单位/频次/域全部有源依据（见 naming-review-batch2.md）。
PASS_IDS = list(ALL_IDS)
HOLD_IDS: list[int] = []

# ── 命名材料（起草依据见 naming-review-batch2.md；None/缺省=保持库内现值）────────
# 六字段门：semantic_code(=code, 不改) + name_zh + name_en + unit + frequency + category(域)
# 抽取批草稿 52 条直接过门（零变更仅置 verified）；以下 3 条为准确性修订：
NAMING_PATCH = {
    2422: {  # afdc energy.stations.electric
        # 原名「电动加注站数」——「加注」不适用于电（README yaxis=Number of Stations、
        # 系列名 Electric charging stations）；充电即电力充能，中文规范词=充电站
        "name_zh": "充电站数",
    },
    2466: {  # socrata-chicago transit.bus.rides.monthly
        # 原名「…月度客次」——「客次」非规范统计词；README 口径「月度乘次/客流」，
        # 统计面标准词=客运量（unit=人次 承载计次语义）
        "name_zh": "芝加哥公交月度客运量",
    },
    2471: {  # amis-market agri.cereal.utilization.amis
        # 原名「谷物总利用量（AMIS 总谷物）」括注与「谷物」重复且系品牌冗余；
        # 融合 product（TOTAL CEREALS=总谷物）+ element（Total Utilization=利用量）单层名
        "name_zh": "AMIS 总谷物利用量",
    },
}

FIELDS = ("name_zh", "name_en", "unit", "frequency", "category")
COLUMNS = "id, code, name_en, name_zh, category, unit, measure, frequency, entity_type, source, verified"
CONCEPT_SNAPSHOT_COLS = ("id", "code", "name_en", "name_zh", "category", "unit",
                         "measure", "frequency", "entity_type", "source", "verified")

# 4 个短窗概念的镜像锚（extract_batch2.py 建条带 short_window 声明，verified=false）
SHORT_WINDOW_CODES = (
    "agri.pork.price.market",
    "disaster.affected.monthly",
    "disaster.deaths.missing.monthly",
    "disaster.econ.loss.monthly",
)


def load_rows(cur):
    cur.execute(
        f"SELECT {COLUMNS} FROM concepts WHERE id = ANY(%s) ORDER BY id", (ALL_IDS,)
    )
    return {r["id"]: r for r in cur.fetchall()}


def six_field_complete(row) -> list[str]:
    """本 change 命名门口径：六字段缺一不可（yearbook「ready 需单位」先例）。"""
    missing = []
    for col in ("code", "name_zh", "name_en", "unit", "frequency", "category"):
        v = row.get(col)
        if v is None or str(v).strip() == "":
            missing.append(col)
    return missing


def snapshot(cur) -> None:
    """变更前快照：55 行 concepts 命名层字段 + 4 行 registry 镜像全列（回滚依据）。"""
    cur.execute(
        f"SELECT {', '.join(CONCEPT_SNAPSHOT_COLS)} FROM concepts WHERE id = ANY(%s) ORDER BY id",
        (ALL_IDS,),
    )
    cols = [d.name for d in cur.description]
    concepts_snap = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.execute(
        "SELECT id, source_db, source_table, source_column, native_code, semantic_code,"
        " name_zh, name_en, unit, frequency, domain, provenance, verified, verified_at,"
        " verified_by FROM registry_entries"
        " WHERE source_db='fd_open_data' AND source_table='concepts'"
        " AND native_code = ANY(%s) ORDER BY native_code",
        (list(SHORT_WINDOW_CODES),),
    )
    reg_cols = [d.name for d in cur.description]
    registry_snap = []
    for r in cur.fetchall():
        d = dict(zip(reg_cols, r))
        d["provenance"] = json.loads(json.dumps(d["provenance"]))  # JSONB -> json safe
        registry_snap.append(d)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(
        json.dumps({"concepts": concepts_snap, "registry_mirror": registry_snap},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"snapshot -> {SNAPSHOT_PATH}（concepts {len(concepts_snap)} 行 + registry {len(registry_snap)} 行）")


def main() -> int:
    apply_mode = "--apply" in sys.argv
    dsn = os.environ.get("FD_OPEN_DATA_DSN")
    if not dsn:
        print("FATAL: $FD_OPEN_DATA_DSN 未设置（source ~/.config/finddata/dashboards.env）")
        return 2

    conn = psycopg.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    rows = load_rows(cur)

    # ── 前置断言 ────────────────────────────────────────────────────────────
    problems = []
    for cid in ALL_IDS:
        row = rows.get(cid)
        if row is None:
            problems.append(f"id={cid} 不存在")
        elif row["verified"]:
            problems.append(f"id={cid} {row['code']} 已 verified，不在本批范围")
    if problems:
        print("FATAL 前置断言失败:", *problems, sep="\n  ")
        return 3
    assert set(rows) == set(ALL_IDS)

    # reg_code 口径与 fd_open_data.yaml adapter 一致：非 deprecated 内 code 重复才加后缀
    cur.execute(
        "SELECT code, count(*) AS n FROM concepts WHERE NOT deprecated "
        "GROUP BY code HAVING count(*) > 1"
    )
    dup_codes = {r["code"] for r in cur.fetchall()}
    clash = dup_codes & {rows[i]["code"] for i in PASS_IDS}
    if clash:
        print(f"FATAL: 过门 code 在非 deprecated 概念内重复，reg_code 需后缀: {clash}")
        return 4
    # registry 语义码唯一索引预检：任何源已占用的 semantic_code 都不能撞
    # （批 2 调整：排除本批自身概念的原生镜像锚——4 个短窗条目已建，anchor 与 semantic_code 同码）
    cur.execute(
        "SELECT semantic_code, source_db, native_code FROM registry_entries"
        " WHERE semantic_code = ANY(%s)"
        " AND NOT (source_db='fd_open_data' AND source_table='concepts'"
        "          AND native_code = semantic_code)",
        ([rows[i]["code"] for i in PASS_IDS],),
    )
    taken = [(r["semantic_code"], r["source_db"], r["native_code"]) for r in cur.fetchall()]
    if taken:
        print(f"FATAL: semantic_code 已被 registry 其他源/锚点占用: {taken}")
        return 5

    # 六字段预检（按 patch 后的拟值）
    incomplete = {}
    for cid in PASS_IDS:
        row = dict(rows[cid])
        for f, v in NAMING_PATCH.get(cid, {}).items():
            row[f] = v
        missing = six_field_complete(row)
        if missing:
            incomplete[rows[cid]["code"]] = missing
    if incomplete:
        print("FATAL: 以下过门候选六字段不齐（按 ready-需单位 规则不得放行）:", incomplete)
        return 6

    # ── 打印变更计划 ────────────────────────────────────────────────────────
    print("== 命名材料变更计划 ==")
    for cid in ALL_IDS:
        row = rows[cid]
        patch = NAMING_PATCH.get(cid, {})
        verify = cid in PASS_IDS
        changes = {f: (row[f], v) for f, v in patch.items() if row[f] != v}
        tag = "PASS→verified" if verify else "HOLD(留门)"
        sw = " [短窗镜像已在]" if row["code"] in SHORT_WINDOW_CODES else ""
        print(f"  [{tag}] id={cid} {row['code']} (source={row['source']}){sw}")
        for f, (old, new) in changes.items():
            print(f"      {f}: {old!r} -> {new!r}")
        if verify and not changes:
            print("      (六字段已齐，零字段变更，仅置 verified)")

    if not apply_mode:
        print("\nDRY-RUN：未写库。加 --apply 执行。")
        conn.close()
        return 0

    # ── 0. 快照（回滚依据）───────────────────────────────────────────────────
    snapshot(cur)

    # ── 1. concepts 单事务：命名层字段 + verified ───────────────────────────
    try:
        for cid, patch in NAMING_PATCH.items():
            sets = ", ".join(f"{f} = %s" for f in patch)
            cur.execute(
                f"UPDATE concepts SET {sets} WHERE id = %s",
                (*patch.values(), cid),
            )
        cur.execute(
            "UPDATE concepts SET verified = TRUE WHERE id = ANY(%s)", (PASS_IDS,)
        )
        conn.commit()
        print(f"\nconcepts 更新已提交：{len(NAMING_PATCH)} 行命名字段修订，"
              f"{len(PASS_IDS)} 行 verified=TRUE（单事务）")
    except Exception as exc:  # noqa: BLE001 — 出错即回滚该批
        conn.rollback()
        print(f"FATAL: concepts 更新失败已回滚（零残留）: {exc}")
        conn.close()
        return 7

    # ── 2. registry 镜像：PostgresRegistryStore upsert + set_verified ──────
    rows_after = load_rows(cur)
    # 短窗 4 条透传库内 provenance（保 short_window；upsert 对 provenance 整列替换）
    cur.execute(
        "SELECT native_code, provenance FROM registry_entries"
        " WHERE source_db='fd_open_data' AND source_table='concepts'"
        " AND native_code = ANY(%s)",
        (list(SHORT_WINDOW_CODES),),
    )
    existing_prov = {r["native_code"]: r["provenance"] for r in cur.fetchall()}
    missing_sw = [c for c in SHORT_WINDOW_CODES if c not in existing_prov]
    if missing_sw:
        print(f"FATAL: 短窗镜像行缺失（extract_batch2 应已建条）: {missing_sw}")
        conn.close()
        return 8

    sys.path.insert(0, PROTOCOL_ROOT)
    from fd_open_data_protocol.registry_store import PostgresRegistryStore  # noqa: E402
    from fd_open_data_protocol.schema import RegistryEntry  # noqa: E402

    entries = []
    for cid in PASS_IDS:
        r = rows_after[cid]
        if r["code"] in existing_prov:
            provenance = existing_prov[r["code"]]  # 透传：base 键 + short_window 原样保留
        else:
            provenance = {
                "measure": r["measure"], "entity_type": r["entity_type"],
                "source": r["source"], "code": r["code"],
            }
        entries.append(
            RegistryEntry(
                source_db="fd_open_data",
                source_table="concepts",
                source_column="code",
                native_code=r["code"],  # 过门 code 均无重复，reg_code=code（见前置断言）
                semantic_code=r["code"],
                name_zh=r["name_zh"],
                name_en=r["name_en"],
                unit=r["unit"],
                frequency=r["frequency"],
                domain=r["category"],
                provenance=provenance,
                verified=False,
            )
        )
    store = PostgresRegistryStore(dsn)
    stats = store.upsert_entries(entries)
    print(f"registry upsert: new={stats.new} updated={stats.updated} "
          f"unchanged={stats.unchanged} demoted={stats.demoted}")
    if stats.demoted:
        print("FATAL: 出现命名漂移降级（不应发生：51 新锚 + 4 条本就 unverified）——人工检查 registry_entries")
        conn.close()
        return 9
    if stats.updated:
        print(f"WARN: {stats.updated} 条已建镜像发生内容更新（预期 4 条 short_window 行 unchanged）——核查 provenance")
    anchors = [("fd_open_data", "concepts", "code", e.native_code) for e in entries]
    n = store.set_verified(anchors=anchors, verified_by="rule:naming-gate")
    print(f"registry set_verified: newly verified={n} / {len(anchors)} (verified_by=rule:naming-gate)")
    if n != len(anchors):
        print("WARN: 部分锚点未被置 verified（store 三字段门拒绝？），逐条核查")

    # ── 3. 库内验证 ─────────────────────────────────────────────────────────
    cur.execute(
        f"SELECT {COLUMNS} FROM concepts WHERE id = ANY(%s) ORDER BY id", (ALL_IDS,)
    )
    bad_six, bad_verified = [], []
    for r in cur.fetchall():
        if six_field_complete(r):
            bad_six.append((r["code"], six_field_complete(r)))
        if not r["verified"]:
            bad_verified.append(r["code"])
    print(f"concepts 六字段复核: 不齐={bad_six or '无'}; 未 verified={bad_verified or '无'}")
    cur.execute(
        "SELECT id, code FROM concepts WHERE source IS NOT NULL AND NOT verified ORDER BY id"
    )
    remaining = [(r["id"], r["code"]) for r in cur.fetchall()]
    print(f"余量断言（source IS NOT NULL AND NOT verified，应为批 1 遗留 fx.rate 双条）: {remaining}")

    # registry 短窗声明保全复核
    cur.execute(
        "SELECT native_code, provenance ? 'short_window' AS sw, verified, verified_by"
        " FROM registry_entries WHERE source_db='fd_open_data' AND source_table='concepts'"
        " AND native_code = ANY(%s) ORDER BY native_code",
        (list(SHORT_WINDOW_CODES),),
    )
    for r in cur.fetchall():
        print(f"short_window 保全: {r['native_code']} sw={r['sw']} verified={r['verified']} by={r['verified_by']}")

    conn.close()
    print(f"\nDONE：{len(PASS_IDS)} 条过门（concepts + registry 镜像双置 verified），"
          f"批 2 留门 0 条。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
