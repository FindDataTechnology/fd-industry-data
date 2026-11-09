#!/usr/bin/env python3
"""港美股事件面名单生成（awaken-agriculture-report-ingest 任务 2.2 / 2.3）。

口径（决策记录 2026-11-08）：港美**仅事件面**——披露日期/公告类型/报告期/头条营收净利，
**不做 XBRL 结构化细分**；农资类（化肥/农药/种子生产/农机）排除并留理由。

两个市场的判定依据**强度不同，产出里如实标注**，不混为一谈：

* **美股 = 官方字段定案**。候选来自 SEC 官方 ticker 表，行业来自 SEC 自己的
  `data.sec.gov/submissions/CIK##########.json` 的 `sic` / `sicDescription`。
  关键词只用来**减少请求**（挑候选），**进不进名单由 SIC 决定**。
* **港股 = 名称筛查，不是官方行业**。港交所官方证券名单（18 列）**没有行业字段**，
  只有 Stock Code / Name / Category / Sub-Category / ISIN。因此港股名单的
  `classification_basis` 明写 `name-screen`，并列出**已知局限**（中文主营公司会漏，
  例如中粮家佳康 01610 这类英文名不带农业词的）。宁可少而准，不凑数。

用法::

    python3 scripts/agri_overseas_event_face.py              # 生成两个名单
    python3 scripts/agri_overseas_event_face.py --check      # 只对账
    python3 scripts/agri_overseas_event_face.py --refresh     # 重拉外部源
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "data" / "cache"
HKEX_XLSX = CACHE / "hkex_list_of_securities.xlsx"
SEC_TICKERS = CACHE / "sec_company_tickers.json"
SEC_SUB_DIR = CACHE / "sec_submissions"

HKEX_URL = ("https://www.hkex.com.hk/eng/services/trading/securities/"
            "securitieslists/ListOfSecurities.xlsx")
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUB_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
UA = "finddata-research contact@finddatatech.cloud"
#: SEC ticker 表在 www.sec.gov（本机直连超时），走 windows-laptop 的深圳家宽出口。
SEC_PROXY = "http://100.64.0.10:7890"

#: 农业相关 SIC 区间（SEC 官方 SIC 体系），按本线 coarse-7 口径对应：
#:   0100–0299 农业种植/畜牧 · 0900–0999 渔业/林业/狩猎 · 2000–2099 食品与同类产品加工（=农产品加工）
#: 农资类不从区间排除，而是按 SIC_EXCLUDED 逐条点名（化肥/农药/农机）。
SIC_AGRI_RANGES = [(100, 299), (900, 999), (2000, 2099)]
#: 明确排除的 SIC：2870 化肥/农药等农资类。
SIC_EXCLUDED = {
    2870: "化肥/农药（农资类，决策记录不变项排除）",
    3523: "农业机械（农资类排除）",
    5083: "农业机械批发（农资类排除）",
}
#: 只用来挑候选、减少 SEC 请求的关键词（**不是判定依据**）。
SEC_CANDIDATE_KW = ("FARM", "AGRI", "CROP", "SEED", "GRAIN", "CATTLE", "LIVESTOCK",
                    "POULTRY", "SWINE", "HOG", "DAIRY", "AQUACULTURE", "FISHER",
                    "PLANTATION", "RANCH", "HARVEST", "ORCHARD", "VINEYARD",
                    "FOOD", "MEAT", "PROTEIN", "POULTRY", "EGG", "MILK",
                    "SUGAR", "CORN", "SOY", "WHEAT", "RICE", "FEED")

#: 港股名称筛查规则（**这是筛查，不是官方行业分类**）。每条带 id 便于审计。
HK_NAME_RULES = [
    ("R1", re.compile(r"\bAGRI", re.I), "名称含 AGRI"),
    ("R2", re.compile(r"\bDAIRY\b", re.I), "乳业/牧场"),
    ("R3", re.compile(r"\b(FARM|RANCH|PLANTATION|ORCHARD)\b", re.I), "农场/种植园"),
    ("R4", re.compile(r"\b(POULTRY|LIVESTOCK|CATTLE|PIG|SWINE|HOG)\b", re.I), "畜禽养殖"),
    ("R5", re.compile(r"\b(AQUACULTURE|FISHER|MARINE\s+HARVEST)\b", re.I), "水产捕捞养殖"),
    ("R6", re.compile(r"\b(FEED|SEED)\b.*\b(TECH|GROUP|HOLD|INTL|INTERNATIONAL)\b", re.I),
     "饲料/种子（需再排除农资）"),
    ("R7", re.compile(r"\b(MEAT|PROTEIN|MILK|EGG|BREED)\w*", re.I), "肉类/蛋白/乳/蛋/育种"),
    ("R8", re.compile(r"\b(SUGAR|CORN|SOY\w*|WHEAT|RICE|GRAIN)\b", re.I), "粮油糖作物"),
    ("R9", re.compile(r"\b(FERTILIZ\w*|PESTICIDE|HERBICIDE|AGROCHEM\w*)\b", re.I),
     "化肥/农药 —— 农资类，命中即排除"),
]
#: 名称命中但**必须排除**的（非农林牧渔主营或农资类），逐条留理由。
HK_NAME_EXCLUDED = {
    "02373": "BEAUTYFARM MED —— 医美，非农业",
    "02657": "FOREST CABIN —— 家具制造，非林业经营",
    "00149": "CH AGRI-PROD EX —— 农产品交易所/平台，非种植养殖经营主体",
}


def _fetch(url: str, dest: Path, *, proxy: str | None = None, refresh: bool = False) -> bytes:
    if dest.is_file() and not refresh:
        return dest.read_bytes()
    dest.parent.mkdir(parents=True, exist_ok=True)
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with opener.open(req, timeout=60) as resp:
        data = resp.read()
    dest.write_bytes(data)
    return data


def build_hk(refresh: bool) -> tuple[list[dict], list[dict]]:
    """港交所官方证券名单 → 名称筛查。返回 (入选, 被排除)。"""
    import openpyxl  # 局部导入：只有本市场需要

    raw = _fetch(HKEX_URL, HKEX_XLSX, refresh=refresh)
    xlsx_path = HKEX_XLSX
    if not xlsx_path.suffix == ".xlsx":
        xlsx_path = xlsx_path.with_suffix(".xlsx")
    if not xlsx_path.is_file() or refresh:
        xlsx_path.write_bytes(raw)
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb["ListOfSecurities"]
    rows = [r for r in ws.iter_rows(min_row=4, values_only=True)
            if r[0] and str(r[0]).strip().isdigit() and len(str(r[0]).strip()) == 5]

    picked: list[dict] = []
    excluded: list[dict] = []
    for row in rows:
        code, name = str(row[0]).strip(), str(row[1]).strip()
        sub = str(row[3] or "")
        if "Equity Securities" not in sub:
            continue  # 只取股票，排除 ETF / 投资公司 / 杠杆产品
        hits = [rid for rid, rx, _why in HK_NAME_RULES if rx.search(name)]
        if not hits:
            continue
        entry = {
            "code": code, "name": name, "exchange": "HKEX",
            "board": "GEM" if "GEM" in sub else "Main Board",
            "isin": str(row[5] or ""),
            "classification_basis": "name-screen",
            "rule_hits": hits,
            "industry_evidence": "; ".join(
                f"{rid} {why}" for rid, why in
                ((r[0], r[2]) for r in HK_NAME_RULES) if rid in hits),
        }
        if code in HK_NAME_EXCLUDED:
            entry["excluded_reason"] = HK_NAME_EXCLUDED[code]
            excluded.append(entry)
        else:
            picked.append(entry)
    return picked, excluded


def build_us(refresh: bool) -> tuple[list[dict], list[dict]]:
    """SEC 官方 ticker 表 → 关键词挑候选 → **SIC 定案**。返回 (入选, 被排除)。"""
    raw = _fetch(SEC_TICKERS_URL, SEC_TICKERS, proxy=SEC_PROXY, refresh=refresh)
    doc = json.loads(raw)
    candidates = []
    for row in doc.values():
        title = str(row.get("title") or "").upper()
        if any(k in title for k in SEC_CANDIDATE_KW):
            candidates.append({"cik": int(row["cik_str"]), "ticker": row["ticker"],
                               "name": row["title"]})
    # 同一 CIK 可对应多个 ticker（多类别股/多地上市），SEC ticker 表会出现重复 CIK。
    # 按 CIK 去重并保留 ticker 字典序第一，避免同一发行人在名单里出现多次。
    uniq: dict[int, dict] = {}
    for cand in sorted(candidates, key=lambda c: (c["cik"], c["ticker"])):
        uniq.setdefault(cand["cik"], cand)
    candidates = sorted(uniq.values(), key=lambda c: c["cik"])

    picked: list[dict] = []
    excluded: list[dict] = []
    for cand in candidates:
        cik10 = f"{cand['cik']:010d}"
        cache_file = SEC_SUB_DIR / f"CIK{cik10}.json"
        try:
            body = _fetch(SEC_SUB_URL.format(cik=cik10), cache_file, refresh=refresh)
            sub = json.loads(body)
        except Exception as exc:  # noqa: BLE001
            excluded.append({**cand, "excluded_reason": f"取 SEC submissions 失败：{exc}"})
            continue
        sic_raw = sub.get("sic")
        try:
            sic = int(str(sic_raw).strip())
        except (TypeError, ValueError):
            excluded.append({**cand, "excluded_reason": f"SIC 缺失/非数值：{sic_raw!r}"})
            continue
        if sic in SIC_EXCLUDED:
            excluded.append({**cand, "sic": sic, "excluded_reason": SIC_EXCLUDED[sic]})
            continue
        if not any(lo <= sic <= hi for lo, hi in SIC_AGRI_RANGES):
            excluded.append({**cand, "sic": sic,
                             "sicDescription": sub.get("sicDescription"),
                             "excluded_reason": "SIC 不在农业区间 0100–0299 / 0900–0999"})
            continue
        picked.append({
            "cik": cik10, "ticker": cand["ticker"], "name": sub.get("name") or cand["name"],
            "sic": sic, "sicDescription": sub.get("sicDescription"),
            "exchange": (sub.get("exchanges") or [None])[0],
            "classification_basis": "sec-sic",
            "industry_evidence": f"SEC SIC {sic} {sub.get('sicDescription')}",
        })
        time.sleep(0.15)  # SEC 公平访问
    return picked, excluded


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", default=str(REPO / "docs" / "agri-overseas-event-face.json"))
    args = ap.parse_args(argv)

    hk, hk_ex = build_hk(args.refresh)
    us, us_ex = build_us(args.refresh)
    problems: list[str] = []

    if len({r["code"] for r in hk}) != len(hk):
        problems.append("港股名单有重复代码")
    for r in hk:
        if not re.fullmatch(r"\d{5}", r["code"]):
            problems.append(f"港股代码不是 5 位：{r['code']}")
    if len({r["cik"] for r in us}) != len(us):
        problems.append("美股名单有重复 CIK")
    for r in us:
        if not re.fullmatch(r"\d{10}", r["cik"]):
            problems.append(f"CIK 未零填充到 10 位：{r['cik']}")
        if not any(lo <= r["sic"] <= hi for lo, hi in SIC_AGRI_RANGES):
            problems.append(f"{r['ticker']} 的 SIC {r['sic']} 不在农业区间")

    print(f"港股事件面：入选 {len(hk)} 家 · 排除 {len(hk_ex)} 家（分类依据=名称筛查，非官方行业）")
    print(f"美股事件面：入选 {len(us)} 家 · 排除 {len(us_ex)} 家（分类依据=SEC SIC 官方字段）")
    if us:
        print("  SIC 分布:", ", ".join(sorted({f"{r['sic']}" for r in us}))[:120])

    if problems:
        print("\n== 对账失败 ==")
        for p in problems[:10]:
            print(f"  ✗ {p}")
        return 1

    if args.check:
        print("\nCHECK PASS：港股代码 5 位唯一、美股 CIK 10 位唯一、SIC 全落农业区间")
        return 0

    doc = {
        "generated_for": "awaken-agriculture-report-ingest W6 事件面",
        "date": "2026-11-08",
        "caliber": {
            "both": "仅事件面：披露日期/公告类型/报告期/头条营收净利；不做 XBRL 结构化细分",
            "hk": "港交所官方证券名单 + 名称筛查（该名单无行业字段，非官方行业分类）",
            "us": "SEC 官方 ticker 表挑候选 + SEC SIC 定案（官方行业字段）",
            "excluded": "农资类（化肥/农药/种子生产/农机）排除，逐条留理由",
        },
        "hk_event_face": {"basis": "name-screen", "stocks": hk, "excluded": hk_ex,
                          "known_limits": [
                              "港交所证券名单只有英文名与板块，无行业字段；名称筛查会漏掉英文名不带农业词的中文主营公司"
                              "（如中粮家佳康 01610 这类），故本名单只作事件面起点，不宣称覆盖全港农业股",
                          ]},
        "us_event_face": {"basis": "sec-sic",
                          "allowed_sic_ranges": SIC_AGRI_RANGES,
                          "excluded_sic": {str(k): v for k, v in SIC_EXCLUDED.items()},
                          "companies": us, "excluded": us_ex},
        "counts": {"hk": len(hk), "hk_excluded": len(hk_ex),
                   "us": len(us), "us_excluded": len(us_ex)},
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n写出 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
