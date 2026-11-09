#!/usr/bin/env python3
"""农业上市公司年报「财务细分」按需抽取（awaken-agriculture-report-ingest W3 试点）。

为什么按需抽而不是先全量 ingest：`fd-cn-report` 的 `report_cache.get_or_fetch` 已经带
缓存（同一份 PDF 只下载解析一次），所以「取一份报告 → 抽需要的段」与「全量入库再查」
在数据获取成本上等价，但前者不改 `daas.db`、不依赖 ingest 流水线跑完，适合先验证规则质量。

抽三类细分（都在「经营情况讨论与分析 → 主营业务分析」里）：
  ① **分行业**：业务名 + 营业收入 + 收入占比 + 上年收入 + 上年占比 + 同比（可能带成本/毛利率列）
  ② **分产品**：同上（生猪/屠宰肉食/饲料原料…）
  ③ **实物销售量/生产量**：行业分类 + 项目(销售量/生产量/库存量) + 单位 + 本期 + 上期 + 同比

纪律：
  · **只抽报告里印出来的数**，不推算、不补零；正则匹配不到就是「未抽到」，不猜。
  · 名称保留原文（各家用词不同：养殖业务 / 生猪 / 猪产业），不做同义词归并——
    归并属概念映射，必须先有人工核过的对照表，不在本脚本里悄悄做。
  · 数字保留原文量级（元），不做单位换算；单位只在实物量表里取报告自印的单位。
  · 输出 JSON 供人工核对；**不写观测库**（入库需先定概念码与命名门）。

用法::

    python3 scripts/agri_segment_extract.py --codes 002714,000998,300498
    python3 scripts/agri_segment_extract.py --pilot          # 五板块各一家
    python3 scripts/agri_segment_extract.py --pilot --years 2024
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

CN_REPORT = Path("/Users/chengsishi/finddata/fd-cn-report")
REPO = Path(__file__).resolve().parents[1]
WHITELIST = REPO / "docs" / "agri-report-whitelist.json"
#: 默认写**全量**产物。历史坑：默认曾指向 `agri-segment-extract.json`（试点名），
#: 抽取端与登记端的默认不一致，导致「以为重抽了全量、其实只动了试点」这类静默错位。
OUT_DEFAULT = REPO / "docs" / "agri-segment-extract-2024-all.json"

#: 数字（含负号、千分位）
_NUM = r"-?\d[\d,]*(?:\.\d+)?"
_PCT = r"-?\d+(?:\.\d+)?%"

#: 「分行业」/「分产品」段：业务名 + 若干数字。
#: 报告文本被压平成一行，业务名是中文串（不含数字、不以标点开头），后接 ≥2 个数字。
_SEG_ROW = re.compile(
    rf"(?P<name>[\u4e00-\u9fa5A-Za-z（）()、·\-]{{2,20}}?)\s+"
    rf"(?P<rev>{_NUM})\s+(?P<rev_pct>{_PCT})\s+"
    rf"(?P<prev_rev>{_NUM})\s+(?P<prev_pct>{_PCT})"
    rf"(?:\s+(?P<yoy>{_PCT}))?"
)

#: 实物量行：行业分类 + 项目 + 单位 + 本期 + 上期 + 同比
_QTY_ROW = re.compile(
    rf"(?P<name>[\u4e00-\u9fa5A-Za-z（）()、·\-]{{2,20}}?)\s+"
    rf"(?P<item>销售量|生产量|库存量)\s+(?P<unit>[^\s\d]{{1,6}})\s+"
    rf"(?P<cur>{_NUM})\s+(?P<prev>{_NUM})\s+(?P<yoy>{_PCT})"
)

#: 段标题。**必须把所有相邻段都列进来**：主营业务分析里这几张表是连排的，
#: 窗口只截到已知标题，否则会把下一张表的数据吃进上一张（实测 000048/000019 的
#: 分地区把「直销 100%」并入，合计恰好 2 倍）。
_HEADERS = ("分行业", "分产品", "分地区", "分销售模式")

#: 表内聚合行（合计/小计/总计）与「减：内部抵消」行：不是分部，混入会让分部合计翻倍。
_AGG_ROW = re.compile(r"^(合计|小计|总计)|^减[:：]|抵消")

#: 段扫描的窗口长度（字符）。主营业务分析这一段通常 2k–8k 字，取 6k 够用且不会串到别章。
_WINDOW = 6000

#: 所有可能出现的段边界（深市简称 + 沪市「主营业务分X情况」写法）。
#: 截断时要把这些全部考虑进去，否则窗口会跨表（实测分地区把「直销 100%」吃掉）。
_BOUNDARIES = ("分行业", "分产品", "分地区", "分销售模式",
               "主营业务分行业情况", "主营业务分产品情况",
               "主营业务分地区情况", "主营业务分销售模式情况")


def _truncate(seg: str, header: str) -> str:
    """把窗口截到**下一个真实段边界**为止。

    沪市常把四个维度写成一行标题「分行业、分产品、分地区、分销售模式情况」，
    若把这种并列标题里的出现当成边界，窗口会被切到只剩几个字符（实测沪市整段抽空）。
    因此跳过「后接、或情」「前接、」的出现。
    """
    # 本段自己的表头（「主营业务分行业情况」或「分行业」）不是「下一个边界」：
    # 沪市切完并列标题行后，紧接着就是本段的表格标题，误当边界会把窗口截成 14 字符。
    own = {header, _SSE_HEADERS.get(header, header)}
    cut = len(seg)
    for b in _BOUNDARIES:
        if b in own:
            continue
        start = 0
        while True:
            k = seg.find(b, start + 1)
            if k < 0:
                break
            after = seg[k + len(b):k + len(b) + 1]
            before = seg[k - 1:k]
            if after in ("、", "情") or before == "、":
                start = k
                continue
            if k < cut:
                cut = k
            break
    return seg[:cut]

#: 沪市版式（实测 600127）：主营业务分行业情况表 —— 收入 / 成本 / 毛利率 /
#: 收入同比 / 成本同比，**没有占比列**，且毛利率变动是「增加0.62个百分点」这种文本。
#: 与深市版式（收入+占比+上年收入+上年占比+同比）列序完全不同，必须分开匹配，
#: 否则沪市公司会整段抽空（实测 37/101 家四维全空，全是 60xxxx）。
# 注意：沪市这两列**不带百分号**（实测「-6.66」而非「-6.66%」），用 _PCT 会全部不命中。
_SEG_ROW_SSE = re.compile(
    rf"(?P<name>[\u4e00-\u9fa5A-Za-z（）()、·\-]{{2,20}}?)\s+"
    rf"(?P<rev>{_NUM})\s+(?P<cost>{_NUM})\s+(?P<margin>{_NUM})\s+"
    rf"(?P<rev_yoy>-?\d+(?:\.\d+)?)\s+(?P<cost_yoy>-?\d+(?:\.\d+)?)"
)

#: 沪市的段标题写法（表头常写「主营业务分行业情况」）
_SSE_HEADERS = {"分行业": "主营业务分行业情况", "分产品": "主营业务分产品情况",
                "分地区": "主营业务分地区情况", "分销售模式": "主营业务分销售模式情况"}



def _load_cn_report():
    """把 fd-cn-report 的模块按需加载（只读使用，不改那边任何文件）。"""
    if str(CN_REPORT) not in sys.path:
        sys.path.insert(0, str(CN_REPORT))
    import cninfo_client  # noqa: F401
    import report_cache  # noqa: F401
    return cninfo_client, report_cache


def _fetch_text(code: str, year: int, form: str, cninfo, cache) -> tuple[str, dict]:
    co = cninfo.lookup_company(code)
    if not co:
        raise RuntimeError(f"cninfo 查不到该公司：{code}")
    filings = cninfo.query_announcements(co["stock_code"], co["org_id"],
                                         form=form, year=year, limit=5)
    # 年报常有「摘要」「英文版」并列，只取正文
    body = [f for f in filings
            if "摘要" not in f["title"] and "英文" not in f["title"]
            and not re.search(r"English", f["title"], re.I)]
    if not body:
        raise RuntimeError(f"{code} {year} {form}：无正文（仅有摘要/英文版）")

    def fetch(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        return urllib.request.urlopen(req, timeout=120).read()

    text, meta = cache.get_or_fetch(body[0]["pdf_url"], fetch)
    return text, {"title": body[0]["title"], "pdf_url": body[0]["pdf_url"], **meta}


def _num(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except (AttributeError, ValueError):
        return None


def _pct(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        return float(raw.rstrip("%"))
    except ValueError:
        return None


def _slice(text: str, header: str) -> str:
    """取某段标题之后的窗口；找不到返回空串。沪市表头写作「主营业务分行业情况」。"""
    for probe in (header, _SSE_HEADERS.get(header, header)):
        i = text.find(probe)
        if i >= 0:
            seg = text[i:i + _WINDOW]
            # 丢掉「分行业、分产品、分地区、分销售模式情况」这种并列标题行
            first_nl = seg.find("\n")
            if first_nl > 0 and "、" in seg[:first_nl]:
                seg = seg[first_nl + 1:]
            return _truncate(seg, header)
    return ""



#: ── 叙述式经营指标（4.2）─────────────────────────────────────────────────
#: 这类数**不出现在分部表里**，只在「经营情况讨论与分析」正文的一句话里。规则 =
#: 关键词 + 单位 + 数值范围护栏；每条命中都带回原文片段（evidence）供人工复核，
#: **抽不到就是抽不到**，不推算、不跨句拼。
#: 范围护栏的作用：把「完全成本 1.2」这种明显是别的数（如费率）挡掉。
NARRATIVE_RULES: list[dict] = [
    {"key": "free_cost", "label": "养殖完全成本",
     "rx": re.compile(r"完全成本[^。；]{0,24}?(\d+(?:\.\d+)?)\s*元\s*/\s*(?:公斤|千克|[kK][gG])"),
     "unit": "元/公斤", "lo": 8.0, "hi": 30.0},
    {"key": "avg_price", "label": "商品猪销售均价",
     "rx": re.compile(r"(?:商品猪|生猪|毛猪)(?:销售)?均价[^。；]{0,24}?(\d+(?:\.\d+)?)\s*元\s*/\s*(?:公斤|千克|[kK][gG])"),
     "unit": "元/公斤", "lo": 5.0, "hi": 50.0},
    {"key": "sow_inventory", "label": "能繁母猪存栏",
     "rx": re.compile(r"能繁母猪存栏[^。；]{0,24}?(\d+(?:[,\d]*)(?:\.\d+)?)\s*万头"),
     "unit": "万头", "lo": 0.1, "hi": 500.0},
    {"key": "feed_sales", "label": "饲料销量",
     "rx": re.compile(r"饲料(?:销量|销售量)[^。；]{0,24}?(\d+(?:[,\d]*)(?:\.\d+)?)\s*(?:万吨|万t|万 吨)"),
     "unit": "万吨", "lo": 0.1, "hi": 3000.0},
    {"key": "seed_area", "label": "制种面积",
     "rx": re.compile(r"制种面积[^。；]{0,24}?(\d+(?:[,\d]*)(?:\.\d+)?)\s*万亩"),
     "unit": "万亩", "lo": 0.01, "hi": 500.0},
    {"key": "planted_area", "label": "播种/种植面积",
     "rx": re.compile(r"(?:自营|流转|播种|种植)面积[^。；]{0,24}?(\d+(?:[,\d]*)(?:\.\d+)?)\s*万亩"),
     "unit": "万亩", "lo": 0.01, "hi": 3000.0},
    {"key": "batch_release", "label": "批签发",
     "rx": re.compile(r"批签发[^。；]{0,24}?(\d+(?:[,\d]*)(?:\.\d+)?)\s*(?:批|万头份|亿头份|万ml|亿ml)"),
     "unit": "", "lo": 0.01, "hi": 100000.0},
]


#: 命中片段的**排除语**：出现这些词说明该数是行业/第三方口径，不是公司自身指标。
#: 实测反例：大北农「全国生猪均价（外三元）在 14.63-20.63 元/公斤区间波动」、
#: 金河生物「据中国兽医药品监察所…2024 年蓝耳苗批签发 4.12 亿头份」。这类混进来
#: 就是把行业数当公司数入库，必须拦。
_NARRATIVE_EXCLUDE = re.compile(
    r"全国|行业|据[^。；]{0,12}(?:统计|调研|数据)|数据显示|同比(?:增长|下降)?\s*区间|区间波动|平均(?:价格)?为")


def _narrative(text: str) -> list[dict]:
    """按 NARRATIVE_RULES 抽叙述式指标；每条带原文片段与范围判定。"""
    out: list[dict] = []
    for rule in NARRATIVE_RULES:
        for m in rule["rx"].finditer(text):
            raw = m.group(1).replace(",", "")
            try:
                val = float(raw)
            except ValueError:
                continue
            in_range = rule["lo"] <= val <= rule["hi"]
            start = max(0, m.start() - 30)
            snippet = text[start:m.end() + 10].replace("\n", " ").strip()
            if _NARRATIVE_EXCLUDE.search(snippet):
                continue  # 行业/第三方口径，不是公司自身指标
            out.append({
                "key": rule["key"], "label": rule["label"], "value": val,
                "unit": rule["unit"], "in_range": in_range,
                "evidence": snippet,
            })
    # 同一 key 多次命中：保留第一个「在范围内」的，其余进 evidence 供核对
    picked: dict[str, dict] = {}
    for item in out:
        if not item["in_range"]:
            continue
        picked.setdefault(item["key"], item)
    for item in out:
        if item["key"] in picked and picked[item["key"]] is not item:
            picked[item["key"]].setdefault("other_hits", []).append(
                f"{item['value']}{item['unit']}")
    return list(picked.values())


def extract_one(code: str, year: int, cninfo, cache) -> dict:
    text, meta = _fetch_text(code, year, "年度报告", cninfo, cache)
    out: dict = {"code": code, "year": year, "report": meta, "by_dimension": {}}
    for header in _HEADERS:
        seg = _slice(text, header)
        if not seg:
            out["by_dimension"][header] = {"rows": [], "note": "报告未印该段"}
            continue
        rows: list[dict] = []
        rejected: list[dict] = []
        for m in _SEG_ROW.finditer(seg):
            name = m.group("name").strip()
            # 跨栏排版会把上一行残字带进行首（实测 000713 抽到「）香料类」）。
            # 剥掉行首的右括号/顿号/逗号等残标点；剥完仍不像业务名就整行丢弃并留痕，
            # **不把残字当业务名**。
            name = name.lstrip("）)、，,。：:；;·")
            if (name in _HEADERS or len(name) < 2
                    or not re.search(r"[\u4e00-\u9fa5A-Za-z]", name)
                    or _AGG_ROW.search(name)):
                rejected.append({"name": m.group("name").strip(), "revenue": _num(m.group("rev"))})
                continue
            rows.append({
                "name": name,
                "revenue": _num(m.group("rev")),
                "revenue_pct": _pct(m.group("rev_pct")),
                "prev_revenue": _num(m.group("prev_rev")),
                "prev_revenue_pct": _pct(m.group("prev_pct")),
                "yoy_pct": _pct(m.group("yoy")),
            })
        # 深市版式没抽到行 → 用沪市版式（收入/成本/毛利率/收入同比）再抽一次。
        if not rows:
            for m in _SEG_ROW_SSE.finditer(seg):
                name = m.group("name").strip().lstrip("）)、，,。：:；;·")
                if (name in _HEADERS or len(name) < 2
                        or not re.search(r"[\u4e00-\u9fa5A-Za-z]", name)
                        or _AGG_ROW.search(name)):
                    rejected.append({"name": m.group("name").strip(), "revenue": _num(m.group("rev"))})
                    continue
                rows.append({
                    "name": name,
                    "revenue": _num(m.group("rev")),
                    "cost": _num(m.group("cost")),
                    "gross_margin": _num(m.group("margin")),
                    "revenue_pct": None,          # 沪市不印占比
                    "prev_revenue": None,
                    "prev_revenue_pct": None,
                    "yoy_pct": float(m.group("rev_yoy")),
                })
        # 同行名去重（同一业务可能在段内出现两次：收入行与成本行）
        seen, uniq = set(), []
        for r in rows:
            if r["name"] in seen:
                continue
            seen.add(r["name"])
            uniq.append(r)
        # 行数过少 = 该报告的分部表排版与本规则不匹配（不是「公司没有分部」）。
        # 如实标「抽取不完整」，绝不把它当「该维度只有这些业务」。
        note = ""
        if not uniq:
            note = "段内未匹配到表格行"
        elif len(uniq) < 2:
            # 分地区同样要求 >=2 行：只抽到 1 个分区而占比不足 100% 时，是「只抽到一部分」，
            # 不是「该公司只在一个地区销售」。实测 000713 只抽到「国外 8.27%」。
            note = f"仅抽到 {len(uniq)} 行，疑排版/窗口不匹配（非「公司仅有这些业务/地区」）"
        out["by_dimension"][header] = {
            "rows": uniq,
            "rejected_rows": rejected,
            "complete": len(uniq) >= 2,
            "note": note,
        }

    qty, qty_rejected = [], []
    for m in _QTY_ROW.finditer(text):
        name, unit = m.group("name").strip(), m.group("unit")
        # 两道拒收（实测各栽过一次）：
        # ① **单位是货币的「销售量」是假的**——`销售量 元 1,252,826,682.61` 其实是
        #    营业收入被当成了成交量（隆平/登海/金大地等 6 家共 20 行）。成交量不可能
        #    用元计价，一律拒收并留痕。
        # ② 聚合行（合计/小计/总计）不是品种/分部——000998 的「合计 54.3 亿元」漏网过。
        if "元" in unit:
            qty_rejected.append({"name": name, "item": m.group("item"), "unit": unit,
                                 "why": "实物量单位是货币（收入被误当成交量），拒收"})
            continue
        if _AGG_ROW.search(name):
            qty_rejected.append({"name": name, "item": m.group("item"), "unit": unit,
                                 "why": "聚合行（合计/小计/总计）不是品种，拒收"})
            continue
        qty.append({"name": name, "item": m.group("item"),
                    "unit": unit, "current": _num(m.group("cur")),
                    "prev": _num(m.group("prev")), "yoy_pct": _pct(m.group("yoy"))})
    out["physical_volume"] = qty[:20]
    out["physical_volume_rejected"] = qty_rejected[:20]
    out["narrative"] = _narrative(text)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--codes", default="", help="逗号分隔股票代码")
    ap.add_argument("--pilot", action="store_true", help="五板块各取一家")
    ap.add_argument("--years", default="2024", help="年份，逗号分隔")
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args(argv)

    if args.pilot:
        wl = json.loads(WHITELIST.read_text(encoding="utf-8"))
        by: dict[str, list[str]] = {}
        for r in wl["a_share"]:
            by.setdefault(r["segment"], []).append(r["code"])
        pick = [v[0] for seg in ("养殖", "饲料", "种植", "动保", "农产品加工") if (v := by.get(seg))]
        codes = pick
    else:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    if not codes:
        raise SystemExit("给 --codes 或 --pilot")

    years = [int(y) for y in args.years.split(",") if y.strip()]
    cninfo, cache = _load_cn_report()

    results, problems = [], []
    for code in codes:
        for year in years:
            try:
                one = extract_one(code, year, cninfo, cache)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{code} {year}: {type(exc).__name__}: {str(exc)[:90]}")
                print(f"  ✗ {code} {year}: {type(exc).__name__} {str(exc)[:60]}")
                continue
            counts = {k: len(v["rows"]) for k, v in one["by_dimension"].items()}
            print(f"  ✓ {code} {year}: 分行业 {counts['分行业']} 行 · 分产品 {counts['分产品']} 行"
                  f" · 分地区 {counts['分地区']} 行 · 实物量 {len(one['physical_volume'])} 行")
            for dim in ("分行业", "分产品"):
                for row in one["by_dimension"][dim]["rows"][:3]:
                    print(f"       {dim} | {row['name'][:12]:<13} 收入 {row['revenue']:,.0f} "
                          f"({row['revenue_pct']}%) 同比 {row['yoy_pct']}%")
            results.append(one)

    doc = {"generated_for": "awaken-agriculture-report-ingest W3 试点（按需抽取）",
           "date": "2026-11-08", "years": years,
           "method": "fd-cn-report report_cache.get_or_fetch（带缓存）+ 全文正则按段抽取；"
                     "只抽报告印出的数，不推算不补零，名称保留原文不归并",
           "companies": results, "problems": problems}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n成功 {len(results)} 份 · 失败 {len(problems)} 份 · 写出 {out}")
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
