#!/usr/bin/env python3
"""Task 4.3 — register the 7-entity double-tier flower monitoring universe.

Truth source: docs/flower-universe.yaml (7 = pure-flower 2 / adjacency 5).

Registration discipline — mirrors scripts/register_hotel_universe.py and
scripts/register_restaurant_universe.py:

* entity_type is always ``stock``;
* EXISTING ``(entity_type, code)`` rows are REUSED, never duplicated;
* names are backfilled only where NULL (never overwritten);
* lane mapping is recorded twice: ``entities.metadata_json`` (the query surface
  MCP tools read) and ``entity_source_identifiers`` (source='flower-universe'
  plus ``exchange-ticker:<exchange>``);
* ``flower_revenue_share`` rides in metadata_json — ``None`` means **explicitly
  unavailable**, and is written as JSON null, never as 0 (a 0 would silently
  rank last in share-sorted boards instead of being excluded);
* rows whose upstream verification FAILED carry ``verified: {ticker: false,
  segments: false}`` so read paths present unavailability instead of a number;
* idempotent: a second run reports ``created=0 updated=0``.

Usage
-----
    source ~/.config/finddata/dashboards.env
    python3 scripts/register_flower_universe.py            # dry-run (ROLLBACK)
    python3 scripts/register_flower_universe.py --apply    # commit
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

UNIVERSE = Path(__file__).resolve().parent.parent / "docs" / "flower-universe.yaml"
ENTITY_TYPE = "stock"

SEGMENTS = {
    "cutflower-trade",
    "breeding-propagation",
    "horticultural-supplies",
    "greenhouse-equipment",
    "greenhouse-grower",
    "landscape-nursery",
}
TIERS = {"pure-flower", "adjacency"}
LANES = {
    "sec-edgar-facts",
    "edinet-api",
    "stock-zygc",
    "hkex-disclosure",
    "cninfo-operating",
    "fd-cn-report",
    "twse-monthly-revenue",
}
REGION_LANES = {
    "US": {"sec-edgar-facts"},
    "JP": {"edinet-api"},
    "CN": {"stock-zygc"},
    "HK": {"hkex-disclosure"},
}
EXPECTED_TOTAL = 7
EXPECTED_PURE = 2
EXPECTED_ADJACENCY = 5
EXPECTED_UNVERIFIED_TICKER = 1  # 1377.T —— EDINET key 未申请，票符未上游核实
EXPECTED_UNAVAILABLE_SHARE = 5  # FLWS/002200 有值；其余 5 家分部占比不可得


def load_universe() -> list[dict]:
    doc = yaml.safe_load(UNIVERSE.read_text(encoding="utf-8"))
    rows = doc["companies"]
    problems: list[str] = []

    if len(rows) != EXPECTED_TOTAL:
        problems.append(f"宇宙应为 {EXPECTED_TOTAL} 家，实为 {len(rows)} 家")
    pure = sum(1 for r in rows if r.get("tier") == "pure-flower")
    if pure != EXPECTED_PURE:
        problems.append(f"纯花卉层应为 {EXPECTED_PURE} 家，实为 {pure} 家")
    adj = sum(1 for r in rows if r.get("tier") == "adjacency")
    if adj != EXPECTED_ADJACENCY:
        problems.append(f"邻接层应为 {EXPECTED_ADJACENCY} 家，实为 {adj} 家")

    codes = [r["code"] for r in rows]
    if len(set(codes)) != len(codes):
        dupes = {c for c in codes if codes.count(c) > 1}
        problems.append(f"代码重复: {sorted(dupes)}")

    for r in rows:
        for field in ("code", "name_zh", "name_en", "region", "exchange", "segment", "tier"):
            if not r.get(field):
                problems.append(f"{r.get('code')}: 缺字段 {field}")
        if "lanes" not in r:
            problems.append(f"{r['code']}: 缺字段 lanes（不可达车道须显式声明）")
        if "flower_revenue_share" not in r:
            problems.append(f"{r['code']}: 缺字段 flower_revenue_share（不可得须显式 null）")
        elif r["flower_revenue_share"] is not None:
            share = r["flower_revenue_share"]
            if isinstance(share, bool) or not isinstance(share, (int, float)):
                problems.append(f"{r['code']}: flower_revenue_share 须为数值或 null，实为 {share!r}")
            elif not (0.0 <= float(share) <= 1.0):
                problems.append(f"{r['code']}: flower_revenue_share 须在 [0,1]，实为 {share}")
        verified = r.get("verified")
        if not isinstance(verified, dict) or "ticker" not in verified or "segments" not in verified:
            problems.append(f"{r['code']}: verified 须含 ticker/segments 两项布尔")
        if r.get("segment") not in SEGMENTS:
            problems.append(f"{r['code']}: segment {r.get('segment')} 不在闭集")
        if r.get("tier") not in TIERS:
            problems.append(f"{r['code']}: tier {r.get('tier')} 不在闭集")
        if r.get("region") not in REGION_LANES:
            problems.append(f"{r['code']}: region {r.get('region')} 无车道映射（港股暂为空集）")
            continue
        for lane in r.get("lanes") or []:
            if lane not in LANES:
                problems.append(f"{r['code']}: lane {lane} 不在车道集")
        want = REGION_LANES[r["region"]]
        if set(r.get("lanes") or []) != want:
            problems.append(
                f"{r['code']}: lanes {r.get('lanes')} 与 region {r['region']} 期望 {sorted(want)} 不符"
            )
        if not r.get("lanes") and r.get("verified", {}).get("segments"):
            problems.append(f"{r['code']}: 无车道却标记 segments verified=true")
        # 真正要抓的是「声称已核实却没有数」这类自相矛盾；不可得本身合规。
        # 1377.T 即此情形：业务口径上是纯花卉，但 EDINET key 未申请 ⇒ 分部未实测 ⇒
        # 占比显式不可得、verified.segments=false。它留在纯花卉层，但不出现在任何
        # 以占比排序的面里（spec: 不可得实体不入排名）。
        if r.get("flower_revenue_share") is None and r.get("verified", {}).get("segments"):
            problems.append(
                f"{r['code']}: verified.segments=true 却把 flower_revenue_share 记为不可得（自相矛盾）"
            )

    excluded_codes = [r["code"] for r in (doc.get("exclusions") or [])]
    for code in excluded_codes:
        if code in codes:
            problems.append(f"{code}: 同时出现在 exclusions 与 companies")

    unverified = sum(1 for r in rows if not r.get("verified", {}).get("ticker"))
    if unverified != EXPECTED_UNVERIFIED_TICKER:
        problems.append(
            f"票符未核实应为 {EXPECTED_UNVERIFIED_TICKER} 家，实为 {unverified} 家"
        )
    unavailable = sum(1 for r in rows if r.get("flower_revenue_share") is None)
    if unavailable != EXPECTED_UNAVAILABLE_SHARE:
        problems.append(
            f"占比不可得应为 {EXPECTED_UNAVAILABLE_SHARE} 家，实为 {unavailable} 家"
        )

    if problems:
        raise SystemExit("宇宙真源自检失败:\n  " + "\n  ".join(problems))
    return rows


def meta_for(company: dict) -> dict:
    """Lane/caliber metadata written into entities.metadata_json."""
    return {
        "list": "flower-universe",
        "change": "add-flower-vertical",
        "task": "4.3",
        "region": company["region"],
        "exchange": company["exchange"],
        "segment": company["segment"],
        "tier": company["tier"],
        "vertical": "flower",
        "ticker": company["code"],
        "lanes": list(company.get("lanes") or []),
        # None = 显式不可得（绝不写 0 —— 0 会让该实体在占比排名里垫底而不是被排除）
        "flower_revenue_share": company.get("flower_revenue_share"),
        "share_caliber": "share of revenue attributable to flower/ornamental business, "
                         "from verified segment or main-business disclosure",
        "verified": dict(company["verified"]),
        "notes": company.get("notes"),
        "truth_source": "fd-industry-data/docs/flower-universe.yaml",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit(
            "FATAL: $FD_OPEN_DATA_DSN 未设置（source ~/.config/finddata/dashboards.env）"
        )

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
            set_zh = name_zh or c["name_zh"]
            set_en = name_en or c["name_en"]
            if (set_zh, set_en, json.dumps(new_meta, sort_keys=True)) != (
                name_zh, name_en, json.dumps(old_meta or {}, sort_keys=True)
            ):
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
                (
                    ENTITY_TYPE,
                    c["code"],
                    c["name_zh"],
                    c["name_en"],
                    json.dumps(meta, ensure_ascii=False),
                ),
            )
            eid = cur.fetchone()[0]
            created += 1

        # 跨源标识账本：本宇宙一份 + 交易所原生票符一份（可回指到车道）
        for source, identifier in (
            ("flower-universe", c["code"]),
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

    # ── 写后核验：7 家全部回来，metadata 带 tier/segment/lanes/verified/占比 ──
    codes = [c["code"] for c in rows]
    cur.execute(
        "SELECT code, name_zh, name_en, metadata_json FROM entities"
        " WHERE entity_type=%s AND code=ANY(%s)",
        (ENTITY_TYPE, codes),
    )
    got = {r[0]: r for r in cur.fetchall()}
    missing = [c for c in codes if c not in got]
    nameless = [c for c, r in got.items() if not r[1] or not r[2]]
    weak = [
        c
        for c, r in got.items()
        if not (r[3] or {}).get("segment")
        or "lanes" not in (r[3] or {})
        or "verified" not in (r[3] or {})
        or "flower_revenue_share" not in (r[3] or {})
    ]
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
        print("FATAL metadata 缺 segment/lanes/verified/flower_revenue_share:", weak)
        conn.rollback()
        return 3

    tier = Counter(r[3]["tier"] for r in got.values())
    seg = Counter(r[3]["segment"] for r in got.values())
    reg = Counter(r[3]["region"] for r in got.values())
    no_share = [c for c, r in got.items() if r[3]["flower_revenue_share"] is None]
    unverified = [c for c, r in got.items() if not r[3]["verified"].get("ticker")]
    print("tier   :", dict(tier))
    print("region :", dict(reg))
    print("segment:", dict(seg))
    print(f"share-unavailable: {len(no_share)} {sorted(no_share)}")
    print(f"unverified-ticker: {len(unverified)} {sorted(unverified)}")
    assert set(seg) <= SEGMENTS, seg
    assert tier["pure-flower"] == EXPECTED_PURE, tier
    assert tier["adjacency"] == EXPECTED_ADJACENCY, tier
    assert len(unverified) == EXPECTED_UNVERIFIED_TICKER, unverified
    assert len(no_share) == EXPECTED_UNAVAILABLE_SHARE, no_share
    # 占比字段类型核验：null 就是 null，不是 0
    for c, r in got.items():
        share = r[3]["flower_revenue_share"]
        assert share is None or isinstance(share, (int, float)), (c, share)

    if args.apply:
        conn.commit()
        print("APPLIED (committed)")
    else:
        conn.rollback()
        print("DRY-RUN (rolled back; 用 --apply 落库)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
