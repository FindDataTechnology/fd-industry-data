"""spiders/wfp-food-price —— WFP 全球市场粮价（HDX 托管，按年分片 CSV）。

**2026-11-08 迁移**：旧数据集 ``wfp-food-prices`` 已于 2021-08 冻结（HDX 现行资源
返回 202 + 空体，零语义入库），本单元改接**活数据集** ``global-wfp-food-prices``。

数据表面（实测 2026-11-08，公开免鉴权，仅 https）：

- 元信息： ``GET https://data.humdata.org/api/3/action/package_show?id=global-wfp-food-prices``
  → CKAN JSON。``last_modified = 2026-11-05``；``resources`` **40 个，一年一个 CSV**
  （2026: 42MB / 2025: 60MB / 2024: 61MB …），命名 ``Global WFP food prices <year>``。
- 资源本体： ``…/resource/<rid>/download/wfp_food_prices_global_<year>.csv``
  （HDX 先 302 到 S3 预签名地址，``urlopen`` 自动跟随）。
- 列（比旧版全换，**不是改名**）：

  ``countryiso3, date, admin1, admin2, market, market_id, latitude, longitude,
  category, commodity, commodity_id, unit, priceflag, pricetype, currency,
  price, usdprice``

  实测行样例：``AFG,2026-05-15,Badakhshan,Argo,Badakhshan,3690,37.04,70.46,
  cereals and tubers,Bread,55,KG,actual,Retail,AFN,77,1.2``

口坑（全部按实测落实）：

- **按年分片**：不能取单个全库 CSV。默认只下**当年 + 上一年**两个分片
  （``years`` 可显式覆盖），避免拉 40 年 ≈ 620MB。
- **必须流式解析**（``urlopen`` 逐行喂 ``csv.reader``），取满 ``limit`` 即断流。
- ``date`` 是**观测日**（多为当月 15 日），``month`` 归一为 ``YYYY-MM``。
- ``category`` 含 ``non-food``（Exchange rate / Fuel / Wage），默认**剔除**
  （``include_non_food=True`` 才保留）——本单元概念是粮价，不是综合物价。
- 容错：任一环节整体失败 → 抛 ``RuntimeError``（失败即红，不静默返空）；
  流中脏行（列数不足 / 日期非 ISO / 价格非数）跳过并计数。
- 只用标准库（urllib/json/csv/datetime）；带常规浏览器 UA，不做指纹伪装与频次对抗。
"""
from __future__ import annotations

import csv
import io
import json
import re
import urllib.request
from datetime import datetime, timezone

SOURCE = "wfp-food-price"
DATASET_PAGE = "https://data.humdata.org/dataset/global-wfp-food-prices"
PACKAGE_API = (
    "https://data.humdata.org/api/3/action/"
    "package_show?id=global-wfp-food-prices"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}
TIMEOUT = 90  # 秒；流式读取，单块超时即整体失败（不重试放大）

_YEAR_RE = re.compile(r"(19|20)\d{2}")
_REQUIRED_COLS = ("countryiso3", "date", "commodity", "price")

logger = None
try:  # 标准库 logging，保持与其它单元一致的日志口径
    import logging as _logging

    logger = _logging.getLogger(__name__)
except Exception:  # pragma: no cover
    pass


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def _http_get(url: str, timeout: int = TIMEOUT):
    req = urllib.request.Request(url, headers=HEADERS)
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (固定 https 主机)


def _resolve_year_resources() -> dict[int, str]:
    """CKAN → {年份: 下载 URL}；年份取自资源名（实测 ``Global WFP food prices 2026``）。"""
    with _http_get(PACKAGE_API, timeout=45) as resp:
        payload = json.loads(resp.read().decode("utf-8", "replace"))
    if not isinstance(payload, dict) or not payload.get("success"):
        raise RuntimeError(
            f"wfp-food-price: package_show success={payload.get('success')!r} "
            f"({PACKAGE_API})"
        )
    out: dict[int, str] = {}
    for res in (payload.get("result") or {}).get("resources") or []:
        if str(res.get("format", "")).upper() != "CSV" or not res.get("url"):
            continue
        name = str(res.get("name") or "")
        m = _YEAR_RE.search(name)
        if not m:
            continue
        out[int(m.group(0))] = str(res["url"])
    if not out:
        raise RuntimeError(
            "wfp-food-price: package_show 未解析出任何按年 CSV 资源"
            f"（dataset=global-wfp-food-prices）")
    return out


# --------------------------------------------------------------------------
# 归一
# --------------------------------------------------------------------------
def _month(date: str) -> str | None:
    """``2026-05-15`` → ``2026-05``；非 ISO 日期 → None（脏行跳过）。"""
    date = (date or "").strip()
    if len(date) < 7 or date[4] != "-":
        return None
    try:
        y, m = int(date[:4]), int(date[5:7])
    except ValueError:
        return None
    if not (1900 <= y <= 2100 and 1 <= m <= 12):
        return None
    return f"{y:04d}-{m:02d}"


def _num(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f


def _window(v: str | None) -> tuple[str, str, str] | None:
    """窗口归一成 ``(粒度键, 下界, 上界)``。

    ``YYYY-MM`` → 整月（``("2026-10", "2026-10-00", "2026-10-99")``）；
    ``YYYY-MM-DD`` → 当日（三者都是该日）。
    """
    if v is None:
        return None
    v = str(v).strip()
    if len(v) == 7:
        return (v, v + "-00", v + "-99")
    if len(v) >= 10:
        return (v[:10], v[:10], v[:10])
    raise ValueError(
        f"wfp-food-price: window 须为 YYYY-MM 或 YYYY-MM-DD，got {v!r}")


def _pick_years(years) -> list[int]:
    """``years`` 归一为升序列表；缺省 = 当年 + 上一年（UTC）。"""
    if years is None:
        cur = datetime.now(timezone.utc).year
        return [cur - 1, cur]
    if isinstance(years, (int, str)):
        years = [years]
    out: set[int] = set()
    for y in years:
        try:
            out.add(int(y))
        except (TypeError, ValueError):
            raise ValueError(
                f"wfp-food-price: years 含非整数值 {y!r}") from None
    return sorted(out)


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def run_wfp_food_price(
    limit: int = 2000,
    window_start: str | None = None,
    window_end: str | None = None,
    years=None,
    include_non_food: bool = False,
) -> list[dict]:
    """取 ``limit`` 行市场级粮价月度行。

    ``years`` 缺省只下**当年 + 上一年**分片；``window_start``/``window_end``
    （``YYYY-MM`` 或 ``YYYY-MM-DD``）按**观测日**过滤、先过滤后截断；
    ``include_non_food=True`` 保留 ``category == "non-food"`` 的行（汇率/燃油/工资）。
    整体失败抛 ``RuntimeError``（失败即红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    lo = _window(window_start)
    hi = _window(window_end)
    if (lo is None) != (hi is None):
        raise ValueError(
            f"wfp-food-price: window_start/window_end 须成对出现，"
            f"got start={window_start!r} end={window_end!r}")
    if lo and hi and lo[1] > hi[1]:
        raise ValueError(
            f"wfp-food-price: window_start {window_start} 晚于 window_end {window_end}")

    by_year = _resolve_year_resources()
    wanted = [y for y in _pick_years(years) if y in by_year]
    if not wanted:
        raise RuntimeError(
            f"wfp-food-price: 请求年份不在数据集分片内 "
            f"({sorted(by_year)})")

    rows: list[dict] = []
    skipped = 0
    scraped_at = datetime.now(timezone.utc).isoformat()
    used_urls: list[str] = []

    for year in wanted:
        url = by_year[year]
        used_urls.append(url)
        with _http_get(url) as resp:
            stream = io.TextIOWrapper(
                resp, encoding="utf-8-sig", errors="replace", newline="")
            reader = csv.reader(stream)
            header = next(reader, None) or []
            missing = [c for c in _REQUIRED_COLS if c not in header]
            if missing:
                raise RuntimeError(
                    f"wfp-food-price: 分片 {year} 表头缺列 {missing}；"
                    f"实际表头前 8 列 = {header[:8]}")
            col = {name: i for i, name in enumerate(header)}
            n = len(header)
            for rec in reader:
                if len(rec) < n:
                    skipped += 1
                    continue
                date = (rec[col["date"]] or "").strip()
                month = _month(date)
                price = _num(rec[col["price"]])
                if month is None or price is None:
                    skipped += 1
                    continue
                category = (rec[col["category"]] or "").strip() if "category" in col else ""
                if not include_non_food and category == "non-food":
                    continue
                if lo and date[:10] < lo[1]:
                    continue
                if hi and date[:10] > hi[2]:
                    continue
                rows.append({
                    "country_iso3": (rec[col["countryiso3"]] or "").strip() or None,
                    "month": month,
                    "date": date[:10] or None,
                    "category": category or None,
                    "commodity": (rec[col["commodity"]] or "").strip() or None,
                    "market": (rec[col["market"]] or "").strip() if "market" in col else None,
                    "admin1": (rec[col["admin1"]] or "").strip() if "admin1" in col else None,
                    "price_type": (rec[col["pricetype"]] or "").strip() if "pricetype" in col else None,
                    "price_flag": (rec[col["priceflag"]] or "").strip() if "priceflag" in col else None,
                    "price": price,
                    "usd_price": _num(rec[col["usdprice"]]) if "usdprice" in col else None,
                    "currency": (rec[col["currency"]] or "").strip() if "currency" in col else None,
                    "unit": (rec[col["unit"]] or "").strip() if "unit" in col else None,
                    "url": url,
                    "source": SOURCE,
                    "scraped_at": scraped_at,
                })
                if len(rows) >= limit:
                    break
        if len(rows) >= limit:
            break

    if not rows:
        raise RuntimeError(
            f"wfp-food-price: 分片 {wanted} 解析 0 行 "
            f"(window={window_start}..{window_end}, skipped={skipped}, urls={used_urls})")

    if logger is not None:
        logger.info(
            "wfp-food-price: %d row(s) from year shard(s) %s; skipped=%d",
            len(rows), wanted, skipped)
    return rows[:limit]


if __name__ == "__main__":  # 冒烟：python3 spiders/wfp-food-price/spider.py
    print(json.dumps(run_wfp_food_price(limit=3), ensure_ascii=False, indent=2))
