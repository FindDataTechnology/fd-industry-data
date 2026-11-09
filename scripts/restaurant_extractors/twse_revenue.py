#!/usr/bin/env python3
"""twse-monthly-revenue — 台股（上市）月营收**快照** extractor (restaurant wave 0).

上游 = 既有单元 ``spiders/twse-monthly-revenue`` 落库的 payload
（``GET https://openapi.twse.com.tw/v1/opendata/t187ap05_L``，上市公司每月
營業收入彙總表，免 key）。本抽取器只做语义落库，不新增抓取：

    公司代號 2727 王品     -> entity 2727.TW   (entity_type 'stock')
    公司代號 2723 美食-KY  -> entity 2723.TW   (entity_type 'stock')
    公司代號 2729 瓦城     -> entity 2729.TW   (entity_type 'stock')

全部写入 concept ``industry.monthly_operation_metric``
（unit twd_thousand / measure value / frequency monthly / entity_type stock），
``date`` = payload ``data_month``（spider 已把民国 ``11509`` 归一为 ``2026-09``），
``granularity='month'``，``source_used='twse-monthly-revenue'``。

口径与诚实边界（2026-11-08 实测）：

* **快照式累积，无历史回溯**：该端点只透出「当期」薪资（資料年月=上一個月），
  每家公司在单次 payload 里只有一行；历史只能按本单元每月一跑正向累加。
  因此本批每家只有 1 期（2026-09）——**少于 12 期不得做趋势结论、不得进
  趋势板**，也不属于 12-23 期的 ``short_window`` 档；不补采、不插值、不拿
  ``累計營業收入`` 拆月伪造历史。未来月份由调度逐月累加，本抽取器可重复跑。
* **2729 瓦城走上柜车道**：``t187ap05_L`` 是**上市**公司彙總，瓦城泰統是
  **上柜（TPEx）**公司，其月营收在 ``spiders/tpex-monthly-revenue``
  （``GET https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O``）。本抽取器
  两源并读、按 payload 的 ``source`` 逐行标 ``source_used``，不把上柜数据
  冒充成上市数据，也不跨源补别人的缺口。
* 数值原样取 ``revenue_current``（營業收入-當月營收，千元新台币），不换算；
  缺测/非法值跳过并留痕。
* 幂等：``INSERT ... ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING``，
  dedup 以 ``(公司代號, data_month)`` 取 ``scraped_at`` 最新的一份。

Module contract: SOURCE / CONCEPTS / extract(cur, notes) — see _common.py.
"""

from __future__ import annotations

from . import _common as C

SOURCE = "twse-monthly-revenue"

#: 第二条台股车道：上櫃（TPEx）。瓦城 2729 是上櫃公司，不在上市彙總
#: ``t187ap05_L``；本抽取器按 payload 的 ``source`` 字段区分两源，
#: 观测行的 ``source_used`` 保留真实来源，不把上櫃数据冒充成上市数据。
TPEX_SOURCE = "tpex-monthly-revenue"

CONCEPT = "industry.monthly_operation_metric"
CATEGORY = "行业经营"

#: (code, name_zh, name_en, category, unit, measure, frequency, entity_type, source)
CONCEPTS = [
    (CONCEPT,
     "月度营业收入（企业月营收公告）",
     "Monthly operating revenue (company monthly disclosure)",
     CATEGORY, "twd_thousand", "value", "monthly", "stock", SOURCE),
]

#: 公司代號 -> 宇宙实体 code（真源 docs/restaurant-universe.yaml；
#: 实体由 scripts/register_restaurant_universe.py 注册，本抽取器不建实体）
CODE_ENTITY = {
    "2727": "2727.TW",   # 王品 Wowprime
    "2729": "2729.TW",   # 瓦城泰統 TTFB（上柜 TPEx，见模块注释）
    "2723": "2723.TW",   # 美食-KY Gourmet Master
}


def _resolve_entity_ids(cur, codes: set[str]) -> dict[str, int]:
    """entity code -> entities.id for codes that already exist (no creation)."""
    if not codes:
        return {}
    cur.execute(
        "SELECT code, id FROM entities"
        " WHERE entity_type = 'stock' AND code = ANY(%s)",
        (sorted(codes),),
    )
    return {code: eid for code, eid in cur.fetchall()}


def extract(cur, notes: list[str]) -> list[tuple]:
    """Return rows (concept, 'stock', entity_id, 'YYYY-MM', 'month', value).

    Appends notes for dedup, absent companies, skipped values and the trend
    status of every series — never silently.
    """
    items = C.unit_items(cur, SOURCE)
    tpex_items = C.unit_items(cur, TPEX_SOURCE)
    items = items + tpex_items
    notes.append(f"{SOURCE}: crawl payloads read: {len(items)}"
                 f"（twse {len(items) - len(tpex_items)} + tpex {len(tpex_items)}）")

    seen = C.dedup_latest(
        items,
        key=lambda p: (str(p.get("code")), str(p.get("data_month")))
        if str(p.get("code")) in CODE_ENTITY and p.get("data_month") else None,
    )
    notes.append(
        f"{SOURCE}: after (公司代號, data_month) dedup keep-latest: {len(seen)}")

    entity_ids = _resolve_entity_ids(cur, set(CODE_ENTITY.values()))
    missing_entities = sorted(set(CODE_ENTITY.values()) - set(entity_ids))
    if missing_entities:
        notes.append(
            f"{SOURCE}: 宇宙实体未注册（main agent 负责注册，抽取器不建实体）"
            f"{missing_entities} —— 其行不写")

    rows: list[tuple] = []
    skipped_values: dict[str, int] = {}
    for (code, month), payload in sorted(seen.items()):
        value = C.num(payload.get("revenue_current"))
        if value is None:
            skipped_values[code] = skipped_values.get(code, 0) + 1
            continue
        eid = entity_ids.get(CODE_ENTITY[code])
        if eid is None:
            continue
        row_source = TPEX_SOURCE if str(payload.get("source")) == TPEX_SOURCE else SOURCE
        rows.append((CONCEPT, "stock", eid, str(month), "month", C.fmt(value),
                     row_source))

    found = {code for (code, _month) in seen}
    absent = [code for code in sorted(CODE_ENTITY) if code not in found]
    if absent:
        detail = ", ".join(f"{c} {CODE_ENTITY[c]}" for c in absent)
        notes.append(
            f"{SOURCE}: 上游 payload 无这些公司（不产行、不跨源冒充）: {detail}")
        if "2729" in absent:
            notes.append(
                f"{SOURCE}: 2729 瓦城泰統 是上柜（TPEx）公司，本次两源并读"
                f"（{SOURCE} + {TPEX_SOURCE}）后仍无其行 → 上柜源当期未落 payload，"
                f"需检查 spiders/tpex-monthly-revenue 是否运行。")
    if skipped_values:
        notes.append(f"{SOURCE}: 当月营收缺测/非法值跳过: {skipped_values}")

    # 趋势资格（不伪造、不静默）：快照式单点不得当趋势
    counts: dict[tuple, int] = {}
    windows: dict[tuple, list[str]] = {}
    for row in rows:
        key = (row[0], row[2])
        counts[key] = counts.get(key, 0) + 1
        windows.setdefault(key, []).append(row[3])
    for (code, eid), n in sorted(counts.items()):
        ds = sorted(windows[(code, eid)])
        notes.append(
            f"{SOURCE}: 趋势资格 {code} entity_id={eid}: {n} 期"
            f"（{ds[0]}..{ds[-1]}）< 12 期 → 不作趋势结论、不进趋势板"
            f"（亦非 12-23 期 short_window 档）")
    if rows:
        notes.append(
            f"{SOURCE}: 快照式累积——t187ap05_L 只透出当期（資料年月=上一個月），"
            f"无历史回溯；从 2026-09 起逐月累加，当期单点不作趋势。"
            f"未补采、未插值、未用累計營業收入拆月。")
    return rows
