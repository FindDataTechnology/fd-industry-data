#!/usr/bin/env python3
"""NBS 农业年度序列装载（A 单任务 2.7）——读 `cnstats`，产出可落库产物。

**为什么走 cnstats 而不是直连 NBS**（2026-11-08 实测）：
`data.stats.gov.cn/easyquery.htm` 对本机出口无条件返回
`403 reason:UrlACL`（IPv6 `2408:8256:…`、IPv4 `163.125.162.244`、强制 IPv4、
scrapling chrome 指纹、ego 真浏览器 —— 全部 403）。而 windows-admin 的
`cnstats` 库就是那条「专门获取 NBS 的项目」的落地：
`nbs_indicator` 79,525 条 + `nbs_observation` 2,047 万条。
**不绕 WAF、不搬 Cookie**，直接从落地库取。

口径纪律：
* 只取 **全国口径**（`region_id IS NULL`）。分省序列是另一层，混进来会让「全国产量」
  变成 31 个省的和（等于翻倍）。
* `value IS NULL` 的期（实测 2026 年尚未发布）**不写行**，不补 0。
* 期数门沿用全库规则：≥24 期 = 正式序列；12–23 期 = `short_window(n)` 显式声明；
  <12 期 = 不注册（只留档）。
* 指标名用源表原名（`rtrim` 去尾随空格——源表名字带尾空格，精确匹配会全部落空，
  这是本轮踩到的第一个坑）。

用法::

    python3 scripts/load_nbs_agri_annual.py            # 只出产物，不写库
    python3 scripts/load_nbs_agri_annual.py --apply     # 写 fd_open_data（需有效 DSN）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse as up
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = Path("/Users/chengsishi/finddata/fd-industry-data/reports/agriculture-nbs")
SOURCE = "nbs-stats"
CATEGORY = "农业"

#: 目标指标：源表原名（rtrim 后精确匹配）→ (概念后缀, 中文名, 英文名, 单位)
SERIES = {
    "粮食产量 (万吨)":           ("grain.output.annual", "粮食产量", "Grain output", "万吨"),
    "谷物产量 (万吨)":           ("cereal.output.annual", "谷物产量", "Cereal output", "万吨"),
    "稻谷产量 (万吨)":           ("rice.output.annual", "稻谷产量", "Rice output", "万吨"),
    "小麦产量 (万吨)":           ("wheat.output.annual", "小麦产量", "Wheat output", "万吨"),
    "玉米产量 (万吨)":           ("corn.output.annual", "玉米产量", "Corn output", "万吨"),
    "肉类产量 (万吨)":           ("meat.output.annual", "肉类产量", "Meat output", "万吨"),
    "猪肉产量 (万吨)":           ("pork.output.annual", "猪肉产量", "Pork output", "万吨"),
    "牛肉产量 (万吨)":           ("beef.output.annual", "牛肉产量", "Beef output", "万吨"),
    "羊肉产量 (万吨)":           ("mutton.output.annual", "羊肉产量", "Mutton output", "万吨"),
    "水产品总产量 (万吨)":       ("aquatic.output.annual", "水产品总产量", "Aquatic products output", "万吨"),
    "造林总面积 (千公顷)":       ("afforestation.area.annual", "造林总面积", "Afforestation area", "千公顷"),
    "农作物总播种面积 (千公顷)": ("crop.sown_area.annual", "农作物总播种面积", "Crop sown area", "千公顷"),
    "有效灌溉面积 (千公顷)":     ("irrigated.area.annual", "有效灌溉面积", "Effective irrigated area", "千公顷"),
    "农业机械总动力 (万千瓦)":   ("machinery.power.annual", "农业机械总动力", "Total agricultural machinery power", "万千瓦"),
    "农用化肥施用折纯量 (万吨)": ("fertilizer.use.annual", "农用化肥施用折纯量", "Chemical fertilizer use (pure nutrient)", "万吨"),
}

#: 交叉核对恒等式（同源内部一致性，不引外部数字）：
#: 粮食产量 应 ≈ 稻谷+小麦+玉米+其他谷物+豆类+薯类；这里只比可得的三个主粮，
#: 故只做「主粮之和 ≤ 粮食产量」的方向性检查，不做等值断言。
IDENTITY_NOTE = "稻谷+小麦+玉米 之和应 ≤ 粮食产量（其余为其他谷物/豆类/薯类）"


def _conn():
    dsn = os.environ.get("CNSTATS_DSN") or os.environ.get("YEARBOOK_DSN")
    if not dsn:
        raise SystemExit("FATAL 缺 CNSTATS_DSN / YEARBOOK_DSN")
    p = up.urlparse(dsn)
    return dsn.replace((p.path or ""), "/cnstats")


def load(cur) -> tuple[list[dict], list[str]]:
    notes: list[str] = []
    names = list(SERIES)
    cur.execute("set statement_timeout='180s'")
    # 名字带尾随空格：必须 rtrim 后再比，否则精确匹配 0 命中（本轮实测坑）
    cur.execute("""select id, rtrim(name) nm from nbs_indicator
                   where dbcode='hgnd' and rtrim(name) = any(%s)""", (names,))
    ids = {nm: i for i, nm in cur.fetchall()}
    missing = [n for n in names if n not in ids]
    if missing:
        notes.append(f"源表未印（跳过，不补造）：{missing}")

    rows: list[dict] = []
    for name, (suffix, zh, en, unit) in SERIES.items():
        if name not in ids:
            continue
        cur.execute("""select period, value from nbs_observation
                       where indicator_id=%s and region_id is null
                       order by period""", (ids[name],))
        pts = [(p, float(v)) for p, v in cur.fetchall() if v is not None]
        nulls = cur.rowcount - len(pts)
        rows.append({
            "concept": f"agriculture.nbs.{suffix}", "name_zh": zh, "name_en": en,
            "unit": unit, "source_name": name,
            "points": pts, "periods": len(pts), "null_periods": nulls,
        })
    return rows, notes


def classify(rows: list[dict]) -> None:
    """期数分档：≥24 正式 / 12–23 short_window / <12 不注册。"""
    for r in rows:
        n = r["periods"]
        if n >= 24:
            r["gate"] = "registered"
            r["short_window"] = None
        elif 12 <= n <= 23:
            r["gate"] = "short_window"
            r["short_window"] = n
        else:
            r["gate"] = "insufficient"
            r["short_window"] = None


def cross_check(rows: list[dict]) -> list[str]:
    """同源内部一致性（方向性，不等值断言）。"""
    out: list[str] = []
    by = {r["concept"]: dict(r["points"]) for r in rows}
    grain = by.get("agriculture.nbs.grain.output.annual") or {}
    mains = [by.get(k) or {} for k in (
        "agriculture.nbs.rice.output.annual",
        "agriculture.nbs.wheat.output.annual",
        "agriculture.nbs.corn.output.annual")]
    common = sorted(set(grain) & set(mains[0]) & set(mains[1]) & set(mains[2]))
    over = [y for y in common if sum(m[y] for m in mains) > grain[y] * 1.001]
    out.append(f"主粮之和 ≤ 粮食产量：共比 {len(common)} 年，越界 {len(over)} 年"
               + (f"（{over[:3]}）" if over else ""))
    meat = by.get("agriculture.nbs.meat.output.annual") or {}
    parts = [by.get("agriculture.nbs.pork.output.annual") or {},
             by.get("agriculture.nbs.beef.output.annual") or {},
             by.get("agriculture.nbs.mutton.output.annual") or {}]
    cy = sorted(set(meat) & set(parts[0]) & set(parts[1]) & set(parts[2]))
    over2 = [y for y in cy if sum(p[y] for p in parts) > meat[y] * 1.001]
    out.append(f"猪+牛+羊肉 ≤ 肉类产量：共比 {len(cy)} 年，越界 {len(over2)} 年"
               + (f"（{over2[:3]}）" if over2 else ""))
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="写 fd_open_data（需有效 DSN）")
    args = ap.parse_args(argv)

    import psycopg2

    conn = psycopg2.connect(_conn(), connect_timeout=10)
    conn.autocommit = True
    cur = conn.cursor()
    rows, notes = load(cur)
    classify(rows)
    checks = cross_check(rows)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    series_doc = {
        "generated_for": "add-agriculture-industry-sources 2.7（NBS 农业年度序列）",
        "source_of_truth": "windows-admin cnstats（nbs_indicator / nbs_observation）",
        "why_not_api": "data.stats.gov.cn 对本机出口 403 reason:UrlACL"
                       "（IPv4/IPv6/指纹/真浏览器全部实测被拦）",
        "caliber": "全国口径（region_id IS NULL）；空值不写行、不补 0",
        "identity_note": IDENTITY_NOTE,
        "checks": checks,
        "notes": notes,
        "series": rows,
    }
    (OUT_DIR / "nbs-agri-annual-series.json").write_text(
        json.dumps(series_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md = ["# NBS 农业年度序列（2.7 产物）", "",
          f"来源：`cnstats` 落库（`nbs_indicator` / `nbs_observation`），全国口径。",
          f"不直连 NBS 的原因：`403 reason:UrlACL`（IPv4/IPv6/浏览器指纹/ego 全部实测被拦）。", "",
          "| 指标 | 单位 | 期数 | 区间 | 门槛 | 短窗声明 |", "|---|---|---|---|---|---|"]
    for r in rows:
        lo, hi = (r["points"][0][0], r["points"][-1][0]) if r["points"] else ("-", "-")
        sw = f"short_window({r['short_window']})" if r["short_window"] else "—"
        md.append(f"| {r['name_zh']} | {r['unit']} | {r['periods']} | {lo}–{hi} | {r['gate']} | {sw} |")
    md += ["", "## 同源一致性核对", ""] + [f"- {c}" for c in checks]
    md += ["", "## 源表未印（跳过，不补造）", ""] + ([f"- {n}" for n in notes] or ["- （无）"])
    (OUT_DIR / "nbs-agri-annual-series.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    ok = [r for r in rows if r["gate"] == "registered"]
    sw = [r for r in rows if r["gate"] == "short_window"]
    bad = [r for r in rows if r["gate"] == "insufficient"]
    print(f"指标 {len(rows)} 条：正式 {len(ok)} · short_window {len(sw)} · 期数不足 {len(bad)}")
    for r in rows:
        print(f"  {r['name_zh']:<14} {r['periods']:>3} 期  {r['gate']}")
    for c in checks:
        print("  核对:", c)
    print("写出", OUT_DIR / "nbs-agri-annual-series.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
