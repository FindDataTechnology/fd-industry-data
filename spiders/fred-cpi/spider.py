"""spiders/fred-cpi —— 美国 CPI「在外就餐 / 在家就餐」月度指数（St. Louis Fed FRED CSV）。

数据表面（2026-11-07 实测，见同目录 README）：

- CSV 直链：``GET https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES_ID>``
  返回两列 ``observation_date,<SERIES_ID>``，月度行日期为当月 1 日。
- 本单元两条序列（BLS CPI-U，1982-84=100，**季调 SA**）：

  * ``CUSR0000SEFV``  —— Food Away From Home（在外就餐），1953-01 起；
  * ``CUSR0000SAF11`` —— Food At Home（在家就餐），1952-01 起。

  工单预告的 ``CUSR0000SEFV02`` / ``CUSR0000SEFV11`` **实测不存在**（直连
  HTTP 404，FRED 站内同名页亦 404）；正确序列号是去掉两位后缀的
  ``CUSR0000SEFV`` / ``CUSR0000SAF11``（页面明确标注 "Seasonally Adjusted"）。
  前导 ``S`` = seasonally adjusted；``CUUR…`` 是 NSA 版本，不得混用。

- **通道定谳（2026-11-07 实测）**：本机直连 fred.stlouisfed.org 的 CSV 端点
  **可用**（CPIAUCSL / CUSR0000SEFV 连续 6 次 200，无挑战页）。工单里
  "Mac-direct 被挑战页挡" 的结论来自对不存在序列号的请求——那 404 是真实
  404（响应体 ``<title>Error - St. Louis Fed</title>``），不是反爬挑战页。
  为生产出口不确定留后路，spider 仍是「直连优先 → 网络级失败经环境代理
  （``HTTPS_PROXY``/``HTTP_PROXY``，由 manifest ``egress_secret:
  fd-industry-egress-fx01`` 在运行侧注入）重试一次」，不硬编码代理地址。

口坑（实测落实）：

- **缺测占位 ``.``**：FRED CSV 用 ``.`` 表示缺测；该行必须跳过，绝不写 0、
  绝不插值。
- **挑战页 / 空 CSV / 列名不符一律判红**：HTTP 非 200 → 红（错误体前 160
  字符进异常文本，保留证据）；表头必须恰为 ``observation_date,<series_id>``；
  数据行必须恰两列、日期 ``YYYY-MM-DD``、数值可解析——任一漂移抛
  ``RuntimeError``，不静默降级。
- **单序列零可用行 → 红**（CPI 序列不可能没有历史；空 CSV = 上游口径变更）。
- **limit 语义**：总行数上限，按整月贴合——保留最新月份、裁最旧月份，
  绝不把「同月两条序列」裁成单边（对照对必须成对）；limit 容不下单月
  （<2 行）→ 红。默认 100 会截断历史，全量回补请显式传大 limit（如 3000）。
- 输出顺序确定性：序列按入参顺序，月内按日期升序。
- 无 schedule：静默合入，点亮留人工门。

工单：``reports/health-tickets/20261107-fred-cpi-ba279d6a.yaml``（``kind=generate``）。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

SOURCE = "fred-cpi"
CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"

# 序列 id -> 人类可读名（series_name 列；不参与取数）
SERIES_NAMES = {
    "CUSR0000SEFV": "CPI-U Food Away From Home (seasonally adjusted)",
    "CUSR0000SAF11": "CPI-U Food At Home (seasonally adjusted)",
}
DEFAULT_SERIES = (
    ("CUSR0000SEFV", SERIES_NAMES["CUSR0000SEFV"]),
    ("CUSR0000SAF11", SERIES_NAMES["CUSR0000SAF11"]),
)
DATE_COLUMN = "observation_date"
MISSING_VALUE = "."
EXPECTED_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv, */*",
}
TIMEOUT = 30  # 秒；单序列 CSV 约 16 KB，超时即失败（失败即红，不重试放大）


def _env_proxy() -> str | None:
    """运行侧注入的代理 URL（manifest egress_secret 提供），无则 None。"""
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return None


def _read(url: str, *, direct: bool) -> bytes:
    """GET 一次。``direct=True`` 显式绕过环境代理；否则按环境代理走。

    代理腿必须走 ``curl_cffi``：标准库 ``urllib`` 在 HTTPS 经代理时不会给
    CONNECT 隧道补 ``Proxy-Authorization``，带认证的出口（本项目
    ``fdproxy:<pass>@100.64.0.x:30080``）会一路 read timeout——2026-11-07 实测
    curl 2.2s/200，urllib 61s 超时。故：直连用 stdlib（零依赖），代理用
    curl_cffi（仓内已装，census-eits/fred-data 先例）。
    """
    if not direct:
        from curl_cffi import requests as _cffi  # 局部导入：直连路径不依赖它

        proxy = _env_proxy()
        try:
            resp = _cffi.get(url, headers=HEADERS, timeout=TIMEOUT,
                             proxies={"http": proxy, "https": proxy},
                             impersonate="chrome120")
        except Exception as exc:  # noqa: BLE001 - 网络级失败交给上层判红
            raise RuntimeError(
                f"fred-cpi: {url} 代理腿失败（{type(exc).__name__}: {exc}）"
            ) from exc
        if resp.status_code != 200:
            head = (resp.text or "")[:160].replace("\n", " ")
            raise RuntimeError(
                f"fred-cpi: {url} HTTP {resp.status_code}"
                f"（响应体前 160 字符：{head!r}）"
            )
        return resp.content

    handlers = [urllib.request.ProxyHandler({})]
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
            status = resp.status
            body = resp.read()
    except urllib.error.HTTPError as exc:
        head = ""
        try:
            head = exc.read()[:160].decode("utf-8", "replace").replace("\n", " ")
        except Exception:  # noqa: BLE001 - 证据读取失败不应掩盖原始错误
            pass
        raise RuntimeError(
            f"fred-cpi: {url} HTTP {exc.code}（响应体前 160 字符：{head!r}）"
        ) from exc
    if status != 200:
        raise RuntimeError(f"fred-cpi: {url} HTTP {status}（期望 200）")
    return body


def _get_csv(series_id: str) -> bytes:
    """取 CSV 字节：直连优先，网络级失败经环境代理重试一次。

    应用级错误（HTTP 404/5xx、挑战页 HTML）不重试——换出口也改不了序列号或
    上游口径，重试只会拖延判红。
    """
    url = CSV_URL.format(series_id=series_id)
    # 顺序：配了出口代理就先代理、再直连。理由（2026-11-07 实测）：本机/集群
    # 直连 fredgraph 均 read timeout（30s 白等 ×2 序列），而 fx 出口 2.2s 拿到
    # 200。生产侧 manifest egress_secret 恒注入代理 → 生产恒走代理腿。
    proxy = _env_proxy()
    if proxy:
        try:
            return _read(url, direct=False)
        except Exception as exc:  # noqa: BLE001 - 代理失败再退回直连一次
            try:
                return _read(url, direct=True)
            except Exception as exc2:  # noqa: BLE001
                raise RuntimeError(
                    f"fred-cpi: {url} 代理与直连均失败"
                    f"（代理 {type(exc).__name__}: {exc}；"
                    f"直连 {type(exc2).__name__}: {exc2}）"
                ) from exc2
    try:
        return _read(url, direct=True)
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - 网络级失败（DNS/连接/超时）
        raise RuntimeError(
            f"fred-cpi: {url} 直连网络级失败（{type(exc).__name__}: {exc}），"
            "且未配置 HTTPS_PROXY/HTTP_PROXY（egress_secret 未注入）——失败即红"
        ) from exc


def _parse_csv(series_id: str, body: bytes, *, url: str, scraped_at: str) -> list[dict]:
    """解析单序列 CSV 为 payload 行；口径漂移一律 ``RuntimeError``。"""
    text = body.decode("utf-8", "replace")
    lines = text.splitlines()
    if not lines or not lines[0].strip():
        raise RuntimeError(f"fred-cpi: {series_id} CSV 为空（首行表头缺失）——判红")
    header = [c.strip() for c in lines[0].split(",")]
    if header != [DATE_COLUMN, series_id]:
        head = lines[0][:160]
        if "<html" in text[:400].lower() or "<!doctype" in text[:400].lower():
            raise RuntimeError(
                f"fred-cpi: {series_id} 返回 HTML 而非 CSV（疑似挑战页/错误页）："
                f"{text[:160]!r}"
            )
        raise RuntimeError(
            f"fred-cpi: {series_id} CSV 列名不符（期望 "
            f"{[DATE_COLUMN, series_id]}，实得 {header}；首行 {head!r}）——判红"
        )

    series_name = SERIES_NAMES.get(series_id, series_id)
    rows: list[dict] = []
    dropped_missing = 0
    for lineno, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        parts = line.split(",")
        if len(parts) != 2:
            raise RuntimeError(
                f"fred-cpi: {series_id} 第 {lineno} 行列数 {len(parts)} != 2：{line[:120]!r}"
                "（上游口径漂移，转人工）"
            )
        date_raw, value_raw = parts[0].strip(), parts[1].strip()
        if value_raw == MISSING_VALUE or value_raw == "":
            # 缺测占位（"." 或空串），绝不造数。空串是真实形态：2025-10 一期
            # 因 BLS 停发只有日期没有值（实测 `2025-10-01,`），该月必须缺席而不是 0。
            dropped_missing += 1
            continue
        if not EXPECTED_DATE_RE.match(date_raw):
            raise RuntimeError(
                f"fred-cpi: {series_id} 第 {lineno} 行日期 {date_raw!r} 非 YYYY-MM-DD"
                "（上游口径漂移，转人工）"
            )
        try:
            value = float(value_raw)
        except ValueError:
            raise RuntimeError(
                f"fred-cpi: {series_id} 第 {lineno} 行数值 {value_raw!r} 不可解析"
                "（上游口径漂移，转人工）"
            ) from None
        rows.append({
            "series_id": series_id,
            "series_name": series_name,
            "date": date_raw,
            "value": value,
            "source": SOURCE,
            "url": url,
            "scraped_at": scraped_at,
        })
    if not rows:
        raise RuntimeError(
            f"fred-cpi: {series_id} CSV 无可用行"
            f"（{dropped_missing} 行缺测占位；空序列=上游口径变更）——判红"
        )
    return rows


def _normalize_series(series) -> list[tuple[str, str]]:
    """``series`` 归一为 [(id, name)]：接受 "\"SID\"" 或 ("SID", "Name") 条目。"""
    if series is None:
        return list(DEFAULT_SERIES)
    if isinstance(series, str):
        series = [series]
    out: list[tuple[str, str]] = []
    for entry in series:
        if isinstance(entry, str):
            sid = entry.strip()
            name = SERIES_NAMES.get(sid, sid)
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            sid, name = str(entry[0]).strip(), str(entry[1]).strip()
        else:
            raise ValueError(
                f"fred-cpi: series 条目 {entry!r} 非法（须为 \"SID\" 或 (\"SID\", \"Name\")）"
            )
        if not sid:
            raise ValueError("fred-cpi: series 条目存在空 id")
        if sid not in {s for s, _ in out}:
            out.append((sid, name))
    if not out:
        raise ValueError("fred-cpi: series 不能为空列表")
    return out


def _trim_whole_months(rows: list[dict], limit: int,
                       series_order: dict[str, int]) -> list[dict]:
    """按整月贴合 limit：保留最新月、裁最旧月，绝不裁成单边对照。

    limit 容不下单月（< 两条序列各一行）→ 红。
    """
    if len(rows) <= limit:
        return rows
    per_date: dict[str, int] = {}
    for r in rows:
        per_date[r["date"]] = per_date.get(r["date"], 0) + 1
    kept_dates: set[str] = set()
    kept_n = 0
    for d in sorted(per_date, reverse=True):
        n = per_date[d]
        if kept_n + n > limit:
            break
        kept_dates.add(d)
        kept_n += n
    if not kept_dates:
        raise RuntimeError(
            f"fred-cpi: limit={limit} 容不下单月行数（每月 {len(series_order)} 行），"
            "请提高运行 limit"
        )
    kept = [r for r in rows if r["date"] in kept_dates]
    kept.sort(key=lambda r: (series_order[r["series_id"]], r["date"]))
    return kept


def run_fred_cpi(limit: int = 100, series=DEFAULT_SERIES) -> list[dict]:
    """取 FRED 的 CPI 在外就餐 / 在家就餐月度指数（SA，1982-84=100）。

    Args:
        limit: 总行数上限；按整月贴合（保最新月、裁最旧月，绝不裁成单边）。
            默认 100 会截断历史——全量回补请显式传大 limit（两条序列各
            约 885/897 行，用 3000 可全覆盖）。
        series: 序列清单，缺省 ``CUSR0000SEFV`` + ``CUSR0000SAF11``；条目为
            ``"SID"`` 或 ``("SID", "Name")``。换序列须确认仍是 SA 且基期
            1982-84=100，否则会造成口径污染。

    Returns:
        list of dict：``{series_id, series_name, date（YYYY-MM-DD）, value,
        source, url, scraped_at}``；缺测占位 ``.`` 的行已跳过。

    Raises:
        RuntimeError: HTTP 非 200 / 挑战页 / 空 CSV / 列名不符 / 数值不可解析 /
            单序列零可用行 / limit 容不下单月（失败即红）。
        ValueError: ``limit`` 或 ``series`` 非法。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []
    wanted = _normalize_series(series)
    order = {sid: i for i, (sid, _name) in enumerate(wanted)}
    scraped_at = datetime.now(timezone.utc).isoformat()

    rows: list[dict] = []
    for sid, _name in wanted:
        url = CSV_URL.format(series_id=sid)
        body = _get_csv(sid)
        rows.extend(_parse_csv(sid, body, url=url, scraped_at=scraped_at))

    rows.sort(key=lambda r: (order[r["series_id"]], r["date"]))
    return _trim_whole_months(rows, limit, order)


if __name__ == "__main__":  # 冒烟：./.venv/bin/python spiders/fred-cpi/spider.py
    print(json.dumps(run_fred_cpi(limit=6), ensure_ascii=False, indent=2))
