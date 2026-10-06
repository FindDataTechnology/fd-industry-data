"""spiders/pboc-monetary —— 中国人民银行 社融与货币统计月度取数。

数据表面（公开静态页 + xlsx 直链，免鉴权；必须带浏览器 UA，见工单 brief.notes）：

- 统计数据栏目： ``GET https://www.pbc.gov.cn/diaochatongjisi/116219/116319/index.html``
  左侧树含各「YYYY年统计数据」年份链接及其下「社会融资规模」「货币统计概览」子栏
  （工单 brief 的 2168/2639 旧路径已 404 下线，2026-10-07 复测现行路径为此）。
- 子栏页内表格块（``class="a2015"``）标题直链 xlsx：
  ``/diaochatongjisi/attachDir/YYYY/MM/<id>.xlsx``（URL 规律可预测，逐表解析获取）。
  - 社会融资规模子栏： ``社会融资规模增量统计表``（月度流量，亿元）、
    ``社会融资规模存量统计表``（月度存量，万亿元，转置表）。
  - 货币统计概览子栏： ``货币供应量``（M0/M1/M2 月末余额，亿元）。
- xlsx = zip + xml：仅标准库 ``zipfile`` + ``xml.etree`` 解析（brief.notes 指定口径）。

口坑（侦察实测，逐一落实）：

- **月份单元格是数字**，10/11/12 月被浮点序列化成 ``2025.1``/``2025.11``（吞尾零），
  存量表模板甚至把 1 月打成 ``2025.1``——**不能按字符串解析月份**。统一用「序列定位」：
  同一表内月格按出现序对应 1..12 月，校验年份一致、格数 ≤12、两位小数月份与序号一致，
  任一不满足即弃表留痕。
- 流量表未公布月份的行存在但值为空 → 跳过该月；公布后的历史月值冻结不变。
- 取**最近两个年度**的年份栏目（当年 + 上年）：当年文件逐月增长，上年文件年终冻结，
  历史深度约 24 个月。
- 容错：单年份/单表失败只跳过并留痕；**全部表失败抛 ``RuntimeError``**（失败即红）。
- 只带常规浏览器 UA，不做指纹伪装与频次对抗；只用标准库（urllib/zipfile/xml/json）。

工单：``reports/health-tickets/20261006-pboc-monetary-ad2a8dc3.yaml``（``kind=generate``）
"""
from __future__ import annotations

import json
import re
import urllib.request
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from xml.etree import ElementTree as ET

SOURCE = "pboc-monetary"
BASE = "https://www.pbc.gov.cn"
LANDING_URL = BASE + "/diaochatongjisi/116219/116319/index.html"
YEAR_SECTION_RE = re.compile(
    r"<a\s[^>]*href=[\"']([^\"']+)[\"'][^>]*>\s*(\d{4})年统计数据\s*</a>")
SECTION_LINK_TMPL = (
    r"<a\s[^>]*href=[\"']([^\"']+)[\"'][^>]*>\s*{}\s*</a>")
TABLE_BLOCK_RE = re.compile(r'class="a2015"(.*?)</table>', re.S)
TABLE_TITLE_RE = re.compile(r'class="titp20">(.*?)</div>', re.S)
MONTH_RE = re.compile(r"^(\d{4})\.(\d{1,2})$")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
TIMEOUT = 30  # 秒；静态页/小 xlsx（约 13KB），超时即跳过该表（不重试放大）
YEAR_WINDOW = 2  # 取最近两个年度栏目（当年 + 上年）

_SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _get(url: str, timeout: int = TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (固定 https 主机)
        return resp.read()


def _get_html(url: str) -> str:
    return _get(url).decode("utf-8", "replace")


def _absolute(href: str, page_url: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return BASE + (href if href.startswith("/") else "/" + href)


# ---------------------------------------------------------------- xlsx 解析


def _col_index(cell_ref: str) -> int:
    """``D6`` → 3（0 基列号；处理 Excel 省略空单元格时的列对齐）。"""
    letters = re.match(r"([A-Z]+)", cell_ref or "")
    if not letters:
        return -1
    n = 0
    for ch in letters.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_sheet(data: bytes) -> list[list[str]]:
    """xlsx 字节 → 二维字符串表（``t="s"`` 共享串还原；数字/文本原样）。"""
    with zipfile.ZipFile(BytesIO(data)) as z:
        names = z.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.iter(_SHEET_NS + "si"):
                shared.append("".join(t.text or "" for t in si.iter(_SHEET_NS + "t")))
        sheet_name = sorted(n for n in names
                            if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))[0]
        root = ET.fromstring(z.read(sheet_name))
    grid: list[list[str]] = []
    for row in root.iter(_SHEET_NS + "row"):
        cells: list[str] = []
        for c in row.iter(_SHEET_NS + "c"):
            idx = _col_index(c.get("r") or "")
            v = c.find(_SHEET_NS + "v")
            if v is None:
                continue
            text = shared[int(v.text)] if c.get("t") == "s" else (v.text or "")
            while len(cells) <= idx:
                cells.append("")
            cells[idx] = text
        grid.append(cells)
    return grid


def _assign_months(raws: list[str]) -> list[str]:
    """月格文本序列 → ``YYYY-MM`` 列表（序列定位法，见模块 docstring 口坑 1）。"""
    years: set[int] = set()
    out: list[str] = []
    for k, raw in enumerate(raws):
        m = MONTH_RE.match((raw or "").strip())
        if not m:
            raise ValueError(f"month cell unparsable: {raw!r}")
        year, frac = int(m.group(1)), m.group(2)
        years.add(year)
        if len(years) > 1:
            raise ValueError("mixed years in one table")
        if len(frac) == 2 and int(frac) != k + 1:  # 两位小数月份必须与序号一致
            raise ValueError(f"month sequence break at {raw!r} (#{k + 1})")
        out.append(f"{year}-{k + 1:02d}")
    if not out or len(out) > 12:
        raise ValueError(f"month cells count abnormal: {len(out)}")
    return out


def _find(grid: list[list[str]], pred) -> tuple[int, list[str]] | None:
    for i, row in enumerate(grid):
        if pred(row):
            return i, row
    return None


def _unit_of(grid: list[list[str]]) -> str:
    m = re.search(r"单位：(亿元|万亿元)", "\n".join("\t".join(r) for r in grid))
    return m.group(1) if m else ""


def _clean(text: str) -> str:
    return (text or "").replace("\xa0", " ").strip()


def parse_flow(grid: list[list[str]], url: str) -> list[dict]:
    """增量统计表：行式，``col0=月份``、``col1=当月增量``（亿元）。"""
    found = _find(grid, lambda r: "月份" in _clean(r[0] if r else ""))
    if found is None:
        raise ValueError("flow header row (月份) not found")
    start, _header = found
    month_rows = [r for r in grid[start + 1:] if r and MONTH_RE.match(_clean(r[0]))]
    months = _assign_months([r[0] for r in month_rows])
    rows: list[dict] = []
    for month, r in zip(months, month_rows):
        raw = _clean(r[1]) if len(r) > 1 else ""
        try:
            value = float(raw.replace(",", "")) if raw else None
        except ValueError:
            value = None
        if value is None:  # 未公布月份：行在值空 → 跳过
            continue
        rows.append({"month": month, "indicator": "社会融资规模增量",
                     "value": value, "unit": "亿元", "url": url})
    return rows


def parse_stock(grid: list[list[str]], url: str) -> list[dict]:
    """存量统计表：转置，月格在表头（隔列带增速），``社会融资规模存量`` 行取存量（万亿元）。"""
    header_hit = None
    for row in grid:
        hits = [c for c in row if MONTH_RE.match(_clean(c))]
        if len(hits) >= 3:
            header_hit = row
            break
    if header_hit is None:
        raise ValueError("stock header row (month cells) not found")
    data_hit = _find(grid, lambda r: r and _clean(r[0]) == "社会融资规模存量")
    if data_hit is None:
        raise ValueError("stock data row (社会融资规模存量) not found")
    data = data_hit[1]
    month_cols = [(i, c) for i, c in enumerate(header_hit) if MONTH_RE.match(_clean(c))]
    months = _assign_months([c for _, c in month_cols])
    unit = _unit_of(grid) or "万亿元"
    rows: list[dict] = []
    for (col, _raw), month in zip(month_cols, months):
        raw = _clean(data[col]) if col < len(data) else ""
        try:
            value = float(raw.replace(",", "")) if raw else None
        except ValueError:
            value = None
        if value is None:
            continue
        rows.append({"month": month, "indicator": "社会融资规模存量",
                     "value": value, "unit": unit, "url": url})
    return rows


_MONEY_SERIES = (("货币和准货币（M2）", "M2"), ("货币（M1）", "M1"), ("流通中货币（M0）", "M0"))


def parse_money(grid: list[list[str]], url: str) -> list[dict]:
    """货币供应量表：``项目`` 表头行带月格，M2/M1/M0 标签行按列取月末余额（亿元）。"""
    header = None
    for row in grid:
        if any("项目" in _clean(c) for c in row) and \
                sum(1 for c in row if MONTH_RE.match(_clean(c))) >= 3:
            header = row
            break
    if header is None:
        raise ValueError("money header row (项目 + month cells) not found")
    month_cols = [(i, c) for i, c in enumerate(header) if MONTH_RE.match(_clean(c))]
    months = _assign_months([c for _, c in month_cols])
    unit = _unit_of(grid) or "亿元"
    rows: list[dict] = []
    for label, indicator in _MONEY_SERIES:
        series_hit = _find(grid, lambda r, _lb=label: any(_lb in _clean(c) for c in r))
        if series_hit is None:
            raise ValueError(f"money series row not found: {label}")
        series = series_hit[1]
        for (col, _raw), month in zip(month_cols, months):
            raw = _clean(series[col]) if col < len(series) else ""
            try:
                value = float(raw.replace(",", "")) if raw else None
            except ValueError:
                value = None
            if value is None:
                continue
            rows.append({"month": month, "indicator": indicator,
                         "value": value, "unit": unit, "url": url})
    return rows


# --------------------------------------------------------------- 页面走栏


def _year_sections(landing_html: str) -> list[dict]:
    """栏目树 → 最近 ``YEAR_WINDOW`` 个年份的社融/货币概览子栏 URL（新年在前）。"""
    matches = list(YEAR_SECTION_RE.finditer(landing_html))
    if not matches:
        raise ValueError("no year sections on landing page")
    picked = matches[:YEAR_WINDOW]
    sections: list[dict] = []
    for i, m in enumerate(picked):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(landing_html)
        slice_html = landing_html[m.start():end]
        sec = {"year": int(m.group(2)), "errors": []}
        for key, title in (("afre", "社会融资规模"), ("money", "货币统计概览")):
            sm = re.search(SECTION_LINK_TMPL.format(title), slice_html)
            sec[key] = _absolute(sm.group(1), LANDING_URL) if sm else None
            if not sm:
                sec["errors"].append(f"{title} 子栏未找到")
        sections.append(sec)
    return sections


def _table_xlsx(section_html: str, title_prefix: str) -> str | None:
    """子栏页内按表标题前缀找 xlsx 直链（``地区…`` 同前缀表用 startswith 排除）。"""
    for block_m in TABLE_BLOCK_RE.finditer(section_html):
        block = block_m.group(1)
        t = TABLE_TITLE_RE.search(block)
        title = re.sub(r"<[^>]+>|\s+", " ", t.group(1)).strip() if t else ""
        if title.startswith(title_prefix):
            lm = re.search(r'href="([^"]+\.xlsx)"', block)
            if lm:
                return _absolute(lm.group(1), "")
    return None


def _parse_table(url: str, parser) -> list[dict]:
    grid = read_sheet(_get(url))
    return parser(grid, url)


def run_pboc_monetary(limit: int = 100) -> list[dict]:
    """取最近两个年度的社融增量/存量与 M0/M1/M2 月度行；全失败 → ``RuntimeError``。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    traces: list[str] = []
    rows: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    for sec in _year_sections(_get_html(LANDING_URL)):
        year = sec["year"]
        plans = (
            (sec.get("afre"), "社会融资规模增量统计表", parse_flow),
            (sec.get("afre"), "社会融资规模存量统计表", parse_stock),
            (sec.get("money"), "货币供应量", parse_money),
        )
        for page_url, title_prefix, parser in plans:
            if not page_url:
                traces.append(f"{year}: {title_prefix} 无子栏页")
                continue
            try:
                xlsx_url = _table_xlsx(_get_html(page_url), title_prefix)
                if not xlsx_url:
                    raise ValueError(f"表 {title_prefix} 未找到 xlsx 直链")
                for row in _parse_table(xlsx_url, parser):
                    rows.append({**row, "source": SOURCE, "scraped_at": now})
            except Exception as exc:  # noqa: BLE001 - 单表失败只跳过并留痕
                traces.append(f"{year} {title_prefix}: {type(exc).__name__}: {exc}"[:200])

    if not rows:
        raise RuntimeError("pboc-monetary: 全部统计表取数失败 — " + "; ".join(traces)[:300])
    return rows[:limit]


if __name__ == "__main__":  # 冒烟：python3 spiders/pboc-monetary/spider.py
    print(json.dumps(run_pboc_monetary(limit=6), ensure_ascii=False, indent=2))
