#!/usr/bin/env python3
"""Flower-vertical wave-1 — run every extractor under scripts/flower_extractors/.

Same contract as ``scripts/extract_restaurant_wave0.py`` (see
``restaurant_extractors/_common.py`` for the write discipline): one module per
source unit exposing ``SOURCE`` / ``CONCEPTS`` / ``extract(cur, notes)``;
additive ``INSERT ... ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING``;
per-unit SAVEPOINT isolation; concepts born ``verified=false``.

Two things are flower-specific and deliberately loud:

* ``--dry-run`` prints the produced row count per unit **and** the units'
  crawl provenance (``crawl_runs`` success count / newest ``finished_at``), so a
  zero-row result is attributable to "unit never ran" rather than a parser bug;
* the trend gate is NOT applied. Flower data is legitimately short-history
  (dounan rolls a ~2-month window; AIPH is an annual cross-section with mixed
  reference years), so the >=24-period gate would delete every series. The
  board layer instead declares ``short_window`` / cross-section caveats, which
  is where that judgement belongs.

Usage::

    source ~/.config/finddata/dashboards.env
    python3 scripts/extract_flower_wave1.py --dry-run
    python3 scripts/extract_flower_wave1.py            # write (one txn)
    python3 scripts/extract_flower_wave1.py --only aiph-stats
"""
from __future__ import annotations

import argparse
import importlib
import os
import pkgutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent))

from restaurant_extractors._common import (  # noqa: E402
    ensure_unit_concepts,
    insert_rows,
)

PACKAGE = "flower_extractors"


def load_extractors() -> dict:
    pkg = importlib.import_module(PACKAGE)
    found = {}
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{PACKAGE}.{info.name}")
        if hasattr(mod, "SOURCE") and hasattr(mod, "extract"):
            found[mod.SOURCE] = mod
    return found


def _provenance(cur, source: str) -> str:
    cur.execute(
        "select count(*), max(finished_at), coalesce(sum(rows_written), 0)"
        " from crawl_runs where source = %s and status = 'success'",
        (source,),
    )
    n, newest, rows = cur.fetchone()
    if not n:
        return "从未成功爬取（单元未部署 / 未跑过）"
    return f"{n} 次成功爬取 · 最近 {newest:%Y-%m-%d %H:%M} · 累计 rows_written={rows}"


def record_execution(conn, *, status: str, started_at, detail: str) -> None:
    """写一行存活台账（``executions``），**独立事务**。

    与 ``scripts/extract_pharma.py`` 同一实现、同一理由：抽取成功后若这行也
    写不进去，「最后成功时刻」会停在旧值，在观测上与「根本没跑」无法区分。
    而它为什么必须存在：``semantic_observations`` 的写入是
    ``ON CONFLICT DO NOTHING``——「跑了但上游没新数据」插 0 行、``fetched_at``
    不动。所以**观测行上没有任何痕迹能证明抽取器活着**，只有这张台账能。
    ``schedule_id``/``concept_id`` 留空：本行记录的是这支脚本的存活，
    不代表某个 MCP 概念的刷新，该表没有别处对它做聚合读。
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


def _dsn(raw: str) -> str:
    """生产 Secret 里的 DSN 带 SQLAlchemy 方言前缀（`postgresql+psycopg2://…`），
    `psycopg2.connect()` 不认，会报 `invalid dsn: missing "=" after …`。
    2026-11-15 审计发现 28 个脚本里有 5 个 wave 抽取器从来没修过这一处，
    于是它们**根本无法用生产 DSN 连库**（这就是 R2「已映射但读面落后」的
    一半根因：它们从来没在集群里跑过）。与 extract_batch1/2 同一份实现。
    """
    raw = (raw or "").strip()
    try:
        from _semextract import parse_dsn
        return parse_dsn(raw)
    except Exception:                                  # noqa: BLE001
        import re as _re
        return _re.sub(r"^(postgres(?:ql)?)\+[a-z0-9_]+://", r"\1://", raw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default=None,
                    help="comma-separated source names (default: all)")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("DSN missing — source ~/.config/finddata/dashboards.env first")

    extractors = load_extractors()
    wanted = set(args.only.split(",")) if args.only else None
    if wanted:
        unknown = wanted - set(extractors)
        if unknown:
            raise SystemExit(f"unknown sources: {sorted(unknown)}; "
                             f"available: {sorted(extractors)}")

    started_at = datetime.now(timezone.utc)
    conn = psycopg2.connect(_dsn(args.dsn))
    failed: list[tuple[str, str]] = []
    per_unit: list[tuple[str, int, str]] = []
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("select count(*) from semantic_observations")
            before_total = cur.fetchone()[0]
            cur.execute("select source_used, count(*) from semantic_observations"
                        " group by 1")
            before_by_src = dict(cur.fetchall())

            notes: list[str] = []
            for source, mod in sorted(extractors.items()):
                if wanted and source not in wanted:
                    continue
                print(f"\n== {source}")
                print(f"   爬取底账: {_provenance(cur, source)}")
                if args.dry_run:
                    rows = mod.extract(cur, notes)
                    print(f"   extracted {len(rows)} rows (dry-run)")
                    continue
                sp = "unit_" + source.replace("-", "_").replace(".", "_")
                cur.execute(f"SAVEPOINT {sp}")
                try:
                    rows = mod.extract(cur, notes)
                    codes = {r[0] for r in rows}
                    ids = ensure_unit_concepts(cur, source, mod.CONCEPTS, codes)
                    ins = insert_rows(cur, source, ids, rows)
                    per_unit.append((source, len(rows), f"inserted {ins}"))
                    print(f"   extracted {len(rows)} rows, inserted {ins}")
                except Exception as exc:  # noqa: BLE001 — per-unit isolation
                    cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                    failed.append((source, str(exc)[:300]))
                    print(f"   FAILED — rolled back: {exc}")
                cur.execute(f"RELEASE SAVEPOINT {sp}")

            if args.dry_run:
                conn.rollback()
                print("\nnotes:")
                for n in notes:
                    print("  -", n)
                return 0

            cur.execute("select count(*) from semantic_observations")
            after_total = cur.fetchone()[0]
            cur.execute("select source_used, count(*) from semantic_observations"
                        " group by 1")
            after_by_src = dict(cur.fetchall())
            conn.commit()

            print(f"\nsemantic_observations: {before_total} -> {after_total}"
                  f" (inserted {after_total - before_total})")
            touched = {s for s in set(before_by_src) | set(after_by_src)
                       if before_by_src.get(s, 0) != after_by_src.get(s, 0)}
            print("sources changed: " + (", ".join(
                f"{s}:{before_by_src.get(s, 0)}->{after_by_src.get(s, 0)}"
                for s in sorted(touched)) or "NONE"))
            print(f"unexpected sources touched: "
                  f"{sorted(touched - set(extractors)) or 'NONE'}")
            if failed:
                print("\nFAILED UNITS:")
                for s, e in failed:
                    print(f"  - {s}: {e}")
            print("\nnotes:")
            for n in notes:
                print("  -", n)
    finally:
        # 台账必须在 conn.close() 之前、且**独立事务**：抽取事务已提交，
        # 这里再写一行只记存活，不掺进抽取的成败。
        try:
            # 每个源写一行：判据按源查「它最近一次抽取是什么时候、成没成」。
            # executions 表没有 source 列，所以约定 detail 以 `wave src=<源>` 开头。
            failed_src = {s for s, _e in failed}
            for src, n_rows, _kind in per_unit:
                record_execution(
                    conn,
                    status="failed" if src in failed_src else "success",
                    started_at=started_at,
                    detail=f"wave src={src} rows={n_rows} pkg=flower_extractors",
                )
            for src, err in failed:
                record_execution(conn, status="failed", started_at=started_at,
                                 detail=f"wave src={src} rows=0 pkg=flower_extractors err={err[:300]}")
        finally:
            conn.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
