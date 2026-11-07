#!/usr/bin/env python3
"""Flower-vertical extractor — sec-edgar-facts（美股 5 家头条财务）.

This is the flower-line sibling of ``restaurant_extractors/sec_edgar_facts.py``:
same upstream unit, same stored payload shape (``kind == "fact"``), but a
different universe and two deliberate differences.

Universe: read from ``docs/flower-universe.yaml`` — the file itself declares
"注册脚本与看板只读本文件，不在单元内硬编码实体列表"… hence no ticker list is
hardcoded here either. Only companies whose ``lanes`` contain
``sec-edgar-facts`` are taken (today: FLWS / CENT / SMG / HYFM / EDBL).

Why this extractor exists at all (2026-11-08): the flower universe was
registered and the lane was declared, but the unit had only ever been run with
the 12 restaurant tickers. The five flower companies had **zero** observations
while a reader of the tool surface was told "美股 company facts 已抓到" — true of
the unit, false of these companies. A crawl with ``tickers=[5 flower]`` (run
4836, 831 rows) was made, and this extractor turns those facts into
observations.

Caliber decisions (each is a place where a wrong default fabricates data):

* **只取单季窗** 70–110 天。XBRL 对同一 ``end`` 同时报单季与 fiscal-YTD，
  两者同写会撞 ``uq_sem_obs`` 并造成量级误读；YTD 行显式 SKIP 并计数。
* **重述取最新 ``filed``**（spider 已按 filed 排序，这里再幂等去重一次）。
* **单位只收 USD**：``USD/shares`` / ``shares`` 不进金额概念。
* **不套 24 期趋势门**：花卉线的新上市公司（HYFM 2020 上市、EDBL 体量小）
  历史短是事实，不是缺陷；``extract_flower_wave1`` 本身也不套该门，短史
  判定归看板层（``short_window`` caveat），这里只如实计数并在 notes 里报。
* **不做任何分部推断**：companyfacts 已剥掉维度成员，分部口径属
  ``awaken-flower-report-ingest`` 的 ``xbrl-segment-extraction``（10-K
  instance），本抽取器不猜。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from restaurant_extractors._common import fmt, num, unit_items

SOURCE = "sec-edgar-facts"

#: No new concepts — financials.* already exist and are shared with the A-share
#: (cnreport / stock-zygc) supply, so one concept query spans both markets.
CONCEPTS: list = []

CODE_BY_TAG = {
    "Revenues": "financials.revenue",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "financials.revenue",
    "SalesRevenueNet": "financials.revenue",
    "OperatingIncomeLoss": "financials.operating_profit",
    "NetIncomeLoss": "financials.net_income",
}
#: Kept as evidence, never mapped: the "Including Assessed Tax" caliber carries
#: tax collected on behalf of authorities, so appending it to the same series
#: as the Excluding tag would fabricate a revenue jump (HYFM did exactly this
#: switch from 2026; that period is a gap with a stated reason, not a value).
UNMAPPED_TAGS = {"RevenueFromContractWithCustomerIncludingAssessedTax"}

QUARTER_MIN_DAYS, QUARTER_MAX_DAYS = 70, 110

_UNIVERSE_YAML = Path(__file__).resolve().parents[2] / "docs" / "flower-universe.yaml"
_LANE = SOURCE
_REGION = "US"


def _universe() -> dict[str, dict]:
    """ticker -> company row, for companies whose lanes include this unit."""
    import yaml

    doc = yaml.safe_load(_UNIVERSE_YAML.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for c in doc.get("companies") or []:
        if str(c.get("region")) != _REGION:
            continue
        if _LANE not in (c.get("lanes") or []):
            continue
        code = str(c.get("code") or "").strip()
        if code:
            out[code] = c
    return out


def _days(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    try:
        return (date.fromisoformat(str(end)) - date.fromisoformat(str(start))).days
    except ValueError:
        return None


def _entity_ids(cur, codes) -> dict:
    """code -> entity id for the US flower universe (entity_type='stock')."""
    cur.execute(
        "SELECT code, id FROM entities WHERE entity_type='stock'"
        " AND code = ANY(%s)",
        (sorted(codes),),
    )
    return {code: eid for code, eid in cur.fetchall()}


def extract(cur, notes) -> list[tuple]:
    universe = _universe()
    if not universe:
        notes.append(f"{SOURCE}: 花卉宇宙里没有 {_LANE} 车道的实体——本批跳过")
        return []

    items = unit_items(cur, SOURCE)
    if not items:
        notes.append(f"{SOURCE}: 中心库无 success payload——本批跳过，不硬抽")
        return []

    codes = set(universe)
    ids = _entity_ids(cur, codes)
    missing = sorted(codes - set(ids))
    if missing:
        notes.append(f"{SOURCE}: ⚠️ 花卉宇宙实体缺 {missing}——这些公司的观测不写"
                     "（实体注册见 scripts/register_flower_universe.py）")

    # (concept_code, entity_id, end) -> (filed, value); latest filing wins
    best: dict[tuple, tuple[str, float]] = {}
    ytd_skipped = 0
    non_usd = 0
    no_entity = 0
    unmapped: set[str] = set()
    seen_tickers: set[str] = set()

    for p in items:
        if p.get("kind") != "fact":
            continue
        ticker = p.get("ticker")
        if ticker not in codes:
            continue  # other verticals' payloads share this unit
        seen_tickers.add(ticker)
        code = CODE_BY_TAG.get(p.get("tag"))
        if code is None:
            if p.get("tag") in UNMAPPED_TAGS:
                unmapped.add(p["tag"])
            continue
        if str(p.get("unit") or "").upper() != "USD":
            non_usd += 1
            continue
        span = _days(p.get("start"), p.get("end"))
        if span is None or not (QUARTER_MIN_DAYS <= span <= QUARTER_MAX_DAYS):
            ytd_skipped += 1
            continue
        eid = ids.get(ticker)
        if eid is None:
            no_entity += 1
            continue
        value = num(p.get("value"))
        if value is None:
            continue
        key = (code, eid, str(p["end"]))
        filed = str(p.get("filed") or "")
        prev = best.get(key)
        if prev is None or filed > prev[0]:
            best[key] = (filed, value)

    rows = [(code, "stock", eid, end, "quarter", fmt(value))
            for (code, eid, end), (_, value) in sorted(best.items())]

    if unmapped:
        notes.append(f"{SOURCE}: payload-only（未映射概念）{sorted(unmapped)}"
                     "——含代收税口径，与 Excluding 不同源不可拼接；该期为 gap")
    never_crawled = sorted(codes - seen_tickers)
    if never_crawled:
        notes.append(f"{SOURCE}: 花卉宇宙 {never_crawled} 在本底账零 payload"
                     "（车道参数未含它们：需以 tickers 显式触发，见 README）")
    if ytd_skipped:
        notes.append(f"{SOURCE}: 剔除 {ytd_skipped} 条非单季窗事实（YTD 累计口径，"
                     "与单季混写会造成同键冲突与量级误读）")
    if non_usd:
        notes.append(f"{SOURCE}: 剔除 {non_usd} 条非 USD 单位事实（量纲保护）")
    if no_entity:
        notes.append(f"{SOURCE}: {no_entity} 条事实的公司实体未注册，未写")

    # Per-company coverage, so a short history is visible instead of implied.
    per: dict[tuple, list[str]] = {}
    for (code, eid, end) in best:
        per.setdefault((code, eid), []).append(end)
    ticker_of = {v: k for k, v in ids.items()}
    for (code, eid), ends in sorted(per.items()):
        ends.sort()
        span = f"{ends[0]}..{ends[-1]}，{len(ends)} 期"
        short = " ⚠️ 短史（<24 期，看板层需 short_window 声明）" if len(ends) < 24 else ""
        notes.append(f"{SOURCE}: {ticker_of.get(eid, eid)} {code} {span}{short}")

    notes.append(
        f"{SOURCE}: 花卉线 {len(codes)} 家美股共 {len(rows)} 个观测点；"
        "分部收入不经本抽取器（companyfacts 已剥维度，归 awaken-flower-report-ingest）"
    )
    return rows
