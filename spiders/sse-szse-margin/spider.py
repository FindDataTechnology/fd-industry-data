"""spiders/sse-szse-margin —— 沪深交易所融资融券交易总量（两融余额，日频）。

数据表面（两所**官方** JSON 通道，**免 key、免鉴权、直连可达**，2026-11-07 实测）：

- 上交所（SSE）「融资融券汇总」页（https://www.sse.com.cn/market/othersdata/margin/sum/）背后的取数接口：

  ``GET https://query.sse.com.cn/marketdata/tradedata/queryMargin.do``

  参数 ``isPagination=true`` + ``beginDate/endDate``（YYYYMMDD）+ ``pageHelp.*``；
  响应 JSON ``result`` 为按日汇总行（``opDate``/``rzmre`` 融资买入额/``rzye``
  融资余额/``rqmcl`` 融券卖出量/``rqyl`` 融券余量/``rqylje`` 融券余量金额/
  ``rzrqjyzl`` 融资融券余额），**单位已是元**（实测 rzye≈1.17e12，
  且 rzrqjyzl == rzye + rqylje 逐元相等，口径自洽）。一次请求可取整个日期窗口，
  ``pageHelp.pageSize`` 给足窗口天数即可。

- 深交所（SZSE）「融资融券交易总量」页
  （https://www.szse.cn/disclosure/margin/margin/index.html）背后的报表接口：

  ``GET https://www.szse.cn/api/report/ShowReport/data``
      ``?SHOWTYPE=JSON&CATALOGID=1837_xxpl&txtDate=YYYY-MM-DD&tab1PAGENO=1``

  响应为单元素数组，``[0].metadata.cols`` 给中文列名+**标注单位**
  （融资买入额(亿元)/融资余额(亿元)/融券卖出量(亿股/亿份)/融券余量(亿股/亿份)/
  融券余额(亿元)/融资融券余额(亿元)），``[0].data`` 为该 ``txtDate`` 的 1 行
  汇总（**行内不带日期**，日期即查询参数），字符串带千分位。**单位为亿元**，
  统一换算为元（×1e8），换算系数从列名标注单位动态解析——上游若改单位口径
  （如改回元）则缺标注即抛错，绝不猜测。非交易日/未发布返回 ``data: []``
  （0 行、无 error），逐日回溯时跳过。

行坑（详见 README）：

- SSE 页面 HTML 不含 sqlId（SPA，common_table.js 装载）；brief 提示的
  ``commonQuery.do?sqlId=COMMON_SSE_SJ_GPSJ_CJGZ_MRSC_JYHL_SUM_STATU_L`` 实测
  HTTP 200 但 ``result: null``/``total: 0``（仅回显参数，无数据）——真通道是
  ``marketdata/tradedata/queryMargin.do``。
- SZSE 报表目录 ID 是 **1837_xxpl**，不是流传的 ``2003_sczm``（后者实测返回
  ``error: "noalert@显示报表报表数据失败"`` 空数据）。
- SZSE 逐日查询才能拿到带日期的行；不带 ``txtDate`` 会返回全历史约 4000 行
  但行内无日期，不可用。最新交易日数据有发布时滞（T 日数据可能 T+1 工作日
  才可查），故从北京时区「今天」逐日回溯取最近 ``limit`` 个有数交易日。
- SSE ``queryMargin.do`` 缺 ``Referer`` 是否 403 与出口 IP/WAF 状态相关
  （2026-11-07 本机实测无 Referer 亦 200；brief 与历史经验称 403 常见）——
  代码**恒带** ``Referer: https://www.sse.com.cn/`` 对齐站点 XHR 行为。
- 仅常规 UA；免 key 免鉴权，不做指纹伪装与频次对抗，遇风控升级按协议转人工。
- **部分失败策略**：两所各自抓取、各自失败即各自抛 ``RuntimeError``；任一所
  失败仅致该所无行（stderr 告警），**另一所照常产出**；两所全部失败（合计
  0 行）才整体抛 ``RuntimeError``——绝不静默回空。golden 只锚可达性最稳的
  SZSE（见 golden/README 论证）。
- 容错：HTTP 错误 / 非 JSON / 缺结构 / 缺列（含列名清单）/ 0 观测行 →
  抛 ``RuntimeError``（失败即红，绝不静默回空）。
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from re import search as _re_search

SOURCE = "sse-szse-margin"

SSE_URL = "https://query.sse.com.cn/marketdata/tradedata/queryMargin.do"
SZSE_URL = "https://www.szse.cn/api/report/ShowReport/data"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
}
SSE_HEADERS = {**HEADERS, "Referer": "https://www.sse.com.cn/"}
SZSE_HEADERS = {
    **HEADERS,
    "Referer": "https://www.szse.cn/disclosure/margin/margin/index.html",
}

BEIJING = timezone(timedelta(hours=8))
TIMEOUT = 60  # 秒/请求；单请求不重试、不加频次
LIMIT_CAP = 500  # limit 上限：SZSE 逐日回溯有请求量，500 交易日≈2 个自然年
_SZSE_ATTEMPTS_CAP = 400  # SZSE 逐日回溯的自然日尝试上限（覆盖长假期仍约 260 个交易日）
_SZSE_CATALOG = "1837_xxpl"  # 融资融券交易总量报表目录（2003_sczm 已失效）
_SSE_KEYS = ("opDate", "rzye", "rqylje", "rzrqjyzl")  # 汇总行必需键（元）
_SZSE_KEYS = ("jrrzye", "jrrjye", "jrrzrjye")  # 融资余额/融券余额/融资融券余额（列名标注单位）

_UNIT_MULT = {"亿元": 1e8, "万元": 1e4, "元": 1.0}


def _get_json(url: str, headers: dict) -> object:
    """GET 并解析 JSON；网络/解码/解析失败统一抛 ``RuntimeError``。"""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 官方主机)
            return json.loads(resp.read().decode("utf-8"))
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 端点级失败即红
        raise RuntimeError(
            f"sse-szse-margin: {url} 取数失败 — {type(exc).__name__}: {exc}"
        ) from exc


def _as_float(value) -> float | None:
    """金额归一：int/float/带千分位字符串 → float；None/不可解析 → None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _beijing_today() -> date:
    """北京时区的「今天」（两所数据均以交易日为准，避免 UTC 偏差）。"""
    return datetime.now(BEIJING).date()


def _emit_row(
    exchange: str,
    trade_date: str,
    rzye: float,
    rqye: float,
    rzrqye: float,
    source_url: str,
    scraped_at: str,
) -> dict:
    return {
        "source": SOURCE,
        "exchange": exchange,
        "trade_date": trade_date,
        "rzye": rzye,
        "rqye": rqye,
        "rzrqye": rzrqye,
        "source_url": source_url,
        "scraped_at": scraped_at,
    }


def _unit_multiplier(title: str) -> float:
    """从 SZSE 列标题（如 ``融资余额<br>(亿元)``）解析金额单位换算系数。

    期望标注 亿元/万元/元；缺失或未知口径即抛 ``RuntimeError``——上游改单位
    而代码仍按旧系数换算会造成 1e8 量级错误，绝不猜测。
    """
    m = _re_search(r"[（(](亿元|万元|元)[)）]", str(title))
    if not m:
        raise RuntimeError(
            f"sse-szse-margin: SZSE 列标题 {title!r} 未标注金额单位"
            "（期望 亿元/万元/元）——单位口径已变，需人工复核换算"
        )
    return _UNIT_MULT[m.group(1)]


def _keep_recent(rows: list[dict], limit: int) -> list[dict]:
    """按 trade_date 升序确定性排序，保留最近 ``limit`` 行并去重。"""
    dedup: dict[str, dict] = {}
    for row in rows:  # 同日重复时后写覆盖（上游滚动修订取新）
        dedup[row["trade_date"]] = row
    ordered = sorted(dedup.values(), key=lambda r: r["trade_date"])
    return ordered[-limit:]


def _fetch_sse(limit: int) -> list[dict]:
    """SSE 融资融券汇总：单请求取最近窗口，按 ``limit`` 截取最新交易日。"""
    today = _beijing_today()
    window = limit * 2 + 30  # 自然日窗口：容纳春节/国庆等长假后仍足够 limit 个交易日
    params = {
        "isPagination": "true",
        "beginDate": (today - timedelta(days=window)).strftime("%Y%m%d"),
        "endDate": today.strftime("%Y%m%d"),
        "tabType": "",
        "stockCode": "",
        "pageHelp.pageSize": str(window + 10),
        "pageHelp.pageNo": "1",
        "pageHelp.beginPage": "1",
        "pageHelp.cacheSize": "1",
        "pageHelp.endPage": "5",
    }
    url = f"{SSE_URL}?{urllib.parse.urlencode(params)}"
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = _get_json(url, SSE_HEADERS)
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"sse: 响应非 JSON 对象（{type(payload).__name__}）——端点形态已变"
        )
    result = payload.get("result") or []
    if not isinstance(result, list):
        raise RuntimeError(
            f"sse: result 非 JSON 数组（{type(result).__name__}）——端点形态已变"
        )

    rows: list[dict] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        missing = [k for k in _SSE_KEYS if k not in item]
        if missing:  # 缺列即抛（列名清单）
            raise RuntimeError(
                f"sse: 汇总行缺键 {missing}（实际键 {sorted(item)}）——字段形态已变"
            )
        raw_date = str(item.get("opDate") or "").strip()
        if len(raw_date) != 8 or not raw_date.isdigit():
            raise RuntimeError(
                f"sse: opDate 非 YYYYMMDD 形态（{raw_date!r}）——字段形态已变"
            )
        rzye = _as_float(item["rzye"])
        rqye = _as_float(item["rqylje"])  # 融券余量金额 = 融券余额（元）
        rzrqye = _as_float(item["rzrqjyzl"])
        if rzye is None or rqye is None or rzrqye is None:
            continue  # 缺测行不透出；全部缺测由下方 0 行断言兜底
        trade_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
        rows.append(_emit_row("SSE", trade_date, rzye, rqye, rzrqye, url, scraped_at))

    if not rows:
        raise RuntimeError(
            f"sse: {SSE_URL} 解析后 0 观测行"
            f"（窗口 {(today - timedelta(days=window)):%Y-%m-%d}..{today:%Y-%m-%d}；"
            "端点形态已变或窗口内无已发布数据）"
        )
    return _keep_recent(rows, limit)


def _fetch_szse(limit: int) -> list[dict]:
    """SZSE 融资融券交易总量：逐日回溯查询，取最近 ``limit`` 个有数交易日。"""
    today = _beijing_today()
    attempts_max = min(limit * 3 + 20, _SZSE_ATTEMPTS_CAP)
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict] = []

    for offset in range(attempts_max):
        if len(rows) >= limit:
            break
        day = today - timedelta(days=offset)
        date_str = f"{day:%Y-%m-%d}"
        params = {
            "SHOWTYPE": "JSON",
            "CATALOGID": _SZSE_CATALOG,
            "txtDate": date_str,
            "tab1PAGENO": "1",
        }
        url = f"{SZSE_URL}?{urllib.parse.urlencode(params)}"
        payload = _get_json(url, SZSE_HEADERS)
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise RuntimeError(
                f"szse: 响应非预期的报表数组形态——端点形态已变（{url}）"
            )
        block = payload[0]
        err = block.get("error")
        if err:
            raise RuntimeError(f"szse: 报表返回 error（{err!r}）——{url}")
        data = block.get("data")
        if not isinstance(data, list):
            raise RuntimeError(
                f"szse: data 非 JSON 数组（{type(data).__name__}）——端点形态已变"
            )
        if not data:
            continue  # 非交易日 / 数据未发布：正常空档，回溯继续

        meta = block.get("metadata") or {}
        cols = meta.get("cols")
        if not isinstance(cols, dict) or not cols:
            raise RuntimeError(
                f"szse: 缺 metadata.cols 列名清单——端点形态已变（{url}）"
            )
        absent = [k for k in _SZSE_KEYS if k not in cols]
        if absent:  # 缺列即抛（列名清单）
            raise RuntimeError(
                f"szse: cols 缺列 {absent}（实际列 {sorted(map(str, cols))}）"
                "——列名已变"
            )
        mult = {k: _unit_multiplier(cols[k]) for k in _SZSE_KEYS}

        for item in data:
            if not isinstance(item, dict):
                continue
            missing = [k for k in _SZSE_KEYS if k not in item]
            if missing:
                raise RuntimeError(
                    f"szse: 数据行缺列 {missing}（实际键 {sorted(item)}）——字段形态已变"
                )
            rzye = _as_float(item["jrrzye"])
            rqye = _as_float(item["jrrjye"])
            rzrqye = _as_float(item["jrrzrjye"])
            if rzye is None or rqye is None or rzrqye is None:
                continue  # 缺测行不透出
            if rzye == 0 and rqye == 0 and rzrqye == 0:
                continue  # 早期全零历史行（2010 年数据起点前）视为无数据
            rows.append(
                _emit_row(
                    "SZSE",
                    date_str,
                    rzye * mult["jrrzye"],
                    rqye * mult["jrrjye"],
                    rzrqye * mult["jrrzrjye"],
                    url,
                    scraped_at,
                )
            )

    if not rows:
        raise RuntimeError(
            f"szse: {SZSE_URL} 逐日回溯 {attempts_max} 个自然日后仍 0 观测行"
            f"（起点 {today:%Y-%m-%d}；端点形态已变或上游停更）"
        )
    return _keep_recent(rows, limit)


def run_sse_szse_margin(limit: int = 50) -> list[dict]:
    """取沪深两所融资融券交易总量（两融余额）最近日频观测行。

    Args:
        limit: **每所**最多保留的最近交易日行数（合计至多 ``2*limit`` 行）；
            SZSE 逐日回溯的自然日尝试上限为 ``min(3*limit+20, 400)``。

    Returns:
        每行：``source``（常量 ``sse-szse-margin``）/ ``exchange``（SSE|SZSE）/
        ``trade_date``（YYYY-MM-DD）/ ``rzye``（融资余额，元）/
        ``rqye``（融券余额，元）/ ``rzrqye``（两融余额，元）/
        ``source_url``（该所实际请求 URL）/ ``scraped_at``（ISO 时间戳）。
        按（exchange, trade_date）升序确定性排序。

    Raises:
        ValueError: ``limit`` 类型非法。
        RuntimeError: 两所全部失败（HTTP 错误/非 JSON/缺结构/缺列/0 行）——
            失败即红；单所失败时该所不产出（stderr 告警），另一所照常返回。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    limit = max(1, min(limit, LIMIT_CAP))

    rows: list[dict] = []
    errors: list[str] = []
    for fetch, exchange in ((_fetch_sse, "SSE"), (_fetch_szse, "SZSE")):
        try:
            rows.extend(fetch(limit))
        except Exception as exc:  # noqa: BLE001 —— 单所失败不拖垮另一所
            errors.append(f"{exchange}: {type(exc).__name__}: {exc}")
            print(
                f"[{SOURCE}] {exchange} 取数失败（另一所照常产出）: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

    if not rows:  # 两所全败：整体抛错，绝不静默回空
        raise RuntimeError(
            f"{SOURCE}: 沪深两所取数全部失败 — " + " | ".join(errors)
        )
    rows.sort(key=lambda r: (r["exchange"], r["trade_date"]))
    return rows


if __name__ == "__main__":  # 冒烟：python3 spiders/sse-szse-margin/spider.py
    import pprint

    pprint.pp(run_sse_szse_margin(limit=4))
