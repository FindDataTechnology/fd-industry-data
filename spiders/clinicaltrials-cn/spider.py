"""spiders/clinicaltrials-cn —— ClinicalTrials.gov 上**中国境内**临床试验取数。

数据表面（公开 REST v2，免 key，仅标准库）：

- 月度窗口： ``GET https://clinicaltrials.gov/api/v2/studies``
  ``filter.advanced=AREA[StartDate]RANGE[<起>,<止>] AND AREA[LocationCountry]China``
  → ``totalCount`` = 该自然月起始的中国境内试验数；``studies[]`` = 明细。

为什么用这个面而不是国内注册平台（2026-11-11 实测，见
``openspec/changes/establish-pharma-domain-stack/SOURCE-FEASIBILITY.md``）：

- ChiCTR（中国临床试验注册中心）与药审中心的「药物临床试验登记与信息公示平台」
  首页可访问，但**数据接口被 WAF 阻断**：ChiCTR 搜索 405（响应体写明
  「访问被阻断」并给请求 ID）、chinadrugtrials 列表 202。真实 Chromium 亦被拦，
  说明不是可自动通过的 JS 挑战而是主动拦截；按 change 批准书不得绕过。
- ClinicalTrials.gov v2 无防护、有分页与 ``countTotal``，且中国境内试验是
  可逐条核对的确定集合（2026-11-11 实测 ``LocationCountry=China`` 全库 56,051 条）。

口径坑位（实测，逐一落实）：

- **``filter.advanced`` 里的日期区间必须写成 ``RANGE[起,止]``**，两端都含；按
  ``StartDate``（试验开始日期）切月，不是登记日期——登记日期在 v2 里是
  ``StudyFirstPostDate``，两者不同，本单元用 StartDate 与「指标族=按月」的口径一致。
- **``sort=NCTId:asc`` 不被接受**（400 ``Unsupported sort field type``），所以
  「抽样编号」不能靠排序取首行；改为**取该月 NCT 编号数值最小者**——NCT 号按提交
  顺序递增，历史月份的最小号不会因后续新增而改变，是稳定抽样。
- **月度计数会被上游回补**：CT.gov 允许补登记过去开始的试验，历史月 ``totalCount``
  会缓慢增长，因此 golden **不断言计数相等**，只断言``抽样编号``与该月行存在；
  计数作为列照常落库，供基线与趋势使用。
- 单月失败只跳过并留痕；**全部月份失败抛 ``RuntimeError``**（失败即红）。
- 非价格面：本单元不含任何金额列（用户裁决「价格/财报数据停爬」）。

工单：``openspec/changes/establish-pharma-domain-stack``（task 2.2）
"""
from __future__ import annotations

import calendar
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SOURCE = "clinicaltrials-cn"
API_URL = "https://clinicaltrials.gov/api/v2/studies"
PAGE_SIZE = 1000  # v2 上限；一个月通常 <500 条，一页足够
TIMEOUT = 40  # 秒
FIELDS = ",".join(
    (
        "NCTId",
        "BriefTitle",
        "OverallStatus",
        "StartDate",
        "Phase",
        "LeadSponsorName",
        "EnrollmentCount",
        "StudyFirstPostDate",
    )
)
_NCT_NUM = re.compile(r"\D")


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "fd-industry-data/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        return json.loads(resp.read().decode("utf-8", "replace"))


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    last = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last:02d}"


def _row(study: dict, month: str, url: str) -> dict | None:
    """把一条 ``studies[]`` 折成一行；缺注册号的记录跳过（不可寻址）。"""
    ps = (study or {}).get("protocolSection") or {}
    nct = (ps.get("identificationModule") or {}).get("nctId")
    if not nct:
        return None
    status = ps.get("statusModule") or {}
    design = ps.get("designModule") or {}
    sponsor = (ps.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {}
    phases = design.get("phases") or []
    enrollment = (design.get("enrollmentInfo") or {}).get("count")
    return {
        "month": month,
        "nct_id": nct,
        "brief_title": (ps.get("identificationModule") or {}).get("briefTitle"),
        "overall_status": status.get("overallStatus"),
        "start_date": (status.get("startDateStruct") or {}).get("date"),
        "first_post_date": (status.get("studyFirstPostDateStruct") or {}).get("date"),
        "phase": ",".join(phases) if isinstance(phases, list) else None,
        "lead_sponsor": sponsor.get("name"),
        "enrollment_count": int(enrollment) if isinstance(enrollment, (int, float)) else None,
        "url": url,
        "source": SOURCE,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_month(year: int, month: int) -> list[dict]:
    """取一个自然月起始的中国境内试验明细行（含分页）。

    该月无记录返回 ``[]``（真实存在的空月，不是错误）；网络/协议错误向上抛。
    """
    start, end = _month_bounds(year, month)
    label = f"{year:04d}-{month:02d}"
    advanced = f"AREA[StartDate]RANGE[{start},{end}] AND AREA[LocationCountry]China"
    rows: list[dict] = []
    token: str | None = None
    while True:
        params = {
            "filter.advanced": advanced,
            "pageSize": PAGE_SIZE,
            "countTotal": "true",
            "fields": FIELDS,
        }
        if token:
            params["pageToken"] = token
        url = f"{API_URL}?{urllib.parse.urlencode(params)}"
        try:
            payload = _get_json(url)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"{label}: HTTP {exc.code}") from None
        for study in payload.get("studies") or []:
            row = _row(study, label, url)
            if row is not None:
                rows.append(row)
        token = payload.get("nextPageToken")
        if not token:
            return rows


def _parse_month(text: str) -> tuple[int, int]:
    parts = str(text).split("-")
    if len(parts) != 2:
        raise ValueError(f"month must be yyyy-mm, got {text!r}")
    year, month = int(parts[0]), int(parts[1])
    if not 1 <= month <= 12:
        raise ValueError(f"month out of range: {text!r}")
    return year, month


def _window(end_year: int, end_month: int, months: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    y, m = end_year, end_month
    for _ in range(months):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(out)


def run_clinicaltrials_cn(limit: int = 12, start_month: str | None = None,
                          end_month: str | None = None) -> list[dict]:
    """取中国境内临床试验明细行（默认截至上个完整自然月的近 ``limit`` 个月）。

    ``start_month/end_month``（``yyyy-mm``）钉死历史窗口供 golden 回放；
    全部月份失败 → 抛 ``RuntimeError``（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    if start_month and end_month:
        sy, sm = _parse_month(start_month)
        ey, em = _parse_month(end_month)
        span = (ey - sy) * 12 + (em - sm) + 1
        if span <= 0:
            raise ValueError(f"empty window: {start_month!r}..{end_month!r}")
        months = _window(ey, em, span)[:limit]
    else:
        today = datetime.now(timezone.utc).date()
        ey, em = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        months = _window(ey, em, limit)

    rows: list[dict] = []
    errors: list[str] = []
    for year, month in months:
        try:
            rows.extend(fetch_month(year, month))
        except Exception as exc:  # noqa: BLE001 - 单月失败只跳过并留痕
            errors.append(f"{year:04d}-{month:02d}: {type(exc).__name__}: {exc}"[:200])
    if not rows and errors:
        raise RuntimeError("clinicaltrials-cn: 全部月份取数失败 — " + "; ".join(errors)[:300])
    return rows


def sample_nct(rows: list[dict], month: str) -> str | None:
    """该月 NCT 数值最小的注册号（稳定抽样；排序键见模块文档）。"""
    ids = [r["nct_id"] for r in rows if r.get("month") == month and r.get("nct_id")]
    if not ids:
        return None
    return min(ids, key=lambda x: int(_NCT_NUM.sub("", x) or 0))


if __name__ == "__main__":  # 单次冒烟：python3 spiders/clinicaltrials-cn/spider.py
    print(json.dumps(run_clinicaltrials_cn(limit=1)[:3], ensure_ascii=False, indent=2))
