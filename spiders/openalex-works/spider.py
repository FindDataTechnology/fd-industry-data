"""spiders/openalex-works —— OpenAlex 全球科研产出**月度活跃度**取数。

数据表面（公开 REST，免 key；polite pool，见工单 brief.notes「侦察簿 W1-F」）：

- 月度计数： ``GET https://api.openalex.org/works?filter=from_publication_date:<月初>,to_publication_date:<月末>&per-page=1&mailto=<邮箱>``
  → ``meta.count`` 即该月全球 works 总量。

口坑（brief.notes 已点名 / 实测踩坑，逐一落实）：

- 工单示例的 ``filter=publication_date:YYYY-MM-DD..YYYY-MM-DD`` **区间语法实测 400**
  （上游返回 "invalid date"），已改用 OpenAlex 官方区间口径
  ``from_publication_date`` + ``to_publication_date``，并与逐日精确计数求和比对
  一致（2020-01-01..07：区间 count == 逐日 count 之和 == 3068855），口径无注水。
- polite pool：请求带 ``mailto`` 参数（上游建议），限速约 10 req/s——串行低频，
  请求间 sleep 0.12s，不并发。
- ``meta.count`` 是唯一目标载荷：``per-page=1`` 只要计数不要明细，请求最轻。
- ``limit`` 语义 = 返回月份数：序列从**固定锚月 2020-01**（brief 指定）按月推进到
  最近一个**已完结月**（UTC），取前 ``limit`` 个月——``limit=1`` 的 golden 重放恒为
  2020-01（站点、URL、锚月均不变）。
- 容错：单月失败只跳过该月并留痕；**全部月份失败则抛 ``RuntimeError``**（失败即红，
  不静默返回空列表，避免「空洞通过」）。

工单：``reports/health-tickets/20261006-openalex-works-b3312205.yaml``（``kind=generate``）
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

SOURCE = "openalex-works"
WORKS_URL = "https://api.openalex.org/works"

# 固定锚月：工单 brief.source_urls 指定的历史月（golden 只锚它，跨期稳定）
SEED_MONTH = "2020-01"
# polite pool 邮箱（上游建议带 mailto；RFC 2606 保留域，非真实邮箱）
MAILTO = "research@finddata.example"

HEADERS = {
    "User-Agent": f"fd-industry-data/1.0 (mailto:{MAILTO})",
    "Accept": "application/json",
}
TIMEOUT = 30  # 秒；上游为轻量 JSON 计数，超时即跳过该月（不重试、不加频次）
REQUEST_GAP = 0.12  # 秒；约 8 req/s < 上限 10 req/s


def _get_json(url: str):
    """GET 一个 JSON 端点（仅固定 https 主机；无鉴权、无重试放大）。"""
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


def fetch_month(month: str) -> dict:
    """取单月全球 works 总量，返回一行。"""
    first, last = _month_bounds(month)
    params = urllib.parse.urlencode({
        "filter": f"from_publication_date:{first},to_publication_date:{last}",
        "per-page": 1,
        "mailto": MAILTO,
    })
    url = f"{WORKS_URL}?{params}"
    payload = _get_json(url)
    meta = payload.get("meta") if isinstance(payload, dict) else None
    count = meta.get("count") if isinstance(meta, dict) else None
    if not isinstance(count, int):
        raise ValueError(f"month {month}: unexpected payload (no meta.count)")
    return {
        "month": month,
        "works_count": count,
        "url": url,
        "source": SOURCE,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def run_openalex_works(limit: int = 100) -> list[dict]:
    """取自固定锚月 2020-01 起 ``limit`` 个月的全球科研产出序列；
    全部月份失败 → 抛 ``RuntimeError``（失败即红）。"""
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
            time.sleep(REQUEST_GAP)  # 串行低频，低于上游 10 req/s 限速
        try:
            rows.append(fetch_month(month))
        except Exception as exc:  # noqa: BLE001 - 单月失败只跳过并留痕
            errors.append(f"{month}: {type(exc).__name__}: {exc}")
    if not rows:
        raise RuntimeError("openalex-works: 全部月份取数失败 — " + "; ".join(errors)[:300])
    return rows[:limit]


if __name__ == "__main__":  # 单月冒烟：python3 spiders/openalex-works/spider.py
    print(json.dumps(run_openalex_works(limit=1), ensure_ascii=False, indent=2))
