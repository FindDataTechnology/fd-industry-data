"""spiders/chinamoney-shibor —— 全国银行间同业拆借中心（ChinaMoney）Shibor 与
回购定盘利率 FR/FDR 日频全史（货币市场基准利率曲线）。

数据表面（交易中心**官方** AGS JSON 通道，免 key、免鉴权，2026-11-07 实测）：

- Shibor 日频历史：

  ``POST https://www.chinamoney.com.cn/ags/ms/cm-u-bk-shibor/ShiborHis
      ?lang=CN&startDate=YYYY-MM-DD&endDate=YYYY-MM-DD``

  **必须 POST**（参数在 query string；侦察记录 GET 曾返无记录壳，2026-11-07
  实测 GET 亦返回全量——以站点 XHR 同款 POST 为准，见 README）。响应顶层
  ``records`` 为日行数组（**注意 records 在顶层，不在 data 下**），行键
  ``ON/1W/2W/1M/3M/6M/9M/1Y``（字符串数值）+ ``showDateCN``（YYYY-MM-DD），
  行按日期倒序。``data.baseCurveCfgList`` 为期限码表（非行数据）。

- FR/FDR（回购定盘利率 Fixing Repo / 定盘利率 Fixing Deposit）历史：

  ``POST https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/FrrHis
      ?lang=CN&startDate=YYYY-MM-DD&endDate=YYYY-MM-DD``

  **模块前缀是 ``cm-u-bk-currency``**——brief 写的 ``cm-u-bk-shibor/FrrHis``
  实测 404 ``Path not found``（curl 证据见 README）。响应顶层 ``records``，
  行键 ``lfiProducDate``（YYYY-MM-DD）+ ``frValueMap``（FR001/FR007/FR014/
  FDR001/FDR007/FDR014，字符串数值；缺测值为字符串 ``"---"``，跳过不透出）。

- 静态快照 CSV（**不作行来源**，README 留证据）：FRR/FDR 图表数据
  ``/r/cms/www/chinamoney/data/currency/frr-chrt.csv``（滚动约 3 年）与
  ``fdr-chrt.csv``（滚动约 1 年）——**无表头**、列为日期+6 空列+3 值，列义
  经与 FrrHis 对账确认为 FR001/FR007/FR014 与 FDR001/FDR007/FDR014；FrrHis
  全史覆盖之，故不进抓取路径（无字段名+滚动窗+冗余，解析风险大于收益）。

行坑（详见 README）：

- **假 200 频控**：频控返回 HTTP 200 + HTML 403 页（``<title>403错误</title>``），
  按「请求指纹」记名封禁（同参数组合反复 403、换参数组合立即恢复）——每次
  响应先嗅探：可 ``json.loads`` **且含顶层 ``records`` 键**才算数；非 JSON →
  退避 5s 重试**一次**并 stderr 留痕，仍失败按该分片失败处理，**不连环重试**。
- **窗口硬限 ≤1 年**：超窗**静默返回空 records**（不报错，实测
  2006-10-08..2008-12-31 与 2022..2023 两窗均 records:[]）——全史按年分片：
  2006-10-08 起，每年一片（2006 片 10-08→12-31，当年片到北京今天），片间
  隔 ≥3-5s（常量 ``INTERVAL_SECONDS=3.5``，time.sleep）。
- **间隔纪律跨端点**：``INTERVAL_SECONDS`` 适用于本站全部 AGS 请求（ShiborHis、
  FrrHis 乃至静态 CSV——本单元不请求 CSV，若消费方补抓亦须守此间隔）。
- ``ShiborMnHis``（月均值）**不接**：brief 侦察两种日期格式均空 records；
  2026-11-07 复测 2025 全年窗返 744 行「日频+5日趋势」行（日期参数不生效、
  非月均值），口径不明——月均值由日频自聚合留待消费方（README 留证据）。
- **部分失败策略**：分片级失败（HTTP 错误/嗅探两次不过/缺结构）stderr 留痕
  后**续跑**其余分片；两通道（ShiborHis/FrrHis）全部失败、合计 0 行才整体抛
  ``RuntimeError``——绝不静默回空。
- **双锚返回**：``run_chinamoney_shibor(limit)`` 全史抓取后按日期倒序返回
  **最近 ``limit`` 行**，并恒附带**首交易日（2006-10-08）锚行**（golden 首日
  锚=ON 2.1184 等即靠它落进重放输出；``limit`` 传大值=全史回补，锚行与全史
  去重不重复）。``limit`` 真实生效（最近窗口行数上限）。
- **首爬回补窗口**（industry-crawl-gitops）：dispatcher 派发首爬时经 runner 注入
  ``window_start``/``window_end``（YYYY-MM-DD，平台属有参数，同 ``limit`` 走专用
  flag，不上 manifest 声明面）。入口签名显式命名这两个参数即被 kwargs 注入；
  未带窗口的运行（含 golden 重放）行为不变=全史。带窗口时只抓窗口内的年分片
  （片长仍 ≤1 年）——日常增量只需 1-2 片，避免每天重抓 20 年全史（约 140 请求
  /156s）。窗口内无定盘行（休市）回空而非报红。
- 仅常规 UA + Referer；免 key 免鉴权，不做指纹伪装与频次对抗，遇风控升级
  按协议转人工。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

SOURCE = "chinamoney-shibor"

SHIBOR_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-shibor/ShiborHis"
FRR_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/FrrHis"
# 静态快照 CSV（不作行来源，README 记录证据；同站请求同样守 INTERVAL_SECONDS）
FRR_CHRT_CSV = "https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/currency/frr-chrt.csv"
FDR_CHRT_CSV = "https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/currency/fdr-chrt.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.chinamoney.com.cn/",
}

BEIJING = timezone(timedelta(hours=8))
TIMEOUT = 60  # 秒/请求
FIRST_DATE = date(2006, 10, 8)  # Shibor 发布首日（全史分片起点）
INTERVAL_SECONDS = 3.5  # 同站请求间隔纪律（≥3-5s；brief 硬坑位）
SNIFF_BACKOFF_SECONDS = 5.0  # 嗅探失败退避后重试一次
LIMIT_CAP = 200_000  # limit 上限（全史约 7 万行，取整裕量）

# ShiborHis 行键 → 输出 curve 名（golden 锚 curve="ShiborO/N"）
SHIBOR_CURVES = {
    "ON": "ShiborO/N",
    "1W": "Shibor1W",
    "2W": "Shibor2W",
    "1M": "Shibor1M",
    "3M": "Shibor3M",
    "6M": "Shibor6M",
    "9M": "Shibor9M",
    "1Y": "Shibor1Y",
}
# FrrHis frValueMap 键 → 输出 curve 名（以实返字段为准：FR/FDR 各 3 期限）
FR_CURVES = ("FR001", "FR007", "FR014", "FDR001", "FDR007", "FDR014")
_CURVE_ORDER = {name: i for i, name in enumerate(list(SHIBOR_CURVES.values()) + list(FR_CURVES))}


def _beijing_today() -> date:
    """北京时区的「今天」（Shibor 每日 11:00 定盘，以交易日时区为准）。"""
    return datetime.now(BEIJING).date()


def _window_dates(window_start, window_end) -> tuple[date, date] | None:
    """首爬回补窗口 → 闭区间 ``(date, date)``；未带窗口 → ``None``。

    平台属有参数（YYYY-MM-DD，dispatcher 经 runner 注入），同 ``limit`` 走专用
    flag、不上 manifest 声明面。非法值抛 ``ValueError``：带窗口的运行必须响亮
    失败，而不是静默取错区间。
    """
    if window_start is None and window_end is None:
        return None
    if not window_start or not window_end:
        raise ValueError(
            f"{SOURCE}: window 参数须成对出现，got start={window_start!r} "
            f"end={window_end!r}")
    try:
        start = datetime.strptime(str(window_start), "%Y-%m-%d").date()
        end = datetime.strptime(str(window_end), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(
            f"{SOURCE}: window 参数须为 YYYY-MM-DD，got start={window_start!r} "
            f"end={window_end!r}") from None
    if start > end:
        raise ValueError(
            f"{SOURCE}: window_start {window_start} 晚于 window_end {window_end}")
    return start, end


def _year_shards(window: tuple[date, date] | None = None) -> list[tuple[date, date]]:
    """全史年分片：2006-10-08 起，每年一片；2006 片 10-08→12-31，当年片→北京今天。

    窗口硬限 ≤1 年，超窗上游**静默返回空 records**——按自然年切分保证每片
    恰 ≤1 年且不重叠。传 ``window`` 则只保留与之相交的部分（每片都是自然年的
    子区间，故仍 ≤1 年）；窗口整体落在数据期外 → 空分片表。
    """
    today = _beijing_today()
    shards: list[tuple[date, date]] = []
    for year in range(FIRST_DATE.year, today.year + 1):
        start = max(date(year, 1, 1), FIRST_DATE)
        end = min(date(year, 12, 31), today)
        if window is not None:
            start = max(start, window[0])
            end = min(end, window[1])
            if start > end:
                continue
        shards.append((start, end))
    return shards


def _post_with_sniff(url: str, params: dict) -> dict:
    """POST 请求 + JSON 嗅探（防假 200）。

    每次请求前 ``time.sleep(INTERVAL_SECONDS)``（同站间隔纪律）。响应必须
    可 ``json.loads`` **且含顶层 ``records`` 键**；否则视为频控假 200（HTTP
    200 + HTML 403 页）或端点形态变更：stderr 留痕 → 退避
    ``SNIFF_BACKOFF_SECONDS`` 重试**一次** → 仍失败抛 ``RuntimeError``（按
    分片失败处理，不连环重试）。HTTP 错误同政策（单次退避重试）。
    """
    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}"
    last_note = ""
    for attempt in (1, 2):
        time.sleep(INTERVAL_SECONDS if attempt == 1 else SNIFF_BACKOFF_SECONDS)
        try:
            req = urllib.request.Request(
                full_url,
                data=b"",  # 空 POST 体：参数在 query string，与站点 XHR 同款
                headers=HEADERS,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 官方主机)
                raw = resp.read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001 —— 记录后按嗅探政策重试一次
            last_note = f"HTTP 层失败 {type(exc).__name__}: {exc}"
            continue
        try:
            body = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            head = raw[:120].replace("\n", " ")
            last_note = f"响应非 JSON（假 200 HTML 403 页?）head={head!r}"
            continue
        if not isinstance(body, dict) or "records" not in body:
            keys = sorted(body) if isinstance(body, dict) else type(body).__name__
            last_note = f"JSON 无顶层 records 键（实际 {keys}）——端点形态已变"
            continue
        return body
    print(
        f"[{SOURCE}] 嗅探失败（{full_url}）尝试 2 次: {last_note}",
        file=sys.stderr,
    )
    raise RuntimeError(f"JSON 嗅探 2 次不过: {last_note} — {full_url}")


def _as_float(value) -> float | None:
    """利率归一：str/int/float → float；None/``"---"``/空/不可解析 → None（缺测）。"""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text or text in {"---", "-", "--"}:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _emit_row(curve: str, day: str, value: float, scraped_at: str) -> dict:
    return {
        "curve": curve,
        "date": day,
        "value": value,
        "unit": "%",
        "source": SOURCE,
        "scraped_at": scraped_at,
    }


def _parse_shibor_records(body: dict, window: str) -> list[dict]:
    """ShiborHis 顶层 records → 行（8 期限 curve）。缺结构即抛（带窗口定位）。"""
    records = body.get("records")
    if not isinstance(records, list):
        raise RuntimeError(
            f"ShiborHis records 非 JSON 数组（{type(records).__name__}）——{window}"
        )
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict] = []
    for item in records:
        if not isinstance(item, dict):
            raise RuntimeError(f"ShiborHis record 非对象——{window}")
        day = str(item.get("showDateCN") or "").strip()
        if len(day) != 10 or day[4] != "-" or day[7] != "-":
            raise RuntimeError(
                f"ShiborHis showDateCN 非 YYYY-MM-DD（{day!r}）——字段形态已变"
            )
        if all(key not in item for key in SHIBOR_CURVES):
            raise RuntimeError(
                f"ShiborHis 行 {day} 无任何期限键（{sorted(item)}）——字段形态已变"
            )
        for key, curve in SHIBOR_CURVES.items():
            value = _as_float(item.get(key))
            if value is not None:
                rows.append(_emit_row(curve, day, value, scraped_at))
    return rows


def _parse_frr_records(body: dict, window: str) -> list[dict]:
    """FrrHis 顶层 records → 行（FR001/007/014 + FDR001/007/014）。"""
    records = body.get("records")
    if not isinstance(records, list):
        raise RuntimeError(
            f"FrrHis records 非 JSON 数组（{type(records).__name__}）——{window}"
        )
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict] = []
    for item in records:
        if not isinstance(item, dict):
            raise RuntimeError(f"FrrHis record 非对象——{window}")
        day = str(item.get("lfiProducDate") or "").strip()
        if len(day) != 10 or day[4] != "-" or day[7] != "-":
            raise RuntimeError(
                f"FrrHis lfiProducDate 非 YYYY-MM-DD（{day!r}）——字段形态已变"
            )
        value_map = item.get("frValueMap")
        if not isinstance(value_map, dict):
            raise RuntimeError(
                f"FrrHis 行 {day} 缺 frValueMap（{sorted(item)}）——字段形态已变"
            )
        for tenor in FR_CURVES:
            value = _as_float(value_map.get(tenor))
            if value is not None:
                rows.append(_emit_row(tenor, day, value, scraped_at))
    return rows


def _fetch_channel(name: str, url: str, parser,
                   window: tuple[date, date] | None = None) -> tuple[list[dict], list[str]]:
    """按年分片抓一个通道（ShiborHis/FrrHis 同法）。

    每片独立 try/except：分片失败 stderr 留痕并续跑（不拖垮其余分片）。
    返回（行, 失败清单）；全片成功则失败清单为空。
    """
    rows: list[dict] = []
    errors: list[str] = []
    for start, end in _year_shards(window):
        window = f"{start:%Y-%m-%d}..{end:%Y-%m-%d}"
        params = {"lang": "CN", "startDate": f"{start:%Y-%m-%d}", "endDate": f"{end:%Y-%m-%d}"}
        try:
            body = _post_with_sniff(url, params)
            shard_rows = parser(body, window)
        except Exception as exc:  # noqa: BLE001 —— 分片失败留痕续跑
            errors.append(f"{name} {window}: {type(exc).__name__}: {exc}")
            print(
                f"[{SOURCE}] 分片失败（{name} {window}）——留痕续跑: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            continue
        rows.extend(shard_rows)
        print(f"[{SOURCE}] {name} {window}: {len(shard_rows)} rows", file=sys.stderr)
    return rows, errors


def run_chinamoney_shibor(limit: int = 200, days: int = 45,
                          window_start: str | None = None,
                          window_end: str | None = None) -> list[dict]:
    """取 Shibor 八期限 + FR/FDR 六期限日频利率（全史 2006-10-08 起可回补）。

    Args:
        limit: 抓取后返回**最近 ``limit`` 行**（按日期倒序）；另恒附带序列起点
            锚行（golden 双锚口径，见模块 docstring）。``limit`` 传大值（如
            ``10**9``，受 ``LIMIT_CAP`` 截断）= 返回全史。
        days: 抓取回看窗口（日历日）。默认 45（≈30 交易日）：日频调度每次只打
            1 个年分片 = 2 个请求，避免每天重抓 20 年全史（约 140 请求/157s，
            同参数每日重复正是 README 记的指纹封禁成因）。``days=0`` = 全史
            （2006-10-08 起，回补/校验用）。
        window_start/window_end: 首爬回补窗口（可选，成对，YYYY-MM-DD）。平台
            属有参数，dispatcher 经 runner 注入，不上 manifest 声明面。带窗口
            时只抓窗口内的年分片（片长仍 ≤1 年），**优先于 ``days``**。

    Returns:
        每行 6 键：``curve``（ShiborO/N..Shibor1Y / FR001/FR007/FR014 /
        FDR001/FDR007/FDR014）/ ``date``（YYYY-MM-DD）/ ``value``（float，%）/
        ``unit``（常量 ``"%"``）/ ``source``（常量 ``chinamoney-shibor``）/
        ``scraped_at``（ISO 时间戳）。按（date 倒序, curve 固定序）确定性排序。

    Raises:
        ValueError: ``limit``/``days`` 类型非法或 ``days`` 为负；``window_*``
            不成对/非 YYYY-MM-DD/起晚于止。
        RuntimeError: 两通道全部分片失败、合计 0 行——失败即红，绝不静默回空；
            部分分片失败仅 stderr 留痕并续跑。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    limit = max(1, min(limit, LIMIT_CAP))
    try:
        days = int(days)
    except (TypeError, ValueError):
        raise ValueError(f"days must be an int, got {days!r}") from None
    if days < 0:
        raise ValueError(f"{SOURCE}: days 不能为负，got {days!r}")
    win = _window_dates(window_start, window_end)
    if win is None and days > 0:
        # 日频默认增量：只回看 days 个日历日，通常落在 1 个年分片内 = 2 个请求。
        end = _beijing_today()
        win = (end - timedelta(days=days - 1), end)

    errors: list[str] = []
    rows: list[dict] = []
    for name, url, parser in (
        ("ShiborHis", SHIBOR_URL, _parse_shibor_records),
        ("FrrHis", FRR_URL, _parse_frr_records),
    ):
        channel_rows, channel_errors = _fetch_channel(name, url, parser, win)
        rows.extend(channel_rows)
        errors.extend(channel_errors)

    if not rows:
        if win is not None and not errors:
            # 带窗口的增量运行：窗口内全是休市日/未定盘日 → 上游按年返回空 records
            # 是正常语义（不是故障）。回空，让平台记 0 行而不是报假红。
            print(
                f"[{SOURCE}] window {window_start}..{window_end} 内无定盘行（休市/未定盘）",
                file=sys.stderr,
            )
            return []
        # 全部分片失败（合计 0 行）：整体抛错，绝不静默回空
        raise RuntimeError(f"{SOURCE}: 全部分片失败（0 行）— " + " | ".join(errors))

    # 去重（同 (date, curve) 后写覆盖——上游滚动修订取新）+ 按日期倒序
    dedup: dict[tuple[str, str], dict] = {}
    for row in rows:
        dedup[(row["date"], row["curve"])] = row
    ordered = sorted(
        dedup.values(),
        key=lambda r: (r["date"], _CURVE_ORDER.get(r["curve"], 99)),
    )

    # 双锚返回：最近 limit 行 + 序列起点锚行（去重；limit 大=全史时天然含锚）
    # 不带窗口时起点=首交易日 2006-10-08；带窗口时起点=窗口内最早定盘日。
    anchor_date = ordered[0]["date"]  # ordered 升序临时取首元素=最早交易日
    recent = ordered[-limit:]
    out: dict[tuple[str, str], dict] = {(r["date"], r["curve"]): r for r in recent}
    for row in ordered:
        if row["date"] == anchor_date:
            out[(row["date"], row["curve"])] = row
    result = sorted(
        out.values(),
        key=lambda r: (-_date_key(r["date"]), _CURVE_ORDER.get(r["curve"], 99)),
    )
    return result


def _date_key(day: str) -> int:
    """YYYY-MM-DD → 可比较整数（倒序排序用）。"""
    year, month, day_num = day.split("-")
    return int(year) * 10000 + int(month) * 100 + int(day_num)


if __name__ == "__main__":  # 冒烟：python3 spiders/chinamoney-shibor/spider.py（全史抓取约 3 分钟）
    import pprint

    pprint.pp(run_chinamoney_shibor(limit=8))
