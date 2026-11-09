#!/usr/bin/env python3
"""把 ingest 进来的农业定期报告登记成语义观测（awaken-agriculture-report-ingest 6.2）。

**来源**：`daas.db` 的 `report_documents`（`parse_status='ok'`）+ `fd-cn-report` 的文本缓存。
**映射**：`docs/agri-taxonomy-mapping.json`（6.1：中文科目 → 概念，含口径与粒度规则）。
**落库**：`source_used='cn-report'`，日期带月日、粒度按报告类型（year / ytd_h1 / ytd / ytd_9m）。

三条硬规则（都是本轮踩出来的）：

* **不覆盖别的车道**：只写 `source_used='cn-report'`，不碰 akshare / gta-panel；
  同一 (实体, 期) 多车道并存是既定形态，读数端按车道优先级取单条，**绝不相加**。
* **不复用坏车道**：库里 `source_used='cnreport'` 的旧行日期只写「2025」、粒度写成 `day`，
  那是坏数据——本脚本不读它、不改它，另起正确粒度的车道。
* **不出假数**：取不到就跳过并计入 `skipped`（带原因）；负号/括号必须保留；
  中文标签乱码（PDF 字体缺 ToUnicode）判 `text_unreadable` 跳过，**不按位置猜数字含义**。

用法::

    python3 scripts/register_agri_cnreport_obs.py --verify      # 只算不写
    python3 scripts/register_agri_cnreport_obs.py --apply       # 写库（幂等）
    python3 scripts/register_agri_cnreport_obs.py --apply --codes 002714 --years 2024
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

MAPPING = REPO / "docs" / "agri-taxonomy-mapping.json"
WHITELIST = REPO / "docs" / "agri-report-whitelist.json"
DAAS = Path("/Users/chengsishi/finddata/daas.db")
CACHE = Path("/Users/chengsishi/finddata/fd-cn-report/.cache/reports")
OUT_DIR = Path("/Users/chengsishi/finddata/reports/agriculture-pilot")
SOURCE = "cn-report"

#: 报告类型 → (期末日模板, 粒度)。与 mapping 的 period_rule 同源，代码内保留一份便于校验。
PERIOD = {
    "年度报告": ("{y}-12-31", "year"),
    "半年度报告": ("{y}-06-30", "ytd_h1"),
    "第一季度报告": ("{y}-03-31", "ytd"),
    "第三季度报告": ("{y}-09-30", "ytd_9m"),
}


def _dec(tok: str) -> str | None:
    """原文数串 → 规范十进制串（去千分位、保留符号与小数位，**不经 float**）。

    实测坑：先转 float 再 str，`137,946,892,076.87` 会写成 `137946892076.8711`
    （value 列是 varchar，这种精度污染会一路带到看板）。
    """
    tok = (tok or "").strip()
    if not tok:
        return None
    neg = tok.startswith("(") or tok.startswith("-") or tok.startswith("－")
    digits = re.sub(r"[^\d.]", "", tok)
    if not digits or not re.search(r"\d", digits):
        return None
    return ("-" if neg else "") + digits


def _appears_as_printed(value: str, flat: str) -> bool:
    """value 是否在正文里以报告印法出现（`82,244,944.00` / `-28,713,852.78` / `(58,615)`）。

    金额 <1000 或每股收益这类短数，substring 命中无意义（`0.06` 到处都有），
    因此只在「整数部分 ≥ 4 位」时才做原样回核；其余按「小数位已限死」放行——
    限死位数的正则不会造数，最多是取不到。
    """
    body = value.lstrip("-")
    whole = body.split(".")[0]
    if len(whole) < 4:
        return True
    cands = {value, "((" + body + "))"}
    if "." in body:
        whole_f, frac = body.split(".", 1)
    else:
        whole_f, frac = body, ""
    grouped = f"{int(whole_f):,}" + (f".{frac}" if frac else "")
    cands |= {grouped, "-" + grouped, f"({grouped})"}
    return any(c in flat for c in cands)


def _num(tok: str) -> float | None:
    """保留负号与括号（第一版把负号删了，亏损被读成盈利）。"""
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
    flat = re.sub(r"\s+", "", text)
    i = flat.find("主要会计数据")
    window = flat[i:i + 600] if i >= 0 else flat[:600]
    if "\ufffd" in window:
        return True
    return sum(1 for ch in window
               if not (ch.isascii() or "\u4e00" <= ch <= "\u9fff" or ch in "，。（）：、%")) > 40


def _extract(text: str, fields: list[dict]) -> tuple[dict, dict]:
    """按映射表取数；返回 (值, 不可得原因)。"""
    flat = re.sub(r"\s+", "", text)
    start = flat.find("主要会计数据")
    head = flat[start:start + 8000] if start >= 0 else flat[:8000]
    vals: dict[str, tuple[float, str]] = {}
    why: dict[str, str] = {}
    for f in fields:
        concept, unit = f["concept"], f["unit"]
        if f.get("section") == "合并利润表":
            lm = flat.find("合并利润表")
            if lm < 0:
                why[concept] = "未找到「合并利润表」"
                continue
            seg = flat[lm:lm + 6000]
        else:
            seg = head
        hit = None
        dec_max = 3 if unit == "元/股" else 2      # 金额两位、每股最多三位，按报告印法
        for zh in f["zh"]:
            # 标签后可能跟括注（「营业利润（亏损以“－”号填列）20,010,546,646.69」），
            # 括注里不许有数字，避免跨行吃到别的数。
            # **小数位必须限死**：PDF 文本层会把相邻列粘起来（`…732.67` 紧跟下一列 `149…`），
            # 用 `\.?\d*` 贪婪匹配就会把 `49,672,606,732.67` 读成 `49672606732.6737`
            # （实测全量登记后有 6,895 行带 float 尾巴，根因就在这里，不是 float 往返）。
            m = re.search(re.escape(zh) + rf"(?:（[^）\d]{{0,16}}）)?\(?(-?\d[\d,]*(?:\.\d{{1,{dec_max}}})?)\)?", seg)
            if m:
                hit = m.group(1)
                break
        if hit is None:
            if f.get("section") == "合并利润表" and re.search(
                    re.escape(f["zh"][0]) + r"[一二三四五六七八九十]+、\d", seg):
                why[concept] = "附注号与金额粘连，金额边界不可辨（不截取）"
            else:
                why[concept] = "该报告未按此写法披露"
            continue
        v = _dec(hit)
        if v is None:
            why[concept] = "取到的片段无法转成数值"
            continue
        # **原文回核（硬门）**：这个数必须能在报告正文里按「两位小数 + 千分位」原样找到，
        # 否则就是粘连/跨列/别表取来的。宁可少登记一个数，也不写一个错数进库
        # （库里 value 是 varchar，一旦写错会一路带到看板且看不出异常）。
        if not _appears_as_printed(v, flat):
            why[concept] = f"还原值 {v} 未在正文中按原样出现（疑跨列粘连），不登记"
            continue
        vals[concept] = (v, unit)
    return vals, why


def _text_of(raw_path: str | None, code: str, year: int, form: str) -> Path | None:
    if raw_path:
        p = Path(raw_path)
        stem = p.with_suffix("")
        cand = Path(str(stem) + ".txt")
        if cand.is_file():
            return cand
    hits = sorted(CACHE.glob(f"{code}_{year}_{form}_*.txt"))
    return hits[0] if hits else None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--codes", default="")
    ap.add_argument("--years", default="")
    ap.add_argument("--daas", default=str(DAAS))
    args = ap.parse_args(argv)

    import psycopg2

    dsn = os.environ.get("FD_OPEN_DATA_DSN")
    if not dsn:
        raise SystemExit("FATAL 缺 FD_OPEN_DATA_DSN（source ~/.config/finddata/dashboards.env）")

    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    fields = mapping["fields"]
    wl = {r["code"]: r for r in json.loads(WHITELIST.read_text(encoding="utf-8"))["a_share"]}
    codes = {c.strip() for c in args.codes.split(",") if c.strip()} or set(wl)
    years = {int(y) for y in args.years.split(",") if y.strip()}

    con = sqlite3.connect(args.daas)
    rows = con.execute("""
        select stock_code, year, raw_path, parse_status, report_id, source
        from report_documents where parse_status = 'ok'
    """).fetchall()

    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute("select id, code, verified from concepts where code = any(%s)",
                ([f["concept"] for f in fields],))
    rows_c = cur.fetchall()
    concept_id = {code: cid for cid, code, _v in rows_c}
    # **命名门（硬门）**：未 verified 的概念一律不写。spec 6.2 要求「未过门指标读面不可见」，
    # 生产端就不该往里灌数——灌进去再靠读面过滤，等于把脏数留在库里等人误用。
    unverified = [code for _i, code, v in rows_c if not v]
    if unverified:
        raise SystemExit(f"FATAL 命名门未过，拒绝写入这些概念：{unverified}")
    missing_c = [f["concept"] for f in fields if f["concept"] not in concept_id]
    if missing_c:
        raise SystemExit(f"FATAL 概念未在册，拒绝写入：{missing_c}")
    cur.execute("select code, id from entities where entity_type = 'stock' and code = any(%s)",
                (list(codes),))
    entity_id = dict(cur.fetchall())

    stats = Counter()
    payload: list[dict] = []
    skipped: list[dict] = []
    for code, year, raw_path, _st, report_id, source in rows:
        if code not in codes or (years and int(year) not in years):
            continue
        if code not in entity_id:
            stats["no_entity"] += 1
            continue
        # 报告类型从 report_id/raw_path 里认（daas 表没有独立类型列）
        blob = f"{raw_path or ''} {source or ''}"
        # **长词优先**：「半年度报告」包含「年度报告」子串，按 dict 顺序取会把半年报判成年报
        # （实测 002714 一年四份报告被全判成年度报告，同一条观测重复登记 2 次）。
        form = next((f for f in sorted(PERIOD, key=len, reverse=True) if f in blob), None)
        if form is None:
            # raw_path 可能为空（早前一轮 --drop-pdf 把 PDF 删了，只留文本缓存），
            # 此时从缓存文件名补判类型（文件名里带「年度报告/半年度报告/…」）。
            for cand in sorted(CACHE.glob(f"{code}_{year}_*.txt")):
                hit = next((f for f in sorted(PERIOD, key=len, reverse=True)
                            if f in cand.name), None)
                if hit:
                    form = hit
                    break
        if form is None:
            stats["no_form"] += 1
            skipped.append({"code": code, "year": year, "why": "无法从路径判定报告类型"})
            continue
        path = _text_of(raw_path, code, int(year), form)
        if path is None:
            stats["no_text"] += 1
            skipped.append({"code": code, "year": year, "form": form, "why": "缺文本缓存"})
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if _garbled(text):
            stats["text_unreadable"] += 1
            skipped.append({"code": code, "year": year, "form": form,
                            "why": "主要会计数据段中文标签乱码（字体缺 ToUnicode），不按位置猜"})
            continue
        vals, why = _extract(text, fields)
        date, gran = PERIOD[form]
        period = date.format(y=int(year))
        for concept, (value, unit) in vals.items():
            payload.append({"entity_id": entity_id[code], "concept_id": concept_id[concept],
                            "date": period, "granularity": gran, "value": value,
                            "unit": unit, "code": code, "concept": concept})
        stats["docs_ok"] += 1
        if why:
            skipped.append({"code": code, "year": year, "form": form, "partial": why})

    # 幂等：uq_sem_obs 冲突即跳过（不改已有值——改值属另一件事，须显式人工确认）
    inserted = 0
    if args.apply and payload:
        for r in payload:
            cur.execute("""
                insert into semantic_observations
                  (concept_id, entity_type, entity_id, date, granularity, value, unit,
                   source_used, fetched_at)
                values (%s, 'stock', %s, %s, %s, %s, %s, %s, now())
                on conflict on constraint uq_sem_obs do nothing
            """, (r["concept_id"], r["entity_id"], r["date"], r["granularity"],
                  r["value"], r["unit"], SOURCE))
            inserted += cur.rowcount
        conn.commit()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "cnreport-obs-registration.json"
    out.write_text(json.dumps({
        "generated_for": "awaken-agriculture-report-ingest 6.2",
        "source_used": SOURCE,
        "mapping": str(MAPPING.name),
        "applied": args.apply,
        "planned_rows": len(payload),
        "inserted_rows": inserted,
        "stats": dict(stats),
        "skipped": skipped,
        "sample": payload[:10],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"文档处理 {stats['docs_ok']} 份 · 计划写入 {len(payload)} 条 · 实际新增 {inserted} 条")
    print("跳过统计:", dict(stats))
    if skipped:
        print("跳过明细（前 8 条）:")
        for s in skipped[:8]:
            print("  -", json.dumps(s, ensure_ascii=False)[:150])
    print("写出", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
