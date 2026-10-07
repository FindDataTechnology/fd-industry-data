"""spiders/eia-v2 —— EIA v2 API：美国小时级电网运行数据（``electricity/rto/region-data``）。

数据表面（公开 REST，**key 鉴权**；工单 ``reports/health-tickets/20261007-eia-v2-b55acdfb.yaml``）：

- ``GET https://api.eia.gov/v2/electricity/rto/region-data/data/``
  参数白名单（仅发实测过的）：``api_key``（env ``EIA_API_KEY``）、``frequency=hourly``、
  ``data[0]=value``、``facets[respondent][]=<BA 码>``、``facets[type][]=<D|DF|NG|TI>``（可选）、
  ``start=YYYY-MM-DDTHH``、``end=YYYY-MM-DDTHH``、``sort[0][column]=period``、
  ``sort[0][direction]=asc``、``offset``、``length``（≤5000）。
- 返回 ``{"warnings"?: [...], "response": {"total": "<字符串>", "dateFormat", "data": [...]},
  "request", "apiVersion"}``；行形如
  ``{"period","respondent","respondent-name","type","type-name","value","value-units"}``。
  实测（2026-10-07，CISO）四类序列：``D`` 实际需求、``DF`` 日前需求预测、``NG`` 净发电、
  ``TI`` 总联络线交换，单位均为 ``megawatthours``。

坑位与容错（brief.notes / 侦察口径逐条落实，均实测）：

- **key 纪律**：``api_key`` 走查询参数、从 ``os.environ[EIA_API_KEY]`` 读取；缺 key 立即抛
  ``RuntimeError``（显式报错，禁止裸跑）。密钥绝不硬编码、绝不写入任何文件/日志/行数据。
- **行内 ``url`` 已脱敏**（``api_key=***``）——落盘行不含密钥，可安全入库。
- **数值全字符串**：``value`` 与 ``response.total`` 均为字符串（``"22497"``），本单元归一为
  ``float`` / ``int``；不可解析 → ``None``（缺测，不猜测）。
- **限流**：<9000 次/时、<5 次/秒。单次运行 1–2 个请求（分页时按 ``PAGE_PAUSE_S`` 间隔），
  远低于限流；不并发、不轮询。
- **单请求 ≤5000 行**：``length>5000`` 会被上游忽略并回 ``parameter out of range: length``
  告警，故本单元硬顶 5000；超过 5000 行的需求走 ``offset`` 分页，且只取窗口**尾部**
  （``offset = total - needed`` 直达），不做全量拉取。
- **截断信号**：``warnings[].warning == "incomplete return"`` 表示返回行数 < ``total``
  （实测即使 ``total=16``、仅因 ``length=5`` 也会出现该告警），可靠判据是
  ``len(data) < int(response.total)``；本单元只用后者。
- **错误码（403）**：``API_KEY_MISSING`` / ``API_KEY_INVALID`` → 立即抛 ``RuntimeError``
  并指名解决办法；其余 4xx 不重试（避免放大）。
- **首连偶发失败**：仅重试 1 次（共 2 次尝试，``RETRY_BACKOFF_S`` 退避），不做频次对抗。
- **四序列播报滞后不同**（2026-10-07T11 UTC 实测 CISO 最末行）：``D`` 近实时（当前小时）、
  ``DF`` ≈ -4h、``NG`` ≈ -5h、``TI`` ≈ -28h。故窗口**尾部小时只含部分序列**，属上游行为，
  原样透出，不在客户端补齐或丢弃。
- **历史深度**：hourly region-data 自 ``2019-01-01T00`` 起（CISO 实测；2018-12-31 窗口返回空）。
- **失败即红**：窗口内无任何行 → 抛 ``RuntimeError``（不静默返回空列表，避免「空洞通过」）。
- **golden 锚固定历史窗口**（2020-01-01T00..03，实测两次逐字节一致）——绝不锚当期。
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

SOURCE = "eia-v2"
API_KEY_ENV = "EIA_API_KEY"
DATA_URL = "https://api.eia.gov/v2/electricity/rto/region-data/data/"

HEADERS = {"Accept": "application/json"}
TIMEOUT = 30                  # 秒
RETRY_ATTEMPTS = 2            # 首连偶发失败：仅重试 1 次（共 2 次尝试）
RETRY_BACKOFF_S = 2.0
MAX_ROWS_PER_REQUEST = 5000    # EIA 单请求硬上限（>5000 会被上游忽略 length 并告警）
MAX_PAGES = 20                 # 分页上限（≤100k 行），防配置错误导致失控
PAGE_PAUSE_S = 0.3             # 分页间隔：<5 次/秒
MASKED_KEY = "***"             # 行内 url 的密钥占位

DEFAULT_RESPONDENT = "CISO"    # 工单 brief.source_urls 指定（California ISO）
SERIES_TYPES = ("D", "DF", "NG", "TI")  # 实测 region-data 的四个序列（可用 series_type 过滤）
TYPES_PER_HOUR = len(SERIES_TYPES)
LIVE_LAG_HOURS = 48            # 播报滞后裕量（CISO 实测近实时，仍留 2 天）
MAX_LIVE_WINDOW_HOURS = 24 * 30

_CODE_RE = re.compile(r"^[A-Z0-9-]{2,12}$")
_HOUR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}$")  # 严格 YYYY-MM-DDTHH（strptime 会宽容 "2020-1-1T0"）
_HOUR_FMT = "%Y-%m-%dT%H"


def _api_key() -> str:
    """从环境读 key；缺失/空白 → 显式报错（禁止裸跑）。"""
    key = (os.environ.get(API_KEY_ENV) or "").strip()
    if not key:
        raise RuntimeError(
            f"eia-v2: 缺少 API key —— 未注入环境变量 {API_KEY_ENV}"
            "（运行时由 fd-industry-source-keys envFrom 注入；本地需 source secrets/env 后再跑）"
        )
    return key


def _build_url(params: list[tuple[str, str]], api_key: str | None = None) -> str:
    """拼查询串：键里的 ``[]`` 保持字面量（EIA 文档与实测形态），值做百分号编码。

    ``api_key=None`` → 生成**脱敏** URL（``api_key=***``），用于行内 ``url`` 字段与报错信息，
    密钥因此绝不进入行数据、日志或异常文本。
    """
    head = (
        f"api_key={MASKED_KEY}"
        if api_key is None
        else "api_key=" + urllib.parse.quote(str(api_key), safe="")
    )
    tail = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params)
    return f"{DATA_URL}?{head}&{tail}"


def _error_code(body: str) -> str:
    """Extract EIA error code (e.g. API_KEY_MISSING) from an error body, if any."""
    try:
        doc = json.loads(body)
    except json.JSONDecodeError:
        return ""
    err = doc.get("error") if isinstance(doc, dict) else None
    if isinstance(err, dict):
        return str(err.get("code") or "")
    return ""


def _request(params: list[tuple[str, str]], api_key: str) -> dict:
    """GET 一页 JSON；瞬时错误重试 1 次；key 相关错误立即显式报错（不重试）。

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
            code = _error_code(body)
            if code or exc.code in (401, 403):
                raise RuntimeError(
                    f"eia-v2: 鉴权失败 HTTP {exc.code}"
                    f"{f' ({code})' if code else ''} —— 检查环境变量 {API_KEY_ENV} 是否有效"
                    f"（凭证来源 https://www.eia.gov/opendata/register.php）；{display}"
                ) from exc
            if 400 <= exc.code < 500 and exc.code not in (408, 429):
                raise RuntimeError(f"eia-v2: HTTP {exc.code} —— {display} —— {body}") from exc
            last = exc
        except Exception as exc:  # noqa: BLE001 - 瞬时网络错误：仅重试 1 次
            last = exc
        if attempt + 1 < RETRY_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_S)
    raise RuntimeError(
        f"eia-v2: 请求失败（{RETRY_ATTEMPTS} 次尝试）—— {display} —— "
        f"{type(last).__name__}: {last}"
    )


def _num(value) -> float | None:
    """EIA 的字符串数值 → float；空白/不可解析 → ``None``（缺测）。"""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return round(float(text), 6)
    except ValueError:
        return None


def _text(value) -> str | None:
    """文本归一：去空白；空 → ``None``。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _page(params, api_key, offset: int, length: int) -> tuple[int, list]:
    """取一页：返回 ``(total, rows)``。``total`` 为上游字符串化的可用行数（不可解析则退回本页行数）。"""
    payload = _request(
        [*params, ("offset", str(offset)), ("length", str(length))], api_key
    )
    response = payload.get("response")
    if not isinstance(response, dict) or not isinstance(response.get("data"), list):
        raise RuntimeError("eia-v2: 负载缺少 response.data（上游形状变化？）")
    rows = response["data"]
    try:
        total = int(str(response.get("total", "")).strip())
    except ValueError:
        total = len(rows)
    return total, rows


def _fetch_tail(params, api_key, limit: int) -> list:
    """取窗口内**最后** ``limit`` 行（period 升序语义），必要时 offset 直达尾部。

    - 首页只取 ``min(5000, limit)`` 行，用于拿 ``total``（同时覆盖 limit ≥ total 的常见情形）；
    - ``limit < total`` 时按 ``offset = total - limit`` 直达尾部，只拉需要的行（不做全量拉取）；
    - ``limit > 5000`` 时按 5000 行分页，最多 ``MAX_PAGES`` 页（防失控）。
    """
    first_len = min(MAX_ROWS_PER_REQUEST, max(limit, 1))
    total, rows = _page(params, api_key, 0, first_len)
    if total <= len(rows):  # 首页已覆盖全窗口（含 limit ≥ total）
        return rows[-limit:]

    collected: list = []
    offset = max(0, total - limit)
    pages = 0
    while len(collected) < limit and offset < total and pages < MAX_PAGES:
        length = min(MAX_ROWS_PER_REQUEST, limit - len(collected))
        _, data = _page(params, api_key, offset, length)
        if not data:
            break
        collected.extend(data)
        offset += len(data)
        pages += 1
        if offset < total:
            time.sleep(PAGE_PAUSE_S)
    return collected[-limit:]


def _hour_text(value, label: str) -> str:
    """校验 ``YYYY-MM-DDTHH``（严格零填充）；非法即抛 ``ValueError``（参数错误不静默）。"""
    text = str(value or "").strip()
    if not text or not _HOUR_RE.match(text):
        raise ValueError(f"{label} must be YYYY-MM-DDTHH, got {value!r}")
    datetime.strptime(text, _HOUR_FMT)  # 双保险：非法日期/时刻抛 ValueError
    return text


def _default_window(limit: int) -> tuple[str, str]:
    """生产缺省窗口：当前 UTC 小时（截断）为终点、按 limit 反推小时数 + 2 天滞后裕量。"""
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    hours = min(MAX_LIVE_WINDOW_HOURS, math.ceil(limit / TYPES_PER_HOUR) + LIVE_LAG_HOURS)
    start = end - timedelta(hours=hours)
    return start.strftime(_HOUR_FMT), end.strftime(_HOUR_FMT)


def _row(rec: dict, url: str, scraped_at: str) -> dict | None:
    """一条 EIA 记录 → 一行；缺 period 的行跳过（无法定位）。"""
    if not isinstance(rec, dict):
        return None
    period = _text(rec.get("period"))
    if not period:
        return None
    return {
        "period": period,
        "respondent": _text(rec.get("respondent")),
        "respondent_name": _text(rec.get("respondent-name")),
        "series_type": _text(rec.get("type")),
        "series_type_name": _text(rec.get("type-name")),
        "value": _num(rec.get("value")),
        "unit": _text(rec.get("value-units")),
        "url": url,
        "source": SOURCE,
        "scraped_at": scraped_at,
    }


def run_eia_v2(limit: int = 100, respondent: str = DEFAULT_RESPONDENT,
               start: str | None = None, end: str | None = None,
               series_type: str | None = None) -> list[dict]:
    """取 EIA ``rto/region-data`` 小时行（period 升序，返回窗口内最新 ``limit`` 行）。

    - ``respondent`` 缺省 ``CISO``（BA 码，非州名）；``series_type`` 可选 ``D|DF|NG|TI``；
    - ``start``/``end`` 必须成对给出（``YYYY-MM-DDTHH``，UTC）；缺省 = 最近若干小时
      （按 ``limit`` 反推 + 2 天滞后裕量）；
    - golden 重放传固定历史窗口（2020-01-01T00..03）+ ``limit=16``，结果恒定；
    - 窗口内无行 → 抛 ``RuntimeError``（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    code = str(respondent or "").strip().upper()
    if not _CODE_RE.match(code):
        raise ValueError(
            f"respondent must be an EIA balancing-authority code (e.g. CISO/PJM/ERCO), got {respondent!r}"
        )
    if series_type is not None:
        series_type = str(series_type).strip().upper()
        if series_type not in SERIES_TYPES:
            raise ValueError(
                f"series_type must be one of {SERIES_TYPES}, got {series_type!r}"
            )
    if start or end:
        if not (start and end):
            raise ValueError("start and end must be given together")
        window = (_hour_text(start, "start"), _hour_text(end, "end"))
    else:
        window = _default_window(limit)

    params: list[tuple[str, str]] = [
        ("frequency", "hourly"),
        ("data[0]", "value"),
        ("facets[respondent][]", code),
    ]
    if series_type:
        params.append(("facets[type][]", series_type))
    params += [
        ("start", window[0]),
        ("end", window[1]),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "asc"),
    ]

    api_key = _api_key()
    display = _build_url(params)
    scraped_at = datetime.now(timezone.utc).isoformat()
    records = _fetch_tail(params, api_key, limit)
    rows = [row for row in (_row(rec, display, scraped_at) for rec in records) if row]
    if not rows:
        raise RuntimeError(
            f"eia-v2: 窗口 {window[0]}..{window[1]} 内 respondent={code}"
            f"{f' series_type={series_type}' if series_type else ''} 无任何行 —— "
            "检查 BA 码/窗口（hourly region-data 自 2019-01-01T00 起）"
        )
    rows.sort(key=lambda r: r["period"])  # 稳定排序：同小时各序列保持上游顺序
    return rows[-limit:]


if __name__ == "__main__":  # 单页冒烟：python3 spiders/eia-v2/spider.py
    print(json.dumps(run_eia_v2(limit=4), ensure_ascii=False, indent=2))