#!/usr/bin/env python3
"""教育采集产出 → 对外目录传导桥（change automate-education-catalog-ingest）。

五个 education 源的 CronJob 把行落进 `crawl_items`，但对外目录
`education_catalog.catalog_items` 此前只有两个手工脚本写入——采集天天跑、
目录停在 11-04。本脚本按排期读 `crawl_items` 的五个源最新行，经共享 `norm()`
归一后幂等 upsert 进目录，使目录新鲜度跟上采集。

驱动：runner 镜像只有 `psycopg2-binary`（见 Dockerfile），故本脚本用 psycopg2，
`doc` 用 `psycopg2.extras.Json` 适配 jsonb；归一逻辑在 `edu_catalog/normalize.py`
（驱动无关，doc 为纯 dict）。

新鲜度口径：条目的 `scraped_at` 取**行自身的采集时间**，不是传导时刻——
「目录新鲜」以条目采集时间为准（spec education-content-catalog R「采集产出按
排期自动传导到对外目录」）。重复传导幂等：同一批行再跑一次不产生重复条目、不改恒等字段。

shortcut: 每轮重读五个源的全部成功 run 之 items 再幂等 upsert（约 6k 行、毫秒级），
不做 run_id 水位表；目录过十万行或源增至数十个时再上增量水位。

用法：
  python3 scripts/extract_education.py --dsn "$FD_OPEN_DATA_DSN" --dry-run
  python3 scripts/extract_education.py --dsn "$FD_OPEN_DATA_DSN" --apply
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import Json

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from edu_catalog.normalize import UPSERT, norm  # noqa: E402  单一真源（驱动无关）

SOURCES = [
    "education_oa_fulltext",
    "education_podcast",
    "education_oer_aggregators",
    "education_cc_courses",
    "education_openalex",
]
INGEST_SOURCE = "education_catalog_ingest"


def _dsn(raw: str) -> str:
    """生产 Secret 的 DSN 可能带 SQLAlchemy 方言前缀（postgresql+psycopg2://），psycopg2 不认。"""
    raw = (raw or "").strip()
    try:
        from _semextract import parse_dsn
        return parse_dsn(raw)
    except Exception:  # noqa: BLE001
        import re as _re
        return _re.sub(r"^(postgres(?:ql)?)\+[a-z0-9_]+://", r"\1://", raw)


def _items_of_success_runs(cur, source: str) -> list[dict]:
    """跨该源全部 success run 取 items（增量源逐期累积，全量快照源重读亦幂等）。"""
    cur.execute(
        "SELECT ci.payload FROM crawl_runs r"
        " JOIN crawl_items ci ON ci.run_id = r.id"
        " WHERE r.source=%s AND r.status='success' ORDER BY r.id, ci.idx",
        (source,),
    )
    return [r[0] for r in cur.fetchall()]


def _existing_ids(cur) -> set[str]:
    cur.execute("SELECT resource_id FROM education_catalog.catalog_items")
    return {r[0] for r in cur.fetchall()}


def collect(cur) -> tuple[list[dict], dict]:
    """逐源读行 → norm 去重。返回 (rows, per_source_counts)。"""
    existing = _existing_ids(cur)
    per_source: dict[str, dict] = {}
    seen: dict[str, dict] = {}
    for src in SOURCES:
        raw = _items_of_success_runs(cur, src)
        distinct = 0
        for item in raw:
            n = norm(item)
            if not n:
                continue
            n["doc"] = Json(n["doc"])
            rid = n["resource_id"]
            if rid in seen:
                continue  # 源内跨 run 同 id，取首见（较新 run 在前序）
            seen[rid] = n
            distinct += 1
        per_source[src] = {"crawled_items": len(raw), "distinct": distinct}
    rows = list(seen.values())
    # 「新增 vs 更新」按最终去重集与既有集算
    new_ids = [r["resource_id"] for r in rows if r["resource_id"] not in existing]
    meta = {
        "per_source": per_source,
        "deduped_total": len(rows),
        "new": len(new_ids),
        "updated": len(rows) - len(new_ids),
    }
    return rows, meta


def write_run_record(cur, status: str, started_at: float, rows_written: int, metrics: dict,
                     error_head: str | None = None) -> None:
    finished = time.time()
    cur.execute(
        """INSERT INTO crawl_runs
           (source, kind, status, started_at, finished_at, rows_written, error_head,
            commit_sha, image_tag, metrics, outcome)
           VALUES (%s,%s,%s,to_timestamp(%s),to_timestamp(%s),%s,%s,%s,%s,%s,%s)""",
        (INGEST_SOURCE, "special-job", status, started_at, finished, rows_written,
         (error_head or None),
         os.environ.get("FD_CONTENT_COMMIT", ""), os.environ.get("FD_IMAGE_TAG", "unknown"),
         Json(metrics),
         "ok" if (status == "success" and rows_written > 0) else
         ("ok_empty" if status == "success" else "failed")),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("FATAL: $FD_OPEN_DATA_DSN 未设置")

    started = time.time()
    conn = psycopg2.connect(_dsn(args.dsn))
    conn.autocommit = False
    cur = conn.cursor()
    try:
        rows, meta = collect(cur)
        print("== per-source ==")
        for src, c in meta["per_source"].items():
            print(f"  {src}: crawled_items={c['crawled_items']} distinct={c['distinct']}")
        print(f"\ndeduped_total={meta['deduped_total']} new={meta['new']} updated={meta['updated']}")
        if args.dry_run:
            print("\n[dry-run] 不写库")
            conn.rollback()
            return 0
        if rows:
            cur.executemany(UPSERT, rows)
        write_run_record(cur, "success", started, len(rows), meta)
        conn.commit()
        print(f"\n[apply] upserted {len(rows)} rows + run record '{INGEST_SOURCE}'")
        return 0
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        try:
            write_run_record(cur, "failed", started, 0, {"error": f"{type(e).__name__}: {e}"[:400]},
                             error_head=f"{type(e).__name__}: {e}")
            conn.commit()
        except Exception:  # noqa: BLE001
            conn.rollback()
        print(f"FATAL: {type(e).__name__}: {e}", file=sys.stderr)
        return 3
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
