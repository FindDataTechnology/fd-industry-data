"""spiders/elexon-bmrs —— 英国 Elexon BMRS 日度温度序列（TEMP 数据集）。

数据表面（Elexon Insights API，**免 key、免鉴权**；经 fx01 代理出口实测）：

- ``GET https://data.elexon.co.uk/bmrs/api/v1/datasets/TEMP?format=json``
  返回**日度全国有效温度序列**（31 天滚动窗，每日一行）：

  - ``measurementDate``             观测日期（``YYYY-MM-DD``）
  - ``temperature``                 全国有效温度（摄氏度）
  - ``temperatureReferenceAverage`` 气候同期参考均值（可选列）
  - ``temperatureReferenceHigh``    气候同期参考高值（可选列）
  - ``temperatureReferenceLow``     气候同期参考低值（可选列）
  - ``publishTime``                 发布时间戳

- TEMP 是 Elexon 需求预测的天气锚（英国电网系统温度），是电力负荷与能源
  需求侧的周期性信号；**日频、31 天滚动窗**（更早历史不可回补——实时快照型）。
- **需代理出口**：data.elexon.co.uk 从 tencent 站点直连不可达（Azure WAF 403；
  本机旧代理出口 38.76.150.185 同样被封），单元依赖运行环境的
  ``HTTPS_PROXY``/``HTTP_PROXY`` 环境变量（标准库 urllib 默认行为，
  本单元**不硬编码代理地址**）；manifest 通过
  ``egress_secret: fd-industry-egress-fx01`` 声明所需出口。

**放弃的兄弟端点**（侦察证据，README 详述）：

- ``/bmrs/api/v1/generation/outturn``（FUELINST 聚合视图）与
  ``/bmrs/api/v1/datasets/FUELINST``：按燃料类型的实时发电构成，但**只保留最新
  1 天快照**（``?settlementDate=<历史日>`` 参数被忽略恒返今天）——无历史回补。
- ``/bmrs/api/v1/datasets/B1610``（30 分钟实发结算数据）：必填
  ``settlementDate``+``settlementPeriod``，但**所有日期/时段组合恒返 0 行**。

口坑：

- **31 天滚动窗**：每日运行取增量即可，缺深历史（ golden 只锚结构常量 +
  ``min_rows``，不锚温度值）。
- **参考基线列可能缺失**：部分行只有 ``temperature``，参考列按存在才输出。
- **全部请求失败抛 ``RuntimeError``**（失败即红，绝不静默返回空列表）。
- 免 key、免 UA 伪装；不做指纹伪装与频次对抗，遇风控升级按协议转人工，不逆向。
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

SOURCE = "elexon-bmrs"
BASE = "https://data.elexon.co.uk"
DATASET_PATH = "/bmrs/api/v1/datasets/TEMP"
TIMEOUT = 30                    # 秒/请求；超时即失败（不重试、不加频次）
_UA = "fd-industry-data/2.0 (elexon-bmrs; industry data pipeline)"

# 可选参考基线列（部分行缺失，按存在才输出）
OPTIONAL_COLS = (
    "temperatureReferenceAverage",
    "temperatureReferenceHigh",
    "temperatureReferenceLow",
)


def _get_json(url: str) -> dict:
    """GET 一个端点并解析 JSON 对象。urllib 尊重环境代理变量。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        return json.loads(resp.read().decode("utf-8"))


def _to_float(raw) -> float | None:
    if raw is None:
        return None
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _period(ts) -> str | None:
    """``2026-10-07`` → 原样；非字符串或长度不足返回 None。"""
    if not isinstance(ts, str) or len(ts) < 10:
        return None
    return ts[:10]


def run_elexon_bmrs(limit: int = 100, **_) -> list[dict]:
    """取英国 BMRS 日度温度序列（TEMP，31 天滚动窗）。

    Args:
        limit: 返回行数上限；按日期升序后保留最近 ``limit`` 行（生产取最新）。

    Returns:
        每行含 period（ISO yyyy-mm-dd）+ temperature（+ 可选参考基线列）
        + url/source/scraped_at。

    Raises:
        RuntimeError: 端点不可达、响应形态异常或零可解析行（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 5000))

    url = f"{BASE}{DATASET_PATH}?format=json"
    try:
        payload = _get_json(url)
    except Exception as exc:  # noqa: BLE001 —— 端点级失败即红
        raise RuntimeError(f"elexon-bmrs: 请求失败 {url}: {type(exc).__name__}: {exc}") from exc

    raw_rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(raw_rows, list):
        raise RuntimeError(
            f"elexon-bmrs: 响应缺少 data 数组（{type(payload).__name__}）——端点形态已变")

    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict] = []
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        period = _period(item.get("measurementDate"))
        if not period:
            continue
        temp = _to_float(item.get("temperature"))
        if temp is None:
            continue
        row: dict = {"period": period, "temperature": temp}
        for col in OPTIONAL_COLS:                 # 参考基线列：存在才输出
            val = _to_float(item.get(col))
            if val is not None:
                row[col] = val
        row["url"] = url
        row["source"] = SOURCE
        row["scraped_at"] = scraped_at
        rows.append(row)

    if not rows:
        raise RuntimeError(
            f"elexon-bmrs: 取到 0 行可解析数据（{url}）——端点形态已变或全为缺测")

    rows.sort(key=lambda r: r["period"])          # 期次升序
    return rows[-limit:]                          # 保留最近 limit 行
