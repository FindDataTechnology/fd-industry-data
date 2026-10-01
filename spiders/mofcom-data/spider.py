"""mofcom-data — 商务部数据中心（data.mofcom.gov.cn）三口径采集。

口径与端点（2026-10 直连实测，全部免鉴权，须带 Referer）：
1. 货物进出口月度  POST /datamofcom/front/totalmonth/query
   当年月度全序列一次返回（2026 年实测 8 行 202601→202608）；
   pageNumber 为唯一生效参数（pageNumber=1 实测有效，翻页返回重复内容，故只取一页）。
2. 服务贸易历年    GET /datamofcom/front/fwmy/overyears?pageNumber=N
   真分页：pageSize=10，total/maxPageNum 由响应给出（实测 44 行、5 页，2025→1982）。
   不带 pageNumber 会报 Spring "Optional int parameter 'pageNumber'" 错误页。
3. 利用外资月度    POST /datamofcom/front/lywz/direct/query（pageNumber=1）
   一次返回全序列（实测 247 行，1983-12→2026-08，全站最深序列）。

返回结构坑：totalmonth/lywz 为裸双元素数组 [数据行, 元信息]，fwmy 为
{"rows": [...], "total": .., "maxPageNum": ..}。解析前先按形态解包。

参数纪律：仅发送实测有效参数（各端点仅 pageNumber），不发 year/pageNo 等多余
参数；5xx/空响应/解析失败 → 记录后跳过该口径，不重试、不绕反爬。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from scrapling.fetchers import FetcherSession

BASE_URL = "https://data.mofcom.gov.cn"
TOTALMONTH_URL = f"{BASE_URL}/datamofcom/front/totalmonth/query"
FWMY_URL = f"{BASE_URL}/datamofcom/front/fwmy/overyears"
LYWZ_URL = f"{BASE_URL}/datamofcom/front/lywz/direct/query"

# 国内源：直连为准，禁走代理。
HDRS = {"Referer": f"{BASE_URL}/"}

logger = logging.getLogger("mofcom-data")

# 指标白名单：字段名 → (中文指标名, 单位)。同比/占比列（*_per）不发。
_TOTALMONTH_INDICATORS = [
    ("total_value", "进出口总额（当月值）"),
    ("export_value", "出口额（当月值）"),
    ("import_value", "进口额（当月值）"),
    ("imexgap_value", "贸易顺差（当月值）"),
    ("total_lj_value", "进出口总额（累计值）"),
    ("export_lj_value", "出口额（累计值）"),
    ("import_lj_value", "进口额（累计值）"),
    ("imexgap_lj_value", "贸易顺差（累计值）"),
]
_TOTALMONTH_UNIT = "亿美元"

_FWMY_INDICATORS = [
    ("importAndExportVolumeRMB", "服务进出口总额（人民币）", "亿元人民币"),
    ("exportVolumeRMB", "服务出口额（人民币）", "亿元人民币"),
    ("importVolumeRMB", "服务进口额（人民币）", "亿元人民币"),
    ("differenceRMB", "服务贸易差额（人民币）", "亿元人民币"),
    ("importAndExportVolumeUSD", "服务进出口总额（美元）", "亿美元"),
    ("exportVolumeUSD", "服务出口额（美元）", "亿美元"),
    ("importVolumeUSD", "服务进口额（美元）", "亿美元"),
    ("differenceUSD", "服务贸易差额（美元）", "亿美元"),
]

_LYWZ_INDICATORS = [
    ("actual_amount", "实际使用外资金额（年内累计）", "亿美元"),
    ("project_number", "新设外商投资企业数（年内累计）", "家"),
]

_YM_RE = re.compile(r"^(\d{4})(\d{2})$")
_Y_RE = re.compile(r"^\d{4}$")
_MISS_VALUES = {"", "-", "—", "–", "null", "None"}
_FWMY_MAX_PAGES = 12  # 防御上限：实测 5 页（maxPageNum），翻不到头即停


def _clean_num(v):
    """千分位/空串/占位符 → None；可转 float 才要，解析失败留空不写脏值。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if s in _MISS_VALUES:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _period_yyyymm(v: str) -> str | None:
    """'202608' → '2026-08'；非 YYYYMM 返回 None（该行丢弃，不写脏行）。"""
    m = _YM_RE.match(str(v).strip())
    return f"{m.group(1)}-{m.group(2)}" if m else None


def _unwrap_rows(payload):
    """解包三种实测返回形态：[rows, meta] / {"rows": [...]} / [...]。"""
    if isinstance(payload, dict):
        rows = payload.get("rows", [])
        return rows if isinstance(rows, list) else []
    if isinstance(payload, list) and payload and isinstance(payload[0], list):
        return payload[0] if isinstance(payload[0], list) else []
    return payload if isinstance(payload, list) else []


def _mk_row(period: str, indicator: str, value, unit: str, source_url: str, now_iso: str) -> dict:
    return {
        "period": period,
        "indicator": indicator,
        "value": value,
        "unit": unit,
        "scraped_at": now_iso,
        "source_url": source_url,
    }


async def _fetch_totalmonth(s) -> tuple[list[dict], str]:
    """货物进出口月度：POST pageNumber=1，当年月度全序列。"""
    r = await s.post(
        TOTALMONTH_URL,
        data={"pageNumber": 1},
        headers={**HDRS, "Content-Type": "application/x-www-form-urlencoded"},
    )
    if r.status != 200:
        logger.warning("totalmonth/query HTTP %s，跳过该口径", r.status)
        return [], ""
    payload = json.loads(r.body)
    rows = _unwrap_rows(payload)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out: list[dict] = []
    for src in rows:
        if not isinstance(src, dict):
            continue
        period = _period_yyyymm(src.get("trade_date", ""))
        if period is None:
            continue
        for field, indicator in _TOTALMONTH_INDICATORS:
            if field not in src:
                continue
            out.append(
                _mk_row(period, indicator, _clean_num(src.get(field)), _TOTALMONTH_UNIT, TOTALMONTH_URL, now_iso)
            )
    return out, TOTALMONTH_URL


async def _fetch_fwmy(s) -> tuple[list[dict], str]:
    """服务贸易历年：GET pageNumber=1..maxPageNum 翻页至空页。"""
    out: list[dict] = []
    now_iso = ""
    page, max_page = 1, None
    while page <= _FWMY_MAX_PAGES and (max_page is None or page <= max_page):
        r = await s.get(FWMY_URL, params={"pageNumber": page}, headers=HDRS)
        if r.status != 200:
            logger.warning("fwmy/overyears HTTP %s（page=%d），停止翻页", r.status, page)
            break
        payload = json.loads(r.body)
        if not isinstance(payload, dict) or "rows" not in payload:
            logger.warning("fwmy/overyears page=%d 返回形态异常，停止翻页", page)
            break
        src_rows = payload.get("rows") or []
        if not src_rows:
            break  # 空页 → 翻页结束
        if not now_iso:
            now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        declared = payload.get("maxPageNum")
        if isinstance(declared, int) and declared > 0:
            max_page = min(declared, _FWMY_MAX_PAGES)
        for src in src_rows:
            if not isinstance(src, dict):
                continue
            year = str(src.get("year", "")).strip()
            if not _Y_RE.match(year):
                continue
            period = f"{year}-12"  # 年度值记当年 12 月期
            for field, indicator, unit in _FWMY_INDICATORS:
                if field not in src:
                    continue
                out.append(_mk_row(period, indicator, _clean_num(src.get(field)), unit, FWMY_URL, now_iso))
        page += 1
    return out, FWMY_URL


async def _fetch_lywz(s) -> tuple[list[dict], str]:
    """利用外资月度：POST pageNumber=1，一次返回全序列（1983-12 起）。"""
    r = await s.post(
        LYWZ_URL,
        data={"pageNumber": 1},
        headers={**HDRS, "Content-Type": "application/x-www-form-urlencoded"},
    )
    if r.status != 200:
        logger.warning("lywz/direct/query HTTP %s，跳过该口径", r.status)
        return [], ""
    payload = json.loads(r.body)
    rows = _unwrap_rows(payload)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out: list[dict] = []
    for src in rows:
        if not isinstance(src, dict):
            continue
        period = _period_yyyymm(src.get("data_time", ""))
        if period is None:
            continue
        for field, indicator, unit in _LYWZ_INDICATORS:
            if field not in src:
                continue
            out.append(_mk_row(period, indicator, _clean_num(src.get(field)), unit, LYWZ_URL, now_iso))
    return out, LYWZ_URL


async def _collect() -> list[dict]:
    """三口径顺序采集；单口径失败记日志后跳过（不重试、不绕反爬）。"""
    out: list[dict] = []
    async with FetcherSession(impersonate="chrome120", timeout=25, verify=False) as s:
        for name, fetcher in (
            ("货物进出口月度", _fetch_totalmonth),
            ("服务贸易历年", _fetch_fwmy),
            ("利用外资月度", _fetch_lywz),
        ):
            try:
                rows, url = await fetcher(s)
                logger.info("%s: %d 行 (%s)", name, len(rows), url)
                out.extend(rows)
            except Exception as e:  # noqa: BLE001 — 单口径故障不拖垮其余口径
                logger.warning("%s 采集失败，跳过: %s", name, e)
    # period 降序（新→旧），同期内保持指标白名单顺序（sort 稳定）。
    out.sort(key=lambda r: r["period"], reverse=True)
    return out


def run_mofcom_data(limit: int = 100) -> list[dict]:
    """fd-runner 唯一入口：商务部数据中心三口径合并输出。"""
    rows = asyncio.run(_collect())
    if limit is not None and limit >= 0:
        rows = rows[:limit]
    return rows


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for row in run_mofcom_data(limit=10):
        print(json.dumps(row, ensure_ascii=False))
