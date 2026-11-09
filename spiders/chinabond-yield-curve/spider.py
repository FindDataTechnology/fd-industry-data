"""中债登 商业银行资本工具收益率曲线（二级资本债 / 总损失吸收能力非资本债）。

端点（本轮实测打通）：

``GET https://yield.chinabond.com.cn/cbweb-mn/yc/downYearBzqx?year=<YYYY>&ycDefId=<曲线ID>``

- 返回**整年包 xlsx**（`application/x-msdownload`，实为 OOXML），四列：
  ``日期 | 标准期限说明 | 标准期限(年) | 收益率(%)``；
- 曲线 ID 来自 ``GET /cbweb-mn/yc/queryTree?locale=zh_CN``（180 个节点，实读码表）；
- 必须带 ``Referer: https://yield.chinabond.com.cn/``（无 Referer 实测被拒）；
- xlsx 的 sharedStrings 为空、字符串内联在 sheet XML——**必须走共享层 `tabular`**
  （侦察簿路 5 已记该形态；内联解析会被 conformance gate 拦）。

侦察簿路 5 另有两条本单元绕开的坑：``GetDataByte`` 的 sCode 与页面序号差一位、
``searchYc`` 老端点已死——本单元只用 ``downYearBzqx`` 整年包，不碰二者。
"""
from __future__ import annotations

import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

from fd_industry_data import tabular

BASE = "https://yield.chinabond.com.cn"
TREE_URL = BASE + "/cbweb-mn/yc/queryTree?locale=zh_CN"
YEAR_URL = BASE + "/cbweb-mn/yc/downYearBzqx"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": BASE + "/",
}

#: 资本工具曲线（queryTree 实读 ID；名称按上游原始写法保留各自评级括号）。
CURVES = (
    ("8a8b2cee67a9cd3a0167ac48fc234850", "二级资本债(AAA-)"),
    ("8a8b2cee67a9cd3a0167ac533bc03f4f", "二级资本债(AA＋)"),
    ("8a8b2cee67a9cd3a0167ac53aa463f51", "二级资本债(AA)"),
    ("8a8b2cee67a9cd3a0167ac5407c63f53", "二级资本债(AA-)"),
    ("8a8b2cee67a9cd3a0167ac54671c3f55", "二级资本债(A＋)"),
    ("8a8b2cee67a9cd3a0167ac54dac83f57", "二级资本债(A)"),
    ("998183fe91c707050193099291ec33b3", "总损失吸收能力非资本债(AAA-)"),
)
# 不做请求间隔：整年包只 7 个请求（每曲线 1 个），实测连打 ~6s 稳定；
# 曾有的 SLEEP 常量从未被使用，留着只会让读者以为有节流。


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _fetch(url: str, *, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    return urllib.request.urlopen(req, timeout=timeout).read()


def _norm_date(cell: str) -> str:
    """``2026/01/04`` / ``2026-01-04`` → ``2026-01-04``。"""
    text = (cell or "").strip().replace("/", "-")
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return ""


def _curve_rows(curve_id: str, curve_name: str, year: int) -> list[dict]:
    payload = _fetch(f"{YEAR_URL}?year={year}&ycDefId={curve_id}")
    grid = tabular.read_grid(payload)
    header_idx = None
    for idx, row in enumerate(grid[:5]):
        cells = [str(c or "").strip() for c in row]
        if "日期" in cells and any("收益率" in c for c in cells):
            header_idx = idx
            break
    if header_idx is None:
        raise ValueError(f"header row not found for curve {curve_name}")
    header = [str(c or "").strip() for c in grid[header_idx]]
    col_date = header.index("日期")
    col_label = next((i for i, c in enumerate(header) if "标准期限说明" in c), 1)
    col_years = next((i for i, c in enumerate(header) if "标准期限" in c and "年" in c), 2)
    col_yield = next((i for i, c in enumerate(header) if "收益率" in c), 3)
    stamp = _now()
    rows: list[dict] = []
    for raw in grid[header_idx + 1:]:
        if len(raw) <= col_yield:
            continue
        day = _norm_date(str(raw[col_date] or ""))
        if not day:
            continue
        try:
            tenor_years = float(str(raw[col_years]).strip())
            value = float(str(raw[col_yield]).strip())
        except ValueError:
            continue
        rows.append(
            {
                "date": day,
                "curve_id": curve_id,
                "curve_name": curve_name,
                "tenor_label": str(raw[col_label] or "").strip(),
                "tenor_years": tenor_years,
                "yield_pct": value,
                "unit": "%",
                "source": "chinabond-yield-curve",
                "scraped_at": stamp,
            }
        )
    return rows


def run_chinabond_yield_curve(limit: int = 1200, days: int = 0) -> list[dict]:
    """中债资本工具收益率曲线点（每（曲线, 日期, 期限）一行），单位 %。

    Args:
        limit: 返回行数上限（按日期倒序截断）。
        days: 只返回最近 ``days`` 个自然日内的点；``0`` = 整年包全量（回补口径）。

    Raises:
        RuntimeError: 所有曲线都取不到数据。
    """
    if limit <= 0:
        return []
    wanted = int(limit)
    today = date.today()
    year = today.year
    cutoff = (today - timedelta(days=int(days))).isoformat() if int(days) > 0 else ""
    rows: list[dict] = []
    for curve_id, curve_name in CURVES:
        try:
            curve_rows = _curve_rows(curve_id, curve_name, year)
        except (urllib.error.URLError, TimeoutError, ValueError, tabular.TabularError) as exc:
            print(f"[chinabond-yield-curve] {curve_name} failed: {exc}", flush=True)
            continue
        if cutoff:
            curve_rows = [r for r in curve_rows if r["date"] >= cutoff]
        rows.extend(curve_rows)
        print(f"[chinabond-yield-curve] {curve_name} {year}: {len(curve_rows)} points", flush=True)
    if not rows:
        raise RuntimeError("chinabond-yield-curve: 全部曲线为空")
    rows.sort(key=lambda r: (r["date"], r["curve_name"], r["tenor_years"]), reverse=True)
    return rows[:wanted]
