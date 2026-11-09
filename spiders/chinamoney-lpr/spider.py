"""中国货币网 LPR（贷款市场报价利率）—— 2013-10 以来全部报价期次。

坑位（侦察簿路 3/4 + 本轮实测）：

1. 参数名与其他模块**不同**：LPR 用 ``strStartDate`` / ``strEndDate``（不是 startDate/endDate）。
2. **数据在响应顶层 `records`**，不在 ``data.records``：行键 ``showDateCN`` + ``1Y`` / ``5Y``。
   查 ``data.records`` 会永远 0 条（本轮改口径前的实测现象）。
3. **窗口 >1 年静默返回空 records**（2024-01-01..2026-11-07 实测 0 条，
   2026-01-01..2026-11-07 实测 10 条）——按年分片。
4. 端点是 ``cm-u-bk-currency`` 模块（不是 ``cm-u-bk-lpr``）。
5. **假 200**：频控返回 HTTP 200 + HTML 403 页；必须验响应体是否 JSON 且含顶层 records。
6. 端点间隔 ≥3-5s。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

BASE = "https://www.chinamoney.com.cn"
HIS_URL = BASE + "/ags/ms/cm-u-bk-currency/LprHis"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": BASE + "/chinese/bklpr/",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/plain, */*",
}
TENORS = (("1Y", "LPR1Y"), ("5Y", "LPR5Y"))
FIRST_YEAR = 2013          # LPR 集中报价机制 2013-10 起
REQUEST_INTERVAL = 3.5
BACKOFF_SECONDS = 60.0
RETRY_ON_THROTTLE = 1


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _fetch_window(start: str, end: str) -> list[dict]:
    query = urllib.parse.urlencode(
        {"lang": "CN", "strStartDate": start, "strEndDate": end}
    )
    url = f"{HIS_URL}?{query}"
    for attempt in range(RETRY_ON_THROTTLE + 1):
        try:
            req = urllib.request.Request(url, data=b"", headers=HEADERS, method="POST")
            raw = urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "replace")
            payload = json.loads(raw)
            if "records" not in payload:
                raise ValueError(f"no top-level records (keys={sorted(payload)})")
            return payload.get("records") or []
        except (json.JSONDecodeError, ValueError, urllib.error.HTTPError) as exc:
            if attempt >= RETRY_ON_THROTTLE:
                print(f"[chinamoney-lpr] {start}..{end} throttled ({exc}) — giving up", flush=True)
                return []
            print(f"[chinamoney-lpr] {start}..{end} fake-200/throttle ({exc}) — backoff", flush=True)
            time.sleep(BACKOFF_SECONDS)
    return []


def _rows(records: list[dict]) -> list[dict]:
    rows: list[dict] = []
    stamp = _now()
    for record in records:
        day = str(record.get("showDateCN") or "").strip()
        if len(day) != 10:
            continue
        for key, curve in TENORS:
            raw = record.get(key)
            if raw in (None, "", "---"):
                continue
            try:
                rate = float(str(raw).replace(",", ""))
            except ValueError:
                continue
            rows.append(
                {
                    "curve": curve,
                    "date": day,
                    "value": rate,
                    "unit": "%",
                    "source": "chinamoney-lpr",
                    "scraped_at": stamp,
                }
            )
    return rows


def run_chinamoney_lpr(limit: int = 800) -> list[dict]:
    """LPR 1Y/5Y 全部报价期次（长表：每期次每期限一行），单位 %。

    2013-10 起按年分片回补（窗口 >1 年上游静默返空）；取数 newest-first，``limit`` 截断。
    """
    if limit <= 0:
        return []
    wanted = int(limit)
    today = date.today()
    rows: list[dict] = []
    for year in range(today.year, FIRST_YEAR - 1, -1):
        if len(rows) >= wanted:
            break
        start = date(year, 1, 1)
        end = min(date(year, 12, 31), today) if year == today.year else date(year, 12, 31)
        if start > end:
            continue
        records = _fetch_window(start.isoformat(), end.isoformat())
        rows.extend(_rows(records))
        print(f"[chinamoney-lpr] {start}..{end} → {len(records)} periods", flush=True)
        time.sleep(REQUEST_INTERVAL)
    if not rows:
        raise RuntimeError("chinamoney-lpr: 全部年度窗口均无记录（检查假 200/窗口长度）")
    rows.sort(key=lambda r: (r["date"], r["curve"]), reverse=True)
    return rows[:wanted]
