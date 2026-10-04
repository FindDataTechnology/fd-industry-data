"""drill-gen-healthz — 平台健康检查 JSON 直取（生成流演练单元）。

来源：``GET https://platform.finddatatech.cloud/healthz``（免鉴权、纯 JSON、无反爬）。
产出：单行，字段 ``ok`` / ``cells`` / ``url`` / ``source`` / ``scraped_at``（+ 观测值 ``uptime_ms``）。

坑位与容错（工单 brief.notes）：
- ``uptimeMs`` 是波动值：本单元原样透出为 ``uptime_ms``，但 **golden 断言绝不锚它、
  也不锚任何数值**（``cells`` 同样只透出不断言）；
- 纯 JSON 直取、无分页参数；``limit`` 只做行数截断（上游本就只有单行）；
- 只依赖标准库 ``urllib``（镜像自带），不引入新依赖、不加 UA 伪装之外的任何请求头；
- 响应非 200 / 非 JSON / 缺 ``ok`` 字段 → 抛异常，让验证链红——不静默返回空行。
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

SOURCE = "drill-gen-healthz"
HEALTHZ_URL = "https://platform.finddatatech.cloud/healthz"
TIMEOUT_S = 20
USER_AGENT = "finddata-spider-drill-gen-healthz/1.0 (+https://finddatatech.cloud)"


def fetch_healthz(url: str = HEALTHZ_URL, timeout: int = TIMEOUT_S) -> dict:
    """GET 平台健康检查端点，返回解析后的 JSON dict（非 200 / 非 JSON → 抛异常）。"""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310 - 固定 https 端点
        status = getattr(resp, "status", 200)
        raw = resp.read().decode("utf-8", errors="replace")
    if status != 200:
        raise RuntimeError(f"{SOURCE}: unexpected HTTP {status} from {url}")
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{SOURCE}: response is not JSON: {raw[:200]!r}") from exc
    if not isinstance(doc, dict) or "ok" not in doc:
        raise ValueError(f"{SOURCE}: unexpected payload (need an 'ok' field): {raw[:200]!r}")
    return doc


def run_drill_gen_healthz(limit: int = 100) -> list[dict]:
    """取一行平台健康状态：``ok`` / ``cells`` / ``url`` / ``source`` / ``scraped_at``。"""
    doc = fetch_healthz()
    row: dict = {
        "ok": bool(doc.get("ok")),
        "cells": doc.get("cells"),
        "url": HEALTHZ_URL,
        "source": SOURCE,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }
    if "uptimeMs" in doc:
        # 波动值：仅作观测透出，不允许作为 golden 断言锚点
        row["uptime_ms"] = doc.get("uptimeMs")
    if limit is None or int(limit) <= 0:
        return [row]  # 单行源：limit 缺失或非正时不静默返回空
    return [row][: int(limit)]