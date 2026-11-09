"""东方金诚 定期跟踪评级（Periodic-tracking）列表。

实测（2026-11-08，见 README 证据表）：

- 列表页 ``GET https://www.dfratings.com/Periodic-tracking.html`` 是**服务端
  渲染**的 HTML 表格（不是 XHR/JS 渲染），6 列：
  ``项目名称 / 主体等级 / 债项等级 / 评级展望 / 公告时间 / 下载``。
- 查询参数三个，全部服务端生效：

  ==========  ==========================================================
  ``wd``      关键词（全文检索）。``wd=银行`` 时页脚 ``共99页``，不带
              ``wd`` 时 ``共1239页`` —— 过滤发生在服务端，不是前端藏行。
  ``page``    1-based 页码。10 行/页，越界页返回空表（HTTP 200）。
  ``year``    4 位公告年份。``wd=银行&year=2025`` → ``共20页``；
              ``wd=银行&year=2024`` → ``共15页``。非法年份返回空表。
  ==========  ==========================================================

- 排序：``公告时间`` 降序（同年内按上游发布顺序），滚动列表最新在前。
- 详情：**没有详情页**。项目名称直接链到 PDF：
  ``/uploads/files/YYYY/MMDD/<token>.pdf``；下载列的 ``download`` 属性给出
  报告文件名。故本单元一行一个（公告, 债项）条目，不抓 PDF 正文。

两个 HTML 坑（首轮实测）：

1. 每个数据行的项目名称单元格后面有一段**被注释掉的重复 `<td>`**，直接
   按 ``<td>`` 计数会得到 7 列并错位——必须先剔除 HTML 注释再切行。
2. 上游等级字段存在原文错字（实测 ``AA_`` / ``AA-`` 混用），本单元**原样
   保留**，不做等级归一化（归一化会掩盖上游质量问题，也会让 golden 失真）。
"""
from __future__ import annotations

import html as html_lib
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://www.dfratings.com"
LIST_URL = BASE + "/Periodic-tracking.html"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": LIST_URL,
}

#: 网络礼节：本单元实测 1.2s 间隔（任务要求 ≥1s；站点无速率头，保守取值）。
SLEEP = 1.2
#: 页脚 ``共N页``；用于提前判定到底，避免无谓翻页。
_TOTAL_PAGES = re.compile(r"共(\d+)页")
_YEAR_RE = re.compile(r"^\d{4}$")
_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_LINK_RE = re.compile(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DOWNLOAD_ATTR = re.compile(r'download="([^"]*)"')
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_HEADER_LABEL = "项目名称"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _text(cell: str) -> str:
    """单元格 → 纯文本：去标签、反转义、压空白（含 &nbsp;）。"""
    plain = _TAG_RE.sub("", cell or "")
    plain = html_lib.unescape(plain).replace("\u00a0", " ")
    return _WS_RE.sub(" ", plain).strip()


def _absolute(href: str) -> str:
    href = html_lib.unescape((href or "").strip())
    if not href:
        return ""
    return urllib.parse.urljoin(BASE + "/", href)


def _fetch(page: int, keyword: str, year: str, timeout: int = 40) -> str:
    params: dict[str, str] = {"page": str(page)}
    if keyword:
        params["wd"] = keyword
    if year:
        params["year"] = year
    url = LIST_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    return raw.decode("utf-8", "replace")


def _total_pages(html_text: str) -> int | None:
    matched = _TOTAL_PAGES.search(html_text)
    return int(matched.group(1)) if matched else None


def _parse_page(
    html_text: str, page: int, keyword: str, year: str, stamp: str
) -> list[dict]:
    """解析一页列表；找不到 ``pjtable`` 表体时抛 ``ValueError``（疑似改版）。"""
    start = html_text.find('<div class="pjtable">')
    if start < 0:
        raise ValueError("未找到 pjtable 列表表体")
    end = html_text.find("</table>", start)
    if end < 0:
        raise ValueError("未找到列表 </table>")
    segment = _COMMENT_RE.sub("", html_text[start:end])

    rows: list[dict] = []
    for raw_row in _ROW_RE.findall(segment):
        if _HEADER_LABEL in raw_row:
            continue
        cells = _CELL_RE.findall(raw_row)
        if len(cells) != 6:
            print(
                f"[dfjc-ratings] 第{page}页行结构异常（{len(cells)} 列，期望 6）已跳过",
                flush=True,
            )
            continue
        link = _LINK_RE.search(cells[0])
        project_name = _text(link.group(2) if link else cells[0])
        if not project_name:
            continue
        href = link.group(1) if link else ""
        download = _LINK_RE.search(cells[5])
        if not href and download:
            href = download.group(1)
        filename = _DOWNLOAD_ATTR.search(cells[5])
        rows.append(
            {
                "project_name": project_name,
                "subject_rating": _text(cells[1]),
                "bond_rating": _text(cells[2]),
                "outlook": _text(cells[3]),
                "announce_date": _text(cells[4]),
                "pdf_url": _absolute(href),
                "pdf_filename": html_lib.unescape(filename.group(1)) if filename else "",
                "keyword": keyword,
                "year": year,
                "page": page,
                "source": "dfjc-ratings",
                "scraped_at": stamp,
            }
        )
    return rows


def run_dfjc_ratings(
    limit: int = 200,
    keyword: str = "银行",
    pages: int = 3,
    year: str = "",
) -> list[dict]:
    """东方金诚定期跟踪评级列表（一行一个「项目名称 × 公告」条目）。

    Args:
        limit: 返回行数上限（按上游列表顺序截断，最新在前）。
        keyword: 关键词，作为 ``wd`` 参数由服务端全文检索；空串 = 不限关键词。
        pages: 从第 1 页起连续翻多少页（每页 10 条；遇空页提前停止）。
        year: 4 位公告年份，作为 ``year`` 参数由服务端过滤；空串 = 全部年份。
            该参数是相对任务原文的**增量**：默认口径仍是 ``wd=银行`` 的全量
            滚动列表，给定年份时用于回补历史/固定 golden 口径（见 README）。

    Raises:
        ValueError: ``year`` 非空且不是 4 位数字。
        RuntimeError: 所有请求页都找不到列表表体（疑似站点改版/全面失败）。

    Returns:
        每行含项目名称、主体/债项等级、展望、公告时间、PDF 直链、页码等字段；
        上游缺测的等级/展望保留空串（公告类条目本就没有等级），不伪造。
    """
    if limit <= 0 or pages <= 0:
        return []
    keyword = (keyword or "").strip()
    year = str(year or "").strip()
    if year and not _YEAR_RE.match(year):
        raise ValueError(f"year 须为 4 位数字年份，收到 {year!r}")

    stamp = _now()
    rows: list[dict] = []
    seen: set[str] = set()
    table_seen = False
    fetch_failures = 0
    total_pages: int | None = None

    for page in range(1, int(pages) + 1):
        if page > 1:
            time.sleep(SLEEP)
        try:
            html_text = _fetch(page, keyword, year)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            fetch_failures += 1
            print(f"[dfjc-ratings] 第{page}页请求失败：{exc}", flush=True)
            continue
        if total_pages is None:
            total_pages = _total_pages(html_text)
            print(f"[dfjc-ratings] wd={keyword!r} year={year!r} 共{total_pages}页", flush=True)
        try:
            page_rows = _parse_page(html_text, page, keyword, year, stamp)
        except ValueError as exc:
            print(f"[dfjc-ratings] 第{page}页解析失败：{exc}", flush=True)
            continue
        table_seen = True
        if not page_rows:
            print(f"[dfjc-ratings] 第{page}页无条目（到底/该年无数据），停止翻页", flush=True)
            break
        added = 0
        for row in page_rows:
            key = row["pdf_url"] or f'{row["project_name"]}|{row["announce_date"]}'
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
            added += 1
            if len(rows) >= int(limit):
                break
        print(
            f"[dfjc-ratings] 第{page}页：{len(page_rows)} 条，新增 {added}，累计 {len(rows)}",
            flush=True,
        )
        if len(rows) >= int(limit):
            break
        if total_pages is not None and page >= total_pages:
            break

    if not table_seen:
        raise RuntimeError(
            f"dfjc-ratings: {fetch_failures} 页请求失败且无任何可解析列表（疑似改版）"
        )
    if not rows:
        print("[dfjc-ratings] 列表可解析但无条目（该关键词/年份无公告）", flush=True)
    return rows[: int(limit)]
