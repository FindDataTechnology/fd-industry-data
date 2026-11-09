#!/usr/bin/env python3
"""Task 3.6 — register the 33-entity hotel monitoring universe.

Truth source: docs/hotel-universe.yaml (33 = CN 6 / HK 5 / US 14 / TW 4 / JP 2 /
EU 2; the US 14 splits into 10 operators + 4 OTA platforms, which is the
"6/5/10/4/2/4/2" split written in tasks.md 3.6).

Registration discipline — mirrors scripts/register_restaurant_universe.py:

* entity_type is always ``stock``;
* EXISTING ``(entity_type, code)`` rows are REUSED, never duplicated — the
  table already carries A-share 6-digit codes, US tickers and ``.HK`` codes;
* names are backfilled only where NULL (never overwritten, so market suffixes
  such as ``*ST西旅`` stay as the market writes them);
* lane mapping is recorded twice: ``entities.metadata_json`` (query surface the
  MCP tools read) and ``entity_source_identifiers`` (source='hotel-universe'
  plus ``exchange-ticker:<exchange>``);
* rows whose upstream verification FAILED carry ``verified: {ticker: false,
  lanes: false}`` in metadata, so read paths can present unavailability instead
  of silently rendering a number. EU rows additionally carry ``lanes: []``
  ("no lane in phase 1");
* idempotent: a second run reports ``created=0 updated=0``.

Usage
-----
    source ~/.config/finddata/dashboards.env
    python3 scripts/register_hotel_universe.py            # dry-run (ROLLBACK)
    python3 scripts/register_hotel_universe.py --apply    # commit
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

import psycopg2
import yaml

UNIVERSE = Path(__file__).resolve().parent.parent / "docs" / "hotel-universe.yaml"
REGIONS = {"CN": 6, "HK": 5, "US": 14, "TW": 4, "JP": 2, "EU": 2}
SEGMENTS = {
    "luxury-upscale", "midscale", "economy-budget",
    "serviced-apartment", "resort-casino", "OTA-platform",
}
PLATFORM_SEGMENT = "OTA-platform"
# tasks.md 3.6 口径：运营主体 6/5/10/4/2 = 27，平台侧 4，欧股 2 → 33
EXPECTED_PLATFORMS = 4
EXPECTED_UNVERIFIED = 8  # 台股 4（TWSE openapi 不可达）+ 日股 2（无 EDINET key）+ 欧股 2（无车道）
LANES = {
    "cninfo-operating", "fd-cn-report", "twse-monthly-revenue",
    "hkex-disclosure", "sec-edgar-facts", "sec-edgar-8k-operating", "edinet-api",
}
#: 每个 region **允许**挂的车道集（不是「必须恰好等于」）。波3 给 5 家美股加了
#: 8-K 经营数据车道（MAR/HLT/WH/H/CHH），其余美股只有财报车道 —— 逐家求等集
#: 会把真实载体差异判成漂移。2026-11-08：本脚本还留着严格等集，自检直接硬失败，
#: 宇宙注册因此**完全无法重跑**（同一缺陷在 tests/test_hotel_universe_doc.py
#: 里 3.6 已修，脚本侧漏了）。
REGION_LANES = {
    "CN": {"cninfo-operating", "fd-cn-report"},
    "HK": {"hkex-disclosure"},
    "US": {"sec-edgar-facts", "sec-edgar-8k-operating"},
    "TW": {"twse-monthly-revenue"},
    "JP": {"edinet-api"},
    "EU": set(),  # 无车道·一期不可得
}
#: 美股必须挂财报车道；8-K 经营数据车道恰好这几家（按实测载体）。
US_REQUIRED_LANE = "sec-edgar-facts"
US_8K_LANE = "sec-edgar-8k-operating"
US_8K_CODES = {"MAR", "HLT", "WH", "H", "CHH"}
ENTITY_TYPE = "stock"


def load_universe() -> list[dict]:
    doc = yaml.safe_load(UNIVERSE.read_text(encoding="utf-8"))
    rows = doc["companies"]
    problems: list[str] = []

    if len(rows) != 33:
        problems.append(f"宇宙应为 33 家，实为 {len(rows)} 家")
    codes = [r["code"] for r in rows]
    if len(set(codes)) != len(codes):
        dupes = {c for c in codes if codes.count(c) > 1}
        problems.append(f"代码重复: {sorted(dupes)}")
    for region, want in REGIONS.items():
        got = sum(1 for r in rows if r["region"] == region)
        if got != want:
            problems.append(f"region {region} 应为 {want} 家，实为 {got} 家")
    platforms = sum(1 for r in rows if r["segment"] == PLATFORM_SEGMENT)
    if platforms != EXPECTED_PLATFORMS:
        problems.append(f"平台侧应为 {EXPECTED_PLATFORMS} 家，实为 {platforms} 家")
    unverified = sum(1 for r in rows if not r.get("verified", {}).get("ticker"))
    if unverified != EXPECTED_UNVERIFIED:
        problems.append(f"票符未核实应为 {EXPECTED_UNVERIFIED} 家，实为 {unverified} 家")

    for r in rows:
        for field in ("code", "name_zh", "name_en", "region", "exchange", "segment"):
            if not r.get(field):
                problems.append(f"{r.get('code')}: 缺字段 {field}")
        if "lanes" not in r:
            problems.append(f"{r['code']}: 缺字段 lanes（欧股须显式空列表）")
        verified = r.get("verified")
        if not isinstance(verified, dict) or "ticker" not in verified or "lanes" not in verified:
            problems.append(f"{r['code']}: verified 须含 ticker/lanes 两项布尔")
        if r.get("segment") not in SEGMENTS:
            problems.append(f"{r['code']}: segment {r.get('segment')} 不在口径集")
        for lane in r.get("lanes") or []:
            if lane not in LANES:
                problems.append(f"{r['code']}: lane {lane} 不在车道集")
        # 车道-地区一致性：不允许「日本公司挂美股车道」这类错配。
        # 判据是**子集**（lane ∈ region 允许集），不是等集 —— 载体差异不算漂移。
        allowed = REGION_LANES[r["region"]]
        got_lanes = set(r.get("lanes") or [])
        if not got_lanes <= allowed:
            problems.append(
                f"{r['code']}: lanes {sorted(got_lanes)} 含 region {r['region']} 不允许的车道（允许 {sorted(allowed)}）"
            )
        if r["region"] == "US" and US_REQUIRED_LANE not in got_lanes:
            problems.append(f"{r['code']}: 美股必须挂 {US_REQUIRED_LANE} 车道")
        # 8-K 经营数据车道按实测载体钉死（多挂=谎称可解析，少挂=数据面缩水）
        if r["region"] == "US":
            has_8k = US_8K_LANE in got_lanes
            if has_8k != (r["code"] in US_8K_CODES):
                problems.append(
                    f"{r['code']}: {US_8K_LANE} 车道与实测载体不符（应为 {r['code'] in US_8K_CODES}）"
                )
        # 无车道的实体必须是 verified.lanes=False（不得谎称可达）
        if not r.get("lanes") and r.get("verified", {}).get("lanes"):
            problems.append(f"{r['code']}: 无车道却标记 lanes verified=true")

    excluded = doc.get("excluded") or []
    for r in excluded:
        if r["code"] in codes:
            problems.append(f"{r['code']}: 同时出现在 excluded 与 companies")
    if problems:
        raise SystemExit("宇宙真源自检失败:\n  " + "\n  ".join(problems))
    return rows


def meta_for(company: dict) -> dict:
    """Lane/caliber metadata written into entities.metadata_json."""
    return {
        # `registry` 是 MCP 工具（fd-open-data-mcp hotel_universe）的筛选键，`list` 是人读的
        # 台账名。两者必须同时写：2026-11-08 实测工具按 `metadata_json->>'registry'`
        # 过滤，而本脚本只写了 `list` → `hotel_universe` 恒返回 0 家（"宇宙为空"），
        # 数据其实一直在库里。改动一侧就会再次漂移，所以两个键都固化在这里。
        "registry": "hotel-universe",
        "list": "hotel-universe",
        "change": "add-hotel-vertical",
        "task": "3.6",
        "region": company["region"],
        "exchange": company["exchange"],
        "segment": company["segment"],
        "vertical": "hotel",
        "ticker": company["code"],
        "lanes": list(company.get("lanes") or []),
        "verified": dict(company["verified"]),
        "notes": company.get("notes"),
        "truth_source": "fd-industry-data/docs/hotel-universe.yaml",
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
            # 名字只补空、不覆盖既有（既有值可能带行情后缀如 *ST西旅）
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
            ("hotel-universe", c["code"]),
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

    # ── 写后核验：33 家全部回来，metadata 带 region/segment/lanes/verified ────
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
            if not (r[3] or {}).get("segment")
            or "lanes" not in (r[3] or {})
            or "verified" not in (r[3] or {})]
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
        print("FATAL metadata 缺 segment/lanes/verified:", weak)
        conn.rollback()
        return 3

    seg = Counter(r[3]["segment"] for r in got.values())
    reg = Counter(r[3]["region"] for r in got.values())
    role = Counter("platform" if r[3]["segment"] == PLATFORM_SEGMENT else "operator"
                   for r in got.values())
    nolaned = [c for c, r in got.items() if not r[3]["lanes"]]
    unverified = [c for c, r in got.items() if not r[3]["verified"].get("ticker")]
    print("region :", dict(reg))
    print("segment:", dict(seg))
    print("role   :", dict(role))
    print(f"no-lane: {len(nolaned)} {nolaned}")
    print(f"unverified-ticker: {len(unverified)} {unverified}")
    assert set(seg) <= SEGMENTS, seg
    assert all(reg[k] == v for k, v in REGIONS.items()), reg
    assert role["platform"] == EXPECTED_PLATFORMS, role
    assert len(unverified) == EXPECTED_UNVERIFIED, unverified

    if args.apply:
        conn.commit()
        print("APPLIED (committed)")
    else:
        conn.rollback()
        print("DRY-RUN (rolled back; 用 --apply 落库)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
