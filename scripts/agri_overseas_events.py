#!/usr/bin/env python3
"""港美「事件面」车道（awaken-agriculture-report-ingest W6 / 7.1-7.3）。

口径（决策记录 2026-11-08）：港美**仅事件面** —— 披露日期 / 公告类型 / 报告期 /
头条营收净利；**不做 XBRL 或其他结构化财务细分**。

两个市场的官方事件源：

* **美股 = SEC EDGAR submissions**（`data.sec.gov/submissions/CIK##########.json`）。
  取 `filings.recent` 的 `form` / `filingDate` / `reportDate` / `items` / `accessionNumber`。
  **绝不调用 `companyfacts` / `companyconcept`（XBRL facts）** —— 那是结构化财务细分，
  属被排除的口径。代码里没有这两个端点，`--check` 会 grep 自证。
* **港股 = HKEXnews 标题检索**（`www1.hkexnews.hk/search/titleSearchServlet.do`）。
  取 `DATE_TIME` / `TITLE` / `LONG_TEXT`（公告类型）/ `FILE_LINK` / `STOCK_CODE`。

**头条数字（营收/净利）取自公告原文，不做 XBRL**：

* 港股 → 最近一份「業績公告」PDF 的「綜合損益表」表体行，单位按表头（人民幣千元 等）换算；
  只认表体，不认摘要里的四舍五入叙述值。
* 美股 → 最近一次 8-K item 2.02 的 EX-99.1 新闻稿，支持叙述式（`Net sales of $2.96 billion`）
  与表体式（`Net sales $ 539,607` + `in thousands`）两种形态。

抽不到的（如近期没有 8-K 2.02、PDF 无文本层）一律 `headline_status: "not_extracted"` + `reason`，
**不用 XBRL 数冒充头条、也不用行业数顶替**；`--check` 对「出数无证据」「不出数无原因」都判失败。

用法::

    python3 scripts/agri_overseas_events.py            # 两个市场各取最近事件
    python3 scripts/agri_overseas_events.py --check     # 只校验已有产物结构
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data import pdftext  # noqa: E402  （港股公告是 PDF，取文本层，不做 OCR）

FACE = REPO / "docs" / "agri-overseas-event-face.json"
OUT = REPO / "docs" / "agri-overseas-events.json"
UA = "finddata-research contact@finddatatech.cloud"
SEC_SUB = "https://data.sec.gov/submissions/CIK{cik}.json"
HKEX_SEARCH = "https://www1.hkexnews.hk/search/titleSearchServlet.do"
HKEX_PREFIX = "https://www1.hkexnews.hk/search/prefix.do"
#: SEC ticker 表在 www.sec.gov（本机直连超时），走 windows-laptop 深圳家宽；data.sec.gov 可直连。
SEC_PROXY = "http://100.64.0.10:7890"

#: 只保留「事件面」关心的表单：定期报告 + 重大事项。
US_FORMS = {"10-K", "10-Q", "20-F", "6-K", "8-K"}
#: HKEX 标题里这些词对应定期/预警类事件。
HK_EVENT_KW = ("年報", "年度報告", "中期報告", "半年", "業績", "盈利警告", "盈警", "虧損", "季度")
#: 只认这两类栏目的公告（通函/上市文件里的「中期報告之發布通知」不是事件本体）。
HK_CATEGORY_OK = ("公告及通告", "財務報表")
#: 表体单位 → 绝对量乘数（公告表头写「人民幣千元」「千港元」等）。
HK_UNIT = {"百萬元": 1e6, "百万元": 1e6, "千港元": 1e3, "萬港元": 1e4,
           "千元": 1e3, "萬元": 1e4, "万元": 1e4, "元": 1.0}
#: 表头币种词 → ISO 码（截前两个字会得到「人民」，那是错的）。
HK_CCY = (("人民幣", "CNY"), ("人民币", "CNY"), ("港幣", "HKD"), ("港元", "HKD"),
          ("美元", "USD"), ("美金", "USD"))
#: 美股叙述式头条的单位词。
US_SCALE = {"billion": 1e9, "million": 1e6, "thousand": 1e3}
#: 千分位整数（用来把表体数字和「12.1」这种指引区间数字区分开）。
_THOU = r"\d{1,3}(?:,\d{3})+"


def _num(tok: str) -> float | None:
    """`(58,615)` → -58615.0；`82,244,944` → 82244944.0；`—` → None。"""
    tok = (tok or "").strip()
    if not tok or tok in {"—", "-", "–"}:
        return None
    neg = tok.startswith("(") and tok.endswith(")")
    digits = re.sub(r"[^\d.]", "", tok)
    if not digits:
        return None
    val = float(digits)
    return -val if neg else val


def _us_text(raw: bytes) -> str:
    """HTML → 扁平纯文本（保留一句一行不重要，正则都带边界）。"""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text))


def _fetch(url: str, *, proxy: str | None = None, timeout: int = 45,
           limit: int | None = None) -> bytes:
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json, text/html;q=0.9,*/*;q=0.8"})
    with opener.open(req, timeout=timeout) as resp:
        return resp.read() if limit is None else resp.read(limit)


def us_events(cik: str, limit: int) -> list[dict]:
    doc = json.loads(_fetch(SEC_SUB.format(cik=cik)))
    recent = (doc.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    periods = recent.get("reportDate") or []
    items = recent.get("items") or []
    accs = recent.get("accessionNumber") or []
    out = []
    for form, filed, period, item, acc in zip(forms, dates, periods, items, accs):
        if form not in US_FORMS:
            continue
        out.append({
            "market": "US", "cik": cik, "name": doc.get("name"),
            "form": form, "filed": filed, "report_period": period or None,
            "items": [x.strip() for x in (item or "").split(",") if x.strip()],
            # 8-K 的 2.02 = Results of Operations（业绩发布），是事件面里最有信息量的一类
            "event_kind": "earnings_release" if (form == "8-K" and "2.02" in (item or ""))
                          else ("periodic_report" if form in ("10-K", "10-Q", "20-F") else "other"),
            "accession": acc,
            "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}",
            "headline": None,
            "headline_status": "not_extracted",
        })
        if len(out) >= limit:
            break
    return out


def _us_figures(text: str) -> dict | None:
    """从业绩新闻稿正文取头条营收/净利。

    两种真实形态（都实测过，别只按一种写）：
      * 叙述式：``Net sales of $2.96 billion``（HRL）
      * 表格式：``Net sales $ 539,607 $ 922,602``，单位在 ``in thousands``（CALM）

    三条防伪约束：
      ① 表格形态只认**千分位整数**（``12.1`` 这种指引区间数字不匹配，避免把 guidance 当实际）；
      ② 叙述式要求 ``net sales of $`` 紧邻，``Expects net sales to be in the range of $12.1 billion`` 不匹配；
      ③ 括号数字是负数（``(58,615)`` → -58615），不取绝对值。
    """
    rev = rev_basis = None
    m = re.search(r"(?i)net sales of \$([\d.]+)\s*(billion|million|thousand)", text)
    if m:
        rev = float(m.group(1)) * US_SCALE[m.group(2).lower()]
        rev_basis = f"新闻稿叙述式：{m.group(0)}"
    else:
        m = re.search(rf"(?i)net sales\s+\$\s*({_THOU})\s+\$?\s*({_THOU})", text)
        if m:
            scale_word = re.search(r"(?i)in (thousands|millions)", text)
            mult = US_SCALE[scale_word.group(1).lower().rstrip("s")] if scale_word else None
            if mult:
                rev = _num(m.group(1)) * mult
                rev_basis = f"新闻稿表格（单位 in {scale_word.group(1)}）：{m.group(0)}"
    ni = ni_basis = None
    for pat in (r"(?i)net (?:earnings|income) of \$([\d.]+)\s*(billion|million|thousand)",
                r"(?i)net (?:earnings|income) (?:were|was|totaled) \$([\d.]+)\s*(billion|million|thousand)"):
        m = re.search(pat, text)
        if m:
            ni = float(m.group(1)) * US_SCALE[m.group(2).lower()]
            ni_basis = f"新闻稿叙述式：{m.group(0)}"
            break
    if ni is None:
        m = re.search(rf"(?i)net (?:earnings|income|income \(loss\))[^$(]{{0,70}}\$\s*({_THOU}|\({_THOU}\))"
                      rf"\s+\$?\s*({_THOU}|\({_THOU}\))", text)
        if m:
            scale_word = re.search(r"(?i)in (thousands|millions)", text)
            mult = US_SCALE[scale_word.group(1).lower().rstrip("s")] if scale_word else None
            if mult:
                ni = _num(m.group(1)) * mult
                ni_basis = f"新闻稿表格（单位 in {scale_word.group(1)}）：{m.group(0)}"
    if rev is None and ni is None:
        return None
    return {"revenue": rev, "revenue_basis": rev_basis, "net_income": ni,
            "net_income_basis": ni_basis, "currency": "USD",
            "unit_note": "USD 绝对量（叙述式按 billion/million 换算，表格按 in thousands/millions 换算）"}


def us_headline(cik: str) -> dict:
    """美股头条 = 最近一次 8-K item 2.02（业绩发布）的新闻稿正文。

    附件名不可预测（实测 ``exhibit991.htm`` / ``hormelearningsreleaseq32026.htm``），
    故先用申报的 SGML 头认出主文档，再剔除主文档/index/R*.htm，最后按名字优先挑 EX-99。
    """
    doc = json.loads(_fetch(SEC_SUB.format(cik=cik)))
    recent = (doc.get("filings") or {}).get("recent") or {}
    hits = [(fld, it, acc) for form, fld, it, acc in zip(
        recent.get("form") or [], recent.get("filingDate") or [],
        recent.get("items") or [], recent.get("accessionNumber") or [])
        if form == "8-K" and "2.02" in (it or "")]
    if not hits:
        return {"market": "US", "cik": cik, "name": doc.get("name"), "headline": None,
                "headline_status": "not_extracted",
                "reason": "近期无 8-K item 2.02（业绩发布）；月报/季报正文不取头条，避免与表体口径混淆"}
    # 只试最近两次业绩发布：再往前翻会把十年前的老申报也拉一遍（实测 BRID 翻到 2008 年），
    # 既慢又把「最近一次没有」这一事实糊成一大堆文件名。
    tried = []
    for filed, _items, acc in hits[:2]:
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}"
        head = _fetch(f"{base}/{acc}.txt", proxy=SEC_PROXY, limit=6000).decode("utf-8", "replace")
        prim = re.search(r"<TYPE>\s*[A-Z0-9\-/]+\s*[\s\S]{0,300}?<FILENAME>\s*([^\n<]+)", head)
        primary = prim.group(1).strip() if prim else None
        names = [i["name"] for i in json.loads(
            _fetch(base + "/index.json", proxy=SEC_PROXY))["directory"]["item"]]
        cand = [n for n in names
                if n.lower().endswith((".htm", ".html")) and n != primary
                and "-index" not in n and not re.fullmatch(r"R\d+\.htm", n, re.I)]
        cand.sort(key=lambda n: (0 if re.search(r"ex.?99|exhibit|earnings|release|press", n, re.I) else 1, n))
        for name in cand[:3]:
            tried.append(f"{filed}:{name}")
            got = _us_figures(_us_text(_fetch(f"{base}/{name}", proxy=SEC_PROXY)))
            if got:
                return {"market": "US", "cik": cik, "name": doc.get("name"), "form": "8-K",
                        "filed": filed, "accession": acc, "doc": name,
                        "doc_url": f"{base}/{name}", "headline_status": "extracted", **got}
    return {"market": "US", "cik": cik, "name": doc.get("name"), "headline": None,
            "headline_status": "not_extracted",
            "reason": f"最近一次 8-K 2.02（{hits[0][0]}）的 EX-99 正文未找到可核对句式（试过 {tried}）"}


def hk_stock_id(stock_code: str) -> int | None:
    """5 位港股代码 → HKEX 内部 stockId。

    **不能把代码当 stockId**（实测 01431 的 stockId 是 96540，2319 是 7578）：
    两者只在少数公司上碰巧相同，直接把代码填进去会让大部分公司检索结果为 0。
    """
    query = urllib.parse.urlencode({"callback": "cb", "lang": "ZH", "type": "A",
                                    "name": stock_code, "market": "SEHK"})
    text = _fetch(f"{HKEX_PREFIX}?{query}").decode("utf-8", "replace").strip()
    # 服务端固定用 JSONP 包一层 `callback(...)`，不理会请求里的 callback 名。
    m = re.search(r"^[A-Za-z_$][\w$]*\((.*)\)\s*;?\s*$", text, re.S)
    if not m:
        return None
    doc = json.loads(m.group(1))
    for row in doc.get("stockInfo") or []:
        if str(row.get("code") or "") == stock_code:
            return int(row["stockId"])
    return None


def _hk_rows(stock_code: str, from_date: str, to_date: str, row_range: int) -> list[dict]:
    """披露易标题检索（**不钉类目**）。

    实测教训：加上 ``t1code=40000 / t2Gcode=-2 / t2code=40100`` 后，蒙牛 2025–2026
    只剩 2 行（年報 + 中期報告），**「全年業績公告」「中期業績公佈」整类被滤掉**——
    那正是头条数字所在的文件。故只按 stockId + 日期取全量，再在本地筛。
    """
    sid = hk_stock_id(stock_code)
    if sid is None:
        raise RuntimeError(f"HKEX 查不到 {stock_code} 的 stockId")
    query = urllib.parse.urlencode({
        "sortDir": "0", "sortByOptions": "DateTime", "category": "0", "market": "SEHK",
        "stockId": str(sid), "documentType": "-1",
        "fromDate": from_date, "toDate": to_date, "title": "", "searchType": "1",
        "rowRange": str(row_range), "lang": "zh",
    })
    raw = json.loads(_fetch(f"{HKEX_SEARCH}?{query}"))
    rows = json.loads(raw.get("result") or "[]")
    out = []
    for row in rows:
        title = html.unescape(re.sub(r"<[^>]+>", "", row.get("TITLE") or "")).strip()
        long_text = html.unescape(re.sub(r"<[^>]+>", "", row.get("LONG_TEXT") or "")).strip()
        if "通函" in long_text or not any(k in long_text for k in HK_CATEGORY_OK):
            continue
        out.append({
            "stock_code": row.get("STOCK_CODE"), "name": row.get("STOCK_NAME"),
            "title": title, "category_text": long_text, "disclosed": row.get("DATE_TIME"),
            "url": ("https://www1.hkexnews.hk" + row["FILE_LINK"]) if row.get("FILE_LINK") else None,
            "news_id": row.get("NEWS_ID"),
        })
    return out


def hk_events(stock_code: str, limit: int, *, from_date="20240101", to_date="20261231") -> list[dict]:
    out = []
    for row in _hk_rows(stock_code, from_date, to_date, max(60, limit * 10)):
        text = row["title"] + row["category_text"]
        if not any(k in text for k in HK_EVENT_KW):
            continue
        out.append({
            **row,
            "market": "HK",
            "event_kind": ("profit_warning" if ("盈" in text and "警" in text) or "虧損" in text
                           else ("periodic_report" if any(k in row["title"] for k in ("年報", "年度報告", "中期報告", "業績"))
                                 else "other")),
            "headline": None,
            "headline_status": "not_extracted",
        })
        if len(out) >= limit:
            break
    return out


def _hk_figures(text: str) -> dict | None:
    """从业绩公告 PDF 文本层取头条营收/归母净利（只认**表体**，不认摘要里的四舍五入值）。

    实测三种真实形态（都来自官方公告，别只按一种写）：

    * 蒙牛 2025 全年（损益表 + 归母块）::

        附註    人民幣千元          人民幣千元
        收入                         4     82,244,944     88,674,774
        歸屬於：
         本公司權益股東                         1,545,350        104,507

    * 原生態牧業 2026 中期：标题是「中期簡明綜合損益**及其他全面收益**表」，
      营收科目叫「收益」不是「收入」，归母块是「本公司擁有人」；
    * 環球玉米 2026 中期：**没有归母块**（无少数股东权益），只有「本期間虧損」，
      此时损益即归母，但 basis 要写明是替代口径。

    两个坑（第一版就栽在这）：
      ① 表名不是「綜合損益表」——「簡明綜合損益及其他全面收益表」也得认；
      ② 归母行缩进可达 25 个空格，`[^\\d]{0,10}` 会漏，放宽到 40。
    """
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if "損益" in ln and "表" in ln]
    if not starts:
        starts = [i for i, ln in enumerate(lines) if "收益表" in ln]
    for start in starts:
        unit_word = unit_mult = None
        for ln in lines[max(0, start - 6): start + 25]:
            m = re.search(r"(?:人民幣|人民币|港幣|港元|美元|美金)?\s*"
                          r"(百萬元|百万元|千港元|萬港元|千元|萬元|万元)", ln)
            if m:
                unit_word = m.group(0).strip()
                unit_mult = HK_UNIT[m.group(1)]
                break
        rev = rev_ev = None
        for ln in lines[start: start + 45]:
            m = re.match(rf"^\s*(?:收入|營業收入|營業額|收益)\s+(?:\d+\s+)?(\(?{_THOU}\)?)\s+(\(?{_THOU}\)?)", ln)
            if m:
                rev, rev_ev = _num(m.group(1)), ln.strip()
                break
        ni = ni_ev = ni_basis = None
        for ln in lines[start: start + 90]:
            m = re.match(rf"^\s*(?:本公司權益股東|本公司擁有人|母公司擁有人|本公司股東|權益持有人)"
                         rf"[^\d\n(]{{0,40}}(\(?{_THOU}\)?)\s+(\(?{_THOU}\)?)", ln)
            if m:
                ni, ni_ev, ni_basis = _num(m.group(1)), ln.strip(), "归母行（本公司权益股东/拥有人）"
                break
        if ni is None:
            for ln in lines[start: start + 90]:
                m = re.match(rf"^\s*(?:本期|期內|本年|年度|本期間)(?:間)?(?:溢利|虧損|利润|亏损)"
                             rf"[^\d\n(]{{0,40}}(\(?{_THOU}\)?)\s+(\(?{_THOU}\)?)", ln)
                if m:
                    ni, ni_ev = _num(m.group(1)), ln.strip()
                    ni_basis = "无归母块（无少数股东权益），取期内溢利/亏损替代"
                    break
        if rev is None and ni is None:
            continue
        mult = unit_mult or 1.0
        ccy = next((c for k, c in HK_CCY if unit_word and k in unit_word), None)
        out = {"revenue": rev * mult if rev is not None else None,
               "net_income": ni * mult if ni is not None else None,
               "currency": ccy,
               "net_income_basis": ni_basis,
               "unit_note": f"公告表体原值 × {mult:g}（表头单位：{unit_word or '未识别'}）",
               "revenue_evidence": rev_ev, "net_income_evidence": ni_ev}
        if out["revenue"] is None:
            out["revenue_missing_reason"] = "该公告损益表里未找到「收入/收益/營業額」行"
        if out["net_income"] is None:
            out["net_income_missing_reason"] = "该公告损益表里既无归母行也无期内溢利/亏损行"
        return out
    return None


def hk_headline(stock_code: str, *, from_date="20240101", to_date="20261231") -> dict:
    """港股头条 = 最近一份「業績公告」PDF 的损益表（不是年报正文、不是摘要叙述）。"""
    rows = _hk_rows(stock_code, from_date, to_date, 100)
    tried = []
    for row in rows:
        title = row["title"]
        if "業績" not in title + row["category_text"]:
            continue
        if ("年報" in title or "中期報告" in title) and "業績" not in title:
            continue
        if not row["url"]:
            continue
        tried.append(title[:30])
        try:
            payload = _fetch(row["url"], timeout=90)
        except Exception as exc:  # noqa: BLE001
            tried[-1] += f"（下载失败 {type(exc).__name__}）"
            continue
        if not pdftext.is_pdf(payload):
            tried[-1] += "（非 PDF）"
            continue
        try:
            text = pdftext.extract_text(payload)
        except pdftext.PdfTextError:
            tried[-1] += "（无文本层，不 OCR）"
            continue
        got = _hk_figures(text)
        if got:
            return {"market": "HK", "stock_code": row["stock_code"], "name": row["name"],
                    "title": title, "disclosed": row["disclosed"], "doc_url": row["url"],
                    "headline_status": "extracted", **got}
    if not tried:
        return {"market": "HK", "stock_code": stock_code, "headline": None,
                "headline_status": "not_extracted",
                "reason": "该窗口内无「業績」公告（只有年報/中期報告正文，不取头条）"}
    return {"market": "HK", "stock_code": stock_code, "headline": None,
            "headline_status": "not_extracted",
            "reason": f"业绩公告里未抽出损益表行（试过 {tried}）"}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--face", default=str(FACE))
    ap.add_argument("--per-market", type=int, default=3, help="每个市场取几家（默认 3）")
    ap.add_argument("--events", type=int, default=5, help="每家取几条事件")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    if args.check:
        src = Path(args.out)
        if not src.is_file():
            raise SystemExit(f"FATAL 缺产物 {src}")
        doc = json.loads(src.read_text(encoding="utf-8"))
        problems = []
        for ev in doc.get("us_events") or []:
            if not re.fullmatch(r"\d{10}", ev.get("cik") or ""):
                problems.append(f"US CIK 非 10 位：{ev.get('cik')}")
            if not ev.get("filed"):
                problems.append("US 事件缺披露日")
        for ev in doc.get("hk_events") or []:
            if not re.fullmatch(r"\d{5}", str(ev.get("stock_code") or "")):
                problems.append(f"HK 代码非 5 位：{ev.get('stock_code')}")
            if not ev.get("disclosed"):
                problems.append("HK 事件缺披露时间")
        # 头条 KPI：出数就必须带证据行（原文片段），不出数必须写明原因——
        # 两条都不满足的就是「静默缺值」，等于让板面显示空值或 0。
        kpi = doc.get("headline_kpi") or {}
        for lane in ("us", "hk"):
            for h in kpi.get(lane) or []:
                who = h.get("ticker") or h.get("stock_code")
                if h.get("headline_status") == "extracted":
                    if not (h.get("revenue") is not None or h.get("net_income") is not None):
                        problems.append(f"{lane} {who} 标 extracted 但两个数都没有")
                    if not (h.get("revenue_evidence") or h.get("net_income_evidence")
                            or h.get("revenue_basis") or h.get("net_income_basis")):
                        problems.append(f"{lane} {who} 出数但无原文证据")
                    for f in ("revenue", "net_income"):
                        v = h.get(f)
                        if isinstance(v, (int, float)) and v == 0:
                            problems.append(f"{lane} {who} {f}=0（0 在财报里几乎总是解析失败，不放过）")
                    if not h.get("doc_url"):
                        problems.append(f"{lane} {who} 出数但无原文链接")
                    # 半抽也要留痕：出了营收缺净利，必须写明为什么缺，否则板面会显示成 0。
                    for f in ("revenue", "net_income"):
                        if h.get(f) is None and not h.get(f + "_missing_reason"):
                            problems.append(f"{lane} {who} 缺 {f} 且未写 missing_reason")
                elif h.get("headline_status") == "not_extracted":
                    if not h.get("reason"):
                        problems.append(f"{lane} {who} not_extracted 但未写原因")
                else:
                    problems.append(f"{lane} {who} headline_status 非法：{h.get('headline_status')}")
        # 口径自证：产物里不得出现 XBRL facts 端点；代码侧只扫**可执行行**
        # （注释与 docstring 里提到这两个词是为了说明「不调用」，不该算违规——
        #  第一版把整文件拼进去，结果被自己的说明文字判失败）。
        # 口径自证只看**真实信号**，不看文字：事件 URL 不得指向 XBRL 接口，
        # 可执行代码里不得出现 `api/xbrl` 路径。
        # （第一版拿 “companyfacts”/“companyconcept” 两个词 grep 整份文件，结果被我自己
        #  「不调用这两个端点」的说明文字判失败——自证要判行为，不是判措辞。）
        # 只看**请求行**：带 http/https 或 SEC_SUB 的赋值/调用行里出现 XBRL 接口才算违规。
        # 自证代码本身、说明文字、变量名都不算（第一版把整份文件拼进去，被自己的
        # 说明文字和自证语句判失败——判行为，不判措辞）。
        marker = "api/" + "xbrl"          # 拆开写，避免本行自己被匹配到
        for ev in (doc.get("us_events") or []) + (doc.get("hk_events") or []):
            if marker in (ev.get("url") or ""):
                problems.append(f"事件 URL 指向 XBRL 接口：{ev.get('url')}")
        body = re.sub(r'""".*?"""', "", Path(__file__).read_text(encoding="utf-8"), flags=re.S)
        for ln in body.splitlines():
            stripped = ln.strip()
            if stripped.startswith("#") or "marker" in ln:
                continue
            if marker in ln and ("http" in ln or "SEC_SUB" in ln or "_fetch(" in ln):
                problems.append(f"代码里出现 XBRL 请求：{stripped[:70]}")
        if problems:
            print("CHECK FAIL")
            for p in problems[:10]:
                print("  ✗", p)
            return 1
        print(f"CHECK PASS：US {len(doc.get('us_events') or [])} 条 · "
              f"HK {len(doc.get('hk_events') or [])} 条 · 无 XBRL facts 调用")
        return 0

    face = json.loads(Path(args.face).read_text(encoding="utf-8"))
    us_targets = (face.get("us_event_face") or {}).get("companies") or []
    hk_targets = (face.get("hk_event_face") or {}).get("stocks") or []

    us_events_all, hk_events_all, problems = [], [], []
    kpi_us, kpi_hk = [], []
    for c in us_targets[: args.per_market]:
        try:
            evs = us_events(c["cik"], args.events)
            us_events_all.extend(evs)
            print(f"  ✓ US {c['ticker']:<6} CIK {c['cik']}：{len(evs)} 条事件")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"US {c.get('ticker')}: {type(exc).__name__}: {str(exc)[:70]}")
            print(f"  ✗ US {c.get('ticker')}: {type(exc).__name__}")
            time.sleep(0.2)
            continue
        try:
            hl = us_headline(c["cik"])
            hl.setdefault("ticker", c["ticker"])
            kpi_us.append(hl)
            mark = "头条 ✓" if hl.get("headline_status") == "extracted" else "头条 —"
            print(f"    {mark} {c['ticker']}")
        except Exception as exc:  # noqa: BLE001
            kpi_us.append({"market": "US", "ticker": c["ticker"], "cik": c["cik"],
                           "headline": None, "headline_status": "not_extracted",
                           "reason": f"{type(exc).__name__}: {str(exc)[:70]}"})
            print(f"    头条 ✗ {c['ticker']}: {type(exc).__name__}")
        time.sleep(0.2)
    for s in hk_targets[: args.per_market]:
        try:
            evs = hk_events(s["code"], args.events)
            hk_events_all.extend(evs)
            print(f"  ✓ HK {s['code']} {s['name'][:12]:<14}：{len(evs)} 条事件")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"HK {s.get('code')}: {type(exc).__name__}: {str(exc)[:70]}")
            print(f"  ✗ HK {s.get('code')}: {type(exc).__name__}")
            time.sleep(0.4)
            continue
        try:
            hl = hk_headline(s["code"])
            hl.setdefault("name", s["name"])
            kpi_hk.append(hl)
            mark = "头条 ✓" if hl.get("headline_status") == "extracted" else "头条 —"
            print(f"    {mark} {s['code']} {s['name'][:12]}")
        except Exception as exc:  # noqa: BLE001
            kpi_hk.append({"market": "HK", "stock_code": s["code"], "name": s["name"],
                           "headline": None, "headline_status": "not_extracted",
                           "reason": f"{type(exc).__name__}: {str(exc)[:70]}"})
            print(f"    头条 ✗ {s['code']}: {type(exc).__name__}")
        time.sleep(0.4)

    doc = {
        "generated_for": "awaken-agriculture-report-ingest W6 事件面（7.1-7.3）",
        "date": "2026-11-08",
        "caliber": {
            "scope": "事件面：披露日期 / 公告类型 / 报告期 / 头条营收净利",
            "not_done": "不做 XBRL 或其他结构化财务细分；SEC 的 companyfacts/companyconcept 一律不调用",
            "headline": "头条取自**公告原文**：港股取业绩公告 PDF 的「綜合損益表」表体行"
                        "（表头单位换算，空值不填 0）；美股取 8-K item 2.02 的 EX-99.1 新闻稿"
                        "（叙述式 $X billion 或表体 + in thousands）。抽不到就 headline_status=not_extracted"
                        "并写明原因，**不用 XBRL 数、行业数或四舍五入的摘要值顶替**",
            "sources": {"us": "SEC EDGAR submissions + 8-K EX-99.1 正文（官方，data/www.sec.gov）",
                        "hk": "HKEXnews 标题检索 + 业绩公告 PDF 文本层（官方，www1.hkexnews.hk）"},
        },
        "board_contract": {
            "shape": "事件流（每行 = 一条披露事件）+ 头条 KPI（每家最近一期）",
            "fields_for_board": ["market", "name", "event_kind", "disclosed|filed",
                                 "report_period", "url", "headline"],
            "kpi_fields_for_board": ["revenue", "net_income", "currency", "unit_note",
                                     "revenue_evidence|net_income_evidence"],
            "unavailable_rule": "headline 为 null 且 headline_status=not_extracted 时，"
                                "板面必须显示「头条未抽取」而不是空值或 0",
        },
        "headline_kpi": {"us": kpi_us, "hk": kpi_hk},
        "us_events": us_events_all,
        "hk_events": hk_events_all,
        "problems": problems,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    got_us = sum(1 for h in kpi_us if h.get("headline_status") == "extracted")
    got_hk = sum(1 for h in kpi_hk if h.get("headline_status") == "extracted")
    print(f"\nUS {len(us_events_all)} 条 · HK {len(hk_events_all)} 条 · 失败 {len(problems)} · 写出 {out}")
    print(f"头条 KPI：US {got_us}/{len(kpi_us)} 家 · HK {got_hk}/{len(kpi_hk)} 家（其余 not_extracted 带原因）")
    return 0 if (us_events_all or hk_events_all) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
