"""spiders/socrata-ny —— 纽约州 Socrata 平台交通燃料现货价（周频价格序列）。

数据表面（Socrata SODA API，**免 key、免鉴权**；经 fx01 代理出口实测）：

- ``GET https://data.ny.gov/resource/k7gz-mn77.json?$limit=N&$order=date DESC``
  返回周频燃料现货价，逐行字段：

  - ``date``                                 ISO 时间戳（``YYYY-MM-DDTHH:MM:SS.000``，周频）
  - ``ny_conventional_gasoline_spot_price_gal``  纽约常规汽油现货价（美元/加仑）
  - ``ny_ultra_low_sulfur_diesel_spot_price_gal``纽约超低硫柴油现货价（美元/加仑）
  - ``wti_crude_oil_spot_price_barrel``      WTI 原油现货价（美元/桶）
  - ``brent_crude_oil_spot_price_barrel``    Brent 原油现货价（美元/桶）

- **需代理出口**：data.ny.gov 从 tencent 站点直连不可达（Socrata 边缘对直连来源
  返回 403），单元依赖运行环境的 ``HTTPS_PROXY``/``HTTP_PROXY`` 环境变量（标准库
  urllib 默认行为，本单元**不硬编码代理地址**）；manifest 通过
  ``egress_secret: fd-industry-egress-fx01`` 声明所需出口。

数据集选择理由（批次二 Wave C 直建）：data.ny.gov 目录 API 检索后取
「Transportation Fuels Spot Prices: Beginning January 2011」（``k7gz-mn77``）——
**周频、含原油与成品油两类价格、2011 年起连续、最近更新 2026-10-06**，
是能源价格的周期性信号；相较目录下的日历型/故事型条目，本数据集是表格型
dataset（非 chart/story），字段稳定可解析。

口坑：

- **URL 含 ``$`` 参数必须 urlencode**：``$limit``/``$order`` 若手工拼接带空格会
  触发 ``InvalidURL: URL can't contain control characters``——本单元统一走
  ``urllib.parse.urlencode``。
- **默认序已为最新**：``$order=date DESC`` 显式声明，避免依赖上游默认序。
- **数值是字符串**：Socrata 数值列以字符串返回，需逐字段 ``float()`` 转换；
  空串/缺失按 ``None`` 处理并跳过该列（不丢整行）。
- **计数与数值类 golden 不锚当期值**：价格随行情滚动，golden 只锚结构常量
  （``source`` 字段 + 列名集合 + ``min_rows``），绝不锚具体价格（README 论证）。
- **全部请求失败抛 ``RuntimeError``**（失败即红，绝不静默返回空列表）。
- 免 key、免 UA 伪装；不做指纹伪装与频次对抗，遇风控升级按协议转人工，不逆向。
- **网络层有界重试**：仅对传输层错误（URLError/socket.timeout/HTTP 5xx）重试 1 次；
  退避走共享节流入口 ``fd_industry_data.http.throttle``（不自己写裸 sleep）。
  403/429 等 4xx 立即抛出并提示检查 egress_secret 注入——不对风控升级做频次对抗、
  不做逆向。
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from fd_industry_data.http import throttle as _throttle

SOURCE = "socrata-ny"
BASE = "https://data.ny.gov"
DATASET_ID = "k7gz-mn77"          # Transportation Fuels Spot Prices: Beginning January 2011
RESOURCE_PATH = f"/resource/{DATASET_ID}.json"
TIMEOUT = 30                      # 秒/请求；超时按传输层错误处理，走有界重试
_UA = "fd-industry-data/2.0 (socrata-ny; industry data pipeline)"

# 重试策略：仅对传输层错误（URLError、socket.timeout、HTTP 5xx）重试 1 次
_MAX_ATTEMPTS = 2
_BASE_DELAY = 1.0                 # 秒
_MAX_DELAY = 4.0
_EGRESS_TAIL = "fx01"  # 仅在诊断文案里给出 egress_secret 尾四位

# 数值列 → 输出列名（美元/加仑 与 美元/桶 两类单位分列，不混淆）
PRICE_COLUMNS = (
    ("ny_conventional_gasoline_spot_price_gal", "ny_gasoline_spot_usd_gal"),
    ("ny_ultra_low_sulfur_diesel_spot_price_gal", "ny_diesel_spot_usd_gal"),
    ("wti_crude_oil_spot_price_barrel", "wti_crude_spot_usd_bbl"),
    ("brent_crude_oil_spot_price_barrel", "brent_crude_spot_usd_bbl"),
)


def _get_json(url: str) -> list[dict]:
    """GET 一个 SODA 端点并解析 JSON 数组。urllib 尊重环境代理变量。

    有界重试策略（仅传输层错误）：
    - URLError（DNS/连接/代理/TLS 错误）、socket.timeout、HTTP 5xx → 重试 1 次
    - HTTP 403/429/其它 4xx → 立即抛出，不重试（避免频次对抗）
    - 退避：指数退避 1s 起步、上限 4s，经共享节流入口等待
    """
    last_exc: Exception | None = None
    gate = time.monotonic()  # 共享节流入口持有的时间戳（上次请求时刻）
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        req = urllib.request.Request(
            url, headers={"User-Agent": _UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 4xx 一律不重试：403 是出口 IP 被拒（不是可重试的瞬时错误），
            # 重试等同频次对抗；直接抛出可执行诊断。
            if 400 <= exc.code < 500:
                if exc.code == 403:
                    raise RuntimeError(
                        f"socrata-ny: 请求被拒绝 (HTTP 403) {url} —— 目标站点拒绝当前出口 IP；"
                        f"请检查 manifest 声明的 egress_secret（尾四位 …{_EGRESS_TAIL}）是否已注入"
                        "运行环境；本单元不做协议破解、不做指纹伪装、不做频次对抗"
                    ) from exc
                raise RuntimeError(
                    f"socrata-ny: 请求失败 {url}: HTTP {exc.code} {exc.reason}"
                ) from exc
            last_exc = exc          # 5xx：可重试
        except urllib.error.URLError as exc:
            last_exc = exc          # DNS/连接/代理/TLS
        except socket.timeout as exc:
            last_exc = exc
        except Exception as exc:    # noqa: BLE001 —— 非预期异常按传输错误处理，允许一次重试
            last_exc = exc

        if attempt < _MAX_ATTEMPTS:
            # 指数退避（1s 起步，上限 4s），经共享节流入口等待
            delay = min(_MAX_DELAY, _BASE_DELAY * (2 ** (attempt - 1)))
            gate = _throttle(gate, delay)
        else:
            raise RuntimeError(
                f"socrata-ny: 请求失败（已尝试 {_MAX_ATTEMPTS} 次）{url}: "
                f"{type(last_exc).__name__}: {last_exc}"
            ) from last_exc


def _to_float(raw) -> float | None:
    """Socrata 数值列以字符串返回；空/缺失返回 None（调用方跳过该列）。"""
    if raw is None:
        return None
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _period(ts: str) -> str | None:
    """``2026-09-25T00:00:00.000`` → ``2026-09-25``；不可解析返回 None。"""
    if not isinstance(ts, str) or len(ts) < 10:
        return None
    return ts[:10]


def run_socrata_ny(limit: int = 100, **_) -> list[dict]:
    """取纽约州交通燃料现货价周频序列。

    Args:
        limit: 返回行数上限；按日期升序后保留最近 ``limit`` 行（生产取最新）。

    Returns:
        每行含 period（日期）+ 四个价格列 + url/source/scraped_at。

    Raises:
        RuntimeError: 端点不可达或响应不是 JSON 数组（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 5000))

    query = urllib.parse.urlencode({"$limit": limit, "$order": "date DESC"})
    url = f"{BASE}{RESOURCE_PATH}?{query}"

    try:
        raw_rows = _get_json(url)
    except Exception as exc:  # noqa: BLE001 —— 端点级失败即红，统一包成 RuntimeError
        raise RuntimeError(f"socrata-ny: 请求失败 {url}: {type(exc).__name__}: {exc}") from exc

    if not isinstance(raw_rows, list):
        raise RuntimeError(f"socrata-ny: 响应非数组（{type(raw_rows).__name__}）——端点形态已变")

    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict] = []
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        period = _period(item.get("date", ""))
        if not period:
            continue
        row: dict = {"period": period}
        for src_col, out_col in PRICE_COLUMNS:
            row[out_col] = _to_float(item.get(src_col))
        row["url"] = url
        row["source"] = SOURCE
        row["scraped_at"] = scraped_at
        rows.append(row)

    if not rows:
        raise RuntimeError(f"socrata-ny: 取到 0 行可解析数据（{url}）——端点形态已变或全为缺测")

    rows.sort(key=lambda r: r["period"])          # 期次升序
    return rows[-limit:]                          # 保留最近 limit 行
