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
- **名义 vs 实际（2026-10-13 补齐）**：月度 CSV 只有名义块；「实际（缩减）指数」在同页
  XLSX（``food_price_index_nominal_real.xlsx``，sheet ``FFPI_Historical``），且该表是
  **年度**序列（1961–2026，列 = Month/Nominal/Real）。此前判为「非标准库可直接解析的
  范围」而不接——平台共享层 ``fd_industry_data.tabular`` 上线后该障碍消除，故本单元
  新增 ``scope="annual"`` 路径（见下）。**月度路径行为不变**（默认 ``scope="monthly"``）。
- ``limit`` 语义 = 返回行数上限：按（month/index_name）字典序确定性排序后截取。
  年度路径按（year, index_name）字典序，同样确定性。
- 容错：页面与 CSV/XLSX 两跳，任何一跳失败或 0 数据行 → 抛 ``RuntimeError``（失败即红）。
  年度路径失败**不影响**月度路径（月度是主数据面）。

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

from fd_industry_data import tabular

SOURCE = "fao-food-price"
PAGE_URL = "https://www.fao.org/worldfoodsituation/foodpricesindex/en/"
FALLBACK_CSV_URL = (
    "https://www.fao.org/media/docs/worldfoodsituationlibraries/wfs-library/"
    "food_price_indices_data.csv"
)
FALLBACK_XLSX_URL = (
    "https://www.fao.org/media/docs/worldfoodsituationlibraries/wfs-library/"
    "food_price_index_nominal_real.xlsx"
)
# 年度表所在 sheet（实测唯一表，含 1961–2026 年度 名义/实际 两列）
ANNUAL_SHEET = "FFPI_Historical"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html, text/csv, */*",
}
TIMEOUT = 60  # 秒；CSV 约 48 KB / 页面 46 KB / XLSX 约 25 KB，给足超时

_CSV_LINK_RE = re.compile(r'href="([^"]*food_price_indices_data\.csv[^"]*)"', re.IGNORECASE)
_XLSX_LINK_RE = re.compile(
    r'href="([^"]*food_price_index_nominal_real\.xlsx[^"]*)"', re.IGNORECASE
)
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_YEAR_RE = re.compile(r"^\d{4}$")


def _get(url: str) -> str:
    """GET 一个 https 资源并返回文本；失败抛异常。"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        return resp.read().decode("utf-8", "replace")


def _get_bytes(url: str) -> bytes:
    """GET 一个 https 资源并返回原始字节（XLSX 用；文本路径走 ``_get``）。"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        return resp.read()


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


def _resolve_xlsx_url() -> str:
    """从官方页发现现行年度 XLSX 链接；发现失败落固定无参 URL（实测两者同内容）。"""
    try:
        page = _get(PAGE_URL)
    except Exception:  # noqa: BLE001 - 页面失败不致命，落固定 URL
        return FALLBACK_XLSX_URL
    match = _XLSX_LINK_RE.search(page)
    if not match:
        return FALLBACK_XLSX_URL
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

# 年度表（FFPI_Historical）的列 → 指数名。该表只有总指数（无分品类），
# 名义/实际两列各自成一个序列，故 index_name 需带后缀以区分。
_ANNUAL_COLUMNS = (("Nominal", "Food Price Index (nominal)"),
                   ("Real", "Food Price Index (real)"))


def _parse_annual_xlsx(data: bytes) -> list[tuple[str, str, float]]:
    """解析年度 XLSX（sheet ``FFPI_Historical``）→ （年, 指数名, 值）长表。

    实测布局（2026-10-13）：前 3 行为标题/基期/表头（``Month`` / ``Nominal`` /
    ``Real``），第 4 行空，其后数据行首格为**年份**（1961…2026）。注意首格虽
    标作 ``Month``，实际是**年度**——按 4 位数字年份校验，不按表头文字。
    """
    grid = tabular.read_grid(data, sheet=ANNUAL_SHEET)
    if not grid:
        return []
    # 定位表头行（含 Nominal 与 Real 两列名）以取列号，不写死列序
    header_idx = None
    col_of: dict[str, int] = {}
    for i, row in enumerate(grid[:10]):
        cells = [str(c or "").strip() for c in row]
        if "Nominal" in cells and "Real" in cells:
            header_idx = i
            col_of = {name: cells.index(name) for name in ("Nominal", "Real")}
            break
    if header_idx is None:
        raise RuntimeError(
            f"fao-food-price: 年度表未找到含 Nominal/Real 的表头行（sheet={ANNUAL_SHEET}）"
        )

    out: list[tuple[str, str, float]] = []
    for row in grid[header_idx + 1:]:
        if not row:
            continue
        year = str(row[0] or "").strip()
        if not _YEAR_RE.match(year):
            continue  # 空行/注记行
        for col, label in _ANNUAL_COLUMNS:
            idx = col_of[col]
            raw = str(row[idx] if idx < len(row) else "").strip()
            if not raw:
                continue  # 该年该列缺值：跳过不造数
            try:
                value = float(raw)
            except ValueError:
                continue
            out.append((year, label, value))
    return out


def _collect_monthly(limit: int) -> list[dict]:
    """月度路径（原有行为，不变）：CSV → 长表。失败抛 ``RuntimeError``。"""
    csv_url = _resolve_csv_url()
    try:
        text = _get(csv_url)
    except Exception as exc:  # noqa: BLE001 - 单一数据表面，失败即红
        raise RuntimeError(f"fao-food-price: CSV 取数失败（{csv_url}）— {type(exc).__name__}: {exc}") from exc

    parsed = _parse_csv(text)
    if not parsed:
        raise RuntimeError("fao-food-price: CSV 解析后 0 数据行（视为异常，不静默回空）")

    return [
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


def _collect_annual(limit: int) -> list[dict]:
    """年度路径（2026-10-13 新增）：同页 XLSX → 1961+ 年度 名义/实际 长表。

    经平台共享层 ``fd_industry_data.tabular`` 解析（该层上线前此项被记为
    「超出本单元标准库直解析范围」而搁置）。失败抛 ``RuntimeError``。
    """
    xlsx_url = _resolve_xlsx_url()
    try:
        data = _get_bytes(xlsx_url)
    except Exception as exc:  # noqa: BLE001 - 单一数据表面，失败即红
        raise RuntimeError(
            f"fao-food-price: 年度 XLSX 取数失败（{xlsx_url}）— {type(exc).__name__}: {exc}"
        ) from exc

    fmt = tabular.detect(data)
    if fmt is not tabular.Format.XLSX:
        raise RuntimeError(
            f"fao-food-price: 年度附件格式为 {fmt.value}，预期 xlsx（{xlsx_url}）"
        )

    parsed = _parse_annual_xlsx(data)
    if not parsed:
        raise RuntimeError("fao-food-price: 年度表解析后 0 数据行（视为异常，不静默回空）")

    return [
        {
            "month": year,  # 年度序列：复用 month 列承载期间键（YYYY），见 manifest 说明
            "index_name": name,
            "value": value,
            "source": SOURCE,
            "url": xlsx_url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }
        for (year, name, value) in sorted(parsed)
    ][:limit]


def run_fao_food_price(limit: int = 100, scope: str = "monthly") -> list[dict]:
    """取 FAO 食品价格指数长表行。

    Args:
        limit: 返回行数上限（按 ``month``/``index_name`` 字典序确定性排序；
            固定历史月重放恒定）。
        scope: ``monthly``（缺省，行为不变）= 月度 CSV，总指数 + 肉/奶/谷/油/糖
            名义值；``annual`` = 同页 XLSX 的年度序列（1961+，总指数 名义/实际
            两列）；``all`` = 两者合并。

    Returns:
        list of long-format rows。年度行的 ``month`` 承载 4 位年份（YYYY），
        其 ``index_name`` 带 ``(nominal)``/``(real)`` 后缀以与月度序列区分。

    Raises:
        RuntimeError: 所请求的任一数据面取数或解析失败（失败即红，不静默回空）。
        ValueError: ``limit`` 或 ``scope`` 非法。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []
    sc = (scope or "monthly").strip().lower()
    if sc not in ("monthly", "annual", "all"):
        raise ValueError(f"unknown scope {scope!r}; valid: monthly / annual / all")

    rows: list[dict] = []
    if sc in ("monthly", "all"):
        rows.extend(_collect_monthly(limit))
    if sc in ("annual", "all"):
        rows.extend(_collect_annual(limit))
    if sc == "all":
        # 两段各自已截断到 limit，合并后按期间键排序再截断，保证确定性
        rows.sort(key=lambda r: (r["month"], r["index_name"]))
        rows = rows[:limit]
    return rows


if __name__ == "__main__":  # 单页冒烟：python3 spiders/fao-food-price/spider.py
    print(json.dumps(run_fao_food_price(limit=6), ensure_ascii=False, indent=2))
