#!/usr/bin/env python
"""证券业监控白名单生成器（add-securities-industry-line 4.3）。

监控白名单 = 沪深300（000300）+ 中证500（000905）核心指数成分
            + 上市券商（中证全指证券公司 399975 成分）。
来源均为中证指数官方（csindex），经 akshare index_stock_cons_csindex 取数；
券商清单 2026-11-07 实取 47 只（用户口算「43 家」为约数——指数含 国投资本 等
控股壳与近期上市券商，以官方成分为准，本生成器不硬编码家数）。

输出（机器可读，供采集管道与看板消费）：
  output/securities-whitelist/whitelist-<YYYYMMDD>.json  当次快照（含拉取日期）
  output/securities-whitelist/latest.json                恒指最新快照
  output/securities-whitelist/symbols.txt               全白名单去重代码清单（每行一个）

JSON 形状：
{
  "generated_at": ISO, "as_of_date": "YYYYMMDD"(指数页披露日),
  "indices": {"000300": {"name": ..., "count": N, "codes": [...], "names": [...]},
              "000905": {...}, "399975": {...}},
  "whitelist": [ {code, name, membership: ["000300","399975"]}, ... ],
  "count_total": N
}

用法（fd-industry-data venv，含 akshare）：
  .venv/bin/python scripts/securities_whitelist.py            # 生成并落盘
  .venv/bin/python scripts/securities_whitelist.py --check    # 重取一遍与现有 latest.json 抽查对账（幂等验证）

依赖：akshare（venv 自带 1.19.1）；网络直连 csindex。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_THIS = Path(__file__).resolve()
_REPO = _THIS.parent.parent
OUT_DIR = _REPO / "output" / "securities-whitelist"

INDICES = {
    "000300": "沪深300",
    "000905": "中证500",
    "399975": "中证全指证券公司(上市券商)",
}


def fetch_index(index_code: str) -> tuple[dict, list[str], list[str]]:
    """取一个中证指数成分表；返回 (原始行集合元信息, codes, names)。"""
    import akshare as ak

    df = ak.index_stock_cons_csindex(symbol=index_code)
    if df is None or len(df) == 0:
        raise RuntimeError(f"csindex {index_code} returned no constituents")
    if "成分券代码" not in df.columns or "成分券名称" not in df.columns:
        raise RuntimeError(
            f"csindex {index_code} missing expected columns; got {df.columns.tolist()}"
        )
    codes = [str(c).zfill(6) for c in df["成分券代码"].tolist()]
    names = [str(n).strip() for n in df["成分券名称"].tolist()]
    as_of = ""
    if "日期" in df.columns and len(df) > 0:
        as_of = str(df["日期"].iloc[0])[:10].replace("-", "")
    meta = {"name": INDICES[index_code], "count": len(codes), "as_of": as_of}
    return meta, codes, names


def build_whitelist() -> dict:
    indices_meta: dict[str, dict] = {}
    membership: dict[str, dict] = {}
    for code in INDICES:
        meta, codes, names = fetch_index(code)
        indices_meta[code] = {**meta, "codes": codes, "names": names}
        for c, n in zip(codes, names):
            entry = membership.setdefault(c, {"code": c, "name": n, "membership": []})
            entry["membership"].append(code)
    wl = sorted(membership.values(), key=lambda e: e["code"])
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "as_of_date": max((m.get("as_of", "") for m in indices_meta.values()), default=""),
        "indices": indices_meta,
        "whitelist": wl,
        "count_total": len(wl),
    }


def write_outputs(doc: dict) -> list[Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = doc.get("as_of_date") or datetime.now().strftime("%Y%m%d")
    snapshot = OUT_DIR / f"whitelist-{stamp}.json"
    snapshot.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = OUT_DIR / "latest.json"
    latest.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    symbols = OUT_DIR / "symbols.txt"
    symbols.write_text("\n".join(e["code"] for e in doc["whitelist"]) + "\n", encoding="utf-8")
    return [snapshot, latest, symbols]


def check_against_latest() -> int:
    """--check：重取一遍，与 latest.json 做抽查对账（指数计数+样本码）。幂等验证入口。"""
    latest = OUT_DIR / "latest.json"
    if not latest.exists():
        print("no latest.json — run without --check first", file=sys.stderr)
        return 2
    old = json.loads(latest.read_text(encoding="utf-8"))
    new = build_whitelist()
    problems: list[str] = []
    for code in INDICES:
        o, n = set(old["indices"][code]["codes"]), set(new["indices"][code]["codes"])
        if len(o) != len(n):
            problems.append(f"{code}: count {len(o)} -> {len(n)} (调样或源漂移)")
        drift = n ^ o
        if drift:
            problems.append(f"{code}: membership drift {sorted(drift)[:5]}{'...' if len(drift) > 5 else ''}")
    if problems:
        print("CHECK_MISMATCH")
        for p in problems:
            print(" -", p)
        return 1
    print("CHECK_OK counts=" + ",".join(
        f"{c}:{new['indices'][c]['count']}" for c in INDICES) + f" total={new['count_total']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="与 latest.json 抽查对账")
    args = ap.parse_args()
    if args.check:
        return check_against_latest()
    doc = build_whitelist()
    paths = write_outputs(doc)
    for code in INDICES:
        m = doc["indices"][code]
        print(f"{code} {m['name']}: {m['count']} (as_of {m['as_of']})")
    print(f"whitelist total: {doc['count_total']}")
    for p in paths:
        print("written:", p.relative_to(_REPO))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
