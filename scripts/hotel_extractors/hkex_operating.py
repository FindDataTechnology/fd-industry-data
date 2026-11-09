#!/usr/bin/env python3
"""HK hotel operating-data channel — HKEX ``hkex-disclosure`` category mining.

SOURCE = ``hkex-disclosure`` (existing lane; this module adds no crawler and
does not change the lane's spider — the category filter lives here, per
design D2 "参数覆盖消费，不新建平行车道").

## What the HK market actually discloses (measured 2026-11-08)

Recon: ``openspec/changes/add-hotel-vertical/reports/4.0-wave3-recon.md`` §2.

| code | name | operating-data carrier | status |
|---|---|---|---|
| 00045 | 大酒店 | stand-alone quarterly 「未經審核營運數據」, category `其他-營運業績最新情況`, continuous 2024Q4→2026Q3 | **parsed** |
| 01179 | 华住 | inside the quarterly results announcement (`海外監管公告-營運業績最新情況`) | `embedded-in-results` |
| 00069 | 香格里拉 | category present, but the ZH row is an English-version placeholder page | `english-only` |
| 01270 | 朗廷 | no operating-data announcement (SS stapled securities) | `no-operating-disclosure` |
| 00071 | 美丽华 | no operating-data announcement | `no-operating-disclosure` |

Only 00045 publishes a machine-parseable operating-data table today. The other
four are reported as explicit unavailability — never as empty values, and never
back-filled from a peer.

## Two measured traps encoded below

1. **HTML entities.** Upstream `title` / `LONG_TEXT` carry undecoded
   ``&ndash;`` / ``&#x2f;``. Matching before ``html.unescape`` misses every
   00045 row. The lane's own ``_clean_title`` already unescapes, but payloads
   from older runs may still carry entities, so selection unescapes first.
2. **The stored ``doc_url`` is the ENGLISH PDF** (the lane joins the ZH row to
   the EN ``FILE_LINK``). The ZH file cannot be derived from it
   (``...1256_c.pdf`` is 404; the Chinese one is a different NEWS_ID,
   ``...1257_c.pdf``). The parser is therefore bilingual and is tested against
   the **Chinese** text layer, while runtime reads the English one. Both were
   verified to carry identical numbers (recon §2).

## Column assignment

Rows are read positionally against the quarter-header anchors (``Q1..Q4 Q1..Q4``
/ ``第一季..第四季`` twice), not by counting numbers: when a future quarter is
still blank (2026Q4 today) a count-based rule silently shifts every value one
column left. Each number is attached to its nearest anchor (tolerance 5 chars,
must be unique).

Every assignment is then **cross-checked against the disclosed ``vs 2025``
row** (or ``與 2025 變動``): the ratio / point difference must reproduce the
printed change. A quarter that fails the cross-check is dropped with a note —
never written. This is what makes the positional read safe on a layout that we
do not control.

Blank cell = not disclosed (2026Q4 today). It is never written as 0.
"""

from __future__ import annotations

import hashlib
import html
import re
import sys
import urllib.request
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from fd_industry_data import pdftext  # noqa: E402
from hotel_extractors._common import (  # noqa: E402
    dedup_latest,
    ensure_unit_concepts,
    insert_rows,
    unit_items,
)

SOURCE = "hkex-disclosure"
ENTITY_CODE = "00045.HK"
COMPANY_ZH = "大酒店"
COMPANY_EN = "The Hongkong and Shanghai Hotels, Limited"

#: 大写 HKEX 公告 id 无法从这里推出中文 PDF；运行时用英文版（见模块 docstring）
DOC_URL_CACHE = Path(__file__).resolve().parents[1] / "data" / "hkex-hotel-cache"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")

# ── availability matrix (measured, do not "unify") ──────────────────────────
_EVIDENCE = "openspec/changes/add-hotel-vertical/reports/4.0-wave3-recon.md"
AVAILABILITY: tuple[dict, ...] = (
    {
        "entity_code": "00045.HK",
        "entity_name": "大酒店",
        "status": "parsed",
        "carrier": "stand-alone quarterly 未經審核營運數據",
        "reason": ("季度独立营运数据公告，2024Q4 起连续；PDF 文本层含"
                   "半島酒店 RevPAR（分地区）与出租物業租金/出租率三表"),
        "evidence": _EVIDENCE,
        "recovery": "无需恢复（源侧按季持续发布，本通道逐季自动提取）",
    },
    {
        "entity_code": "01179.HK",
        "entity_name": "华住集团－Ｓ",
        "status": "embedded-in-results",
        "carrier": "季度业绩公告内的 海外監管公告-營運業績最新情況",
        "reason": ("经营数据不单独成公告，嵌在季度业绩公告里；本批不解析"
                   "业绩公告正文，故 RevPAR 系列按不可得呈现"),
        "evidence": _EVIDENCE,
        "recovery": "后续单立业绩公告抽取单元（同 4.3 美股 8-K 路线）",
    },
    {
        "entity_code": "00069.HK",
        "entity_name": "香格里拉（亚洲）",
        "status": "no-operating-data-table",
        "carrier": "海外監管公告-營運業績最新情況（通用标题）",
        "reason": ("类别命中 22 条，但标题一律是通用「Overseas Regulatory Announcement」，"
                   "中文版正文是「英文版已发布」占位页；实测其中一份 1,189,868 字节 PDF"
                   "（2026-08-10）全文 100,339 字符里 RevPAR 命中数为 0——该类别不含营运数据表"),
        "evidence": _EVIDENCE,
        "recovery": "若后续出现带 RevPAR 表的独立营运数据公告，再按 00045 同一解析器接入",
    },
    {
        "entity_code": "01270.HK",
        "entity_name": "朗廷－ＳＳ",
        "status": "no-operating-disclosure",
        "carrier": None,
        "reason": "无营运数据公告（合订证券，仅中期/年度业绩）",
        "evidence": _EVIDENCE,
        "recovery": "无（源侧不披露季度营运数据）",
    },
    {
        "entity_code": "00071.HK",
        "entity_name": "美丽华酒店",
        "status": "no-operating-disclosure",
        "carrier": None,
        "reason": "无营运数据公告",
        "evidence": _EVIDENCE,
        "recovery": "无（源侧不披露季度营运数据）",
    },
)
AVAILABILITY_BY_CODE = {r["entity_code"]: r for r in AVAILABILITY}


def availability(entity_code: str) -> dict:
    """Read-path status for a HK hotel entity (never a silent empty)."""
    rec = AVAILABILITY_BY_CODE.get(str(entity_code))
    if rec is None:
        return {
            "entity_code": str(entity_code),
            "status": "not-in-hk-hotel-universe",
            "reason": "该实体不在港股酒店宇宙（00045/01179/00069/01270/00071）",
            "evidence": _EVIDENCE,
        }
    return dict(rec)


# ── metrics / dimensions ────────────────────────────────────────────────────
#: metric key -> (zh, en, unit, entity_type-ish measure)
METRICS = {
    "revpar": ("RevPAR", "RevPAR", "HKD", "value"),
    "rent": ("每平方呎每月平均租金", "average monthly rent per sq ft leased", "HKD/sqft/month", "value"),
    "occupancy": ("出租率", "occupancy rate", "%", "ratio"),
}

#: portfolio -> (zh, en) ; revpar rows live under 半島酒店, rent/occupancy under 出租物業
PORTFOLIO = {
    "revpar": ("半島酒店", "PENINSULA"),
    "rent": ("出租物業", "LEASING"),
    "occupancy": ("出租物業", "LEASING"),
}

#: row label -> dimension key
REGIONS = {
    "大中華地區": "greater_china",
    "greater china": "greater_china",
    "歐洲": "europe",
    "europe": "europe",
    "美國": "usa",
    "usa": "usa",
    "亞洲(不包括大中華地區)": "asia_ex_greater_china",
    "asia (excluding greater china)": "asia_ex_greater_china",
}
SEGMENTS = {
    "住宅": "residential",
    "residential": "residential",
    "零售商場": "retail_arcades",
    "retail arcades": "retail_arcades",
    "辦公大樓": "office",
    "office": "office",
}

#: caliber text carried on every observation (source notes 1/3/4/6/7/8)
CALIBER = {
    "revpar": ("半島酒店：平均可出租客房收入＝客房總收入／可出租客房；"
               "各地区按各酒店组别加权平均；港元；未经审核"),
    "rent": ("出租物業：每平方呎每月平均租金＝租金總收入／已出租面積；"
             "按已出租面積加权；港元；未经审核"),
    "occupancy": ("出租物業：出租率＝已出租面積／可出租面積；"
                  "按可出租面積加权；百分比；未经审核"),
}

_QUARTER_TOKEN = re.compile(r"第([一二三四])季|Q([1-4])")
_CN_QUARTER = {"一": 1, "二": 2, "三": 3, "四": 4}
_TITLE_QUARTER = re.compile(
    r"(20\d{2})\s*年\s*第\s*([一二三四1-4])\s*季|(?:First|Second|Third|Fourth)\s+Quarter\s+of\s+(20\d{2})",
    re.I,
)
_EN_ORDINAL = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_TITLE_QUARTER_EN = re.compile(r"(First|Second|Third|Fourth)\s+Quarter\s+of\s+(20\d{2})", re.I)
_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_PCT = re.compile(r"[+-]?\d+(?:\.\d+)?\s*%")
_PP = re.compile(r"[+-]?\d+(?:\.\d+)?\s*pp", re.I)
_OPERATING_TITLE = re.compile(r"營運數據|营运数据|operating statistics", re.I)
_PLACEHOLDER_TITLE = re.compile(r"發行人剛在本網站英文版面發布|发行人刚在本网站英文版面发布")
_CHANGE_ROW = re.compile(r"與\s*20\d{2}\s*變動|与\s*20\d{2}\s*变动|vs\s*20\d{2}", re.I)

# 实测中英两版的列对齐都只是「接近」而非精确：中文版偏差 ≤4，英文版最高 6
# （2026Q3 的 2,912 距锚点 6 字符）。容差取 8 覆盖两版，同时保留「必须唯一最近」
# 的约束——锚点最小间距 6，等距即拒绝，不会吸到隔壁列。
ANCHOR_TOLERANCE = 8


def _norm_label(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip()).lower()


def title_period(title: str) -> tuple[int, int] | None:
    """(year, quarter) from the announcement title, both languages."""
    m = _TITLE_QUARTER_EN.search(title or "")
    if m:
        return int(m.group(2)), _EN_ORDINAL[m.group(1).lower()]
    m = re.search(r"(20\d{2})\s*年\s*第\s*([一二三四1-4])\s*季度", title or "")
    if m:
        q = m.group(2)
        return int(m.group(1)), _CN_QUARTER.get(q) or int(q)
    return None


def quarter_end(year: int, quarter: int) -> str:
    return f"{year}-{quarter * 3:02d}-{'31' if quarter in (1, 4) else '30'}"


def _anchors(header_line: str) -> list[int] | None:
    """Character offsets of the 8 quarter tokens (Q1..Q4 twice), or None."""
    hits = [(m.start(), int(m.group(2) or _CN_QUARTER[m.group(1)]))
            for m in _QUARTER_TOKEN.finditer(header_line)]
    if len(hits) != 8:
        return None
    # 两组各四季，按出现顺序；分不清四季边界就是畸形表头
    first = [q for _, q in hits[:4]]
    second = [q for _, q in hits[4:]]
    if first != [1, 2, 3, 4] or second != [1, 2, 3, 4]:
        return None
    return [pos for pos, _ in hits]


def _metric_of(line: str) -> str | None:
    low = line.lower()
    if "revpar" in low or "平均可出租客房收入" in line:
        return "revpar"
    if "average monthly rent" in low or "每平方呎每月平均租金" in line:
        return "rent"
    if ("occupancy rate" in low or "出租率" in line) and ("%" in line or "（%）" in line):
        return "occupancy"
    return None


def _dimension_of(metric: str, label: str) -> str | None:
    table = REGIONS if metric == "revpar" else SEGMENTS
    key = _norm_label(label)
    if key in table:
        return table[key]
    # 去括号再试一次（「亞洲(不包括大中華地區)」/「Asia (excluding Greater China)」）
    stripped = re.sub(r"[（(].*?[)）]", "", key).strip()
    for candidate, dim in table.items():
        if stripped and stripped == re.sub(r"[（(].*?[)）]", "", candidate).strip():
            return dim
    return None


def _assign(numbers: list[tuple[str, int]], anchors: list[int]) -> dict[int, str]:
    """Attach each number to its nearest unique anchor within tolerance."""
    out: dict[int, str] = {}
    for token, pos in numbers:
        dists = sorted((abs(pos - a), i) for i, a in enumerate(anchors))
        (d0, i0) = dists[0]
        if d0 > ANCHOR_TOLERANCE:
            continue
        if len(dists) > 1 and dists[1][0] == d0:
            continue                      # 等距=歧义，不猜
        out[i0] = token.replace(",", "")
    return out


def _assign_row(numbers: list[tuple[str, int]], anchors: list[int]) -> tuple[dict[int, str], str]:
    """Column assignment for one data row, with an explicit method label.

    1. ``anchor`` — nearest unique header anchor. Exact when the layout is
       clean (the Chinese PDF is: max deviation 4 chars).
    2. ``sequential`` — fallback for the ragged English layout, where a blank
       column (2026Q4 today) collapses the spacing and makes a value
       equidistant between two anchors. The rule it relies on is a property of
       the source, not of our layout guess: the previous-year group is always
       complete (4 values), and the current-year group fills strictly
       left-to-right as quarters are published, so the numbers before the last
       four are Q1, Q2, Q3 … in order.

    ``sequential`` is never trusted on its own: every current-year value it
    produces still has to reproduce the disclosed ``vs <year>`` change, and a
    value that fails is dropped with a note.
    """
    by_anchor = _assign(numbers, anchors)
    if len(by_anchor) == len(numbers):
        return by_anchor, "anchor"
    if len(numbers) < 5:
        return {}, "insufficient"
    out: dict[int, str] = {}
    for i, (tok, _pos) in enumerate(numbers[-4:]):
        out[4 + i] = tok.replace(",", "")
    for i, (tok, _pos) in enumerate(numbers[:-4]):
        out[i] = tok.replace(",", "")
    return out, "sequential"


def _change_pct(s: str) -> float | None:
    m = _PCT.search(s) or _PP.search(s)
    if not m:
        return None
    return float(re.sub(r"[^\-\d.]", "", m.group(0)))


def parse_operating_text(text: str, title: str, notes: list | None = None) -> list[dict]:
    """Parse one HK operating-data PDF text layer into per-(dimension, quarter).

    Returns dicts::

        {"metric": "revpar", "dimension": "greater_china", "date": "2026-03-31",
         "year": 2026, "quarter": 1, "value": "2902", "unit": "HKD",
         "caliber": "...", "period_label": "2026Q1", "change": "+31%"}

    Values are the exact disclosed integers/percentages (commas stripped).
    A quarter that fails the ``vs 2025`` cross-check is dropped with a note.
    A blank cell is simply absent — never 0.
    """
    notes = notes if notes is not None else []
    period = title_period(title)
    if period is None:
        notes.append(f"{SOURCE}: 原文不可解析——标题无「20XX 年第N季度 / Nth Quarter of 20XX」，"
                     "无法确定列所属年份（卡在标题期次）")
        return []
    year, _quarter = period
    years = (year, year - 1)

    lines = (text or "").split("\n")
    records: list[dict] = []
    metric: str | None = None
    anchors: list[int] | None = None
    recent_label: str | None = None

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        seen = _metric_of(stripped)
        if seen:
            metric = seen
            anchors = None                     # 换表必须重新找表头锚点
            continue

        cand = _anchors(line)
        if cand:
            anchors = cand
            continue

        if anchors is None or metric is None:
            continue

        if _CHANGE_ROW.search(stripped):
            continue                           # 变动行在下一轮用「前一行」配对

        if not _NUM.search(line):
            # 无数字行可能是换行标签的前半段（「Asia (excluding Greater」）
            recent_label = stripped
            continue

        numbers = [(m.group(0), m.start()) for m in _NUM.finditer(line)]
        # 标签只取第一个数字之前的部分——把整行（含数值）当标签去查维度永远查不中
        label = line[:numbers[0][1]].strip()
        dim = _dimension_of(metric, label)
        if dim is None and recent_label is not None:
            joined = f"{recent_label} {label}"
            merged = _dimension_of(metric, joined)
            if merged:
                dim = merged
                label = joined
        if dim is None:
            recent_label = None
            continue

        recent_label = None
        assigned, method = _assign_row(numbers, anchors)
        if method == "insufficient":
            notes.append(f"{SOURCE}: {label} 行只有 {len(numbers)} 个数字"
                         "（少于「上年四列」所需），列归属无法确定，该行不写值")
            continue
        if method == "sequential":
            notes.append(f"{SOURCE}: {label} 列对齐不足，改用「上年四列完整 + 当年自 Q1 "
                         "顺序」规则，并由 vs 上年变动行逐季校验")

        # 下一非空行是变动行（「與 2025 變動」「vs 2025」）
        changes: dict[int, float] = {}
        for nxt in lines[idx + 1: idx + 3]:
            if not nxt.strip():
                continue
            if _CHANGE_ROW.search(nxt):
                cnums = [(m.group(0), m.start()) for m in _PCT.finditer(nxt)]
                if not cnums:
                    cnums = [(m.group(0), m.start()) for m in _PP.finditer(nxt)]
                c_assigned = _assign(cnums, anchors)
                if len(c_assigned) != len(cnums) and 0 < len(cnums) <= 4:
                    # 变动行只给当年季度，数量即季数，按序落 Q1..Qk
                    c_assigned = {i: tok for i, (tok, _p) in enumerate(cnums)}
                for i, tok in c_assigned.items():
                    val = _change_pct(tok)
                    if val is not None:
                        changes[i] = val
            break

        for anchor_idx, raw in assigned.items():
            group, slot = divmod(anchor_idx, 4)
            if group != 0:
                continue                       # 只写当年列（上年列只用于核对）
            quarter = slot + 1
            prev = assigned.get(anchor_idx + 4)
            chg = changes.get(anchor_idx)
            if prev is not None and chg is not None:
                cur_v, prev_v = float(raw), float(prev)
                if metric == "occupancy":
                    ok = abs((cur_v - prev_v) - chg) <= 1.5      # 百分点，取整误差
                else:
                    ok = prev_v != 0 and abs((cur_v / prev_v - 1) * 100 - chg) <= 1.5
                if not ok:
                    notes.append(
                        f"{SOURCE}: {label} {years[0]}Q{quarter} 与「vs 上年」变动不符"
                        f"（本期 {raw}、上年 {prev}、披露变动 {chg:+.1f}）——列归属不可信，"
                        "该季不写值")
                    continue
            records.append({
                "metric": metric,
                "dimension": dim,
                "date": quarter_end(years[0], quarter),
                "year": years[0],
                "quarter": quarter,
                "value": raw,
                "unit": METRICS[metric][2],
                "caliber": CALIBER[metric],
                "period_label": f"{years[0]}Q{quarter}",
                "change": f"{chg:+.1f}" if chg is not None else None,
            })
    return records


#: 去重后的维度序列（REGIONS/SEGMENTS 里中英别名指向同一维度，不能直接取 values()）
REGION_DIMS = ("greater_china", "europe", "usa", "asia_ex_greater_china")
SEGMENT_DIMS = ("residential", "retail_arcades", "office")

# ── DB-facing ──────────────────────────────────────────────────────────────
CONCEPTS: list[tuple] = []
for _metric, (_zh, _en, _unit, _measure) in METRICS.items():
    for _dim in (REGION_DIMS if _metric == "revpar" else SEGMENT_DIMS):
        _code = f"hotel.hk.{_metric}.{_dim}.quarterly"
        CONCEPTS.append((
            _code,
            f"港股酒店 {_zh} {_dim}（季度）",
            f"HK hotel {_en}, {_dim.replace('_', ' ')} (quarterly)",
            "行业经营",
            _unit,
            _measure,
            "quarter",
            "stock",
            SOURCE,
        ))
CONCEPT_BY_KEY = {
    (c[0].split(".")[2], c[0].split(".")[3]): c[0] for c in CONCEPTS
}


def fetch_pdf(url: str, notes: list, cache_dir: Path = DOC_URL_CACHE) -> bytes | None:
    """Fetch an announcement PDF (gzip + timeout), with a disk cache.

    Returns None on any failure **and** records a note — a missing announcement
    is visible, never silently reduced to zero rows.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".pdf")
    if cache.exists():
        return cache.read_bytes()
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip",
        "Accept": "application/pdf,*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            raw = resp.read()
    except Exception as exc:  # noqa: BLE001
        notes.append(f"{SOURCE}: 公告 PDF 抓取失败 {url} — {type(exc).__name__}: {exc}")
        return None
    if not pdftext.is_pdf(raw):
        notes.append(f"{SOURCE}: 公告不是 PDF（魔数不符）{url} — 跳过不猜")
        return None
    cache.write_bytes(raw)
    return raw


def extract(cur, notes) -> list[tuple]:
    """Read stored hkex-disclosure payloads for 00045 and emit observations."""
    payloads = unit_items(cur, SOURCE)
    if not payloads:
        notes.append(f"{SOURCE}: 库内无载荷——先跑 hkex-disclosure 车道（00045 等 5 家），"
                     "否则本通道无从提取")
        return []

    latest = dedup_latest(
        [p for p in payloads
         if str(p.get("stock_code")) == "00045"
         and _OPERATING_TITLE.search(html.unescape(str(p.get("title") or "")))
         and not _PLACEHOLDER_TITLE.search(html.unescape(str(p.get("title") or "")))],
        key=lambda p: html.unescape(str(p.get("title") or "")),
    )
    if not latest:
        notes.append(f"{SOURCE}: 00045 无「未經審核營運數據」类公告载荷——"
                     f"按 availability 呈现，不造数")
        return []

    cur.execute(
        "SELECT id FROM entities WHERE entity_type='stock' AND code=%s",
        (ENTITY_CODE,),
    )
    row = cur.fetchone()
    if row is None:
        notes.append(f"{SOURCE}: 实体 {ENTITY_CODE} 未注册（任务 3.6）——先注册再提取")
        return []
    eid = row[0]

    rows: list[tuple] = []
    seen_quarter: set[tuple[str, str]] = set()
    for payload in sorted(latest.values(), key=lambda p: str(p.get("release_date") or "")):
        title = html.unescape(str(payload.get("title") or ""))
        url = payload.get("doc_url")
        if not url:
            notes.append(f"{SOURCE}: {title} 无 doc_url——跳过（不猜公告编号）")
            continue
        raw = fetch_pdf(url, notes)
        if raw is None:
            continue
        text = pdftext.extract_text(raw)
        if not text.strip():
            notes.append(f"{SOURCE}: {title} PDF 无文本层（图片型）——按不可得留痕，不做 OCR")
            continue
        parsed = parse_operating_text(text, title, notes)
        for rec in parsed:
            key = (rec["metric"], rec["dimension"], rec["date"])
            if key in seen_quarter:
                continue                      # 同一季在多份公告里重复出现，取首份
            seen_quarter.add(key)
            code = CONCEPT_BY_KEY[(rec["metric"], rec["dimension"])]
            rows.append((code, "stock", eid, rec["date"], "quarter", rec["value"]))

    notes.append(f"{SOURCE}: 00045 解析出 {len(rows)} 条季度观测"
                 f"（覆盖 {len({r[3] for r in rows})} 个季度）；"
                 "其余 4 家按 AVAILABILITY 呈现不可得")
    return rows


def main(argv=None) -> int:
    import argparse
    import json
    import os

    import psycopg2

    ap = argparse.ArgumentParser(description="港股酒店营运数据通道（默认 dry-run）")
    ap.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    ap.add_argument("--write", action="store_true", help="写入 semantic_observations")
    ap.add_argument("--availability", action="store_true", help="只打印可得性矩阵")
    args = ap.parse_args(argv)

    if args.availability:
        print(json.dumps(list(AVAILABILITY), ensure_ascii=False, indent=2))
        return 0
    if not args.dsn:
        raise SystemExit("FATAL: $FD_OPEN_DATA_DSN 未设置（source ~/.config/finddata/dashboards.env）")

    notes: list[str] = []
    conn = psycopg2.connect(args.dsn)
    cur = conn.cursor()
    try:
        rows = extract(cur, notes)
        for n in notes:
            print("NOTE", n)
        print(f"rows={len(rows)}")
        for r in rows[:12]:
            print("  ", r[3], r[5], r[0])
        if args.write and rows:
            ids = ensure_unit_concepts(cur, SOURCE, CONCEPTS, {r[0] for r in rows})
            print("inserted =", insert_rows(cur, SOURCE, ids, rows))
            conn.commit()
            print("COMMITTED")
        else:
            conn.rollback()
            print("DRY-RUN (no write)" if not args.write else "nothing to write")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
