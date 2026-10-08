"""spiders/un-comtrade —— UN Comtrade 年度 HS 贸易流（生产端点 data/v1/get）取数。

数据表面（工单 brief / 20261007-un-comtrade-d0c86639）：

- ``GET https://comtradeapi.un.org/data/v1/get/C/A/HS`` → JSON
  ``{"count": N, "data": [record...], "error": ""}``
- 鉴权：请求头 ``Ocp-Apim-Subscription-Key: $COMTRADE_API_KEY``（env 注入，
  绝不硬编码/入文件；Azure APIM 双 key 轮换场景兼容 ``COMTRADE_SUBSCRIPTION_KEY`` 回退）
- 免费订阅层 500 次/天——本爬虫每次调用只发 1 个请求（单一
  reporter/period/cmd/flow/partner 组合），控频由调用方负责
- preview 端点（``/public/v1/preview``，免 key）仅开发期对照：单次 ≤500 行且
  中国月度止于 2024-12（实测）——生产取数一律走本生产端点

实测锚（2026-10-07 实况冻结，年度定稿历史值）：

- reporter 156（中国）/ period 2023 / cmdCode 854142（锂离子蓄电池）/
  flowCode X（出口）/ partner 0（世界）→ ``primaryValue == 4154890471.0``、
  ``netWgt == 92282836.464``、``qty == 5209311702.0``（golden 重放锚）

口坑：

- 数值直接透传上游 JSON number；null → ``None``，绝不造数
- ``error`` 非空 / ``data`` 为空 / 非 200 → 抛 ``RuntimeError``（失败即红，
  不静默返回空列表）
- ``limit`` 同时作为 ``maxRecords`` 下发（防御性钳到 100000）

工单：``reports/health-tickets/20261007-un-comtrade-d0c86639.yaml``（kind=generate）
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

SOURCE = "un-comtrade"
BASE_URL = "https://comtradeapi.un.org/data/v1/get/C/A/HS"
MAX_RECORDS_CAP = 100000  # 防御性上限；免费层单调用实际配额以上游为准
TIMEOUT = 40  # 秒；实测单组合查询 <3s，超时即失败（失败即红，不重试放大）


def _get_json(url: str, key: str):
    req = urllib.request.Request(
        url,
        headers={
            "Ocp-Apim-Subscription-Key": key,
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(300).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - 错误体读取失败不掩盖主错误
            pass
        raise RuntimeError(f"un-comtrade: HTTP {exc.code} {detail}") from exc
    except Exception as exc:  # noqa: BLE001 - 连接层失败统一失败即红
        raise RuntimeError(f"un-comtrade: 拉取失败 {type(exc).__name__}: {exc}") from exc
    return json.loads(raw.decode("utf-8", "replace"))


def _num(value):
    """透传上游数值；null / 布尔 / 非数值 → None（绝不造数）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def run_un_comtrade(
    limit: int = 100,
    reporter_code: int = 156,
    period: str | None = None,
    years_back: int = 5,
    cmd_code: str = "854142",
    flow_code: str = "X",
    partner_code: int = 0,
) -> list[dict]:
    """取单一组合（reporter/cmd/flow/partner）近 N 个完整年度的贸易流序列。

    ``period`` 缺省时取**滚动窗口**：从去年往前共 ``years_back`` 个完整年度
    （逗号列表一次请求，仍只发 1 个 API 调用）——单一组合+单年的查询结构性
    只返回 1 行（2026-10-08 巡检发现首爬 rows=1 的根因，此前 period 硬编码
    "2023" 每月重写同一行）；显式传 ``period`` 可复现单年行为（golden 重放）。

    key 缺失 / 非 200 / ``error`` 非空 / ``data`` 为空 → 抛 ``RuntimeError``（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    key = os.environ.get("COMTRADE_API_KEY") or os.environ.get("COMTRADE_SUBSCRIPTION_KEY")
    if not key:
        raise RuntimeError(
            "un-comtrade: env COMTRADE_API_KEY 未设置（secrets/fd-industry-source-keys.env 注入），拒绝裸跑"
        )

    if period is None:
        try:
            years_back_n = max(1, min(int(years_back), 30))
        except (TypeError, ValueError):
            years_back_n = 5
        last_complete = datetime.now(timezone.utc).year - 1
        period = ",".join(str(y) for y in range(last_complete, last_complete - years_back_n, -1))

    url = (
        f"{BASE_URL}?reporterCode={int(reporter_code)}"
        f"&period={period}&cmdCode={cmd_code}&flowCode={flow_code}"
        f"&partnerCode={int(partner_code)}&maxRecords={min(limit, MAX_RECORDS_CAP)}"
    )
    payload = _get_json(url, key)
    if not isinstance(payload, dict):
        raise RuntimeError("un-comtrade: 载荷非 JSON object（上游口径已变，转人工）")
    if str(payload.get("error") or "").strip():
        raise RuntimeError(f"un-comtrade: 上游 error 字段非空: {payload['error']}")
    records = payload.get("data")
    if not isinstance(records, list) or not records:
        raise RuntimeError("un-comtrade: data 为空（组合无数据或上游空窗，转人工）")

    scraped_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        rows.append({
            "period": rec.get("period"),
            "ref_year": rec.get("refYear"),
            "ref_month": rec.get("refMonth"),
            "reporter_code": rec.get("reporterCode"),
            "flow_code": rec.get("flowCode"),
            "partner_code": rec.get("partnerCode"),
            "partner2_code": rec.get("partner2Code"),
            "classification_code": rec.get("classificationCode"),
            "cmd_code": rec.get("cmdCode"),
            "customs_code": rec.get("customsCode"),
            "mos_code": rec.get("mosCode"),
            "qty": _num(rec.get("qty")),
            "net_wgt": _num(rec.get("netWgt")),
            "gross_wgt": _num(rec.get("grossWgt")),
            "fob_value": _num(rec.get("fobvalue")),
            "cif_value": _num(rec.get("cifvalue")),
            "primary_value": _num(rec.get("primaryValue")),
            "is_reported": rec.get("isReported"),
            "is_aggregate": rec.get("isAggregate"),
            "source": SOURCE,
            "source_url": url,  # key 走请求头，URL 本身无凭据
            "scraped_at": scraped_at,
        })
    if not rows:
        raise RuntimeError("un-comtrade: 记录全部无法解析（上游口径变更，转人工）")
    rows.sort(key=lambda r: (r.get("ref_year") or r.get("period") or 0))
    return rows[-limit:]


if __name__ == "__main__":  # 冒烟：source secrets 后 python3 spiders/un-comtrade/spider.py
    print(json.dumps(run_un_comtrade(limit=3), ensure_ascii=False, indent=2))
