"""spiders/agri-casde —— 中国农业农村信息网「供需形势」CASDE 月度供需平衡表。

数据表面（2026-11-08 实测）：

- 列表 ``https://www.agri.cn/sj/gxxs/``，分页 ``index_N.htm``（``index_1`` 即
  裸目录），10 条/页；**没有固定总页数**，``index_14.htm`` 起 404，翻页必须以
  404 终止。
- 列表混流：正式 CASDE 报告与大蒜/罗非鱼/对虾专报混排，大蒜还有大众版与
  详细版两个版本。只保留标题含 ``CASDE-No.<n>`` 且含「农产品供需形势分析」
  的条目（专报不带 CASDE 期号）。
- 详情页裸 GET 即 200，无 UA/Cookie 要求；新版正文含 **5 张 HTML ``<table>``**
  （玉米 / 大豆 / 棉花 / 食用植物油 / 食糖），每表 18–24 行。
- 表内结构（逐条对应侦察坑位）：

  1. **单位独立成行**：``千公顷（1000 hectares）`` / ``万吨（10000 tons）`` /
     ``公斤/公顷`` / ``吨/公顷`` / ``元/吨`` / ``美分/磅`` 各自单占一行，切换
     后续所有数据行的单位——单位行绝不能当数据行，单位未知的数据行跳过并计数。
  2. **多列预测**：表头形如 ``2026/27 （9月预测）`` / ``2026/27 （10月预测）``，
     月份写在列名里，列名不可硬编码：按列名解析出预测月，与本期刊次月比较，
     同月为 ``current``，否则为 ``previous``；无月份的历史年度列记 ``actual``，
     并保留 ``basis``（forecast/estimate）区分「预测」「估计」。注意 5 月这类
     期次的表头是「4月估计 / 5月估计 / 5月预测」——current/previous 由月份
     判定，不绑定「预测」二字。
  3. **价格区间**：单元格可为 ``2300-2600`` / ``14.0-18.5``，拆成 ``_low`` /
     ``_high`` 两行，不取中点、不填 0。
  4. **期次主键** ``CASDE-No.<n>`` 落成 ``casde_no`` 列；``period`` 取自列表
     标题的「YYYY年M月」，缺标题月份时按 ``No.124 = 2026-10`` 的期号-月份
     等差数列回推。
  5. **历史期次形态不同**：HTML 表格从 ``CASDE-No.95``（2024-05）开始；更早
     期次正文只有叙述文字 + PDF 附件（无 HTML 表），本单元不解析 PDF/不 OCR，
     这类期次跳过并计数（见 README「历史深度」）。

入口 ``run_agri_casde(limit=50, window_start=None, window_end=None)``。``limit``
是返回行数上限（确定性顺序：列表新→旧、表内行序、列序）；``window_start`` /
``window_end`` 为首爬回补窗口（YYYY-MM-DD），**先按窗口过滤期次、再截断 limit**。

只用标准库：``urllib`` + ``html.parser`` + ``re`` + ``json``（镜像未装 lxml /
httpx，亦不引入新依赖）。
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin

SOURCE = "agri-casde"
LIST_URL = "https://www.agri.cn/sj/gxxs/"
LIST_PAGE_URL = "https://www.agri.cn/sj/gxxs/index_{page}.htm"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
}
TIMEOUT = 60  # 秒；详情页约 0.3–0.5 MB
MAX_PAGES = 200  # 防御性硬上限；真实翻页由 404 终止

# CASDE 期号 ↔ 月份基准：No.124 = 2026-10（实测），每月一期。
_CASDE_REF_NO = 124
_CASDE_REF_YEAR = 2026
_CASDE_REF_MONTH = 10

# 表格前的小标题 → 商品代码（标题优先，标题缺省才按固定表序兜底）
_COMMODITY_KEYWORDS = (
    ("食用植物油", "edible_oil"),
    ("玉米", "corn"),
    ("大豆", "soybean"),
    ("棉花", "cotton"),
    ("食糖", "sugar"),
)
_COMMODITY_ORDER = ("corn", "soybean", "cotton", "edible_oil", "sugar")

# 表内可识别的单位（单位行取括号前中文部分）；未知单位 → 该块数据行跳过并计数
_KNOWN_UNITS = ("千公顷", "万吨", "公斤/公顷", "吨/公顷", "元/吨", "美分/磅")

_CASDE_RE = re.compile(r"CASDE\s*[-–—]\s*No\.?\s*(\d+)", re.I)
_TITLE_MONTH_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")
# 列名：2026/27 （9月预测）/ 2025/26 (5月估计) / 2024/25
_COLUMN_RE = re.compile(
    r"^\s*(\d{4}\s*/\s*\d{2})\s*"
    r"(?:[（(]\s*(\d{1,2})\s*月\s*(预测|估计)[^）)]*[）)])?"
)
_ANCHOR_RE = re.compile(
    r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.S | re.I)
_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>", re.S)
_SPACE_RE = re.compile(r"[\s\u00a0]+")
# 区间：2300-2600 / 14.0—18.5 / 75~100（单负值 -111 不会命中）
_RANGE_RE = re.compile(
    r"^([+-]?\d+(?:\.\d+)?)\s*[-–—−~～至]\s*([+-]?\d+(?:\.\d+)?)$"
)

logger = logging.getLogger(__name__)


class _TableParser(HTMLParser):
    """把一段 ``<table>`` HTML 解析成 ``list[list[str]]``（单元格纯文本）。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            self._row.append(_clean_text("".join(self._cell)))
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _clean_text(text: str) -> str:
    text = text.replace("\u00a0", " ")
    return _SPACE_RE.sub(" ", text).strip()


def _fetch(url: str, *, allow_404: bool = False) -> str | None:
    """GET 一个页面；``allow_404`` 时 404/410 返回 ``None``（翻页终止信号）。"""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        if allow_404 and exc.code in (404, 410):
            return None
        raise RuntimeError(
            f"agri-casde: HTTP {exc.code} 取数失败 {url}") from exc
    except Exception as exc:  # noqa: BLE001 - 单口失败即红
        raise RuntimeError(
            f"agri-casde: 取数失败 {url}: {type(exc).__name__}: {exc}") from exc
    return raw.decode("utf-8", "replace")


def _period_from_title(title: str) -> str | None:
    m = _TITLE_MONTH_RE.search(title)
    if not m:
        return None
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"


def _period_from_casde(casde_no: int) -> str:
    """期号 → YYYY-MM：No.124 = 2026-10，每月一期（实测可回推到 No.1 = 2016-07）。"""
    total = _CASDE_REF_YEAR * 12 + (_CASDE_REF_MONTH - 1)
    total -= _CASDE_REF_NO - casde_no
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _window_months(window_start, window_end) -> tuple[str, str] | None:
    """首爬回补窗口 → 闭区间 ``(YYYY-MM, YYYY-MM)``；未带窗口 → ``None``。"""
    if window_start is None and window_end is None:
        return None
    if not window_start or not window_end:
        raise ValueError(
            f"agri-casde: window 参数须成对出现，got start={window_start!r} "
            f"end={window_end!r}")
    try:
        start = datetime.strptime(str(window_start), "%Y-%m-%d").strftime("%Y-%m")
        end = datetime.strptime(str(window_end), "%Y-%m-%d").strftime("%Y-%m")
    except ValueError:
        raise ValueError(
            f"agri-casde: window 参数须为 YYYY-MM-DD，got start={window_start!r} "
            f"end={window_end!r}") from None
    if start > end:
        raise ValueError(
            f"agri-casde: window_start {window_start} 晚于 window_end {window_end}")
    return start, end


def _parse_list_entries(html: str, page_url: str) -> list[dict]:
    """列表页 → 正式 CASDE 报告条目（按标题过滤，专报一律不取）。"""
    entries: dict[int, dict] = {}
    for href, raw_title in _ANCHOR_RE.findall(html):
        title = _clean_text(_TAG_RE.sub("", unescape(raw_title)))
        if "农产品供需形势分析" not in title:
            continue
        m = _CASDE_RE.search(title)
        if not m:
            continue  # 大蒜/罗非鱼/对虾专报：无 CASDE 期号
        casde_no = int(m.group(1))
        period = _period_from_title(title) or _period_from_casde(casde_no)
        if casde_no in entries:
            continue
        entries[casde_no] = {
            "casde_no": casde_no,
            "period": period,
            "title": title,
            "url": urljoin(page_url, href),
        }
    return sorted(entries.values(), key=lambda e: e["casde_no"], reverse=True)


def _detect_commodity(html: str, table_start: int) -> str | None:
    """表格前 400 字符内的小标题（如「中国玉米供需平衡表」）→ 商品代码。"""
    context = html[max(0, table_start - 400):table_start]
    text = _SPACE_RE.sub("", unescape(_TAG_RE.sub(" ", context)))
    best: str | None = None
    best_pos = -1
    for keyword, code in _COMMODITY_KEYWORDS:
        pos = text.rfind(keyword)
        if pos > best_pos:
            best_pos, best = pos, code
    return best


def _forecast_month(issue_period: str, label_month: int) -> str:
    """列名里的月份 → YYYY-MM；1 月期次的「12月预测」属于上一年。"""
    issue_year, issue_month = (int(x) for x in issue_period.split("-"))
    year = issue_year if label_month <= issue_month else issue_year - 1
    return f"{year:04d}-{label_month:02d}"


def _parse_columns(header: list[str], issue_period: str) -> tuple[list[dict], int]:
    """表头 → 列定义。返回 ``(columns, 无法解析的列数)``。"""
    columns: list[dict] = []
    bad = 0
    for idx in range(1, len(header)):
        label = header[idx]
        m = _COLUMN_RE.match(label)
        if not m:
            if label.strip():
                bad += 1
            continue
        season = _SPACE_RE.sub("", m.group(1))
        label_month = m.group(2)
        basis_cn = m.group(3)
        if label_month:
            forecast_month = _forecast_month(issue_period, int(label_month))
            forecast_kind = "current" if forecast_month == issue_period else "previous"
            basis = "forecast" if basis_cn == "预测" else "estimate"
        else:
            forecast_month = None
            forecast_kind = "actual"
            basis = None
        columns.append(
            {
                "index": idx,
                "season": season,
                "basis": basis,
                "forecast_month": forecast_month,
                "forecast_kind": forecast_kind,
            }
        )
    return columns, bad


def _parse_values(raw: str) -> list[tuple[str, float]]:
    """单元格 → ``[(后缀, 数值)]``；区间返回 ``_low``/``_high``，非法 → ``[]``。"""
    text = raw.replace(",", "").replace("，", "").replace("\u2212", "-").strip()
    if not text:
        return []
    m = _RANGE_RE.match(text)
    if m:
        return [("_low", float(m.group(1))), ("_high", float(m.group(2)))]
    try:
        return [("", float(text))]
    except ValueError:
        return []


def _is_unit_row(row: list[str]) -> bool:
    non_empty = [c for c in row if c]
    return len(non_empty) == 1 and any(k in non_empty[0] for k in ("公顷", "吨", "磅"))


def _parse_unit(text: str) -> str | None:
    unit = re.split(r"[（(]", text, maxsplit=1)[0].strip()
    return unit if unit in _KNOWN_UNITS else None


def _parse_detail(html: str, entry: dict, scraped_at: str,
                  skipped: Counter) -> list[dict]:
    """详情页 → 长表行（每商品 × 每数据行 × 每列）。"""
    rows: list[dict] = []
    matches = list(_TABLE_RE.finditer(html))
    used: set[str] = set()
    for position, match in enumerate(matches):
        table_html = match.group(0)
        commodity = _detect_commodity(html, match.start())
        if commodity is None or commodity in used:
            # 标题缺失或重复时才按固定表序兜底（5 表为正式版式）
            commodity = (_COMMODITY_ORDER[position]
                         if len(matches) == len(_COMMODITY_ORDER) else None)
        if commodity is None or commodity in used:
            skipped["unknown_commodity"] += 1
            continue
        used.add(commodity)

        parser = _TableParser()
        parser.feed(table_html)
        table_rows = parser.rows
        header_idx = next(
            (i for i, r in enumerate(table_rows)
             if len(r) > 1 and any(re.search(r"\d{4}\s*/\s*\d{2}", c) for c in r)),
            None,
        )
        if header_idx is None:
            skipped["missing_header"] += 1
            continue
        header = table_rows[header_idx]
        columns, bad_columns = _parse_columns(header, entry["period"])
        skipped["bad_column"] += bad_columns
        if not columns:
            skipped["missing_header"] += 1
            continue

        unit: str | None = None
        for row in table_rows[header_idx + 1:]:
            if _is_unit_row(row):
                unit = _parse_unit(row[0])
                if unit is None:
                    skipped["unknown_unit"] += 1
                continue
            if len(row) < len(header):
                row = row + [""] * (len(header) - len(row))
            elif len(row) > len(header):
                skipped["bad_width"] += 1
                continue
            label = row[0].strip()
            if not label:
                skipped["missing_label"] += 1
                continue
            if unit is None:
                skipped["no_unit"] += 1
                continue
            for col in columns:
                raw = row[col["index"]].strip()
                if not raw:
                    skipped["empty_value"] += 1
                    continue
                values = _parse_values(raw)
                if not values:
                    skipped["bad_value"] += 1
                    continue
                for suffix, value in values:
                    rows.append(
                        {
                            "casde_no": entry["casde_no"],
                            "period": entry["period"],
                            "commodity": commodity,
                            "item": f"{label}{suffix}",
                            "value": value,
                            "unit": unit,
                            "forecast_kind": col["forecast_kind"],
                            "season": col["season"],
                            "forecast_month": col["forecast_month"],
                            "basis": col["basis"],
                            "source_url": entry["url"],
                            "scraped_at": scraped_at,
                        }
                    )
    return rows


def _log_skipped(skipped: Counter) -> None:
    if not skipped:
        return
    detail = ", ".join(f"{key}={value}" for key, value in sorted(skipped.items()))
    logger.warning("agri-casde: skipped/dropped counters: %s", detail)


def run_agri_casde(limit: int = 50, window_start: str | None = None,
                   window_end: str | None = None) -> list[dict]:
    """抓取 CASDE 月度报告长表，至多 ``limit`` 行（确定性顺序）。

    ``window_start``/``window_end``（可选，YYYY-MM-DD）为首爬回补窗口：
    只保留期次月份落在闭区间内的报告，过滤先于 ``limit`` 截断。
    无窗口且解析 0 行 → ``RuntimeError``（失败即红）；带窗口且窗口内无
    HTML 表格期次（2016–2024-04 只有 PDF）→ 返回 ``[]``。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []
    window = _window_months(window_start, window_end)
    scraped_at = datetime.now(timezone.utc).isoformat()

    rows: list[dict] = []
    skipped: Counter = Counter()
    seen: set[int] = set()
    page = 1
    crossed_window = False

    while page <= MAX_PAGES:
        page_url = LIST_URL if page == 1 else LIST_PAGE_URL.format(page=page)
        html = _fetch(page_url, allow_404=page > 1)
        if html is None:
            break  # 真实翻页终点：index_N.htm 404
        for entry in _parse_list_entries(html, page_url):
            if entry["casde_no"] in seen:
                continue
            seen.add(entry["casde_no"])
            if window is not None:
                if not (window[0] <= entry["period"] <= window[1]):
                    if entry["period"] < window[0]:
                        crossed_window = True
                    continue
            rows.extend(
                _parse_detail(_fetch(entry["url"]), entry, scraped_at, skipped))
            if len(rows) >= limit:
                _log_skipped(skipped)
                return rows[:limit]
        if crossed_window:
            break  # 列表新→旧，已越过窗口下界
        page += 1

    _log_skipped(skipped)
    if not rows and window is None:
        raise RuntimeError("agri-casde: 未解析到任何 CASDE 数据行 — 失败即红")
    return rows[:limit]


if __name__ == "__main__":  # 冒烟：python3 spiders/agri-casde/spider.py
    print(json.dumps(run_agri_casde(limit=3), ensure_ascii=False, indent=2))
