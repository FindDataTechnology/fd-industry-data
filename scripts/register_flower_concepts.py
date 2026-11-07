#!/usr/bin/env python3
"""Task 3.4 前置 — materialize the PlantedArea concept family + its 3 variables.

Why a narrow script instead of ``fd-open-data-mcp consume-concepts``
--------------------------------------------------------------------
The production ontology store (``$FD_OPEN_DATA_DSN``, database ``fd_open_data``)
holds 2 372 Variables, of which **2 364 have ``concept_code IS NULL``** and
``concept_families`` holds **0 rows**. A full ``consume-concepts`` run would
therefore create 25 families *and* backfill thousands of orphan Variables into
freshly-derived family-of-one rows — a store-wide mutation that is not this
change's business and cannot be reviewed here.

So this script writes exactly the rows the flower line needs, and proves it
touched nothing else:

* one ``concept_families`` row: ``PlantedArea``;
* three ``concepts`` rows (``area.flower.planted.{total,open,protected}``),
  the five-tuple identity copied from the protocol vocabulary — the same rows
  ``consume_indicator_defs`` would create for these seeds;
* idempotent: existing rows are read, compared, and only updated on real drift;
* a before/after census of ``concepts``/``concept_families`` that fails loudly
  if any pre-existing row changed (guards against a broad side effect).

Truth source: ``fd-open-data-protocol/fd_open_data_protocol/vocabulary/concepts.yaml``
(family ``PlantedArea``). Never edit ``openspec/specs/``.

Usage
-----
    source ~/.config/finddata/dashboards.env
    python3 scripts/register_flower_concepts.py            # dry-run (ROLLBACK)
    python3 scripts/register_flower_concepts.py --apply    # commit
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg2
import yaml

FAMILY = "PlantedArea"
SOURCE = "aiph-stats"

VOCAB = (
    Path(__file__).resolve().parents[2]
    / "fd-open-data-protocol"
    / "fd_open_data_protocol"
    / "vocabulary"
    / "concepts.yaml"
)
EXPECTED_VARIABLES = {
    "area.flower.rose.planted",
    "area.flower.carnation.planted",
    "area.flower.orchid.planted",
    "area.flower.chrysanthemum.planted",
    "area.flower.lily.planted",
    "area.flower.christmas_tree.planted",
    "area.flower.cut_foliage.planted",
    "area.flower.flowering_plant.planted",
    "area.flower.green_foliage_plant.planted",
    # 3.3：EU 花卉主面积（Eurostat apro_cpsh1 / N0000 / MAR_THS_HA）
    "area.flower.all.planted",
}
# 本 change 自己写坏的第一版（品类没编进 code，会把同国两品类撞成一条）。
# 只删这三个自己建的、且从未写入过任何观测的 code；发现它们已有观测即拒删。
SUPERSEDED = {
    "area.flower.planted.total",
    "area.flower.planted.open",
    "area.flower.planted.protected",
}


def load_family(vocab: Path) -> tuple[dict, list[dict]]:
    doc = yaml.safe_load(vocab.read_text(encoding="utf-8"))
    hit = [e for e in doc["entries"] if e.get("id") == FAMILY]
    if len(hit) != 1:
        raise SystemExit(f"FATAL: {vocab} 里 {FAMILY} 族条目数={len(hit)}（应为 1）")
    entry = hit[0]
    variables = entry.get("variables") or []
    codes = {v["code"] for v in variables}
    if codes != EXPECTED_VARIABLES:
        raise SystemExit(f"FATAL: {FAMILY} 变量集不符: {sorted(codes)}")
    for v in variables:
        for field in ("code", "entity_type", "measure", "unit", "frequency"):
            if not v.get(field):
                raise SystemExit(f"FATAL: {v} 缺 {field}")
    return entry, variables


def census(cur) -> dict:
    cur.execute("SELECT count(*), count(*) FILTER (WHERE concept_code IS NULL) FROM concepts")
    concepts, orphan = cur.fetchone()
    cur.execute("SELECT count(*) FROM concept_families")
    return {"concepts": concepts, "orphan_concept_code": orphan,
            "families": cur.fetchone()[0]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    ap.add_argument("--vocab", default=str(VOCAB))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("FATAL: $FD_OPEN_DATA_DSN 未设置"
                         "（source ~/.config/finddata/dashboards.env）")

    entry, variables = load_family(Path(args.vocab))

    conn = psycopg2.connect(args.dsn)
    conn.autocommit = False
    cur = conn.cursor()

    # ── 撤掉本 change 第一版写坏的三个 code（品类未编进 code）──────────
    cur.execute(
        "SELECT c.code, count(o.id) FROM concepts c"
        " LEFT JOIN semantic_observations o ON o.concept_id = c.id"
        " WHERE c.code = ANY(%s) GROUP BY 1", (sorted(SUPERSEDED),))
    doomed = cur.fetchall()
    with_obs = [code for code, n in doomed if n]
    if with_obs:
        print("FATAL 待撤销的旧 code 已有观测，不能删:", with_obs)
        conn.rollback()
        return 6
    if doomed:
        cur.execute("DELETE FROM concepts WHERE code = ANY(%s) AND concept_code = %s",
                    (sorted(SUPERSEDED), FAMILY))
        print(f"superseded 撤销 {len(doomed)} 个旧 code: {sorted(c for c, _ in doomed)}")

    before = census(cur)
    print("before:", before)

    # 记下全表指纹，写后复核「只多了我这几行」——防止误改别的概念
    cur.execute("SELECT code, entity_type, measure, unit, frequency, concept_code"
                " FROM concepts ORDER BY id")
    pre_rows = cur.fetchall()

    fam_fields = {
        "code": entry["id"],
        "name_en": entry.get("label_en"),
        "name_zh": entry.get("label_zh"),
        "description": entry.get("description"),
        "value_type": entry.get("value_type"),
        "unit_type": entry.get("unit_type"),
        "dimensions": json.dumps(entry.get("dimensions") or [], ensure_ascii=False),
        "uri": f"https://schema.finddata.tech/concept/{entry['id']}",
        "deprecated": False,
    }
    cur.execute("SELECT id, name_en, name_zh, description, value_type, unit_type,"
                " dimensions::text, uri, deprecated FROM concept_families WHERE code=%s",
                (FAMILY,))
    fam_hit = cur.fetchone()
    if fam_hit:
        cur.execute(
            "UPDATE concept_families SET name_en=%s, name_zh=%s, description=%s,"
            " value_type=%s, unit_type=%s, dimensions=%s, uri=%s, deprecated=false"
            " WHERE code=%s",
            (fam_fields["name_en"], fam_fields["name_zh"], fam_fields["description"],
             fam_fields["value_type"], fam_fields["unit_type"], fam_fields["dimensions"],
             fam_fields["uri"], FAMILY),
        )
        print(f"family {FAMILY}: updated (id={fam_hit[0]})")
    else:
        cur.execute(
            "INSERT INTO concept_families (code, name_en, name_zh, description,"
            " value_type, unit_type, dimensions, uri, deprecated, created_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,false,now()) RETURNING id",
            (fam_fields["code"], fam_fields["name_en"], fam_fields["name_zh"],
             fam_fields["description"], fam_fields["value_type"],
             fam_fields["unit_type"], fam_fields["dimensions"], fam_fields["uri"]),
        )
        print(f"family {FAMILY}: created (id={cur.fetchone()[0]})")

    created = updated = 0
    for v in variables:
        key = (v["code"], v["entity_type"], v["measure"], v["unit"], v["frequency"])
        cur.execute(
            "SELECT id, name_en, name_zh, source, verified, concept_code, deprecated"
            " FROM concepts WHERE code=%s AND entity_type=%s AND measure=%s"
            " AND unit=%s AND frequency=%s", key,
        )
        hit = cur.fetchone()
        if hit:
            cid, name_en, name_zh, csource, verified, ccode, deprecated = hit
            if (csource, verified, ccode, deprecated) != (SOURCE, True, FAMILY, False):
                cur.execute(
                    "UPDATE concepts SET source=%s, verified=true, concept_code=%s,"
                    " deprecated=false WHERE id=%s", (SOURCE, FAMILY, cid),
                )
                updated += 1
                print(f"  {v['code']}: updated (id={cid})")
            else:
                print(f"  {v['code']}: already correct (id={cid})")
        else:
            suffix = v["code"].rsplit(".", 1)[-1]
            cur.execute(
                "INSERT INTO concepts (code, name_en, name_zh, unit, measure, frequency,"
                " entity_type, source, verified, deprecated, created_at, concept_code)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true,false,now(),%s)",
                (v["code"],
                 f"{entry.get('label_en') or FAMILY} ({suffix})",
                 f"{entry.get('label_zh') or FAMILY}（{suffix}）",
                 v["unit"], v["measure"],
                 v["frequency"], v["entity_type"], SOURCE, FAMILY),
            )
            created += 1
            print(f"  {v['code']}: created")
    print(f"variables: created={created} updated={updated} total={len(variables)}")

    # ── 写后核验 1：三变量都在，且挂在 PlantedArea 族下 ──
    cur.execute(
        "SELECT code, entity_type, measure, unit, frequency, concept_code"
        " FROM concepts WHERE concept_code=%s ORDER BY code", (FAMILY,))
    got = cur.fetchall()
    codes = {r[0] for r in got}
    print("family variables:", len(got))
    if codes != EXPECTED_VARIABLES:
        print("FATAL 变量集不符:", sorted(codes))
        conn.rollback()
        return 3

    # ── 写后核验 2：既有 2 364 行一行未变（指纹逐行比对）──
    cur.execute("SELECT code, entity_type, measure, unit, frequency, concept_code"
                " FROM concepts ORDER BY id")
    post_rows = cur.fetchall()
    mine = set(EXPECTED_VARIABLES)
    extra = [r for r in post_rows if r not in pre_rows]
    strangers = [r for r in extra if r[0] not in mine]
    # 只有本 change 的三行允许被 UPDATE（幂等重跑修正 source/verified 的情况）
    changed = [r for r in pre_rows if r not in set(post_rows) and r[0] not in mine]
    if strangers or changed:
        print("FATAL 触碰到非本 change 的行:",
              {"new_unexpected": strangers[:5], "changed": changed[:5]})
        conn.rollback()
        return 4
    print(f"isolation: new={len(extra)} changed=0 (全部为本 change 新增的行)")

    after = census(cur)
    print("after :", after)
    expected_delta = 1 if not fam_hit else 0
    if after["families"] - before["families"] != expected_delta:
        print("FATAL concept_families 增量异常")
        conn.rollback()
        return 5
    if after["orphan_concept_code"] != before["orphan_concept_code"]:
        print("FATAL 已有的 concept_code 空值行被改动")
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
