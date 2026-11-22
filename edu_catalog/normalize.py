"""教育目录入库归一与幂等 UPSERT（单一真源，驱动无关）。

`norm()` 与 `UPSERT` 原在 `scripts/education_batch_load.py`（psycopg v3）。抽取桥
`scripts/extract_education.py` 跑在 runner 镜像里（只有 `psycopg2-binary`），import 那份
会 `ModuleNotFoundError: psycopg`。故把两者下沉到本模块，**doc 以纯 dict 返回**，
由各调用方用自己的驱动适配（psycopg3 `Json` / psycopg2.extras `Json`）。

license_layer 归一口径与 pod 读路径一致（edu_catalog.tools._visible_rows 同回落）：
行自带优先；缺则从 license 推（cc0→cc0_pd / *by-nc*→cc-by-nc / cc by*→oa_cc_by）。
推导结果同时写进 doc——pod rows() 只读 doc，漏写会在 overview 造出原始许可串桶。
"""
from __future__ import annotations

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


def norm(row: dict) -> dict:
    """落库行归一：resource_id 回落、status 分离、doc 全量保留（纯 dict，交调用方 Json 适配）。"""
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
        "doc": r,
    }
