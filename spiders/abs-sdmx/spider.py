"""spiders/abs-sdmx —— 澳大利亚统计局 SDMX API（BA_GCCSA 建筑许可，月度）取数。

数据表面（官方 SDMX REST，公开免鉴权；工单 brief）：

- ``GET https://data.api.abs.gov.au/rest/data/BA_GCCSA/{key}?format=csv``
  → 平面 CSV（DATAFLOW,MEASURE,...,REGION,FREQ,TIME_PERIOD,OBS_VALUE,...）
- 数据流 ``BA_GCCSA`` = Building Approvals by GCCSA and above（**建筑许可**，月度）。
  key 维度序（DSD 实测）：``MEASURE.VALUE.SECTOR.WORK_TYPE.BUILDING_TYPE.TSEST.REGION.FREQ``。
- brief 点名 **RPPI（住宅价格指数）2021Q4 已停更，勿选该数据流**——本单元只用 BA_GCCSA。

口坑（brief.notes 逐条落实）：

- 无 key；仅标准库 ``urllib``/``csv``。``all`` 维度全量 CSV 实测 360 MB+，必须用
  key 过滤取窄切片（默认 20 KB 级）。
- 默认 key = ``1.1.9.TOT.100.10.1GSYD.M``：住宅审批套数（MEASURE=1）/ 总价值段
  （VALUE=1）/ 全部门（SECTOR=9）/ 全部工程（WORK_TYPE=TOT）/ 住宅合计
  （BUILDING_TYPE=100）/ 原始值（TSEST=10）/ 大悉尼（REGION=1GSYD）/ 月度（FREQ=M）。
  切片行序 = TIME_PERIOD 升序（2001-07 起，实测稳定）。
- 历史月值固定（2001-07 = 2513 套），golden 锚固定 region 固定月值；当期月份绝不锚。
- 容错：取数失败 / 负载非 CSV / 解析 0 行 → ``RuntimeError``（失败即红）；
  只带常规浏览器 UA，不做频次对抗。

工单：``reports/health-tickets/20261006-abs-sdmx-05cdc4e2.yaml``（``kind=generate``）
"""
from __future__ import annotations

import csv
import io
import json
import re
import urllib.request
from datetime import datetime, timezone

SOURCE = "abs-sdmx"
BASE_URL = "https://data.api.abs.gov.au/rest/data/BA_GCCSA/{key}"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv, */*",
}
TIMEOUT = 60  # 秒；默认窄切片（约 20 KB），公网常态内
DEFAULT_KEY = "1.1.9.TOT.100.10.1GSYD.M"
_KEY_RE = re.compile(r"^[0-9A-Za-z_+*.(](\+[0-9A-Za-z_+*.(]+)*\.[^,]{1,400}$")


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


def run_abs_sdmx(limit: int = 100, key: str = DEFAULT_KEY) -> list[dict]:
    """取 BA_GCCSA 指定 key 切片的至多 ``limit`` 行；全链路失败 → ``RuntimeError``。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []
    if not isinstance(key, str) or not _KEY_RE.match(key.strip()):
        raise ValueError(f"abs-sdmx: SDMX key 形态非法: {key!r}（应为点分维度过滤串）")
    key = key.strip()

    url = BASE_URL.format(key=key) + "?format=csv"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
            raw = resp.read()
    except Exception as exc:  # noqa: BLE001 - 单口失败即红
        raise RuntimeError(f"abs-sdmx: SDMX CSV 取数失败 {url}: {type(exc).__name__}: {exc}") from exc
    text = raw.decode("utf-8-sig", "replace")
    if not text.startswith("DATAFLOW"):
        raise RuntimeError(f"abs-sdmx: 负载非预期 CSV（头 {text[:40]!r}…）: {url}")

    rows: list[dict] = []
    for rec in csv.DictReader(io.StringIO(text)):
        rows.append(
            {
                "period": rec.get("TIME_PERIOD") or None,
                "region": rec.get("REGION") or None,
                "measure": rec.get("MEASURE") or None,
                "unit": rec.get("UNIT_MEASURE") or None,
                "value": _num(rec.get("OBS_VALUE")),
                "url": url,
                "source": SOURCE,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        if len(rows) >= limit:
            break
    if not rows:
        raise RuntimeError(f"abs-sdmx: 切片解析 0 行 — 失败即红: {url}")
    return rows[:limit]


if __name__ == "__main__":  # 单页冒烟：python3 spiders/abs-sdmx/spider.py
    print(json.dumps(run_abs_sdmx(limit=3), ensure_ascii=False, indent=2))
