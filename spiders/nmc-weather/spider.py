"""spiders/nmc-weather —— 中央气象台（NMC）气象站**实况**取数。

数据表面（公开 REST，免鉴权；必须带浏览器 UA，见工单 brief.notes「侦察簿 W2-B」）：

- 实况： ``GET https://www.nmc.cn/rest/weather?stationid=<站码>`` → JSON
- 省码表： ``GET https://www.nmc.cn/rest/province`` → ``[{code,name,url}]``
- 站点表： ``GET https://www.nmc.cn/rest/province/<省码>`` → ``[{code,province,city,url}]``

口坑（brief.notes 已点名，逐一落实）：

- ``9999`` 是**缺测占位符**（数值 ``9999.0`` 与字符串 ``"9999"`` 皆是），一律归一为
  ``None``，绝不当作真实观测值透出（例如 ``airpressure=9999.0``、``warn.*="9999"``）。
- 站码是站点**内部短码**（``Wqsps`` = 北京），不是 WMO 站号，不要与站号混用。
- 须带浏览器 User-Agent，否则可能被站点拒绝（本单元只带常规 UA/Referer，不做任何
  指纹伪装与频次对抗）。
- ``limit`` 语义 = 返回行数上限：先取种子站（brief 指定 ``Wqsps``），数量不足时才按
  省码表顺序扩展站点——这样 ``limit=1`` 的 golden 重放结果恒定（站点与 URL 不变）。
- 容错：单个站点失败只跳过该站并留痕；**全部站点失败则抛异常**（失败即红，不静默返回
  空列表，避免「空洞通过」）。

工单：``reports/health-tickets/20261004-nmc-weather-23a303a2.yaml``（``kind=generate``）
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

SOURCE = "nmc-weather"
BASE = "https://www.nmc.cn"
WEATHER_URL = BASE + "/rest/weather?stationid={station}"
PROVINCE_LIST_URL = BASE + "/rest/province"
PROVINCE_URL = BASE + "/rest/province/{code}"

# 种子站：工单 brief.source_urls 指定（北京）。仅当 limit 大于种子数量时才走省码表扩展。
SEED_STATIONS = ("Wqsps",)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": BASE + "/",
    "Accept": "application/json, text/plain, */*",
}
TIMEOUT = 15  # 秒；站点为轻量 JSON，超时即跳过该站（不重试、不加频次）
MISSING_TOKEN = "9999"


def _get_json(url: str):
    """GET 一个 JSON 端点（仅 https 数据表面；无鉴权、无重试放大）。"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _num(value):
    """数值字段归一：缺测占位 ``9999`` → ``None``；无法解析 → ``None``。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num == 9999.0:  # 站点缺测占位
        return None
    return num


def _text(value):
    """文本字段归一：空串与 ``"9999"`` 占位 → ``None``。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == MISSING_TOKEN:
        return None
    return text


def _station_codes(limit: int) -> list[str]:
    """站点清单：种子站优先，不够时按省码表顺序扩展（失败即退化为种子站）。"""
    codes: list[str] = list(dict.fromkeys(SEED_STATIONS))
    if len(codes) >= limit:
        return codes[:limit]
    try:
        provinces = _get_json(PROVINCE_LIST_URL)
    except Exception:  # noqa: BLE001 - 枚举口不可用时仍交付种子站
        return codes[:limit]
    for prov in provinces or []:
        if len(codes) >= limit:
            break
        code = prov.get("code") if isinstance(prov, dict) else None
        if not code:
            continue
        try:
            stations = _get_json(PROVINCE_URL.format(code=code))
        except Exception:  # noqa: BLE001 - 单省失败不影响其它省
            continue
        for station in stations or []:
            st_code = station.get("code") if isinstance(station, dict) else None
            if st_code and st_code not in codes:
                codes.append(st_code)
            if len(codes) >= limit:
                break
    return codes[:limit]


def fetch_station(station_code: str) -> dict:
    """取单个气象站实况，返回一行（缺测字段为 ``None``）。"""
    url = WEATHER_URL.format(station=station_code)
    payload = _get_json(url)
    if not isinstance(payload, dict) or payload.get("code") != 0 or not isinstance(
        payload.get("data"), dict
    ):
        got = payload.get("code") if isinstance(payload, dict) else type(payload).__name__
        raise ValueError(f"station {station_code}: unexpected payload (code={got!r})")

    real = payload["data"].get("real") or {}
    station = real.get("station") or {}
    weather = real.get("weather") or {}
    wind = real.get("wind") or {}

    return {
        "station": _text(station.get("code")) or station_code,
        "station_name": _text(station.get("city")),
        "province": _text(station.get("province")),
        "city": _text(station.get("city")),
        "temperature": _num(weather.get("temperature")),
        "humidity": _num(weather.get("humidity")),
        "weather": _text(weather.get("info")),
        "wind": _num(wind.get("speed")),
        "wind_direct": _text(wind.get("direct")),
        "wind_power": _text(wind.get("power")),
        "publish_time": _text(real.get("publish_time")),
        "url": url,
        "source": SOURCE,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def run_nmc_weather(limit: int = 100) -> list[dict]:
    """取 ``limit`` 个气象站的实况行；全部站点失败 → 抛 ``RuntimeError``（失败即红）。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    rows: list[dict] = []
    errors: list[str] = []
    for code in _station_codes(limit):
        try:
            rows.append(fetch_station(code))
        except Exception as exc:  # noqa: BLE001 - 单站失败只跳过并留痕
            errors.append(f"{code}: {type(exc).__name__}: {exc}")
    if not rows:
        raise RuntimeError("nmc-weather: 全部站点取数失败 — " + "; ".join(errors)[:300])
    return rows[:limit]


if __name__ == "__main__":  # 单页冒烟：python3 spiders/nmc-weather/spider.py
    print(json.dumps(run_nmc_weather(limit=1), ensure_ascii=False, indent=2))