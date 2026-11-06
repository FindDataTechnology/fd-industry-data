#!/usr/bin/env python3
"""wire-industry-supply-funnel 任务 3.1/3.2 — 命名门批量补齐（批次 1 新 5 + 存量 8）。

真实链路（照既有机制，不发明新表）：
1. concepts 表命名层字段（name_zh/name_en/unit/frequency/category）+ verified 布尔
   —— SQL 单事务，先照批次 1（extract_batch1.py）惯例；快照已存
   output/supply-funnel/naming-before-snapshot.json，出错回滚事务或用快照还原。
2. registry 镜像（fd_open_data.registry_entries，anchor=fd_open_data/concepts/code/<reg_code>）
   —— 复用 fd-open-data-protocol 的 PostgresRegistryStore（yearbook
   sync_registry_5x.py 同款）：upsert 12 条（不带 verified）→ set_verified(anchors=…)
   由 store 的命名门（semantic_code+双语名）+ 本脚本六字段预检共同把关。
   verified_by=rule:naming-gate 由 store 写入。

留门（不写 verified）：fx.rate（id 2388）——与 fx.rate.ecb.monthly 同源同值
（ecb-exr 单元 2018-06..2026-09 逐点相同，batch1-report §口径1），命名放行会在
目录面造成双条目；移交生命周期裁决，不在本批命名放行。

用法：python3 naming_gate_batch1.py [--apply]   （缺省 dry-run，只打印将执行的变更）
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

# ── 命名材料（起草依据见 naming-review.md；None=保持库内现值）─────────────────
# 六字段门：semantic_code(=code, 不改) + name_zh + name_en + unit + frequency + category(域)
NAMING_PATCH = {
    # 存量 8 中的 7 条：泛化名/非规范单位 → 按单元 README 与规范单位表修订
    2402: {  # portwatch-imf port.calls
        "unit": "次",  # README:87「portcalls 4728 艘次」；规范单位表取 次（count 非表内值）
    },
    2401: {  # wto-eping policy.notice_count
        "name_zh": "WTO 通报数",        # 原名「政策通报数」过泛化；观察=TBT/SPS 通报按成员按月计数
        "name_en": "WTO TBT/SPS notification count",
        "unit": "条",                    # 通报条数；规范单位表取 条
    },
    2383: {  # trade.export_flow.world
        "name_zh": "对全球出口额", "name_en": "Exports to world", "unit": "美元",
    },
    2384: {  # trade.export_flow.us
        "name_zh": "对美国出口额", "name_en": "Exports to the United States", "unit": "美元",
    },
    2385: {  # trade.export_flow.de
        "name_zh": "对德国出口额", "name_en": "Exports to Germany", "unit": "美元",
    },
    2386: {  # trade.export_flow.jp
        "name_zh": "对日本出口额", "name_en": "Exports to Japan", "unit": "美元",
    },
    2387: {  # trade.export_flow.uk
        "name_zh": "对英国出口额", "name_en": "Exports to the United Kingdom", "unit": "美元",
    },
    # trade.* 五条：原名 Trade flow value/贸易流值 五条同名不可分辨；按 code 后缀 partner 拆名。
    # unit usd→美元（README:61 实锚「4,501,488,833 美元」；规范单位表有 美元）。
}

#: 过门放行名单（12）。其余字段（batch1 新 5 条已按规范落库）不改。
PASS_IDS = [2409, 2410, 2411, 2412, 2414, 2402, 2401, 2383, 2384, 2385, 2386, 2387]
#: 留门名单（1）。不写 verified、不进 registry 镜像。
HOLD_IDS = [2388]

ALL_IDS = sorted(PASS_IDS + HOLD_IDS)

FIELDS = ("name_zh", "name_en", "unit", "frequency", "category")
COLUMNS = "id, code, name_en, name_zh, category, unit, measure, frequency, entity_type, source, verified"


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
    cur.execute(
        "SELECT semantic_code FROM registry_entries WHERE semantic_code = ANY(%s)",
        ([rows[i]["code"] for i in PASS_IDS],),
    )
    taken = [r["semantic_code"] for r in cur.fetchall()]
    if taken:
        print(f"FATAL: semantic_code 已被 registry 其他源占用: {taken}")
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
        print(f"  [{tag}] id={cid} {row['code']} (source={row['source']})")
        for f, (old, new) in changes.items():
            print(f"      {f}: {old!r} -> {new!r}")
        if verify and not changes:
            print("      (六字段已齐，零字段变更，仅置 verified)")

    if not apply_mode:
        print("\nDRY-RUN：未写库。加 --apply 执行。")
        conn.close()
        return 0

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
    sys.path.insert(0, PROTOCOL_ROOT)
    from fd_open_data_protocol.registry_store import PostgresRegistryStore  # noqa: E402
    from fd_open_data_protocol.schema import RegistryEntry  # noqa: E402

    entries = []
    for cid in PASS_IDS:
        r = rows_after[cid]
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
                provenance={
                    "measure": r["measure"], "entity_type": r["entity_type"],
                    "source": r["source"], "code": r["code"],
                },
                verified=False,
            )
        )
    store = PostgresRegistryStore(dsn)
    stats = store.upsert_entries(entries)
    print(f"registry upsert: new={stats.new} updated={stats.updated} "
          f"unchanged={stats.unchanged} demoted={stats.demoted}")
    if stats.demoted:
        print("FATAL: 出现命名漂移降级（不应发生，本批全为新锚点）——人工检查 registry_entries")
        conn.close()
        return 8
    anchors = [("fd_open_data", "concepts", "code", e.native_code) for e in entries]
    n = store.set_verified(anchors=anchors, verified_by="rule:naming-gate")
    print(f"registry set_verified: newly verified={n} / {len(anchors)} (verified_by=rule:naming-gate)")
    if n != len(anchors):
        print("WARN: 部分锚点未被置 verified（store 三字段门拒绝？），逐条核查")

    conn.close()
    print("\nDONE：12 条过门（concepts + registry 镜像双置 verified），fx.rate 留门待裁决。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
