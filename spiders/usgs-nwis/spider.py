"""spiders/usgs-nwis —— USGS 水文日值（National Water Information System, DV）取数。

数据表面（公开 REST，免 key；侦察簿 W1-J，见工单 brief.notes）：

- ``GET https://waterservices.usgs.gov/nwis/dv/`` → JSON
  ``value.timeSeries[<i>].values[<j>].value[]``

实测请求参数（只发这些，2026-10-07 直连冒烟；brief.source_urls 原样端点）：

- ``sites=01646500``（Potomac River 近华盛顿 Little Falls 泵站，百年站）
- ``startDT=2020-01-01&endDT=2020-12-31``（固定历史年，ISO 日期；2020 闰年 366 天）
- ``parameterCd=00060``（河流流量 Streamflow, ft3/s）
- ``format=json``

口坑（brief.notes 逐条落实）：

- **golden 锚固定站点固定历史年份数值**：2020 年已定稿（qualifier=A 已核准），
  日值不再变化——锚 2020-01-01 == 7610 ft3/s（实测）。
- ``dateTime`` 为 ``2020-01-01T00:00:00.000`` → 取日期部分 ISO ``date``。
- ``qualifiers``（复数，list）逗号拼接透出（``A``=已核准，``P``=预发布等）。
- 缺测占位 ``-999999`` → ``None``，绝不当作真实流量透出。
- 容错：单序列/单值失败跳过留痕；拉取失败 / 零可用行 → 抛 ``RuntimeError``
  （失败即红，不静默返回空列表）。

工单：``reports/health-tickets/20261006-usgs-nwis-f0a47fcb.yaml``（``kind=generate``）
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone

SOURCE = "usgs-nwis"
DV_URL = (
    "https://waterservices.usgs.gov/nwis/dv/"
    "?sites=01646500&startDT=2020-01-01&endDT=2020-12-31"
    "&parameterCd=00060&format=json"
)
FILL_VALUE = -999999.0  # NWIS 缺测占位

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
TIMEOUT = 30  # 秒；单站全年序列约 30 KB，超时即失败（失败即红，不重试放大）


def _get_json(url: str):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _num(value):
    """流量归一：``-999999`` 缺测占位 / 脏值 → ``None``。"""
    if value is None:
        return None
    try:
        num = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return None if num == FILL_VALUE else num


def run_usgs_nwis(limit: int = 100) -> list[dict]:
    """取站点 01646500 于 2020 年的流量日值行（date 升序），至多 ``limit`` 行。

    拉取失败或零可用行 → 抛 ``RuntimeError``（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    try:
        payload = _get_json(DV_URL)
    except Exception as exc:  # noqa: BLE001 - 全局失败：失败即红
        raise RuntimeError(f"usgs-nwis: 拉取失败 {type(exc).__name__}: {exc}") from exc
    series_list = (payload.get("value") or {}).get("timeSeries") if isinstance(
        payload, dict) else None
    if not isinstance(series_list, list):
        raise RuntimeError("usgs-nwis: 载荷无 value.timeSeries（上游口径已变，转人工）")

    rows: list[dict] = []
    errors: list[str] = []
    for series in series_list:
        try:
            info = series.get("sourceInfo") or {}
            variable = series.get("variable") or {}
            site = ((info.get("siteCode") or [{}])[0]).get("value")
            site_name = info.get("siteName")
            param_cd = ((variable.get("variableCode") or [{}])[0]).get("value")
            unit = (variable.get("unit") or {}).get("unitCode")
            if not site:
                raise ValueError("series without siteCode")
            for block in series.get("values") or []:
                for item in block.get("value") or []:
                    date = str(item.get("dateTime", ""))[:10] or None
                    qualifiers = ",".join(item.get("qualifiers") or []) or None
                    rows.append({
                        "site": site,
                        "site_name": site_name,
                        "parameter_cd": param_cd,
                        "date": date,
                        "value": _num(item.get("value")),
                        "unit": unit,
                        "qualifiers": qualifiers,
                        "url": DV_URL,
                        "source": SOURCE,
                        "scraped_at": datetime.now(timezone.utc).isoformat(),
                    })
        except Exception as exc:  # noqa: BLE001 - 单序列失败跳过留痕
            errors.append(f"series: {type(exc).__name__}: {exc}")
    if errors:
        print(f"{SOURCE}: skipped {len(errors)} series failure(s): "
              + "; ".join(errors[:3]), file=sys.stderr)
    if not rows:
        raise RuntimeError("usgs-nwis: 全部序列取数失败或为空（上游空窗或口径变更，转人工）")
    return rows[:limit]


if __name__ == "__main__":  # 冒烟：python3 spiders/usgs-nwis/spider.py
    print(json.dumps(run_usgs_nwis(limit=3), ensure_ascii=False, indent=2))
