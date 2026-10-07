"""spiders/usda-quickstats —— USDA NASS Quick Stats 农情调查取数（keyed 源）。

数据表面（官方 Quick Stats API GET，key 走 ``key=`` 查询参数；工单
``reports/health-tickets/20261007-usda-quickstats-6fa3d4d0.yaml``）：

- 实测端点： ``GET https://quickstats.nass.usda.gov/api/api_GET/?key=$QUICKSTATS_API_KEY
  &commodity_desc=CORN&year=2020&agg_level_desc=NATIONAL&statisticcat_desc=AREA%20PLANTED&format=JSON``
  → ``{"data": [...]}``；本单元默认查询即该口径（实测 12 行，含 2020 年
  CORN / NATIONAL / AREA PLANTED / ACRES / YEAR = 90,432,000 的定值行）。
- 官方参数白名单以 quickstats.nass.usda.gov/api 为准；本单元只发实测有效的参数
  （``commodity_desc`` / ``year`` / ``agg_level_desc`` / ``statisticcat_desc`` /
  ``state_alpha`` / ``state_name`` / ``reference_period_desc`` / ``unit_desc`` /
  ``format``）。

坑位（工单 brief.notes / 实测逐一落实）：

- **缺 key / 无效 key**：实测 HTTP 401 + ``{"error":["unauthorized"]}``（另一实测
  口径为纯文本 ``unauthorized``）。响应体首字符非 ``{``/``[`` 一律报错；JSON 内
  带 ``error`` 键同样报错——绝不把错误页当数据吞掉。
- **单次 ≤ 50,000 行**：单分片返回行数已达上限视为被截断（数据必丢），该分片按
  失败处理（跳过留痕），绝不静默截断；大查询按 ``years``（逐年）与 ``state``
  （逐州）分片。
- **逗号年份不可用**：实测 ``year=2019,2020`` 返 HTTP 500 HTML 错误页——分片只能
  在客户端逐年发请求（本单元即如此，每年一个请求）。
- 单分片失败只跳过并向 stderr 留痕；**全部分片失败抛 ``RuntimeError``**（失败即
  红，不静默返回空列表）。
- 密钥只从 ``os.environ["QUICKSTATS_API_KEY"]`` 读；响应行内 ``url`` 列已剥离 key
  （只带真实查询参数），密钥不进任何行/日志。

入口：``run_usda_quickstats(limit=100, ...) -> list[dict]``；仅标准库。
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

SOURCE = "usda-quickstats"
API_URL = "https://quickstats.nass.usda.gov/api/api_GET/"
KEY_ENV = "QUICKSTATS_API_KEY"

# Quick Stats 单次请求硬上限（官方公示）：达到即疑截断，见模块 docstring
MAX_ROWS_PER_REQUEST = 50000
# 单次运行最多分片数（逐年查询），防参数误用导致请求放大
MAX_SHARDS = 60

# golden 锚定的默认口径（工单 brief 指定：2020 CORN / NATIONAL / AREA PLANTED）
DEFAULT_COMMODITY = "CORN"
DEFAULT_YEARS = "2020"
DEFAULT_AGG_LEVEL = "NATIONAL"
DEFAULT_STATISTICCAT = "AREA PLANTED"

HEADERS = {"User-Agent": "fd-industry-data/1.0", "Accept": "application/json"}
TIMEOUT = 45  # 秒；上游为整包 JSON，超时即该分片失败
REQUEST_GAP = 0.5  # 秒；无公示频控，保守串行节流
_MISSING_MARKERS = {"(D)", "(Z)", "(S)", "(NA)", "(X)", ""}


def _api_key() -> str:
    """从环境读 Quick Stats key；缺失即显式报错（绝不回退、绝不写默认值）。"""
    key = (os.environ.get(KEY_ENV) or "").strip()
    if not key:
        raise RuntimeError(
            f"usda-quickstats: 缺少环境变量 {KEY_ENV}（集群由 secret "
            "fd-industry-source-keys 注入；本地自验先 source secrets 文件）"
        )
    return key


def _parse_years(years) -> list[int]:
    """分片年份归一：int / 可迭代 / '2020' / '2019,2020' / '2018-2020' → [int]。"""
    if isinstance(years, bool):
        raise ValueError(f"years must not be a bool, got {years!r}")
    if isinstance(years, int):
        out = [years]
    elif isinstance(years, (list, tuple, set)):
        out = [int(y) for y in years]
    else:
        text = str(years).strip()
        if not text:
            raise ValueError("years must not be empty")
        span = re.fullmatch(r"(\d{4})\s*-\s*(\d{4})", text)
        if span:
            start, end = int(span.group(1)), int(span.group(2))
            if end < start:
                raise ValueError(f"years range reversed: {text!r}")
            out = list(range(start, end + 1))
        else:
            out = [int(part) for part in re.split(r"[,\s]+", text) if part]
    if not out:
        raise ValueError("years must contain at least one year")
    if len(out) > MAX_SHARDS:
        raise ValueError(f"years yields {len(out)} shards > {MAX_SHARDS}（拆分调用避免请求放大）")
    for year in out:
        if year < 1850 or year > 2100:
            raise ValueError(f"year out of range: {year}")
    return out


def _parse_value(raw: object) -> int | float | None:
    """Quick Stats 值归一：'90,432,000'→90432000、(D)/(Z)/(S)/(NA) 等屏蔽符→None。"""
    text = str(raw).replace(",", "").strip()
    if text.upper() in _MISSING_MARKERS:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return None


def _query_params(commodity: str, year: int, agg_level: str | None,
                  statisticcat: str | None, state: str | None,
                  reference_period: str | None, unit: str | None) -> dict[str, str]:
    """构造单分片查询参数（不含 key；顺序固定，便于 url 列逐字符稳定）。"""
    params: dict[str, str] = {"commodity_desc": commodity, "year": str(year)}
    if agg_level:
        params["agg_level_desc"] = agg_level
    if statisticcat:
        params["statisticcat_desc"] = statisticcat
    if state:
        st = str(state).strip()
        if len(st) == 2 and st.isalpha():
            params["state_alpha"] = st.upper()
        else:
            params["state_name"] = st.upper()
    if reference_period:
        params["reference_period_desc"] = reference_period
    if unit:
        params["unit_desc"] = unit
    params["format"] = "JSON"
    return params


def _display_url(params: dict[str, str]) -> str:
    """行内可追溯 URL：真实查询参数、**不含 key**。"""
    encoded = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return f"{API_URL}?{encoded}"


def _row(raw: dict, year: int, url: str) -> dict:
    """Quick Stats 原始行 → 平台行（字段口径见 README）。"""
    raw_value = raw.get("Value")
    raw_year = str(raw.get("year", "")).strip()
    return {
        "commodity": raw.get("commodity_desc"),
        "year": int(raw_year) if raw_year.isdigit() else year,
        "state": raw.get("state_name"),
        "state_alpha": raw.get("state_alpha"),
        "agg_level": raw.get("agg_level_desc"),
        "statisticcat": raw.get("statisticcat_desc"),
        "unit": raw.get("unit_desc"),
        "value": _parse_value(raw_value),
        "value_raw": raw_value,
        "reference_period": raw.get("reference_period_desc"),
        "short_desc": raw.get("short_desc"),
        "source_desc": raw.get("source_desc"),
        "load_time": raw.get("load_time"),
        "url": url,
        "source": SOURCE,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def _fetch_shard(year: int, key: str, params: dict[str, str]) -> list[dict]:
    """取单个 year 分片；任何异常（网络/HTTP/非 JSON/error 键/疑似截断）向上抛。"""
    query = dict(params)
    query["key"] = key
    url = f"{API_URL}?{urllib.parse.urlencode(query, quote_via=urllib.parse.quote)}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
            body = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"HTTP {exc.code}（{detail!r}）") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"网络失败：{exc.reason}") from None

    text = body.decode("utf-8", "replace").lstrip()
    # 工单 brief：响应体首字符必须为 { 或 [ 才进 JSON 解析（unauthorized 为纯文本）
    if not text or text[0] not in "{[":
        raise RuntimeError(
            f"非 JSON 响应（很可能缺 key/key 无效；首 80 字符：{text[:80]!r}）"
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"JSON 解析失败：{exc}") from None
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(f"上游 error：{payload['error']}")
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise RuntimeError(f"响应缺少 data 列表（顶层 {type(payload).__name__}）")
    if len(data) >= MAX_ROWS_PER_REQUEST:
        raise RuntimeError(
            f"单次返回 {len(data)} 行（≥ {MAX_ROWS_PER_REQUEST} 公示上限），疑被截断——"
            "请缩减查询（按 years/state 分片）"
        )
    display = _display_url(params)
    return [_row(r, year, display) for r in data if isinstance(r, dict)]


def run_usda_quickstats(limit: int = 100, commodity: str = DEFAULT_COMMODITY,
                        years=DEFAULT_YEARS, agg_level: str | None = DEFAULT_AGG_LEVEL,
                        statisticcat: str | None = DEFAULT_STATISTICCAT,
                        state: str | None = None,
                        reference_period: str | None = None,
                        unit: str | None = None) -> list[dict]:
    """取 Quick Stats 行；``years`` 逐年分片（单请求 ≤50,000 行），``limit`` 截断总量。

    默认口径 = 工单实测端点（2020 / CORN / NATIONAL / AREA PLANTED），golden 只锚
    该口径。单分片失败跳过并向 stderr 留痕；全部分片失败抛 ``RuntimeError``。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    key = _api_key()
    commodity = str(commodity).strip().upper()
    if not commodity:
        raise ValueError("commodity must not be empty")
    shard_years = _parse_years(years)

    rows: list[dict] = []
    errors: list[str] = []
    for i, year in enumerate(shard_years):
        if i:
            time.sleep(REQUEST_GAP)  # 串行低频，不并发、不重试放大
        try:
            params = _query_params(commodity, year, agg_level, statisticcat,
                                   state, reference_period, unit)
            rows.extend(_fetch_shard(year, key, params))
        except Exception as exc:  # noqa: BLE001 - 单分片失败只跳过并留痕
            errors.append(f"{year}: {type(exc).__name__}: {exc}")
            print(f"usda-quickstats: year {year} 分片失败已跳过 — {exc}", file=sys.stderr)
        if len(rows) >= limit:
            break
    if not rows:
        raise RuntimeError("usda-quickstats: 全部分片取数失败 — " + "; ".join(errors)[:300])
    return rows[:limit]


if __name__ == "__main__":  # 冒烟：python3 spiders/usda-quickstats/spider.py
    print(json.dumps(run_usda_quickstats(limit=3), ensure_ascii=False, indent=2))