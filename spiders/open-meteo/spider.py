"""spiders/open-meteo —— Open-Meteo 历史气象再分析（ERA5）日值取数。

数据表面（公开 REST，免 key；仅标准库）：

- 历史日值： ``GET https://archive-api.open-meteo.com/v1/archive``
  参数 ``latitude / longitude / start_date / end_date / daily`` → JSON
  （工单 brief.source_urls 实测面；``air-quality-api`` 同构可扩，本单元不接）。

口径坑位（brief.notes 已点名，逐一落实）：

- 免 key、无反爬；archive 数据 1940 年起（1950 上海 365 天满量实测），请求只带
  白名单参数（latitude/longitude/start_date/end_date/daily），绝无其他参数。
- 城市坐标是**固定常量**（种子北京 = brief 实测坐标 39.9/116.4），不足时按固定顺序
  扩展城市——``limit`` 小时只打种子城市，golden 重放确定。
- 日期窗口：缺省 = 截至昨天（UTC）的近 31 天滚动窗口（daily 节奏的生产语义）；
  golden 用 ``start_date/end_date`` 钉死 2020-01 固定历史区间，锚再分析值可复现。
- ``limit`` 语义 = 返回行数上限：城市外层、日期内层顺序展开（先种子城市先日期）。
- 容错：单城市失败只跳过并留痕；**全部城市失败抛 ``RuntimeError``**（失败即红）。

工单：``reports/health-tickets/20261006-open-meteo-ee751664.yaml``（``kind=generate``）
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

SOURCE = "open-meteo"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# 种子城市在前（北京 = 工单实测坐标）；其余按固定顺序扩展，保证行序确定。
CITIES: tuple[tuple[str, float, float], ...] = (
    ("beijing", 39.9, 116.4),
    ("shanghai", 31.2, 121.45),
    ("guangzhou", 23.13, 113.26),
    ("shenzhen", 22.55, 114.1),
    ("chengdu", 30.66, 104.07),
    ("hangzhou", 30.27, 120.15),
    ("wuhan", 30.59, 114.3),
    ("xian", 34.26, 108.94),
)

DAILY_FIELDS = ("temperature_2m_mean", "precipitation_sum")
DEFAULT_WINDOW_DAYS = 31  # 缺省取截至昨天（UTC）的近 31 天，与 daily 节奏对齐
TIMEOUT = 30  # 秒；archive 单请求为轻量 JSON，超时即跳过该城市（不重试、不加频次）


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "fd-industry-data/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _default_window() -> tuple[str, str]:
    """缺省窗口：昨天（UTC）往前 DEFAULT_WINDOW_DAYS 天，闭区间。"""
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = end - timedelta(days=DEFAULT_WINDOW_DAYS - 1)
    return start.isoformat(), end.isoformat()


def _num(value):
    """数值归一：None / 非数值 → None（archive 缺测即为 null，无 9999 占位）。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_city_days(city: str, lat: float, lon: float, start_date: str, end_date: str) -> list[dict]:
    """取一个城市一段窗口的逐日行（archive 再分析值为冻结历史，不会回改）。"""
    params = urllib.parse.urlencode(
        {
            "latitude": lat,
            "longitude": lon,
            "start_date": start_date,
            "end_date": end_date,
            "daily": ",".join(DAILY_FIELDS),
        }
    )
    url = f"{ARCHIVE_URL}?{params}"
    payload = _get_json(url)
    daily = payload.get("daily") if isinstance(payload, dict) else None
    if not isinstance(daily, dict) or not isinstance(daily.get("time"), list):
        raise ValueError(f"city {city}: unexpected payload (no daily.time)")

    times = daily["time"]
    values = {field: daily.get(field) or [None] * len(times) for field in DAILY_FIELDS}
    rows: list[dict] = []
    for i, day in enumerate(times):
        rows.append(
            {
                "city": city,
                "latitude": lat,
                "longitude": lon,
                "date": day,
                "temperature_2m_mean": _num(values["temperature_2m_mean"][i]),
                "precipitation_sum": _num(values["precipitation_sum"][i]),
                "url": url,
                "source": SOURCE,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return rows


def run_open_meteo(limit: int = 100, city: str | None = None,
                   start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    """取城市 × 日期的逐日历史行；全部城市失败 → 抛 ``RuntimeError``（失败即红）。

    ``city`` 限定单城（golden 钉种子城市）；``start_date/end_date`` 钉死历史窗口
    （golden 锚固定再分析值），缺省为截至昨天的近 31 天滚动窗口。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    if start_date and end_date:
        date.fromisoformat(str(start_date))  # 非法日期直接抛 ValueError
        date.fromisoformat(str(end_date))
    else:
        start_date, end_date = _default_window()

    cities = CITIES
    if city:
        cities = tuple(c for c in CITIES if c[0] == str(city))
        if not cities:
            raise ValueError(f"unknown city {city!r}; known: {[c[0] for c in CITIES]}")

    rows: list[dict] = []
    errors: list[str] = []
    for name, lat, lon in cities:
        if len(rows) >= limit:
            break
        try:
            rows.extend(fetch_city_days(name, lat, lon, str(start_date), str(end_date)))
        except Exception as exc:  # noqa: BLE001 - 单城市失败只跳过并留痕
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    if not rows:
        raise RuntimeError("open-meteo: 全部城市取数失败 — " + "; ".join(errors)[:300])
    return rows[:limit]


if __name__ == "__main__":  # 单次冒烟：python3 spiders/open-meteo/spider.py
    print(json.dumps(run_open_meteo(limit=3), ensure_ascii=False, indent=2))
