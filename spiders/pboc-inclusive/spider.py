"""spiders/pboc-inclusive —— 中国人民银行 普惠金融口径统计取数。

与既有 ``pboc-monetary`` 的关系（change ``inclusive-finance-sources`` 设计决策 D2）：
``pboc-monetary`` 读 ``116219/116319`` 年度栏目下的 xlsx 附件（社融流量/存量 + M0/M1/M2），
本单元读 ``116219/116225``「数据解读」扁平目录里的**月度/季度 HTML 报告正文**，形态不同，
故新建独立单元、不合并、不修改 ``pboc-monetary``。

三件数据（同一扁平目录页，按标题正则分类）：

- **A 小额贷款公司统计数据报告**（季度）→ 机构数（家）、贷款余额（亿元）。
  正文形如 ``截至2025年9月末，全国共有小额贷款公司4863家。贷款余额7229亿元。``
- **B 金融统计数据报告**（月度）→ 住户贷款结构、人民币贷款/存款余额、社融存量、M0/M1/M2。
  正文为 ``<div id="zoom">`` 内的若干 ``<p>``，含“增加/减少”方向呾与时点/时期两种口径。
- **C 地区社会融资规模增量统计表**（季度）→ 31 个省区的地区社融增量（亿元）。
  正文只有一行“附件：…xlsx”，须二段取数：先从正文抽出 xlsx 直链，再取附件解析。

目录发现（必做，绝不硬编码单篇 docid）：

- 目录页 ``116225/index.html`` + 分页 ``116225/11871-<n>.html``（``totalpage`` 自页内读出，
  实测 38 页 / ~760 条）。按标题正则分三类后**逐页回溯**，取到 enough 即止。
- 单篇 URL 形如 ``/diaochatongjisi/116219/116225/<docid>/index.html``，``<docid>`` 既可能是
  纯数字（``5885724``）也可能是 32 位 hash（``dded7449c74144a19171f582bec9ed36``）。

口坑（侦察实测，逐一落实）：

- **站点对 UA/TLS 指纹敏感**：裸 UA 会 302 或被拦。传输一律走 scrapling 的
  ``FetcherSession(impersonate="chrome")``（curl_cffi 引擎，Chrome 指纹），与
  carbon-monitor / chinawuliu-logistics / sc-gd-stats / nbs_gdp 四个存量单元同法。
  ``scrapling`` 不可用时**降级**到带常规 Chrome UA 的 ``urllib``——仍可能可用，但
  属降级路径，取不到即按网络失败留痕跳过，绝不重试放大、不做指纹对抗以外的绕过。
- **数字带千分位逗号**：解析成数值前剥离 ``,`` 与全角 ``，``。
- **页面含大量导航噪音**：正文必须定位到 ``<div id="zoom">`` 并做 div 配平闭合，
  而不是全文正则（否则会被导航/附表标题/注脚污染）。
- **“增加/减少”共用句式**：``住户存款增加6.99万亿元`` 与 ``住户贷款减少1.03万亿元``
  写法一致，仅动词不同；本单元**只取绝对值**（时期流量口径），方向不入 value，避免伪精确。
- **xlsx 解析经平台共享层** ``fd_industry_data.tabular``（2026-10-13 回接，原为单元内手写）。

容错（必须）：单篇报告失败跳过并留痕，不阻塞其他报告；指标抽不到则整篇跳过，
绝不用 0/占位值填充；网络失败记录后跳过，不连环重试、不绕过反爬；``limit`` 真实生效。

只依赖标准库 + 共享层：``urllib/json/re/datetime/html`` + ``fd_industry_data.tabular``。
"""
from __future__ import annotations

import asyncio
import io
import json
import re
import urllib.request
from datetime import datetime, timezone

from fd_industry_data.tabular import read_grid

try:  # 首选通道：Chrome 指纹（站点对 UA/TLS 指纹敏感，裸 UA 会 302 或被拦）
    from scrapling.fetchers import FetcherSession
except ImportError:  # pragma: no cover - 降级路径，见模块 docstring
    FetcherSession = None  # type: ignore[assignment]

SOURCE = "pboc-inclusive"
BASE = "https://www.pbc.gov.cn"
LISTING_URL = BASE + "/diaochatongjisi/116219/116225/index.html"
PAGING_URL = BASE + "/diaochatongjisi/116219/116225/11871-{page}.html"

TIMEOUT_HTML = 30  # 秒；正文页约 27–45KB
TIMEOUT_XLSX = 40  # 秒；附表 xlsx 约 26KB

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 每次入库的目录回溯页数上限（每页 20 条）。第一页通常已覆盖近 12 个月 +
# 最近 2 个季度的全部三件，取不到再往下翻。
MAX_LISTING_PAGES = 6

CAT_MICRO = "小额贷款公司统计"
CAT_MONTHLY = "金融统计月报"
CAT_REGIONAL = "地区社融"


_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4}


# ------------------------------------------------------------------ 传输层


class Http:
    """Chrome 指纹取数器：一次 run 共用一条会话，单次尝试即放弃（不重试放大）。"""

    def __init__(self) -> None:
        self._session = None  # scrapling 会话（async，懒开）
        self.mode = "curl_cffi(chrome)" if FetcherSession else "urllib(chrome-UA)"

    async def _open(self):
        if FetcherSession is None:
            return None
        if self._session is None:
            self._session = FetcherSession(
                impersonate="chrome", timeout=TIMEOUT_HTML, verify=False, retries=1)
            self._session = await self._session.__aenter__()
        return self._session

    async def _get(self, url: str, timeout: int) -> bytes:
        session = await self._open()
        if session is not None:
            resp = await session.get(url, timeout=timeout, follow_redirects=True)
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status} for {url}")
            return resp.body or b""
        # 降级：带常规 Chrome UA 的 urllib（仍可能被拦；失败即留痕跳过）
        req = urllib.request.Request(url, headers=HEADERS)
        return await asyncio.to_thread(
            lambda: urllib.request.urlopen(req, timeout=timeout).read())  # noqa: S310

    async def html(self, url: str) -> str:
        return (await self._get(url, TIMEOUT_HTML)).decode("utf-8", "replace")

    async def bytes(self, url: str) -> bytes:
        return await self._get(url, TIMEOUT_XLSX)

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.__aexit__(None, None, None)
            self._session = None


def _get(url: str, timeout: int = TIMEOUT_HTML) -> bytes:
    """同步便捷入口（冒烟/单测用）：单次 run 一把会话，取完即关。"""
    async def _once() -> bytes:
        http = Http()
        try:
            return await http._get(url, timeout)
        finally:
            await http.aclose()
    return asyncio.run(_once())


def _get_html(url: str) -> str:
    return _get(url, TIMEOUT_HTML).decode("utf-8", "replace")


# ------------------------------------------------------------------ 目录发现


_LISTING_LINK_RE = re.compile(
    r'<a\s[^>]*href="([^"]*?/116225/[^"]*?/index\.html)"[^>]*>(.*?)</a>', re.S)
_TOTALPAGE_RE = re.compile(r'totalpage="(\d+)"')
_TAG_RE = re.compile(r"<[^>]+>")


async def _listing_page(http: Http, page: int) -> tuple[int, list[tuple[str, str]]]:
    """取一页目录 → ``(totalpage, [(url, title), ...])``。"""
    html = await http.html(LISTING_URL if page == 1 else PAGING_URL.format(page=page))
    m = _TOTALPAGE_RE.search(html)
    total = int(m.group(1)) if m else 1
    items: list[tuple[str, str]] = []
    for lm in _LISTING_LINK_RE.finditer(html):
        url = lm.group(1)
        url = url if url.startswith("http") else BASE + url
        title = _TAG_RE.sub("", lm.group(2)).strip()
        if title:
            items.append((url, title))
    return total, items


def _category_of(title: str) -> str | None:
    """标题 → 三类之一；不在三件范围内返回 ``None``（如社融增量/存量报告）。"""
    if "小额贷款公司" in title:
        return CAT_MICRO
    if "地区社会融资规模" in title:
        return CAT_REGIONAL
    if "金融统计数据报告" in title:
        return CAT_MONTHLY
    return None


_CATEGORY_ORDER = (CAT_MICRO, CAT_MONTHLY, CAT_REGIONAL)


async def discover_reports(http: Http,
                           max_pages: int = MAX_LISTING_PAGES) -> tuple[list[dict], list[str]]:
    """目录页发现条目 → 报告清单（同类内保持目录倒序）+ 留痕。

    目录天然按发布时间倒序，故同类第一份即最新一期，无需再按标题排序。
    """
    traces: list[str] = []
    by_cat: dict[str, list[tuple[str, str]]] = {c: [] for c in _CATEGORY_ORDER}
    total_pages = 1
    for page in range(1, max_pages + 1):
        try:
            total_pages, items = await _listing_page(http, page)
        except Exception as exc:  # noqa: BLE001 - 单页失败只跳过并留痕
            traces.append(f"listing page {page}: {type(exc).__name__}: {exc}"[:200])
            continue
        if not items:
            traces.append(f"listing page {page}: no entries parsed")
            continue
        for url, title in items:
            cat = _category_of(title)
            if cat:
                by_cat[cat].append((url, title))
        if page >= total_pages:
            break

    # 三件**轮询交错**（each category 取第 k 新的报告作为一轮），保证小 limit 时
    # 三类都有代表 row；若按类串行，limit 会被第一类吃满而饿死后两类。
    reports: list[dict] = []
    for k in range(max((len(v) for v in by_cat.values()), default=0)):
        for cat in _CATEGORY_ORDER:
            if k < len(by_cat[cat]):
                url, title = by_cat[cat][k]
                reports.append({"url": url, "title": title, "category": cat})
    if not reports:
        traces.append("directory discovery found no report entries")
    return reports, traces


# ------------------------------------------------------------------ 正文抽取


def zoom_text(html: str) -> str:
    """取 ``<div id="zoom">`` 的正文纯文本（div 配平，避开导航噪音）。"""
    m = re.search(r'<div\s+id="zoom"[^>]*>', html)
    if not m:
        raise ValueError("zoom container not found")
    start = m.end()
    depth, end = 1, len(html)
    for step in re.finditer(r"<div\b|</div\s*>", html[start:]):
        depth += 1 if step.group(0).startswith("<div") else -1
        if depth == 0:
            end = start + step.end()
            break
    seg = html[start:end]
    seg = re.sub(r"<script.*?</script>|<style.*?</style>", "", seg, flags=re.S)
    text = seg.replace("&nbsp;", " ").replace("&ensp;", " ").replace("&#160;", " ")
    text = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _num(raw: str) -> float | None:
    """``1,162,939`` → ``1162939.0``；非数值返回 ``None``（绝不猜 0）。"""
    text = (raw or "").replace(",", "").replace("，", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _period(title: str) -> str | None:
    """标题 → 期次：``2026-08`` / ``2026H1`` / ``2025Q3`` / ``2025``。"""
    m = re.match(r"^(\d{4})年", title)
    if not m:
        return None
    year = int(m.group(1))
    mv = re.search(r"(\d{4})年(\d{1,2})月", title)
    if mv:
        return "%04d-%02d" % (year, int(mv.group(2)))
    mq = re.search(r"([一二三四])季度", title)
    if mq:
        return "%04dQ%d" % (year, _CN_NUM[mq.group(1)])
    mh = re.search(r"(上|下)半年", title)
    if mh:
        return "%04dH%s" % (year, "1" if mh.group(1) == "上" else "2")
    return "%04d" % year


def _search(pattern: str, text: str) -> float | None:
    m = re.search(pattern, text)
    return _num(m.group(1)) if m else None


# (indicator, unit, pattern) —— 首个命中为准；抽不到即无该行（不用 0 填充）
_MICRO_RULES = (
    ("小额贷款公司机构数", "家", r"全国共有小额贷款公司([\d,，]+)家"),
    ("小额贷款公司贷款余额", "亿元", r"贷款余额([\d,，]+)亿元"),
    # 余额较年初/同比的变化量（时期口径）；句式「前三季度减少319亿元」，只取绝对值
    ("小额贷款公司贷款余额变化", "亿元",
     r"贷款余额[\d,，.]+亿元，\s*(?:前[一二三四]季度|前三季度|上半年|全年)"
     r"(?:增加|减少)([\d,，.]+)亿元"),
)

# 金融统计月报/季报/年报共用的正文句式（同一份模板，覆盖 2020–2026 实测全通过）
_MONTHLY_RULES = (
    ("住户贷款", "亿元", r"住户贷款(?:增加|减少)([\d,，]+(?:\.\d+)?)亿元"),
    ("住户短期贷款", "亿元",
     r"住户贷款(?:增加|减少)[\d,，.]+亿元，\s*其中，\s*短期贷款(?:增加|减少)([\d,，.]+)亿元"),
    ("住户中长期贷款", "亿元",
     r"住户贷款(?:增加|减少)[\d,，.]+亿元，\s*其中，\s*短期贷款(?:增加|减少)[\d,，.]+亿元，"
     r"\s*中长期贷款(?:增加|减少)([\d,，.]+)亿元"),
    ("人民币贷款余额", "万亿元", r"月末人民币贷款余额([\d,，.]+)万亿元"),
    ("本外币贷款余额", "万亿元", r"本外币贷款余额([\d,，.]+)万亿元"),
    ("人民币存款余额", "万亿元", r"月末人民币存款余额([\d,，.]+)万亿元"),
    ("住户存款", "万亿元", r"住户存款(?:增加|减少)([\d,，.]+)万亿元"),
    ("人民币贷款增量", "万亿元", r"人民币贷款增加([\d,，.]+)万亿元"),
    ("社会融资规模存量", "万亿元", r"社会融资规模存量(?:为|余额为)?([\d,，.]+)万亿元"),
    ("M2余额", "万亿元", r"广义货币[（(]M2[)）]余额([\d,，.]+)万亿元"),
    ("M1余额", "万亿元", r"狭义货币[（(]M1[)）]余额([\d,，.]+)万亿元"),
    ("M0余额", "万亿元", r"流通中货币[（(]M0[)）]余额([\d,，.]+)万亿元"),
)

_CATEGORY_RULES = {
    CAT_MICRO: _MICRO_RULES,
    CAT_MONTHLY: _MONTHLY_RULES,
}


# ------------------------------------------------------- C · 地区社融 xlsx


_PROVINCES = (
    "北京 天津 河北 山西 内蒙古 辽宁 吉林 黑龙江 上海 江苏 浙江 安徽 福建 江西 山东 河南 "
    "湖北 湖南 广东 广西 海南 重庆 四川 贵州 云南 西藏 陕西 甘肃 青海 宁夏 新疆"
).split()
_PROVINCE_SET = set(_PROVINCES)


async def _regional_rows(http: Http, html: str, page_url: str, period: str,
                         title: str) -> list[dict]:
    """C 类：正文里只有“附件：…xlsx”，数值在附表 xlsx 的第 1 列。"""
    links = re.findall(r'href="([^"]+\.xlsx)"', html)
    if not links:
        raise ValueError("no xlsx attachment link in body")
    href = links[0]
    xlsx_url = href if href.startswith("http") else BASE + (href if href.startswith("/") else "/" + href)
    grid = read_grid(await http.bytes(xlsx_url))

    # 单位从表头「单位：…」格实读（实测「单位：亿元人民币」）；读不到才回落亿元
    unit = "亿元"
    for row in grid:
        for c in row:
            if isinstance(c, str) and "单位" in c:
                unit = "万亿元" if "万亿元" in c else ("亿元" if "亿元" in c else unit)
                break
        else:
            continue
        break

    rows: list[dict] = []
    for row in grid:
        if len(row) < 2 or not isinstance(row[0], str):
            continue
        label = row[0].strip()
        province = label.split(" ")[0]
        if province not in _PROVINCE_SET:
            continue
        value = _num(row[1])
        if value is None:
            continue  # 该行无数值 → 跳过，不用 0 填充
        rows.append({
            "indicator": "地区社会融资规模增量",
            "period": period,
            "value": value,
            "unit": unit,
            "category": CAT_REGIONAL,
            "region": province,
            "report_title": title,
            "report_url": page_url,
        })
    if not rows:
        raise ValueError(f"no province rows parsed from xlsx {xlsx_url}")
    return rows


# ------------------------------------------------------------------ 编排


async def _parse_report(http: Http, report: dict, now: str) -> tuple[list[dict], str | None]:
    """单篇报告 → (行, 失败原因)。失败不抛，交由调用方留痕。"""
    url, title, cat = report["url"], report["title"], report["category"]
    period = _period(title)
    if period is None:
        return [], f"{title}: period unparsable"

    if cat == CAT_REGIONAL:
        try:
            html = await http.html(url)
            return await _regional_rows(http, html, url, period, title), None
        except Exception as exc:  # noqa: BLE001
            return [], f"{title}: {type(exc).__name__}: {exc}"[:200]

    text = zoom_text(await http.html(url))
    rows: list[dict] = []
    for indicator, unit, pattern in _CATEGORY_RULES[cat]:
        value = _search(pattern, text)
        if value is None:
            continue  # 该指标本篇未出现 → 无该行（不用占位值）
        rows.append({
            "indicator": indicator,
            "period": period,
            "value": value,
            "unit": unit,
            "category": cat,
            "region": "全国",
            "report_title": title,
            "report_url": url,
        })
    if not rows:
        return [], f"{title}: no indicator matched in body"
    return rows, None


async def _collect(limit: int) -> list[dict]:
    http = Http()
    now = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    traces: list[str] = []
    try:
        reports, traces = await discover_reports(http)
        for report in reports:
            if len(rows) >= limit:
                traces.append(f"limit reached ({limit}), stop at {report['title']}")
                break
            try:
                parsed, err = await _parse_report(http, report, now)
            except Exception as exc:  # noqa: BLE001 - 单篇失败跳过，不阻塞其他报告
                traces.append(f"{report['title']}: {type(exc).__name__}: {exc}"[:200])
                continue
            if err:
                traces.append(err)
                continue
            for row in parsed:
                rows.append({**row, "source": SOURCE, "scraped_at": now})
                if len(rows) >= limit:
                    break
    finally:
        await http.aclose()

    if traces:
        # 留痕：交给运行日志/巡检，不进业务行
        print(f"[pboc-inclusive] traces({len(traces)}): " + " | ".join(traces[:8]))  # noqa: T201
    if not rows:
        raise RuntimeError("pboc-inclusive: 全部报告取数失败 — " + "; ".join(traces)[:300])
    return rows[:limit]


def run_pboc_inclusive(limit: int = 100) -> list[dict]:
    """取央行普惠金融口径三件的最新指标行；全失败 → ``RuntimeError``。

    Args:
        limit: 返回的最大行数（真实生效；按目录倒序取最新的报告）。

    Returns:
        行字典列表，键见 manifest ``functions[].columns``。

    Raises:
        ValueError: ``limit`` 非法。
        RuntimeError: 全部报告均未取到指标（失败即红，不外泄静默空表）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []
    return asyncio.run(_collect(limit))


if __name__ == "__main__":  # 冒烟：python3 spiders/pboc-inclusive/spider.py
    print(json.dumps(run_pboc_inclusive(limit=8), ensure_ascii=False, indent=2))
