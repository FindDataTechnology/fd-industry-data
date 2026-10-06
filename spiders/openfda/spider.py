"""spiders/openfda —— openFDA 药物不良事件**月度量**取数。

数据表面（公开 REST，免 key 240/min；仅标准库）：

- 月度量： ``GET https://api.fda.gov/drug/event.json?search=receivedate:[<起>+TO+<止>]&limit=1``
  → ``meta.results.total`` = 该 receivedate 区间的事件总量；
  ``results[0]`` = 抽样明细（safetyreportid）。

口径坑位（brief.notes + 实测，逐一落实）：

- **区间必须有方括号**：``search=receivedate:2020-01-01+TO+2020-01-31``（工单字面 URL）
  实测会把 ``TO`` 当噪音静默忽略、返回全库 1394 万条；正确写法是
  ``receivedate:[2020-01-01+TO+2020-01-31]``（URL 编码 ``%5B``/``%5D``），2020-01 实测
  134,689 条（符合 brief「2004 起月度 10 万+」量级）。本单元只发方括号形态。
- 免 key 限 240/min：缺省窗口 24 个月 = 24 请求，远低于限额；不重试、不加频次。
- **未发布月 = 404 无匹配**：drug/event 索引有加载滞后（实测 2026-10 时数据止于
  2026-06），未加载月份 API 返回 404 ``NOT_FOUND``——按「该月尚未发布」**跳过留痕**，
  绝不落 0 行污染正向累积序列（成熟月份必然 10 万+，真实 0 月不存在）。
- 历史深度：2004 年起；成熟月份 total 发布后基本冻结 → golden 锚 2020-01 固定 total。
- ``limit`` 语义 = 返回月份数（截至上个完整自然月、倒推）；golden 用 ``start_month/end_month``
  钉死 2020-01 固定历史月。
- 容错：单月失败只跳过并留痕；**全部月份失败抛 ``RuntimeError``**（失败即红）。
- 审批/器械端点同构可扩（device/event 等），本单元只接 ``drug/event``。

工单：``reports/health-tickets/20261006-openfda-56bbec7f.yaml``（``kind=generate``）
"""
from __future__ import annotations

import calendar
import json
import urllib.error
import urllib.request
from datetime import date, datetime, timezone

SOURCE = "openfda"
EVENT_URL = "https://api.fda.gov/drug/event.json"

DEFAULT_MONTHS = 24  # 缺省取截至上个完整自然月的近 24 个月，与 monthly 节奏对齐
TIMEOUT = 30  # 秒；轻量 JSON（limit=1），超时即跳过该月


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "fd-industry-data/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _month_window(end_year: int, end_month: int, months: int) -> list[tuple[int, int]]:
    """``(起年, 起月)`` 倒推 ``months`` 个自然月（含端点），按时间正序返回。"""
    out: list[tuple[int, int]] = []
    y, m = end_year, end_month
    for _ in range(months):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(out)


def _default_window(months: int) -> tuple[list[tuple[int, int]], list[str]]:
    """缺省窗口：从上个完整自然月起**向后探测**最近一个已发布月（索引有滞后，
    实测滞后 1~4 个月），以它为端点倒推 ``months`` 个月。

    返回 ``(月份清单, 探测留痕)``；探测深度上限 6 个月，全部未发布返回空清单。
    """
    today = datetime.now(timezone.utc).date()
    y, m = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    trail: list[str] = []
    for _ in range(6):
        try:
            fetch_month(y, m)
        except ValueError as exc:  # 未发布月（404 无匹配）→ 再往前探
            trail.append(str(exc))
            m -= 1
            if m == 0:
                y, m = y - 1, 12
            continue
        return _month_window(y, m, months), trail
    return [], trail


def _parse_month(text: str) -> tuple[int, int]:
    parts = text.split("-")
    if len(parts) != 2:
        raise ValueError(f"month must be yyyy-mm, got {text!r}")
    year, month = int(parts[0]), int(parts[1])
    if not 1 <= month <= 12:
        raise ValueError(f"month out of range: {text!r}")
    return year, month


def fetch_month(year: int, month: int) -> dict:
    """取一个自然月的事件总量 + 抽样明细行（方括号区间，含首尾）。

    未发布月（API 404 无匹配）抛 ``ValueError``，由调用方按「跳过留痕」处理。
    """
    last_day = calendar.monthrange(year, month)[1]
    search = (
        f"receivedate:%5B{year:04d}-{month:02d}-01+TO+{year:04d}-{month:02d}-{last_day:02d}%5D"
    )
    url = f"{EVENT_URL}?search={search}&limit=1"
    try:
        payload = _get_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # openFDA「无匹配」= 该月尚未加载进索引，不落 0 行
            raise ValueError(f"{year:04d}-{month:02d}: not published yet (404 no-match)") from None
        raise
    meta = payload.get("meta") if isinstance(payload, dict) else None
    total = (meta or {}).get("results", {}).get("total") if isinstance(meta, dict) else None
    if total is None:
        raise ValueError(f"{year:04d}-{month:02d}: unexpected payload (no meta.results.total)")
    results = payload.get("results") or []
    sample = results[0] if isinstance(results, list) and results else {}
    return {
        "month": f"{year:04d}-{month:02d}",
        "event_count": int(total),
        "sample_report_id": sample.get("safetyreportid"),
        "url": url,
        "source": SOURCE,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def run_openfda(limit: int = 24, start_month: str | None = None,
                end_month: str | None = None) -> list[dict]:
    """取 ``limit`` 个自然月的月度量行；全部月份失败 → 抛 ``RuntimeError``（失败即红）。

    ``start_month/end_month``（``yyyy-mm``）钉死历史窗口（golden 锚固定月 total）；
    缺省为截至上个完整自然月的近 ``limit`` 个月。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    if start_month and end_month:
        sy, sm = _parse_month(str(start_month))
        ey, em = _parse_month(str(end_month))
        span = (ey - sy) * 12 + (em - sm) + 1
        if span <= 0:
            raise ValueError(f"empty window: {start_month!r}..{end_month!r}")
        months = _month_window(ey, em, span)[:limit]
    else:
        months, trail = _default_window(limit)
        if not months:
            raise RuntimeError("openfda: 近 6 个月均未发布（索引滞后异常）— " + "; ".join(trail)[:200])

    rows: list[dict] = []
    errors: list[str] = []
    for year, month in months:
        try:
            rows.append(fetch_month(year, month))
        except Exception as exc:  # noqa: BLE001 - 单月失败只跳过并留痕
            errors.append(f"{year:04d}-{month:02d}: {type(exc).__name__}: {exc}")
    if not rows:
        raise RuntimeError("openfda: 全部月份取数失败 — " + "; ".join(errors)[:300])
    return rows


if __name__ == "__main__":  # 单次冒烟：python3 spiders/openfda/spider.py
    print(json.dumps(run_openfda(limit=3), ensure_ascii=False, indent=2))
