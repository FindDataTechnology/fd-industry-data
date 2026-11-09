#!/usr/bin/env python3
"""Register the seven JF 外食 format segments as `industry` entities.

Needed because `jp-foodservice-trend` reports four 前年同月比 metrics × seven
segments (全体 + six formats) and the observation natural key
(concept_id, entity_type, entity_id, date, granularity, source_used) has no
segment column — the segment therefore has to be the entity, otherwise six of
seven rows per month would collide and be dropped by ON CONFLICT DO NOTHING.

Codes are namespaced `jp.foodservice.<slug>` so they never collide with the
GICS/sector industry rows (801010…) already in `entities`.

Idempotent: re-running reports created=0 updated=0. Dry-run by default.

Usage: ../fd-open-data-protocol/.venv/bin/python scripts/register_jp_foodservice_segments.py [--apply]
"""
from __future__ import annotations

import json
import os
import sys

import psycopg

# (upstream 全店 row label, code slug, name_zh, name_en)
SEGMENTS = [
    ("全体", "all", "日本外食市场 全体",
     "Japan foodservice market — all stores (全体)"),
    ("ファーストフード", "fast_food", "日本外食 快餐（ファーストフード）",
     "Japan foodservice — fast food (ファーストフード)"),
    ("ファミリーレストラン", "family_restaurant", "日本外食 家庭餐厅（ファミリーレストラン）",
     "Japan foodservice — family restaurant (ファミリーレストラン)"),
    ("パブレストラン/居酒屋", "pub_restaurant_izakaya", "日本外食 酒吧餐厅/居酒屋（パブレストラン/居酒屋）",
     "Japan foodservice — pub restaurant / izakaya (パブレストラン/居酒屋)"),
    ("ディナーレストラン", "dinner_restaurant", "日本外食 正餐餐厅（ディナーレストラン）",
     "Japan foodservice — dinner restaurant (ディナーレストラン)"),
    ("喫茶", "coffee_shop", "日本外食 喫茶（喫茶）",
     "Japan foodservice — coffee shop (喫茶)"),
    ("その他", "other", "日本外食 其他业态（その他）",
     "Japan foodservice — other formats (その他)"),
]

CODE_PREFIX = "jp.foodservice."
SOURCE_UNIT = "jp-foodservice-trend"


def main() -> int:
    apply_mode = "--apply" in sys.argv
    dsn = os.environ.get("FD_OPEN_DATA_DSN")
    if not dsn:
        print("FATAL: $FD_OPEN_DATA_DSN 未设置（source ~/.config/finddata/dashboards.env）")
        return 2

    created = updated = reused = 0
    conn = psycopg.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor()
    for label, slug, name_zh, name_en in SEGMENTS:
        code = CODE_PREFIX + slug
        meta = {"upstream_label": label, "source_unit": SOURCE_UNIT,
                "segment_basis": "JF 外食産業市場動向調査 全店 sheet"}
        cur.execute("SELECT id, name_zh, name_en, metadata_json FROM entities"
                    " WHERE entity_type = 'industry' AND code = %s", (code,))
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO entities (entity_type, code, name_en, name_zh,"
                " metadata_json, updated_at) VALUES ('industry', %s, %s, %s, %s, now())"
                " RETURNING id", (code, name_en, name_zh, json.dumps(meta)),
            )
            print(f"  create id={cur.fetchone()[0]} {code} {name_zh}")
            created += 1
        else:
            eid, old_zh, old_en, old_meta = row
            same = (old_zh == name_zh and old_en == name_en
                    and (old_meta or {}) == meta)
            if same:
                print(f"  reuse  id={eid} {code}")
                reused += 1
            else:
                cur.execute(
                    "UPDATE entities SET name_en=%s, name_zh=%s, metadata_json=%s,"
                    " updated_at=now() WHERE id=%s",
                    (name_en, name_zh, json.dumps(meta), eid),
                )
                print(f"  update id={eid} {code}")
                updated += 1

    if not apply_mode:
        conn.rollback()
        print(f"\nDRY-RUN：未写库（拟 created={created} updated={updated} reuse={reused}）。"
              "加 --apply 执行。")
        conn.close()
        return 0
    conn.commit()
    print(f"\nDONE: created={created} updated={updated} reused={reused} / {len(SEGMENTS)} 段")
    cur.execute("SELECT count(*) FROM entities WHERE entity_type='industry'"
                " AND code LIKE %s", (CODE_PREFIX + "%",))
    print(f"verify: jp.foodservice.* industry 实体 {cur.fetchone()[0]} 行")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
