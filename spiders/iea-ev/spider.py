"""spiders/iea-ev —— IEA Global EV Data API（全球电动汽车销量/保有量/渗透率）取数。

数据表面（公开 REST，免鉴权；Global EV Data Explorer 的后端，见工单 brief）：

- 数据： ``GET https://api.iea.org/evs?region=&category=&parameter=&mode=&powertrain=&year=``
  → ``[{region, category, parameter, mode, powertrain, year, unit, value}]``
- 维度实测（2026-10-06，全部 GET 实测）：``category`` ∈ {Historical, Projection-CPS,
  Projection-STEPS}；``parameter`` ∈ {EV sales, EV stock, EV sales share, EV stock share,
  Battery deployment, Electricity demand, Oil displacement Mlge, Oil displacement, Mbd}；
  历史段 2010-2025（年），投影段至 2035；行按 ``year`` 升序稳定返回。
- 维度枚举口（不进主流程，仅排障）：``/evs/list/region``、``/evs/list/mode?region=``、
  ``/evs/list/powertrain?region=&mode=``。

口坑（brief.notes 逐条落实）：

- 无 key；仅标准库 ``urllib``；不带 year 参数 = 全年份（2010 起逐年一行）。
- 参数白名单：只发实测过的维度值（region=World / mode=Cars / category=Historical /
  parameter=EV sales / powertrain=BEV），未见过的组合返回空数组**不报错**，故白名单
  之外的组合视为编程错误拒绝。
- 历史段（≤2024）跨期稳定，golden 锚固定 region/year 数值（World·Cars·BEV·EV sales
  2010 = 7000 Vehicles）；当期（2025）与投影值绝不锚。
- 容错：单一数据口，取数失败/负载非预期 → 抛 ``RuntimeError``（失败即红）；空结果
  视为失败，不静默返回空列表。

工单：``reports/health-tickets/20261006-iea-ev-53f8d3d7.yaml``（``kind=generate``）
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SOURCE = "iea-ev"
BASE = "https://api.iea.org/evs"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.iea.org/",
}
TIMEOUT = 30  # 秒；单口 JSON，超时即红（不重试、不加频次）

# 参数白名单：仅发实测过的维度值（见模块 docstring）
DEFAULT_QUERY = {
    "region": "World",
    "category": "Historical",
    "parameter": "EV sales",
    "mode": "Cars",
    "powertrain": "BEV",
}


def _get_json(url: str):
    """GET 一个 JSON 端点（固定 https 主机；无鉴权、无重试放大）。"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _num(value):
    """数值归一：无法解析 → ``None``（不透出脏值）。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def run_iea_ev(
    limit: int = 100,
    region: str = DEFAULT_QUERY["region"],
    category: str = DEFAULT_QUERY["category"],
    parameter: str = DEFAULT_QUERY["parameter"],
    mode: str = DEFAULT_QUERY["mode"],
    powertrain: str = DEFAULT_QUERY["powertrain"],
) -> list[dict]:
    """取 IEA EV 序列（年份升序），返回至多 ``limit`` 行；失败/空结果抛 ``RuntimeError``。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    query = {
        "region": region,
        "category": category,
        "parameter": parameter,
        "mode": mode,
        "powertrain": powertrain,
    }
    # 参数白名单：只发实测过的维度值；其他组合上游返回空数组不报错，容易造成空洞通过
    for key, allowed in (
        ("region", ("World", "China", "Europe", "USA")),
        ("category", ("Historical", "Projection-CPS", "Projection-STEPS")),
        (
            "parameter",
            (
                "EV sales",
                "EV stock",
                "EV sales share",
                "EV stock share",
                "Battery deployment",
                "Electricity demand",
                "Oil displacement Mlge",
                "Oil displacement, Mbd",
            ),
        ),
        ("mode", ("Cars", "2 and 3 wheelers", "Trucks", "Vans", "Buses", "EV", "EVSE")),
        ("powertrain", ("BEV", "PHEV", "FCEV", "EV")),
    ):
        if query[key] not in allowed:
            raise ValueError(f"iea-ev: {key}={query[key]!r} 不在实测白名单内（拒绝发出）")

    url = BASE + "?" + urllib.parse.urlencode(query)
    try:
        payload = _get_json(url)
    except Exception as exc:  # noqa: BLE001 - 单口失败即红
        raise RuntimeError(f"iea-ev: 数据口取数失败 {url}: {type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError(f"iea-ev: 数据口返回非数组负载（{type(payload).__name__}）: {url}")

    rows: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        year = item.get("year")
        rows.append(
            {
                "region": item.get("region"),
                "category": item.get("category"),
                "parameter": item.get("parameter"),
                "mode": item.get("mode"),
                "powertrain": item.get("powertrain"),
                "year": int(year) if isinstance(year, (int, float)) and not isinstance(year, bool) else None,
                "unit": item.get("unit"),
                "value": _num(item.get("value")),
                "url": url,
                "source": SOURCE,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    if not rows:
        raise RuntimeError(f"iea-ev: 数据口返回 0 行（query={query}）— 失败即红，不静默返回空列表")
    return rows[:limit]


if __name__ == "__main__":  # 单页冒烟：python3 spiders/iea-ev/spider.py
    print(json.dumps(run_iea_ev(limit=5), ensure_ascii=False, indent=2))
