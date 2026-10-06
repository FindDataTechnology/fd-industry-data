"""spiders/uk-hpi —— 英国土地注册局 UK HPI 房价指数（月度）取数。

数据表面（官方固定文件，公开 S3+CloudFront，免鉴权；工单 brief）：

- ``GET https://publicdata.landregistry.gov.uk/market-trend-data/house-price-index-data/
  UK-HPI-full-file-YYYY-MM.csv`` → 全量 CSV（约 34 MB，区域×月份长表）
- 文件按月刷新，**当月未出时新近月份可能缺位**（实测 2026-10 时最新为 2026-07，
  2026-08/09 404），故从当月起向**前**扫描至多 7 个自然月取最新在架文件。
- 文件支持 HTTP Range（``accept-ranges: bytes``，实测）；本单元按工单口径**只取头部
  样例**（默认前 2 MB，约 9000+ 行），不整档下载。

口坑（brief.notes 逐条落实）：

- 无 key；仅标准库 ``urllib``/``csv``/``re``。
- 文件内行序 = 区域（字母序）内按日期升序；头部行是**固定历史期**（英格兰区域自
  1995-01、苏格兰 2004-01、北爱 2005-01 起），跨版本字节级稳定（2025-08 与 2026-07
  两个版本首行比对一致）→ ``limit`` 小值时结果恒定，golden 锚固定历史月数值。
- ``Date`` 列为 ``dd/mm/YYYY``，归一为 ``YYYY-MM``；空值字段（如 ``SalesVolume``）→
  ``None``，不透出脏值。
- 容错：文件探测全失败 / 头部样例取数失败 / 解析 0 行 → ``RuntimeError``（失败即红）。
- 不做指纹伪装与频次对抗；只带常规浏览器 UA。

工单：``reports/health-tickets/20261006-uk-hpi-48ae2c94.yaml``（``kind=generate``）
"""
from __future__ import annotations

import csv
import io
import json
import re
import urllib.request
from datetime import date, datetime, timezone

SOURCE = "uk-hpi"
BASE_URL = (
    "https://publicdata.landregistry.gov.uk/market-trend-data"
    "/house-price-index-data/UK-HPI-full-file-{ym}.csv"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv, */*",
}
TIMEOUT = 60  # 秒；头部样例（2 MB）在公网常态内
HEAD_BYTES = 2 * 1024 * 1024  # 只取头部样例（工单口径），不整档下载
SCAN_BACK_MONTHS = 6  # 当月起向前扫描 6 个自然月（实测最新文件可落后当月 3 个月）
_DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


def _month_label(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _newest_file_url() -> str:
    """从当月起向前扫描最新在架的 HPI 全量文件；全部 404 → 抛 ``RuntimeError``。"""
    today = date.today()
    errors: list[str] = []
    for back in range(SCAN_BACK_MONTHS + 1):
        month = today.month - back
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        ym = f"{year:04d}-{month:02d}"
        url = BASE_URL.format(ym=ym)
        req = urllib.request.Request(url, headers=HEADERS, method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
                resp.read()
            return url
        except Exception as exc:  # noqa: BLE001 - 单月缺位是常态，继续向前扫
            errors.append(f"{ym}: {type(exc).__name__}")
    raise RuntimeError("uk-hpi: 近 7 个自然月均无在架 HPI 全量文件 — " + "; ".join(errors))


def _fetch_head_sample(url: str) -> str:
    """Range 取文件头部样例，返回完整行文本（截掉最后一个残行）。"""
    req = urllib.request.Request(
        url, headers={**HEADERS, "Range": f"bytes=0-{HEAD_BYTES - 1}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
            raw = resp.read()
    except Exception as exc:  # noqa: BLE001 - 单站失败即红
        raise RuntimeError(f"uk-hpi: 头部样例取数失败 {url}: {type(exc).__name__}: {exc}") from exc
    text = raw.decode("utf-8-sig", "replace")
    lines = text.split("\n")
    if len(lines) < 2:
        raise RuntimeError(f"uk-hpi: 头部样例不足一行（{len(raw)} bytes）: {url}")
    return "\n".join(lines[:-1])  # 截掉 Range 截断的残行，保证行完整


def _num(value):
    """数值归一：空串/无法解析 → ``None``。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _month(value):
    """``dd/mm/YYYY`` → ``YYYY-MM``；异常格式 → ``None``。"""
    m = _DATE_RE.match(str(value or "").strip())
    if not m:
        return None
    return f"{m.group(3)}-{m.group(2)}"


def run_uk_hpi(limit: int = 100) -> list[dict]:
    """取 UK HPI 全量文件头部样例的至多 ``limit`` 行；探测/取数/解析全失败 → ``RuntimeError``。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    url = _newest_file_url()
    sample_text = _fetch_head_sample(url)

    rows: list[dict] = []
    for rec in csv.DictReader(io.StringIO(sample_text)):
        rows.append(
            {
                "region": rec.get("RegionName") or None,
                "area_code": rec.get("AreaCode") or None,
                "month": _month(rec.get("Date")),
                "average_price": _num(rec.get("AveragePrice")),
                "index": _num(rec.get("Index")),
                "sales_volume": _num(rec.get("SalesVolume")),
                "url": url,
                "source": SOURCE,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        if len(rows) >= limit:
            break
    if not rows:
        raise RuntimeError(f"uk-hpi: 头部样例解析 0 行 — 失败即红: {url}")
    return rows[:limit]


if __name__ == "__main__":  # 单页冒烟：python3 spiders/uk-hpi/spider.py
    print(json.dumps(run_uk_hpi(limit=3), ensure_ascii=False, indent=2))
