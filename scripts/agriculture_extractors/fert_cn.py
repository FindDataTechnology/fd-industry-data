#!/usr/bin/env python3
"""Agricultural extractor — fert-cn (中肥网 月度进出口 / 产量数据).

**数据面**：crawl_items 里的长表，一行 = (period, product, flow) 三元组，
字段 ``volume_wan_t / amount_usd / yoy_pct / mom_pct``。序列维度是
**产品 × 流向**（尿素·进口 / 尿素·出口 / 三元复合肥·进口 …）。

**期数实测（2026-11-08，limit=5000 + 窗口 2024-01 起）**：491 行 / 18 期
（2025-03 … 2026-08）/ 30 条序列，每条 18 期。12–23 期属模板里的
``short_window`` 档（与 ``moa-fishery`` 同为 18 期），故显式声明 18，不是偷偷放宽门。

**口径纪律**：
* 只写 ``volume_wan_t``（实物量，万吨）。``amount_usd`` 是金额，进另一条序列会
  把「量」和「额」混在一个概念里；本轮不做金额序列（要做需另立概念）。
* ``product`` 保留源表原词，不归并（「尿素」≠「小颗粒尿素」，同族变体必须逐条比）。
* 缺失值为空就不写行，**不补 0**；``period`` 解析不出来也跳过。
* 只读 ``crawl_items``（单元已在平台点亮），不再 live 重抓——重抓会让「入库」与
  「抓取」两件事的时点错开，读数无法复现。
"""
from __future__ import annotations

from ._common import (ENTITY_COUNTRY_CN, fmt, gate_periods, num, unit_items)

SOURCE = "fert-cn"
_CAT = "农业"

#: 源表里的流向词 → 概念后缀词（**只做同一含义的英文对译**，不做归并）
_FLOW_EN = {"import": "import", "export": "export"}

#: 12–23 期档：显式 short_window 声明（实测 18 期）
SHORT_WINDOW_PERIODS = 18

#: **由 extract() 现填**：产品清单来自数据本身，不在代码里硬编码。
#: driver 的调用顺序是 extract() → mod.CONCEPTS，故这里留空表由 extract 填。
CONCEPTS: list[tuple] = []


def _concept(product: str, flow: str) -> tuple[str, str]:
    """产品 × 流向 → (concept_code, 中文名)。

    代码里的产品段用哈希短码：中文词直接落码在跨源合并时会产生歧义，
    原名进 name_zh（与分部实体同一套做法）。
    """
    import hashlib
    slug = hashlib.sha1(product.encode("utf-8")).hexdigest()[:8]
    flow_en = _FLOW_EN.get(flow, flow)
    code = f"agriculture.cn.fertilizer.p{slug}.{flow_en}.monthly"
    return code, f"{product}{'进口' if flow == 'import' else '出口'}量（中肥网月度）"


def extract(cur, notes) -> list:
    raw = unit_items(cur, SOURCE)
    notes.append(f"{SOURCE}: read {len(raw)} stored crawl_items (no live refetch)")

    rows: list = []
    skipped_value = skipped_period = skipped_flow = 0
    seen: dict[str, str] = {}          # code -> 中文名（供概念创建）
    for r in raw:
        product = (r.get("product") or "").strip()
        flow = (r.get("flow") or "").strip()
        period = str(r.get("period") or "").strip()
        if not product or flow not in _FLOW_EN:
            skipped_flow += 1
            continue
        if len(period) != 7 or period[4] != "-":
            skipped_period += 1
            continue
        v = num(r.get("volume_wan_t"))
        if v is None:
            skipped_value += 1
            continue
        code, zh = _concept(product, flow)
        seen[code] = zh
        rows.append((code, "country", ENTITY_COUNTRY_CN, period, "month", fmt(v)))

    # 同一 (code, period) 只留一行（同月重复抓取导致），计数留痕
    # 概念定义由实际出现的 (product, flow) 现推，名称必须非空（concepts 表 NOT NULL）
    CONCEPTS.clear()
    for code, zh in sorted(seen.items()):
        CONCEPTS.append((code, zh,
                         f"China fertilizer monthly volume, {zh.split('（')[0]}",
                         _CAT, "万吨", "value", "monthly", "country", SOURCE))

    uniq, dup = [], 0
    got = set()
    for row in rows:
        key = (row[0], row[3])
        if key in got:
            dup += 1
            continue
        got.add(key)
        uniq.append(row)

    notes.append(
        f"{SOURCE}: {len(raw)} payload(s) -> {len(uniq)} row(s);"
        f" skip missing-volume {skipped_value}, unparsable period {skipped_period},"
        f" non-import/export flow {skipped_flow}, duplicate (code,period) {dup}"
    )

    kept, dropped = gate_periods(uniq, 12)   # 12–23 档：short_window 放行
    if dropped:
        notes.append(f"{SOURCE}: DROPPED below 12 periods: "
                     f"{len({r[0] for r in dropped})} series ({len(dropped)} rows)")
    for code in sorted({r[0] for r in kept}):
        n = len([r for r in kept if r[0] == code])
        notes.append(f"{SOURCE}: {code} -> {n} period(s) （{seen.get(code, '')}）")
    notes.append(f"{SOURCE}: {len({r[0] for r in kept})} series over 12-23 band"
                 f" -> declared short_window({SHORT_WINDOW_PERIODS})")
    return kept
