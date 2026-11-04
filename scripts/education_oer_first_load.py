"""4.2 首跑：OER 聚合器三源入库（Gutendex/Wikibooks/DOAB 各 50 条起步）。

用 aggregator 蜘蛛三命令采行 → 复用 education_batch_load 的 UPSERT/归一逻辑
（import collect 不合适——那会重跑全部七通道；本脚本只做聚合器增量，幂等 upsert）。

用法：cd fd-industry-data && set -a; source ~/.config/finddata/dashboards.env; set +a
      EDU_OER_PROXY=http://100.64.0.10:7890 python scripts/education_oer_first_load.py
（本地跑可不走代理——出海通道；DSN=FD_OPEN_DATA_DSN，源码零口令。）
"""
from __future__ import annotations

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from education_batch_load import UPSERT, norm  # noqa: E402  复用归一+幂等 upsert


def collect_rows() -> list[dict]:
    from spiders.education_oer_aggregators.spider import (
        run_doab_oai_harvest,
        run_gutendex_catalog,
        run_wikibooks_catalog,
    )
    rows: list[dict] = []
    print("== Gutendex (public-domain books, cc0_pd) ==")
    rows += run_gutendex_catalog(query="education", limit=50)
    print(f"  got {len(rows)} rows so far")
    n1 = len(rows)
    print("== Wikibooks (CC BY-SA textbooks) ==")
    rows += run_wikibooks_catalog(query="textbook", limit=50)
    print(f"  got {len(rows) - n1} rows")
    n2 = len(rows)
    print("== DOAB (OAI-PMH academic books) ==")
    rows += run_doab_oai_harvest(limit=50)
    print(f"  got {len(rows) - n2} rows")
    return rows


def main():
    raw = collect_rows()
    seen: dict[str, dict] = {}
    for r in raw:
        n = norm(r)
        if not n:
            continue
        seen[n["resource_id"]] = n
    rows = list(seen.values())
    print(f"upserting {len(rows)} rows (deduped from {len(raw)})")
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.executemany(UPSERT, rows)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT education_source, license_layer, count(*) FROM education_catalog.catalog_items "
                        "WHERE education_source IN ('gutenberg_catalog','wikibooks_search','doab_oai') "
                        "GROUP BY 1,2 ORDER BY 1,2")
            print("\n== aggregator rows by source × license_layer ==")
            for src, layer, cnt in cur.fetchall():
                print(f"  {src:24s} {layer:14s} {cnt}")
            cur.execute("SELECT count(*) FROM education_catalog.published_items "
                        "WHERE education_source IN ('gutenberg_catalog','wikibooks_search','doab_oai')")
            print("published (gate-passed):", cur.fetchone()[0])


if __name__ == "__main__":
    main()
