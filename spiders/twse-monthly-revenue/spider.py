"""spiders/twse-monthly-revenue —— 台灣證交所上市公司**當月營收快照**取数。

数据表面（公开 OpenAPI，免 key；仅标准库）：

- 当月营收： ``GET https://openapi.twse.com.tw/v1/opendata/t187ap05_L`` → JSON 数组
  （中文键：公司代號/公司名稱/產業別/營業收入-當月營收/…）。

口径坑位（brief.notes 已点名，逐一落实）：

- **当月快照**：该端点只透出「当期」营收（資料年月 = 上一個月），**无历史回溯**——
  历史靠本单元按月正向累积（每月跑一次落一行/公司）。golden 因此只锚**结构性常量**
  （字段结构 + 半導體業别过滤非空），绝不锚当月数值。
- 民国纪年：``資料年月`` 形如 ``11508``（=2026-08）、``出表日期`` 形如 ``1150917``
  （=2026-09-17），归一为 ISO ``yyyy-mm`` / ``yyyy-mm-dd``（+1911）。
- 营收单位 = 千元（新台币）；``-`` 与空串是缺测占位，归一为 ``None``。
- ``limit`` 语义 = 返回行数上限（保持上游行序）；``industry`` 参数做**子串过滤**
  （如 ``半導體`` 命中 ``半導體業``），golden 用它锚「半导体业别过滤非空」。
- 容错：单端点取数（仅一页）；**任何全量失败抛 ``RuntimeError``**（失败即红），
  解析单行异常只跳过该行并留痕。

工单：``reports/health-tickets/20261006-twse-monthly-revenue-e1427690.yaml``（``kind=generate``）
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

SOURCE = "twse-monthly-revenue"
REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"

HEADERS = {
    "User-Agent": "fd-industry-data/1.0",
    "Accept": "application/json",
}
TIMEOUT = 30  # 秒；单页 JSON（~1000 行），超时即全量失败

# 缺测占位：营收/百分比字段与備註里的 "-" 及空串一律归一 None
MISSING = {"-", "", "—", "–"}


def _get_json(url: str):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _num(value):
    """数值归一：``-``/空串/非法 → ``None``；其余转 float（营收为千元整数）。"""
    if value is None:
        return None
    text = str(value).strip()
    if text in MISSING:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _text(value):
    if value is None:
        return None
    text = str(value).strip()
    return None if text in MISSING else text


def _roc_to_iso_month(value) -> str | None:
    """民国年月 ``11508`` → ``2026-08``；已 ISO 的原样透出。"""
    text = _text(value)
    if not text:
        return None
    if "-" in text:
        return text
    if text.isdigit() and len(text) == 5:
        return f"{int(text[:3]) + 1911}-{text[3:]}"
    return text


def _roc_to_iso_date(value) -> str | None:
    """民国日期 ``1150917`` → ``2026-09-17``；已 ISO 的原样透出。"""
    text = _text(value)
    if not text:
        return None
    if "-" in text:
        return text
    if text.isdigit() and len(text) == 7:
        y, m, d = int(text[:3]) + 1911, text[3:5], text[5:]
        return f"{y}-{m}-{d}"
    return text


def _row(raw: dict, url: str) -> dict:
    """上游中文键 → 行字段（manifest.columns 一一对应）。"""
    return {
        "code": _text(raw.get("公司代號")),
        "name": _text(raw.get("公司名稱")),
        "industry": _text(raw.get("產業別")),
        "data_month": _roc_to_iso_month(raw.get("資料年月")),
        "report_date": _roc_to_iso_date(raw.get("出表日期")),
        "revenue_current": _num(raw.get("營業收入-當月營收")),
        "revenue_prev": _num(raw.get("營業收入-上月營收")),
        "revenue_last_year": _num(raw.get("營業收入-去年當月營收")),
        "mom_pct": _num(raw.get("營業收入-上月比較增減(%)")),
        "yoy_pct": _num(raw.get("營業收入-去年同月增減(%)")),
        "revenue_accum": _num(raw.get("累計營業收入-當月累計營收")),
        "note": _text(raw.get("備註")),
        "url": url,
        "source": SOURCE,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def run_twse_monthly_revenue(limit: int = 100, industry: str | None = None) -> list[dict]:
    """取当月营收快照行（可选 ``industry`` 子串过滤）；全量失败 → 抛 ``RuntimeError``。

    快照语义：每期运行只透出当期行，历史由调度按月正向累积，不在本单元内回溯。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    payload = _get_json(REVENUE_URL)
    if not isinstance(payload, list):
        raise RuntimeError(f"twse-monthly-revenue: unexpected payload {type(payload).__name__}")

    needle = str(industry).strip() if industry else None
    rows: list[dict] = []
    skipped = 0
    for raw in payload:
        if len(rows) >= limit:
            break
        if not isinstance(raw, dict):
            skipped += 1
            continue
        try:
            row = _row(raw, REVENUE_URL)
        except Exception:  # noqa: BLE001 - 单行结构异常只跳过并留痕
            skipped += 1
            continue
        if needle and (not row["industry"] or needle not in row["industry"]):
            continue
        rows.append(row)
    if not rows:
        raise RuntimeError(
            f"twse-monthly-revenue: 取数失败（payload={len(payload)} 行, 跳过={skipped}, "
            f"industry={industry!r} 过滤后为空）"
        )
    return rows[:limit]


if __name__ == "__main__":  # 单页冒烟：python3 spiders/twse-monthly-revenue/spider.py
    print(json.dumps(run_twse_monthly_revenue(limit=2), ensure_ascii=False, indent=2))
