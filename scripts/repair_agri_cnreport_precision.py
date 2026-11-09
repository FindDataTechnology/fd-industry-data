#!/usr/bin/env python3
"""修 `source_used='cn-report'` 旧行的小数位精度污染（awaken-agriculture-report-ingest 6.2 附带）。

**症状**：库里 `cn-report` 车道的旧行（本 change 之前由别的管线写入）金额带 float 尾巴——
`1083621562.231`、`-28713852.7848`、`0.090`、`18826479018.919998`。报告的金额一律两位小数，
这些多出来的位是 float 往返产生的噪声。

**修法**（不做盲目四舍五入）：
1. 只挑 `value ~ '\\.\\d{3,}$'` 的行（**只动可疑行**，两位以内的 2304 行不碰）；
2. 按两位小数（Decimal，half-up）还原；
3. **逐行回原文核验**：把还原后的数按报告三种写法（`1,083,621,562.23` / `-28,713,852.78` /
   `(28,713,852.78)`）去报告正文里找，找到才算「有原文支撑」；
4. 有原文支撑 → `--apply` 时更新；找不到 → **不改**，列入 `unverified` 交人工看；
5. 每行 old/new 全量落痕，可重放可回滚。

为什么不是「直接 round 完事」：四舍五入在没有原文支撑时就是猜；这批行里
`agri.segment.revenue` 21 条是分部级金额，必须回原文分部表核，不能拿公司级数推。

用法::

    python3 scripts/repair_agri_cnreport_precision.py            # 只核验，不改库
    python3 scripts/repair_agri_cnreport_precision.py --apply     # 更新有原文支撑的行
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CACHE = Path("/Users/chengsishi/finddata/fd-cn-report/.cache/reports")
OUT_DIR = Path("/Users/chengsishi/finddata/reports/agriculture-pilot")
SUSPECT = r"\.\d{3,}$"


def _round2(value: str) -> str:
    d = Decimal(value)
    q = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(q, "f")


def _thousands(s: str) -> str:
    neg = s.startswith("-")
    body = s[1:] if neg else s
    if "." in body:
        whole, frac = body.split(".", 1)
    else:
        whole, frac = body, ""
    whole = f"{int(whole):,}" if whole else "0"
    out = f"{whole}.{frac}" if frac else whole
    return ("-" if neg else "") + out


def _text_for(code: str) -> list[Path]:
    """该公司的报告文本缓存（年报/半年报/季报全要，按日期匹配由调用方做）。"""
    return sorted(CACHE.glob(f"{code}_*.txt"))


def _evidence(value: str, texts: list[Path]) -> str | None:
    """在报告正文里找这个数，返回命中的文件名。

    **只认带千分位的金额写法**：`0.06` 这类小数字在任何财报里都能被 substring 命中，
    那种「证据」等于没有（第一版把 EPS 0.0584→0.06 判成有支撑，其实是巧合）。
    故金额 <1 万的值一律判「不可由正文证实」，交人工；1 万以上用 `1,234.56` 这种
    带逗号的具体串去核。
    """
    if abs(Decimal(value)) < Decimal(10000):
        return None
    forms = {_thousands(value), f"({_thousands(value.lstrip('-'))})"}
    for path in texts:
        text = path.read_text(encoding="utf-8", errors="replace")
        flat = re.sub(r"\s+", "", text)
        if any(f in flat for f in forms if f):
            return path.name
    return None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    import psycopg2

    dsn = os.environ.get("FD_OPEN_DATA_DSN")
    if not dsn:
        raise SystemExit("FATAL 缺 FD_OPEN_DATA_DSN（source ~/.config/finddata/dashboards.env）")
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute("""
        select o.id, e.code, k.code, o.date, o.granularity, o.value
        from semantic_observations o
        join entities e on e.id = o.entity_id
        join concepts k on k.id = o.concept_id
        where o.source_used = 'cn-report' and o.value ~ %s
        order by e.code, o.date
    """, (SUSPECT,))
    rows = cur.fetchall()
    if args.limit:
        rows = rows[: args.limit]
    print(f"可疑行 {len(rows)} 条（只动小数位 >2 的行）")

    cache: dict[str, list[Path]] = {}
    fixes, unverified = [], []
    for oid, code, concept, dt, gran, value in rows:
        try:
            new = _round2(value)
        except Exception:  # noqa: BLE001
            unverified.append({"id": oid, "code": code, "concept": concept, "date": str(dt),
                               "value": value, "why": "无法解析成十进制"})
            continue
        texts = cache.setdefault(code, _text_for(code))
        hit = _evidence(new, texts) if texts else None
        if hit:
            fixes.append({"id": oid, "code": code, "concept": concept, "date": str(dt),
                          "granularity": gran, "old": value, "new": new, "evidence": hit})
        else:
            unverified.append({"id": oid, "code": code, "concept": concept, "date": str(dt),
                               "value": value, "rounded": new,
                               "why": ("无该股票的文本缓存" if not texts
                                       else "还原值在报告正文中未找到（写法不同或非报告数）")})

    print(f"有原文支撑 {len(fixes)} 条 · 未证实 {len(unverified)} 条")
    for f in fixes[:5]:
        print(f"  ✓ {f['code']} {f['concept']:<30} {f['date']} {f['old']} → {f['new']}（{f['evidence'][:40]}）")
    for u in unverified[:5]:
        print(f"  ? {u['code']} {u['concept']:<30} {u['date']} {u['value']}")

    updated = 0
    if args.apply and fixes:
        for f in fixes:
            cur.execute("""
                update semantic_observations set value = %s
                where id = %s and value = %s
            """, (f["new"], f["id"], f["old"]))
            updated += cur.rowcount
        conn.commit()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "cnreport-precision-repair.json"
    out.write_text(json.dumps({
        "generated_for": "awaken-agriculture-report-ingest 6.2 附带：旧车道精度修复",
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "applied": args.apply,
        "rule": ("只改 source_used='cn-report' 且小数位 >2 的行；按两位小数还原；"
                 "**还原值必须在报告正文中找到才算有支撑**，找不到的不改、列 unverified"),
        "suspect_rows": len(rows),
        "fixed_rows": len(fixes),
        "updated_rows": updated,
        "unverified_rows": len(unverified),
        "fixes": fixes,
        "unverified": unverified,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"更新 {updated} 行 · 写出 {out}")
    print("未证实行**保持原值**，交人工复核（不猜）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
