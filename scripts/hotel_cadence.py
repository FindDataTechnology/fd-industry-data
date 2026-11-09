#!/usr/bin/env python3
"""酒店垂直线 · 发布节奏画像与判定（add-hotel-vertical 6.4 / 6.5）。

复用 ``fd_industry_data.publication`` 引擎（不新建机制）：四态
``published`` / ``awaiting_publication`` / ``stream_broken`` / ``window_learning``，
「上游未发布」与「我方断流」严格分流，并给出证据串。

## 为什么需要这一层（6.1 暴露的空白）

三源的 ``data_period`` 都是 ``irregular``（季度/累计期/已停更），健康级按
``data_period`` 推容忍窗，对它们**没有窗**——健康级只能判「跑没跑」，判不了
「该来没来」。「该来没来」只能在这里判。

## 画像的诚实边界

``crawl_runs`` 里的滞后是「我方运行日 − 数据日」。本线三源都是 2026-11 才接入，
历史运行只有几次、且是在数据早已发布之后补抓的——用这段历史学出来的滞后是
**接入滞后**，不是**上游发布滞后**。因此本脚本：

* 有足够「接入后按点跑」的样本时，正常走引擎学窗；
* 样本不足时如实返回 ``window_learning``（引擎自身语义：冷启动不出告警）；
* 同时输出 ``published_anchors``（外部实测发布日推出的滞后，如星级饭店
  2024Q2≈64 天、2024Q3≈85 天），供人读——但**不拿它冒充学出来的窗**。

用法::

    .venv/bin/python scripts/hotel_cadence.py --as-of 2026-11-08
    .venv/bin/python scripts/hotel_cadence.py --as-of 2026-11-08 --out reports/hotel-cadence
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.publication import Cadence, Run, classify  # noqa: E402

CONFIG = REPO / "config" / "hotel-cadence.json"


def load_runs(cur, source: str) -> list[Run]:
    """每次运行 + 它当时看到的最新数据日（水位）。

    水位口径：该 run 的 ``finished_at`` 之前已入库观测的最大 date。用观测而不是
    「预期期次」是刻意的——只有真拿到数据才算发布证据。
    """
    cur.execute(
        """
        SELECT r.started_at, r.status,
               (SELECT max(o.date) FROM semantic_observations o
                 WHERE o.source_used = r.source
                   AND o.fetched_at <= COALESCE(r.finished_at, r.started_at)) AS watermark
          FROM crawl_runs r
         WHERE r.source = %s
         ORDER BY r.started_at
        """,
        (source,),
    )
    out = []
    for started_at, status, watermark in cur.fetchall():
        out.append(Run(started_at=str(started_at), status=str(status),
                       watermark=str(watermark) if watermark else None))
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=None, help="判定基准日 YYYY-MM-DD（缺省=现在）")
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--out", default=None, help="画像输出目录")
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    args = ap.parse_args(argv)

    if not args.dsn:
        print("FATAL: 需要 FD_OPEN_DATA_DSN（source ~/.config/finddata/dashboards.env）")
        return 2
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    now = (datetime.fromisoformat(args.as_of).replace(tzinfo=timezone.utc)
           if args.as_of else datetime.now(timezone.utc))

    conn = psycopg2.connect(args.dsn)
    conn.set_session(readonly=True)
    profiles: dict[str, dict] = {}
    try:
        with conn.cursor() as cur:
            for source, spec in cfg["sources"].items():
                runs = load_runs(cur, source)
                cadence = Cadence(
                    period_days=float(spec["period_days"]),
                    min_samples=int(spec["min_samples"]),
                    lag_percentile=float(spec["lag_percentile"]),
                    lag_trim=float(spec["lag_trim"]),
                    grace_hours=float(spec["grace_hours"]),
                )
                state = classify(runs, cadence, now)
                profiles[source] = {
                    "label": spec["label"],
                    "expected_cadence": spec["expected_cadence"],
                    "discontinued": bool(spec.get("discontinued")),
                    "runs": len(runs),
                    "successes": sum(1 for r in runs if r.status == "success"),
                    "state": state.state,
                    "detail": state.detail,
                    "watermark": state.watermark,
                    "expected_next_by": state.expected_next_by,
                    "learned_lag_days": state.learned_lag_days,
                    "samples": state.samples,
                    "needs_attention": state.needs_attention,
                    "published_anchors": spec.get("published_anchors") or {},
                    "anchor_source": spec.get("anchor_source"),
                    "note": spec.get("note"),
                }
    finally:
        conn.close()

    payload = {"as_of": now.date().isoformat(), "engine": cfg["engine"],
               "profiles": profiles}
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    if args.out:
        outdir = Path(args.out) / now.date().isoformat()
        outdir.mkdir(parents=True, exist_ok=True)
        path = outdir / "cadence-profile.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
        print(f"written {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
