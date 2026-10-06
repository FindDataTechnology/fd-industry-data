"""spiders/wto-eping —— WTO ePing 贸易壁垒通报流（最新通报，匿名表面）。

数据表面（公开 REST，免鉴权；见工单 brief.notes「侦察簿 W3」）：

- 最新通报： ``GET https://eping.wto.org/api/v1/notifications/getLatestNotifications``
  → ``[{notifyingMember, distributionDate, documentSymbol, title, products}]``（匿名实测 10 行，
  当日新鲜：TBT 与 SPS 混排，按分发时间倒序）。

口坑（brief.notes / 实测逐一落实）：

- **匿名可用，但不收参数**：实测带 ``language=EN`` 即 HTTP 400——本单元**零查询参数**，
  只带常规浏览器 UA/头；HS+日期检索需免费注册，本批不接（按 brief 只接匿名最新流）。
- **内容协商看 Accept 精确值**：``Accept: application/json`` 返 JSON；复合值（``... , */*``）
  会回落 ``application/xml``（实测）——故只发这一个精确头。
- ``documentSymbol`` 原文带**前导空格**（如 ``" G/SPS/N/KHM/3"``），须 strip；
  系列段（``G/SPS``/``G/TBT``）派生 ``type``：SPS 或 TBT，其余原样小写透出（不丢真）。
- ``distributionDate`` 为 ``dd/mm/yyyy`` 字符串，归一为 ISO ``YYYY-MM-DD``（``date_raw`` 保留原文）。
- ``title``/``products`` 是富文本 HTML 片段（``<p>``/``<strong>``/``&nbsp;``），去标签 + 反转义
  成纯文本；不去真、不造数。
- ``limit`` 语义 = 返回行数上限（端点单次至多 10 行，>10 也只有 10）。
- 容错：单一数据表面，任何取数/解析失败或 0 行 → 抛 ``RuntimeError``（失败即红，不静默回空）。

工单：``reports/health-tickets/20261006-wto-eping-51734044.yaml``（``kind=generate``）
"""
from __future__ import annotations

import html
import json
import re
import urllib.request
from datetime import datetime, timezone

SOURCE = "wto-eping"
FEED_URL = "https://eping.wto.org/api/v1/notifications/getLatestNotifications"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    # 内容协商按 Accept **精确值**判定：``application/json`` 返 JSON；
    # 复合值（如 ``application/json, text/plain, */*``）会回落 application/xml —— 只发这一个。
    "Accept": "application/json",
    "Referer": "https://eping.wto.org/",
}
TIMEOUT = 30  # 秒；轻量 JSON，超时即红（单表面不重试、不加频次）

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _get_json(url: str):
    """GET 一个 JSON 端点（固定 https 主机；无鉴权、零查询参数、无重试放大）。"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _clean_text(value) -> str | None:
    """富文本 HTML 片段 → 压缩空白纯文本；空串归 ``None``。"""
    if value is None:
        return None
    text = html.unescape(_TAG_RE.sub(" ", str(value)))
    text = _WS_RE.sub(" ", text).strip()
    return text or None


def _iso_date(value) -> str | None:
    """``dd/mm/yyyy`` → ``YYYY-MM-DD``；解析失败原样透出（不丢真）。"""
    if not value:
        return None
    text = str(value).strip()
    parts = text.split("/")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        dd, mm, yyyy = parts
        return f"{yyyy}-{mm}-{dd}"
    return text


def _series_type(document_symbol: str) -> str | None:
    """从 documentSymbol 系列段派生类型：``G/SPS/...`` → ``SPS``，``G/TBT/...`` → ``TBT``。"""
    symbol = document_symbol.strip().upper()
    if "/SPS/" in symbol:
        return "SPS"
    if "/TBT/" in symbol:
        return "TBT"
    return None


def run_wto_eping(limit: int = 100) -> list[dict]:
    """取最新通报流（匿名，单次至多 10 行）；全部失败 → 抛 ``RuntimeError``（失败即红）。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    try:
        payload = _get_json(FEED_URL)
    except Exception as exc:  # noqa: BLE001 - 单一数据表面，失败即红
        raise RuntimeError(f"wto-eping: 取数失败 — {type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError(f"wto-eping: unexpected payload type {type(payload).__name__}")
    if not payload:
        raise RuntimeError("wto-eping: 最新通报流为 0 行（视为异常，不静默回空）")

    rows: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("documentSymbol") or "").strip()
        rows.append(
            {
                "notification_id": symbol or None,
                "type": _series_type(symbol),
                "member": (str(item.get("notifyingMember") or "").strip() or None),
                "date": _iso_date(item.get("distributionDate")),
                "date_raw": (str(item.get("distributionDate") or "").strip() or None),
                "title": _clean_text(item.get("title")),
                "products": _clean_text(item.get("products")),
                "url": FEED_URL,
                "source": SOURCE,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        if len(rows) >= limit:
            break
    return rows


if __name__ == "__main__":  # 单页冒烟：python3 spiders/wto-eping/spider.py
    print(json.dumps(run_wto_eping(limit=2), ensure_ascii=False, indent=2))
