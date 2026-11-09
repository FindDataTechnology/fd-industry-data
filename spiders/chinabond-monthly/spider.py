"""中债登 债券市场统计分析月报（8 张月报表，1997 年起）。

端点（本轮从栏目页 JS 实读打通）：

1. ``POST https://www.chinabond.com.cn/cbiw/GetMonthReport/queryContent``
   参数 ``language=CN & reportFlag=T & sMonth=<MM> & sYear=<YYYY>``
   → JSON 数组，8 条 ``{bbbh: "01"…"08", bbmc: "<表名>", ywbbnr: <base64 PDF>}``；
   **未发布的月份返回 ``[]``**（实测 2026-10 未发、2026-09 已发 8 表）。
2. ``POST https://www.chinabond.com.cn/cbiw/GetMonthReport/GetDataByte``
   参数 ``sBbly=<YYYYMM> & sCode=<bbbh> & sWjlx=3`` → **OOXML xlsx**（PK 魔数）。
   ``sWjlx=3`` 自 2015-09 起启用；本单元只用 xlsx 通道，不碰内联 base64 PDF。

两个实读出来的坑：

- ``reportFlag`` 取 **``T``**（栏目页隐藏表单值），不是 ``0/1/2``——取 0/1/2 一律 ``[]``；
- ``sCode`` 在这里直接用 ``bbbh``（``01``…``08``），**没有**侦察簿路 5 记的
  "``GetDataByte`` 页码与 sCode 差一位" 问题（那个坑属另一个页面）。

表格形态（8 表共用骨架，逐表实读）：

```
row0  表名（"4.债券托管情况"）
row2  统计年月：202609
row3  单位：只，亿元
row4  表头（首列为空，其后为指标列；07/08 为两行表头：交易方式 + 本月/本年累计）
row5+ 合计 / 一、xxx / 1.xxx 数据行
```

xlsx 的 sharedStrings 为空、字符串内联在 sheet XML——**必须走共享层 `tabular`**。
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

from fd_industry_data import tabular

BASE = "https://www.chinabond.com.cn"
COLUMN_URL = BASE + "/zzsj/zzsj_tjsj/tjsj_tjyb/"
QUERY_URL = BASE + "/cbiw/GetMonthReport/queryContent"
BYTE_URL = BASE + "/cbiw/GetMonthReport/GetDataByte"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": COLUMN_URL,
}

#: 表号 → (表名, 行维度)。表号是上游主键，表名仅用于校验（上游改名不改表号）。
TABLES: dict[str, tuple[str, str]] = {
    "01": ("债券业务总览", "indicator"),
    "02": ("债券发行情况", "bond_type"),
    "03": ("债券付息兑付情况", "bond_type"),
    "04": ("债券托管情况", "bond_type"),
    "05": ("债券持有者结构", "investor_type"),
    "06": ("主要券种持有者结构", "investor_type"),
    "07": ("银行间市场债券交易结算情况（按券种）", "bond_type"),
    "08": ("银行间市场债券交易结算情况（按投资者）", "investor_type"),
}

#: 单位解析表（相对 ``单位：`` 行做例外修正）。
#: 01 的单位行是「亿元，个」——两个 token 不是按列对应，而是按**行**对应：
#: 投资者数量 → 个，美元债托管量 → 美元，其余 → 亿元。
#: 04 的单位行是「只，亿元」——按**指标列**对应：托管只数 → 只，托管面额 → 亿元。
TABLE_UNITS: dict[str, tuple[dict[str, str], str]] = {
    "01": ({"数量": "个", "美元": "美元"}, "亿元"),
    "04": ({"只数": "只", "面额": "亿元"}, "亿元"),
}

DEFAULT_UNIT = "亿元"
SLEEP = 0.8
_SKIP_LABEL_PREFIX = ("注", "说明", "数据来源", "资料来源")
_DATA_ROW = re.compile(r"^(合计|一、|[0-9]+[.、])")
_PERIOD_RE = re.compile(r"^(\d{4})-(\d{2})$")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _post(url: str, data: dict[str, str], *, timeout: int = 90) -> bytes:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=HEADERS)
    return urllib.request.urlopen(req, timeout=timeout).read()


def _query(year: int, month: int) -> list[dict]:
    """某年月报目录；未发布返回 ``[]``，响应非 JSON 时抛 ``ValueError``。"""
    raw = _post(QUERY_URL, {
        "language": "CN",
        "reportFlag": "T",
        "sMonth": f"{month:02d}",
        "sYear": str(year),
    })
    text = raw.decode("utf-8", "replace").strip()
    if not text.startswith("["):
        raise ValueError(f"queryContent 非 JSON（{text[:80]!r}）")
    return json.loads(text)


def _catalog(year: int, month: int) -> dict[str, str]:
    """枚举校验后的 {表号: 表名}；未知表号留痕丢弃，已知表号校验表名。"""
    records = _query(year, month)
    if not records:
        return {}
    catalog: dict[str, str] = {}
    for rec in records:
        code = str(rec.get("bbbh") or "").strip()
        name = str(rec.get("bbmc") or "").strip()
        if code not in TABLES:
            print(f"[chinabond-monthly] {year}-{month:02d} 未知表号 {code!r}（{name}）已跳过", flush=True)
            continue
        expected = TABLES[code][0]
        if name != expected:
            print(
                f"[chinabond-monthly] {year}-{month:02d} 表号 {code} 表名漂移："
                f"上游 {name!r} / 台账 {expected!r}（以表号为准，表名取上游）",
                flush=True,
            )
        catalog[code] = name or expected
    missing = [c for c in TABLES if c not in catalog]
    if missing:
        print(f"[chinabond-monthly] {year}-{month:02d} 缺表 {missing}", flush=True)
    return catalog


def _num(cell) -> float | None:
    text = str(cell if cell is not None else "").strip().replace(",", "").replace("\u00a0", "")
    if not text or text in {"-", "--", "—", "---", "…"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _unit(code: str, metric: str, row_label: str) -> str:
    overrides, default = TABLE_UNITS.get(code, ({}, DEFAULT_UNIT))
    for needle, unit in overrides.items():
        if needle in row_label or needle in metric:
            return unit
    return default


def _header_columns(grid: list[list]) -> tuple[int, list[str]]:
    """返回 (数据起始行下标, 每列标签)。

    表头 = ``单位：`` 行与首个数据行之间。数据行判定：首列为 ``合计`` /
    ``一、`` / ``N.`` 开头——01 表（债券业务总览）没有 ``合计`` 行，
    首行直接是 ``1.债券发行量``，故不能只认 ``合计``。
    """
    total_idx = None
    for idx, row in enumerate(grid):
        if idx < 3:
            continue
        first = str(row[0] if row else "").strip()
        if first and _DATA_ROW.match(first):
            total_idx = idx
            break
    if total_idx is None:
        raise ValueError("未找到数据起始行")
    width = max((len(r) for r in grid[:total_idx]), default=0)
    header_rows = [
        [str(c if c is not None else "").strip() for c in row] + [""] * (width - len(row))
        for row in grid[:total_idx]
        if any(str(c if c is not None else "").strip() for c in row[1:])
    ]
    if not header_rows:
        raise ValueError("未找到表头行")
    group_row = header_rows[-2] if len(header_rows) >= 2 else [""] * width
    label_row = header_rows[-1]
    labels: list[str] = []
    carry = ""
    for j in range(width):
        group = group_row[j] if j < len(group_row) else ""
        if group:
            carry = group
        elif j > 0:
            group = carry
        sub = label_row[j] if j < len(label_row) else ""
        if not sub and not group:
            labels.append("")
        elif not sub or sub == group:
            labels.append(group)
        elif not group:
            labels.append(sub)
        else:
            labels.append(f"{group}·{sub}")
    return total_idx, labels


def _period_of(grid: list[list], fallback: str) -> str:
    for row in grid[:4]:
        for cell in row:
            text = str(cell if cell is not None else "").strip()
            if text.startswith("统计年月"):
                stamp = text.split("：")[-1].split(":")[-1].strip()
                digits = "".join(ch for ch in stamp if ch.isdigit())
                if len(digits) >= 6:
                    return f"{digits[:4]}-{digits[4:6]}"
    return fallback


def _table_rows(code: str, payload: bytes, period: str, stamp: str) -> list[dict]:
    grid = tabular.read_grid(payload)
    start, labels = _header_columns(grid)
    table_name, dimension = TABLES[code]
    rows: list[dict] = []
    for raw in grid[start:]:
        label = str(raw[0] if raw else "").strip()
        if not label or label.startswith(_SKIP_LABEL_PREFIX):
            continue
        for j, metric in enumerate(labels):
            if j == 0 or not metric:
                continue
            value = _num(raw[j] if j < len(raw) else None)
            if value is None:
                continue
            rows.append(
                {
                    "period": period,
                    "table_code": code,
                    "table_name": table_name,
                    "row_dimension": dimension,
                    "row_label": label,
                    "metric": metric,
                    "value": value,
                    "unit": _unit(code, metric, label),
                    "source": "chinabond-monthly",
                    "scraped_at": stamp,
                }
            )
    return rows


def _previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def run_chinabond_monthly(limit: int = 2000, months: int = 1, period: str = "") -> list[dict]:
    """中债登月报长表（每（期, 表, 行, 指标）一行），金额单位亿元。

    Args:
        limit: 返回行数上限（按期倒序后按表号顺序截断）。
        months: 从起点期往回取几期已发布月报（``1`` = 只取一期）。
        period: 起点期 ``YYYY-MM``；空 = 从上一个自然月起探测最新已发布期
            （调度口径）。给定时从该期起回溯，用于回补 1997 年以来的历史月份
            与 golden 定月复算。

    Raises:
        RuntimeError: 探测窗内没有任何已发布月报。
        ValueError: ``period`` 不是 ``YYYY-MM``。
    """
    if limit <= 0 or months <= 0:
        return []
    stamp = _now()
    if period:
        matched = _PERIOD_RE.match(period.strip())
        if not matched:
            raise ValueError(f"period 须为 YYYY-MM，收到 {period!r}")
        year, month = int(matched.group(1)), int(matched.group(2))
        if not 1 <= month <= 12:
            raise ValueError(f"period 月份越界：{period!r}")
    else:
        today = date.today()
        year, month = _previous_month(today.year, today.month)
    stamp = _now()
    periods: list[tuple[int, int, dict[str, str]]] = []
    attempts = 0
    max_attempts = int(months) + 4
    while len(periods) < int(months) and attempts < max_attempts:
        attempts += 1
        if year < 1997:
            break
        try:
            catalog = _catalog(year, month)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            print(f"[chinabond-monthly] {year}-{month:02d} 目录失败：{exc}", flush=True)
            catalog = {}
        if catalog:
            periods.append((year, month, catalog))
        else:
            print(f"[chinabond-monthly] {year}-{month:02d} 未发布/空", flush=True)
        year, month = _previous_month(year, month)
    if not periods:
        raise RuntimeError("chinabond-monthly: 探测窗内无已发布月报")

    rows: list[dict] = []
    for year, month, catalog in periods:
        fallback = f"{year:04d}-{month:02d}"
        sbbly = f"{year:04d}{month:02d}"
        for code in TABLES:
            if code not in catalog:
                continue
            try:
                payload = _post(BYTE_URL, {"sBbly": sbbly, "sCode": code, "sWjlx": "3"})
            except (urllib.error.URLError, TimeoutError) as exc:
                print(f"[chinabond-monthly] {sbbly} 表{code} 下载失败：{exc}", flush=True)
                continue
            try:
                table_rows = _table_rows(code, payload, fallback, stamp)
            except (ValueError, tabular.TabularError) as exc:
                print(f"[chinabond-monthly] {sbbly} 表{code} 解析失败：{exc}", flush=True)
                continue
            rows.extend(table_rows)
            print(f"[chinabond-monthly] {sbbly} 表{code} {catalog[code]}：{len(table_rows)} 行", flush=True)
            time.sleep(SLEEP)
    if not rows:
        raise RuntimeError("chinabond-monthly: 全部表为空")
    return rows[: int(limit)]
