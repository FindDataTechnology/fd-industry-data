#!/usr/bin/env python3
"""Hotel-vertical wave-0/3 — naming quality gate (EU series + company channels).

Same mechanism as ``scripts/naming_gate_restaurant.py`` (no new tables, no
invented pipeline):

1. ``concepts`` naming-layer fields (name_zh/name_en/unit/frequency/category)
   + ``verified`` boolean — one SQL transaction; a pre-change snapshot is
   written to ``output/supply-funnel/naming-before-snapshot-hotel.json``.
2. registry mirror (``registry_entries``, anchor
   ``fd_open_data/concepts/code/<code>``) via the protocol's
   ``PostgresRegistryStore``: upsert → ``set_verified(verified_by="rule:naming-gate")``.

Two groups, deliberately kept apart because their trend-gate dispositions differ:

**A. wave-0 EU series (6 concepts, ``scripts/hotel_extractors/eurostat_accommodation.py``)**

| code | periods | disposition |
|---|---|---|
| ``hotel.eu.turnover.accommodation.monthly`` | 139 | verified (>=24) |
| ``hotel.eu.nights.monthly`` | 188 | verified |
| ``hotel.eu.nights.foreign.monthly`` | 188 | verified |
| ``hotel.eu.nights.yoy.monthly`` | 188 | verified |
| ``hotel.eu.capacity.bed_places.annual`` | 21 | ``short_window`` (12–23) |
| ``hotel.eu.nights.annual`` | 21 | ``short_window`` (12–23) |

**B. wave-3 company channels (85 concepts, sources ``cninfo-operating`` /
``hkex-disclosure`` / ``sec-edgar-8k-operating``)**

Every one of them carries 1–11 periods — below even the 12-period short_window
floor — because the carriers themselves are short (锦江 two 2023 briefs, 00045
eleven quarters, 8-K five companies' last seven quarters). They are **not**
trend series: they exist for level comparison on the company board. They are
therefore registered with an explicit ``level_only`` declaration carrying the
real period count and the measured reason, so no reader can mistake them for
trend-qualified data. The alternative — leaving them ``verified=false`` — keeps
441 observations invisible on the read path, which is the state task 1.1
measured and task 1.2 has to fix.

**The price × volume pair the spec asks for has a missing side.** EU
accommodation has no price index (HICP ``CP11``/``CP112`` are 404 upstream) and
China's 住宿业 CPI does not exist in any lane (``nbs-stats`` has no such
category). So the nights concepts carry a ``pairing`` declaration naming the
absent counterpart and its reason — the board must render "missing side"
instead of computing a YoY spread against nothing.

Usage: python3 scripts/naming_gate_hotel.py [--apply]   (default dry-run)
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
SNAPSHOT_PATH = OUT_DIR / "naming-before-snapshot-hotel.json"

#: wave-0 EU accommodation/tourism series (6)
EU_CODES = [
    "hotel.eu.capacity.bed_places.annual",
    "hotel.eu.nights.annual",
    "hotel.eu.nights.foreign.monthly",
    "hotel.eu.nights.monthly",
    "hotel.eu.nights.yoy.monthly",
    "hotel.eu.turnover.accommodation.monthly",
]

#: wave-3 company-channel sources (85 concepts in total)
COMPANY_SOURCES = ("cninfo-operating", "hkex-disclosure", "sec-edgar-8k-operating")
COMPANY_EXPECTED = 85

#: wave-1 MCT sources — same ``level_only`` disposition (short history), but the
#: reason is different: the survey series is *discontinued*, not still filling.
MCT_SOURCES = ("mct-star-hotel", "mct-tourism-demand")

#: 12–23 期 → short_window（真实年频历史，不是滚动窗）
SW_PERIODS = {
    "hotel.eu.capacity.bed_places.annual": 21,
    "hotel.eu.nights.annual": 21,
}
SW_DECLARED_BY = "adjudication-review-2026-11-08-hotel-wave0"
SW_DECLARED_IN = "add-hotel-vertical task 1.2"

#: level-only 声明（<12 期；只做水平对照，不作趋势）
LEVEL_ONLY_DECLARED_BY = "measurement-2026-11-08-hotel-wave3-carriers"
LEVEL_ONLY_REASON = (
    "载体本身历史短：A 股只有锦江 2023 两份简报、港股只有 00045 十一季、"
    "美股 8-K 仅最近七季；<12 期不足以成趋势，只作同一时点的水平对照"
)
MCT_LEVEL_ONLY_REASON = (
    "文旅部星级饭店季报系列已停更（最新 2024Q3，2024-12-26 发布），本单元产出 "
    "2023Q2–2024Q3 共 5 期；<12 期且无后续期次，只作历史水平对照与断更基线，"
    "不得画趋势线"
)

#: 价量对照对的缺失侧（EU 无住宿价格指数；中国无住宿业 CPI 类目）
PAIRING = {
    "counterpart": "accommodation_price_index",
    "state": "missing",
    "reason": ("EU: HICP coicop=CP11/CP112 均 404 not available for dissemination；"
               "CN: nbs-stats 的 INDICATOR_CATEGORIES 无住宿业类目"),
    "debt": "D1/D5",
    "rule": "板面必须显式标注缺失侧，不得用餐饮 CP111 或营业额指数冒充住宿价格",
}

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


def level_only_decl(periods: int, reason: str = LEVEL_ONLY_REASON) -> dict:
    return {"trend_gate": {"periods": periods, "gate": ">=24",
                           "disposition": "level_only",
                           "reason": reason,
                           "decided_by": LEVEL_ONLY_DECLARED_BY}}


def six_field_complete(row) -> list[str]:
    return [c for c in ("code",) + FIELDS
            if row.get(c) is None or str(row.get(c)).strip() == ""]


def load_rows(cur, codes):
    cur.execute(f"SELECT {COLUMNS} FROM concepts WHERE code = ANY(%s)", (codes,))
    return {r["code"]: r for r in cur.fetchall()}


def company_codes(cur) -> list[str]:
    cur.execute(
        "SELECT code FROM concepts WHERE NOT deprecated"
        " AND source = ANY(%s) AND code LIKE 'hotel.%%' ORDER BY code",
        (list(COMPANY_SOURCES),),
    )
    return [r["code"] for r in cur.fetchall()]


def mct_codes(cur) -> list[str]:
    """Concepts produced by the MCT survey units (level_only disposition)."""
    cur.execute(
        "SELECT code FROM concepts WHERE NOT deprecated"
        " AND source = ANY(%s) AND code LIKE 'hotel.%%' ORDER BY code",
        (list(MCT_SOURCES),),
    )
    return [r["code"] for r in cur.fetchall()]


def source_of(rows: dict, code: str) -> str:
    row = rows.get(code)
    return str(row["source"]) if row else ""


def periods_by_code(cur, codes) -> dict[str, int]:
    cur.execute(
        "SELECT c.code, count(DISTINCT s.date) AS n FROM concepts c"
        " JOIN semantic_observations s ON s.concept_id = c.id"
        " WHERE c.code = ANY(%s) GROUP BY c.code", (codes,),
    )
    return {r["code"]: r["n"] for r in cur.fetchall()}


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
        (list(rows),),
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

    company = company_codes(cur)
    if len(company) != COMPANY_EXPECTED:
        print(f"FATAL: 企业侧概念数变了（期望 {COMPANY_EXPECTED}，实得 {len(company)}）"
              "——先查明来源，别让门静默放行一批新概念")
        conn.close()
        return 3
    mct = mct_codes(cur)
    all_codes = EU_CODES + company + mct

    rows = load_rows(cur, all_codes)
    problems = [c for c in all_codes if c not in rows]
    if problems:
        print("FATAL 前置断言失败:", *problems, sep="\n  ")
        conn.close()
        return 3
    skipped_verified = [c for c in all_codes if rows[c]["verified"]]
    if skipped_verified:
        print(f"跳过已 verified {len(skipped_verified)} 条（concepts 侧幂等）")
    active = [c for c in all_codes if not rows[c]["verified"]]
    if not active:
        print("concepts 侧无事可做（全部已 verified）——仍补 registry 镜像")
    else:
        incomplete = {c: six_field_complete(rows[c]) for c in active
                      if six_field_complete(rows[c])}
        if incomplete:
            print("FATAL: 六字段不齐（不得放行）:", incomplete)
            conn.close()
            return 6

        # code 唯一性 + registry semantic_code 占用预检
        cur.execute(
            "SELECT code, count(*) FROM concepts WHERE NOT deprecated AND code = ANY(%s)"
            " GROUP BY code HAVING count(*) > 1", (active,))
        if cur.fetchall():
            print("FATAL: 目标 code 在非 deprecated 概念内重复")
            conn.close()
            return 4
        cur.execute(
            "SELECT semantic_code, source_db, native_code FROM registry_entries"
            " WHERE semantic_code = ANY(%s) AND source_db <> 'fd_open_data'",
            (active,))
        taken = cur.fetchall()
        if taken:
            print(f"FATAL: semantic_code 已被其他源占用: {taken}")
            conn.close()
            return 5

    periods = periods_by_code(cur, all_codes)

    if active:
        print(f"== 命名门计划（{len(active)} 概念：EU {len(EU_CODES)} + "
              f"企业 {len(company)} + MCT {len(mct)}）==")
        for code in active:
            row = rows[code]
            n = periods.get(code, 0)
            if code in SW_PERIODS:
                tag = f" [short_window {SW_PERIODS[code]}期]"
            elif n and n < 12:
                tag = f" [level_only {n}期]"
            else:
                tag = f" [verified {n}期]"
            print(f"  [PASS→verified] id={row['id']} {code} (source={row['source']}){tag}")

    print("\n价量对照对: 住宿业价格指数缺失侧 → 已写入 nights 概念 provenance.pairing")
    print(f"  ({PAIRING['debt']}) {PAIRING['reason']}")

    if not apply_mode:
        print("\nDRY-RUN：未写库。加 --apply 执行。")
        conn.close()
        return 0

    if active:
        snapshot(cur, {c: rows[c] for c in active})

        ids = [rows[c]["id"] for c in active]
        try:
            cur.execute("UPDATE concepts SET verified = TRUE WHERE id = ANY(%s)", (ids,))
            conn.commit()
            print(f"\nconcepts 已提交：{len(ids)} 行 verified=TRUE（零命名字段变更）")
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            print(f"FATAL: concepts 更新失败已回滚（零残留）: {exc}")
            conn.close()
            return 7
    else:
        ids = []

    # ── registry 镜像 ────────────────────────────────────────────────────────
    sys.path.insert(0, PROTOCOL_ROOT)
    try:
        import pydantic  # noqa: F401
    except ModuleNotFoundError:
        print(
            "FATAL: 缺 pydantic —— registry 镜像需要协议仓依赖；concepts 侧已提交。\n"
            "用协议仓的 venv 重跑（concepts 侧会按幂等跳过，只补 registry）：\n"
            "  PYTHONPATH=/Users/chengsishi/finddata/fd-open-data-protocol \\\n"
            "  /Users/chengsishi/finddata/fd-open-data-protocol/.venv/bin/python \\\n"
            "  scripts/naming_gate_hotel.py --apply"
        )
        conn.close()
        return 8
    from fd_open_data_protocol.registry_store import PostgresRegistryStore  # noqa: E402
    from fd_open_data_protocol.schema import RegistryEntry  # noqa: E402

    rows_after = load_rows(cur, all_codes)
    entries = []
    for code, row in sorted(rows_after.items()):
        provenance = {"measure": row["measure"], "entity_type": row["entity_type"],
                      "source": row["source"], "code": row["code"]}
        if code in SW_PERIODS:
            provenance.update(sw_decl(SW_PERIODS[code]))
        elif code not in EU_CODES:
            reason = (MCT_LEVEL_ONLY_REASON
                      if row["source"] in MCT_SOURCES else LEVEL_ONLY_REASON)
            provenance.update(level_only_decl(periods.get(code, 0), reason))
        if code.startswith("hotel.eu.nights"):
            provenance["pairing"] = PAIRING
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
    # set_verified 返回「本次新置」数：幂等重跑时已 verified 的锚点不计入，
    # 所以 n < len(anchors) 是正常的；是否真全绿看下面的读路径复核。
    print(f"registry set_verified: {n} 条新置 / {len(anchors)} 条锚点"
          " (verified_by=rule:naming-gate；已 verified 的不计入)")

    # ── 复核 ─────────────────────────────────────────────────────────────────
    rows_final = load_rows(cur, all_codes)
    bad_six = [(c, six_field_complete(r)) for c, r in rows_final.items()
               if six_field_complete(r)]
    bad_v = [c for c, r in rows_final.items() if not r["verified"]]
    print(f"concepts 复核: 六字段不齐={bad_six or '无'}; 未 verified={bad_v or '无'}")
    cur.execute(
        "SELECT native_code, provenance ? 'short_window' AS sw,"
        " provenance ? 'trend_gate' AS lo, provenance ? 'pairing' AS pr,"
        " verified, verified_by FROM registry_entries"
        " WHERE source_db='fd_open_data' AND source_table='concepts'"
        " AND native_code = ANY(%s) ORDER BY native_code", (all_codes,))
    reg = cur.fetchall()
    print(f"registry 读路径: {len(reg)} 条锚点"
          f"（short_window={sum(1 for r in reg if r['sw'])}，"
          f"level_only={sum(1 for r in reg if r['lo'])}，"
          f"pairing={sum(1 for r in reg if r['pr'])}，"
          f"verified={sum(1 for r in reg if r['verified'])}）")
    cur.execute("SELECT count(*) FROM semantic_observations s JOIN concepts c"
                " ON c.id = s.concept_id WHERE c.code = ANY(%s)", (all_codes,))
    print(f"观测行合计: {cur.fetchone()['count']} 行")
    conn.close()
    print(f"\nDONE：{len(anchors)} 条过命名门（concepts + registry 双置 verified）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
