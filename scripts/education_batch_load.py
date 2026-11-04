"""教育垂域首批真落库（A2，2026-11-04 Owner 放行后第一跑）。

七通道扩量批 → upsert 进 fd_open_data.education_catalog.catalog_items。
resource_id 缺位回落（openalex 行用 work_id）；墓碑行（status withdrawn/merged）
与现行行同表分 status；发布态由视图过滤（spec cap1 R4 在库面再钉一遍）。

用法：cd fd-industry-data && set -a; source ~/.config/finddata/dashboards.env; set +a
      python scripts/education_batch_load.py
DSN 取 FD_OPEN_DATA_DSN 环境变量（源码零口令，dashboards.env 惯例）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Json

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DSN = os.environ.get("FD_OPEN_DATA_DSN")
if not DSN:
    raise SystemExit("FD_OPEN_DATA_DSN missing — source ~/.config/finddata/dashboards.env first")

UPSERT = """
INSERT INTO education_catalog.catalog_items
  (resource_id, work_id, title, platform, license, license_layer, commercial_ok,
   body_state, education_source, source, source_url, scraped_at, status, merged_into, doc)
VALUES
  (%(resource_id)s, %(work_id)s, %(title)s, %(platform)s, %(license)s, %(license_layer)s,
   %(commercial_ok)s, %(body_state)s, %(education_source)s, %(source)s, %(source_url)s,
   %(scraped_at)s, %(status)s, %(merged_into)s, %(doc)s)
ON CONFLICT (resource_id) DO UPDATE SET
  work_id=EXCLUDED.work_id, title=EXCLUDED.title, platform=EXCLUDED.platform,
  license=EXCLUDED.license, license_layer=EXCLUDED.license_layer,
  commercial_ok=EXCLUDED.commercial_ok, body_state=EXCLUDED.body_state,
  education_source=EXCLUDED.education_source, source=EXCLUDED.source,
  source_url=EXCLUDED.source_url, scraped_at=EXCLUDED.scraped_at,
  status=EXCLUDED.status, merged_into=EXCLUDED.merged_into,
  doc=EXCLUDED.doc, ingested_at=now()
"""


def collect_rows() -> list[dict]:
    from spiders.education_openalex.spider import run_education_openalex
    from spiders.education_oa_fulltext.spider import run_education_arxiv_new, run_education_epmc_oa
    from spiders.education_cc_courses.spider import run_ocw_catalog, run_openedx_catalog
    from spiders.education_podcast.spider import run_podcast_seed

    rows: list[dict] = []
    # 1) OpenAlex 教育骨架 2000 行（API 游标；免费预算新的一天）
    rows += run_education_openalex(limit=2000, phase="skeleton", education_source="subfield_3304")
    # 2) OA 正文层元数据
    rows += run_education_epmc_oa(limit=100)
    rows += run_education_arxiv_new(limit=30)
    # 3) CC 课程全目录（OCW sitemap 全量；OL Library 目录）
    rows += run_ocw_catalog(limit=3000)
    rows += run_openedx_catalog(limit=600)
    # 4) 播客 seed 30 节目
    rows += run_podcast_seed(
        keywords=["science", "history", "language learning", "programming",
                  "finance", "philosophy", "书籍 历史", "科学", "财经", "学习"],
        countries=["us", "cn"], limit=30)
    print(f"collected {len(rows)} rows from channels")
    return rows


def norm(row: dict) -> dict:
    """落库行归一：resource_id 回落、status 分离、doc 全量保留。

    license_layer 归一口径与 pod 读路径一致（tools._visible_rows 同回落）：
    spide 行自带优先；缺则从 license 推（cc0→cc0_pd / *by-nc*→cc-by-nc / cc by*→oa_cc_by）。
    推导结果必须同时写进 doc——pod rows() 只读 doc，漏写会在 overview 造出原始许可串桶。
    """
    r = dict(row)
    rid = r.get("resource_id") or r.get("work_id") or r.get("episode_guid") \
        or r.get("show_id") or r.get("source_url")
    if not rid:
        return {}
    status = r.get("status")
    layer = r.get("license_layer") or (
        "cc0_pd" if r.get("license") in ("cc0", "CC0", "public domain") else
        "cc-by-nc" if isinstance(r.get("license"), str) and "by-nc" in r["license"].lower() else
        "oa_cc_by" if isinstance(r.get("license"), str) and r["license"].lower().startswith("cc by") else
        None)
    if layer:
        r.setdefault("license_layer", layer)
    return {
        "resource_id": str(rid),
        "work_id": r.get("work_id"),
        "title": r.get("title") or r.get("display_name") or r.get("name"),
        "platform": r.get("platform"),
        "license": r.get("license"),
        "license_layer": layer,
        "commercial_ok": r.get("commercial_ok"),
        "body_state": r.get("body_state") or r.get("episode_status"),
        "education_source": r.get("education_source") or r.get("source"),
        "source": r.get("source"),
        "source_url": r.get("source_url") or r.get("feed_url"),
        "scraped_at": r.get("scraped_at"),
        "status": status if status in ("withdrawn", "merged") else "active",
        "merged_into": r.get("merged_into"),
        "doc": Json(r),
    }


def main():
    raw = collect_rows()
    seen: dict[str, dict] = {}
    for r in raw:
        n = norm(r)
        if not n:
            continue
        seen[n["resource_id"]] = n  # 批内按 resource_id 去重（upsert 幂等）
    rows = list(seen.values())
    print(f"upserting {len(rows)} rows (deduped from {len(raw)})")
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.executemany(UPSERT, rows)
        conn.commit()
        # 对账（重开游标——2026-11-04 首跑撞的游标关闭 bug，数据本身已提交成功）
        with conn.cursor() as cur:
            cur.execute("SELECT education_source, count(*) FROM education_catalog.catalog_items GROUP BY 1 ORDER BY 2 DESC")
        print("\n== catalog_items by education_source ==")
        for src, cnt in cur.fetchall():
            print(f"  {src}: {cnt}")
        cur.execute("SELECT license_layer, count(*) FROM education_catalog.published_items GROUP BY 1 ORDER BY 2 DESC")
        print("\n== published by license_layer ==")
        for lay, cnt in cur.fetchall():
            print(f"  {lay}: {cnt}")
            cur.execute("SELECT count(*) FROM education_catalog.catalog_items")
            total = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM education_catalog.published_items")
            pub = cur.fetchone()[0]
            print(f"\ntotal={total}  published={pub}")


if __name__ == "__main__":
    main()
