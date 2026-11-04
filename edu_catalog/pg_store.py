"""PG 版目录/订阅存储（7.3 部署用；fd_open_data.education_catalog schema）。

接口与 store.py 的 JSONL 版完全同构（查询面代码零分支）：
- PGCatalogStore.rows() → SELECT doc FROM catalog_items（status/发布态过滤在
  tools._visible_rows 做，库面另有 published_items 视图供对账）。
- PGSubscriptionStore：唯一写面——只碰 subscriptions 表，MUST NOT 触目录表
  （spec cap2 R1 写面边界在库层的强隔离）。

DSN 只走环境（EDU_CATALOG_DSN），源码零口令。
"""
from __future__ import annotations

import json
import os
from typing import Any

import psycopg
from psycopg.rows import dict_row

SCHEMA = "education_catalog"


class PGCatalogStore:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def rows(self) -> list[dict]:
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(f"SELECT doc FROM {SCHEMA}.catalog_items")
                return [r["doc"] for r in cur.fetchall()]

    def counts(self) -> dict[str, Any]:
        """落库对账用（跑批/验收判据①直查）。"""
        with psycopg.connect(self.dsn) as cur_conn:
            with cur_conn.cursor() as cur:
                cur.execute(f"SELECT education_source, count(*) FROM {SCHEMA}.catalog_items GROUP BY 1")
                by_source = dict(cur.fetchall())
                cur.execute(f"SELECT count(*) FROM {SCHEMA}.catalog_items")
                total = cur.fetchone()[0]
                cur.execute(f"SELECT count(*) FROM {SCHEMA}.published_items")
                published = cur.fetchone()[0]
                return {"by_source": by_source, "total": total, "published": published}


class PGSubscriptionStore:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def add(self, sub: dict) -> dict:
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""INSERT INTO {SCHEMA}.subscriptions (id, kind, target, cadence_min)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT DO NOTHING""",
                    (sub["id"], sub["kind"], sub["target"], sub.get("cadence_min", 1440)),
                )
                conn.commit()
        return sub

    def remove(self, subscription_id: str) -> bool:
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {SCHEMA}.subscriptions WHERE id = %s", (subscription_id,))
                deleted = cur.rowcount > 0
                conn.commit()
        return deleted

    def list(self) -> list[dict]:
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(f"SELECT id, kind, target, cadence_min, created_at FROM {SCHEMA}.subscriptions ORDER BY created_at")
                out = []
                for r in cur.fetchall():
                    d = dict(r)
                    d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
                    out.append(d)
                return out


def stores_from_env():
    """按环境选存储：EDU_CATALOG_DSN 在场用 PG，否则回落 JSONL 文件（本地/测试）。"""
    dsn = os.environ.get("EDU_CATALOG_DSN")
    if dsn:
        return PGCatalogStore(dsn), PGSubscriptionStore(dsn)
    from .store import CatalogStore, SubscriptionStore
    from pathlib import Path
    return (CatalogStore(Path(os.getenv("EDU_CATALOG_STORE", "/tmp/edu-catalog/catalog.jsonl"))),
            SubscriptionStore(Path(os.getenv("EDU_SUBS_STORE", "/tmp/edu-catalog/subscriptions.json"))))
