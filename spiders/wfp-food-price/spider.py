"""spiders/wfp-food-price —— WFP 全球市场粮价月度（HDX 托管）取数。

数据表面（公开、免鉴权；仅 https）：

- 数据集元信息： ``GET https://data.humdata.org/api/3/action/package_show?id=wfp-food-prices``
  → CKAN JSON，``result.resources[]`` 里取 ``format == "CSV"`` 的现行资源下载 URL
  （工单 brief.notes「接前复测现行资源路径」：URL 由 API 现场解析，不硬编码旧路径）。
- 资源本体： 上一步解析出的 ``.../download/wfpvam_foodprices.csv``（约 200MB 全库：
  90+ 国 × 市场级 × 月度，1990 起；该数据集 2021-08 后归档停更，历史值恒定）。

口坑（brief.notes 已点名，逐一落实）：

- 全库 CSV 很大，**必须流式解析**（``urlopen`` 逐行喂 ``csv.reader``，取满 ``limit``
  行即断流），绝不整文件落地。
- 列名是 WFP VAM 原始口径：``adm0_name``(国)/``mkt_name``(市场)/``cm_name`(品种)/
  ``mp_year``+``mp_month``(月)/``mp_price``(价)/``cur_name``(币种)/``um_name``(计价单位)。
- ``month`` 归一为 ``YYYY-MM``（零填充）。
- 容错：资源解析或下载是单链路，任一环节整体失败 → 抛 ``RuntimeError``（失败即红，
  不静默返回空列表）；流中个别脏行（列数不足/价格非数）跳过并留痕。
- 只用标准库（urllib/json/csv）；带常规浏览器 UA，不做指纹伪装与频次对抗。

工单：``reports/health-tickets/20261006-wfp-food-price-a8b9bfd6.yaml``（``kind=generate``）
"""
from __future__ import annotations

import csv
import io
import json
import urllib.request
from datetime import datetime, timezone

SOURCE = "wfp-food-price"
DATASET_PAGE = "https://data.humdata.org/dataset/wfp-food-prices"
PACKAGE_API = "https://data.humdata.org/api/3/action/package_show?id=wfp-food-prices"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}
TIMEOUT = 60  # 秒；资源文件大但流式读取，单块超时即整体失败（不重试放大）


def _http_get(url: str, timeout: int = TIMEOUT):
    req = urllib.request.Request(url, headers=HEADERS)
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (固定 https 主机)


def _resolve_resource_url() -> str:
    """从 CKAN API 现场解析现行 CSV 资源下载 URL（不硬编码旧路径）。"""
    with _http_get(PACKAGE_API, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8", "replace"))
    if not isinstance(payload, dict) or not payload.get("success"):
        raise ValueError(f"HDX package_show success={payload.get('success')!r}")
    resources = (payload.get("result") or {}).get("resources") or []
    for res in resources:
        if str(res.get("format", "")).upper() == "CSV" and res.get("url"):
            return str(res["url"])
    raise ValueError(f"no CSV resource in dataset wfp-food-prices "
                     f"({len(resources)} resources)")


def _month(year, month) -> str | None:
    """``mp_year``/``mp_month`` → ``YYYY-MM``（零填充）；非法则 None（脏行跳过）。"""
    try:
        y, m = int(float(year)), int(float(month))
    except (TypeError, ValueError):
        return None
    if not (1 <= m <= 12) or not (1900 <= y <= 2100):
        return None
    return f"{y:04d}-{m:02d}"


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def run_wfp_food_price(limit: int = 100) -> list[dict]:
    """取 ``limit`` 行市场粮价月度行；整体失败 → 抛 ``RuntimeError``（失败即红）。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    resource_url = _resolve_resource_url()
    rows: list[dict] = []
    skipped: list[str] = []
    with _http_get(resource_url) as resp:
        stream = io.TextIOWrapper(resp, encoding="utf-8-sig", errors="replace", newline="")
        reader = csv.reader(stream)
        header = next(reader, None)
        if not header or "adm0_name" not in header:
            raise RuntimeError(
                f"wfp-food-price: 资源表头异常（无 adm0_name 列）：{str(header)[:120]}")
        col = {name: i for i, name in enumerate(header)}
        for rec in reader:
            if len(rec) < len(header):
                skipped.append(f"short row: {rec!r}"[:80])
                continue
            month = _month(rec[col["mp_year"]], rec[col["mp_month"]])
            price = _num(rec[col["mp_price"]])
            if month is None or price is None:
                skipped.append(f"bad month/price: {rec!r}"[:80])
                continue
            rows.append({
                "country": (rec[col["adm0_name"]] or "").strip() or None,
                "market": (rec[col["mkt_name"]] or "").strip() or None,
                "commodity": (rec[col["cm_name"]] or "").strip() or None,
                "month": month,
                "price": price,
                "currency": (rec[col["cur_name"]] or "").strip() or None,
                "unit": (rec[col["um_name"]] or "").strip() or None,
                "url": resource_url,
                "source": SOURCE,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            })
            if len(rows) >= limit:
                break

    if not rows:
        raise RuntimeError(
            "wfp-food-price: 全库资源解析 0 行 — "
            f"url={resource_url}; skipped={skipped[:3]}")
    return rows[:limit]


if __name__ == "__main__":  # 冒烟：python3 spiders/wfp-food-price/spider.py
    print(json.dumps(run_wfp_food_price(limit=3), ensure_ascii=False, indent=2))
