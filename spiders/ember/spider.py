"""spiders/ember —— Ember 电力结构月度数据（``electricity-generation/monthly``）。

数据表面（公开 REST，**key 鉴权**；工单 ``reports/health-tickets/20261007-ember-e945ff89.yaml``）：

- ``GET https://api.ember-energy.org/v1/electricity-generation/monthly``
  参数白名单（仅发实测过的）：``api_key``（env ``EMBER_API_KEY``）、``entity_code``（**ISO3**，
  如 ``DEU``）、``start_date=YYYY-MM``、``end_date=YYYY-MM``、``series``（可选，精确名）。
- 返回 ``{"stats": {"number_of_records": N, "date_range"…}, "data": [row…]}``；行形如
  ``{"entity","entity_code","is_aggregate_entity","date","series","is_aggregate_series",
  "generation_twh","share_of_generation_pct"}``——**宽表**：一行同时载发电量（TWh）与占比（%），
  单位内嵌在列名里（上游不返回 unit 列）。实测 ``series`` 17 项：Bioenergy/Clean/Coal/Demand/
  Fossil/Gas/Hydro/"Hydro, bioenergy and other renewables"/Net imports/Nuclear/Other fossil/
  Other renewables/Renewables/Solar/Total generation/Wind/"Wind and solar"。

坑位与容错（brief.notes / 侦察口径逐条落实，均实测）：

- **key 纪律**：``api_key`` 走查询参数（**非请求头**）；从 ``os.environ[EMBER_API_KEY]`` 读取，
  缺失立即抛 ``RuntimeError``（显式报错，禁止裸跑）；绝不硬编码、绝不落文件/日志/行数据。
- **行内 ``url`` 已脱敏**（``api_key=***``）——落盘行不含密钥。
- **``entity_code`` 用 ISO3**（``DEU`` 而非 ``DE``）：实测 ``entity_code=DE`` **静默返回 0 记录**
  （不报错），是最易踩的坑；本单元校验码形并对空结果抛错（含 ISO3 提示），把静默空变成红。
- **重复 ``entity_code`` 不聚合**：实测 ``entity_code=DEU&entity_code=FRA`` 只按**最后一个**取值
  （返回 16 行 FRA），故本单元只接受单个 ``entity_code``，不做多实体拼接。
- **``start_date``/``end_date`` 为 ``YYYY-MM``**（月度）；非法形态抛 ``ValueError``。
- **上游 ``limit`` 参数对返回行数无效**（实测 ``limit=3`` 仍回 17 行）——本单元的 ``limit`` 是
  **客户端尾部截取**（排序后取最新 ``limit`` 行），不做上游分页（响应自带 ``stats.number_of_records``）。
- **月度粒度**：上游 ``date`` 恒为当月 1 日（``2024-01-01``），本单元归一为 ``month=YYYY-MM``。
- **官方未公示数值限速**（响应 ``stats.rate_limit: "No"``）；月度任务量级 + 单次 1 请求，
  仍按惯例不并发、不轮询。
- **历史深度**：DEU 月度序列自 ``2015-01`` 起（``2014-01`` 及更早 0 记录，实测）。
- **首连偶发失败**：仅重试 1 次（共 2 次尝试，``RETRY_BACKOFF_S`` 退避），不做频次对抗。
- **失败即红**：0 行 → 抛 ``RuntimeError``（点名 ISO3 坑位与窗口），不静默返回空列表。
- **golden 锚固定历史月**（DEU 2024-01，实测两次一致）——绝不锚当期（实测当前最新月为 2026-09）。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

SOURCE = "ember"
API_KEY_ENV = "EMBER_API_KEY"
DATA_URL = "https://api.ember-energy.org/v1/electricity-generation/monthly"

HEADERS = {"Accept": "application/json"}
TIMEOUT = 30                  # 秒
RETRY_ATTEMPTS = 2            # 首连偶发失败：仅重试 1 次（共 2 次尝试）
RETRY_BACKOFF_S = 2.0
MASKED_KEY = "***"            # 行内 url 的密钥占位

DEFAULT_ENTITY_CODE = "DEU"   # 工单 brief 已实测口径（ISO3）
LIVE_WINDOW_MONTHS = 6        # 缺省窗口长度：月度发布滞后 ≈1 月，留 6 月裕量
MAX_LIVE_WINDOW_MONTHS = 36

_CODE_RE = re.compile(r"^[A-Z0-9]{2,8}$")
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")  # 严格 YYYY-MM（strptime 会宽容 "2024-1"）
_MONTH_FMT = "%Y-%m"


def _api_key() -> str:
    """从环境读 key；缺失/空白 → 显式报错（禁止裸跑）。"""
    key = (os.environ.get(API_KEY_ENV) or "").strip()
    if not key:
        raise RuntimeError(
            f"ember: 缺少 API key —— 未注入环境变量 {API_KEY_ENV}"
            "（运行时由 fd-industry-source-keys envFrom 注入；本地需 source secrets/env 后再跑）"
        )
    return key


def _build_url(params: list[tuple[str, str]], api_key: str | None = None) -> str:
    """拼查询串；``api_key=None`` → **脱敏** URL（``api_key=***``）。

    脱敏 URL 用于行内 ``url`` 字段与全部报错信息 —— 密钥因此绝不进入行数据、日志或异常文本。
    """
    head = (
        f"api_key={MASKED_KEY}"
        if api_key is None
        else "api_key=" + urllib.parse.quote(str(api_key), safe="")
    )
    tail = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params)
    return f"{DATA_URL}?{head}&{tail}"


def _detail(body: str) -> str:
    """取错误体摘要（Ember 缺 key 返回 ``{"detail":"No API key set"}``）。"""
    try:
        doc = json.loads(body)
    except json.JSONDecodeError:
        return body[:200]
    if isinstance(doc, dict):
        return str(doc.get("detail") or doc.get("error") or doc)[:200]
    return str(doc)[:200]


def _request(params: list[tuple[str, str]], api_key: str) -> dict:
    """GET JSON；瞬时错误重试 1 次；鉴权/参数错误立即显式报错（不重试）。

    报错文本只用脱敏 URL —— 密钥不出现在异常信息里。
    """
    url = _build_url(params, api_key=api_key)
    display = _build_url(params)
    last: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
                raw = resp.read()
            payload = json.loads(raw.decode("utf-8", "replace"))
            if not isinstance(payload, dict):
                raise RuntimeError(f"unexpected payload {type(payload).__name__}")
            return payload
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            if exc.code in (401, 403):
                raise RuntimeError(
                    f"ember: 鉴权失败 HTTP {exc.code} —— 检查环境变量 {API_KEY_ENV} 是否有效"
                    f"（凭证来源 https://ember-energy.org/data/api/）；{display}；{_detail(body)}"
                ) from exc
            if 400 <= exc.code < 500 and exc.code not in (408, 429):
                raise RuntimeError(
                    f"ember: HTTP {exc.code} —— {display} —— {_detail(body)}"
                ) from exc
            last = exc
        except Exception as exc:  # noqa: BLE001 - 瞬时网络错误：仅重试 1 次
            last = exc
        if attempt + 1 < RETRY_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_S)
    raise RuntimeError(
        f"ember: 请求失败（{RETRY_ATTEMPTS} 次尝试）—— {display} —— "
        f"{type(last).__name__}: {last}"
    )


def _num(value) -> float | None:
    """数值列 → float；缺失/不可解析 → ``None``（缺测，不猜测）。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _text(value) -> str | None:
    """文本归一：去空白；空 → ``None``。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool(value) -> bool | None:
    """布尔列归一（上游为 JSON bool；形态异常 → ``None``，不猜测）。"""
    return value if isinstance(value, bool) else None


def _month_text(value, label: str) -> str:
    """校验 ``YYYY-MM``（严格零填充，月 01-12）；非法即抛 ``ValueError``（参数错误不静默）。"""
    text = str(value or "").strip()
    if not text or not _MONTH_RE.match(text):
        raise ValueError(f"{label} must be YYYY-MM, got {value!r}")
    datetime.strptime(text, _MONTH_FMT)  # 双保险：非法月抛 ValueError
    return text


def _shift_month(month: str, delta: int) -> str:
    """以 ``YYYY-MM`` 为单位的月份平移（``delta`` 可为负）。"""
    year, mon = int(month[:4]), int(month[5:7])
    total = year * 12 + (mon - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _default_window(limit: int) -> tuple[str, str]:
    """生产缺省窗口：当月为终点、向前 ``max(LIVE_WINDOW_MONTHS, limit/17)`` 个月（上限 36）。"""
    end = date.today().strftime(_MONTH_FMT)
    months = min(MAX_LIVE_WINDOW_MONTHS, max(LIVE_WINDOW_MONTHS, 1 + (limit // 17)))
    return _shift_month(end, -(months - 1)), end


def _row(rec: dict, url: str, scraped_at: str) -> dict | None:
    """一条 Ember 记录 → 一行；缺 date/series 的行跳过（无法定位）。"""
    if not isinstance(rec, dict):
        return None
    day = _text(rec.get("date"))
    series = _text(rec.get("series"))
    if not day or not series:
        return None
    return {
        "month": day[:7],  # 月度粒度：上游 date 恒为当月 1 日
        "entity": _text(rec.get("entity")),
        "entity_code": _text(rec.get("entity_code")),
        "series": series,
        "is_aggregate_series": _bool(rec.get("is_aggregate_series")),
        "is_aggregate_entity": _bool(rec.get("is_aggregate_entity")),
        "generation_twh": _num(rec.get("generation_twh")),
        "share_of_generation_pct": _num(rec.get("share_of_generation_pct")),
        "url": url,
        "source": SOURCE,
        "scraped_at": scraped_at,
    }


def run_ember(limit: int = 100, entity_code: str = DEFAULT_ENTITY_CODE,
              start_date: str | None = None, end_date: str | None = None,
              series: str | None = None) -> list[dict]:
    """取 Ember 国别月度发电结构行（``month``+``series`` 升序，返回最新 ``limit`` 行）。

    - ``entity_code`` 缺省 ``DEU``（**ISO3**；``DE`` 会被上游静默判空）；
    - ``series`` 可选，精确序列名（如 ``Wind``；未知名 → 0 行 → 抛错并列出实测枚举）；
    - ``start_date``/``end_date`` 必须成对给出（``YYYY-MM``）；缺省 = 最近 6 个月（至当月）；
    - golden 重放传固定历史月（``2024-01`` DEU）+ ``limit=17``，结果恒定；
    - 0 行 → 抛 ``RuntimeError``（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    code = str(entity_code or "").strip().upper()
    if not _CODE_RE.match(code):
        raise ValueError(
            f"entity_code must be an Ember ISO3 country code (e.g. DEU), got {entity_code!r}"
        )
    if start_date or end_date:
        if not (start_date and end_date):
            raise ValueError("start_date and end_date must be given together")
        window = (_month_text(start_date, "start_date"), _month_text(end_date, "end_date"))
    else:
        window = _default_window(limit)

    params: list[tuple[str, str]] = [
        ("entity_code", code),
        ("start_date", window[0]),
        ("end_date", window[1]),
    ]
    if series is not None:
        series_name = str(series).strip()
        if not series_name:
            raise ValueError(f"series must be a series name when given, got {series!r}")
        params.append(("series", series_name))

    api_key = _api_key()
    display = _build_url(params)
    payload = _request(params, api_key)
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("ember: 负载缺少 data 数组（上游形状变化？）")

    scraped_at = datetime.now(timezone.utc).isoformat()
    rows = [row for row in (_row(rec, display, scraped_at) for rec in data) if row]
    if not rows:
        raise RuntimeError(
            f"ember: {window[0]}..{window[1]} entity_code={code}"
            f"{f' series={series!r}' if series else ''} 返回 0 行 —— "
            "检查 entity_code 是否为 ISO3（DEU 而非 DE；DE 会静默判空）、窗口是否早于 2015-01，"
            "或 series 是否为实测枚举名之一"
        )
    rows.sort(key=lambda r: (r["month"], r["series"]))  # 确定性：月 + 序列名
    return rows[-limit:]


if __name__ == "__main__":  # 单页冒烟：python3 spiders/ember/spider.py
    print(json.dumps(run_ember(limit=3, start_date="2024-01", end_date="2024-01"),
                     ensure_ascii=False, indent=2))