"""spiders/gleif-lei —— GLEIF 全球新设法人（LEI 注册）**月度序列**取数。

数据表面（公开 REST，免 key；GLEIF API v1，见工单 brief.notes「侦察簿 W1-F」）：

- 月度计数： ``GET https://api.gleif.org/api/v1/lei-records?page[size]=1&filter[registration.initialRegistrationDate]=<月初>..<月末>``
  → ``meta.pagination.total`` 即该月新注册 LEI 总量。
- 可选拆分： 追加 ``filter[entity.legalAddress.country]=<ISO 国家码>`` 得单国序列
  （``country`` 参数）。

口坑（brief.notes / 实测踩坑，逐一落实）：

- 工单示例的嵌套过滤 ``filter[registration][initialRegistrationDate]`` **实测 400**
  （"Filter should contain only allowed values"）；实测有效形态是**点号平铺**
  ``filter[registration.initialRegistrationDate]=YYYY-MM-DD..YYYY-MM-DD``，日期区间
  ``..`` 语法 GLEIF 实测支持。
- ``page[size]`` 上限 200；本单元只要计数，固定 ``page[size]=1`` 请求最轻。
- 行字段 ``month`` / ``lei_count``（``meta.pagination`` 总数）；可选拆
  ``entity.legalAddress.country``（``country`` 参数，全局行该列为 ``None``）。
- ``limit`` 语义 = 返回月份数：序列从**固定锚月 2020-01**（brief 指定）按月推进到
  最近一个**已完结月**（UTC），取前 ``limit`` 个月——``limit=1`` 的 golden 重放恒为
  2020-01（站点、URL、锚月均不变）。
- 容错：单月失败只跳过该月并留痕；**全部月份失败则抛 ``RuntimeError``**（失败即红，
  不静默返回空列表，避免「空洞通过」）。

工单：``reports/health-tickets/20261006-gleif-lei-b66a12c1.yaml``（``kind=generate``）
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

SOURCE = "gleif-lei"
API_URL = "https://api.gleif.org/api/v1/lei-records"

# 固定锚月：工单 brief.source_urls 指定的历史月（golden 只锚它，跨期稳定）
SEED_MONTH = "2020-01"

HEADERS = {
    "User-Agent": "fd-industry-data/1.0",
    "Accept": "application/vnd.api+json",
}
TIMEOUT = 30  # 秒；上游为轻量 JSON 计数，超时即跳过该月（不重试、不加频次）
REQUEST_GAP = 0.25  # 秒；匿名公开 API，串行低频不并发


def _get_json(url: str):
    """GET 一个 JSON API 端点（仅固定 https 主机；无鉴权、无重试放大）。"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        return json.loads(resp.read().decode("utf-8", "replace"))


def _month_bounds(month: str) -> tuple[str, str]:
    """'YYYY-MM' -> (月初 'YYYY-MM-01', 月末 'YYYY-MM-DD')。"""
    year, mon = int(month[:4]), int(month[5:7])
    if mon == 12:
        last_day = 31
    else:
        last_day = (date(year, mon + 1, 1) - date(year, mon, 1)).days
    return f"{month}-01", f"{month}-{last_day:02d}"


def _next_month(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    if mon == 12:
        return f"{year + 1}-01"
    return f"{year}-{mon + 1:02d}"


def _last_complete_month() -> str:
    """UTC 上一整月（当月未完结，不计入序列）。"""
    today = datetime.now(timezone.utc).date()
    first_of_month = today.replace(day=1)
    if first_of_month.month == 1:
        return f"{first_of_month.year - 1}-12"
    return f"{first_of_month.year}-{first_of_month.month - 1:02d}"


def _norm_country(country: str | None) -> str | None:
    """国家码归一：空串 → None，其余大写去空白（GLEIF 要求 ISO 3166-1 alpha-2）。"""
    if country is None:
        return None
    code = str(country).strip().upper()
    return code or None


def fetch_month(month: str, country: str | None = None) -> dict:
    """取单月新注册 LEI 总量，返回一行。"""
    first, last = _month_bounds(month)
    params: dict = {"page[size]": 1}
    filters = [f"registration.initialRegistrationDate:{first}..{last}"]
    cc = _norm_country(country)
    if cc:
        filters.append(f"entity.legalAddress.country:{cc}")
    for f in filters:
        name, _, value = f.partition(":")
        params[f"filter[{name}]"] = value
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    payload = _get_json(url)
    pagination = (payload.get("meta") or {}).get("pagination") if isinstance(payload, dict) else None
    total = pagination.get("total") if isinstance(pagination, dict) else None
    if not isinstance(total, int):
        raise ValueError(f"month {month}: unexpected payload (no meta.pagination.total)")
    return {
        "month": month,
        "country": cc,
        "lei_count": total,
        "url": url,
        "source": SOURCE,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def run_gleif_lei(limit: int = 100, country: str | None = None) -> list[dict]:
    """取自固定锚月 2020-01 起 ``limit`` 个月的全球（或 ``country`` 单国）新设法人
    序列；全部月份失败 → 抛 ``RuntimeError``（失败即红）。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    months: list[str] = []
    cur, end = SEED_MONTH, _last_complete_month()
    while cur <= end and len(months) < limit:
        months.append(cur)
        cur = _next_month(cur)

    rows: list[dict] = []
    errors: list[str] = []
    for i, month in enumerate(months):
        if i:
            time.sleep(REQUEST_GAP)  # 串行低频，不并发
        try:
            rows.append(fetch_month(month, country))
        except Exception as exc:  # noqa: BLE001 - 单月失败只跳过并留痕
            errors.append(f"{month}: {type(exc).__name__}: {exc}")
    if not rows:
        raise RuntimeError("gleif-lei: 全部月份取数失败 — " + "; ".join(errors)[:300])
    return rows[:limit]


if __name__ == "__main__":  # 单月冒烟：python3 spiders/gleif-lei/spider.py
    print(json.dumps(run_gleif_lei(limit=1), ensure_ascii=False, indent=2))
