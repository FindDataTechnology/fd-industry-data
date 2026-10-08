"""spiders/socrata-chicago —— 芝加哥 CTA 公交月度客流（Socrata SODA 聚合查询）。

数据表面（Socrata SODA API，**免 key、免鉴权**；经 fx01 代理出口实测）：

- ``GET https://data.cityofchicago.org/resource/bynn-gwxy.json``
  带 SoQL 聚合参数（``$select=month_beginning,sum(monthtotal)`` +
  ``$group=month_beginning`` + ``$order=month_beginning DESC``）
  直接返回**全网公交月度客流总量**：

  - ``month_beginning``  ISO 时间戳（``YYYY-MM-01T00:00:00.000``，月频）
  - ``sum_monthtotal``  该月全网（全部线路合计）公交乘次

- 原始行粒度为「线路 × 月」（``route`` / ``routename`` / 三类日均 / ``monthtotal``）；
  本单元取**聚合后的全网月度总量**为输出口径（城市交通运营量的周期信号）。
- **需代理出口**：data.cityofchicago.org 从 tencent 站点直连不可达（Socrata 边缘
  对直连来源返回 403），单元依赖运行环境的 ``HTTPS_PROXY``/``HTTP_PROXY`` 环境变量
  （标准库 urllib 默认行为，本单元**不硬编码代理地址**）；manifest 通过
  ``egress_secret: fd-industry-egress-fx01`` 声明所需出口。

数据集选择理由（批次二 Wave C 直建）：本域目录 API 检索后取
「CTA - Ridership - Bus Routes - Monthly Day-Type Averages」（``bynn-gwxy``）——
**2001-01 起连续、月频、最近更新 2026-09-28**，是城市交通运营量的周期性信号。
同城候选 ``t2rn-p8d7``（L 车站月度进站量）为同构备选；``esuh-pijk``（网约车司机
月度数）实测 ``/resource/`` 返回空对象、字段不可解析故排除。

口坑：

- **URL 含 ``$`` 参数必须 urlencode**：``$select``/``$group``/``$order`` 若手工拼接
  带空格会触发 ``InvalidURL: URL can't contain control characters``——本单元统一走
  ``urllib.parse.urlencode``。
- **聚合列名是 ``sum_monthtotal``**（Socrata 给 ``sum(monthtotal)`` 的自动别名）；
  不同数据集/不同聚合函数的别名会变，若上游改名需同步此处。
- **数值是字符串**：Socrata 数值列以字符串返回，需 ``float()``/``int()`` 转换。
- **计数类 golden 不锚当期值**：客流逐月更新且历史期次会被上游修订，golden 只锚
  结构常量（``source`` 字段 + ``period`` 格式 + ``min_rows``），绝不锚具体客流量。
- **全部请求失败抛 ``RuntimeError``**（失败即红，绝不静默返回空列表）。
- 免 key、免 UA 伪装；不做指纹伪装与频次对抗，遇风控升级按协议转人工，不逆向。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SOURCE = "socrata-chicago"
BASE = "https://data.cityofchicago.org"
DATASET_ID = "bynn-gwxy"         # CTA - Ridership - Bus Routes - Monthly Day-Type Averages
RESOURCE_PATH = f"/resource/{DATASET_ID}.json"
TIMEOUT = 30                     # 秒/请求；超时即失败（不重试、不加频次）
_UA = "fd-industry-data/2.0 (socrata-chicago; industry data pipeline)"

# SoQL 聚合：按月份汇总全网（全部线路）当月乘次
AGG_SELECT = "month_beginning,sum(monthtotal)"
AGG_GROUP = "month_beginning"
AGG_ORDER = "month_beginning DESC"


def _get_json(url: str) -> list[dict]:
    """GET 一个 SODA 端点并解析 JSON 数组。urllib 尊重环境代理变量。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        return json.loads(resp.read().decode("utf-8"))


def _period(ts: str) -> str | None:
    """``2026-07-01T00:00:00.000`` → ``2026-07``；不可解析返回 None。"""
    if not isinstance(ts, str) or len(ts) < 7:
        return None
    return ts[:7]


def _to_int(raw) -> int | None:
    """Socrata 数值列以字符串返回；空/缺失/非数值返回 None。"""
    if raw is None:
        return None
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return None


def run_socrata_chicago(limit: int = 100, **_) -> list[dict]:
    """取芝加哥 CTA 公交全网月度客流总量序列。

    Args:
        limit: 返回行数上限；按月份升序后保留最近 ``limit`` 个月（生产取最新）。

    Returns:
        每行含 period（ISO yyyy-mm）+ bus_rides_total + url/source/scraped_at。

    Raises:
        RuntimeError: 端点不可达、响应不是 JSON 数组或零可解析行（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 5000))

    query = urllib.parse.urlencode({
        "$select": AGG_SELECT,
        "$group": AGG_GROUP,
        "$order": AGG_ORDER,
        "$limit": limit,
    })
    url = f"{BASE}{RESOURCE_PATH}?{query}"

    try:
        raw_rows = _get_json(url)
    except Exception as exc:  # noqa: BLE001 —— 端点级失败即红，统一包成 RuntimeError
        raise RuntimeError(
            f"socrata-chicago: 请求失败 {url}: {type(exc).__name__}: {exc}") from exc

    if not isinstance(raw_rows, list):
        raise RuntimeError(
            f"socrata-chicago: 响应非数组（{type(raw_rows).__name__}）——端点形态已变")

    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict] = []
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        period = _period(item.get("month_beginning", ""))
        if not period:
            continue
        total = _to_int(item.get("sum_monthtotal"))
        if total is None:
            continue
        rows.append({
            "period": period,
            "bus_rides_total": total,
            "url": url,
            "source": SOURCE,
            "scraped_at": scraped_at,
        })

    if not rows:
        raise RuntimeError(
            f"socrata-chicago: 取到 0 行可解析数据（{url}）——"
            "端点形态已变或聚合别名已改名（期望 sum_monthtotal）")

    rows.sort(key=lambda r: r["period"])          # 期次升序
    return rows[-limit:]                          # 保留最近 limit 个月
