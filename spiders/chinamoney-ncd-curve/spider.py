"""中国货币网 同业存单 AAA 收盘收益率曲线（NCD）—— 日频曲线点。

端点（本轮从站点前端 fragment ``/r/cms/chinese/chinamoney/html/bond/cls-yld-curv-cn.html``
实读，非猜测）：

``POST /ags/ms/cm-u-bk-currency/ClsYldCurvHis
    ?lang=CN&reference=1&bondType=CYCC41B&startDate=D&endDate=D&termId=0.5&pageNum=1&pageSize=50``

坑位：

1. **只能按单日查**：``startDate`` 必须等于 ``endDate``；给区间（8-01..11-07）实测
   返回 0 条，给单日（10-20）返回 17 个期限点。故本单元按**交易日逐日**取，不按区间。
2. **顶层 `records`**，行键 ``newDateValueCN`` + ``yearTermStr``（年化期限，如 0.019=1W）
   + ``maturityYieldStr`` / ``currentYieldStr`` / ``futureYieldStr``（reference=1 只填到期）。
3. ``bondType`` 码表来自 ``ClsYldCurvCurvGO``（75 条）：CYCC41B=同业存单(AAA)，
   CYCC41C=AA+，CYCC41D=AA，CYCC41J=AAA-，其余见 README 表。
4. **线上只滚动约 2 个月**（侦察簿路 4；本单元不做历史回补）——序列自点亮日起累积，
   中断即缺段，由既有 gap_in_series 断档检测兜底（design D3）。
5. 端点间隔 ≥3-5s；频控同 chinamoney 假 200（HTTP 200 + HTML 403 页），须验 JSON。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

BASE = "https://www.chinamoney.com.cn"
HIS_URL = BASE + "/ags/ms/cm-u-bk-currency/ClsYldCurvHis"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": BASE + "/chinese/bkcurvclosedy/",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/plain, */*",
}
#: 曲线码（ClsYldCurvCurvGO 实测）：本单元只取 AAA。
BOND_TYPE = "CYCC41B"
CURVE_NAME = "同业存单(AAA)"
REFERENCE = "1"            # 1=到期收盘（2=即期 3=远期；NCD 只用到期）
REQUEST_INTERVAL = 3.5     # 端点间隔 ≥3-5s
BACKOFF_SECONDS = 60.0
RETRY_ON_THROTTLE = 1
MAX_CONSECUTIVE_EMPTY = 3  # 连续空交易日即判「已越过滚动窗起点」


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _fetch_day(day: str) -> list[dict]:
    query = urllib.parse.urlencode(
        {
            "lang": "CN",
            "reference": REFERENCE,
            "bondType": BOND_TYPE,
            "startDate": day,
            "endDate": day,
            "termId": "0.5",
            "pageNum": "1",
            "pageSize": "50",
        }
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
                print(f"[chinamoney-ncd-curve] {day} throttled ({exc}) — giving up", flush=True)
                return []
            print(f"[chinamoney-ncd-curve] {day} fake-200/throttle ({exc}) — backoff", flush=True)
            time.sleep(BACKOFF_SECONDS)
    return []


def _rows(records: list[dict], day: str) -> list[dict]:
    rows: list[dict] = []
    stamp = _now()
    for record in records:
        pub_day = str(record.get("newDateValueCN") or day).strip()
        raw_term = record.get("yearTermStr")
        raw_yield = record.get("maturityYieldStr")
        if raw_yield in (None, "", "---"):
            raw_yield = record.get("currentYieldStr")
        try:
            tenor_years = float(str(raw_term))
            value = float(str(raw_yield))
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "date": pub_day,
                "curve": "NCD_AAA",
                "curve_name": CURVE_NAME,
                "reference": "maturity",
                "tenor_years": tenor_years,
                "tenor_days": int(round(tenor_years * 365)),
                "yield_pct": value,
                "unit": "%",
                "source": "chinamoney-ncd-curve",
                "scraped_at": stamp,
            }
        )
    return rows


def run_chinamoney_ncd_curve(limit: int = 600, days: int = 45) -> list[dict]:
    """同业存单 AAA 收盘曲线点（每交易日 × 每期限一行），单位 %。

    Args:
        limit: 返回行数上限（按日期倒序截断）。
        days: 回溯的自然日天数（默认 45 ≈ 30 个交易日；线上滚动窗约 2 个月）。

    Raises:
        RuntimeError: 回溯窗内一天数据都没取到（全假 200/端点变更）。
    """
    if limit <= 0:
        return []
    wanted = int(limit)
    span = max(1, int(days))
    today = date.today()
    rows: list[dict] = []
    empty_streak = 0
    for offset in range(span):
        day = today - timedelta(days=offset)
        if day.weekday() >= 5:      # 周末无曲线
            continue
        records = _fetch_day(day.isoformat())
        if records:
            empty_streak = 0
            rows.extend(_rows(records, day.isoformat()))
            print(f"[chinamoney-ncd-curve] {day} → {len(records)} tenors", flush=True)
        else:
            empty_streak += 1
            print(f"[chinamoney-ncd-curve] {day} → empty", flush=True)
            if empty_streak >= MAX_CONSECUTIVE_EMPTY:
                print(
                    "[chinamoney-ncd-curve] 连续空窗——已越过线上滚动起点，停止回溯",
                    flush=True,
                )
                break
        time.sleep(REQUEST_INTERVAL)
    if not rows:
        raise RuntimeError("chinamoney-ncd-curve: 回溯窗内无任何曲线点（检查假 200/端点）")
    rows.sort(key=lambda r: (r["date"], r["tenor_years"]), reverse=True)
    return rows[:wanted]
