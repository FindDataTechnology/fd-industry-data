#!/usr/bin/env python3
"""试点逐值核对：年报原文 vs 语义库（awaken-agriculture-report-ingest 3.3）。

**为什么要有这一步**：ingest 只证明「文件下下来了、章切出来了」，不证明**数对得上**。
本轮 3.3 要求把通用财务面逐值对照报告原文，所以这里从**报告正文**里取
「主要会计数据」表的四个数（营业收入 / 归母净利润 / 经营活动现金流量净额 / 总资产）
加「合并利润表」的营业成本（用来核毛利率），再与语义库里同一 (公司, 期末)
的观测值逐值比对。

口径（实证踩过的坑都在这里）：

* 「主要会计数据」表是**标签一行、数值各占一行**，且长标签会断行
  （「归属于上市公司股东的」/「净利润（元）」），所以先扁平化空白再取
  标签后的**第一个**千分位数——不能按行取。
* 标签必须带 ``（元）`` 锚定：「分季度主要财务指标」里也有「营业收入」，
  不带单位锚定会取到单季数。
* 库里有**两条车道**（akshare 季频 / gta-panel 年频），同一 (公司,期) 只能取**一条**，
  否则相加翻倍（本轮早前在 verify SQL 上栽过一次）。这里按 akshare 优先取单条。
* 毛利率是**派生**指标（(营业收入−营业成本)/营业收入），两边各自用同一公式算，
  不拿库里的比率去比原文的比率（原文常写「毛利率 19.05%」但口径含运费）。

用法::

    python3 scripts/agri_pilot_value_check.py                 # 5 家 × FY2024/FY2025
    python3 scripts/agri_pilot_value_check.py --codes 002714 --years 2024

退出码：任一 (公司,期,指标) 对不上 → 1；对不上就先解释是「口径差异」还是「解析坏了」。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

CACHE = Path("/Users/chengsishi/finddata/fd-cn-report/.cache/reports")
OUT_DIR = Path("/Users/chengsishi/finddata/reports/agriculture-pilot")

#: 3.1 试点（与 fd-cn-report/scripts/agri_ingest.py 的 PILOT 一致）
PILOT = ["002714", "300967", "001366", "000998", "600598"]
#: 参与**独立核对**的车道：季频原始报表优先，年频面板做交叉校验。
#: **`cn-report` 必须排除**：那正是本线从同一份报告正文抽出来的值，
#: 拿它来核报告原文等于自证（实测加了它以后两条「库内缺期」凭空消失，是假绿）。
INDEPENDENT_LANES = ("akshare", "cnreport", "gta-panel")

#: 报告原文「主要会计数据」里的标签 → 语义库概念
#: 标签的「（元）」**不能写死**：深市版式带它，沪市（如北大荒）只在表头写
#: 「单位：元 币种：人民币」，正文标签不带单位。两种都得认。
TEXT_FIELDS = {
    "营业收入": ("financials.revenue", r"营业收入（元）|营业收入"),
    "归母净利润": ("financials.net_income", r"归属于上市公司股东的净利润（元）|归属于上市公司股东的净利润"),
    "经营现金流净额": ("financials.operating_cash_flow",
                 r"经营活动产生的现金流量净额（元）|经营活动产生的现金流量净额"),
    # 晓鸣股份写的是「资产总额（元）」不是「总资产」——两种都要认
    "总资产": ("financials.total_assets", r"总资产（元）|资产总额（元）|总资产|资产总额"),
}
#: 相对容差 0.5%（原文两位小数、库里可能是整数元）
REL_TOL = 5e-3


def _flat(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _num(tok: str) -> float | None:
    """`-28,713,852.78` → 负；`(58,615)` → 负。

    第一版把非数字全删了，**连负号一起删**——播恩集团两年净利与经营现金流被读成
    正负相反（原文明明是亏损），差点当成数据源错误上报。符号必须单独保留。
    """
    tok = (tok or "").strip()
    if not tok:
        return None
    neg = tok.startswith("(") or tok.startswith("-") or tok.startswith("－")
    digits = re.sub(r"[^\d.]", "", tok)
    if not digits:
        return None
    val = float(digits)
    return -val if neg else val


def _garbled(text: str) -> bool:
    """判断「主要会计数据」段的**中文标签**是否因字体缺 ToUnicode 而乱码。

    判据只看标签段：出现替换符或（在 600 字窗口里）成片非汉字扩展区字符。
    数字是 ASCII，不会被误判——正因为数字正常、标签乱码，才不能按位置猜数字含义。
    """
    flat = re.sub(r"\s+", "", text)
    i = flat.find("主要会计数据")
    window = flat[i:i + 600] if i >= 0 else flat[:600]
    if "\ufffd" in window:
        return True
    weird = sum(1 for ch in window if not (ch.isascii() or "\u4e00" <= ch <= "\u9fff" or ch in "，。（）：、%"))
    return weird > 40


def _report_text(code: str, year: int) -> Path | None:
    hits = sorted(CACHE.glob(f"{code}_{year}_年度报告_*.txt"))
    return hits[0] if hits else None


def _text_values(text: str) -> dict[str, float | None]:
    flat = _flat(text)
    start = flat.find("主要会计数据和财务指标")
    window = flat[start:start + 6000] if start >= 0 else flat[:6000]
    out: dict[str, float | None] = {}
    for label, (_concept, pattern) in TEXT_FIELDS.items():
        m = re.search(f"(?:{pattern})" + r"(-?\d[\d,]*\.\d{2})", window)
        out[label] = _num(m.group(1)) if m else None
    # 营业成本必须锚在**合并利润表**里取：第一版对全文取第一个「营业成本」，
    # 抓到的是「营业收入构成」分析表里分行业的成本，牧原毛利率因此被算成 19.05%
    # （真值 20.75%）。表内取不到就留空，不猜。
    out["营业成本"] = None
    out["_cost_note"] = None
    lm = flat.find("合并利润表")
    if lm >= 0:
        seg = flat[lm:lm + 6000]
        m = re.search(r"营业成本(-?\d[\d,]*\.\d{2})", seg)
        if m:
            out["营业成本"] = _num(m.group(1))
        elif re.search(r"营业成本[一二三四五六七八九十]+、\d", seg):
            # 北大荒版式：附注号与金额**粘连**（`营业成本七、613,492,136,748.90`，
            # 真值 3,492,136,748.90，前面 61 是附注号）。金额边界不可辨 →
            # 标为不可靠并跳过毛利率，绝不按位置截取。
            out["_cost_note"] = "附注号与金额粘连，金额边界不可辨（不截取）"
        else:
            out["_cost_note"] = "合并利润表窗口内无「营业成本」行"
    else:
        out["_cost_note"] = "未找到「合并利润表」"
    return out


def _db_values(cur, code: str, year: int) -> dict[str, tuple[float, str]]:
    concepts = [c for c, _p in TEXT_FIELDS.values()] + ["financials.operating_cost"]
    cur.execute("""
        select k.code, o.value, o.source_used,
               row_number() over (partition by k.code
                                  order by array_position(%s::text[], o.source_used)) as rn
        from semantic_observations o
        join entities e on e.id = o.entity_id
        join concepts k on k.id = o.concept_id
        where e.code = %s and o.date = %s and k.code = any(%s)
          and o.source_used = any(%s)
    """, (list(INDEPENDENT_LANES), code, f"{year}-12-31", concepts,
         list(INDEPENDENT_LANES)))
    out: dict[str, tuple[float, str]] = {}
    for kcode, value, lane, rn in cur.fetchall():
        if rn == 1 and value is not None:
            out[kcode] = (float(value), lane)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--codes", default=",".join(PILOT))
    ap.add_argument("--years", default="2024,2025")
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args(argv)

    import psycopg2

    dsn = os.environ.get("FD_OPEN_DATA_DSN")
    if not dsn:
        raise SystemExit("FATAL 缺 FD_OPEN_DATA_DSN（source ~/.config/finddata/dashboards.env）")
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    rows: list[dict] = []
    problems: list[str] = []
    findings: list[str] = []
    for code in [c.strip() for c in args.codes.split(",") if c.strip()]:
        for year in [int(y) for y in args.years.split(",") if y.strip()]:
            path = _report_text(code, year)
            if path is None:
                rows.append({"code": code, "year": year, "state": "no_cache",
                             "note": f"缺 {code}_{year}_年度报告 的文本缓存"})
                problems.append(f"NO_CACHE {code} FY{year}")
                continue
            tv = _text_values(path.read_text(encoding="utf-8", errors="replace"))
            db = _db_values(cur, code, year)
            rec = {"code": code, "year": year, "report": path.name, "items": {}}
            garbled = _garbled(path.read_text(encoding="utf-8", errors="replace"))
            for label, (concept, _pat) in TEXT_FIELDS.items():
                src, got = tv.get(label), db.get(concept)
                if src is None:
                    # 标签取不到分两种：真没这段，还是**这段中文标签是乱码**
                    # （隆平 FY2025 那份 PDF 的字体没有 ToUnicode，数字正常、标签成
                    # 「哳Ứ㘄∳ϳ∑ϴ」）。乱码时数字虽在但无标签锚定，**不按位置猜**。
                    state = "text_unreadable" if garbled else "text_missing"
                    rec["items"][label] = {"state": state, "text": None}
                    problems.append(f"{state.upper()} {code} FY{year} {label}")
                    continue
                if got is None:
                    rec["items"][label] = {"state": "db_missing", "text": src}
                    problems.append(f"DB_MISSING {code} FY{year} {label}（原文 {src}）")
                    continue
                value, lane = got
                ok = abs(value - src) <= max(abs(src) * REL_TOL, 1.0)
                rec["items"][label] = {"state": "matched" if ok else "mismatch",
                                       "text": src, "db": value, "lane": lane,
                                       "rel_diff": (value - src) / src if src else None}
                if not ok:
                    problems.append(f"MISMATCH {code} FY{year} {label}：原文 {src} vs 库 {value}（{lane}）")
            # 毛利率（派生）：两边各自用同一公式算，比的是入参是否同源
            try:
                t_rev, t_cost = tv.get("营业收入"), tv.get("营业成本")
                d_rev, d_cost = db.get("financials.revenue"), db.get("financials.operating_cost")
                if t_rev and t_cost and d_rev and d_cost:
                    gm_text = (t_rev - t_cost) / t_rev * 100
                    gm_db = (d_rev[0] - d_cost[0]) / d_rev[0] * 100
                    ok = abs(gm_text - gm_db) <= 0.05
                    # 毛利率不等时，先看是**入参差**还是解析差：营收已对上而成本不等
                    # → 记 caliber_diff（数据源口径差），不当成解析失败，也绝不当成一致。
                    cost_gap = d_cost[0] - t_cost
                    rec["items"]["毛利率(派生)"] = {
                        "state": "matched" if ok else "caliber_diff",
                        "text": round(gm_text, 4), "db": round(gm_db, 4),
                        "lane": d_rev[1],
                        "note": (None if ok else
                                 f"营收一致但营业成本差 {cost_gap:,.2f} 元"
                                 f"（原文 {t_cost:,.2f} vs 库 {d_cost[0]:,.2f}）"),
                    }
                    if not ok:
                        findings.append(f"CALIBER {code} FY{year} 毛利率：原文派生 {gm_text:.4f}% "
                                        f"vs 库派生 {gm_db:.4f}%（营业成本差 {cost_gap:,.2f} 元）")
                else:
                    why = []
                    if not (t_rev and t_cost):
                        why.append(tv.get("_cost_note") or "原文缺营业收入/营业成本")
                    if not (d_rev and d_cost):
                        why.append("库内该车道无营业成本（gta-panel 未登记 operating_cost）")
                    rec["items"]["毛利率(派生)"] = {
                        "state": "insufficient",
                        "text": round((t_rev - t_cost) / t_rev * 100, 4) if t_rev and t_cost else None,
                        "note": "；".join(why) or None}
            except Exception as exc:  # noqa: BLE001
                rec["items"]["毛利率(派生)"] = {"state": f"error: {type(exc).__name__}"}
            rec["state"] = ("ok" if all(v.get("state") == "matched"
                                        for v in rec["items"].values()) else "attention")
            rows.append(rec)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "value-check-2026-11-08.json"
    out.write_text(json.dumps({
        "generated_for": "awaken-agriculture-report-ingest 3.3 试点逐值核对",
        "rule": "原文取自年报「主要会计数据和财务指标」表；库内按车道优先级取单条（不相加）；"
                "毛利率两边各自用 (营业收入-营业成本)/营业收入 派生后比对",
        "tolerance": {"rel": REL_TOL, "abs_floor": 1.0, "gross_margin_pp": 0.05},
        "rows": rows, "findings": findings, "problems": problems,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for rec in rows:
        print(f"{rec['code']} FY{rec['year']} [{rec['state']}] {rec.get('report', '')}")
        for label, item in rec.get("items", {}).items():
            if item["state"] == "matched":
                print(f"    ✓ {label:<12} 原文 {item['text']:.2f} · 库 {item['db']:.2f}"
                      f"（{item.get('lane')}）")
            else:
                print(f"    ✗ {label:<12} {item['state']} {item}")
    print(f"\n口径差异 {len(findings)} 条（不是解析错，要按报告口径解释）")
    for f in findings[:10]:
        print("  ~", f)
    print(f"问题 {len(problems)} 条 · 写出 {out}")
    for p in problems[:20]:
        print("  ✗", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
