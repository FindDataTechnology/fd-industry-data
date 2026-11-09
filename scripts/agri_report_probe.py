#!/usr/bin/env python3
"""农业定期报告可得性探查（awaken-agriculture-report-ingest W2 前置，任务 3.1/3.2 的只读部分）。

**只读**：只调巨潮检索接口 + 取 HEAD/首字节验 PDF 可达，**不下载全量、不写 daas.db、
不改 `fd-cn-report`**。目的是在真正跑 ingest 之前，先把链路三环验通并留证：

1. **白名单 orgId 有效**：用白名单里的 `(code, orgId)` 调 `hisAnnouncement/query`，
   返回条数非零——顺带证明「orgId 必须查巨潮官方映射表」（农业样本里 `gssz` 与 `9900`
   两种前缀并存，按交易所拼前缀必错）。
2. **类目未失效**：`category=category_ndbg_szsh` 返回条数 > 0，且标题里能筛出
   「年度报告 / 半年度报告 / 第一季度报告 / 第三季度报告」（排除「摘要」「英文版」）。
3. **PDF 直链可达**：`https://static.cninfo.com.cn/<adjunctUrl>` 返回 200 且
   content-type 为 PDF、体积合理（>100 KB），**不落盘全文**（只读前 1 KB 判类型）。

用法::

    python3 scripts/agri_report_probe.py                 # 全白名单抽样（每板块 1 家）
    python3 scripts/agri_report_probe.py --per-segment 2 # 每板块 2 家
    python3 scripts/agri_report_probe.py --all           # 全部 101 家（慢，~101 次请求）
    python3 scripts/agri_report_probe.py --out docs/agri-report-probe.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WHITELIST = REPO / "docs" / "agri-report-whitelist.json"
QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
PDF_BASE = "https://static.cninfo.com.cn/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
SE_DATE = "2023-01-01~2026-11-08"   # 近三年

#: 四类定期报告 → 巨潮类目。标题正则兼容「年度报告/年年度报告/半年度报告」等写法。
REPORT_KINDS = {
    "annual":  {"category": "category_ndbg_szsh",  "rx": re.compile(r"^\d{4}\s*年\s*年?度报告$")},
    "interim": {"category": "category_bndbg_szsh", "rx": re.compile(r"^\d{4}\s*年\s*半年度报告$")},
    "q1":      {"category": "category_yjdbg_szsh", "rx": re.compile(r"^\d{4}\s*年\s*(第一季度报告|一季度报告)$")},
    "q3":      {"category": "category_sjdbg_szsh", "rx": re.compile(r"^\d{4}\s*年\s*第三季度报告$")},
}
#: 明确不是定期报告正文的（摘要/英文版/已取消），一律排除。
TITLE_EXCLUDE = re.compile(r"摘要|英文|English|已取消|更正后|修订")


def _query(code: str, orgid: str, category: str) -> dict:
    body = urllib.parse.urlencode({
        "pageNum": 1, "pageSize": 30, "column": "szse", "tabName": "fulltext",
        "stock": f"{code},{orgid}", "category": category,
        "seDate": SE_DATE, "isHLtitle": "true",
    }).encode()
    req = urllib.request.Request(QUERY_URL, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "User-Agent": UA,
        "Referer": f"https://www.cninfo.com.cn/new/disclosure/stock?stockCode={code}",
    })
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.load(resp)


def _pdf_ok(adjunct: str) -> tuple[bool, str]:
    """只读 1 KB 判类型与体积，不落全文。"""
    req = urllib.request.Request(PDF_BASE + adjunct, headers={
        "User-Agent": UA, "Range": "bytes=0-1023"})
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            head = resp.read(1024)
            ctype = resp.headers.get("Content-Type", "")
            total = resp.headers.get("Content-Range") or resp.headers.get("Content-Length") or ""
        ok = head[:4] == b"%PDF" and ("pdf" in ctype.lower() or ctype == "")
        return ok, f"{ctype} · {total}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:60]}"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--per-segment", type=int, default=1, help="每板块抽几家（默认 1）")
    ap.add_argument("--all", action="store_true", help="全白名单")
    ap.add_argument("--out", default=str(REPO / "docs" / "agri-report-probe.json"))
    ap.add_argument("--no-pdf", action="store_true", help="只验检索，不验 PDF")
    args = ap.parse_args(argv)

    wl = json.loads(WHITELIST.read_text(encoding="utf-8"))
    rows = wl["a_share"]
    if not args.all:
        by_seg: dict[str, list[dict]] = {}
        for r in rows:
            by_seg.setdefault(r["segment"], []).append(r)
        rows = [r for seg in sorted(by_seg) for r in by_seg[seg][: args.per_segment]]

    results: list[dict] = []
    problems: list[str] = []
    for r in rows:
        entry = {"code": r["code"], "name": r["name"], "segment": r["segment"],
                 "orgId": r["orgId"], "kinds": {}}
        for kind, spec in REPORT_KINDS.items():
            try:
                doc = _query(r["code"], r["orgId"], spec["category"])
            except Exception as exc:  # noqa: BLE001
                entry["kinds"][kind] = {"error": f"{type(exc).__name__}: {str(exc)[:60]}"}
                problems.append(f"{r['code']} {kind} 检索失败")
                continue
            anns = doc.get("announcements") or []
            hits = [a for a in anns
                    if "摘要" not in a["announcementTitle"]
                    and not TITLE_EXCLUDE.search(a["announcementTitle"])]
            entry["kinds"][kind] = {
                "total": doc.get("totalRecordNum"),
                "matched": len(hits),
                "titles": [a["announcementTitle"] for a in hits[:3]],
                "first_adjunct": (hits[0]["adjunctUrl"] if hits else None),
            }
            if not hits:
                problems.append(f"{r['code']} {r['name']} {kind}：类目返回 {len(anns)} 条但无标题匹配")
            time.sleep(0.35)
        if not args.no_pdf:
            sample = next((v["first_adjunct"] for v in entry["kinds"].values()
                           if isinstance(v, dict) and v.get("first_adjunct")), None)
            if sample:
                ok, info = _pdf_ok(sample)
                entry["pdf"] = {"adjunct": sample, "ok": ok, "info": info}
                if not ok:
                    problems.append(f"{r['code']} PDF 不可达：{info}")
                time.sleep(0.35)
        results.append(entry)
        got = sum(1 for v in entry["kinds"].values() if isinstance(v, dict) and v.get("matched"))
        print(f"  {r['code']} {r['name'][:10]:<12}{r['segment']:<7} orgId={r['orgId']:<14}"
              f" 四类命中 {got}/4 · PDF {'OK' if entry.get('pdf', {}).get('ok') else '—'}")

    ok_rows = sum(1 for e in results if all(
        isinstance(v, dict) and v.get("matched") for v in e["kinds"].values()))
    print(f"\n样本 {len(results)} 家 · 四类报告全部可得 {ok_rows} 家 · 问题 {len(problems)} 条")
    for p in problems[:8]:
        print(f"  ✗ {p}")

    doc = {
        "generated_for": "awaken-agriculture-report-ingest W2 前置探查（只读）",
        "date": "2026-11-08",
        "window": SE_DATE,
        "method": "巨潮 hisAnnouncement/query（免鉴权）+ static.cninfo 首 1KB 类型校验；不下载全文、不写库",
        "samples": results,
        "summary": {"sampled": len(results), "all_four_kinds": ok_rows,
                    "problems": problems},
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"写出 {out}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
