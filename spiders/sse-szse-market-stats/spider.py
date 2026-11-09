"""spiders/sse-szse-market-stats —— 沪深交易所每日成交概况（成交量/成交额/估值概览）。

数据表面（两所官方报表/查询通道，**免 key、免鉴权、直连可达**）：

- SSE（上交所）「每日股票情况」JSON 通道：

  ``GET http://query.sse.com.cn/commonQuery.do?sqlId=COMMON_SSE_SJ_GPSJ_CJGK_MRGK_C&PRODUCT_CODE=01%2C02%2C03%2C11%2C17&type=inParams&SEARCH_DATE=``

  （SEARCH_DATE 空 = 最新交易日；**必须带 ``Referer: http://www.sse.com.cn/``**，
  否则返回空壳）。返回 ``result[]`` 每行一个板块：``PRODUCT_CODE`` 17=股票(合计,
  不含回购)/01=主板A/02=主板B/03=科创板/11=股票回购；字段 ``TRADE_DATE``
  (YYYYMMDD)/``TRADE_AMT``(成交金额,亿元)/``TRADE_VOL``(成交量,亿股)/
  ``AVG_PE_RATE``(平均市盈率,倍)/``LIST_NUM``(挂牌数)/``TOTAL_VALUE``(市价总值,亿元)。
  sqlId 与参数来自页面脚本 ``/xhtml/home/2021public/querySearch/search_stockData_2021.js``
  的 ``overviewDay`` 块（brief 给的 ``COMMON_SSE_SJ_GPSJ_CJGK_DAYMD_C`` 实测
  ``result:null``，是坏通道，见 README）。

- SZSE（深交所）「股票日度概况」报表：

  ``GET https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=scsj_gprdgk_after&TABKEY=tab1&txtQueryDate=YYYY-MM-DD&tjzqlb=D&bkbz=GP%7CXZ%7CCY&cjbz=cjl%7Ccjje``

  返回「指标行 × 板块列」：``zbmc``(指标名称) × ``xz``(主板)/``cy``(创业板)/
  ``gp``(深市合计)；本单元取 ``gp`` 列的「成交量（亿）」（亿股）与
  「成交金额（亿元）」两行。日期条件名 ``txtQueryDate`` 来自报表 metadata
  ``conditions``（brief 给的 ``CATALOGID=1803_scxw`` 实测报
  ``noalert@显示报表数据失败``，真 catalogid 从页面
  ``data-s-catalog-id`` 读出，见 README）。

- SZSE「基本指标」报表（估值列来源）：

  ``GET https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1803_after&TABKEY=tab5&txtQueryDate=YYYY-MM-DD``

  指标行含「股票平均市盈率」（倍）——SZSE 市场级 PE 官方口径；该行缺失或
  当日未发布时 ``avg_pe=null``（估值列 best-effort，不编造；成交量/成交额
  列缺失则按纪律抛错）。

输出行：``(source, exchange, trade_date, turnover_amount, turnover_volume,
avg_pe, source_url, scraped_at)``，每所最新交易日一行，共 2 行。

单位统一（换算写死，README §4 有对照）：两所成交金额 亿元×1e8 → 元；
成交量 亿股×1e8 → 股；平均市盈率（倍）原值透传。

口坑：

- **brief 给的两个端点都不能直接用**：SZSE ``1803_scxw`` 报
  ``noalert@显示报表数据失败``（真 catalogid ``scsj_gprdgk_after`` 藏在页面
  ``data-s-catalog-id`` 属性里）；SSE ``COMMON_SSE_SJ_GPSJ_CJGK_DAYMD_C``
  返回 ``result:null``（真 sqlId ``..._MRGK_C`` 要配
  ``PRODUCT_CODE``+``type=inParams``，在页面 JS 里）。
- **SZSE 不带 ``txtQueryDate`` 直接报错**：报表 metadata
  ``conditions`` 声明 ``txtQueryDate`` required；日期不合法时报
  ``日期不合法，日期不能为空。``。
- **SZSE 非交易日静默回空**：周末/节假日请求 ``err=None`` 但 ``data=[]``
  ——不报错也不给日期线索，本单元从今天（UTC+8）起逐日回溯（上限
  ``SZSE_MAX_BACKTRACK_DAYS=10``，可跨最长假期），取第一个有数据的交易日。
- **SZSE 报表是转置表且不带日期列**：指标是行不是列，``trade_date`` 取自
  查询参数本身；中文指标名（``zbmc``）漂移即抛错并列出实际取值。
- **SZSE 数值带千分位逗号**（``"8,857.16"``），解析前必须去逗号。
- **SSE PRODUCT_CODE 17=股票合计（不含回购）**：17 的 LIST_NUM=01+02+03
  之和、不含 11（股票回购）；17 的 TRADE_VOL/TRADE_AMT 即沪深口径的
  「股票」市场合计，本单元固定取 17。
- **SSE 缺市盈率用 "-"**：无 PE 的板块（如股票回购 11）``AVG_PE_RATE`` 为
  ``"-"``，解析为 ``null``；键整体缺失同样 ``null``（核心列
  TRADE_DATE/TRADE_AMT/TRADE_VOL 缺失才抛错）。
- **SSE 必须带 Referer**：``query.sse.com.cn`` 校验
  ``Referer: http://www.sse.com.cn/``（实测带 Referer 返回全量 result，
  brief 原样请求返回空壳 result:null）。
- **直连可达**：本机直连两所端点均 HTTP 200（2026-11-07 实测，见 README
  证据表），免 key 免鉴权，无需代理出口，manifest 不声明 ``egress_secret``；
  仅常规 UA，不做指纹伪装与频次对抗，遇风控升级按协议转人工。
- **失败即红**：HTTP 错误 / 非 JSON / 报表 error 字段 / 结构缺失 / 中文
  列名漂移 / 回溯 10 日仍 0 观测 → 抛 ``RuntimeError``，绝不静默回空。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

SOURCE = "sse-szse-market-stats"

SSE_SOURCE_URL = "http://query.sse.com.cn/commonQuery.do"
SSE_SQL_ID = "COMMON_SSE_SJ_GPSJ_CJGK_MRGK_C"  # 股票成交概况-每日概况（页面 JS overviewDay 块）
SSE_PRODUCT_CODES = "01,02,03,11,17"  # 主板A/主板B/科创板/股票回购/股票合计
SSE_PRODUCT_TOTAL = "17"  # 股票合计（不含回购）

SZSE_API = "https://www.szse.cn/api/report/ShowReport/data"
SZSE_DAILY_CATALOG = "scsj_gprdgk_after"  # 股票日度概况（成交量/成交金额，含存托凭证）
SZSE_PE_CATALOG = "1803_after"  # 基本指标 tab5（股票平均市盈率等）
SZSE_MAX_BACKTRACK_DAYS = 10  # 非交易日静默回空：从今天起逐日回溯的上限（跨长假）

BEIJING_TZ = timezone(timedelta(hours=8))
YI = 100_000_000.0  # 亿 → 一（×1e8）

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
SSE_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json, */*",
    "Referer": "http://www.sse.com.cn/",  # query.sse.com.cn 校验此头
}
SZSE_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "https://www.szse.cn/market/stock/situation/daily/index.html",
}
TIMEOUT = 60  # 秒/请求；单端点单请求，超时即失败（不重试、不加频次）

SZSE_VOL_LABEL = "成交量（亿）"  # 亿股（含存托凭证，gp=深市合计）
SZSE_AMT_LABEL = "成交金额（亿元）"  # 亿元
SZSE_PE_LABEL = "股票平均市盈率"  # 倍（估值列，缺失 → null）


def _http_get_json(url: str, headers: dict[str, str]) -> object:
    """GET 一个 JSON 端点；HTTP 错误/非 JSON 抛 ``RuntimeError``（失败即红）。"""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定官方主机)
            body = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 —— 端点级失败即红
        raise RuntimeError(
            f"{SOURCE}: {url} 取数失败 — {type(exc).__name__}: {exc}"
        ) from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{SOURCE}: {url} 响应非合法 JSON（前 200 字节: {body[:200]!r}）"
        ) from exc


def _cn_float(value) -> float | None:
    """SZSE 报表数值归一：去千分位逗号 → float；``None``/``-``/空 → None。"""
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if text in ("", "-", "—"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _fetch_szse_rows(catalog_id: str, tab: str, trade_date: str) -> list[dict]:
    """取 SZSE 报表数据行；报表 ``error`` 字段非空即抛 ``RuntimeError``。"""
    params = {
        "SHOWTYPE": "JSON",
        "CATALOGID": catalog_id,
        "TABKEY": tab,
        "txtQueryDate": trade_date,
        "PAGENO": "1",
    }
    if catalog_id == SZSE_DAILY_CATALOG:
        # 日度概况的三个隐藏条件（报表 metadata conditions 的 defaultValue）：
        # 类别 D=日度；板块 GP|XZ|CY=股票|主板|创业板；列 cjl|cjje=成交量|成交金额
        params.update({"tjzqlb": "D", "bkbz": "GP|XZ|CY", "cjbz": "cjl|cjje"})
    url = f"{SZSE_API}?{urllib.parse.urlencode(params)}"
    payload = _http_get_json(url, SZSE_HEADERS)
    if not (isinstance(payload, list) and payload and isinstance(payload[0], dict)):
        raise RuntimeError(
            f"{SOURCE}: SZSE {catalog_id} 响应缺少报表块（期望 list[dict]，"
            f"实得 {type(payload).__name__}）——端点形态已变"
        )
    block = payload[0]
    error = block.get("error")
    if error:
        raise RuntimeError(f"{SOURCE}: SZSE {catalog_id} 报表报错 {error!r}（{url}）")
    data = block.get("data")
    if not isinstance(data, list):
        raise RuntimeError(
            f"{SOURCE}: SZSE {catalog_id} 响应缺 data 数组——端点形态已变"
        )
    return data  # 非交易日合法回空（静默），由调用方决定下一步


def _fetch_szse_row() -> dict:
    """取 SZSE 最新交易日的成交概况行：从今天（UTC+8）起逐日回溯找第一个有数据的交易日。"""
    today = datetime.now(BEIJING_TZ).date()
    for offset in range(SZSE_MAX_BACKTRACK_DAYS):
        trade_date = (today - timedelta(days=offset)).isoformat()
        rows = _fetch_szse_rows(SZSE_DAILY_CATALOG, "tab1", trade_date)
        if not rows:
            continue  # 非交易日静默回空（err=None 且 data=[]）
        url_for_row = (
            f"{SZSE_API}?{urllib.parse.urlencode({'SHOWTYPE': 'JSON', 'CATALOGID': SZSE_DAILY_CATALOG, 'TABKEY': 'tab1', 'txtQueryDate': trade_date})}"
        )
        by_label: dict[str, dict] = {}
        for row in rows:
            if isinstance(row, dict) and row.get("zbmc"):
                by_label[str(row["zbmc"]).strip()] = row
        if SZSE_VOL_LABEL not in by_label or SZSE_AMT_LABEL not in by_label:
            actual = ", ".join(sorted(by_label)) or "(空)"
            raise RuntimeError(
                f"{SOURCE}: SZSE {SZSE_DAILY_CATALOG} @{trade_date} 中文指标名漂移："
                f"缺 {SZSE_VOL_LABEL!r}/{SZSE_AMT_LABEL!r}，实际指标 {actual}"
            )
        vol = _cn_float(by_label[SZSE_VOL_LABEL].get("gp"))
        amt = _cn_float(by_label[SZSE_AMT_LABEL].get("gp"))
        if vol is None or amt is None:
            raise RuntimeError(
                f"{SOURCE}: SZSE {SZSE_DAILY_CATALOG} @{trade_date} 深市合计(gp)列缺失或"
                f"非数值：成交量={by_label[SZSE_VOL_LABEL].get('gp')!r} "
                f"成交金额={by_label[SZSE_AMT_LABEL].get('gp')!r}，"
                f"实际列 {sorted(by_label[SZSE_VOL_LABEL])}"
            )
        return {
            "trade_date": trade_date,
            "turnover_amount": amt * YI,  # 亿元 → 元
            "turnover_volume": vol * YI,  # 亿股 → 股
            "avg_pe": _fetch_szse_pe(trade_date),  # 估值列 best-effort，缺失 → None
            "source_url": url_for_row,
        }
    raise RuntimeError(
        f"{SOURCE}: SZSE {SZSE_DAILY_CATALOG} 自 {today.isoformat()} 起回溯 "
        f"{SZSE_MAX_BACKTRACK_DAYS} 日均无数据——或逢超长休市，或上游报表已迁移"
    )


def _fetch_szse_pe(trade_date: str) -> float | None:
    """从基本指标报表取「股票平均市盈率」；当日报表/行缺失 → None（估值列 best-effort）。"""
    try:
        rows = _fetch_szse_rows(SZSE_PE_CATALOG, "tab5", trade_date)
    except RuntimeError:
        return None
    for row in rows:
        if isinstance(row, dict) and str(row.get("zbmc", "")).strip() == SZSE_PE_LABEL:
            return _cn_float(row.get("brsz"))  # 本日数值；仍为 "-" 等 → None
    return None


def _fetch_sse_row() -> dict:
    """取 SSE 最新交易日「股票」合计行（PRODUCT_CODE=17），核心列缺失即抛错。"""
    params = {
        "sqlId": SSE_SQL_ID,
        "PRODUCT_CODE": SSE_PRODUCT_CODES,
        "type": "inParams",
        "SEARCH_DATE": "",  # 空 = 最新交易日
    }
    url = f"{SSE_SOURCE_URL}?{urllib.parse.urlencode(params)}"
    payload = _http_get_json(url, SSE_HEADERS)
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"{SOURCE}: SSE 响应非 JSON 对象（{type(payload).__name__}）——端点形态已变"
        )
    result = payload.get("result")
    if not isinstance(result, list) or not result:
        raise RuntimeError(
            f"{SOURCE}: SSE {SSE_SQL_ID} result 为空（受 Referer/参数影响）——"
            f"响应键 {sorted(payload)}"
        )
    total = None
    codes: list[str] = []
    for row in result:
        if not isinstance(row, dict):
            continue
        code = str(row.get("PRODUCT_CODE", "")).strip()
        codes.append(code)
        if code == SSE_PRODUCT_TOTAL:
            total = row
            break
    if total is None:
        raise RuntimeError(
            f"{SOURCE}: SSE 响应缺 PRODUCT_CODE={SSE_PRODUCT_TOTAL}（股票合计）行，"
            f"实际 PRODUCT_CODE {codes or sorted(result[0])}"
        )
    trade_date = str(total.get("TRADE_DATE") or "").strip()
    if len(trade_date) != 8 or not trade_date.isdigit():
        raise RuntimeError(
            f"{SOURCE}: SSE TRADE_DATE 非法（期望 YYYYMMDD，得 {trade_date!r}）——"
            f"行键 {sorted(total)}"
        )
    trade_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
    amt = _cn_float(total.get("TRADE_AMT"))
    vol = _cn_float(total.get("TRADE_VOL"))
    if amt is None or vol is None:
        raise RuntimeError(
            f"{SOURCE}: SSE 核心列缺失/非数值 TRADE_AMT={total.get('TRADE_AMT')!r} "
            f"TRADE_VOL={total.get('TRADE_VOL')!r}，行键 {sorted(total)}"
        )
    return {
        "trade_date": trade_date,
        "turnover_amount": amt * YI,  # 亿元 → 元
        "turnover_volume": vol * YI,  # 亿股 → 股
        "avg_pe": _cn_float(total.get("AVG_PE_RATE")),  # "-"/缺失 → null
        "source_url": url,
    }


def run_sse_szse_market_stats(limit: int = 20) -> list[dict]:
    """取沪深两所最新交易日的成交概况（成交量/成交额/平均市盈率）观测行。

    Args:
        limit: 返回行数上限；全量解析后按（trade_date, exchange）字典序确定性
            排序，保留最近 ``limit`` 行（当前每所 1 行、共 2 行，``limit`` 为
            防御性上限；生产每日跑批取当日快照）。

    Returns:
        每行：``source``(常量 sse-szse-market-stats) / ``exchange``(SSE|SZSE) /
        ``trade_date``(YYYY-MM-DD) / ``turnover_amount``(float, 元) /
        ``turnover_volume``(float, 股) / ``avg_pe``(float|null, 倍) /
        ``source_url`` / ``scraped_at``(ISO)。

    Raises:
        ValueError: ``limit`` 类型非法。
        RuntimeError: 端点不可达、结构异常、中文列名漂移或 0 观测行（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    limit = max(1, min(limit, 1000))

    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict] = []
    for exchange, fetch in (("SSE", _fetch_sse_row), ("SZSE", _fetch_szse_row)):
        rows.append({"source": SOURCE, "exchange": exchange, **fetch(),
                     "scraped_at": scraped_at})

    if not rows:
        raise RuntimeError(f"{SOURCE}: 0 观测行（两所解析均为空）")
    rows.sort(key=lambda r: (r["trade_date"], r["exchange"]))
    return rows[-limit:]


if __name__ == "__main__":  # 冒烟：python3 spiders/sse-szse-market-stats/spider.py
    import pprint

    pprint.pp(run_sse_szse_market_stats(limit=3))
