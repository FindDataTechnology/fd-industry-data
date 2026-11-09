#!/usr/bin/env python3
"""清掉「单位是货币的实物量」观测（awaken-agriculture-report-ingest 4.1 返修）。

**问题**：4.1 的实物量正则把「销售量」列下的**金额**当成了成交量，入到库里 20 条
`agri.volume.*`，单位写成「元 / 万元」——成交量不可能用元计价。其中 1 条还是
「合计 54.3 亿元」聚合行。这些数一旦上板就是「某品种销售量 12.5 亿元」。

**修法**：抽取端已加两道拒收（货币单位、聚合行）。本脚本只负责把**已经进库**的
21 行（20 货币 + 1 聚合）清掉，并删除因此变空的 segment 实体；其余实物量不动。

**留痕**：删除的每一行（实体 code / 概念 / 日期 / 值 / 单位）写进
`reports/agriculture-pilot/volume-cleanup-2026-11-08.json`，可复核、可重放。

用法::

    python3 scripts/cleanup_agri_volume_currency.py            # 只报告，不改库
    python3 scripts/cleanup_agri_volume_currency.py --apply    # 执行删除
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = Path("/Users/chengsishi/finddata/reports/agriculture-pilot")

#: 度量单位里出现这些字就是货币，不是实物量
CCY_MARKERS = ("元",)
#: 聚合行名称（不是品种）
AGG_NAMES = ("合计", "小计", "总计")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    import psycopg2

    dsn = os.environ.get("FD_OPEN_DATA_DSN")
    if not dsn:
        raise SystemExit("FATAL 缺 FD_OPEN_DATA_DSN（source ~/.config/finddata/dashboards.env）")
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute("""
        select o.id, e.code, e.name_zh, k.code, o.date, o.value, o.unit, o.granularity
        from semantic_observations o
        join entities e on e.id = o.entity_id
        join concepts k on k.id = o.concept_id
        where k.code like 'agri.volume.%%'
          and (o.unit like '%%元%%' or e.name_zh = any(%s))
        order by e.code
    """, (list(AGG_NAMES),))
    rows = cur.fetchall()
    print(f"待清观测 {len(rows)} 条")
    for r in rows:
        print(f"  {r[1]:<22} {r[3]:<22} {r[4]} value={r[5]} unit={r[6]}")
    if not rows:
        print("无需清理")
        return 0
    if not args.apply:
        print("\n（未加 --apply，未改库）")
        return 0

    ids = [r[0] for r in rows]
    entities = sorted({r[1] for r in rows})
    cur.execute("delete from semantic_observations where id = any(%s)", (ids,))
    deleted_obs = cur.rowcount
    cur.execute("""
        delete from entities e
        where e.code = any(%s) and e.entity_type = 'segment'
          and not exists (select 1 from semantic_observations o where o.entity_id = e.id)
    """, (entities,))
    deleted_ent = cur.rowcount
    conn.commit()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "volume-cleanup-2026-11-08.json"
    out.write_text(json.dumps({
        "generated_for": "awaken-agriculture-report-ingest 4.1 返修",
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "rule": "agri.volume.* 里单位含「元」或名称为合计/小计/总计的行 = 脏数据（成交量不可能以元计价）",
        "deleted_observations": deleted_obs,
        "deleted_entities": deleted_ent,
        "rows": [{"entity": r[1], "name": r[2], "concept": r[3], "date": str(r[4]),
                  "value": str(r[5]), "unit": r[6], "granularity": r[7]} for r in rows],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    cur.execute("""select count(*) from semantic_observations o join concepts k
                   on k.id=o.concept_id where k.code like 'agri.volume.%%'""")
    left = cur.fetchone()[0]
    print(f"\n删除观测 {deleted_obs} 条 · 删除空实体 {deleted_ent} 个 · 剩余实物量观测 {left}")
    print("写出", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
