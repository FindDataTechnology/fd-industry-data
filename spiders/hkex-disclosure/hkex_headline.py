"""hkex_headline — 港股业绩公告「营收 / 归母利润」头条数字的最小化严格抽取。

本模块只做一件事：从 HKEXnews 业绩公告 PDF 的**文本层**里，按固定标签行取
**两个**头条数字，并连同报告期、币种、单位、原文行一起返回。它不是「PDF 全文
解析」——不做版面重建、不做表格推断、不做 OCR，也不猜任何标签。

口径（与 ``docs/hkex-results-headline-caliber.md`` 一致，改动必须同步）：

* **只认显式标签行**，标签必须与本模块的 ``REVENUE_LABELS`` /
  ``PROFIT_LABELS`` 逐字（规范化后）相等；同义词不扩写、不模糊匹配。
* **利润取「归属于母公司」口径**（``Profit attributable to owners of the
  Company`` / ``... equity shareholders of the Company`` / ``... owners of the
  parent``）。**不取** ``Profit for the period``（含少数股东权益，与美股
  XBRL ``NetIncomeLoss`` 口径不同）。
* **报告期来自事件标题**（``for the six months ended June 30, 2026`` /
  ``for the year ended 31 March 2026``），并要求该期末日期在 PDF 正文中真实
  出现；标题未写报告期 → 不可得（年报/中报正文类标题不猜期）。
* **单位来自数字表头**（``RMB'000`` / ``HK$'000`` / ``(in US$ millions)``），
  只认 000/thousand/million 三档；币种只认 RMB/HK$/US$。金额换算成**原始
  币种单位**的整数（千元 → ×1000，百万 → ×1,000,000），不做汇率换算。
* **列序必须是本期在前**：数字行上方 8 行内必须能找到 ``2026  2025`` 或
  ``6/30/2026  6/30/2025`` 这样的双期表头，且第一期年份 = 报告期年份。
* **任何歧义 → 不可得**：同一概念出现两个不同值、营收与利润不在同一张表
  （同一页同一单位）、币种/单位冲突、标签只命中一次但单位找不到——一律返回
  ``status='unavailable'`` 并给出原因码，绝不挑一个「看起来对」的数。
"""
from __future__ import annotations

import re

__all__ = [
    "STATUS_OK",
    "STATUS_UNAVAILABLE",
    "STATUS_NOT_APPLICABLE",
    "REVENUE_LABELS",
    "PROFIT_LABELS",
    "parse_title_period",
    "extract_headline",
]

STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"
STATUS_NOT_APPLICABLE = "not_applicable"

# ── 标签白名单（规范化后逐字相等；规范化 = casefold + 去标点 + 折叠空白）───
REVENUE_LABELS = frozenset({
    "revenue",
    "revenues",
    "total revenue",
    "total revenues",
    "turnover",            # 港股传统写法；本批 10 家未触发，留作显式支持
    "total turnover",
})

PROFIT_LABELS = frozenset({
    # 归母利润（英式 IFRS 写法）
    "profit attributable to owners of the company",
    "profit for the period attributable to owners of the company",
    "profit for the year attributable to owners of the company",
    "profit for the period attributable to owners of the parent",
    "profit for the year attributable to owners of the parent",
    "profit attributable to equity shareholders",
    "profit attributable to shareholders",
    "profit for the period attributable to equity shareholders of the company",
    "profit for the year attributable to equity shareholders of the company",
    "profit attributable to equity holders of the company",
    "profit for the period attributable to equity holders of the company",
    "profit for the year attributable to equity holders of the company",
    # 「Profit attributable to: / Equity holders of the Company」两行式
    "profit attributable to equity holders of the company",
    "profit attributable to equity shareholders of the company",
    # 「Attributable to: / Equity shareholders of the Company」两行式
    "attributable to equity shareholders of the company",
    "attributable to equity holders of the company",
    # 亏损期同构写法（loss 替代 profit，仅标签差异，口径同为归母）
    "loss attributable to owners of the company",
    "loss for the period attributable to owners of the company",
    "loss for the year attributable to owners of the company",
    "loss for the period attributable to owners of the parent",
    "loss for the year attributable to owners of the parent",
    "loss attributable to equity shareholders",
    "loss attributable to shareholders",
    "loss for the period attributable to equity shareholders of the company",
    "loss for the year attributable to equity shareholders of the company",
    "loss attributable to equity holders of the company",
    "loss for the period attributable to equity holders of the company",
    "loss for the year attributable to equity holders of the company",
    # 中美准则写法（09987 百胜中国，美股口径归母 = Net Income – Yum China）
    "net income yum china holdings inc",
})

# 单位：货币 + 量级。000/thousand(s) -> 1e3，million(s) -> 1e6。
_UNIT_RE = re.compile(
    r"(?P<cur>rmb|hk\$|hkd|us\$|usd)\s*(?:['’]\s*)?(?P<mag>000|thousand|thousands|million|millions)\b",
    re.I,
)
_CURRENCY = {"rmb": "CNY", "hk$": "HKD", "hkd": "HKD", "us$": "USD", "usd": "USD"}
_SCALE = {"000": 1000, "thousand": 1000, "thousands": 1000,
          "million": 1000000, "millions": 1000000}

_INT_TOKEN_RE = re.compile(r"^\(?-?[\d,]+\)?$")
_NON_LABEL_RE = re.compile(r"[^a-z0-9 ]+")

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

_DATE_TEXT_RE = re.compile(
    r"(?:(?P<d1>\d{1,2})\s+(?P<m1>[A-Za-z]{3,9})\s*,?\s*(?P<y1>\d{4}))"
    r"|(?:(?P<m2>[A-Za-z]{3,9})\s+(?P<d2>\d{1,2})\s*,?\s*(?P<y2>\d{4}))"
)

_PERIOD_HEAD_RE = re.compile(
    r"\b(?:(?P<kind>three|six|nine)\s+months?|(?P<year>year)|(?P<annual>annual))\s+ended\b",
    re.I,
)
_KIND_MONTHS = {"three": 3, "six": 6, "nine": 9, "year": 12, "annual": 12}


# ── 基础工具 ────────────────────────────────────────────────────────────────

def _norm_label(text: str) -> str:
    s = text.casefold().replace("’", "'").replace("–", "-").replace("—", "-")
    s = _NON_LABEL_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _int_token(tok: str) -> int | None:
    """``1,493,047`` -> int；``(35,990)`` -> -35990；百分比/小数 -> None。"""
    if not _INT_TOKEN_RE.match(tok):
        return None
    body = tok
    neg = body.startswith("(") and body.endswith(")")
    body = body.strip("()").replace(",", "")
    if body.startswith("-"):
        neg = True
        body = body[1:]
    if not body.isdigit():
        return None
    return -int(body) if neg else int(body)


def _parse_date(text: str) -> str | None:
    m = _DATE_TEXT_RE.search(text)
    if not m:
        return None
    if m.group("d1"):
        day, mon, year = m.group("d1"), m.group("m1"), m.group("y1")
    else:
        day, mon, year = m.group("d2"), m.group("m2"), m.group("y2")
    month = _MONTHS.get(mon.casefold()[:9]) or _MONTHS.get(mon.casefold()[:3])
    if not month:
        return None
    try:
        return f"{int(year):04d}-{month:02d}-{int(day):02d}"
    except ValueError:
        return None


def parse_title_period(title: str) -> tuple[int, str, str] | None:
    """标题 -> ``(months, period_end, verbatim_phrase)``；无法判定 -> None。

    只接受 ``(three|six|nine) months ended <date>`` / ``year ended <date>``
    两种显式写法。标题内多处报告期且不一致 -> None（歧义不当证据）。
    """
    if not title:
        return None
    found: list[tuple[int, str, str]] = []
    for m in _PERIOD_HEAD_RE.finditer(title):
        kind = (m.group("kind") or m.group("year") or m.group("annual") or "").casefold()
        months = _KIND_MONTHS.get(kind)
        if months is None:
            continue
        tail = title[m.end(): m.end() + 48]
        end = _parse_date(tail)
        if end is None:
            continue
        found.append((months, end, title[m.start(): m.end() + _DATE_TEXT_RE.search(tail).end()]))
    if not found:
        return None
    if len({(months, end) for months, end, _ in found}) != 1:
        return None
    return found[0]


def _end_date_forms(period_end: str) -> list[str]:
    y, mo, d = (int(x) for x in period_end.split("-"))
    names = [k for k, v in _MONTHS.items() if v == mo and len(k) > 3]
    name = names[0] if names else ""
    forms = [period_end, f"{d}/{mo}/{y}", f"{d:02d}/{mo:02d}/{y}"]
    if name:
        forms += [f"{name} {d}, {y}", f"{d} {name} {y}", f"{name} {d} {y}"]
    return forms


def _period_in_text(text: str, period_end: str) -> bool:
    low = re.sub(r"\s+", " ", text).casefold()
    for form in _end_date_forms(period_end):
        if re.sub(r"\s+", " ", form).casefold() in low:
            return True
    return False


def _empty_result() -> dict:
    return {
        "headline_status": STATUS_NOT_APPLICABLE,
        "headline_reason": None,
        "headline_period_end": None,
        "headline_period_months": None,
        "headline_period_label": None,
        "headline_currency": None,
        "headline_unit": None,
        "headline_revenue": None,
        "headline_net_income": None,
        "headline_evidence": [],
    }


def _fail(out: dict, code: str, detail: str | None = None,
          evidence: list[str] | None = None) -> dict:
    out["headline_status"] = STATUS_UNAVAILABLE
    out["headline_reason"] = code if detail is None else f"{code}: {detail}"
    if evidence:
        out["headline_evidence"] = evidence[:8]
    return out


# ── 行级匹配 ────────────────────────────────────────────────────────────────

def _tokens(line: str) -> list[re.Match]:
    return list(re.finditer(r"\S+", line))


def _split_line(line: str):
    """-> ``(label_text, values)`` or None when the line carries < 2 integers."""
    toks = _tokens(line)
    first = None
    for idx, tok in enumerate(toks):
        if _int_token(tok.group()) is not None:
            first = idx
            break
    if first is None:
        return None
    values = [v for tok in toks[first:]
              if (v := _int_token(tok.group())) is not None]
    if len(values) < 2:
        return None
    # 去掉注号列（"Revenue  3  3,098,831  3,100,070"），注号 <= 99 且无千分位
    if len(values) >= 3 and abs(values[0]) < 100:
        raw_first = toks[first].group()
        if "," not in raw_first and not raw_first.startswith("("):
            values = values[1:]
    return line[: toks[first].start()], values


def _concept_of(label: str) -> str | None:
    norm = _norm_label(label)
    if norm in REVENUE_LABELS:
        return "revenue"
    if norm in PROFIT_LABELS:
        return "profit"
    return None


# 这两条标签本身只是「Attributable to:」的下半句，必须验证上一行就是
# 「Attributable to:」。否则会误命中别的表：
#   「Total comprehensive income for the period / attributable to equity holders ...」
#   「Total equity attributable to / equity shareholders ...」
_CONTINUATION_ONLY = frozenset({
    "attributable to equity shareholders of the company",
    "attributable to equity holders of the company",
})


def _prev_nonempty(page_lines: list[str], i: int) -> str | None:
    for j in range(i - 1, max(-1, i - 4), -1):
        if page_lines[j].strip():
            return page_lines[j]
    return None


def _find_matches(pages: list[tuple[int, list[str]]]) -> list[dict]:
    """Scan every page; return label matches with page/line provenance."""
    out: list[dict] = []
    for page_start, page_lines in pages:
        for i, line in enumerate(page_lines):
            split = _split_line(line)
            if split is None:
                continue
            label_text, values = split
            if _norm_label(label_text) in _CONTINUATION_ONLY:
                prev = _prev_nonempty(page_lines, i)
                if prev is None or _norm_label(prev) != "attributable to":
                    continue
            raw_used = [line]
            concept = _concept_of(label_text)
            # 折行标签：最多回接 2 条非空且无数字的行（跳过空行）
            if concept is None:
                prefix: list[str] = []
                j = i - 1
                steps = 0
                while j >= 0 and len(prefix) < 2 and steps < 4:
                    stripped = page_lines[j].strip()
                    if stripped:
                        if _split_line(page_lines[j]) is not None:
                            break
                        prefix.insert(0, stripped)
                        steps += 1
                        concept = _concept_of(" ".join(prefix + [label_text]))
                        if concept is not None:
                            raw_used = [page_lines[j], line]
                            break
                    j -= 1
            if concept is None:
                continue
            out.append({
                "concept": concept,
                "label": _norm_label(" ".join(raw_used[:-1] + [label_text])),
                "values": values,
                "page": page_start,
                "line_in_page": i,
                "line_no": page_start + i + 1,
                "raw": raw_used,
            })
    return out


def _unit_for_match(page_start: int, page_lines: list[str], i: int):
    """数字行上方同页最近一次单位声明 -> ``(currency, scale, evidence)``."""
    window = page_lines[max(0, i - 45): i]
    joined = " ".join(w.strip() for w in window if w.strip())
    matches = list(_UNIT_RE.finditer(joined))
    if not matches:
        return None
    m = matches[-1]
    cur = _CURRENCY[m.group("cur").casefold()]
    scale = _SCALE[m.group("mag").casefold()]
    # 证据：从最近一行往上找，允许 1-3 行的折行表头（09658 的
    # "(US$ / ’ / 000)" 在 -layout 下被拆成三行）
    evidence = None
    for span in (1, 2, 3):
        for back in range(1, len(window) + 1):
            end = len(window) - back + 1
            start = max(0, end - span)
            cand = " ".join(x.strip() for x in window[start:end] if x.strip())
            if cand and _UNIT_RE.search(cand):
                evidence = cand
                break
        if evidence:
            break
    return cur, scale, evidence or joined[-160:]


def _year_pair_ok(page_lines: list[str], i: int, period_end: str) -> bool:
    """数字行上方 8 行内必须有 ``2026 2025`` / ``6/30/2026 6/30/2025`` 表头."""
    year = int(period_end[:4])
    for back in range(1, 9):
        j = i - back
        if j < 0:
            break
        cand = page_lines[j]
        years = [int(y) for y in re.findall(r"\b(\d{4})\b", cand)]
        if len(years) >= 2:
            return years[0] == year
    return False


# ── 主入口 ──────────────────────────────────────────────────────────────────

def extract_headline(text: str, title: str) -> dict:
    """从公告文本层抽取头条数字；任何不确定都返回不可得（不抛业务异常）。"""
    out = _empty_result()
    if not text or not text.strip():
        return _fail(out, "no_text_layer")
    if not title:
        return _fail(out, "no_title")

    period = parse_title_period(title)
    if period is None:
        return _fail(out, "period_not_stated_in_title")
    months, period_end, phrase = period
    out.update(
        headline_status=STATUS_UNAVAILABLE,
        headline_period_end=period_end,
        headline_period_months=months,
        headline_period_label=phrase,
    )
    if not _period_in_text(text, period_end):
        return _fail(out, "period_end_not_in_document")

    pages: list[tuple[int, list[str]]] = []
    offset = 0
    for chunk in text.split("\f"):
        page_lines = chunk.splitlines()
        pages.append((offset, page_lines))
        offset += len(page_lines)

    matches = _find_matches(pages)
    rev = [m for m in matches if m["concept"] == "revenue"]
    prof = [m for m in matches if m["concept"] == "profit"]
    if not rev:
        return _fail(out, "no_revenue_line")
    if not prof:
        return _fail(out, "no_profit_attributable_line")

    page_by_start = {start: lines for start, lines in pages}
    groups: dict[tuple, dict] = {}
    for m in rev + prof:
        page_lines = page_by_start[m["page"]]
        unit = _unit_for_match(m["page"], page_lines, m["line_in_page"])
        if unit is None:
            continue
        cur, scale, unit_ev = unit
        m["currency"], m["scale"], m["unit_evidence"] = cur, scale, unit_ev
        key = (m["page"], cur, scale)
        g = groups.setdefault(key, {"revenue": [], "profit": []})
        g[m["concept"]].append(m)

    # 列序校验锚在整张表的最上面一行（利润行常在表尾，离双期表头 >8 行）
    valid: list[tuple[tuple, dict]] = []
    year_seen = False
    for key, g in groups.items():
        if not (g["revenue"] and g["profit"]):
            continue
        page_lines = page_by_start[key[0]]
        anchor = min(m["line_in_page"] for m in g["revenue"] + g["profit"])
        if _year_pair_ok(page_lines, anchor, period_end):
            year_seen = True
            valid.append((key, g))
    if not valid:
        if not any(m.get("currency") for m in rev + prof):
            return _fail(out, "unit_not_found",
                         evidence=[m["raw"][-1].strip() for m in (rev + prof)[:4]])
        if not year_seen:
            return _fail(out, "column_header_not_found",
                         evidence=[m["raw"][-1].strip() for m in (rev + prof)[:4]])
        return _fail(out, "revenue_and_profit_not_in_same_table",
                     evidence=[m["raw"][-1].strip() for m in (rev + prof)[:4]])

    units = {(cur, scale) for (_, cur, scale) in (k for k, _ in valid)}
    if len(units) != 1:
        return _fail(out, "conflicting_units",
                     detail=", ".join(f"{c}/{s}" for c, s in sorted(units)))
    currency, scale = units.pop()

    rev_values = {m["values"][0] for _k, g in valid for m in g["revenue"]}
    prof_values = {m["values"][0] for _k, g in valid for m in g["profit"]}
    if len(rev_values) != 1:
        return _fail(out, "ambiguous_revenue",
                     detail="values " + ", ".join(str(v) for v in sorted(rev_values)))
    if len(prof_values) != 1:
        return _fail(out, "ambiguous_profit",
                     detail="values " + ", ".join(str(v) for v in sorted(prof_values)))

    rev_m = next(m for _k, g in valid for m in g["revenue"])
    prof_m = next(m for _k, g in valid for m in g["profit"])
    evidence: list[str] = []
    for m in (rev_m, prof_m):
        evidence.extend(raw.strip() for raw in m["raw"] if raw.strip())
        evidence.append(f"[unit] {m['unit_evidence']}")

    out.update(
        headline_status=STATUS_OK,
        headline_reason=None,
        headline_currency=currency,
        headline_unit="thousand" if scale == 1000 else "million",
        headline_revenue=str(rev_values.pop() * scale),
        headline_net_income=str(prof_values.pop() * scale),
        headline_evidence=evidence,
    )
    return out
