"""spiders/jp-yahoo-events —— 日本个股的**定期披露事件**（決算発表日 + 业绩历史），
走 Yahoo Finance 的公开端点（与 `yfinance` 同源），无需任何 key。

为什么有它（add-restaurant-vertical 3.4）：TDNet 日次列表只保留约 31 天，覆盖不到
「泉盛/萨莉亚**最新年度**披露事件」；EDINET API v2 需要 `Subscription-Key`。Yahoo 的
`quoteSummary` 给出 `earningsHistory`（已披露的各期实际 EPS）与 `calendarEvents.earnings`
（下次決算発表日），正好补上"年度/季度披露事件"这一面。

访问契约（2026-11-09 实测，经集群认证代理）：

- `GET https://finance.yahoo.com` → 发 cookie（**必须用首页**；`/quote/<tk>` 不发 cookie，
  实测 0 个 cookie 时 crumb 必 401）
- `GET https://query1.finance.yahoo.com/v1/test/getcrumb` → 11 字符 crumb（带 cookie 才有）
- `GET .../v10/finance/quoteSummary/<tk>?modules=earningsHistory,calendarEvents&crumb=<crumb>`
- 首页 seed 偶发 401/无 cookie → **必须重试**（实测第 1 次失败、第 2 次成功）

口坑：

- crumb 与会话 cookie 绑定：换 jar 必重取 crumb。
- 只取"事实"字段：`earningsHistory.history[].{quarter.fmt, epsActual.raw}` 与
  `calendarEvents.earnings.{earningsDate[].fmt, isEarningsDateEstimate}`；
  上游给 `{}`（空对象）表示该字段缺，**如实留空，不补零不推断**。
- `isEarningsDateEstimate=true` 表示日期是**预估**，行里必须带 `date_is_estimate` 标注。
- 失败即红（`RuntimeError`）；单个标的失败不掩盖其它标的，但**全部失败**才抛错。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.cookiejar import CookieJar

SOURCE = "jp-yahoo-events"
SEED_URL = "https://finance.yahoo.com"
CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
SUMMARY_URL = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
MODULES = "earningsHistory,calendarEvents"
DEFAULT_TICKERS = ("7550.T", "7581.T", "2702.T", "3197.T")  # 日股餐饮宇宙
TIMEOUT = 60
SEED_TRIES = 4


def _proxy_map() -> dict:
    """从环境显式构造代理表。

    为什么不用默认 ProxyHandler：macOS 上 ``urllib.request.getproxies()`` 会回落到
    系统代理配置（本机 7892 劫持），而不是我们导出的 ``HTTP(S)_PROXY``——实测表现是
    直连 Yahoo 回 **403**。显式注入保证与运维脚本一致的行为；无代理环境则返回空表
    （等价直连）。"""
    out: dict[str, str] = {}
    for scheme, keys in (("http", ("http_proxy", "HTTP_PROXY")),
                         ("https", ("https_proxy", "HTTPS_PROXY"))):
        for key in keys:
            val = (os.environ.get(key) or "").strip()
            if val:
                out[scheme] = val
                break
    return out


def _opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler(_proxy_map()),
        urllib.request.HTTPCookieProcessor(CookieJar()),
    )


def _ua():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _session():
    """首页 seed → crumb。首页偶发 401/无 cookie，故重试 ``SEED_TRIES`` 次。"""
    last = "unknown"
    for _ in range(SEED_TRIES):
        op = _opener()
        try:
            op.open(urllib.request.Request(SEED_URL, headers=_ua()), timeout=TIMEOUT).read()
        except Exception as exc:  # noqa: BLE001 - 统一重试
            last = f"seed {type(exc).__name__}: {exc}"
            time.sleep(2)
            continue
        try:
            crumb = op.open(urllib.request.Request(CRUMB_URL, headers=_ua()), timeout=TIMEOUT).read().decode().strip()
        except Exception as exc:  # noqa: BLE001
            last = f"crumb {type(exc).__name__}: {exc}"
            time.sleep(2)
            continue
        if crumb and "<" not in crumb:
            return op, crumb
        last = "crumb 空或非法"
        time.sleep(2)
    raise RuntimeError(f"{SOURCE}: 会话建立失败（首页/crumb {SEED_TRIES} 次均未成功：{last}）")


def _num(node):
    """Yahoo 的 {raw,fmt} 包装 → 数值；空对象/缺字段 → None（如实留空）。"""
    if isinstance(node, dict):
        v = node.get("raw")
        return v if isinstance(v, (int, float)) else None
    return None


def parse_summary(payload: dict, ticker: str) -> list[dict]:
    """把一次 quoteSummary 响应拍平成事件行。纯函数（golden 直接喂夹具）。"""
    scraped_at = datetime.now(timezone.utc).isoformat()
    body = (payload.get("quoteSummary") or {})
    result = body.get("result") or []
    if not result:
        err = json.dumps(body.get("error"), ensure_ascii=False)[:160]
        raise RuntimeError(f"{SOURCE}: quoteSummary {ticker} 无 result（{err}）")
    r0 = result[0]
    rows: list[dict] = []

    for h in ((r0.get("earningsHistory") or {}).get("history") or []):
        period = (h.get("quarter") or {}).get("fmt")
        if not period:
            continue  # 结构不可用即跳过，不造行
        rows.append({
            "ticker": ticker,
            "event_kind": "earnings_result",
            "period": period,
            "eps_actual": _num(h.get("epsActual")),
            "surprise_pct": _num(h.get("surprisePercent")),
            "date_is_estimate": None,
            "source": SOURCE,
            "scraped_at": scraped_at,
        })

    earnings = ((r0.get("calendarEvents") or {}).get("earnings") or {})
    is_est = earnings.get("isEarningsDateEstimate")
    for d in (earnings.get("earningsDate") or []):
        fmt = d.get("fmt") if isinstance(d, dict) else None
        if not fmt:
            continue
        rows.append({
            "ticker": ticker,
            "event_kind": "earnings_date",
            "period": fmt,
            "eps_actual": None,
            "surprise_pct": None,
            "date_is_estimate": bool(is_est) if isinstance(is_est, bool) else None,
            "source": SOURCE,
            "scraped_at": scraped_at,
        })
    return rows


def run_jp_yahoo_events(
    limit: int = 50,
    tickers: str | None = None,
    fixture: str | None = None,
) -> list[dict]:
    """返回日股餐饮宇宙的披露事件行（业绩实际 + 下次決算発表日）。

    ``tickers`` 缺省为 4 家；``fixture`` 直供时只解析该 JSON（golden 重放，零网络）。
    单个标的失败记 stderr 并继续；**全部失败**抛 ``RuntimeError``。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{SOURCE}: limit 非法（{limit!r}）") from exc
    tks = [t.strip() for t in (tickers.split(",") if tickers else DEFAULT_TICKERS) if t.strip()]

    if fixture:
        doc = json.loads(open(fixture, encoding="utf-8").read())
        tk = doc.get("_ticker") or (tks[0] if tks else "?")
        rows = parse_summary(doc, tk)
        print(f"{SOURCE}: fixture={fixture} rows={len(rows)}", file=sys.stderr)
        return rows[:limit]

    op, crumb = _session()
    rows: list[dict] = []
    failed: list[str] = []
    for tk in tks:
        url = (f"{SUMMARY_URL.format(ticker=urllib.parse.quote(tk))}"
               f"?modules={MODULES}&crumb={urllib.parse.quote(crumb)}")
        try:
            payload = json.loads(op.open(urllib.request.Request(url, headers=_ua()), timeout=TIMEOUT).read())
            rows.extend(parse_summary(payload, tk))
        except Exception as exc:  # noqa: BLE001 - 单标的失败不掩盖其它
            failed.append(tk)
            print(f"{SOURCE}: {tk} 失败 {type(exc).__name__}: {exc}", file=sys.stderr)
    if len(failed) == len(tks):
        raise RuntimeError(f"{SOURCE}: 全部标的失败（{', '.join(failed)}），转人工")
    print(f"{SOURCE}: tickers={len(tks)} failed={len(failed)} rows={len(rows)}", file=sys.stderr)
    return rows[:limit]
