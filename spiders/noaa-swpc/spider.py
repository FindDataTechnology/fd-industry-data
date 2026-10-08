"""spiders/noaa-swpc —— NOAA 空间天气行星 Kp 指数（3h 观测 → 日级统计）。

数据表面（NOAA SWPC products，**免 key、免鉴权、直连可达**；2026-10-08 实测
直连 HTTP 200，无需代理出口）：

- ``GET https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json``
  返回滚动窗（约 8–30 天）内逐 3 小时一条的行星 Kp 观测，行字段：

  - ``time_tag``       ISO 观测时刻（``YYYY-MM-DDTHH:MM:SS``，UTC）
  - ``Kp``             行星 Kp 指数（0–9，1/3 步长；可能字符串化，需 float 转换）
  - ``a_running``      3h running Ap 值（辅助列，原样带出）
  - ``station_count``  参与该 3h 值统计的台站数（辅助列，原样带出）

- **响应形态有两种（同端点历史上都出现过），单元两者都吃**：
  1) 数组首元素是字符串表头 ``["time_tag","Kp","a_running","station_count"]``，
     其后每行是数组（SWPC products 家族常见形态）；
  2) 数组每元素直接是对象（2026-10-08 实测形态）。
  按首元素类型判别后统一成 dict 行。

信号口径（批次三直建）：3h 观测粒度对日频消费太碎，按 **UTC 日期分桶**出日级
统计——``daily_max_kp``（当日最大 Kp，空间天气日报的标准口径）/``daily_avg_kp``
（当日均值）/``obs_count``（当日 3h 观测条数），滚动窗内逐日一行。

口坑：

- **滚动窗**：窗口随时间滚动，重放日的「最新」观测日期与建样日不同——golden 只锚
  结构常量（``source`` + ``period`` 格式 + ``min_rows``），绝不锚 Kp 数值/日期。
- **末行可能是半天**：当天 8 个 3h 档未跑满时该日统计基于已有观测，``obs_count``
  供对账（解读当日 max/avg 时先看 obs_count）。
- **Kp 数值可能字符串化**：统一 ``float()`` 转换，坏值跳过该条观测（不丢整日，
  除非该日观测全坏）。
- **全部请求失败抛 ``RuntimeError``**（失败即红，绝不静默返回空列表）。
- 免 key、免 UA 伪装；不做指纹伪装与频次对抗，遇风控升级按协议转人工，不逆向。
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

SOURCE = "noaa-swpc"
ENDPOINT = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
TIMEOUT = 30                      # 秒/请求；超时即失败（不重试、不加频次）
_UA = "fd-industry-data/2.0 (noaa-swpc; industry data pipeline)"

_HEADER_ALIASES = {
    "time_tag": "time_tag",
    "kp": "Kp",
    "a_running": "a_running",
    "station_count": "station_count",
}


def _get_json(url: str):
    """GET 端点并解析 JSON。urllib 尊重环境代理变量（本源直连即可）。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        return json.loads(resp.read().decode("utf-8"))


def _to_float(raw) -> float | None:
    """Kp 可能字符串化；空/坏值返回 None（调用方跳过该条观测）。"""
    if raw is None:
        return None
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _normalize(raw) -> list[dict]:
    """两种响应形态统一成 dict 行列表；形态不识别抛 RuntimeError。"""
    if not isinstance(raw, list) or not raw:
        raise RuntimeError(
            f"noaa-swpc: 响应非非空数组（{type(raw).__name__}）——端点形态已变")

    first = raw[0]
    if isinstance(first, dict):                      # 形态 2：对象数组（2026-10-08 实测）
        return [r for r in raw if isinstance(r, dict)]

    if isinstance(first, list):                      # 形态 1：首行表头 + 数组行
        header = [str(h).strip().lower() for h in first]
        unknown = [h for h in header if h not in _HEADER_ALIASES]
        if unknown:
            raise RuntimeError(
                f"noaa-swpc: 表头出现未知列 {unknown} ——端点 schema 已变")
        names = [_HEADER_ALIASES[h] for h in header]
        rows = []
        for item in raw[1:]:
            if isinstance(item, list) and len(item) == len(names):
                rows.append(dict(zip(names, item)))
        return rows

    raise RuntimeError(
        f"noaa-swpc: 首元素类型 {type(first).__name__} 不识别——端点形态已变")


def run_noaa_swpc(limit: int = 100, **_) -> list[dict]:
    """取 NOAA 行星 Kp 观测并按 UTC 日期分桶出日级统计。

    Args:
        limit: 返回行数上限；按日期升序后保留最近 ``limit`` 日（生产取最新）。

    Returns:
        每行含 period（UTC 日期）+ daily_max_kp/daily_avg_kp/obs_count +
        url/source/scraped_at。

    Raises:
        RuntimeError: 端点不可达、响应形态已变或零可解析观测（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 1000))

    try:
        raw = _get_json(ENDPOINT)
    except Exception as exc:  # noqa: BLE001 —— 端点级失败即红，统一包成 RuntimeError
        raise RuntimeError(f"noaa-swpc: 请求失败 {ENDPOINT}: {type(exc).__name__}: {exc}") from exc

    obs = _normalize(raw)

    buckets: dict[str, list[float]] = {}
    for item in obs:
        time_tag = item.get("time_tag")
        if not isinstance(time_tag, str) or len(time_tag) < 10:
            continue
        kp = _to_float(item.get("Kp"))
        if kp is None:
            continue
        buckets.setdefault(time_tag[:10], []).append(kp)

    if not buckets:
        raise RuntimeError(
            f"noaa-swpc: {len(obs)} 条观测中 0 条含可解析 time_tag+Kp ——端点 schema 已变或全为缺测")

    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict] = []
    for period in sorted(buckets):
        kps = buckets[period]
        rows.append({
            "period": period,
            "daily_max_kp": max(kps),
            "daily_avg_kp": sum(kps) / len(kps),
            "obs_count": len(kps),
            "url": ENDPOINT,
            "source": SOURCE,
            "scraped_at": scraped_at,
        })

    return rows[-limit:]                             # 期次升序，保留最近 limit 日


if __name__ == "__main__":
    import json as _json
    out = run_noaa_swpc()
    print(_json.dumps(out, ensure_ascii=False, indent=2))
