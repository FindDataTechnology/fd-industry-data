"""spiders/jodi-oil —— JODI-Oil 月度石油供需（primary 流，约 100 国自报）。

数据表面（确定性 URL 模式，免鉴权；见工单 brief.notes「侦察簿 W3」）：

- 年度全量 CSV： ``GET https://www.jodidata.org/_resources/files/downloads/oil-data/annual-csv/primary/{year}.csv``
  - 实测可用区间 **2002..(当前年-1)**：2001 与当前年 404，2002/2024/2025 均 200（SDMX 风格 CSV，
    约 11 MB / 28.3 万行）。
  - 列： ``REF_AREA,TIME_PERIOD,ENERGY_PRODUCT,FLOW_BREAKDOWN,UNIT_MEASURE,OBS_VALUE,ASSESSMENT_CODE``

口坑（brief.notes / 实测逐一落实）：

- **确定性 URL**：只有 ``{year}.csv`` 一个自由度；本单元只发这一个参数化的 URL，不猜其它路径。
- **缺测占位**：``OBS_VALUE`` 非 数值 的占位有四种——``-`` / ``x`` / ``N/A`` / ``..``（2002 实测
  占 26.3 万行），一律归 ``None`` 并**跳过该行**（不透出空洞行，``limit`` 只数真实观测）。
- **质量旗**：``ASSESSMENT_CODE`` 原样透出为 ``assessment``（1/2/3，上游自评质量）。
- 流代码保持上游原码（README 有对照）：``INDPROD``（产量）/ ``TOTIMPSB``（进口）/
  ``TOTEXPSB``（出口）/ ``REFINOBS``、``DIRECUSE``（表观需求相关）等。
- ``limit`` 语义 = 返回行数上限：全量解析后按（month, country, product, flow, unit）字典序
  确定性排序再截取——同 ``year`` 重放结果恒定。
- 容错：单文件表面，404/HTTP 错误/解析失败/0 观测行 → 抛 ``RuntimeError``（失败即红）。

工单：``reports/health-tickets/20261006-jodi-oil-a587475b.yaml``（``kind=generate``）
"""
from __future__ import annotations

import csv
import io
import json
import re
import urllib.request
from datetime import datetime, timezone

SOURCE = "jodi-oil"
URL_TEMPLATE = (
    "https://www.jodidata.org/_resources/files/downloads/oil-data/annual-csv/primary/{year}.csv"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv, */*",
}
TIMEOUT = 120  # 秒；年度 CSV 约 11 MB，给足超时
_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _num(value: str) -> float | None:
    """``OBS_VALUE`` 归一：数值 → float；``-``/``x``/``N/A``/``..`` 等占位 → ``None``。"""
    text = (value or "").strip()
    if not _NUM_RE.match(text):
        return None
    return float(text)


def fetch_year(year: int) -> list[dict]:
    """取一个 JODI 年度 CSV，解析为观测行（缺测行跳过）；失败抛异常。"""
    url = URL_TEMPLATE.format(year=year)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
            raw = resp.read()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"jodi-oil: {year}.csv 取数失败 — {type(exc).__name__}: {exc}") from exc

    text = raw.decode("utf-8", "replace")
    if "REF_AREA" not in text[:400]:  # 404 时返回的是 HTML 错误页
        raise RuntimeError(f"jodi-oil: {year}.csv 非 JODI CSV 载荷（head={text[:80]!r}）")

    rows: list[dict] = []
    for rec in csv.DictReader(io.StringIO(text)):
        value = _num(rec.get("OBS_VALUE", ""))
        if value is None:  # 缺测占位行不透出
            continue
        period = (rec.get("TIME_PERIOD") or "").strip()  # 形如 2002-01
        if "-" not in period:
            continue
        year_s, month_s = period.split("-", 1)
        rows.append(
            {
                "year": int(year_s),
                "month": month_s.strip(),
                "country": (rec.get("REF_AREA") or "").strip(),
                "flow": (rec.get("FLOW_BREAKDOWN") or "").strip(),
                "product": (rec.get("ENERGY_PRODUCT") or "").strip(),
                "value": value,
                "unit": (rec.get("UNIT_MEASURE") or "").strip(),
                "assessment": (rec.get("ASSESSMENT_CODE") or "").strip() or None,
                "url": url,
                "source": SOURCE,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return rows


def run_jodi_oil(limit: int = 100, year: int | None = None) -> list[dict]:
    """取 JODI-Oil primary 流观测行。

    - ``year`` 缺省 = 上一个完整年度（当前年年度文件未发布，实测 404）。
    - ``limit`` = 返回行数上限（确定性排序后截取；``year`` 固定时重放结果恒定）。
    - 全部取数失败 → 抛 ``RuntimeError``（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []
    if year is None:
        year = datetime.now(timezone.utc).year - 1
    year = int(year)

    rows = fetch_year(year)
    if not rows:
        raise RuntimeError(f"jodi-oil: {year}.csv 解析后 0 观测行（视为异常，不静默回空）")

    rows.sort(key=lambda r: (r["month"], r["country"], r["product"], r["flow"], r["unit"]))
    return rows[:limit]


if __name__ == "__main__":  # 单页冒烟：python3 spiders/jodi-oil/spider.py
    print(json.dumps(run_jodi_oil(limit=3), ensure_ascii=False, indent=2))
