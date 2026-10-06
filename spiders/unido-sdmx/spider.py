"""spiders/unido-sdmx —— UNIDO 工业生产指数（IIP，月度）取数。

数据表面（公开 SDMX REST，免鉴权、直连无需代理；工单 brief.notes「侦察簿 W2-12」）：

- 数据： ``GET {BASE}/sdmx/data/UNIDO/IIP/1.0/{COUNTRY}.{INDICATOR}.{CLASSIFICATION}``
- 结构： ``GET {BASE}/sdmx/datastructure/UNIDO/IIP_STRUCTURE/1.0``（仅用于把代码翻成名称）
- IIP 数据流目录： ``GET {BASE}/sdmx/dataflow/UNIDO/all/all``（IIP = Indices of Industrial Production）

口坑（brief.notes 已点名，逐一落实）：

- ``Accept`` 头必须用 ``application/vnd.sdmx.data+json;version=2.0.0``（数据）/
  ``application/vnd.sdmx.structure+json;version=2.0.0``（结构），否则 406；
- 国码用 **ISO 数字码字符串**（``156`` = 中国，结构枚举内为零填充三位，如 ``008``；
  实测 ``156`` 与 ``0156`` 都能命中键，本单元统一发三位零填充码 ``156``——探测确认
  服务端键值即为 ``156``）；key 维度序 = COUNTRY.INDICATOR.CLASSIFICATION；
- ``startPeriod``/``endPeriod`` 格式为 ``YYYY-MXX``（如 ``2020-M01``），格式错返回 400，
  本单元默认**不带**该参数（全史一次取回后本地按月过滤），规避格式坑；
- 响应是紧凑 SDMX-JSON v2：``data.dataSets[*].series[key].observations`` 为
  ``{TIME_PERIOD: [[value]]}``；TIME_PERIOD 混频（``2005`` 年度 / ``2026 M01`` 月度 /
  ``2026 Q1`` 季度），月度源只保留 ``YYYY MXX`` 形态；
- ``limit`` 语义 = 返回行数上限：按种子国顺序取数、期内按 period 升序截断，``limit``
  固定时行集确定（golden 重放可复现）；
- 容错：单国失败只跳过并留痕；**全部国家失败抛 ``RuntimeError``**（失败即红，不静默
  返回空列表）。结构（名称表）取不到时降级为纯代码行，不影响取数。

工单：``reports/health-tickets/20261006-unido-sdmx-96c2142b.yaml``（``kind=generate``）
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone

SOURCE = "unido-sdmx"
BASE = "https://stat.unido.org/portal"
FLOW = "IIP"
FLOW_VERSION = "1.0"
DATA_URL = BASE + "/sdmx/data/UNIDO/" + FLOW + "/" + FLOW_VERSION + "/{key}"
STRUCTURE_URL = BASE + "/sdmx/datastructure/UNIDO/" + FLOW + "_STRUCTURE/" + FLOW_VERSION

ACCEPT_DATA = "application/vnd.sdmx.data+json;version=2.0.0"
ACCEPT_STRUCTURE = "application/vnd.sdmx.structure+json;version=2.0.0"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# 种子国：工单 brief.expectations 指定「中国 156 等国」；实测参数只有 156，其余仅 README 示例。
SEED_COUNTRIES = ("156",)
INDICATOR = "52"  # Original index（原指数；53 = 季调指数）
CLASSIFICATION = "C"  # Total manufacturing（ISIC 全部制造业）

MONTHLY_PERIOD_RE = re.compile(r"^\d{4} M\d{2}$")
TIMEOUT = 30  # 秒；全史 JSON 单响应 ~20KB/国，超时即跳过该国（不重试、不加频次）


def _get_json(url: str, accept: str):
    """GET 一个 SDMX-JSON 端点（Accept 决定表示；无鉴权、无重试放大）。"""
    req = urllib.request.Request(url, headers={**HEADERS, "Accept": accept})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        return json.loads(resp.read().decode("utf-8", "replace"))


def _structure_names() -> tuple[dict[str, str], dict[str, str]]:
    """从 IIP 结构取 COUNTRY / CLASSIFICATION 代码→名称表；失败返回空表（降级为纯代码）。"""
    try:
        doc = _get_json(STRUCTURE_URL, ACCEPT_STRUCTURE)
        comps = doc["data"]["dataStructures"][0]["dataStructureComponents"]
        names: dict[str, dict[str, str]] = {"COUNTRY": {}, "CLASSIFICATION": {}}
        for dim in comps["dimensionList"]["dimensions"]:
            enum = dim.get("localRepresentation", {}).get("enumeration")
            if dim.get("id") in names and isinstance(enum, list):
                names[dim["id"]] = {
                    str(c["id"]): (c.get("names") or {}).get("en") or ""
                    for c in enum
                    if isinstance(c, dict) and c.get("id") is not None
                }
        return names["COUNTRY"], names["CLASSIFICATION"]
    except Exception:  # noqa: BLE001 - 名称表不可用不阻断取数
        return {}, {}


def fetch_iip(country: str, indicator: str, classification: str) -> dict[str, float]:
    """取一国一分类的 IIP 观测，返回 ``{月度 period: value}``（非月度期次被过滤）。"""
    key = f"{country}.{indicator}.{classification}"
    doc = _get_json(DATA_URL.format(key=key), ACCEPT_DATA)
    data_sets = (doc.get("data") or {}).get("dataSets") or []
    if not data_sets:
        raise ValueError(f"{key}: response has no dataSets")
    out: dict[str, float] = {}
    for ds in data_sets:
        for _skey, series in (ds.get("series") or {}).items():
            for period, cell in (series.get("observations") or {}).items():
                if not MONTHLY_PERIOD_RE.match(str(period)):
                    continue  # 年度/季度期次不是月度源口径
                try:
                    value = cell[0][0]
                except (TypeError, ValueError, IndexError):
                    continue
                if value is None:
                    continue
                out[str(period)] = float(value)
    if not out:
        raise ValueError(f"{key}: no monthly observations returned")
    return out


def run_unido_sdmx(limit: int = 100) -> list[dict]:
    """取种子国的月度 IIP 行（≤ ``limit``）；全部国家失败 → 抛 ``RuntimeError``（失败即红）。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    country_names, class_names = _structure_names()
    rows: list[dict] = []
    errors: list[str] = []
    for country in SEED_COUNTRIES:
        if len(rows) >= limit:
            break
        try:
            observations = fetch_iip(country, INDICATOR, CLASSIFICATION)
        except Exception as exc:  # noqa: BLE001 - 单国失败只跳过并留痕
            errors.append(f"{country}: {type(exc).__name__}: {exc}")
            continue
        url = DATA_URL.format(key=f"{country}.{INDICATOR}.{CLASSIFICATION}")
        for period in sorted(observations):
            rows.append({
                "country": country,
                "country_name": country_names.get(country),
                "indicator": INDICATOR,
                "classification": CLASSIFICATION,
                "classification_name": class_names.get(CLASSIFICATION),
                "period": period,
                "value": observations[period],
                "url": url,
                "source": SOURCE,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            })
            if len(rows) >= limit:
                break
    if not rows:
        raise RuntimeError("unido-sdmx: 全部国家取数失败 — " + "; ".join(errors)[:300])
    return rows[:limit]


if __name__ == "__main__":  # 单页冒烟：python3 spiders/unido-sdmx/spider.py
    print(json.dumps(run_unido_sdmx(limit=3), ensure_ascii=False, indent=2))
