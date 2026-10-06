"""spiders/fao-food-price —— FAO 食品价格指数（月度，总指数+分品类）。

数据表面（官方页内固定 CSV 链接，免鉴权；见工单 brief.notes「拷问 Q1 裁决：官方统计口径非行情」）：

- 官方页： ``GET https://www.fao.org/worldfoodsituation/foodpricesindex/en/``
- CSV 链接（页内 data 链接，历史实测两种形态均 200、字节一致）：
  - 版本戳形态： ``https://www.fao.org/media/docs/worldfoodsituationlibraries/wfs-library/food_price_indices_data.csv?sfvrsn=< stamp>&download=true``
  - 无参形态（固定）： ``https://www.fao.org/media/docs/worldfoodsituationlibraries/wfs-library/food_price_indices_data.csv``
- CSV 布局（实测）：头两行说明（`FAO Food Price Index` / `2014-2016=100`）+ 表头行
  （首格 `Date`）+ 空行 + 数据行（首格 `YYYY-MM`，7 列：总指数+肉/奶/谷/油/糖），
  至 1990-01 起、当月止（2026-10-07 实测至 2026-09，445 行）。

口坑（brief.notes / 实测逐一落实）：

- **版本戳会漂**：`sfvrsn` 是 FAO 附件版本戳，重新上传即变——本单元先抓官方页正则发现现行
  CSV 链接（brief「接前复测现行链接」），发现失败再落固定无参 URL（两者 2026-10-07 实测同内容）。
- **非表格 CSV**：前几行是标题/说明，必须先定位首格为 `Date` 的表头行，再收 `YYYY-MM` 数据行；
  数据开始后再遇非空非数据行即停（防未来追加异构块时串味）。
- **行模型**：宽表转长表——每（月, 指数名）一行：`month` / `index_name` / `value`（工单口径）。
  指数名保留官方英文列名（`Food Price Index`/`Meat`/`Dairy`/`Cereals`/`Oils`/`Sugar`）。
- **名义 vs 实际**：现行官方 CSV 只有名义块；「实际（缩减）指数」在同页 XLSX
  （`food_price_index_nominal_real.xlsx`），非标准库可直接解析的范围，本单元不接、不造数——
  偏差记 README 与工单回执。
- ``limit`` 语义 = 返回行数上限：按（month, index_name）字典序确定性排序后截取。
- 容错：页面与 CSV 两跳，任何一跳失败或 0 数据行 → 抛 ``RuntimeError``（失败即红）。

工单：``reports/health-tickets/20261006-fao-food-price-d98f85a4.yaml``（``kind=generate``）
"""
from __future__ import annotations

import csv
import html
import io
import json
import re
import urllib.request
from datetime import datetime, timezone

SOURCE = "fao-food-price"
PAGE_URL = "https://www.fao.org/worldfoodsituation/foodpricesindex/en/"
FALLBACK_CSV_URL = (
    "https://www.fao.org/media/docs/worldfoodsituationlibraries/wfs-library/"
    "food_price_indices_data.csv"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html, text/csv, */*",
}
TIMEOUT = 60  # 秒；CSV 约 48 KB / 页面 46 KB，给足超时

_CSV_LINK_RE = re.compile(r'href="([^"]*food_price_indices_data\.csv[^"]*)"', re.IGNORECASE)
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def _get(url: str) -> str:
    """GET 一个 https 资源并返回文本；失败抛异常。"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        return resp.read().decode("utf-8", "replace")


def _resolve_csv_url() -> str:
    """从官方页发现现行 CSV 链接（去 HTML 转义）；发现失败落固定无参 URL。"""
    try:
        page = _get(PAGE_URL)
    except Exception:  # noqa: BLE001 - 页面失败不致命，落固定 URL
        return FALLBACK_CSV_URL
    match = _CSV_LINK_RE.search(page)
    if not match:
        return FALLBACK_CSV_URL
    return html.unescape(match.group(1))


def _parse_csv(text: str) -> list[tuple[str, str, float]]:
    """解析非表格 CSV：定位 `Date` 表头行，收 `YYYY-MM` 数据行为（月, 指数名, 值）长表。"""
    out: list[tuple[str, str, float]] = []
    header_seen = False
    for row in csv.reader(io.StringIO(text)):
        if not row or not (row[0] or "").strip():
            continue
        first = row[0].strip()
        if not header_seen:
            if first == "Date":
                header_seen = True
            continue
        if not _MONTH_RE.match(first):
            break  # 数据块结束（防未来追加异构块串味）
        names = [c.strip() for c in row[1:7]]  # 总指数 + 肉/奶/谷/油/糖
        for name, raw in zip(_INDEX_NAMES, names):
            if not name:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue  # 个别空值格跳过，不造数
            out.append((first, name, value))
    return out


_INDEX_NAMES = ("Food Price Index", "Meat", "Dairy", "Cereals", "Oils", "Sugar")


def run_fao_food_price(limit: int = 100) -> list[dict]:
    """取 FAO 食品价格指数月度长表行（总指数+分品类，名义）。

    - ``limit`` = 返回行数上限（按 month/index_name 字典序确定性排序；固定历史月重放恒定）。
    - 全部取数失败 → 抛 ``RuntimeError``（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    csv_url = _resolve_csv_url()
    try:
        text = _get(csv_url)
    except Exception as exc:  # noqa: BLE001 - 单一数据表面，失败即红
        raise RuntimeError(f"fao-food-price: CSV 取数失败（{csv_url}）— {type(exc).__name__}: {exc}") from exc

    parsed = _parse_csv(text)
    if not parsed:
        raise RuntimeError("fao-food-price: CSV 解析后 0 数据行（视为异常，不静默回空）")

    rows = [
        {
            "month": month,
            "index_name": name,
            "value": value,
            "source": SOURCE,
            "url": csv_url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }
        for (month, name, value) in sorted(parsed)
    ][:limit]
    return rows


if __name__ == "__main__":  # 单页冒烟：python3 spiders/fao-food-price/spider.py
    print(json.dumps(run_fao_food_price(limit=6), ensure_ascii=False, indent=2))
