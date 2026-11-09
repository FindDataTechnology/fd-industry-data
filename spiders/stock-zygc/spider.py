"""spiders/stock-zygc —— 上市公司主营构成（分行业/分产品/分地区，季度报告期）。

数据表面（东方财富 F10「主营构成分析」，**免 key、免鉴权、直连可达**）：

- ``GET https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax?code=SH600000``
  返回 JSON：``zyfw``（经营范围）/ ``zygcfx``（主营构成分析列表）/ ``jyps``（经营评述）。
  本单元只消费 ``zygcfx``：每行 ``REPORT_DATE``（"2026-06-30 00:00:00"）/
  ``MAINOP_TYPE``（"1"/"2"/"3" 分类类型）/ ``ITEM_NAME``（分部名）/
  ``MAIN_BUSINESS_INCOME``（主营收入，元）/ ``MBI_RATIO``（收入占比，**小数**）/
  ``MAIN_BUSINESS_RPOFIT``（主营利润，上游拼写 RPOFIT 照抄不改）/
  ``GROSS_RPOFIT_RATIO``（毛利率，**小数**，同样带上游拼写）等。
- 个股代码形态 ``SH600000``/``SZ000001``（沪深；``BJ`` 北交所同端点同形态）。
- **只做单股/少股查询入口**：批量按白名单是采集调度层的事，本单元按
  ``symbols`` 列表逐股循环；单股失败显式抛错（错误信息含 symbol），不静默跳过。

MAINOP_TYPE 映射依据（**实测推翻 brief 预写的 2=行业/3=产品/1=地区**，
三方独立证据一致指向 1=行业、2=产品、3=地区，详见 README §2）：

- 东方财富 F10 前端源码 ``pc_hsf10/pages/js/chunk-3b84982d.c1c66306.js``：
  ``types:[{name:"按产品",value:"2"},{name:"按行业",value:"1"},{name:"按地区",value:"3"}]``；
- akshare 同端点映射 ``{"1":"按行业分类","2":"按产品分类","3":"按地区分类"}``；
- 实测数据：茅台 SH600519 type 2 = 茅台酒/其他系列酒（产品），type 3 = 国内/国外
  （地区），type 1 = 酒类（行业）；平安 SZ000001 type 3 = 总部/南区/东区/北区/西区/境外。

口坑：

- **旧路径 ``PC_F10`` 已被 WAF 软拦截（302 → other.html?type=soft）**：brief 给的
  ``PC_F10/BusinessAnalysis/PageAjax`` 实测 302 软跳；沪深 F10 现役前缀是
  ``PC_HSF10``（2026-11-07 实测 200，README 证据表）。
- **错误响应也是 HTTP 200**：非法代码返回
  ``{"status":-1,"message":"股票代码不合法"}``——只能按结构判错（缺 ``zygcfx``
  或空列表），不能靠 HTTP 状态码。
- **上游每股硬上限 200 行**：浦发/平安/茅台均恰好返回 200 行（约 9 个报告期），
  更早报告期拿不到；本单元 ``limit`` 为**每股**上限（默认 200，与上游对齐），
  确定性排序后每股保留最近 ``limit`` 行。
- **占比字段上游以小数编码**（0.31127 = 31.127%）：输出统一 ×100 转百分比，
  与 manifest concept ``unit: percent`` 对齐（README §3 字段映射表）。
- **银行等金融机构分类怪异**：分类语义以上游 type 码为准，不按分部名猜——
  浦发银行的地区分部（总行/长三角地区…）上游就标在 type 3 下。
- **仅常规 UA、直连**：无 UA（curl 默认）也能 200，拦截是路径级的；不做指纹
  伪装与频次对抗，遇风控升级按协议转人工。
- **失败即红**：HTTP 错误 / 非 JSON / 缺 zygcfx / 空列表 / 0 观测行 /
  非法 symbol → 抛 ``RuntimeError``/``ValueError``（多股时任一股失败即整体失败），
  绝不静默回空。
"""
from __future__ import annotations

import gzip
import json
import re
import urllib.request
from datetime import datetime, timezone

SOURCE = "stock-zygc"
BASE_URL = (
    "https://emweb.securities.eastmoney.com/PC_HSF10"
    "/BusinessAnalysis/PageAjax?code={code}"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
    # 请求不压缩；上游偶发仍回 gzip（urllib 不自动解压），见 fetch_zygc 的魔数兜底
    "Accept-Encoding": "identity",
}
TIMEOUT = 60  # 秒/请求；单端点单请求，超时即失败（不重试、不加频次）

# MAINOP_TYPE -> segment_type（依据见模块 docstring 与 README §2；未知码跳过）
MAINOP_TYPE_MAP = {"1": "industry", "2": "product", "3": "region"}

SYMBOL_RE = re.compile(r"^(?:SH|SZ|BJ)\d{6}$")
DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _as_float(value) -> float | None:
    """数值归一：int/float/数值字符串 → float；``null``/异常 → None（透出 null）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _normalize_symbol(raw: str) -> str:
    """个股代码归一：去空白、大写；须形如 SH600000/SZ000001/BJ430047。"""
    sym = str(raw).strip().upper()
    if not SYMBOL_RE.match(sym):
        raise ValueError(
            f"stock-zygc: 非法个股代码 {raw!r}（须形如 SH600000/SZ000001）"
        )
    return sym


def fetch_zygc(symbol: str) -> list[dict]:
    """取单股 ``zygcfx`` 主营构成列表（免 key）；失败抛 ``RuntimeError``。"""
    url = BASE_URL.format(code=symbol)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
            raw = resp.read()
    except Exception as exc:  # noqa: BLE001 —— 端点级失败即红
        raise RuntimeError(
            f"stock-zygc: {symbol} {url} 取数失败 — {type(exc).__name__}: {exc}"
        ) from exc
    if raw[:2] == b"\x1f\x8b":  # 上游偶发无视 Accept-Encoding 回 gzip，魔数兜底解压
        try:
            raw = gzip.decompress(raw)
        except OSError as exc:
            raise RuntimeError(
                f"stock-zygc: {symbol} 响应 gzip 解压失败 — {exc}"
            ) from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"stock-zygc: {symbol} {url} 响应非合法 JSON — "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"stock-zygc: {symbol} 响应非 JSON 对象（{type(payload).__name__}）——端点形态已变"
        )
    rows = payload.get("zygcfx")
    if not isinstance(rows, list) or not rows:
        # 上游对非法代码返回 HTTP 200 + {"status":-1,"message":"股票代码不合法"}，
        # 无 zygcfx；正常股票 zygcfx 恒非空——空即结构异常或该股无披露，一律显式红
        hint = payload.get("message") or payload.get("status")
        raise RuntimeError(
            f"stock-zygc: {symbol} zygcfx 缺失或为空"
            f"（上游提示: {hint!r}）——代码不存在、无主营构成披露或端点形态已变"
        )
    return rows


def run_stock_zygc(symbols: str = "SH600000,SZ000001", limit: int = 200) -> list[dict]:
    """取上市公司主营构成（分行业/分产品/分地区）观测行。

    Args:
        symbols: 逗号分隔个股代码（``SH600000,SZ000001``；沪深京 F10 代码形态）。
            批量按白名单全量采集是调度层的事，本入口只做单股/少股查询。
        limit: **每股**返回行数上限（默认 200，与上游每股 200 行硬上限对齐）。
            每股内按（report_date, segment_type, segment_name）字典序确定性
            排序，保留最近 ``limit`` 行（固定参数重放结果恒定）。

    Returns:
        每行：``source``（"stock-zygc"）/ ``symbol``（SH600000 形态）/
        ``report_date``（"YYYY-MM-DD"）/ ``segment_type``（industry|product|region）/
        ``segment_name``（分部名，上游 ITEM_NAME 原文）/
        ``main_income``（主营收入，元，float|null）/
        ``income_ratio``（收入占比，百分比 0-100，float|null；上游小数 ×100）/
        ``main_profit``（主营利润，元，float|null；上游拼写 RPOFIT 照抄不改）/
        ``gross_margin``（毛利率，百分比 0-100，float|null；上游小数 ×100）/
        ``source_url`` / ``scraped_at``。

    Raises:
        ValueError: ``symbols`` 含非法代码或 ``limit`` 类型非法。
        RuntimeError: 任一股端点不可达、结构异常、无数据或 0 观测行（失败即红；
            错误信息含 symbol，多股时任一股失败即整体失败）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    limit = max(1, min(limit, 1000))
    symbol_list = [_normalize_symbol(s) for s in str(symbols).split(",") if str(s).strip()]
    if not symbol_list:
        raise ValueError(f"symbols must be a non-empty comma string, got {symbols!r}")

    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out: list[dict] = []
    for symbol in symbol_list:  # 任一股失败即抛，fail-fast（错误信息含 symbol）
        raw_rows = fetch_zygc(symbol)
        per_symbol: list[dict] = []
        for raw in raw_rows:
            if not isinstance(raw, dict):
                continue
            seg_type = MAINOP_TYPE_MAP.get(str(raw.get("MAINOP_TYPE", "")).strip())
            seg_name = str(raw.get("ITEM_NAME") or "").strip()
            m = DATE_PREFIX_RE.match(str(raw.get("REPORT_DATE") or "").strip())
            if seg_type is None or not seg_name or m is None:
                continue  # 未知分类码/缺分部名/报告期不可解析的脏行不透出
            income_ratio = _as_float(raw.get("MBI_RATIO"))
            gross_margin = _as_float(raw.get("GROSS_RPOFIT_RATIO"))
            per_symbol.append(
                {
                    "source": SOURCE,
                    "symbol": symbol,
                    "report_date": m.group(1),
                    "segment_type": seg_type,
                    "segment_name": seg_name,
                    "main_income": _as_float(raw.get("MAIN_BUSINESS_INCOME")),
                    "income_ratio": (
                        income_ratio * 100.0 if income_ratio is not None else None
                    ),
                    "main_profit": _as_float(raw.get("MAIN_BUSINESS_RPOFIT")),
                    "gross_margin": (
                        gross_margin * 100.0 if gross_margin is not None else None
                    ),
                    "source_url": BASE_URL.format(code=symbol),
                    "scraped_at": scraped_at,
                }
            )
        if not per_symbol:
            raise RuntimeError(
                f"stock-zygc: {symbol} 解析后 0 观测行（全部为脏行或端点形态已变）"
            )
        per_symbol.sort(key=lambda r: (r["report_date"], r["segment_type"],
                                       r["segment_name"]))
        out.extend(per_symbol[-limit:])

    return out


if __name__ == "__main__":  # 冒烟：python3 spiders/stock-zygc/spider.py
    import pprint

    pprint.pp(run_stock_zygc(symbols="SH600000", limit=3))
