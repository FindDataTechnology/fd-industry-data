"""spiders/hsgt-northbound —— 沪深港通北向资金（沪深股通每日成交概况，交易所官方口径）。

数据表面（**交易所官方披露**，免 key、免鉴权、直连可达）：

- **2024-08-16 披露制度变迁（本单元的口径基石）**：港交所自 2024-08-16 起
  停止披露北向实时成交与每日净买入；北向（沪股通/深股通）现存官方披露面 =
  **每日成交概况**（买+卖合计的成交总额、成交总笔数、ETF 成交总额），
  **不再有买入/卖出拆分、不再有净买入**。因此本单元输出
  ``buy_turnover``/``sell_turnover`` 恒为 ``None``（键保留、值空缺，
  官方不披露而非采集缺陷），仅 ``total_turnover`` 有值。
- **沪股通（SSE 沪港通专栏「沪股通成交概况」页）**：
  ``GET https://query.sse.com.cn/commonSoaQuery.do?sqlId=FW_HGTZL_HGTSCSJ_HGTCJGK_MRTJ&tradeDate=YYYYMMDD``
  （页面 http://www.sse.com.cn/services/hkexsc/hgtscsj/hgtcjgk/ 的官方 JS
  ``search_southboundStock_2021.js`` 即走此端点）。返回
  ``result[0] = {tradeDate:"20261106", totalAmount:"1,283.68",
  totalVolume:"704.67", etfTotalAmount:"26.48"}``（亿元/万笔）。
  **必须带 ``Referer: http://www.sse.com.cn/``**，否则返回
  ``{"success":"false","errorMsg":"null"}``。
- **深股通（SZSE 深港通「深股通交易日报」页）**：
  ``GET https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=SGT_SGTJYRB&txtDate=YYYY-MM-DD``
  （页面 http://www.szse.cn/szhk/szhktradeinfo/szdaily/index.html）。
  返回垂直报表：``data = [{label:"当日交易总额（亿元人民币）", total:"1,342.25"},
  {label:"当日交易总笔数（万笔）", ...}, {label:"当日ETF交易总额（亿元人民币）", ...}]``，
  日期见 ``metadata.subname``。免 Referer、免 UA 也 200（实测）。
- 两通道均为**单日查询**（每交易日一次请求），无免 key 的日期区间端点；
  ``limit`` 个交易日 = 从今天（北京时间）起倒着逐日探测，周末直接跳过，
  空结果视为非交易日（节假日）继续回溯，直到集齐 ``limit`` 个交易日或触及
  回看上限（60 个自然日）。

单位换算：上游两通道均以 **亿元人民币** 计，输出统一乘 1e8 换算为 **元**
（如 1,283.68 亿 → 128368000000.0 元）。

口坑：

- **brief 给的 sqlId 是死路**：``COMMON_SSE_SJ_SHHGTLGT_JYGK_MRTJ_JYL_Y_M`` 在
  ``commonQuery.do`` 上 200 但 ``result:null``（换日期格式/去参数同空），搬到
  ``commonSoaQuery.do`` 上直接 ``"SOA service is null！"``——该 sqlId 已退役。
  可用通道是官方页面 JS 实走的 ``commonSoaQuery.do + FW_HGTZL_HGTSCSJ_HGTCJGK_MRTJ``
  （README 有完整探测清单）。
- **SSE 无 Referer 即拒绝**：HTTP 仍 200 但 body 为
  ``{"jsonCallBack":"null","success":"false","errorMsg":"null"}``——必须判
  ``success/errorMsg`` 形态，不能只看状态码。
- **SSE 数值带千分位逗号**：``totalAmount: "1,283.68"``，解析前必须去逗号。
- **北向无买/卖拆分是披露制度而非漏采**：上游南向（港股通）仍披露买入/卖出
  （如 SSE ``ggt/getQuatationInfo.do`` 返回 BUY_AMOUNT/SELL_AMOUNT），北向自
  2024-08 起只有合计——不要试图从北向端点里"找回"买卖字段。
- **直连优先、单通道失败不拖垮整体**：某通道端点级失败（HTTP 错误/非 JSON/
  结构缺失）时该通道停止回溯并记入 stderr 告警，只透出可达通道的行；两通道
  全失败或合计 0 行才抛 ``RuntimeError``（失败即红，绝不静默空）。
- **单请求瞬断有界重试**：逐日回溯是多次请求形态，单请求网络层失败（SSL
  握手超时等）重试 1 次再红；解析错误（非 JSON/缺列/数值坏）不重试直接红。
- **非交易日 = 空结果**：两通道对非交易日（周末/节假日）均返回空数据体而非
  报错，逐日回溯据此跳过；单请求内解析错误（缺列/数值坏）仍显式抛错。
- 仅常规 UA（SSE 侧强制 Referer 是其 CORS 防盗链约定，非指纹伪装）；
  免 key 免鉴权，不做频次对抗，遇风控升级按协议转人工。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

SOURCE = "hsgt-northbound"

# --- 沪股通（SSE 官方「沪股通成交概况」页背后的查询端点） ---
SSE_API = "https://query.sse.com.cn/commonSoaQuery.do"
SSE_SQL_ID = "FW_HGTZL_HGTSCSJ_HGTCJGK_MRTJ"  # 沪股通成交概况·每日统计
SSE_PAGE = "http://www.sse.com.cn/services/hkexsc/hgtscsj/hgtcjgk/"
SSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "http://www.sse.com.cn/",  # 必带，否则 success=false
    "Accept": "application/json, */*",
}

# --- 深股通（SZSE 深港通「深股通交易日报」页背后的报表端点） ---
SZSE_API = "https://www.szse.cn/api/report/ShowReport/data"
SZSE_CATALOG = "SGT_SGTJYRB"  # 深股通交易日报
SZSE_PAGE = "http://www.szse.cn/szhk/szhktradeinfo/szdaily/index.html"
SZSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
}

TIMEOUT = 30          # 秒/请求；单端点单请求，超时即该次尝试失败
RETRY_ATTEMPTS = 2    # 单请求瞬断（SSL 握手超时等）重试 1 次；重试耗尽仍失败即红
RETRY_SLEEP = 2       # 秒/重试间隔
LOOKBACK_DAYS = 60    # 交易日回看上限（自然日）；触顶仍不够行则接受已收集行
YI = 100_000_000.0    # 亿元 -> 元


def _beijing_today() -> datetime:
    """交易所口径的"今天"（北京时间），避免 UTC 晚 8 小时导致漏采当日。"""
    return datetime.now(timezone(timedelta(hours=8)))


def _as_float(raw) -> float | None:
    """上游数值归一：去千分位逗号后转 float；空/非法返回 None（调用方校验）。"""
    if raw is None or isinstance(raw, bool):
        return None
    text = str(raw).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_iso_date(compact: str) -> str | None:
    """``YYYYMMDD`` -> ``YYYY-MM-DD``；非法返回 None。"""
    if len(compact) == 8 and compact.isdigit():
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
    return None


def _get_json(url: str, headers: dict) -> object:
    """GET 并解析 JSON；网络层/解析层失败抛 ``RuntimeError``（失败即红）。

    逐日回溯是多次请求的采集形态，单请求瞬断（如 SSL 握手超时）做一次有界
    重试（共 2 次尝试，间隔 2s）；重试耗尽仍失败即红，不做频次对抗式重试。
    """
    last_exc: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
                body = resp.read().decode("utf-8")
            try:
                return json.loads(body)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"hsgt-northbound: {url} 响应非 JSON — {body[:120]!r}"
                ) from exc
        except RuntimeError:
            raise  # 解析错误非瞬断，不重试
        except Exception as exc:  # noqa: BLE001 —— 网络层瞬断，重试一次
            last_exc = exc
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_SLEEP)
    raise RuntimeError(
        f"hsgt-northbound: {url} 取数失败（重试 {RETRY_ATTEMPTS} 次后）— "
        f"{type(last_exc).__name__}: {last_exc}"
    ) from last_exc


def _sse_url(trade_date: str) -> str:
    return f"{SSE_API}?sqlId={SSE_SQL_ID}&tradeDate={trade_date.replace('-', '')}"


def _szse_url(trade_date: str) -> str:
    return f"{SZSE_API}?SHOWTYPE=JSON&CATALOGID={SZSE_CATALOG}&txtDate={trade_date}"


def _fetch_sse_day(trade_date: str) -> dict | None:
    """取沪股通单日成交概况；非交易日返回 None；结构/数值异常抛 ``RuntimeError``。

    返回行：trade_date / total_turnover / buy_turnover / sell_turnover（后两者
    2024-08 披露变更后官方不再披露，恒为 None；若上游恢复披露则自动透出）。
    """
    url = _sse_url(trade_date)
    payload = _get_json(url, SSE_HEADERS)
    if not isinstance(payload, dict):
        raise RuntimeError(f"hsgt-northbound: SSE 响应非 JSON 对象（{type(payload).__name__}）— {url}")
    if payload.get("success") == "false" or payload.get("errorMsg"):
        raise RuntimeError(f"hsgt-northbound: SSE 拒绝查询（success/errorMsg 形态）— {url} — {payload!r}"[:400])
    result = payload.get("result")
    if result is None:
        # 非交易日形态（如周末/节假日）：200 + result:null / result:[]，视为无数据
        return None
    if not isinstance(result, list):
        raise RuntimeError(f"hsgt-northbound: SSE result 形态异常（{type(result).__name__}）— {url}")
    if not result:
        return None  # 非交易日：空数组
    row_raw = result[0]
    if not isinstance(row_raw, dict):
        raise RuntimeError(f"hsgt-northbound: SSE result[0] 形态异常（{type(row_raw).__name__}）— {url}")
    upstream_date = _to_iso_date(str(row_raw.get("tradeDate") or "").strip())
    if upstream_date is None:
        raise RuntimeError(f"hsgt-northbound: SSE tradeDate 缺失/非法 — {url}")
    total = _as_float(row_raw.get("totalAmount"))
    if total is None or total <= 0:
        raise RuntimeError(
            f"hsgt-northbound: SSE totalAmount 缺失/非法（{row_raw.get('totalAmount')!r}）— {url}"
        )
    return {
        "trade_date": upstream_date,
        "total_turnover": round(total * YI, 2),
        "buy_turnover": _as_float(row_raw.get("buyAmount")),
        "sell_turnover": _as_float(row_raw.get("sellAmount")),
        "source_url": url,
    }


def _fetch_szse_day(trade_date: str) -> dict | None:
    """取深股通单日成交概况；非交易日返回 None；缺列/数值异常抛 ``RuntimeError``。"""
    url = _szse_url(trade_date)
    payload = _get_json(url, SZSE_HEADERS)
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise RuntimeError(f"hsgt-northbound: SZSE 响应形态异常（{type(payload).__name__}）— {url}")
    meta = payload[0].get("metadata")
    data = payload[0].get("data")
    if not isinstance(meta, dict) or not isinstance(data, list):
        raise RuntimeError(f"hsgt-northbound: SZSE metadata/data 形态异常 — {url}")
    if not data:
        return None  # 非交易日：空数据体
    upstream_date = str(meta.get("subname") or "").strip()
    if not upstream_date:
        upstream_date = trade_date
    total = None
    for item in data:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "")
        # 精确前缀匹配，避免误吃「当日ETF交易总额」；上游单位亿元人民币
        if label.startswith("当日交易总额"):
            total = _as_float(item.get("total"))
            break
    if total is None or total <= 0:
        raise RuntimeError(
            f"hsgt-northbound: SZSE 缺『当日交易总额』列或数值非法 — {url} — labels={[i.get('label') for i in data if isinstance(i, dict)]}"
        )
    buy = sell = None  # 北向无买卖拆分；若上游恢复披露，按标签自动透出
    for item in data:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "")
        if label.startswith("当日买入成交金额") and buy is None:
            buy = _as_float(item.get("total"))
        elif label.startswith("当日卖出成交金额") and sell is None:
            sell = _as_float(item.get("total"))
    return {
        "trade_date": upstream_date,
        "total_turnover": round(total * YI, 2),
        "buy_turnover": buy,
        "sell_turnover": sell,
        "source_url": url,
    }


def _walk_channel(channel: str, fetch_day, limit: int) -> list[dict]:
    """从今天（北京时间）起逐日回溯，收齐 ``limit`` 个交易日的观测行。

    周末直接跳过；非交易日（空结果）继续回溯；触及 ``LOOKBACK_DAYS`` 上限
    即停止（接受已收集行，由调用方决定是否够用）。网络/结构异常向上抛
    ``RuntimeError``，由 ``run_hsgt_northbound`` 按通道隔离。
    """
    rows: list[dict] = []
    day = _beijing_today().date()
    checked = 0
    while checked < LOOKBACK_DAYS and len(rows) < limit:
        if day.weekday() < 5:  # 周末（5=Sat,6=Sun）必非交易日，直接跳过
            checked += 1
            row = fetch_day(day.isoformat())
            if row is not None:
                row["channel"] = channel
                rows.append(row)
        day -= timedelta(days=1)
    return rows


def run_hsgt_northbound(limit: int = 20) -> list[dict]:
    """取沪深港通北向（沪股通+深股通）每日成交概况观测行。

    Args:
        limit: **每通道**返回的最近交易日数上限（合计至多 ``2*limit`` 行）；
            全量解析后按 (trade_date, channel) 字典序确定性排序。

    Returns:
        每行：``source``（常量 ``hsgt-northbound``）/ ``channel``（``沪股通`` |
        ``深股通``）/ ``trade_date``（``YYYY-MM-DD``）/ ``buy_turnover`` /
        ``sell_turnover``（2024-08 披露变更后官方不再披露，恒为 None）/
        ``total_turnover``（元，亿元×1e8）/ ``source_url`` / ``scraped_at``。

    Raises:
        ValueError: ``limit`` 类型非法。
        RuntimeError: 两通道均不可达或合计 0 观测行（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    limit = max(1, min(limit, 100))

    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict] = []
    errors: list[str] = []

    for channel, fetch_day in (("沪股通", _fetch_sse_day), ("深股通", _fetch_szse_day)):
        try:
            rows.extend(_walk_channel(channel, fetch_day, limit))
        except RuntimeError as exc:
            # 单通道不可达：记录告警，只出可达通道的行（README 纪律条目）
            errors.append(f"{channel}: {exc}")
            print(f"[hsgt-northbound] WARN channel unavailable — {exc}", file=sys.stderr)

    if not rows:
        raise RuntimeError(
            "hsgt-northbound: 两通道均无观测行（"
            + "; ".join(errors or ["0 行，端点形态可能已变"])
            + "）"
        )

    rows.sort(key=lambda r: (r["trade_date"], r["channel"]))
    for row in rows:
        row["source"] = SOURCE
        row["scraped_at"] = scraped_at
    # 输出键序与 manifest columns 一致
    return [
        {
            "source": row["source"],
            "channel": row["channel"],
            "trade_date": row["trade_date"],
            "buy_turnover": row["buy_turnover"],
            "sell_turnover": row["sell_turnover"],
            "total_turnover": row["total_turnover"],
            "source_url": row["source_url"],
            "scraped_at": row["scraped_at"],
        }
        for row in rows
    ]


if __name__ == "__main__":  # 冒烟：python3 spiders/hsgt-northbound/spider.py
    import pprint

    pprint.pp(run_hsgt_northbound(limit=3))
