"""spiders/afdc —— 美国能源部替代燃料数据中心（AFDC）替代燃料加注站数量（年度）。

数据表面（AFDC「Data, Analysis & Trends」图表 JSON 通道，**免 key、免鉴权、直连可达**）：

- ``GET https://afdc.energy.gov/data/{chart_id}.json``
  返回 Highcharts 渲染描述：``categories``（年份轴）+ ``series``（按燃料分series，每条
  含 ``name`` 与 ``data`` 数值数组，缺测为 ``null``），另带 ``title`` / ``yaxis`` /
  ``last_update`` / ``spreadsheet``（xlsx 兜底直链）等元数据。
- 本单元选图表 **10332「U.S. Public and Private Alternative Fueling Stations by
  Fuel Type」**：1992..2025 年度序列（实测 34 个年度点），9 条燃料 series
  （Biodiesel / CNG / Electric / Ethanol (E85) / Hydrogen / LNG / Methanol (M85) /
  Propane / Renewable Diesel），单位 = 加注站数量（yaxis "Number of Stations"）。
  选择理由：替代燃料加注基础设施规模是能源转型类周期统计信号，年度序列自 1992 年
  连续可回补；且该图是 AFDC 目录中的表格型 line 图（JSON 载荷含数值），而非
  州级 map 图（见坑位——map 图 JSON 不载数值）。

通道选择（批次三 brief 要求 JSON 优先）：AFDC 无需 key 的 JSON 端点即
``/data/{id}.json``（图表渲染数据），实测 200 且结构稳定——**无需走 xlsx 解析**；
``spreadsheet`` 字段里的 xlsx 仅作上游兜底直链记录，不解析。

口坑：

- **``/data.json``（DCAT 式全量目录）返回 406**：AFDC 不提供站点级 data.json，
  只能按图表 ID 逐个取 ``/data/{id}.json``；图表 ID 来自 /data 目录页（HTML）。
- **州级「by State」图是 map 图，JSON 无数据**：10365-10371 / 10971 等返回的
  JSON ``categories`` 为空、``series`` 无数值——只有 line/bar 型图（如 10332、
  10326 零售价、10323 乙醇产销）才在 JSON 里载数值。
- **series ``data`` 与 ``categories`` 按下标配对**：缺测为 JSON ``null``，
  一律跳过该行（``limit`` 只数真实观测）；非数值（异常字符串）同样跳过。
- **计数随上游滚动修订**：最近年度的加注站数量每月都可能变——golden 只锚
  结构常量（``source`` / ``unit`` / 固定历史年 ``year`` + ``fuel`` 码表）+
  ``min_rows``，**绝不锚任何当期计数**（README 论证）。
- **直连可达**：本机与 tencent 站点直连 afdc.energy.gov 均 200（2026-10-08 实测，
  见 README 证据表），无需代理出口，manifest 不声明 ``egress_secret``。
- 仅常规 UA；免 key 免鉴权，不做指纹伪装与频次对抗，遇风控升级按协议转人工。
- 容错：HTTP 错误 / 非 JSON / 缺 categories-series 结构 / 0 观测行 →
  抛 ``RuntimeError``（失败即红，绝不静默回空）。
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

SOURCE = "afdc"
CHART_ID = 10332  # U.S. Public and Private Alternative Fueling Stations by Fuel Type
CHART_URL = f"https://afdc.energy.gov/data/{CHART_ID}.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
}
TIMEOUT = 60  # 秒/请求；单端点单请求，超时即失败（不重试、不加频次）

UNIT = "stations"  # yaxis "Number of Stations" 归一后的单位码（本单元常量）


def _as_int(value) -> int | None:
    """``categories`` 元素归一为年份 int（兼容 int / 数值字符串）；不可解析返回 None。"""
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_float(value) -> float | None:
    """series 观测值归一：数值 → float；``null``/异常字符串 → None（调用方跳过）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def fetch_chart() -> dict:
    """取 AFDC 图表 JSON（免 key）；失败抛 ``RuntimeError``。"""
    req = urllib.request.Request(CHART_URL, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 —— 端点级失败即红
        raise RuntimeError(
            f"afdc: {CHART_URL} 取数失败 — {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"afdc: 响应非 JSON 对象（{type(payload).__name__}）——端点形态已变")
    categories = payload.get("categories")
    series = payload.get("series")
    if not isinstance(categories, list) or not isinstance(series, list) or not categories:
        raise RuntimeError(
            "afdc: JSON 缺 categories/series 结构（或 categories 为空）——"
            "该图表可能是无载荷的 map 图或端点形态已变"
        )
    return payload


def run_afdc(limit: int = 100, year: int | None = None) -> list[dict]:
    """取 AFDC 替代燃料加注站数量年度观测行。

    Args:
        limit: 返回行数上限；全量解析后按（year, fuel）字典序确定性排序，保留最近
            ``limit`` 行（生产取最新期次；固定参数重放结果恒定）。
        year: 可选年份过滤（如 ``2000``），只保留该年度观测；缺省不过滤。

    Returns:
        每行：``year``（int）/ ``fuel``（燃料 series 名，上游原码）/
        ``stations``（float，加注站数量）/ ``unit``（常量 ``stations``）/
        ``chart_title`` / ``upstream_updated``（上游 last_update 标注）/
        ``url`` / ``source`` / ``scraped_at``。

    Raises:
        ValueError: ``limit``/``year`` 类型非法。
        RuntimeError: 端点不可达、结构异常或 0 观测行（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    limit = max(1, min(limit, 1000))
    if year is not None:
        year = int(year)

    payload = fetch_chart()
    categories = payload["categories"]
    chart_title = str(payload.get("title") or "")
    upstream_updated = str(payload.get("last_update") or "") or None

    rows: list[dict] = []
    for serie in payload["series"]:
        if not isinstance(serie, dict):
            continue
        fuel = str(serie.get("name") or "").strip()
        data = serie.get("data")
        if not fuel or not isinstance(data, list):
            continue
        for i, value in enumerate(data):
            if i >= len(categories):  # series 短于 categories 时以下标对齐截断
                break
            obs = _as_float(value)
            if obs is None:  # 缺测（null）或异常值行不透出
                continue
            row_year = _as_int(categories[i])
            if row_year is None or (year is not None and row_year != year):
                continue
            rows.append(
                {
                    "year": row_year,
                    "fuel": fuel,
                    "stations": obs,
                    "unit": UNIT,
                    "chart_title": chart_title,
                    "upstream_updated": upstream_updated,
                    "url": CHART_URL,
                    "source": SOURCE,
                    "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
            )

    if not rows:
        raise RuntimeError(
            f"afdc: {CHART_URL} 解析后 0 观测行"
            f"（year={year!r} 过滤后为空或端点形态已变）"
        )

    rows.sort(key=lambda r: (r["year"], r["fuel"]))
    return rows[-limit:]


if __name__ == "__main__":  # 冒烟：python3 spiders/afdc/spider.py
    import pprint

    pprint.pp(run_afdc(limit=3))
