"""spiders/clinicaltrials —— ClinicalTrials.gov 全球临床试验**注册月度量**取数。

数据表面（公开 REST v2，免 key；仅标准库）：

- 月度量： ``GET https://clinicaltrials.gov/api/v2/studies?countTotal=true``
  ``&filter.advanced=AREA[StudyFirstPostDate]RANGE[<首日>,<末日>]&pageSize=1``
  → ``totalCount`` = 该月注册（首发帖）量，一次请求整月。

口径坑位（brief.notes + 实测，逐一落实）：

- **filter 参数名**：工单字面 URL 的 ``filter.area=StudyFirstPostDate RANGE[...]`` 实测
  400 ``filter.area is unknown parameter``；v2 正确写法是
  ``filter.advanced=AREA[StudyFirstPostDate]RANGE[起,止]``（方括号/逗号需 URL 编码），
  2020-01 实测 totalCount=2867、2024-01=3776（与 brief 侦察簿口径一致）。
- 数据近实时（实测 2026-10 时 2026-09 完整月即有量），缺省窗口 = 截至上个完整自然月的
  近 ``limit`` 个月，无 openFDA 式滞后问题。
- 历史深度 2000 起；历史月 totalCount 冻结 → golden 锚固定历史月 total。
- 容错：单月失败只跳过并留痕；**全部月份失败抛 ``RuntimeError``**（失败即红）。

工单：``reports/health-tickets/20261006-clinicaltrials-941ede99.yaml``（``kind=generate``）
"""
from __future__ import annotations

import calendar
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

SOURCE = "clinicaltrials"
STUDIES_URL = "https://clinicaltrials.gov/api/v2/studies"

DEFAULT_MONTHS = 24  # 缺省取截至上个完整自然月的近 24 个月，与 monthly 节奏对齐
TIMEOUT = 30  # 秒；countTotal 轻量响应（pageSize=1），超时即跳过该月


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "fd-industry-data/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _month_window(end_year: int, end_month: int, months: int) -> list[tuple[int, int]]:
    """``(端年, 端月)`` 倒推 ``months`` 个自然月（含端点），按时间正序返回。"""
    out: list[tuple[int, int]] = []
    y, m = end_year, end_month
    for _ in range(months):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(out)


def _default_end() -> tuple[int, int]:
    """缺省端点：上个完整自然月（UTC）。"""
    today = datetime.now(timezone.utc).date()
    return (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)


def _parse_month(text: str) -> tuple[int, int]:
    parts = text.split("-")
    if len(parts) != 2:
        raise ValueError(f"month must be yyyy-mm, got {text!r}")
    year, month = int(parts[0]), int(parts[1])
    if not 1 <= month <= 12:
        raise ValueError(f"month out of range: {text!r}")
    return year, month


def fetch_month(year: int, month: int) -> dict:
    """取一个自然月的注册总量 + 抽样研究行（RANGE 含首尾）。"""
    last_day = calendar.monthrange(year, month)[1]
    rng = f"{year:04d}-{month:02d}-01,{year:04d}-{month:02d}-{last_day:02d}"
    url = (
        f"{STUDIES_URL}?countTotal=true"
        f"&filter.advanced=AREA%5BStudyFirstPostDate%5DRANGE%5B{rng}%5D&pageSize=1"
    )
    try:
        payload = _get_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # v2 对 0 结果返回 404：该月 0 注册是真实语义
            payload = {"totalCount": 0, "studies": []}
        else:
            raise
    total = payload.get("totalCount") if isinstance(payload, dict) else None
    if total is None:
        raise ValueError(f"{year:04d}-{month:02d}: unexpected payload (no totalCount)")
    studies = payload.get("studies") or []
    sample_id = None
    if isinstance(studies, list) and studies:
        ident = (studies[0].get("protocolSection") or {}).get("identificationModule") or {}
        sample_id = ident.get("nctId")
    return {
        "month": f"{year:04d}-{month:02d}",
        "total_count": int(total),
        "sample_nct_id": sample_id,
        "url": url,
        "source": SOURCE,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def run_clinicaltrials(limit: int = 24, start_month: str | None = None,
                       end_month: str | None = None) -> list[dict]:
    """取 ``limit`` 个自然月的注册月度量行；全部月份失败 → 抛 ``RuntimeError``（失败即红）。

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
        months = _month_window(*_default_end(), limit)

    rows: list[dict] = []
    errors: list[str] = []
    for year, month in months:
        try:
            rows.append(fetch_month(year, month))
        except Exception as exc:  # noqa: BLE001 - 单月失败只跳过并留痕
            errors.append(f"{year:04d}-{month:02d}: {type(exc).__name__}: {exc}")
    if not rows:
        raise RuntimeError("clinicaltrials: 全部月份取数失败 — " + "; ".join(errors)[:300])
    return rows


if __name__ == "__main__":  # 单次冒烟：python3 spiders/clinicaltrials/spider.py
    print(json.dumps(run_clinicaltrials(limit=3), ensure_ascii=False, indent=2))
