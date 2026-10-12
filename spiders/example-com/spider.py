"""example-com — 示例站公开页面标题列表取数单元（wire 需求单生成单 / 冒烟 drill）。

来源：https://example.com/ —— IANA 保留的公开示例站（RFC 2606），单页静态 HTML。
本单元由 wire 需求单 `DE-20261012-99` 生成（demand-orchestration 生产验证 drill，
客户代号 `drill-wa`），brief.expectations 点名「抓取示例站公开页面标题列表」。

字段口径：
- title：页面 `<title>` 文本，原样透传（不改写、不映射、不补默认值）；
- url：该标题所属页面的绝对 URL（供下游溯源）；
- source：常量站点标签 `example.com`；
- scraped_at：取数时刻 UTC ISO8601。

抓取口径（只浅一层）：
- 种子 = https://example.com/；
- 只跟随同 host（example.com / www.example.com）的 `<a href>`，去重后按发现顺序取前 limit 条；
- 外链（如 https://www.iana.org/domains/example）不入结果集——本单元只交付示例站自身的公开页面；
- 页面无 `<title>` → 该页跳过，不补占位标题。

坑位与容错：
- **只依赖标准库**（urllib + html.parser）：静态 HTML、无 JS 渲染需求，既不新增依赖
  （新依赖 = 发版决策），也少一层解析漂移。
- **网络层只做容错、不做对抗**：15s 超时 + 至多 3 次尝试（退避 2s/4s）；不伪装指纹、
  不做频次对抗、不做协议破解。
- **失败不伪造行**：种子页不可达 / 非 HTML / 无 `<title>` → 抛 `RuntimeError`，交验证链判红
  并转人工；浅层的次级页面取数失败只跳过该页（不拖垮整单），也不产出占位行。
- 未写 `schedule`（静默合入，点亮由人工补写，`brief.cadence` = daily）；
- 未写 `site`（走默认执行位；sites.yaml 是执行位登记，与源站无关）。

入口：`run_example_com(limit=100) -> list[dict]`
"""
from __future__ import annotations

import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

SEED_URL = "https://example.com/"
SITE_HOSTS = ("example.com", "www.example.com")
SOURCE = "example.com"
TIMEOUT_SECONDS = 15
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2
USER_AGENT = "finddata-spider/1.0 (+https://finddatatech.cloud)"


class _PageParser(HTMLParser):
    """只抽 `<title>` 文本与 `<a href>`，不做 DOM 重建（够用即止，少解析漂移）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._title_parts: list[str] = []
        self._in_title = False
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        elif tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self.links.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)

    @property
    def title(self) -> str:
        return "".join(self._title_parts).strip()


def _fetch_html(url: str, attempts: int = MAX_ATTEMPTS) -> str:
    """取页面 HTML；有限重试后仍失败则抛 RuntimeError（不返回假数据）。"""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(
                url, headers={"Accept": "text/html", "User-Agent": USER_AGENT}
            )
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except Exception as exc:  # noqa: BLE001 — 收敛为有限重试
            last_error = exc
            if attempt < attempts:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(
        f"page unreachable after {attempts} attempts: {url}: "
        f"{type(last_error).__name__}: {last_error}"
    )


def _same_site_links(base_url: str, hrefs: list[str]) -> list[str]:
    """把 href 归一成绝对 URL，只留同站、去重，保序。"""
    out: list[str] = []
    for href in hrefs:
        absolute = urllib.parse.urljoin(base_url, href.strip())
        parts = urllib.parse.urlsplit(absolute)
        if parts.scheme not in ("http", "https"):
            continue
        if parts.netloc.lower() not in SITE_HOSTS:
            continue
        clean = urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path or "/", parts.query, "")
        )
        if clean not in out:
            out.append(clean)
    return out


def _parse_page(url: str) -> tuple[str, list[str]]:
    """返回 (title, 同站链接列表)；页面结构异常交由调用方处置。"""
    parser = _PageParser()
    parser.feed(_fetch_html(url))
    parser.close()
    return parser.title, _same_site_links(url, parser.links)


def _row(title: str, url: str) -> dict:
    return {
        "title": title,
        "url": url,
        "source": SOURCE,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def run_example_com(limit: int = 100) -> list[dict]:
    """Entry point for fd-open-data-protocol dispatch."""
    if limit is not None and int(limit) <= 0:
        return []
    limit = int(limit)

    seed_title, links = _parse_page(SEED_URL)
    if not seed_title:
        raise RuntimeError(f"seed page has no <title>: {SEED_URL}")

    rows = [_row(seed_title, SEED_URL)]
    seen = {SEED_URL}
    for link in links:
        if len(rows) >= limit:
            break
        if link in seen:
            continue
        seen.add(link)
        try:
            title, _ = _parse_page(link)
        except RuntimeError:
            # 浅抓容错：单个次级页面失败只跳过该页（种子页失败才判红）
            continue
        if not title:
            continue
        rows.append(_row(title, link))

    return rows[:limit]
