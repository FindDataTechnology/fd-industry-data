"""usaspending-awards — USAspending 联邦合同按 NAICS 行业月度聚合（信号层）。

数据源：USAspending.gov 公共检索 API（无 key、无验证码/签名墙）：

- 取数端点：``POST /api/v2/search/spending_by_award/``（逐单 award 行，按
  时间窗 + award_type_codes 过滤，单页上限 100，``page_metadata.hasNext``
  翻页）。实测 200 可达，无需认证。
- ``GET /api/v2/references/filter_tree/naics/`` 目前 404，本单元不依赖它：
  每行 award 自带 ``NAICS.code``，聚合在客户端完成。

为什么是聚合层：工单 brief 明确「只做聚合信号层，不做逐单明细」。本单元把
monthly 窗口内的头部 award（每 award_type × 每月至多 ``MAX_PAGES_PER_BATCH``
页，按 Award Amount 降序）在客户端按 ``(award_type, naics_code,
agency_name)`` 聚合，产出 月度 × 行业 × 部门 粒度的 obligated_amount 信号行。
翻页有上限，金额为窗口内头部 award 的部分和——README 有如实说明。

窗口：入口显式声明 ``window_start``/``window_end``（kwargs 注入；runner 亦经
``FD_WINDOW_START``/``FD_WINDOW_END`` 环境变量暴露），缺省 = 上一个完整自然
月。窗口过滤先于 limit 截断（templates/new-source/CHECKLIST.md 回填口径）。

节流与 robots 复用 ``fd_industry_data.http``（throttle / allowed_by_robots），
不自建频次控制。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

from fd_industry_data.http import allowed_by_robots, throttle

__all__ = ["run_usaspending_awards"]

SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
USER_AGENT = "FindDataIndustryBot/1.0 (+https://www.finddatatech.cloud)"

AWARD_TYPE_CODES = ("A", "B", "C", "D")  # 合同类 award_type_codes（契约检索口径）
FIELDS = ["Award ID", "Award Amount", "Awarding Agency", "NAICS"]
PAGE_SIZE = 100  # 端点单页上限（实测）
MAX_PAGES_PER_BATCH = 3  # 每 award_type × 每月页数上限——信号层取头部，不做全量翻页
MAX_MONTHS = 12  # 窗口最多展开 12 个整月（与首爬回补的滚动 12 整月对齐）
REQUEST_TIMEOUT_S = 60.0
REQUEST_RETRIES = 2  # 瞬时网络错误重试次数（幂等检索 POST）


def _post_page(body: dict) -> dict:
    """POST 一页检索请求，返回 JSON；瞬时错误重试后仍失败则抛 RuntimeError。"""
    data = json.dumps(body).encode("utf-8")
    last_exc: Exception | None = None
    for attempt in range(REQUEST_RETRIES):
        try:
            req = urllib.request.Request(
                SEARCH_URL,
                data=data,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                },
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_exc = exc
            time.sleep(1.0)
    raise RuntimeError(f"usaspending search request failed: {last_exc}")


def _parse_day(value: str) -> date:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(
        f"unparseable window date: {value!r} (expected YYYY-MM-DD or YYYY-MM)"
    )


def _month_first(d: date) -> date:
    return d.replace(day=1)


def _add_months(d: date, n: int) -> date:
    total = d.month - 1 + n
    return d.replace(year=d.year + total // 12, month=total % 12 + 1, day=1)


def _month_buckets(
    window_start: str | None, window_end: str | None
) -> list[tuple[str, str, str]]:
    """把窗口展开成整月桶 ``[(period, first_day, last_day), ...]``，旧→新。

    缺省窗口 = 上一个完整自然月；窗口过滤先于任何截断（回填口径）。
    """
    today = date.today()
    if window_start and window_end:
        start, end = _parse_day(window_start), _parse_day(window_end)
    elif window_start or window_end:
        raise ValueError("window_start/window_end must be provided together")
    else:
        end = _add_months(_month_first(today), -1)  # 上个完整自然月的 1 号
        start = end
    if start > end:
        raise ValueError(f"window_start {start} is after window_end {end}")
    buckets: list[tuple[str, str, str]] = []
    cursor = _month_first(start)
    last_month = _month_first(end)
    while cursor <= last_month and len(buckets) < MAX_MONTHS:
        bucket_end = min(end, _add_months(cursor, 1) - timedelta(days=1))
        buckets.append(
            (cursor.strftime("%Y-%m"), cursor.isoformat(), bucket_end.isoformat())
        )
        cursor = _add_months(cursor, 1)
    return buckets


def _aggregate_batch(
    award_type: str, start: str, end: str, period: str
) -> list[dict]:
    """取一个 (award_type, 整月) 批次并聚合成信号行，按 (naics, agency) 稳定排序。"""
    agg: dict[tuple[str, str], dict] = {}
    seen: set[str] = set()
    gate = time.monotonic()
    for page in range(1, MAX_PAGES_PER_BATCH + 1):
        gate = throttle(gate)
        payload = _post_page(
            {
                "filters": {
                    "time_period": [{"start_date": start, "end_date": end}],
                    "award_type_codes": [award_type],
                },
                "fields": FIELDS,
                "limit": PAGE_SIZE,
                "page": page,
                "sort": "Award Amount",
                "order": "desc",
                "subawards": False,
            }
        )
        results = payload.get("results") or []
        for award in results:
            if not isinstance(award, dict):
                continue
            award_id = str(award.get("Award ID") or "")
            if not award_id or award_id in seen:
                continue  # 翻页窗口漂移去重
            seen.add(award_id)
            naics = award.get("NAICS") if isinstance(award.get("NAICS"), dict) else {}
            naics_code = str(naics.get("code") or "")
            agency = str(award.get("Awarding Agency") or "")
            try:
                amount = float(award.get("Award Amount") or 0.0)
            except (TypeError, ValueError):
                amount = 0.0
            key = (naics_code, agency)
            bucket = agg.setdefault(
                key, {"obligated_amount": 0.0, "award_count": 0}
            )
            bucket["obligated_amount"] += amount
            bucket["award_count"] += 1
        metadata = payload.get("page_metadata") or {}
        if not metadata.get("hasNext") or not results:
            break
    return [
        {
            "award_type": award_type,
            "naics_code": naics_code,
            "agency_name": agency,
            "obligated_amount": round(bucket["obligated_amount"], 2),
            "award_count": bucket["award_count"],
            "period": period,
            "url": SEARCH_URL,
            "source": "usaspending-awards",
        }
        for (naics_code, agency), bucket in sorted(agg.items())
    ]


def run_usaspending_awards(
    limit: int = 100,
    window_start: str | None = None,
    window_end: str | None = None,
) -> list[dict]:
    """USAspending 联邦合同 月度×NAICS×部门 聚合信号行。

    缺省窗口为上一个完整自然月；``limit`` 在窗口过滤之后截断（保留窗口内
    最旧月份起的行，水位追加友好）。
    """
    if not allowed_by_robots(SEARCH_URL, user_agent=USER_AGENT):
        raise RuntimeError(f"robots.txt disallows {SEARCH_URL}")
    window_start = window_start or os.environ.get("FD_WINDOW_START") or None
    window_end = window_end or os.environ.get("FD_WINDOW_END") or None
    months = _month_buckets(window_start, window_end)
    rows: list[dict] = []
    for period, start, end in months:
        for award_type in AWARD_TYPE_CODES:
            rows.extend(_aggregate_batch(award_type, start, end, period))
    if limit is not None:
        rows = rows[: max(int(limit), 0)]
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for row in rows:
        row["scraped_at"] = scraped_at
    return rows
