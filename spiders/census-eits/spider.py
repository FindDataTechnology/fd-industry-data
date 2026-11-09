"""spiders/census-eits —— 美国零售/批发月度销售与库存（Census EITS：MARTS/MWTS）取数。

数据表面（api.census.gov 公开 REST，key= 查询参数传递；工单 brief.notes）：

- 零售：``GET https://api.census.gov/data/timeseries/eits/mrts``（MARTS 月度零售，
  旧名 mots 不存在勿用）
- 批发：``GET https://api.census.gov/data/timeseries/eits/mwts``（月度批发库存/销售）
- 返回二维数组：首行表头 ``[cell_value, data_type_code, time_slot_id, time,
  category_code, seasonally_adj, us]``，其后每行一条指标值。

三种取数口径（2026-11-07 餐饮垂直线波0 滚动窗改造）：

- **滚动窗（缺省，``months_back``，缺省 13）**：先探各 program 最新可得期次
  （自当月起逐级放宽 3/6/12 个月），再从该期往回取 ``months_back`` 个月。
  实测（2026-11-07）：mrts/mwts 的 NSA 最新可得月均为 2026-08（上游 NSA 滞后
  约 3 个月），多期语法为 ``time=from+YYYY-MM+to+YYYY-MM``（闭区间；实测
  ``time=2024-01+2024-02`` 与 ``time=2023+2024`` 均报 400 unsupported date/time
  format）。区间响应行数 = 月数 × 指标码数（mrts 44X72 为 4 码/月，mwts 42 为
  10 码/月），实测 13 个月单请求 130/52 行无截断，无行数上限；仍按 ≤12 个月
  分片请求，避免单请求过大。窗口行数按**整月**粒度贴合 ``limit``（保留最新
  月、裁最旧月，绝不月中截断）——limit 容不下单月（<14 行）时红。
- **固定月（仅当显式传 ``time``）**：``time=YYYY-MM`` 单月快照，两 program 各一
  请求——2020-01 快照的旧行为由该通道保留（golden 001 显式传 time=2020-01）。
- **历史回补（仅当显式传 ``time_range``）**：``"YYYY-MM/YYYY-MM"`` 闭区间逐月
  各一请求，单 program 失败整体红。

实测请求参数（只发这些）：

- ``get=cell_value,data_type_code,time_slot_id`` + ``for=us:*``
- 零售 ``category_code=44X72``（零售贸易含餐饮总计）、批发 ``category_code=42``
  （商户批发总计）
- ``seasonally_adj=no``（不经季调原值）

口坑（brief.notes + 2026-11-07 实测逐条落实）：

- **必填谓词 category_code + seasonally_adj**：缺任一上游即 400
  ``error: missing required variable/predicate: ...``（实测）。
- **无 key / key 非法时上游 302 → HTML "Missing Key" 错误页（HTTP 200 形态）**：
  响应体首字符必须为 ``[``，否则显式报错——绝不允许静默降级为无 key 通道裸跑。
- **HTTP 200 + 空响应体 = 该 time 区间无数据**（实测 mrts NSA 2026-09 之后即
  如此）：探测路径将其视为"放宽探测窗"信号逐级回溯；取数路径将其视为失败红
  （窗口内应有数据，静默吞掉=口径漂移）。注意该形态也可能是出口软封（实测
  同一查询换出口即有数据）——故空体一律先重试一次再判。
- key 一律从 ``os.environ["CENSUS_API_KEY"]`` 读取；缺失/空值 → ``RuntimeError``
  （失败即红），**绝不硬编码/落文件**。
- **golden 锚定与复锚**：001 锚固定月 2020-01 实测值；002 锚滚动窗内稳定历史
  月 2024-01（``months_back=60`` 使锚月约 3 年内不滚出窗）。**上游年度基准修订
  会改写历史月值**（实测 2026-10-07→10-28 之间 mwts 2020-01 SM 488638→483141、
  IM 684010→679282 被修订）——锚值失真属预期维护项，需人工复锚，不视为代码
  缺陷。
- ``cell_value`` 数值化：可解析为 float 则转 float，否则原样字符串透出。
- 容错：单 program 失败整体红（无"部分成功"口径）；零可用行 → 抛
  ``RuntimeError``（失败即红，不静默返回空列表）。
- **需代理出口**：api.census.gov 对本仓库默认直连站点返回 403（Cloudflare IP 级
  拦截页，2026-11-07 实测），单元依赖运行环境的 ``HTTPS_PROXY``/``HTTP_PROXY``
  环境变量（标准库 urllib 缺省行为，本单元不硬编码代理地址）；manifest 通过
  ``egress_secret: fd-industry-egress-fx01`` 声明所需出口。

工单：``reports/health-tickets/20261007-census-eits-0a67b68f.yaml``（``kind=generate``）
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

SOURCE = "census-eits"
EITS_URL = "https://api.census.gov/data/timeseries/eits/{program}"
CATEGORY_BY_PROGRAM = {
    "mrts": "44X72",  # 零售贸易与餐饮服务总计（MARTS）
    "mwts": "42",     # 商户批发总计（MWTS）
}
DEFAULT_MONTHS_BACK = 13   # 滚动窗缺省：最新可得月起往回 13 个月
CHUNK_MONTHS = 12          # 分片：单请求至多跨 12 个月（实测 13 个月 130 行亦无截断，留裕度）
PROBE_WIDEN_MONTHS = (3, 6, 12)  # 最新可得月探测逐级放宽（当前上游 NSA 滞后约 3 个月）
RETRY_SLEEP_SECONDS = 2    # 空响应体重试间隔（空体也可能是出口软封，先重试再判）

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
TIMEOUT = 30  # 秒；单请求（≤12 个月 × ≤10 指标码）约 20 KB，超时即失败（失败即红，不重试放大）


class _EmptyBody(RuntimeError):
    """HTTP 200 + 空响应体：上游对该 time 区间无数据（或出口软封）。"""


def _api_key() -> str:
    """从环境读 Census key；缺失/空 → RuntimeError（失败即红，不静默降级）。"""
    key = (os.environ.get("CENSUS_API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "census-eits: 缺少环境变量 CENSUS_API_KEY（key= 查询参数传递；"
            "拒绝无 key 通道裸跑——上游会 302 到 HTML Missing Key 页）"
        )
    return key


def _get_rows(url: str) -> list[list]:
    """取二维数组载荷；**首字符必须 ``[``**。

    - 空响应体 → ``_EmptyBody``（"该区间无数据"或出口软封，由调用方定夺）；
    - 非 JSON 数组（无 key/超限的 HTML 错误页，HTTP 仍可能为 200 形态）→ 红；
    - 无表头行 → 红（上游口径已变，转人工）。
    """
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        raw = resp.read()
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        raise _EmptyBody("census-eits: HTTP 200 空响应体（该 time 区间无数据或出口软封）")
    if not text.startswith("["):
        head = text[:120].replace("\n", " ")
        raise RuntimeError(
            "census-eits: 响应非 JSON 数组（疑似无 key/超限的 HTML 错误页）："
            f"{head!r}"
        )
    payload = json.loads(text)
    if not (isinstance(payload, list) and len(payload) >= 2
            and isinstance(payload[0], list)):
        raise RuntimeError("census-eits: 载荷无表头行（上游口径已变，转人工）")
    return payload


def _get_rows_with_retry(url: str) -> list[list]:
    """空响应体先重试一次再判（空体也可能是出口软封）；其余失败直接红。"""
    try:
        return _get_rows(url)
    except _EmptyBody:
        time.sleep(RETRY_SLEEP_SECONDS)
        return _get_rows(url)  # 仍空则 _EmptyBody 上抛，由调用方定夺


def _num(value):
    """cell_value 归一：可解析为 float 则转 float，脏值原样字符串透出。"""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None if value is None else str(value)


def _parse_period(raw: str) -> str:
    """校验 ``YYYY-MM`` 期次串；非法即 ``ValueError``。"""
    text = str(raw).strip()
    parts = text.split("-")
    if (len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 2
            or not parts[0].isdigit() or not parts[1].isdigit()
            or not 1 <= int(parts[1]) <= 12):
        raise ValueError(f"census-eits: 期次 {raw!r} 必须是 YYYY-MM")
    return text


def _month_sub(period: str, n: int) -> str:
    """``YYYY-MM`` 往前推 n 个月（n≥0）。"""
    y, m = (int(x) for x in _parse_period(period).split("-"))
    total = y * 12 + (m - 1) - n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _month_range(start: str, end: str) -> list[str]:
    """闭区间 ``YYYY-MM`` 月序（含两端）；end 早于 start → ``ValueError``。"""
    sy, sm = (int(x) for x in start.split("-"))
    ey, em = (int(x) for x in end.split("-"))
    if (ey, em) < (sy, sm):
        raise ValueError(f"census-eits: 区间倒挂 start={start} end={end}")
    months: list[str] = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return months


def _chunk_windows(start: str, end: str,
                   size: int = CHUNK_MONTHS) -> list[tuple[str, str]]:
    """把闭区间切成至多 size 个月的首尾对（分片请求，避免单请求过大）。"""
    months = _month_range(start, end)
    return [(months[i], months[min(i + size, len(months)) - 1])
            for i in range(0, len(months), size)]


def _row_url(program: str, category: str, time_expr: str, key: str) -> tuple[str, str]:
    """组装请求 URL，返回 (带 key 的真 URL, 去 key 的展示 URL)。

    key 绝不落盘/入行数据，展示 URL 只供行数据 url 字段复演请求形态。
    """
    url = (
        f"{EITS_URL.format(program=program)}?get=cell_value,data_type_code,"
        "time_slot_id&for=us:*"
        f"&time={time_expr}&category_code={category}&seasonally_adj=no&key={key}"
    )
    return url, url.replace(f"&key={key}", "")


def _fetch_payload(program: str, category: str, time_expr: str, key: str,
                   *, empty_ok: bool = False) -> list[dict] | None:
    """发一次区间请求并解析为行字典列表。

    ``empty_ok=True``（探测路径）：空体（重试后仍空）返回 None；其余失败红。
    ``empty_ok=False``（取数路径）：空体也红——窗口内应有数据，静默吞掉=口径漂移。
    """
    url, display_url = _row_url(program, category, time_expr, key)
    try:
        payload = _get_rows_with_retry(url)
    except _EmptyBody:
        if empty_ok:
            return None
        raise RuntimeError(
            f"census-eits: {program} {time_expr} 窗口空响应体"
            "（窗口内应有数据，出口软封或上游口径变更，转人工）"
        ) from None
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - 单 program 拉取失败 → 整体红
        raise RuntimeError(
            f"census-eits: {program} 拉取失败 {type(exc).__name__}: {exc}"
        ) from exc
    header = [str(col) for col in payload[0]]
    rows: list[dict] = []
    for line in payload[1:]:
        if not isinstance(line, list) or len(line) != len(header):
            continue
        rec = dict(zip(header, line))
        rows.append({
            "program": program,
            "time": rec.get("time"),
            "category_code": rec.get("category_code"),
            "data_type_code": rec.get("data_type_code"),
            "seasonally_adj": rec.get("seasonally_adj"),
            "cell_value": _num(rec.get("cell_value")),
            "time_slot_id": rec.get("time_slot_id"),
            "url": display_url,
            "source": SOURCE,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })
    return rows


def _latest_available(program: str, category: str, key: str) -> str | None:
    """探测 program 最新可得期次：自当月逐级放宽 3/6/12 个月，取响应最大 time。

    全窗皆空（重试后）→ None（调用方红）。响应里出现的时间即上游实有的期次，
    绝不用本地时钟臆造"最新月"——上游 NSA 滞后约 3 个月（2026-11-07 实测）。
    """
    cur = datetime.now(timezone.utc).strftime("%Y-%m")
    for width in PROBE_WIDEN_MONTHS:
        start = _month_sub(cur, width)
        rows = _fetch_payload(program, category, f"from+{start}+to+{cur}", key,
                              empty_ok=True)
        if rows:
            return max(r["time"] for r in rows if r.get("time"))
    return None


def _fetch_window(program: str, category: str, start: str, end: str,
                  key: str) -> list[dict]:
    """闭区间取数（≤12 个月分片，各片一次 from/to 请求）。"""
    rows: list[dict] = []
    for chunk_start, chunk_end in _chunk_windows(start, end):
        rows.extend(_fetch_payload(
            program, category, f"from+{chunk_start}+to+{chunk_end}", key))
    return rows


def _trim_whole_months(rows: list[dict], limit: int) -> list[dict]:
    """按整月贴合 limit：保留最新月、裁最旧月，绝不月中截断。

    limit 容不下单月（< 最低单月行数）→ 红（失败即红，不静默掏空窗口）。
    """
    if len(rows) <= limit:
        return rows
    kept = list(rows)
    for period in sorted({r["time"] for r in rows if r.get("time")}):
        if len(kept) <= limit:
            break
        kept = [r for r in kept if r.get("time") != period]
    if not kept:
        raise RuntimeError(
            f"census-eits: limit={limit} 容不下单月行数"
            "（滚动窗单月 mrts+mwts 合计 ≥14 行），请提高运行 limit"
        )
    return kept


def run_census_eits(limit: int = 100, months_back: int = DEFAULT_MONTHS_BACK,
                    time: str | None = None,
                    time_range: str | None = None) -> list[dict]:
    """取零售（mrts/44X72）与批发（mwts/42）未季调指标行。

    口径（按优先级）：

    - 显式 ``time``（``YYYY-MM``）→ 固定月快照（旧行为通道，golden 001）；
    - 显式 ``time_range``（``"YYYY-MM/YYYY-MM"`` 闭区间）→ 逐月历史回补；
    - 缺省 → **滚动窗**：探各 program 最新可得月，往回 ``months_back`` 个月
      （缺省 13），行数按整月贴合 ``limit``（保最新、裁最旧）。

    拉取失败、HTML 错误页、空响应体（取数路径）或零可用行 → 抛 ``RuntimeError``
    （失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    key = _api_key()
    if time is not None or time_range is not None:
        # 显式通道：固定月 / 逐月回补（旧行为，逐月各一请求，单 program 失败整体红）
        if time is not None:
            periods = [_parse_period(time)]
        else:
            text = str(time_range).strip()
            if "/" not in text:
                raise ValueError(
                    f"census-eits: time_range {time_range!r} 须为 'YYYY-MM/YYYY-MM'"
                )
            start, end = (_parse_period(p) for p in text.split("/", 1))
            periods = _month_range(start, end)
        rows: list[dict] = []
        for period in periods:
            for program, category in CATEGORY_BY_PROGRAM.items():
                rows.extend(_fetch_payload(program, category, period, key))
        if not rows:
            raise RuntimeError("census-eits: 两 program 取数均为空（上游空窗或口径变更，转人工）")
        return rows[:limit]

    # 缺省：滚动窗（餐饮垂直线波0，2026-11-07）
    try:
        months_back = int(months_back)
    except (TypeError, ValueError):
        raise ValueError(f"months_back must be an int, got {months_back!r}") from None
    if months_back <= 0:
        raise ValueError(
            f"census-eits: months_back={months_back} 须为正整数"
            "（固定月请显式传 time，历史回补请显式传 time_range）"
        )

    rows = []
    for program, category in CATEGORY_BY_PROGRAM.items():
        latest = _latest_available(program, category, key)
        if latest is None:
            raise RuntimeError(
                f"census-eits: {program} 近 {PROBE_WIDEN_MONTHS[-1]} 个月探测无可用期次"
                "（上游空窗或口径变更，转人工）"
            )
        start = _month_sub(latest, months_back - 1)
        program_rows = _fetch_window(program, category, start, latest, key)
        if not program_rows:
            raise RuntimeError(
                f"census-eits: {program} 滚动窗 {start}..{latest} 取数为空"
                f"（探测到最新月 {latest} 却取不到数据，出口软封或口径变更，转人工）"
            )
        rows.extend(program_rows)
    return _trim_whole_months(rows, limit)


if __name__ == "__main__":  # 冒烟：python3 spiders/census-eits/spider.py
    print(json.dumps(run_census_eits(limit=6), ensure_ascii=False, indent=2))
