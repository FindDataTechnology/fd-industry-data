#!/usr/bin/env python3
"""医药两源 → 概念观测抽取（change add-pharma-observation-extraction）。

把 `crawl_items` 里两个源的 payload 抽成 4 条概念观测，落进 `semantic_observations`：

| 概念 | 粒度 | 实体 | 值 |
|---|---|---|---|
| `trial.new` | month | country/CN（范围） | 该月新注册试验数 |
| `trial.enrollment.planned` | month | trial/注册号（个体） | 该试验计划入组人数 |
| `catalog.drug.entries` | year | country/CN（范围） | 该目录年度版可支付条目数 |
| `catalog.drug.class_a_share` | year | country/CN（范围） | 甲类占比（%） |

为什么聚合量挂国家而不是样本实体：计数与占比是「范围内该期总量」，挂到单个试验/药品上
会让同一概念在每个样本上各有一条同名值，读侧分不清总量与个体值
（见 change 的 design D1）。逐试验的入组计划数则相反，实体就是该试验。

沿用既有纪律（`scripts/extract_batch1.py` 同一套）：
* 只追加：`ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING`，第二次运行插 0 行；
* 不插值、不补零：只用 `crawl_items` 里真实存在的期间；
* 同一 (序列, 期间) 多次抓取取 `scraped_at` 最新的一条。

写完必须对账（D3）：四个概念的取值都要能被 `crawl_items` 复算出来，不等即失败——口径
漂移（例如把「不得纳入基金支付范围」也算进条目数）不会让写入失败，只会让数字静默偏大。

用法::

    python3 scripts/extract_pharma.py --dsn "$FD_OPEN_DATA_DSN" --dry-run
    python3 scripts/extract_pharma.py --dsn "$FD_OPEN_DATA_DSN" --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP

import psycopg2

# 范围实体：country/CN（实体表既有，id=1）
ENTITY_COUNTRY_CN = 1
ENTITY_TYPE_COUNTRY = "country"
ENTITY_TYPE_TRIAL = "trial"

TRIAL_SOURCE = "clinicaltrials-cn"
CATALOG_SOURCE = "nrdl-national-catalog"

# 每个概念固定一种实体粒度；写前断言，混用即失败（D1）。
CONCEPT_ENTITY = {
    "trial.new": ENTITY_TYPE_COUNTRY,
    "trial.enrollment.planned": ENTITY_TYPE_TRIAL,
    "catalog.drug.entries": ENTITY_TYPE_COUNTRY,
    "catalog.drug.class_a_share": ENTITY_TYPE_COUNTRY,
}


def _month_of(start_date: str | None) -> str | None:
    """StartDate 的几种实测形态 → 'YYYY-MM'；不可解析返回 None。"""
    if not start_date:
        return None
    s = str(start_date).strip()
    if len(s) >= 7 and s[4] == "-" and s[:4].isdigit() and s[5:7].isdigit():
        return s[:7]
    if len(s) == 4 and s.isdigit():
        # 只有年份的记录（CT.gov 的 StartDate 有时是 'Month Year' / 'Year'）
        return None
    return None


def _latest_success_run(cur, source: str) -> int | None:
    cur.execute(
        "SELECT id FROM crawl_runs WHERE source=%s AND status='success'"
        " ORDER BY id DESC LIMIT 1",
        (source,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _items_of_run(cur, run_id: int) -> list[dict]:
    cur.execute("SELECT payload FROM crawl_items WHERE run_id=%s ORDER BY idx", (run_id,))
    return [r[0] for r in cur.fetchall()]


def _items_of_all_runs(cur, source: str) -> tuple[list[dict], list[int]]:
    """跨**全部**成功 run 取 items（增量源专用）。

    临床试验源按月窗口增量抓取：只读最近一次 run 会让序列每期只剩一个月，
    跨期累积必须把所有成功 run 并起来（去重交给按注册号取最新的那一步）。
    目录源相反——它是年度全量快照，只读最近一次（见 ``_items_of_run``）。
    """
    cur.execute(
        "SELECT r.id, ci.payload FROM crawl_runs r"
        " JOIN crawl_items ci ON ci.run_id = r.id"
        " WHERE r.source=%s AND r.status='success' ORDER BY r.id, ci.idx",
        (source,),
    )
    rows = cur.fetchall()
    return [r[1] for r in rows], sorted({r[0] for r in rows})


def ensure_trial_entities(cur, trials: list[dict]) -> dict[str, int]:
    """把试验注册号登记成 ``entities``(entity_type='trial')，返回 code -> id。

    为什么必须登记：``semantic_observations.entity_id`` 是整型外键，指向 ``entities.id``，
    不是外部编号。直接把 ``NCT...`` 写进去会 `invalid input syntax for type integer`
    （本批实测踩过一次）。登记用 ``ON CONFLICT (entity_type, code) DO NOTHING`` 幂等，
    二次运行不新增实体。
    """
    if not trials:
        return {}
    cur.executemany(
        "INSERT INTO entities (entity_type, code, name_en, name_zh, metadata_json)"
        " VALUES ('trial', %s, %s, NULL, NULL)"
        " ON CONFLICT ON CONSTRAINT uq_entity_type_code DO NOTHING",
        [(t["nct_id"], (t.get("brief_title") or "")[:500] or None) for t in trials],
    )
    codes = [t["nct_id"] for t in trials]
    cur.execute(
        "SELECT code, id FROM entities WHERE entity_type='trial' AND code = ANY(%s)", (codes,)
    )
    return dict(cur.fetchall())


def extract_trials(items: list[dict], notes: list[str]) -> list[tuple]:
    """返回 (code, entity_type, entity_id, date, granularity, value) 行。"""
    # 同一试验可能被多轮抓取到；按 registry id 取 scraped_at 最新的一条。
    newest: dict[str, dict] = {}
    for p in items:
        rid = p.get("nct_id")
        if not rid:
            continue
        prev = newest.get(rid)
        if prev is None or str(p.get("scraped_at") or "") >= str(prev.get("scraped_at") or ""):
            newest[rid] = p

    per_month = Counter()
    rows: list[tuple] = []
    skipped_no_month = 0
    for rid, p in sorted(newest.items()):
        month = _month_of(p.get("start_date"))
        if not month:
            skipped_no_month += 1
        else:
            per_month[month] += 1
        enroll = p.get("enrollment_count")
        if enroll in (None, "") or not month:
            continue
        try:
            val = int(str(enroll).strip())
        except ValueError:
            continue
        rows.append(("trial.enrollment.planned", ENTITY_TYPE_TRIAL, rid,
                     f"{month}-01", "month", str(val)))

    for month in sorted(per_month):
        rows.append(("trial.new", ENTITY_TYPE_COUNTRY, ENTITY_COUNTRY_CN,
                     f"{month}-01", "month", str(per_month[month])))

    notes.append(
        f"{TRIAL_SOURCE}: 试验 {len(newest)} 条唯一注册号；有 StartDate 月份的 "
        f"{sum(per_month.values())} 条覆盖 {len(per_month)} 个月；"
        f"无可用月份 {skipped_no_month} 条（不补）"
    )
    return rows


def extract_catalog(items: list[dict], notes: list[str]) -> list[tuple]:
    """目录条目数与甲类占比：只用可支付行（排除黑名单），年份取目录年度版。"""
    newest: dict[int, dict] = {}
    for p in items:
        year = p.get("catalog_year")
        if year is None:
            continue
        try:
            y = int(year)
        except (TypeError, ValueError):
            continue
        prev = newest.get(y)
        if prev is None or str(p.get("scraped_at") or "") >= str(prev.get("scraped_at") or ""):
            newest[y] = p

    payable = [p for p in items if p.get("payment_scope") == "payable"]
    excluded = [p for p in items if p.get("payment_scope") == "excluded"]
    years = sorted({int(p["catalog_year"]) for p in payable if p.get("catalog_year")})
    if len(years) != 1:
        raise SystemExit(
            f"FATAL: 目录 run 里出现多个年度版 {years}（预期单一年度）；"
            f"期间语义会含混，先确认数据再抽取"
        )
    year = years[0]
    total = len(payable)
    class_a = sum(1 for p in payable if p.get("class_ab") == "甲")
    share = (Decimal(class_a) * 100 / Decimal(total)).quantize(Decimal("0.01"),
                                                              rounding=ROUND_HALF_UP)
    notes.append(
        f"{CATALOG_SOURCE}: 年度版 {year}；可支付 {total} 行（甲类 {class_a}，"
        f"占比 {share}%）；黑名单 {len(excluded)} 行不计入条目数"
    )
    period = f"{year}-01-01"
    return [
        ("catalog.drug.entries", ENTITY_TYPE_COUNTRY, ENTITY_COUNTRY_CN,
         period, "year", str(total)),
        ("catalog.drug.class_a_share", ENTITY_TYPE_COUNTRY, ENTITY_COUNTRY_CN,
         period, "year", str(share)),
    ]


def reconcile(cur, rows: list[tuple], trial_items: list[dict], catalog_items: list[dict]) -> None:
    """与 crawl_items 复算比对；不等即失败（D3）。

    这是唯一能发现口径漂移的地方：抽取算错不会让写入失败，只会让数字静默偏大。
    """
    by_code = defaultdict(list)
    for code, _et, _eid, _d, _g, value in rows:
        by_code[code].append(value)

    payable = [p for p in catalog_items if p.get("payment_scope") == "payable"]
    expect_entries = len(payable)
    got_entries = [int(v) for v in by_code["catalog.drug.entries"]]
    if got_entries != [expect_entries]:
        raise SystemExit(
            f"FATAL 对账失败 catalog.drug.entries: 抽出 {got_entries} ≠ 复算 {expect_entries}"
        )
    class_a = sum(1 for p in payable if p.get("class_ab") == "甲")
    expect_share = (Decimal(class_a) * 100 / Decimal(expect_entries)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP) if expect_entries else Decimal("0")
    got_share = [Decimal(v) for v in by_code["catalog.drug.class_a_share"]]
    if got_share != [expect_share]:
        raise SystemExit(
            f"FATAL 对账失败 catalog.drug.class_a_share: 抽出 {got_share} ≠ 复算 {expect_share}"
        )

    newest: dict[str, dict] = {}
    for p in trial_items:
        rid = p.get("nct_id")
        if not rid:
            continue
        prev = newest.get(rid)
        if prev is None or str(p.get("scraped_at") or "") >= str(prev.get("scraped_at") or ""):
            newest[rid] = p
    expect_months = Counter(_month_of(p.get("start_date")) for p in newest.values())
    expect_months.pop(None, None)
    got_months = {}
    for code, _et, _eid, d, _g, value in rows:
        if code == "trial.new":
            got_months[d[:7]] = int(value)
    if got_months != dict(expect_months):
        raise SystemExit(
            f"FATAL 对账失败 trial.new: 抽出 {len(got_months)} 个月 ≠ 复算 {len(expect_months)} 个月"
        )
    got_enroll = {eid: int(v) for code, _et, eid, _d, _g, v in rows
                  if code == "trial.enrollment.planned"}
    expect_enroll = {}
    for rid, p in newest.items():
        if not _month_of(p.get("start_date")):
            continue
        try:
            expect_enroll[rid] = int(str(p.get("enrollment_count")).strip())
        except (TypeError, ValueError):
            continue
    if got_enroll != expect_enroll:
        raise SystemExit(
            f"FATAL 对账失败 trial.enrollment.planned: 抽出 {len(got_enroll)} 条 ≠ "
            f"复算 {len(expect_enroll)} 条"
        )


def record_execution(conn, *, status: str, started_at, detail: str) -> None:
    """写一行存活台账（``executions``），**独立事务**。

    为什么必须独立于抽取事务：抽取失败时若台账也不写，"最后成功时刻"会一直停在旧值，
    在观测上与"根本没跑"无法区分——那样巡检就失去了检测停摆的能力（design D4）。

    ``schedule_id``/``concept_id`` 留空：本行不代表某个 MCP 概念的刷新，
    它记录的是这一支抽取脚本本身的存活。该表没有别处对它做聚合读，不会污染视图。
    """
    try:
        with conn.cursor() as c:
            c.execute(
                "INSERT INTO executions (schedule_id, concept_id, status, started_at,"
                " finished_at, detail) VALUES (NULL, NULL, %s, %s, now(), %s)",
                (status, started_at, detail[:900]),
            )
        conn.commit()
    except Exception as e:  # noqa: BLE001
        # 台账写不进去不应掩盖抽取本身的失败原因
        print(f"WARN: 存活台账写入失败：{type(e).__name__}: {e}", file=sys.stderr)
        conn.rollback()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("FATAL: $FD_OPEN_DATA_DSN 未设置（source ~/.config/finddata/dashboards.env）")
    if args.apply and args.dry_run:
        raise SystemExit("FATAL: --apply 与 --dry-run 互斥")

    conn = psycopg2.connect(args.dsn)
    conn.autocommit = False
    cur = conn.cursor()
    notes: list[str] = []
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        catalog_run = _latest_success_run(cur, CATALOG_SOURCE)
        if catalog_run is None:
            raise SystemExit(f"FATAL: {CATALOG_SOURCE} 无成功 run")
        trial_items, trial_runs = _items_of_all_runs(cur, TRIAL_SOURCE)
        if not trial_runs:
            raise SystemExit(f"FATAL: {TRIAL_SOURCE} 无成功 run")
        catalog_items = _items_of_run(cur, catalog_run)
        print(f"{TRIAL_SOURCE}: {len(trial_runs)} 个成功 run 合并 {len(trial_items)} 行 "
              f"(增量月窗，跨 run 累积)")
        print(f"{CATALOG_SOURCE}: run#{catalog_run} 全量快照 {len(catalog_items)} 行")

        rows = extract_trials(trial_items, notes) + extract_catalog(catalog_items, notes)

        # 逐试验观测的 entity_id 必须是 entities.id；先把注册号登记成实体（幂等），再替换。
        trial_ids = ensure_trial_entities(
            cur, [{"nct_id": r[2], "brief_title": None}
                  for r in rows if r[0] == "trial.enrollment.planned"]
        )
        resolved = []
        for code, et, eid, d, gran, value in rows:
            if et == ENTITY_TYPE_TRIAL:
                if eid not in trial_ids:
                    raise SystemExit(f"FATAL: 试验实体未登记：{eid}")
                eid = trial_ids[eid]
            resolved.append((code, et, eid, d, gran, value))
        rows = resolved

        # 粒度断言：每个概念只允许一种 entity_type（D1）
        for code, et, *_ in rows:
            if CONCEPT_ENTITY.get(code) != et:
                raise SystemExit(
                    f"FATAL: {code} 的 entity_type={et} 与声明的 {CONCEPT_ENTITY.get(code)} 不符"
                )

        # 对账在实体 id 替换之后做，但 trial.enrollment 的键是注册号：
        # 用「id -> code」反查还原，保证对账比较的是同一套键。
        id_to_code = {v: k for k, v in trial_ids.items()}
        reconcile(cur, [(c, e, id_to_code.get(i, i) if e == ENTITY_TYPE_TRIAL else i, d, g, v)
                        for c, e, i, d, g, v in rows], trial_items, catalog_items)
        print("reconcile: 四个概念与 crawl_items 复算一致")

        cur.execute("SELECT code, id FROM concepts WHERE code = ANY(%s) AND deprecated=false",
                    (list(CONCEPT_ENTITY),))
        ids = dict(cur.fetchall())
        missing = sorted(set(CONCEPT_ENTITY) - set(ids))
        if missing:
            raise SystemExit(f"FATAL: 概念未播种：{missing}")

        print(f"\ntotal observation rows: {len(rows)}")
        for code in sorted(CONCEPT_ENTITY):
            n = sum(1 for r in rows if r[0] == code)
            print(f"  {code:<32} {n:>6} rows")

        print("\nnotes:")
        for n in notes:
            print("  -", n)

        if args.dry_run or not args.apply:
            print("\ndry-run: 未写库（加 --apply 落库）")
            conn.rollback()
            return 0

        with conn:
            with conn.cursor() as c2:
                c2.execute("SELECT count(*) FROM semantic_observations WHERE source_used = ANY(%s)",
                           ([TRIAL_SOURCE, CATALOG_SOURCE],))
                before = c2.fetchone()[0]
                for code, et, eid, d, gran, value in rows:
                    src = TRIAL_SOURCE if code.startswith("trial.") else CATALOG_SOURCE
                    c2.execute(
                        "INSERT INTO semantic_observations"
                        " (concept_id, entity_type, entity_id, date, granularity, value,"
                        "  unit, source_used, fetched_at, obs_month)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now(),%s)"
                        " ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING",
                        (ids[code], et, eid, d, gran, value, None, src,
                         d[:7] if gran == "month" else d[:4] + "-" + d[5:7]),
                    )
                c2.execute("SELECT count(*) FROM semantic_observations WHERE source_used = ANY(%s)",
                           ([TRIAL_SOURCE, CATALOG_SOURCE],))
                after = c2.fetchone()[0]
        print(f"\nsemantic_observations(pharma): {before} -> {after} (inserted {after - before})")

        per_concept = ", ".join(
            f"{code}={sum(1 for r in rows if r[0] == code)}" for code in sorted(CONCEPT_ENTITY)
        )
        record_execution(
            conn, status="success", started_at=started_at,
            detail=(f"extract_pharma: 试算 {len(rows)} 行，落库 {after - before} 行"
                    f"（{per_concept}）；trial_runs={len(trial_runs)}，"
                    f"catalog_run={catalog_run}"),
        )
        return 0
    except BaseException as exc:  # 失败也要留痕（design D4）
        if not args.dry_run and args.apply:
            record_execution(
                conn, status="failed", started_at=started_at,
                detail=f"extract_pharma: {type(exc).__name__}: {exc}",
            )
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
