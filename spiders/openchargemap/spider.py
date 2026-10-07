"""spiders/openchargemap —— Open Charge Map 全球充电桩点位（POI）取数。

数据表面（工单 brief / 20261007-openchargemap-e92579f3）：

- ``GET https://api.openchargemap.io/v3/poi/`` → JSON 点位数组
- 鉴权：``key=`` 查询参数（env ``OCM_API_KEY``，官方实锤 key= 与 x-api-key 头二选一，
  统一按查询参数用；绝不硬编码/入文件，行内 ``source_url`` 一律 REDACTED）
- fair use 无公示数值限速——点位量级小、周任务足够；本爬虫每次调用发 1 个请求

实测口径坑（2026-10-07 直连实测）：

- **``countrycode=`` 只认 ISO alpha-2**（``DE`` → CountryID 87 过滤正确）；
  alpha-3（``DEU``）**静默不过滤**、返回全球最新点位——绝不用 alpha-3。
- 默认排序按新增/核验时间倒序（数据持续增长，首页不断变化）——golden 绝不锚总数、
  绝不锚首页特定行；**固定 POI 锚改用地理半径过滤**（``latitude``/``longitude``/
  ``distance``/``distanceunit=KM`` 实测有效，按距离升序、锚点 dist=0 恒第一）。
- ``compact=true`` 仍返回全结构（引用对象置 null）；``poiids=`` 参数实测被忽略（不过滤）。
- 单点上游响应可能偏慢且本机到 OCM 链路 ~1/4 概率断连/滞留（curl 与 urllib 同样中招，
  实测）——单次尝试 TIMEOUT 30s；连接层抖动退避 5/15/30s 至多重试 3 次（残余失败
  ~0.8%/调用）；HTTP 状态错误不重试；重试用尽即红（失败即红，不重试放大）。

实测锚（2026-10-07 实况冻结，结构常量而非计数）：

- POI ``511254``（UUID ``39B6C0CE-6C7C-4E66-9DF5-93DF49682756``，Reutlingen，CountryID 87，
  坐标 48.4998228/9.1533116）——以该坐标 2km 半径过滤恒含此点（实测半径内仅 6 点、按距离升序
  此点第一）；锚其 UUID/id/country_id 存在性 + 字段集合 + min_rows，绝不锚点位总数。

口坑：非 200 / 空数组 / 载荷非 list → 抛 ``RuntimeError``（失败即红，不静默返回空列表）。

工单：``reports/health-tickets/20261007-openchargemap-e92579f3.yaml``（kind=generate）
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SOURCE = "openchargemap"
BASE_URL = "https://api.openchargemap.io/v3/poi/"
TIMEOUT = 30  # 秒/次尝试；成功实测 <6s，失败多为连接层长滞留（17-60s），30s 快速暴露
# 连接层抖动退避重试（实测本机到 OCM 链路 ~1/4 概率断连/滞留，curl 与 urllib 同样中招）：
# 至多 4 次尝试（1 次原始 + 3 次退避 5/15/30s），残余失败率 ~0.8%/调用；
# 服务端已应答的 HTTP 状态错误不重试；重试用尽即红（失败即红，不重试放大）。
BACKOFF_SECONDS = (5, 15, 30)


def _get_json(url: str):
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        },
    )
    last_exc: Exception | None = None
    for attempt in range(1 + len(BACKOFF_SECONDS)):
        if attempt:
            wait = BACKOFF_SECONDS[attempt - 1]
            print(f"{SOURCE}: connection-level failure ({type(last_exc).__name__}: {last_exc}); "
                  f"retry {attempt}/{len(BACKOFF_SECONDS)} after {wait}s", file=sys.stderr)
            time.sleep(wait)
        try:
            return _get_json_once(req)
        except urllib.error.HTTPError as exc:
            # 服务端已应答（含 4xx/5xx）：口径/配额问题，重试无意义，失败即红
            detail = ""
            try:
                detail = exc.read(300).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - 错误体读取失败不掩盖主错误
                pass
            raise RuntimeError(f"openchargemap: HTTP {exc.code} {detail}") from exc
        except Exception as exc:  # noqa: BLE001 - 连接层抖动（断连/滞留/超时）退避重试
            last_exc = exc
    raise RuntimeError(
        f"openchargemap: 拉取失败（重试 {len(BACKOFF_SECONDS)} 次后仍失败）"
        f"{type(last_exc).__name__}: {last_exc}"
    ) from last_exc


def _get_json_once(req: urllib.request.Request):
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _num(value):
    """数值透传；null / 布尔 / 脏值 → None（绝不造数）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def run_openchargemap(
    limit: int = 100,
    country_code: str = "DE",
    latitude: float | None = None,
    longitude: float | None = None,
    distance_km: float | None = None,
) -> list[dict]:
    """取充电桩点位行，至多 ``limit`` 行。

    缺省按 ``country_code``（ISO alpha-2，如 ``DE``）拉取；
    给定 ``latitude``+``longitude``（``distance_km`` 可选，默认 10）时改用地理半径过滤
    （golden 固定 POI 锚走此形态）。非 200 / 空数组 → 抛 ``RuntimeError``（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    key = os.environ.get("OCM_API_KEY")
    if not key:
        raise RuntimeError(
            "openchargemap: env OCM_API_KEY 未设置（secrets/fd-industry-source-keys.env 注入），拒绝裸跑"
        )
    if latitude is not None and longitude is None:
        raise ValueError("latitude and longitude must be given together")
    if latitude is None and longitude is not None:
        raise ValueError("latitude and longitude must be given together")

    params: list[tuple[str, str]] = [
        ("maxresults", str(min(limit, 1000))),
        ("compact", "true"),
        ("output", "json"),
    ]
    if latitude is not None:
        params += [
            ("latitude", str(latitude)),
            ("longitude", str(longitude)),
            ("distance", str(distance_km if distance_km is not None else 10)),
            ("distanceunit", "KM"),
        ]
    else:
        if not country_code or not str(country_code).strip():
            raise ValueError("country_code must be a non-empty ISO alpha-2 code (e.g. DE)")
        params.append(("countrycode", str(country_code).strip()))
    params.insert(0, ("key", key))
    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"

    payload = _get_json(url)
    if not isinstance(payload, list):
        raise RuntimeError("openchargemap: 载荷非点位数组（上游口径已变，转人工）")
    if not payload:
        raise RuntimeError("openchargemap: 点位数组为空（过滤条件无命中或上游空窗，转人工）")

    redacted_url = url.replace(urllib.parse.quote(key), "REDACTED").replace(key, "REDACTED")
    scraped_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    for poi in payload:
        if not isinstance(poi, dict):
            continue
        address = poi.get("AddressInfo") or {}
        connections = poi.get("Connections") or []
        powers = [c.get("PowerKW") for c in connections if isinstance(c, dict)]
        powers = [p for p in powers if p is not None]
        rows.append({
            "id": poi.get("ID"),
            "uuid": poi.get("UUID"),
            "title": address.get("Title"),
            "address": address.get("AddressLine1"),
            "town": address.get("Town"),
            "postcode": address.get("Postcode"),
            "state_or_province": address.get("StateOrProvince"),
            "country_id": address.get("CountryID"),
            "latitude": _num(address.get("Latitude")),
            "longitude": _num(address.get("Longitude")),
            "usage_type_id": poi.get("UsageTypeID"),
            "status_type_id": poi.get("StatusTypeID"),
            "number_of_points": poi.get("NumberOfPoints"),
            "date_created": poi.get("DateCreated"),
            "date_last_verified": poi.get("DateLastVerified"),
            "is_recently_verified": poi.get("IsRecentlyVerified"),
            "operator_id": poi.get("OperatorID"),
            "data_provider_id": poi.get("DataProviderID"),
            "num_connections": len(connections),
            "max_power_kw": max(powers) if powers else None,
            "source": SOURCE,
            "source_url": redacted_url,
            "scraped_at": scraped_at,
        })
    if not rows:
        raise RuntimeError("openchargemap: 点位全部无法解析（上游口径变更，转人工）")
    return rows[:limit]


if __name__ == "__main__":  # 冒烟：source secrets 后 python3 spiders/openchargemap/spider.py
    print(json.dumps(run_openchargemap(limit=3), ensure_ascii=False, indent=2))
