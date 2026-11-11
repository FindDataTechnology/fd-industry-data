#!/usr/bin/env python3
"""Task 3.3/3.4 前置 — 把医药两个**有源**概念族及其 4 个变量落进本体库。

为什么是窄脚本而不是 ``fd-open-data-mcp consume-concepts``
--------------------------------------------------------
与 ``register_flower_concepts.py`` 同一先例：生产本体库里有数千个
``concept_code IS NULL`` 的历史变量、``concept_families`` 近乎空表；全量
``consume-concepts`` 会一次性派生大量只含单变量的族行，属库级变更，不在本
change 的范围内。本脚本只写：

* ``ClinicalTrial`` 族 + 2 个变量（``trial.new`` / ``trial.enrollment.planned``）
* ``InsuranceCatalogEntry`` 族 + 2 个变量（``catalog.drug.entries`` / ``catalog.drug.class_a_share``）

三个**声明式**族（DrugApproval / ReviewProgress / MedicalInstitution）**不落库**：
它们的源当前不可达，落库只会造出永远填不上的 concepts 行（"声明≠挂载"）。

幂等与隔离
----------
* 幂等：同身份四元组已存在则只比对、仅在真漂移时 UPDATE；连跑两次行数不变。
* 频率拼写规范化（annual → yearly）就地同步，不新建变量（fix-concept-freshness-window）。
* 隔离：写前/写后各取一次全表指纹，逐行比对；出现任何非本脚本新增/改动的行
  即回滚并非零退出（防止误改别人的概念）。

真源：``fd-open-data-protocol/fd_open_data_protocol/vocabulary/concepts.yaml``。

用法
----
    source ~/.config/finddata/dashboards.env
    python3 scripts/register_pharma_concepts.py            # dry-run（ROLLBACK）
    python3 scripts/register_pharma_concepts.py --apply     # 落库
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg2
import yaml

FAMILIES = ("ClinicalTrial", "InsuranceCatalogEntry")
SOURCE = "pharma-domain-stack"

VOCAB = (
    Path(__file__).resolve().parents[2]
    / "fd-open-data-protocol"
    / "fd_open_data_protocol"
    / "vocabulary"
    / "concepts.yaml"
)
# 期望变量集（写死以便"词汇表被人手改坏了"能被发现，而不是跟着一起坏）
EXPECTED = {
    "ClinicalTrial": {"trial.new", "trial.enrollment.planned"},
    "InsuranceCatalogEntry": {"catalog.drug.entries", "catalog.drug.class_a_share"},
}
TUPLE_FIELDS = ("code", "entity_type", "measure", "unit")
# ^ frequency 刻意不在身份键里（change fix-concept-freshness-window D1）：同一 code 只有一条
#   变量，频率是它的元数据。把它放进身份键，会让「annual → yearly 拼写规范化」被判成换了
#   一个变量而失败——那正是要修的缺陷。entity_type 仍留在身份键里：粒度不同，观测该挂到
#   别的实体上，硬改会把历史观测挂错。


def load_families(vocab: Path) -> dict[str, tuple[dict, list[dict]]]:
    doc = yaml.safe_load(vocab.read_text(encoding="utf-8"))
    out: dict[str, tuple[dict, list[dict]]] = {}
    for family, expected_codes in EXPECTED.items():
        hit = [e for e in doc["entries"] if e.get("id") == family]
        if len(hit) != 1:
            raise SystemExit(f"FATAL: {vocab} 里 {family} 族条目数={len(hit)}（应为 1）")
        entry = hit[0]
        variables = entry.get("variables") or []
        codes = {v.get("code") for v in variables}
        if codes != expected_codes:
            raise SystemExit(f"FATAL: {family} 变量集不符，实得 {sorted(codes)}")
        for v in variables:
            for field in TUPLE_FIELDS:
                if not v.get(field):
                    raise SystemExit(f"FATAL: {family} 的变量缺 {field}: {v}")
        out[family] = (entry, variables)
    # 声明式三族必须**没有** variables —— 有就说明有人给不可达的源播了种子
    for family in ("DrugApproval", "ReviewProgress", "MedicalInstitution"):
        hit = [e for e in doc["entries"] if e.get("id") == family]
        if hit and hit[0].get("variables"):
            raise SystemExit(f"FATAL: {family} 是声明式族，不应带 variables 种子")
    return out


def census(cur) -> dict:
    cur.execute("SELECT count(*), count(*) FILTER (WHERE concept_code IS NULL) FROM concepts")
    concepts, orphan = cur.fetchone()
    cur.execute("SELECT count(*) FROM concept_families")
    return {"concepts": concepts, "orphan_concept_code": orphan, "families": cur.fetchone()[0]}


def fingerprint(cur) -> list[tuple]:
    cur.execute(
        "SELECT code, entity_type, measure, unit, frequency, concept_code"
        " FROM concepts ORDER BY id"
    )
    return cur.fetchall()


def upsert_family(cur, entry: dict) -> bool:
    """写族行；返回是否**新建**（用于增量断言）。"""
    fields = {
        "code": entry["id"],
        "name_en": entry.get("label_en"),
        "name_zh": entry.get("label_zh"),
        "description": entry.get("description"),
        "value_type": entry.get("value_type"),
        "unit_type": entry.get("unit_type"),
        "dimensions": json.dumps(entry.get("dimensions") or [], ensure_ascii=False),
        "uri": f"https://schema.finddata.tech/concept/{entry['id']}",
    }
    cur.execute("SELECT id FROM concept_families WHERE code=%s", (fields["code"],))
    hit = cur.fetchone()
    if hit:
        cur.execute(
            "UPDATE concept_families SET name_en=%s, name_zh=%s, description=%s,"
            " value_type=%s, unit_type=%s, dimensions=%s, uri=%s, deprecated=false"
            " WHERE code=%s",
            (fields["name_en"], fields["name_zh"], fields["description"],
             fields["value_type"], fields["unit_type"], fields["dimensions"],
             fields["uri"], fields["code"]),
        )
        print(f"family {fields['code']}: updated (id={hit[0]})")
        return False
    cur.execute(
        "INSERT INTO concept_families (code, name_en, name_zh, description,"
        " value_type, unit_type, dimensions, uri, deprecated, created_at)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,false,now()) RETURNING id",
        (fields["code"], fields["name_en"], fields["name_zh"], fields["description"],
         fields["value_type"], fields["unit_type"], fields["dimensions"], fields["uri"]),
    )
    print(f"family {fields['code']}: created (id={cur.fetchone()[0]})")
    return True


def upsert_variable(cur, family: str, label_en: str, label_zh: str, v: dict,
                    migrate_entity_type: bool = False) -> tuple[int, int]:
    """按身份四元组 upsert 一条 concept；返回 (created, updated)。

    身份四元组 = ``(code, entity_type, measure, unit)``，**频率不在其中**（见
    ``TUPLE_FIELDS`` 的注释）：频率是元数据，拼写规范化时就地同步，不新建变量。

    四元组含 ``entity_type``，所以**改了声明里的粒度就等于换了一个变量**：按四元组查不到
    时会新插一条同 ``code`` 的行，旧行仍在——两条 code 相同、粒度不同的变量并存，读侧
    按哪个都对不上。因此插入前必须先查「同 code 不同四元组」：命中即失败，只有显式
    ``--migrate-entity-type`` 且该概念没有任何观测时才就地改粒度。
    """
    key = tuple(v[f] for f in TUPLE_FIELDS)
    cur.execute(
        "SELECT id, entity_type, measure, unit, frequency, source, verified,"
        " concept_code, deprecated FROM concepts WHERE code=%s ORDER BY id",
        (v["code"],),
    )
    rows = cur.fetchall()
    exact = [r for r in rows if (r[1], r[2], r[3]) == key[1:]]
    if exact:
        cid, _etype, _m, _u, _f, csource, verified, ccode, deprecated = exact[0]
        changed = 0
        if _f != v["frequency"]:
            # 频率是元数据而非身份：词表把拼写规范化（annual → yearly）时 MUST 就地同步。
            # 不同步的后果很具体：非规范拼写不在新鲜度表的键里，会被静默当成 1 小时窗口
            # （change fix-concept-freshness-window D2）。
            cur.execute("UPDATE concepts SET frequency=%s WHERE id=%s",
                        (v["frequency"], cid))
            print(f"  {v['code']}: frequency {_f} -> {v['frequency']} (id={cid})")
            changed = 1
        if (csource, verified, ccode, deprecated) != (SOURCE, True, family, False):
            cur.execute(
                "UPDATE concepts SET source=%s, verified=true, concept_code=%s,"
                " deprecated=false WHERE id=%s", (SOURCE, family, cid),
            )
            print(f"  {v['code']}: updated (id={cid})")
            return 0, 1
        if changed:
            return 0, 1
        print(f"  {v['code']}: already correct (id={cid})")
        return 0, 0
    if rows:
        same_measure = [r for r in rows if (r[2], r[3]) == key[2:]]
        if not (migrate_entity_type and same_measure):
            raise SystemExit(
                f"FATAL: {v['code']} 已存在但身份四元组不同（数据库行 "
                f"{[(r[1], r[2], r[3], r[4]) for r in rows]} ≠ 声明 {key[1:]}）。"
                f"改粒度会产生同 code 的两条变量，读侧对不上。"
                f"若确认只是 entity_type 变更且该概念尚无观测，用 --migrate-entity-type 显式迁移。"
            )
        cid = same_measure[0][0]
        cur.execute("SELECT count(*) FROM semantic_observations WHERE concept_id=%s", (cid,))
        used = cur.fetchone()[0]
        if used:
            raise SystemExit(
                f"FATAL: {v['code']} 已有 {used} 条观测，不能改 entity_type"
                f"（会把这些观测挂到错误的实体粒度上）。先处理观测再迁移。"
            )
        cur.execute(
            "UPDATE concepts SET entity_type=%s, source=%s, verified=true,"
            " concept_code=%s, deprecated=false WHERE id=%s",
            (v["entity_type"], SOURCE, family, cid),
        )
        print(f"  {v['code']}: entity_type migrated -> {v['entity_type']} (id={cid})")
        return 0, 1
    suffix = v["code"].rsplit(".", 1)[-1]
    cur.execute(
        "INSERT INTO concepts (code, name_en, name_zh, unit, measure, frequency,"
        " entity_type, source, verified, deprecated, created_at, concept_code)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true,false,now(),%s)",
        (v["code"], f"{label_en} ({suffix})", f"{label_zh}（{suffix}）",
         v["unit"], v["measure"], v["frequency"], v["entity_type"], SOURCE, family),
    )
    print(f"  {v['code']}: created")
    return 1, 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    ap.add_argument("--vocab", default=str(VOCAB))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--migrate-entity-type", action="store_true",
                    help="显式允许把已存在变量的 entity_type 就地改掉（仅当该概念零观测时）")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("FATAL: $FD_OPEN_DATA_DSN 未设置（source ~/.config/finddata/dashboards.env）")

    families = load_families(Path(args.vocab))
    conn = psycopg2.connect(args.dsn)
    conn.autocommit = False
    cur = conn.cursor()

    before = census(cur)
    pre_rows = fingerprint(cur)
    print("before:", before)

    created_families = 0
    created = updated = 0
    for family, (entry, variables) in families.items():
        if upsert_family(cur, entry):
            created_families += 1
        for v in variables:
            c, u = upsert_variable(cur, family, entry.get("label_en") or family,
                                   entry.get("label_zh") or family, v,
                                   migrate_entity_type=args.migrate_entity_type)
            created += c
            updated += u

    mine = {v["code"] for _e, vs in families.values() for v in vs}
    # 写后核验 1：变量集与归属正确
    cur.execute(
        "SELECT code, concept_code FROM concepts WHERE concept_code = ANY(%s) ORDER BY code",
        (list(families),),
    )
    got = cur.fetchall()
    got_codes = {r[0] for r in got}
    if got_codes != mine:
        print("FATAL 变量集不符:", {"missing": sorted(mine - got_codes), "extra": sorted(got_codes - mine)})
        conn.rollback()
        return 3

    # 写后核验 2：非本脚本的行一行未变
    post_rows = fingerprint(cur)
    pre_set = set(pre_rows)
    post_set = set(post_rows)
    strangers = [r for r in post_rows if r not in pre_set and r[0] not in mine]
    changed = [r for r in pre_rows if r not in post_set and r[0] not in mine]
    if strangers or changed:
        print("FATAL 触碰到非本脚本的行:", {"new_unexpected": strangers[:5], "changed": changed[:5]})
        conn.rollback()
        return 4
    print(f"isolation: new={len(post_set - pre_set)} changed=0（全部为本脚本新增的行）")

    after = census(cur)
    print("after :", after)
    print(f"families: +{after['families'] - before['families']} | "
          f"concepts: +{after['concepts'] - before['concepts']} | "
          f"orphan_concept_code: {before['orphan_concept_code']} -> {after['orphan_concept_code']}")
    if after["orphan_concept_code"] != before["orphan_concept_code"]:
        print("FATAL 已有的 concept_code 空值行被改动")
        conn.rollback()
        return 5
    # 幂等：第二次运行时族与变量都不应再新增
    if created_families != after["families"] - before["families"]:
        print("FATAL 族增量与新建计数不一致")
        conn.rollback()
        return 5
    if created != after["concepts"] - before["concepts"]:
        print("FATAL 变量增量与新建计数不一致")
        conn.rollback()
        return 5

    if args.apply:
        conn.commit()
        print("APPLIED (committed)")
    else:
        conn.rollback()
        print("DRY-RUN (rolled back; 用 --apply 落库)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
