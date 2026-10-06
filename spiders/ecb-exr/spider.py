"""spiders/ecb-exr —— 欧央行（ECB）欧元参考汇率序列取数。

数据表面（公开 SDMX 2.1 REST，免 key；工单 brief「侦察簿 W1-C」）：

- ``GET https://data-api.ecb.europa.eu/service/data/EXR/<series>?format=csvdata``
  种子 series ``M.CNY.EUR.SP00.A``（人民币/欧元，月均，即期，A=平均）；
  **``format=csvdata`` 最顺滑**（实测；纯码值列 CSV）。
- 日频序列可扩 ``EXR/D...``（brief 点名的扩展路径；本单元只实测过 M，不盲发）。

坑位与容错（brief.notes 逐条落实）：

- ``limit`` 语义 = 返回行数上限：期次升序排序后保留**最近** ``limit`` 期（生产取最新）；
  golden 重放用小 ``limit`` 锚固定历史月值，结果恒定。
- 空响应/无数据列 → 该 series 失败并留痕；**全部 series 失败抛 ``RuntimeError``**
  （失败即红，不静默返回空列表）。
- OBS_VALUE 为全精度浮点（如 ``7.6832363636364``），归一为 round 6 位小数。
- 免 key、免 UA 伪装；不带任何签名/频次对抗。

工单：``reports/health-tickets/20261006-ecb-exr-a0aee352.yaml``（``kind=generate``）
"""
from __future__ import annotations

import csv
import io
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SOURCE = "ecb-exr"
DATA_URL = "https://data-api.ecb.europa.eu/service/data/EXR"

# 种子序列：CNY/EUR 月均参考汇率。可扩币种（换 CNY）或日频（M→D，brief 点名路径，
# 未实测不默认发）。
SEED_SERIES = ("M.CNY.EUR.SP00.A",)
TIMEOUT = 60  # 秒；CSV 全史约 300 行，超时即跳过该 series（不重试、不加频次）

_CONTEXT_COLS = ("FREQ", "CURRENCY", "CURRENCY_DENOM", "EXR_TYPE", "EXR_SUFFIX")


def _get_text(url: str) -> str:
    """GET 一个 CSV 端点（仅 https 固定主机；无鉴权、无重试放大）。"""
    req = urllib.request.Request(url, headers={"Accept": "text/csv, */*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        raw = resp.read()
    return raw.decode("utf-8-sig", "replace")  # 兼容可能的 BOM


def _num(value) -> float | None:
    """OBS_VALUE 归一：全精度浮点字符串 → round 6 位；空/无法解析 → ``None``（缺测）。"""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return round(float(text), 6)
    except ValueError:
        return None


def fetch_series(series_key: str) -> list[dict]:
    """取一条 EXR 汇率序列的全部 obs；空响应/无有效行抛 ``ValueError``。"""
    query = urllib.parse.urlencode({"format": "csvdata"})
    url = f"{DATA_URL}/{series_key}?{query}"
    reader = csv.DictReader(io.StringIO(_get_text(url)))
    if not reader.fieldnames or "TIME_PERIOD" not in reader.fieldnames:
        raise ValueError(f"series {series_key}: CSV has no TIME_PERIOD column (got {reader.fieldnames})")

    rows: list[dict] = []
    for rec in reader:
        period = str(rec.get("TIME_PERIOD", "")).strip()
        if not period:
            continue
        rows.append({
            "period": period,  # 期次格式，如 2020-01（月频）或 2020-01-02（日频）
            "value": _num(rec.get("OBS_VALUE")),  # 1 EUR 兑 CNY，round 6；缺测为 None
            "series_key": series_key,
            **{c.lower(): (str(rec.get(c, "")).strip() or None) for c in _CONTEXT_COLS},
            "url": url,
            "source": SOURCE,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })
    if not rows:
        raise ValueError(f"series {series_key}: response parsed but zero data rows (key may be invalid)")
    rows.sort(key=lambda r: r["period"])
    return rows


def run_ecb_exr(limit: int = 100, series: str | None = None) -> list[dict]:
    """取 EXR 汇率序列行；全部 series 失败 → 抛 ``RuntimeError``（失败即红）。

    - ``series`` 缺省用种子 ``M.CNY.EUR.SP00.A``（可扩币种/日频 series key）；
    - 行按期次升序排列，返回最近 ``limit`` 行（生产取最新）；
    - golden 重放传小 ``limit`` 并锚固定历史月值（2020-01），结果恒定。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    keys = [series] if series else list(SEED_SERIES)
    rows: list[dict] = []
    errors: list[str] = []
    for key in keys:
        try:
            rows.extend(fetch_series(key))
        except Exception as exc:  # noqa: BLE001 - 单序列失败只跳过并留痕
            errors.append(f"{key}: {type(exc).__name__}: {exc}")
    if not rows:
        raise RuntimeError("ecb-exr: 全部序列取数失败 — " + "; ".join(errors)[:300])
    rows.sort(key=lambda r: r["period"])
    return rows[-limit:]


if __name__ == "__main__":  # 单页冒烟：python3 spiders/ecb-exr/spider.py
    print(json.dumps(run_ecb_exr(limit=3), ensure_ascii=False, indent=2))
