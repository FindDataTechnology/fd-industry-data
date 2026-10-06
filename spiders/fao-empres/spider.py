"""spiders/fao-empres —— FAO EMPRES-i 全球动物疫病事件（WOAH 镜像）。

数据表面（FAO 通用 BigQuery SQL 代理模式，免 key，返回 CSV；见工单 brief.notes「侦察簿 W3」）：

- SQL 代理： ``GET https://api.data.apps.fao.org/api/v2/bigquery?sql_url=<公开 .sql>&<参数>``
- .sql 指向 FAO 数据目录（CKAN）资源正文，路径以目录为准（2026-10-07 实测，
  工单 brief 里的 ``cat-sql/empres-i/public/latest_outbreaks.sql`` 已 404，目录现行资源为
  ``animal-major-disease-parameterized-query.sql``）：
  ``https://data.apps.fao.org/catalog/dataset/3ff164cd-8b44-46d3-8f88-92b0361c7878/resource/137a69a0-ad5f-48c3-b927-3a61d2c9a2ce/download/animal-major-disease-parameterized-query.sql``
- SQL 参数白名单（.sql 内 ``@param``，必须全给，缺一代理即 400）：
  ``start_date`` / ``end_date`` / ``diagnosis_status`` / ``animal_type`` / ``disease`` / ``country``，
  通配值一律 ``all``；本单元只发这 6 个参数，不发其它。

口径坑位（brief.notes 逐条落实）：

- 事件日期 = ``observation_date``，缺失回落 ``report_date``（与 .sql 的 WHERE 口径一致）；
  行模型为（date 月粒度, country, disease）聚合计数：``outbreaks`` = 该格事件数
  （brief「golden 锚固定病种固定年计数」即此口径）。
- 现行公开视图**没有 serotype 列**（17 列全列过：global_id/lat/lon/.../disease/country），
  故行内不产出 serotype，绝不造数——偏差已记 README 与工单回执。
- 历史深度：2023 起可查（2023 全年 1.59 万事件行、2025 全年 2.4 万事件行，实测）。
- 容错：单一数据表面，任何取数/解析失败直接抛 ``RuntimeError``（失败即红，不静默回空）。

工单：``reports/health-tickets/20261006-fao-empres-2fd55009.yaml``（``kind=generate``）
"""
from __future__ import annotations

import csv
import io
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone

SOURCE = "fao-empres"
PROXY_URL = "https://api.data.apps.fao.org/api/v2/bigquery"
SQL_URL = (
    "https://data.apps.fao.org/catalog/dataset/3ff164cd-8b44-46d3-8f88-92b0361c7878"
    "/resource/137a69a0-ad5f-48c3-b927-3a61d2c9a2ce"
    "/download/animal-major-disease-parameterized-query.sql"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv, text/plain, */*",
}
TIMEOUT = 120  # 秒；全年窗口 CSV 可达数 MB，给足超时
DEFAULT_DAYS = 30  # 缺省窗口：最近 30 天（weekly 节奏一窗足量）
PARAM_NAMES = ("start_date", "end_date", "diagnosis_status", "animal_type", "disease", "country")


def _fetch_csv(start_date: str, end_date: str) -> list[dict]:
    """调 FAO BigQuery SQL 代理，返回事件级 CSV 行（dict）；失败抛异常。"""
    params = {
        "sql_url": SQL_URL,
        "start_date": start_date,
        "end_date": end_date,
        "diagnosis_status": "all",
        "animal_type": "all",
        "disease": "all",
        "country": "all",
    }
    from urllib.parse import urlencode

    url = PROXY_URL + "?" + urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        raw = resp.read()
    text = raw.decode("utf-8", "replace")
    if not text.strip().startswith(("global_id", "global_id,")):  # 代理出错时返回 JSON error
        raise ValueError(f"unexpected proxy payload (head={text[:120]!r})")
    return list(csv.DictReader(io.StringIO(text)))


def _event_date(row: dict) -> str:
    """事件日期：``observation_date`` 缺失回落 ``report_date``（与 .sql WHERE 口径一致）。"""
    return (row.get("observation_date") or row.get("report_date") or "").strip()[:10]


def run_fao_empres(limit: int = 100, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    """取窗口内动物疫病事件，按（date 月粒度, country, disease）聚合计数。

    - ``limit`` 语义 = 返回聚合计数行的上限（按 date 升序、country/disease 字典序确定性排序）。
    - ``start_date``/``end_date`` 缺省 = 最近 30 天；传固定历史窗口可确定性重放（golden 用）。
    - 全部取数失败 → 抛 ``RuntimeError``（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    end = date.fromisoformat(end_date) if end_date else datetime.now(timezone.utc).date()
    start = date.fromisoformat(start_date) if start_date else end - timedelta(days=DEFAULT_DAYS)
    window = (start.isoformat(), end.isoformat())

    try:
        events = _fetch_csv(*window)
    except Exception as exc:  # noqa: BLE001 - 单一数据表面，失败即红
        raise RuntimeError(f"fao-empres: 取数失败（window={window}）— {type(exc).__name__}: {exc}") from exc
    if not events:
        raise RuntimeError(f"fao-empres: 窗口 {window} 内 0 事件（视为异常，不静默回空）")

    counter: Counter = Counter()
    for ev in events:
        ev_date = _event_date(ev)
        if not ev_date:
            continue
        key = (ev_date[:7], (ev.get("country") or "").strip(), (ev.get("disease") or "").strip())
        counter[key] += 1

    rows = [
        {
            "date": month,
            "country": country,
            "disease": disease,
            "outbreaks": count,
            "source": SOURCE,
            "url": PROXY_URL + "?sql_url=" + SQL_URL,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }
        for (month, country, disease), count in sorted(counter.items())
    ][:limit]
    return rows


if __name__ == "__main__":  # 单页冒烟：python3 spiders/fao-empres/spider.py
    print(__import__("json").dumps(run_fao_empres(limit=3), ensure_ascii=False, indent=2))
