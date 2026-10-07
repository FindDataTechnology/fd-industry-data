"""spiders/usda-psd —— USDA FAS PSD 全球农产品供需平衡表取数（keyed 源）。

数据表面（FAS OpenData REST，key 走 ``X-Api-Key`` 请求头；工单
``reports/health-tickets/20261007-usda-psd-9f3fb5e1.yaml``）：

- 目录（实测可用，取数的起点）：
  ``GET https://api.fas.usda.gov/api/psd/commodities`` → ``[{"commodityCode","commodityName"}…]``
  **必须复数 commodities（单数实测 404）**；
  另有 ``/api/psd/countries``、``/api/psd/commodityAttributes``、
  ``/api/psd/unitsOfMeasure``（属性/单位名目录，用于行内富化）。
- 取数（实测有效路径族）：``GET /api/psd/commodity/{commodityCode}/country/{countryCode}/year/{marketYear}``
  → 该商品/国家/市场年最新一期修订的 ``[{"commodityCode","countryCode","marketYear",
  "calendarYear","month","attributeId","unitId","value"}…]``；
  ``countryCode=all``（实测 200）返回全部国家并带**完整修订历史**。
  实测：``0440000``（Corn）/``US``/``2020`` → 15 行（Yield 行 ``attributeId=184``
  ``unitId=26`` ``value=10.7608``）。

坑位（工单 brief.notes / 实测逐一落实）：

- **平台配额 1,000 次/时/key，且跨 api.data.gov 全平台代理 API 合并计算**（超限
  临时封禁）：本单元每次运行 1 个数据请求 + ≤4 个目录请求（目录结果进程内缓存），
  串行、间隔 0.5s、不并发不重试。
- 缺 key / 无效 key：实测 HTTP 403 + ``{"error":{"code":"API_KEY_MISSING"|
  "API_KEY_INVALID",…}}``；响应体首字符非 ``{``/``[`` 报错，JSON 带 ``error`` 键
  同样报错（api.fas.usda.gov 亦有纯文本 ``Bad API Key`` 口径）。
- 未知 commodity/country 实测 HTTP 200 + ``[]``（空列表）；未知年度（如 1900）
  实测 404。PSD 自 1960 起有数据，空结果视为该分片可疑失败（跳过留痕），
  **全部分片失败/全空抛 ``RuntimeError``**，不静默返回空列表。
- 单页失败只跳过并向 stderr 留痕（目录富化失败只降级为名字列 None，不影响数据行）。
- 密钥只从 ``os.environ["DATA_GOV_API_KEY"]`` 读（api.data.gov 联邦代理平台 key，
  与其它 api.data.gov 源共用一把）；数据端点 URL 本身不含 key，行内 ``url`` 无密钥。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SOURCE = "usda-psd"
API_URL = "https://api.fas.usda.gov/api"
KEY_ENV = "DATA_GOV_API_KEY"

PSD_FIRST_YEAR = 1960          # PSD 数据起点（官方 1960 起）
MAX_SHARDS = 60

# golden 锚定的默认口径（工单 brief：固定商品固定市场年；构建时实况冻结）
DEFAULT_COMMODITY = "0440000"  # Corn
DEFAULT_COUNTRY = "US"
DEFAULT_MARKET_YEARS = "2020"

HEADERS = {
    "User-Agent": "fd-industry-data/1.0",
    "Accept": "application/json",
}
TIMEOUT = 45  # 秒
REQUEST_GAP = 0.5  # 秒；api.data.gov 平台配额 1000 次/时，保守串行节流

# 进程内目录缓存（commodityAttributes / unitsOfMeasure / commodities / countries）
_CATALOG_CACHE: dict[str, dict[str, str]] = {}


def _api_key() -> str:
    """从环境读 api.data.gov key；缺失即显式报错（绝不回退到 DEMO_KEY）。"""
    key = (os.environ.get(KEY_ENV) or "").strip()
    if not key:
        raise RuntimeError(
            f"usda-psd: 缺少环境变量 {KEY_ENV}（api.data.gov key；集群由 secret "
            "fd-industry-source-keys 注入，本地自验先 source secrets 文件）"
        )
    return key


def _parse_market_years(years) -> list[int]:
    """分片市场年归一：int / 可迭代 / '2020' / '2019,2020' / '2018-2020' → [int]。"""
    if isinstance(years, bool):
        raise ValueError(f"market_year must not be a bool, got {years!r}")
    if isinstance(years, int):
        out = [years]
    elif isinstance(years, (list, tuple, set)):
        out = [int(y) for y in years]
    else:
        text = str(years).strip()
        if not text:
            raise ValueError("market_year must not be empty")
        span = re.fullmatch(r"(\d{4})\s*-\s*(\d{4})", text)
        if span:
            start, end = int(span.group(1)), int(span.group(2))
            if end < start:
                raise ValueError(f"market_year range reversed: {text!r}")
            out = list(range(start, end + 1))
        else:
            out = [int(part) for part in re.split(r"[,\s]+", text) if part]
    if not out:
        raise ValueError("market_year must contain at least one year")
    if len(out) > MAX_SHARDS:
        raise ValueError(f"market_year yields {len(out)} shards > {MAX_SHARDS}（拆分调用避免配额放大）")
    for year in out:
        if year < PSD_FIRST_YEAR or year > datetime.now(timezone.utc).year + 1:
            raise ValueError(
                f"market_year out of PSD range: {year}（PSD 自 {PSD_FIRST_YEAR} 起，"
                "未知年度上游实测 404）"
            )
    return out


def _get_json(path: str, key: str):
    """GET 一个 FAS OpenData JSON 端点（key 走 X-Api-Key 头，不入 URL/日志）。"""
    url = f"{API_URL}{path}"
    req = urllib.request.Request(url, headers={**HEADERS, "X-Api-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
            body = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"HTTP {exc.code}（{detail!r}）") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"网络失败：{exc.reason}") from None

    text = body.decode("utf-8", "replace").lstrip()
    if not text or text[0] not in "{[":
        raise RuntimeError(f"非 JSON 响应（很可能 key 无效/缺 key；首 80 字符：{text[:80]!r}）")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"JSON 解析失败：{exc}") from None
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(f"上游 error：{payload['error']}")
    return payload


def _catalog(kind: str, path: str, key: str, id_field: str, name_field: str) -> dict[str, str]:
    """目录富化映射 {code: name}，进程内缓存；失败向上抛（由调用方降级为 None）。"""
    if kind not in _CATALOG_CACHE:
        payload = _get_json(path, key)
        if not isinstance(payload, list):
            raise RuntimeError(f"目录 {path} 返回 {type(payload).__name__}，预期 list")
        mapping: dict[str, str] = {}
        for item in payload:
            if isinstance(item, dict) and item.get(id_field) is not None:
                name = item.get(name_field)
                mapping[str(item[id_field])] = str(name).strip() if name is not None else None
        _CATALOG_CACHE[kind] = mapping
    return _CATALOG_CACHE[kind]


def _load_catalogs(key: str, with_names: bool) -> tuple[dict[str, str], ...]:
    """尽力加载 4 个目录；任一失败只 stderr 留痕并降级为 {}（名字列 None）。"""
    empty: dict[str, str] = {}
    if not with_names:
        return empty, empty, empty, empty
    specs = (
        ("attributes", "/psd/commodityAttributes", "attributeId", "attributeName"),
        ("units", "/psd/unitsOfMeasure", "unitId", "unitDescription"),
        ("commodities", "/psd/commodities", "commodityCode", "commodityName"),
        ("countries", "/psd/countries", "countryCode", "countryName"),
    )
    out: list[dict[str, str]] = []
    for i, (kind, path, id_field, name_field) in enumerate(specs):
        if i:
            time.sleep(REQUEST_GAP)
        try:
            out.append(_catalog(kind, path, key, id_field, name_field))
        except Exception as exc:  # noqa: BLE001 - 目录富化失败只降级，不影响数据行
            print(f"usda-psd: 目录 {kind} 加载失败已降级 — {exc}", file=sys.stderr)
            out.append(empty)
    return tuple(out)  # type: ignore[return-value]


def _int_or_none(raw: object) -> int | None:
    try:
        text = str(raw).strip()
        return int(text) if text else None
    except (TypeError, ValueError):
        return None


def _float_or_none(raw: object) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _row(raw: dict, url: str, names: tuple[dict[str, str], ...]) -> dict:
    """PSD 原始行 → 平台行（列口径见 README）。"""
    attributes, units, commodities, countries = names
    commodity_code = str(raw.get("commodityCode", "")).strip()
    country_code = str(raw.get("countryCode", "")).strip()
    attribute_id = _int_or_none(raw.get("attributeId"))
    unit_id = _int_or_none(raw.get("unitId"))
    return {
        "commodity_code": commodity_code,
        "commodity_name": commodities.get(commodity_code),
        "country_code": country_code,
        "country_name": countries.get(country_code),
        "market_year": _int_or_none(raw.get("marketYear")),
        "calendar_year": _int_or_none(raw.get("calendarYear")),
        "month": _int_or_none(raw.get("month")),
        "attribute_id": attribute_id,
        "attribute_name": attributes.get(str(attribute_id)) if attribute_id is not None else None,
        "unit_id": unit_id,
        "unit": units.get(str(unit_id)) if unit_id is not None else None,
        "value": _float_or_none(raw.get("value")),
        "url": url,
        "source": SOURCE,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def _fetch_shard(commodity: str, country: str, market_year: int, key: str,
                 names: tuple[dict[str, str], ...]) -> list[dict]:
    """取单个 (commodity, country, market_year) 分片；空结果视为可疑失败。"""
    path = (f"/psd/commodity/{urllib.parse.quote(commodity)}"
            f"/country/{urllib.parse.quote(country)}/year/{market_year}")
    url = f"{API_URL}{path}"
    payload = _get_json(path, key)
    if not isinstance(payload, list):
        raise RuntimeError(f"数据结构变化：预期 list，实际 {type(payload).__name__}")
    if not payload:
        raise RuntimeError(
            "空结果（commodity/country 组合无数据或代码有误；未知代码上游实测 200+[]）"
        )
    return [_row(r, url, names) for r in payload if isinstance(r, dict)]


def run_usda_psd(limit: int = 100, commodity: str = DEFAULT_COMMODITY,
                 country: str = DEFAULT_COUNTRY, market_year=DEFAULT_MARKET_YEARS,
                 with_names: bool = True) -> list[dict]:
    """取 PSD 供需平衡表行；``market_year`` 逐年分片，``limit`` 截断总量。

    默认口径 = 工单实测端点（Corn ``0440000`` / ``US`` / MY2020，实测 15 行，
    Yield 行 value=10.7608），golden 只锚该口径。单分片失败/空结果跳过并向
    stderr 留痕；全部分片失败抛 ``RuntimeError``。``with_names=False`` 跳过目录
    富化（省 4 次 api.data.gov 请求，名字列为 None）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    key = _api_key()
    commodity = str(commodity).strip()
    country = str(country).strip()
    if not commodity or not country:
        raise ValueError("commodity and country must not be empty")
    shard_years = _parse_market_years(market_year)
    names = _load_catalogs(key, with_names)

    rows: list[dict] = []
    errors: list[str] = []
    for i, year in enumerate(shard_years):
        if i:
            time.sleep(REQUEST_GAP)
        try:
            rows.extend(_fetch_shard(commodity, country, year, key, names))
        except Exception as exc:  # noqa: BLE001 - 单分片失败只跳过并留痕
            errors.append(f"{year}: {type(exc).__name__}: {exc}")
            print(f"usda-psd: market_year {year} 分片失败已跳过 — {exc}", file=sys.stderr)
        if len(rows) >= limit:
            break
    if not rows:
        raise RuntimeError("usda-psd: 全部分片取数失败 — " + "; ".join(errors)[:300])
    return rows[:limit]


if __name__ == "__main__":  # 冒烟：python3 spiders/usda-psd/spider.py
    print(json.dumps(run_usda_psd(limit=3), ensure_ascii=False, indent=2))