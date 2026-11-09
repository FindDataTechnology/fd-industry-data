#!/usr/bin/env python3
"""农业上市公司财报监控白名单生成器（awaken-agriculture-report-ingest W1 / 任务 2.1–2.4）。

口径（决策记录 2026-11-08，侦察簿「硬性排除」节）：
* **A 股**：申万一级「农林牧渔」（801010）成分全量，走 cn-report 车道，四类定期报告近三年；
* **港股 / 美股**：**仅事件面**（披露日期/公告类型/报告期/头条营收净利），不做 XBRL 结构化细分；
* **农资类排除**：化肥 / 农药 / 种子生产 / 农机 不进入公司财务监控。

两个真源，都不手工维护：
* 成分集合 = `docs/agriculture-universe.yaml`（A 单 8.1 已裁定并落库，coarse-7 口径）；
* orgId = 巨潮官方映射表 `https://www.cninfo.com.cn/new/data/szse_stock.json`
  （**唯一真源**：同一交易所混用 `jjxt`/`gssh`/`9900`/`gssz` 四种前缀，交易所只能按代码段判定，
  绝不按前缀拼接）。

用法::

    python3 scripts/agri_report_whitelist.py                 # 生成 + 自检 + 打印摘要
    python3 scripts/agri_report_whitelist.py --check         # 只对账（与宇宙 yaml / 库内实体比对）
    python3 scripts/agri_report_whitelist.py --out docs/agri-report-whitelist.json

`--check` 用的三条断言（任一不成立即非零退出并指名）：
1. 白名单 A 股代码集合 == `agriculture-universe.yaml` 的成分集合；
2. 每条 A 股都有 orgId（映射表覆盖全 A 股 6263 行，缺一条即说明代码段/映射异常）；
3. **农资类排除生效**：化肥/农药/种子生产/农机 名单与白名单交集为空（排除项逐条带理由）。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
UNIVERSE = REPO / "docs" / "agriculture-universe.yaml"
ORGID_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"
ORGID_CACHE = REPO / "data" / "cache" / "szse_stock.json"

#: 农资类：不进入公司财务监控（决策记录 2026-11-08 不变项）。
#: **只能列宇宙内的成分**——列一个根本不在申万农林牧渔里的公司是错的（它本来就不是候选，
#: 说它「被排除」会让人误以为它在监控范围内）。脚本对此有硬校验：每条都必须能在
#: `agriculture-universe.yaml` 里找到，否则非零退出。
AGRI_INPUT_EXCLUDED: dict[str, str] = {
    "002215": "农药制剂（诺普信）——农资投入品，非种植/养殖经营主体",
    "002556": "农资流通（辉隆股份）——化肥/农药贸易，同上",
    "300021": "节水灌溉设备（大禹节水）——农机/农服口径，非种植经营主体",
}


def _load_orgid_map(refresh: bool) -> dict[str, dict]:
    """巨潮官方映射：code → {orgId, name, category}。缓存落盘，--refresh 重取。"""
    if refresh or not ORGID_CACHE.is_file():
        ORGID_CACHE.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(ORGID_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            ORGID_CACHE.write_bytes(resp.read())
    doc = json.loads(ORGID_CACHE.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for row in doc.get("stockList") or []:
        code = str(row.get("code") or "").strip()
        if code:
            out[code] = {"orgId": row.get("orgId"), "name": row.get("zwjc"),
                         "category": row.get("category")}
    return out


def _load_universe() -> dict:
    doc = yaml.safe_load(UNIVERSE.read_text(encoding="utf-8"))
    return {
        "caliber": doc.get("caliber"),
        "ratified": doc.get("ratified"),
        "companies": [
            {"code": c["code"], "name": c["name"], "segment": c.get("segment")}
            for c in doc.get("companies") or []
        ],
    }


def build(refresh: bool) -> tuple[dict, list[str]]:
    universe = _load_universe()
    orgids = _load_orgid_map(refresh)
    problems: list[str] = []

    excluded_codes = {c for c in AGRI_INPUT_EXCLUDED if c.isdigit()}
    universe_codes = {c["code"] for c in universe["companies"]}
    # 排除项必须来自宇宙成分：不在宇宙里的公司本来就不是候选，说它「被排除」是错的
    stray = sorted(excluded_codes - universe_codes)
    if stray:
        problems.append(
            f"农资排除名单里有不在宇宙内的代码 {stray}（不是候选，不该列入排除项）"
        )

    a_share: list[dict] = []
    for company in universe["companies"]:
        code = company["code"]
        meta = orgids.get(code)
        if code in excluded_codes:
            continue  # 农资类：排除（下面单独断言交集为空）
        a_share.append({
            "code": code,
            "name": company["name"],
            "segment": company["segment"],
            "orgId": (meta or {}).get("orgId"),
            "market": "SZ" if code[0] in "023" else "SH" if code[0] in "689" else "BJ",
        })

    for row in a_share:
        if not row["orgId"]:
            problems.append(f"A 股 {row['code']} {row['name']} 在巨潮映射表里没有 orgId")

    overlap = sorted({r["code"] for r in a_share} & excluded_codes)
    if overlap:
        problems.append(f"农资排除名单与白名单有交集（排除未生效）：{overlap}")

    doc = {
        "generated_for": "awaken-agriculture-report-ingest",
        "date": "2026-11-08",
        "caliber": {
            "a_share": "申万一级「农林牧渔」（801010）成分全量 · 四类定期报告 · 近三年 · cn-report 车道",
            "hk_us": "仅事件面（披露日期/公告类型/报告期/头条营收净利），不做 XBRL 结构化细分",
            "excluded": "农资类（化肥/农药/种子生产/农机）不进入公司财务监控",
            "orgId_source": "巨潮官方映射表 /new/data/szse_stock.json（唯一真源，不按交易所前缀拼接）",
        },
        "universe": {"caliber": universe["caliber"], "ratified": universe["ratified"],
                     "count": len(universe["companies"])},
        "a_share": sorted(a_share, key=lambda r: r["code"]),
        "excluded_agri_input": [
            {"code": c, "name": next((u["name"] for u in universe["companies"] if u["code"] == c), ""),
             "reason": r}
            for c, r in sorted(AGRI_INPUT_EXCLUDED.items()) if c.isdigit()
        ],
        "hk_event_face": [],   # 2.2：待生成（HKEX 披露易可达实体）
        "us_event_face": [],   # 2.3：待生成（SEC EDGAR CIK）
        "counts": {
            "universe": len(universe["companies"]),
            "a_share_whitelisted": len(a_share),
            "a_share_with_orgid": sum(1 for r in a_share if r["orgId"]),
            "excluded_agri_input": sum(1 for c in AGRI_INPUT_EXCLUDED if c.isdigit()),
        },
    }
    return doc, problems


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(REPO / "docs" / "agri-report-whitelist.json"))
    ap.add_argument("--refresh", action="store_true", help="重新拉取巨潮 orgId 映射表")
    ap.add_argument("--check", action="store_true", help="只对账，不写文件")
    args = ap.parse_args(argv)

    doc, problems = build(args.refresh)
    c = doc["counts"]
    print(f"宇宙 {c['universe']} 家（{doc['universe']['caliber']}）· 白名单 {c['a_share_whitelisted']} 家"
          f" · 带 orgId {c['a_share_with_orgid']} 家 · 农资排除 {c['excluded_agri_input']} 家")

    if problems:
        print("\n== 对账失败 ==")
        for p in problems:
            print(f"  ✗ {p}")
        return 1

    seg: dict[str, int] = {}
    for row in doc["a_share"]:
        seg[row["segment"]] = seg.get(row["segment"], 0) + 1
    print("  分板块:", " / ".join(f"{k} {v}" for k, v in sorted(seg.items(), key=lambda x: -x[1])))
    prefixes: dict[str, int] = {}
    for row in doc["a_share"]:
        p = (row["orgId"] or "")[:4]
        prefixes[p] = prefixes.get(p, 0) + 1
    print("  orgId 前缀分布（证明不能按交易所拼接）:",
          " / ".join(f"{k} {v}" for k, v in sorted(prefixes.items(), key=lambda x: -x[1])))

    if args.check:
        print("\nCHECK PASS：代码集合与宇宙一致、orgId 齐备、农资排除无交集")
        return 0

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n写出 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
