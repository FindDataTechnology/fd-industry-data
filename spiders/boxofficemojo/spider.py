"""spiders/boxofficemojo —— Box Office Mojo 年度票房排行取数。

数据表面（公开 HTML 表，免鉴权；直连低频，见工单 brief.notes）：

- 年度页： ``GET https://www.boxofficemojo.com/year/<YYYY>/`` → 单张 HTML 表
  （`mojo-field-type-rank` / `mojo-field-type-release` / `mojo-field-type-money` 三类
  `<td>`，表结构稳定可正则）。年份覆盖 1977 起；工单 brief 指定 2024 页为数据表面。
- 本单元取**最近三个年度页**（当年进行中 + 两个已完成年）：当年页随周度上映更新，
  已完成年页冻结不变。

口坑（侦察实测，逐一落实）：

- **gross 取行内第一个非 hidden 的 money 格**（年度 gross）；行内更靠前的 hidden money
  格是 Budget（`-` 占位），`[^"]*mojo-field-type-money(?![^"]*hidden)[^"]*"` 负向排除。
- **行内三要素齐全才算数据行**：rank/release/money 任一缺失即跳过（表头排序行、布局行）。
- 标题 HTML 转义用 ``html.unescape`` 还原（如 ``Deadpool &amp; Wolverine``）。
- ``week`` 字段为周度排行页（``/week/YYYYWnn/``）预留，年度行恒为 ``null``。
- 容错：单年页失败只跳过并留痕；**全部年页失败抛 ``RuntimeError``**（失败即红）。
- 只带常规浏览器 UA，不做指纹伪装与频次对抗；只用标准库（urllib/re/html/json）。

工单：``reports/health-tickets/20261006-boxofficemojo-c200b654.yaml``（``kind=generate``）
"""
from __future__ import annotations

import html
import json
import re
import urllib.request
from datetime import datetime, timezone

SOURCE = "boxofficemojo"
YEAR_URL = "https://www.boxofficemojo.com/{year_path}/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
}
TIMEOUT = 30   # 秒；年页约 460KB，超时即跳过该年（不重试放大）
YEAR_WINDOW = 3  # 当年 + 两个已完成年；再深的年份由 1977 起年份页按需扩

ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
RANK_RE = re.compile(r'<td class="[^"]*mojo-field-type-rank[^"]*">\s*(\d+)\s*</td>')
TITLE_RE = re.compile(
    r'<td class="[^"]*mojo-field-type-release[^"]*">\s*<a[^>]*>(.*?)</a>', re.S)
GROSS_RE = re.compile(
    r'<td class="(?![^"]*hidden)[^"]*mojo-field-type-money[^"]*">'
    r"\s*\$([\d,]+)\s*</td>")


def _get_html(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        return resp.read().decode("utf-8", "replace")


def fetch_year(year: int) -> list[dict]:
    """取一个年度页并解析为排行行（表头/布局行自动跳过）。"""
    url = YEAR_URL.format(year_path=f"year/{year}")
    page = _get_html(url)
    rows: list[dict] = []
    for row_html in ROW_RE.findall(page):
        rank, title, gross = (
            RANK_RE.search(row_html), TITLE_RE.search(row_html), GROSS_RE.search(row_html))
        if not (rank and title and gross):
            continue
        rows.append({
            "year": year,
            "week": None,  # 年度行；周度页（/week/YYYYWnn/）接入时填 ISO 周号
            "rank": int(rank.group(1)),
            "title": html.unescape(title.group(1)).strip() or None,
            "gross": int(gross.group(1).replace(",", "")),
            "url": url,
        })
    return rows


def run_boxofficemojo(limit: int = 100) -> list[dict]:
    """取最近三个年度页的排行行（新年份在前）；全部年页失败 → ``RuntimeError``。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    current_year = datetime.now(timezone.utc).year
    traces: list[str] = []
    rows: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    for year in range(current_year, current_year - YEAR_WINDOW, -1):
        try:
            for row in fetch_year(year):
                rows.append({**row, "source": SOURCE, "scraped_at": now})
        except Exception as exc:  # noqa: BLE001 - 单年页失败只跳过并留痕
            traces.append(f"{year}: {type(exc).__name__}: {exc}"[:200])

    if not rows:
        raise RuntimeError("boxofficemojo: 全部年页取数失败 — " + "; ".join(traces)[:300])
    return rows[:limit]


if __name__ == "__main__":  # 冒烟：python3 spiders/boxofficemojo/spider.py
    print(json.dumps(run_boxofficemojo(limit=3), ensure_ascii=False, indent=2))
