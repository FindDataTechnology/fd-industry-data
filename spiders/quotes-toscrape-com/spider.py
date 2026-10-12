"""quotes-toscrape-com — quotes.toscrape.com 公开练习站点名句列表取数单元。

来源：https://quotes.toscrape.com/ —— Scrapy 官方练习站（静态 HTML，公开、无反爬、
无鉴权、无 JS 渲染）。本单为 wire 需求单生成单（DE-20261013-U1，UI 目检 drill）。

字段口径（brief 未点名列，按站点结构取最小稳定集）：
- text：名句原文（页面渲染原样，含弯引号；不改写、不截断、不去引号）；
- author：作者名（页面渲染原样）；
- tags：标签逗号连接（无标签时为空串，占位不臆造）；
- url：该行读取的页面地址（首页为常量，翻页后为对应 /page/N/ 地址）；
- source：恒为 "quotes-toscrape-com"（常量）；
- scraped_at：取数时刻 UTC ISO8601。

坑位与容错：
- 只用标准库（urllib + html.parser）：站点是静态 HTML，无需 scrapling/httpx/lxml，
  既不加新依赖，也少一层解析漂移；
- 解析按 div.quote 块内的 span.text / small.author / a.tag 取字段，块内嵌套 div.tags
  用深度计数收口，避免把相邻名句串行；
- 分页：站点每页 10 条，<li class="next"><a href="/page/N/"> 提供下一页；按 limit
  需要翻页，最多 5 页（上限硬编码，防越界抓取），不重复访问同一页；
- 网络层：15s 超时 + 至多 3 次尝试（退避 2s/4s），只做容错，不做频次对抗；
- **取数失败绝不伪造行**：页面不可达/解析不到任何名句块时抛 RuntimeError，
  交给验证链判红并转人工，不静默返回空列表（避免验证链"空通过"）；
- 未写 schedule（静默合入，点亮由人工补写）、未写 site（走默认执行位）。

入口：run_quotes_toscrape_com(limit=100) -> list[dict]
"""
from __future__ import annotations

import time
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin

SOURCE = "quotes-toscrape-com"
SOURCE_URL = "https://quotes.toscrape.com/"
MAX_PAGES = 5
TIMEOUT_SECONDS = 15
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2
USER_AGENT = "finddata-spider/1.0 (+https://finddatatech.cloud)"


class _QuotesParser(HTMLParser):
    """抽取 ``<div class="quote">`` 块：``span.text`` / ``small.author`` / ``a.tag``。

    同时记录 ``<li class="next"><a href="...">`` 的下一页地址（站点翻页入口）。
    只认页面结构，不做任何字段改写。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict] = []
        self.next_href: str | None = None
        self._in_quote = False
        self._depth = 0
        self._row: dict | None = None
        self._capture: str | None = None
        self._buf: list[str] = []
        self._in_next_li = False

    # -- 解析回调 ---------------------------------------------------------
    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        attr = {k: (v or "") for k, v in attrs}
        classes = attr.get("class", "").split()

        if tag == "li" and "next" in classes:
            self._in_next_li = True
        elif tag == "a" and self._in_next_li and self.next_href is None:
            self.next_href = attr.get("href") or None

        if self._in_quote:
            if tag == "div":
                self._depth += 1
            elif tag == "span" and "text" in classes:
                self._start_capture("text")
            elif tag == "small" and "author" in classes:
                self._start_capture("author")
            elif tag == "a" and "tag" in classes:
                self._start_capture("tag")
        elif tag == "div" and "quote" in classes:
            self._in_quote = True
            self._depth = 1
            self._row = {"text": "", "author": "", "tags": []}

    def handle_endtag(self, tag: str) -> None:  # noqa: ANN001
        if tag == "li":
            self._in_next_li = False
        if self._capture is not None:
            closing = {"text": "span", "author": "small", "tag": "a"}[self._capture]
            if tag == closing:
                self._flush_capture()
        if self._in_quote and tag == "div":
            self._depth -= 1
            if self._depth <= 0:
                self._flush_capture()
                if self._row is not None and (self._row["text"] or self._row["author"]):
                    self.rows.append(self._row)
                self._row = None
                self._in_quote = False

    def handle_data(self, data: str) -> None:  # noqa: ANN001
        if self._capture is not None:
            self._buf.append(data)

    # -- 内部 -------------------------------------------------------------
    def _start_capture(self, kind: str) -> None:
        self._capture = kind
        self._buf = []

    def _flush_capture(self) -> None:
        if self._capture is None:
            return
        value = " ".join("".join(self._buf).split())
        if self._row is not None:
            if self._capture == "text":
                self._row["text"] = value
            elif self._capture == "author":
                self._row["author"] = value
            else:
                if value:
                    self._row["tags"].append(value)
        self._capture = None
        self._buf = []


def _fetch_page(url: str, attempts: int = MAX_ATTEMPTS) -> str:
    """取页面 HTML；有限重试后仍失败则抛 RuntimeError（不返回假数据）。"""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "User-Agent": USER_AGENT,
                },
            )
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except Exception as exc:  # noqa: BLE001 — 收敛为有限重试
            last_error = exc
            if attempt < attempts:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(
        f"quotes page unreachable after {attempts} attempts: "
        f"{type(last_error).__name__}: {last_error}"
    )


def _parse_page(html: str, page_url: str, scraped_at: str) -> tuple[list[dict], str | None]:
    parser = _QuotesParser()
    parser.feed(html)
    parser.close()
    rows = [
        {
            "text": item["text"],
            "author": item["author"],
            "tags": ",".join(item["tags"]),
            "url": page_url,
            "source": SOURCE,
            "scraped_at": scraped_at,
        }
        for item in parser.rows
    ]
    next_url = urljoin(page_url, parser.next_href) if parser.next_href else None
    return rows, next_url


def run_quotes_toscrape_com(limit: int = 100) -> list[dict]:
    """Entry point for fd-open-data-protocol dispatch."""
    if limit is not None and int(limit) <= 0:
        return []
    wanted = int(limit) if limit is not None else 100

    scraped_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    visited: set[str] = set()
    url: str | None = SOURCE_URL
    pages = 0

    while url and pages < MAX_PAGES and len(rows) < wanted:
        pages += 1
        visited.add(url)
        html = _fetch_page(url)
        page_rows, next_url = _parse_page(html, url, scraped_at)
        if not page_rows:
            raise RuntimeError(
                f"no quote blocks parsed from {url} "
                "(site structure may have changed — fail loudly, no fake rows)"
            )
        rows.extend(page_rows)
        url = next_url if next_url and next_url not in visited else None

    if not rows:
        raise RuntimeError("no quotes fetched (fail loudly, no fake rows)")
    return rows[:wanted]
