#!/usr/bin/env python3
"""Task 3.1 — register the 35-company restaurant monitoring universe.

Truth source: docs/restaurant-universe.yaml (35 = HK 10 / CN 6 / US 12 / TW 3 /
JP 4, each with region / segment / ticker / lane mapping).

Registration discipline (same shape as fd-open-data-mcp/scripts/sync_xb_entities.py):

* entity_type is always ``stock`` (protocol vocabulary: listed equity issues
  keyed by exchange-suffixed ticker);
* EXISTING entities are REUSED, never duplicated — the table already carries
  9,544 ``stock`` rows (A-share 6-digit codes, US tickers, ``.HK`` codes), so the
  script resolves by ``(entity_type, code)`` first and only inserts what is
  genuinely missing (this run: BROS / WING / the 10 HK / 3 TW / 4 JP codes);
* names and metadata are **backfilled** where the existing row has them NULL
  (US tickers landed without names) and merged — never overwritten -- otherwise;
* lane mapping is recorded twice: in ``entities.metadata_json`` (query surface
  the MCP tools read) and in ``entity_source_identifiers`` (cross-source id
  ledger, source='restaurant-universe');
* idempotent: a second run reports ``created=0`` and leaves every row unchanged.

Usage
-----
    source ~/.config/finddata/dashboards.env
    python3 scripts/register_restaurant_universe.py            # dry-run (ROLLBACK)
    python3 scripts/register_restaurant_universe.py --apply    # commit
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg2
import yaml

UNIVERSE = Path(__file__).resolve().parent.parent / "docs" / "restaurant-universe.yaml"
REGIONS = {"HK": 10, "CN": 6, "US": 12, "TW": 3, "JP": 4}
SEGMENTS = {"full-service", "limited-service", "cafe-teahouse", "prepared-food"}
LANES = {
    "cninfo-operating", "fd-cn-report", "twse-monthly-revenue",
    "hkex-disclosure", "sec-edgar-facts", "edinet-api",
}
ENTITY_TYPE = "stock"


def load_universe() -> list[dict]:
    doc = yaml.safe_load(UNIVERSE.read_text(encoding="utf-8"))
    rows = doc["companies"]
    problems: list[str] = []

    if len(rows) != 35:
        problems.append(f"宇宙应为 35 家，实为 {len(rows)} 家")
    codes = [r["code"] for r in rows]
    if len(set(codes)) != len(codes):
        dupes = {c for c in codes if codes.count(c) > 1}
        problems.append(f"代码重复: {sorted(dupes)}")
    for region, want in REGIONS.items():
        got = sum(1 for r in rows if r["region"] == region)
        if got != want:
            problems.append(f"region {region} 应为 {want} 家，实为 {got} 家")
    for r in rows:
        for field in ("code", "name_zh", "name_en", "region", "exchange",
                      "segment", "lanes"):
            if not r.get(field):
                problems.append(f"{r.get('code')}: 缺字段 {field}")
        if r.get("segment") not in SEGMENTS:
            problems.append(f"{r['code']}: segment {r.get('segment')} 不在口径集")
        for lane in r.get("lanes") or []:
            if lane not in LANES:
                problems.append(f"{r['code']}: lane {lane} 不在车道集")
        # 车道-地区一致性：不允许出现「日本公司挂美股车道」这类错配
        want = {
            "HK": {"hkex-disclosure"},
            "CN": {"cninfo-operating", "fd-cn-report"},
            "US": {"sec-edgar-facts"},
            "TW": {"twse-monthly-revenue"},
            "JP": {"edinet-api"},
        }[r["region"]]
        if set(r["lanes"]) != want:
            problems.append(f"{r['code']}: lanes {r['lanes']} 与 region {r['region']} 期望 {sorted(want)} 不符")
    if problems:
        raise SystemExit("宇宙真源自检失败:\n  " + "\n  ".join(problems))
    return rows


def meta_for(company: dict) -> dict:
    """Lane/caliber metadata written into entities.metadata_json."""
    return {
        "list": "restaurant-universe",
        "change": "add-restaurant-vertical",
        "task": "3.1",
        "region": company["region"],
        "exchange": company["exchange"],
        "segment": company["segment"],
        "ticker": company["code"],
        "lanes": list(company["lanes"]),
        "notes": company.get("notes"),
        "truth_source": "fd-industry-data/docs/restaurant-universe.yaml",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("FATAL: $FD_OPEN_DATA_DSN 未设置（source ~/.config/finddata/dashboards.env）")

    rows = load_universe()

    conn = psycopg2.connect(args.dsn)
    conn.autocommit = False
    cur = conn.cursor()
    created = updated = reused = 0
    touched: list[tuple[int, str]] = []

    for c in rows:
        cur.execute(
            "SELECT id, name_zh, name_en, metadata_json FROM entities"
            " WHERE entity_type=%s AND code=%s",
            (ENTITY_TYPE, c["code"]),
        )
        hit = cur.fetchone()
        meta = meta_for(c)
        if hit:
            eid, name_zh, name_en, old_meta = hit
            reused += 1
            new_meta = dict(old_meta or {})
            new_meta.update(meta)
            # 名字只补空、不覆盖既有（既有值可能带行情后缀如 ST绝味）
            set_zh = name_zh or c["name_zh"]
            set_en = name_en or c["name_en"]
            if (set_zh, set_en, json.dumps(new_meta, sort_keys=True)) != (
                    name_zh, name_en, json.dumps(old_meta or {}, sort_keys=True)):
                cur.execute(
                    "UPDATE entities SET name_zh=%s, name_en=%s, metadata_json=%s,"
                    " updated_at=now() WHERE id=%s",
                    (set_zh, set_en, json.dumps(new_meta, ensure_ascii=False), eid),
                )
                updated += 1
        else:
            cur.execute(
                "INSERT INTO entities (entity_type, code, name_zh, name_en,"
                " metadata_json, updated_at) VALUES (%s,%s,%s,%s,%s,now())"
                " RETURNING id",
                (ENTITY_TYPE, c["code"], c["name_zh"], c["name_en"],
                 json.dumps(meta, ensure_ascii=False)),
            )
            eid = cur.fetchone()[0]
            created += 1

        # 跨源标识账本：本宇宙一份 + 交易所原生票符一份（可回指到车道）
        for source, identifier in (
            ("restaurant-universe", c["code"]),
            (f"exchange-ticker:{c['exchange']}", c["code"].split(".")[0]),
        ):
            cur.execute(
                "SELECT 1 FROM entity_source_identifiers"
                " WHERE entity_type=%s AND entity_id=%s AND source=%s AND identifier=%s",
                (ENTITY_TYPE, eid, source, identifier),
            )
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO entity_source_identifiers"
                    " (entity_type, entity_id, source, identifier, created_at)"
                    " VALUES (%s,%s,%s,%s,now())",
                    (ENTITY_TYPE, eid, source, identifier),
                )
        touched.append((eid, c["code"]))

    # ── 写后核验：35 家全部回来，metadata 带 region/segment/lanes ────────────
    codes = [c["code"] for c in rows]
    cur.execute(
        "SELECT code, name_zh, name_en, metadata_json FROM entities"
        " WHERE entity_type=%s AND code=ANY(%s)",
        (ENTITY_TYPE, codes),
    )
    got = {r[0]: r for r in cur.fetchall()}
    missing = [c for c in codes if c not in got]
    nameless = [c for c, r in got.items() if not r[1] or not r[2]]
    weak = [c for c, r in got.items()
            if not (r[3] or {}).get("segment") or not (r[3] or {}).get("lanes")]
    print(f"universe: reused={reused} created={created} updated={updated}"
          f" total={len(got)}/{len(codes)}")
    if missing:
        print("FATAL 未注册:", missing)
        conn.rollback()
        return 3
    if nameless:
        print("FATAL 中英文名缺失:", nameless)
        conn.rollback()
        return 3
    if weak:
        print("FATAL metadata 缺 segment/lanes:", weak)
        conn.rollback()
        return 3

    # 分段分布（提案要求口径显式：紫燕/绝味/巴比 = prepared-food）
    from collections import Counter
    seg = Counter(r[3]["segment"] for r in got.values())
    reg = Counter(r[3]["region"] for r in got.values())
    print("segment:", dict(seg))
    print("region :", dict(reg))
    assert set(seg) <= SEGMENTS, seg
    assert all(reg[k] == v for k, v in REGIONS.items()), reg

    if args.apply:
        conn.commit()
        print("APPLIED (committed)")
    else:
        conn.rollback()
        print("DRY-RUN (rolled back; 用 --apply 落库)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
