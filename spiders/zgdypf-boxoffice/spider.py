#!/usr/bin/env python3
"""zgdypf-boxoffice — 国家电影专资办 电影票房数据中心 (zgdypw.cn).

数据源（免鉴权直连，国内源禁走代理）:
- 日榜  GET https://zgdypf.zgdypw.cn/getDayData?withSvcFee=1&date=YYYY-MM-DD&dateType=0
- 周榜  GET https://zgdypf.zgdypw.cn/getPeriodData?withSvcFee=1&date=YYYY-MM-DD&dateType=1
  （周榜 date 必须锚定周一，否则返回 {"list":[]}）

口径: 含服务费（withSvcFee=1，与大盘 nationalSales.salesDesc 同口径）；
分账口径单列 box_office_split_wan 供对照（两端口径差约 0.7%）。

容错（硬约束）:
- HTTP 204 / 空响应（当日无数据）→ 记录后跳过，不报错不重试；
- 5xx / 非 JSON → 记录后跳过，不连环重试、不绕反爬；
- "<0.1" 字符串值 → 统一清洗为区间中值 0.05（万元），绝不写脏 float；
  清洗失败该数值置 None，解析失败整行不写。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone

from scrapling.fetchers import FetcherSession

logger = logging.getLogger("zgdypf-boxoffice")

BASE_URL = "https://zgdypf.zgdypw.cn"
DAY_URL = f"{BASE_URL}/getDayData"
WEEK_URL = f"{BASE_URL}/getPeriodData"

# 参数白名单（实测有效，多余参数一律不发）:
#   日榜: withSvcFee=1, date=YYYY-MM-DD, dateType=0
#   周榜: withSvcFee=1, date=YYYY-MM-DD(必须周一), dateType=1
DAY_QUERY = "withSvcFee=1&date={day}&dateType=0"
WEEK_QUERY = "withSvcFee=1&date={day}&dateType=1"

HDRS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://zgdypf.zgdypw.cn/",
    "Accept": "application/json, text/plain, */*",
}

TIMEOUT = 25
CST = timezone(timedelta(hours=8))

DEFAULT_DAYS = 3   # 稳态：今日+昨日+营业日 catch-up（首爬 370 天回补已于 2026-10-02 完成并留痕 crawl_runs #724）
DEFAULT_WEEKS = 2  # 稳态：本周+上周（周一锚定）


def _clean_num(raw) -> float | None:
    """统一数值清洗。

    - "<0.1" 这类下界字符串 → 取区间中值 0.05（万元），绝不写脏 float；
    - 空/None/解析失败 → None（该数值缺失，不造假）；
    - 千分位逗号剔除后转 float。
    """
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if not s:
        return None
    if s.startswith("<"):
        try:
            bound = float(s[1:])
        except ValueError:
            return None
        return round(bound / 2, 4)
    try:
        return float(s)
    except ValueError:
        return None


def _national_to_wan(sales_desc: dict | None) -> float | None:
    """nationalSales.salesDesc {unit: 亿|万, value: str} → 万元 float。"""
    if not isinstance(sales_desc, dict):
        return None
    value = _clean_num(sales_desc.get("value"))
    if value is None:
        return None
    unit = str(sales_desc.get("unit") or "").strip()
    if unit == "亿":
        return round(value * 10000, 2)
    if unit == "万":
        return value
    logger.warning("unknown nationalSales unit %r, skip", unit)
    return None


def _cst_today() -> date:
    return datetime.now(CST).date()


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _now_iso() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def _base_row(period: str, scope: str, source_url: str) -> dict:
    return {
        "period": period,
        "scope": scope,
        "film_code": "",
        "film_name": "",
        "ranking": 0,
        "box_office_wan": None,
        "box_office_split_wan": None,
        "box_office_rate": "",
        "session_rate": "",
        "seat_rate": "",
        "online_sales_rate": "",
        "release_days": None,
        "total_sales_desc": "",
        "avg_price": None,
        "avg_attendance": None,
        "release_date": "",
        "scraped_at": _now_iso(),
        "source_url": source_url,
    }


def _day_rows(payload: dict, day: str, source_url: str) -> list[dict]:
    """日榜 payload → [大盘行, 影片行...]。nationalSales 缺失时仅返回影片行。"""
    rows: list[dict] = []
    national = payload.get("nationalSales") or {}
    total_wan = _national_to_wan(national.get("salesDesc"))
    split_wan = _national_to_wan(national.get("splitSalesDesc"))
    if total_wan is not None:
        r = _base_row(day, "day", source_url)
        r.update(film_name="大盘", box_office_wan=total_wan, box_office_split_wan=split_wan)
        rows.append(r)
    else:
        logger.warning("day %s: nationalSales missing/unparsable, films only", day)

    for i, item in enumerate(payload.get("list") or [], start=1):
        name = str(item.get("name") or "").strip()
        box = _clean_num(item.get("salesInWanDesc"))
        if not name or box is None:
            logger.warning("day %s: skip unparsable film row #%d", day, i)
            continue
        r = _base_row(day, "day", source_url)
        r.update(
            film_code=str(item.get("code") or ""),
            film_name=name,
            ranking=int(item.get("rank") or i),
            box_office_wan=box,
            box_office_split_wan=_clean_num(item.get("splitSalesInWanDesc")),
            box_office_rate=str(item.get("salesRateDesc") or ""),
            session_rate=str(item.get("sessionRateDesc") or ""),
            seat_rate=str(item.get("seatRateDesc") or ""),
            online_sales_rate=str(item.get("onlineSalesRateDesc") or ""),
            release_days=int(item["releaseDays"]) if item.get("releaseDays") is not None else None,
            total_sales_desc=str(item.get("sumSalesDesc") or ""),
        )
        rows.append(r)
    return rows


def _week_rows(payload: dict, monday: str, source_url: str) -> list[dict]:
    """周榜 payload → [大盘行, 影片行...]。period=周一锚定日。"""
    rows: list[dict] = []
    national = payload.get("nationalSales") or {}
    total_wan = _national_to_wan(national.get("salesDesc"))
    split_wan = _national_to_wan(national.get("splitSalesDesc"))
    if total_wan is not None:
        r = _base_row(monday, "week", source_url)
        r.update(film_name="大盘", box_office_wan=total_wan, box_office_split_wan=split_wan)
        rows.append(r)

    for i, item in enumerate(payload.get("list") or [], start=1):
        name = str(item.get("name") or "").strip()
        box = _clean_num(item.get("salesInWanDesc"))
        if not name or box is None:
            logger.warning("week %s: skip unparsable film row #%d", monday, i)
            continue
        r = _base_row(monday, "week", source_url)
        r.update(
            film_code=str(item.get("code") or ""),
            film_name=name,
            ranking=i,
            box_office_wan=box,
            box_office_split_wan=_clean_num(item.get("splitSalesInWanDesc")),
            box_office_rate=str(item.get("salesRateDesc") or ""),
            avg_price=_clean_num(item.get("avgPrice")),
            avg_attendance=_clean_num(item.get("avgSalesCount")),
            release_date=str(item.get("releaseDate") or ""),
        )
        rows.append(r)
    return rows


async def _fetch_json(session, url: str) -> dict | None:
    """GET → JSON dict；204/空响应、5xx、非 JSON 一律记日志后返回 None（跳过，不重试）。"""
    try:
        resp = await session.get(url, headers=HDRS)
    except Exception as exc:  # 网络/引擎异常：记录后跳过
        logger.warning("skip %s: %s", url, exc)
        return None
    body = resp.body or b""
    if resp.status == 204 or not body.strip():
        logger.info("no data (HTTP %s): %s", resp.status, url)
        return None
    if resp.status != 200:
        logger.warning("skip (HTTP %s): %s", resp.status, url)
        return None
    try:
        return json.loads(body)
    except ValueError:
        logger.warning("skip (non-JSON body): %s", url)
        return None


async def _collect(days: int, weeks: int, end: date) -> list[dict]:
    rows: list[dict] = []
    async with FetcherSession(impersonate="chrome120", timeout=TIMEOUT, verify=False) as s:
        for i in range(days):
            d = end - timedelta(days=i)
            url = f"{DAY_URL}?{DAY_QUERY.format(day=d.isoformat())}"
            payload = await _fetch_json(s, url)
            if payload:
                rows.extend(_day_rows(payload, d.isoformat(), url))
        for i in range(weeks):
            m = _monday_of(end) - timedelta(weeks=i)
            url = f"{WEEK_URL}?{WEEK_QUERY.format(day=m.isoformat())}"
            payload = await _fetch_json(s, url)
            if payload:
                rows.extend(_week_rows(payload, m.isoformat(), url))
    return rows


def run_zgdypf_boxoffice(
    limit: int = 100,
    days: int = DEFAULT_DAYS,
    weeks: int = DEFAULT_WEEKS,
    end_date: str | None = None,
) -> list[dict]:
    """fd-runner 入口：返回日榜（含大盘行）+ 周榜行，≤limit 条。

    days/weeks/end_date 为内部辅助参数（稳态默认 days=3, weeks=2，从今天回看；
    首爬回补曾用 370/53 并留痕 crawl_runs #724）；周榜日期内部强制锚定周一，
    绝不发送白名单之外的参数。
    """
    end = date.fromisoformat(end_date) if end_date else _cst_today()
    rows = asyncio.run(_collect(max(0, int(days)), max(0, int(weeks)), end))
    return rows[: max(0, int(limit))]
