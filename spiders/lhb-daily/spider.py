"""spiders/lhb-daily —— 沪深交易所龙虎榜（每日上榜股票×上榜原因，东财聚合口径）。

数据表面（东方财富 datacenter 聚合端点，**免 key、免鉴权、直连可达**；
与 akshare ``stock_lhb_detail_em`` 同源，实测逐值一致）：

- ``GET https://datacenter-web.eastmoney.com/api/data/v1/get``
  ``?reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL``
  ``&filter=(TRADE_DATE='YYYY-MM-DD')&pagesize=500&pagenum=1``
  ``&sortColumns=SECURITY_CODE&sortTypes=1&source=WEB&client=WEB``

  返回信封 ``{version, success, code, message, result:{count, pages, data[]}}``；
  每行一个「股票×上榜原因」：``SECURITY_CODE``(6 位字符串)/``SECURITY_NAME_ABBR``/
  ``TRADE_DATE``("YYYY-MM-DD 00:00:00")/``EXPLANATION``(上榜原因)/``CLOSE_PRICE``/
  ``CHANGE_RATE``(涨跌幅 %)/``BILLBOARD_NET_AMT``(龙虎榜净买额,元)/
  ``BILLBOARD_BUY_AMT``/``BILLBOARD_SELL_AMT``/``MARKET``(SH|SZ|BJ)/
  ``TRADE_MARKET``/``ACCUM_AMOUNT`` 等。

输出行（每上榜股票×上榜原因一行）：
``(source, symbol, name, trade_date, reason, close, change_pct, lhb_net_buy,
lhb_buy, lhb_sell, source_url, scraped_at)``；金额一律元原值（上游即元，无换算）。
日期取「最近有榜交易日」：从今天（UTC+8）起逐日回溯跳过无榜日，上限 7 日。

口坑：

- **非交易日端点不回空数组而是回错误信封**：``success=false, code=9201,
  message="返回数据为空", result=null``——这是「该日无榜」的合法信号，
  回溯跳过；除此之外任何 HTTP/JSON/信封异常一律抛 ``RuntimeError``（失败即红，
  回溯绝不掩盖端点级故障）。
- **pagesize/pagenum 被端点忽略**：实测 ``pagesize=10`` 仍回当日全量 79 行、
  ``pagenum=2`` 同样回全量、``pages`` 恒为 1——单请求即全量；用 ``result.count``
  与 ``len(data)`` 对账，不等即抛错（防上游哪天恢复分页导致静默半量）。
- **东财 WAF 可能软拦截无 UA 请求**：2026-11-07 本机实测无 UA 恰好放行，
  但属间歇风控——一律补常规浏览器 UA（不做指纹伪装与频次对抗，遇风控
  升级按协议转人工）。
- **端点聚合含北交所**：``MARKET=BJ``（TRADE_MARKET="北京证券交易所"）行
  属北交所，不在本单元「沪深交易所」裁决范围内——显式剔除（仅剔除 MARKET
  明确为 BJ 的行，字段缺失不臆测），见 README §2。
- **同一股票可有多条上榜原因**（如「日涨幅偏离值达 7% 前五」+「连续三日
  涨幅偏离值累计达 20%」），每原因一行、金额按该原因口径重复透出——
  按（symbol, trade_date, reason）去重保持「一股票一原因一行」契约。
- **TRADE_DATE 带 00:00:00 尾巴**：与查询日期对账（前 10 位必须相等），
  防端点串日；规范化为 YYYY-MM-DD。
- **数值列 null 容忍**：close/change_pct/三金额列实测无 null，但 schema 声明
  可空——缺失一律 null（不编造）；身份列（SECURITY_CODE/SECURITY_NAME_ABBR/
  EXPLANATION）缺失或非 6 位码即抛错。
- **净买额恒等式**：实测 BILLBOARD_NET_AMT = BILLBOARD_BUY_AMT −
  BILLBOARD_SELL_AMT（79/79 行成立），透传不重算。
- **席位明细是另一张报表**：RPT_BILLBOARD_DAILYDETAILSBUY（买卖席位营业部，
  实测可用，见 README）——本单元输出契约只含汇总口径，席位明细留作扩展，
  不拉（一次请求一条聚合已自足）。
- **直连可达**：本机直连 datacenter-web.eastmoney.com HTTP 200（2026-11-07
  实测，见 README 证据表），免 key 免鉴权，无需代理出口，manifest 不声明
  ``egress_secret``。
- **失败即红**：HTTP 错误 / 非 JSON / 信封异常 / count 对账不平 / 身份列
  缺失 / 回溯 7 日仍 0 观测 → 抛 ``RuntimeError``，绝不静默回空。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

SOURCE = "lhb-daily"

API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_NAME = "RPT_DAILYBILLBOARD_DETAILSNEW"  # 龙虎榜每日详情（股票×上榜原因）

MAX_BACKTRACK_DAYS = 7  # 最近有榜交易日回溯上限（覆盖元旦/春节外的普通长假缺口）

BEIJING_TZ = timezone(timedelta(hours=8))
TIMEOUT = 60  # 秒/请求；单端点单请求，超时即失败（不重试、不加频次）

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
    "Referer": "https://data.eastmoney.com/stock/lhb.html",
}


def _as_float(value) -> float | None:
    """数值列归一：数值 → float；``null``/异常字符串 → None（schema 可空，不编造）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _lhb_url(trade_date: str) -> str:
    """拼一次单日查询 URL（filter 用单引号日期，其余参数 urlencode）。"""
    params = {
        "reportName": REPORT_NAME,
        "columns": "ALL",
        "filter": f"(TRADE_DATE='{trade_date}')",
        "pagesize": "500",
        "pagenum": "1",
        "sortColumns": "SECURITY_CODE",
        "sortTypes": "1",
        "source": "WEB",
        "client": "WEB",
    }
    return f"{API}?{urllib.parse.urlencode(params)}"


def _fetch_day(url: str) -> list[dict] | None:
    """取单日龙虎榜原始行。

    Returns:
        数据行列表；该日无榜（信封 success=false 且 result=null，实测
        code=9201「返回数据为空」）返回 ``[]``。

    Raises:
        RuntimeError: HTTP 错误 / 非 JSON / 信封形态异常 / count 对账不平。
    """
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
            body = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 —— 端点级失败即红
        raise RuntimeError(f"{SOURCE}: {url} 取数失败 — {type(exc).__name__}: {exc}") from exc
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{SOURCE}: {url} 响应非合法 JSON（前 200 字节: {body[:200]!r}）"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{SOURCE}: 响应非 JSON 对象（{type(payload).__name__}）——端点形态已变")

    result = payload.get("result")
    if result is None:
        if payload.get("success") is False:
            return []  # 非交易日/无榜日：合法空（实测 code=9201 返回数据为空）
        raise RuntimeError(
            f"{SOURCE}: 信封 result=null 但 success!=false（code={payload.get('code')!r} "
            f"message={payload.get('message')!r}）——端点形态已变"
        )
    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        raise RuntimeError(
            f"{SOURCE}: result 缺 data 数组（result={type(result).__name__}）——端点形态已变"
        )
    data = result["data"]
    count = result.get("count")
    # 实测端点忽略 pagesize/pagenum（pagesize=10 仍回全量）：count 是当日全量口径，
    # 与实收行数对账，防上游恢复分页后静默半量。
    if isinstance(count, int) and count != len(data):
        raise RuntimeError(
            f"{SOURCE}: result.count={count} 与实收 {len(data)} 行不平"
            f"（端点分页行为已变，需改走翻页取全量）—— {url}"
        )
    return data


def _row_from(raw: dict, trade_date: str, url: str, scraped_at: str) -> dict | None:
    """原始行 → 输出行；北交所行（MARKET=BJ）超出沪深裁决范围 → None（剔除）。"""
    market = raw.get("MARKET")
    if isinstance(market, str) and market.strip().upper() == "BJ":
        return None  # 北交所：本单元只做沪深（显式剔除，不做静默混入）

    symbol = str(raw.get("SECURITY_CODE") or "").strip()
    name = str(raw.get("SECURITY_NAME_ABBR") or "").strip()
    reason = str(raw.get("EXPLANATION") or "").strip()
    if len(symbol) != 6 or not symbol.isdigit() or not name or not reason:
        raise RuntimeError(
            f"{SOURCE}: @{trade_date} 身份列缺失/非法（SECURITY_CODE={raw.get('SECURITY_CODE')!r} "
            f"SECURITY_NAME_ABBR={raw.get('SECURITY_NAME_ABBR')!r} "
            f"EXPLANATION={raw.get('EXPLANATION')!r}）——端点形态已变"
        )

    raw_date = str(raw.get("TRADE_DATE") or "").strip()
    if raw_date[:10] != trade_date:  # "YYYY-MM-DD 00:00:00" 前 10 位必须等于查询日期
        raise RuntimeError(
            f"{SOURCE}: @{trade_date} 行 TRADE_DATE={raw_date!r} 与查询日期不符——端点串日"
        )

    return {
        "source": SOURCE,
        "symbol": symbol,
        "name": name,
        "trade_date": trade_date,
        "reason": reason,
        "close": _as_float(raw.get("CLOSE_PRICE")),
        "change_pct": _as_float(raw.get("CHANGE_RATE")),
        "lhb_net_buy": _as_float(raw.get("BILLBOARD_NET_AMT")),
        "lhb_buy": _as_float(raw.get("BILLBOARD_BUY_AMT")),
        "lhb_sell": _as_float(raw.get("BILLBOARD_SELL_AMT")),
        "source_url": url,
        "scraped_at": scraped_at,
    }


def run_lhb_daily(limit: int = 100) -> list[dict]:
    """取最近有榜交易日的沪深龙虎榜观测行（每上榜股票×上榜原因一行）。

    Args:
        limit: 返回行数上限；全量解析后按（trade_date, symbol, reason）字典序
            确定性排序，保留最后 ``limit`` 行（龙虎榜为当日快照，正常一行
            不超当日榜容量；``limit`` 为防御性上限，固定参数重放结果恒定）。

    Returns:
        每行：``source``(常量 lhb-daily) / ``symbol``(6 位股票代码 str) /
        ``name``(证券简称) / ``trade_date``(YYYY-MM-DD) / ``reason``(上榜原因) /
        ``close``(float|null, 元) / ``change_pct``(float|null, %) /
        ``lhb_net_buy``(float|null, 元, 龙虎榜净买额) / ``lhb_buy``(float|null, 元) /
        ``lhb_sell``(float|null, 元) / ``source_url`` / ``scraped_at``(ISO)。

    Raises:
        ValueError: ``limit`` 类型非法。
        RuntimeError: 端点不可达、信封/结构异常、count 对账不平、身份列
            缺失、回溯 7 日仍无榜（失败即红，绝不静默回空）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    limit = max(1, min(limit, 1000))

    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today = datetime.now(BEIJING_TZ).date()
    for offset in range(MAX_BACKTRACK_DAYS):
        trade_date = (today - timedelta(days=offset)).isoformat()
        url = _lhb_url(trade_date)
        raws = _fetch_day(url)
        if not raws:
            continue  # 无榜日（非交易日端点回 9201 空信封）：回溯跳过
        rows: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for raw in raws:
            if not isinstance(raw, dict):
                continue
            row = _row_from(raw, trade_date, url, scraped_at)
            if row is None:  # 北交所行：范围外剔除
                continue
            key = (row["symbol"], row["trade_date"], row["reason"])
            if key in seen:  # 一股票一原因一行：上游偶发重复行去重
                continue
            seen.add(key)
            rows.append(row)
        if not rows:
            raise RuntimeError(
                f"{SOURCE}: @{trade_date} 上游回 {len(raws)} 行但沪深口径解析后 0 行"
                "（MARKET 字段形态已变或全为北交所）——端点形态已变"
            )
        rows.sort(key=lambda r: (r["trade_date"], r["symbol"], r["reason"]))
        return rows[-limit:]

    raise RuntimeError(
        f"{SOURCE}: 自 {today.isoformat()}（UTC+8）起回溯 {MAX_BACKTRACK_DAYS} 日均无榜"
        "——或逢超长休市，或上游报表已迁移"
    )


if __name__ == "__main__":  # 冒烟：python3 spiders/lhb-daily/spider.py
    import pprint

    pprint.pp(run_lhb_daily(limit=3))
