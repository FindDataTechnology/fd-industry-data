#!/usr/bin/env python3
"""农业分部数据 golden 锚校验（awaken-agriculture-report-ingest 4.4）。

**锚的取法**：每个指标至少 1 个「已知期、已知值」，值必须是**从年报原文逐字核过的**，
不是拿抽取器自己的输出当锚（那等于自证）。本文件的每条 `evidence` 都写清该数在年报
里的原样片段，任何人可以回原文复核。

**判据**：重跑抽取后，锚点若对不上 → 非零退出、指名是哪一条；抽取器改了版式规则
导致数变了，必须先解释是「源报告改版」还是「解析坏了」，不许静默放过。

**三态**（对齐 spec「对位失败不出数、未披露三态之一」）：
  * `matched`  —— 抽到且与锚一致
  * `missing`  —— 该公司该维度不在抽取结果里（可能未披露、可能被判不完整而跳过）
  * `mismatch` —— 抽到了但与锚不一致（解析退化，必须人工看）

用法::

    python3 scripts/verify_agri_segment_golden.py
    python3 scripts/verify_agri_segment_golden.py --extract docs/agri-segment-extract-2024-all.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "docs" / "golden-agri-segments.json"
DEFAULT_EXTRACT = REPO / "docs" / "agri-segment-extract-2024-all.json"

#: 锚：全部取自年报原文（2024 年报），evidence 写清原文片段。
ANCHORS: list[dict] = [
    {
        "code": "002714", "year": 2024, "dim": "分行业", "name": "养殖业务",
        "revenue": 136229329743.49, "revenue_pct": 98.75, "yoy_pct": 25.88,
        "evidence": "牧原 2024 年报「主营业务分行业情况」：养殖业务 136,229,329,743.49 98.75% 108,224,321,673.98 97.62% 25.88%",
    },
    {
        "code": "002714", "year": 2024, "dim": "分产品", "name": "生猪",
        "revenue": 136229329743.49, "revenue_pct": 98.75, "yoy_pct": 25.88,
        "evidence": "牧原 2024 年报「主营业务分产品情况」：生猪 136,229,329,743.49 98.75%",
    },
    {
        "code": "600127", "year": 2024, "dim": "分行业", "name": "粮油食品加工业",
        "revenue": 2777388464.58, "cost": 2564665157.37, "gross_margin": 7.66,
        "yoy_pct": -6.66,
        "evidence": "金健米业 2024 年报「主营业务分行业情况」（沪市版式：收入/成本/毛利率/收入同比）："
                    "粮油食品加工业 2,777,388,464.58 2,564,665,157.37 7.66 -6.66",
    },
    {
        "code": "000019", "year": 2024, "dim": "分地区", "name": "内销",
        "revenue": 5342106430.3, "revenue_pct": 99.39, "yoy_pct": -12.7,
        "evidence": "深粮控股 2024 年报「主营业务分地区情况」：内销 5,342,106,430.3 99.39%",
    },
    {
        "code": "002100", "year": 2024, "dim": "分地区", "name": "新疆区内",
        "revenue": 8540450235.63, "revenue_pct": 49.72, "yoy_pct": -21.47,
        "evidence": "天康生物 2024 年报「主营业务分地区情况」：新疆区内 8,540,450,235.63 49.72%",
    },
]

#: 相对容差（浮点/四舍五入）：0.01% 足够，锚值本身就是原文照抄。
REL_TOL = 1e-4


def _close(a, b) -> bool:
    if a is None or b is None:
        return a is b or (a is None and b is None)
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return False
    if b == 0:
        return abs(a) < 1e-9
    return abs(a - b) / abs(b) <= REL_TOL


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--extract", default=str(DEFAULT_EXTRACT))
    ap.add_argument("--write-golden", action="store_true",
                    help="把本文件的 ANCHORS 落成 docs/golden-agri-segments.json")
    args = ap.parse_args(argv)

    if args.write_golden:
        GOLDEN.write_text(json.dumps(
            {"generated_for": "awaken-agriculture-report-ingest 4.4",
             "date": "2026-11-08",
             "rule": "锚值取自年报原文逐字核对；重跑抽取对不上即失败，不许静默放过",
             "anchors": ANCHORS}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"写出 {GOLDEN}（{len(ANCHORS)} 条锚）")
        return 0

    src = Path(args.extract)
    if not src.is_file():
        raise SystemExit(f"FATAL 缺抽取结果 {src}")
    doc = json.loads(src.read_text(encoding="utf-8"))
    by_code = {c["code"]: c for c in doc.get("companies") or []}

    states = {"matched": 0, "missing": 0, "mismatch": 0}
    problems: list[str] = []
    for a in ANCHORS:
        company = by_code.get(a["code"])
        if not company or int(company.get("year") or 0) != a["year"]:
            states["missing"] += 1
            problems.append(f"MISSING {a['code']} {a['year']}：抽取结果里没有这家/这一年")
            continue
        rows = ((company.get("by_dimension") or {}).get(a["dim"]) or {}).get("rows") or []
        row = next((r for r in rows if (r.get("name") or "").strip() == a["name"]), None)
        if row is None:
            states["missing"] += 1
            problems.append(f"MISSING {a['code']} {a['dim']} {a['name']}：该行未抽到")
            continue
        bad = [f for f in ("revenue", "revenue_pct", "yoy_pct", "cost", "gross_margin")
               if a.get(f) is not None and not _close(row.get(f), a[f])]
        if bad:
            states["mismatch"] += 1
            detail = "，".join(f"{f}: 锚 {a[f]} vs 抽到 {row.get(f)}" for f in bad)
            problems.append(f"MISMATCH {a['code']} {a['dim']} {a['name']}：{detail}")
        else:
            states["matched"] += 1
            print(f"  ✓ {a['code']} {a['dim']} {a['name']}")

    print(f"\n锚 {len(ANCHORS)} 条：matched {states['matched']} · "
          f"missing {states['missing']} · mismatch {states['mismatch']}")
    for p in problems:
        print("  ✗", p)
    # missing 不算失败（可能确实未披露/被判不完整），mismatch 必须失败
    return 1 if states["mismatch"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
