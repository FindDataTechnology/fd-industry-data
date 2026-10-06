"""spiders/nasa-power —— NASA POWER 全球逐点气象日值（long 格式）取数。

数据表面（公开 REST，免 key；侦察簿 W1-J，见工单 brief.notes）：

- ``GET https://power.larc.nasa.gov/api/temporal/daily/point`` → JSON
  ``properties.parameter.<PARAM>{ "YYYYMMDD": value }``

实测口径（2026-10-07 直连冒烟，只发这些参数；brief.notes 实测「1984 乌鲁木齐
366 天满量」即本配置）：

- ``parameters=T2M,PRECTOTCORR``（气温 ℃ / 降水 mm/day）
- ``community=AG``、``longitude=87.6``、``latitude=43.8``（乌鲁木齐）
- ``start=19840101&end=19841231``（YYYYMMDD；1984 闰年 366 天，MERRA-2 已定版，
  历史**数值可复现**——golden 依此锚固定历史值）
- ``format=JSON``

口坑（brief.notes 逐条落实）：

- **缺测占位 ``-999.0``**（响应 header 明示 ``fill_value=-999.0``）→ ``None``，
  绝不当作真实观测值透出。
- 行形态为 brief 指定 long 格式：``date/parameter/value/lon/lat``，每参数×每天一行
  （1984 全年 = 366 天 × 2 参数 = 732 行）；``limit`` 截断返回行数。
- 容错：单参数缺列跳过留痕；拉取失败 / 载荷无任何参数数据 → 抛 ``RuntimeError``
  （失败即红，不静默返回空列表）。

工单：``reports/health-tickets/20261006-nasa-power-64d35640.yaml``（``kind=generate``）
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SOURCE = "nasa-power"
API = "https://power.larc.nasa.gov/api/temporal/daily/point"

# 实测配置（brief.notes：1984 乌鲁木齐 366 天满量，数值可复现）
PARAMETERS = ("T2M", "PRECTOTCORR")  # 固定输出顺序：date 升序 × 参数序
UNITS = {"T2M": "degC", "PRECTOTCORR": "mm"}  # POWER 文档口径：T2M ℃、PRECTOTCORR mm/day
LONGITUDE = 87.6
LATITUDE = 43.8
START = "19840101"
END = "19841231"
FILL_MAX = -900.0  # header.fill_value=-999.0；≤ -900 一律视为缺测占位

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
TIMEOUT = 60  # 秒；全年两点序列约 12 KB，超时即失败（失败即红，不重试放大）


def _request_url() -> str:
    params = urllib.parse.urlencode([
        ("parameters", ",".join(PARAMETERS)),
        ("community", "AG"),
        ("longitude", LONGITUDE),
        ("latitude", LATITUDE),
        ("start", START),
        ("end", END),
        ("format", "JSON"),
    ])
    return f"{API}?{params}"


def _get_json(url: str):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _iso_date(yyyymmdd: str) -> str:
    return f"{yyyymmdd[0:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def run_nasa_power(limit: int = 100) -> list[dict]:
    """取固定点（乌鲁木齐）1984 全年 T2M/PRECTOTCORR 日值 long 行，至多 ``limit`` 行。

    拉取失败或载荷无参数数据 → 抛 ``RuntimeError``（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    url = _request_url()
    try:
        payload = _get_json(url)
    except Exception as exc:  # noqa: BLE001 - 全局失败：失败即红
        raise RuntimeError(f"nasa-power: 拉取失败 {type(exc).__name__}: {exc}") from exc
    parameter = (payload.get("properties") or {}).get("parameter") if isinstance(
        payload, dict) else None
    if not isinstance(parameter, dict) or not parameter:
        raise RuntimeError("nasa-power: 载荷无 properties.parameter 数据（上游口径已变，转人工）")

    errors: list[str] = []
    rows: list[dict] = []
    dates = sorted({
        d for series in parameter.values() if isinstance(series, dict) for d in series
    })
    for ymd in dates:
        for param in PARAMETERS:  # 固定参数序 → 行序确定，golden 重放可复现
            series = parameter.get(param)
            if not isinstance(series, dict):
                errors.append(f"parameter {param} missing from payload")
                continue
            if ymd not in series:
                continue
            value = series[ymd]
            try:
                value = float(value)
            except (TypeError, ValueError):
                errors.append(f"{param}@{ymd}: unparsable value {value!r}")
                continue
            if value <= FILL_MAX:  # -999.0 缺测占位 → None
                value = None
            rows.append({
                "date": _iso_date(ymd),
                "parameter": param,
                "value": value,
                "unit": UNITS.get(param),
                "lon": LONGITUDE,
                "lat": LATITUDE,
                "url": url,
                "source": SOURCE,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            })
    if errors:
        print(f"{SOURCE}: skipped {len(errors)} bad series/value(s): "
              + "; ".join(errors[:3]), file=sys.stderr)
    if not rows:
        raise RuntimeError("nasa-power: 全部参数序列为空（上游空窗或口径变更，转人工）")
    return rows[:limit]


if __name__ == "__main__":  # 冒烟：python3 spiders/nasa-power/spider.py
    print(json.dumps(run_nasa_power(limit=4), ensure_ascii=False, indent=2))
