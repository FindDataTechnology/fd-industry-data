"""中国理财网（银行业理财登记托管中心）理财市场报告元数据。

栏目页 ``https://www.chinawealth.com.cn/lc_lcsj/lcbg/`` 是静态分页列表：

- 首页 ``index.html``；第 ``k`` 页（``k >= 2``）= ``index_{pageCount-k+1}.html``。
  ``pageCount`` 写在首页内联脚本里（``let pageCount = Number('3')``），
  实测 2026-11-08 为 3 页 / 28 条。页码后缀**不是**正序：第 2 页是
  ``index_2.html``、第 3 页是 ``index_1.html``（由栏目页 ``jumpTo()`` 的
  ``pageCount - page + 1`` 决定），所以每轮都从首页现读 ``pageCount``，
  不硬编码后缀。
- 列表项：``<h2 class="news_name"><a title="…" href="./YYYYMM/t….html">``，
  同 ``<li>`` 内的 ``<span class="news_date">YYYY-MM-DD</span>`` 是发布日期。
- 详情页的附件不是 ``<a href>``，而是空 ``<li>`` + 属性，由页面 JS 拼出链接::

      <li class='file-list' data-recurl='./P020261021528431241365.pdf'
          data-sourceName='P020261021528431241365.pdf'
          data-download='中国银行业理财市场季度报告（2026年三季度）.pdf' …>

  ``data-recurl`` 是**相对详情页目录**的 PDF 路径（``./P0….pdf``），
  本单元按详情页 URL 解析为绝对地址。

本单元只交付**报告元数据**（标题/期号/类型/发布日/发布机构/详情页/PDF 直链），
不下载也不解析 PDF 正文——因此不触碰 ``fd_industry_data.pdftext``。
HTML 是列表式片段（无 ``<table>``），按字符串/正则切片，不引入表格解析器。

实测（2026-11-08）：28 条 / 3 页 / 27 条带 PDF（1 条纯文字公告无附件，
见 README 失败策略）；全部详情页 HTTP 200，PDF 直链 HTTP 200 + ``application/pdf``。
"""
from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://www.chinawealth.com.cn"
LIST_URL = BASE + "/lc_lcsj/lcbg/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": LIST_URL,
}

#: 请求间隔（秒）——站点无 robots.txt，按 ≥1s 的礼貌间隔串行抓取。
SLEEP = 1.0

_PAGECOUNT_RE = re.compile(r"let\s+pageCount\s*=\s*Number\('(\d+)'\)")
_ITEM_RE = re.compile(
    r'<h2 class="news_name">\s*<a title="(?P<title>[^"]*)"\s+'
    r'href="(?P<href>[^"]+)"'
    r'.*?<span class="news_date">\s*(?P<date>\d{4}-\d{2}-\d{2})\s*</span>',
    re.S,
)
_DETAIL_TITLE_RE = re.compile(r'<h4\s+title="([^"]*)"\s+class="news_tit"')
_PUBLISHED_RE = re.compile(r"发布时间：\s*(\d{4}-\d{2}-\d{2})")
_PUBLISHER_RE = re.compile(r"文章来源：\s*([^\s<&]+)")
_RECURL_RE = re.compile(r"data-recurl=['\"]([^'\"]+)['\"]")
_DOWNLOAD_RE = re.compile(r"data-download=['\"]([^'\"]+)['\"]")
_ISSUE_RE = re.compile(r"[（(]([^（）()]+)[）)]")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _get(url: str, referer: str = LIST_URL, *, retries: int = 1) -> str:
    """GET 一页 HTML（UTF-8）。失败重试 ``retries`` 次，仍失败则抛出。"""
    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url, headers={**HEADERS, "Referer": referer}
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                return resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(SLEEP)
    raise RuntimeError(f"GET {url} 失败：{last}")


def _page_count(index_html: str) -> int:
    matched = _PAGECOUNT_RE.search(index_html)
    return int(matched.group(1)) if matched else 1


def _page_url(page_no: int, page_count: int) -> str:
    """第 ``page_no`` 页（1 起）的 URL。

    首页 = ``index.html``；其余按栏目页 ``jumpTo()`` 的
    ``index_{pageCount-page+1}.html`` 反推（实测第 2 页 = ``index_2.html``，
    第 3 页 = ``index_1.html``）。
    """
    if page_no <= 1:
        return urllib.parse.urljoin(LIST_URL, "index.html")
    return urllib.parse.urljoin(
        LIST_URL, f"index_{page_count - page_no + 1}.html"
    )


def _issue_of(title: str) -> str | None:
    """报告期标签 = 标题里括号内的文字（多个用 ``、`` 连接）。

    ``中国银行业理财市场季度报告（2026年三季度）`` → ``2026年三季度``；
    合并公告 ``…年度报告（2013年）、…半年度报告（2014上半年）`` →
    ``2013年、2014上半年``（如实保留两个报告期，不猜单期）。
    """
    groups = [g.strip() for g in _ISSUE_RE.findall(title) if g.strip()]
    return "、".join(groups) if groups else None


def _doc_type(title: str) -> str:
    """报告类型（按标题字面分类，优先级见 README「类型规则」）。

    - 含「季度」→ ``quarterly``
    - 同时含年度与半年标记、且标题以「、」并列两个报告 → ``mixed``
    - 含「半年」/「上半年」→ ``half_year``
    - 其余（年度报告 / ``报告（YYYY年）``）→ ``annual``
    """
    if "季度" in title:
        return "quarterly"
    half = "半年" in title or "上半年" in title
    annual = "年度报告" in title or "年报" in title or bool(
        re.search(r"报告[（(]\d{4}", title)
    )
    if half and annual and "、" in title:
        return "mixed"
    if half:
        return "half_year"
    return "annual"


def _list_items(html: str) -> list[dict]:
    items: list[dict] = []
    for m in _ITEM_RE.finditer(html):
        items.append(
            {
                "title": m.group("title").strip(),
                "url": urllib.parse.urljoin(LIST_URL, m.group("href")),
                "list_date": m.group("date"),
            }
        )
    return items


def _detail(url: str) -> dict:
    """详情页附件与元数据；页面结构不符时抛 ``ValueError``。"""
    html = _get(url, referer=LIST_URL)
    title = _DETAIL_TITLE_RE.search(html)
    published = _PUBLISHED_RE.search(html)
    publisher = _PUBLISHER_RE.search(html)
    recurl = _RECURL_RE.search(html)
    download = _DOWNLOAD_RE.search(html)
    if not title and not published:
        raise ValueError("详情页缺标题与发布时间，疑似结构变更")
    return {
        "detail_title": title.group(1).strip() if title else None,
        "published_at": published.group(1) if published else None,
        "publisher": publisher.group(1).strip() if publisher else None,
        "pdf_url": (
            urllib.parse.urljoin(url, recurl.group(1).strip())
            if recurl
            else None
        ),
        "pdf_filename": download.group(1).strip() if download else None,
    }


def run_chinawealth_reports(limit: int = 60, pages: int = 3) -> list[dict]:
    """中国理财网理财市场报告元数据（每条报告/公告一行，最新在前）。

    Args:
        limit: 返回行数上限（默认 60；当前全量 28 条）。
        pages: 列表翻页深度（默认 3 = 当前全部页）。超过站点实际页数时
            按 ``pageCount`` 截断；``<= 0`` 返回空列表。

    Raises:
        RuntimeError: 首页不可达，或列表页解析不出任何条目。
    """
    if limit <= 0 or pages <= 0:
        return []
    stamp = _now()

    index_html = _get(_page_url(1, 1), referer=LIST_URL)
    page_count = _page_count(index_html)
    entries: list[dict] = _list_items(index_html)
    print(
        f"[chinawealth-reports] 第 1 页 {len(entries)} 条（pageCount={page_count}）",
        flush=True,
    )

    depth = min(int(pages), page_count)
    for page_no in range(2, depth + 1):
        time.sleep(SLEEP)
        url = _page_url(page_no, page_count)
        try:
            html = _get(url, referer=LIST_URL)
        except RuntimeError as exc:
            print(f"[chinawealth-reports] 第 {page_no} 页失败，停止翻页：{exc}", flush=True)
            break
        page_entries = _list_items(html)
        if not page_entries:
            print(f"[chinawealth-reports] 第 {page_no} 页 0 条，停止翻页", flush=True)
            break
        entries.extend(page_entries)
        print(
            f"[chinawealth-reports] 第 {page_no} 页 {len(page_entries)} 条"
            f"（{url.rsplit('/', 1)[-1]}）",
            flush=True,
        )

    # 详情页去重（按详情页 URL），保持列表顺序（最新在前）。
    seen: set[str] = set()
    unique: list[dict] = []
    for entry in entries:
        if entry["url"] in seen:
            continue
        seen.add(entry["url"])
        unique.append(entry)

    rows: list[dict] = []
    for entry in unique:
        time.sleep(SLEEP)
        try:
            detail = _detail(entry["url"])
        except (RuntimeError, ValueError) as exc:
            print(f"[chinawealth-reports] 详情失败，保留列表元数据：{entry['url']} {exc}", flush=True)
            detail = {
                "detail_title": None,
                "published_at": None,
                "publisher": None,
                "pdf_url": None,
                "pdf_filename": None,
            }
        title = detail["detail_title"] or entry["title"]
        if detail["detail_title"] and detail["detail_title"] != entry["title"]:
            print(
                f"[chinawealth-reports] 标题漂移 {entry['url']}："
                f"列表 {entry['title']!r} / 详情 {detail['detail_title']!r}（取详情）",
                flush=True,
            )
        published_at = detail["published_at"] or entry["list_date"]
        if not detail["pdf_url"]:
            print(
                f"[chinawealth-reports] 无 PDF 附件（纯文字公告）：{entry['url']}",
                flush=True,
            )
        rows.append(
            {
                "title": title,
                "issue": _issue_of(title),
                "doc_type": _doc_type(title),
                "published_at": published_at,
                "publisher": detail["publisher"],
                "report_url": entry["url"],
                "pdf_url": detail["pdf_url"],
                "pdf_filename": detail["pdf_filename"],
                "source": "chinawealth-reports",
                "scraped_at": stamp,
            }
        )

    if not rows:
        raise RuntimeError("chinawealth-reports: 列表为 0 条（页面结构可能已变）")

    rows.sort(
        key=lambda r: (r["published_at"] or "", r["report_url"]), reverse=True
    )
    return rows[: int(limit)]
