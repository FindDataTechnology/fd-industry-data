"""spiders/noaa-ncei —— NOAA NCEI GHCN-Daily 全球气象站日值取数。

数据表面（公开 REST，免 key；工单 brief「侦察簿 W1-J」）：

- ``GET https://www.ncei.noaa.gov/access/services/data/v1``
  参数白名单（仅发实测过的参数）：``dataset=daily-summaries``、``stations=<US 站码，
  可逗号分隔>``、``startDate=YYYY-MM-DD``、``endDate=YYYY-MM-DD``、
  ``dataTypes=TMAX,TMIN,PRCP``、``format=json``。
- 返回 JSON 数组，每行形如
  ``{"DATE":"2020-12-31","STATION":"USW00023174","TMAX":"  200","TMIN":"   61","PRCP":"    0"}``
  ——**值是带空白填充的字符串**，GHCN-Daily 原生口径（TMAX/TMIN 为 0.1 ℃、PRCP
  为 0.1 mm 计的整数），本单元归一为浮点数（``"  200"`` → ``20.0``）。

坑位与容错（brief.notes 逐条落实）：

- 免 key、免 UA 伪装；不带任何签名/频次对抗。
- ``limit`` 语义 = 返回行数上限：日期升序排序后保留**最近** ``limit`` 行（生产取最新
  日值）；golden 重放用固定历史窗口（2020 年）+ 小 ``limit``，结果恒定。
- 缺测：GHCN 对缺失要素可能缺键或给空串/空白 → 归一为 ``None``。
- 失败即红：单站失败只跳过并留痕；**全部站点失败抛 ``RuntimeError``**（不静默返回
  空列表，避免「空洞通过」）。
- golden 锚固定站点（USW00023174）固定历史年（2020）的数值——绝不锚当期。

工单：``reports/health-tickets/20261006-noaa-ncei-dfcec3de.yaml``（``kind=generate``）
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

SOURCE = "noaa-ncei"
DATA_URL = "https://www.ncei.noaa.gov/access/services/data/v1"

# 种子站：工单 brief.source_urls 指定（USW00023174 = Los Angeles Downtown USC）。
SEED_STATIONS = ("USW00023174",)
DATA_TYPES = "TMAX,TMIN,PRCP"
TIMEOUT = 30  # 秒；轻量 JSON，超时即跳过该站（不重试、不加频次）

_DATE_LEN = 10  # YYYY-MM-DD


def _get_json(url: str):
    """GET 一个 JSON 端点（仅 https 固定主机；无鉴权、无重试放大）。"""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _num(value) -> float | None:
    """GHCN 字符串值（带空白填充）归一为 float；空白/无法解析 → ``None``（缺测）。"""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return round(float(text), 6)
    except ValueError:
        return None


def _tenths(value) -> float | None:
    """GHCN 原生 0.1 计值 → 物理量（``"  200"`` → ``20.0`` ℃；``"  414"`` → ``41.4`` mm）。"""
    num = _num(value)
    return None if num is None else round(num / 10.0, 6)


def _day_text(value, label: str) -> str:
    """校验 YYYY-MM-DD 形态；非法即抛 ``ValueError``（参数错误不静默）。"""
    text = str(value or "").strip()
    if len(text) != _DATE_LEN:
        raise ValueError(f"{label} must be YYYY-MM-DD, got {value!r}")
    date.fromisoformat(text)  # 非法日期抛 ValueError
    return text


def _default_window() -> tuple[str, str]:
    """生产缺省窗口：昨日为终点的最近 31 天（日值更新滞后 ≤ 数日，窗口留裕量）。"""
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = end - timedelta(days=30)
    return start.isoformat(), end.isoformat()


def _fetch_station(station: str, start_date: str, end_date: str) -> list[dict]:
    """取单站日期区间内的日值行（缺测要素为 ``None``）。"""
    params = {
        "dataset": "daily-summaries",
        "stations": station,
        "startDate": start_date,
        "endDate": end_date,
        "dataTypes": DATA_TYPES,
        "format": "json",
    }
    url = DATA_URL + "?" + urllib.parse.urlencode(params)
    payload = _get_json(url)
    if not isinstance(payload, list):
        raise ValueError(f"station {station}: unexpected payload {type(payload).__name__}")

    rows: list[dict] = []
    for rec in payload:
        if not isinstance(rec, dict):
            continue
        day = str(rec.get("DATE", "")).strip()[:_DATE_LEN]
        if not day:
            continue
        rows.append({
            "station": str(rec.get("STATION", "")).strip() or station,
            "date": day,
            "tmax": _tenths(rec.get("TMAX")),  # ℃（GHCN 0.1 ℃ 原生值已换算）
            "tmin": _tenths(rec.get("TMIN")),
            "prcp": _tenths(rec.get("PRCP")),  # mm（GHCN 0.1 mm 原生值已换算）
            "url": url,
            "source": SOURCE,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })
    rows.sort(key=lambda r: r["date"])
    return rows


def run_noaa_ncei(limit: int = 100, station: str | None = None,
                  start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    """取 GHCN 站点日值行；全部站点失败 → 抛 ``RuntimeError``（失败即红）。

    - ``station`` 缺省用种子站（brief 指定 USW00023174）；
    - ``start_date``/``end_date`` 缺省 = 最近 31 天（截至昨日）；
    - 行按日期升序排列，返回最近 ``limit`` 行（生产取最新）；
    - golden 重放传固定历史窗口（2020 年）+ 小 ``limit``，结果恒定。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    stations = [station] if station else list(SEED_STATIONS)
    if start_date or end_date:
        if not (start_date and end_date):
            raise ValueError("start_date and end_date must be given together")
        window = (_day_text(start_date, "start_date"), _day_text(end_date, "end_date"))
    else:
        window = _default_window()

    rows: list[dict] = []
    errors: list[str] = []
    for st in stations:
        try:
            rows.extend(_fetch_station(st, window[0], window[1]))
        except Exception as exc:  # noqa: BLE001 - 单站失败只跳过并留痕
            errors.append(f"{st}: {type(exc).__name__}: {exc}")
    if not rows:
        raise RuntimeError("noaa-ncei: 全部站点取数失败 — " + "; ".join(errors)[:300])
    rows.sort(key=lambda r: (r["date"], r["station"]))
    return rows[-limit:]


if __name__ == "__main__":  # 单页冒烟：python3 spiders/noaa-ncei/spider.py
    print(json.dumps(run_noaa_ncei(limit=3), ensure_ascii=False, indent=2))
