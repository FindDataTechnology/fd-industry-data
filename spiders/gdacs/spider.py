"""spiders/gdacs —— GDACS 全球灾害事件列表（归档检索）。

数据表面（公开 REST，免 key；工单 brief.notes「侦察簿 W3」）：

- 事件检索： ``GET https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH
  ?fromDate=<YYYY-MM-DD>&toDate=<YYYY-MM-DD>`` → GeoJSON FeatureCollection（多灾种）

口坑（brief.notes + 2026-10-06 实测，逐一落实）：

- **MAP 变体不吃时间窗**：``geteventlist/MAP?fromDate=&toDate=`` 实测返回的全是
  当前事件（2025/2026），历史窗口参数被完全忽略——按 brief「golden 锚固定历史期事件 id」
  的口径，本单元改用 **SEARCH 变体**（归档检索，fromDate/toDate 生效，README 勘误留档）。
- **SEARCH 的 ``eventtype`` 参数是空操作**（带不带返回同集），故不发；响应本身就是多灾种
  （EQ/TC/VO/DR/WF/FL）。2026-01 月窗实测 13 条、2020-01 三个月窗 20 条。
- **SEARCH 会混入窗口外关联事件**（如跨期干旱 2018/2019 起），客户端按 ``fromdate`` 日期
  过滤到 ``[from_date, to_date]`` 闭区间，保证「固定历史期」口径纯净。
- 事件 id 全局唯一但响应顺序由检索排序决定（实测两次全等），golden 只锚**成员资格**
  （固定事件 id 出现）而非顺序。
- 默认窗口 = 截至今天的**近 30 天**（daily 节奏）；golden 显式传 2020-01 固定窗。
- 容错：单次取数失败/空特征集 → 抛 ``RuntimeError``（失败即红，不静默返回空列表）。

工单：``reports/health-tickets/20261006-gdacs-8149dca4.yaml``（``kind=generate``）
"""
from __future__ import annotations

import json
import urllib.request
from datetime import date, datetime, timedelta, timezone

SOURCE = "gdacs"
SEARCH_URL = (
    "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
    "?fromDate={from_date}&toDate={to_date}"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}
TIMEOUT = 60  # 秒；单次检索一窗，超时即红（不重试、不加频次）
DEFAULT_WINDOW_DAYS = 30

_DATE_LEN = 10  # ISO 日期前缀长度（fromdate = YYYY-MM-DDTHH:MM:SS）


def _fetch_window(from_date: str, to_date: str) -> list[dict]:
    """取一窗 GeoJSON 并返回原始 feature 列表（响应异常/空集抛错）。"""
    url = SEARCH_URL.format(from_date=from_date, to_date=to_date)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        payload = json.loads(resp.read().decode("utf-8", "replace"))
    features = (payload.get("features") or []) if isinstance(payload, dict) else []
    if not features:
        raise RuntimeError(f"gdacs: empty feature set for {from_date}..{to_date}")
    return features


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_from_feature(feature: dict) -> dict | None:
    """GeoJSON feature → 行；缺关键字段（eventid/eventtype）返回 ``None`` 跳过。"""
    props = feature.get("properties") or {}
    event_id = props.get("eventid")
    event_type = props.get("eventtype")
    if event_id is None or not event_type:
        return None
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") if geom.get("type") == "Point" else None
    lat = lon = None
    if isinstance(coords, (list, tuple)) and len(coords) >= 2:
        lon, lat = _num(coords[0]), _num(coords[1])
    links = props.get("url") if isinstance(props.get("url"), dict) else {}
    return {
        "event_id": event_id,
        "episode_id": props.get("episodeid"),
        "type": event_type,
        "country": props.get("country"),
        "iso3": props.get("iso3"),
        "alertlevel": props.get("alertlevel"),
        "alertscore": _num(props.get("alertscore")),
        "name": props.get("name"),
        "date": props.get("fromdate"),
        "todate": props.get("todate"),
        "lat": lat,
        "lon": lon,
        "event_source": props.get("source"),
        "sourceid": props.get("sourceid"),
        "url": (links or {}).get("report"),
        "source": SOURCE,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def run_gdacs(limit: int = 100, from_date: str | None = None,
              to_date: str | None = None) -> list[dict]:
    """取 ``[from_date, to_date]``（闭区间，YYYY-MM-DD）窗内 GDACS 事件行（≤ ``limit``）。

    缺省窗口 = 近 ``DEFAULT_WINDOW_DAYS`` 天（截至今天 UTC）；取数/解析失败 → 抛
    ``RuntimeError``（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []
    try:
        end = date.fromisoformat(to_date) if to_date else datetime.now(timezone.utc).date()
        start = date.fromisoformat(from_date) if from_date else end - timedelta(
            days=DEFAULT_WINDOW_DAYS)
    except ValueError as exc:
        raise ValueError(f"from_date/to_date must be YYYY-MM-DD: {exc}") from None

    features = _fetch_window(start.isoformat(), end.isoformat())
    rows: list[dict] = []
    seen: set[tuple] = set()
    for feature in features:
        row = _row_from_feature(feature)
        if row is None:
            continue  # 缺 eventid/eventtype 的残缺 feature，跳过留痕于行序外
        day = (row["date"] or "")[:_DATE_LEN]
        if not (start.isoformat() <= day <= end.isoformat()):
            continue  # SEARCH 混入的窗口外关联事件（如跨期干旱），过滤
        key = (row["type"], row["event_id"], row["episode_id"])
        if key in seen:
            continue  # 同事件同集次重复出现（几何变体），去重
        seen.add(key)
        rows.append(row)
        if len(rows) >= limit:
            break
    if not rows:
        raise RuntimeError(
            f"gdacs: 0 in-window rows for {start}..{end} "
            f"(raw features: {len(features)})")
    return rows[:limit]


if __name__ == "__main__":  # 单页冒烟：python3 spiders/gdacs/spider.py（近 30 天窗）
    print(json.dumps(run_gdacs(limit=3), ensure_ascii=False, indent=2))
