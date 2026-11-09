"""spiders/pboc-loan-usage —— 中国人民银行 金融机构贷款投向统计报告（季度）。

数据表面（2026-11-07 侦察 + 首次实爬，全部免 key 直连）：

- 目录页 ``https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html``，
  分页 ``.../113469/11040-<n>.html``（页内 ``11040-<max>.html`` 给出末页号，实测 413 页）。
- 目录是**混合新闻流**（行长会见、公告、金融市场运行情况…），贷款投向报告按季度
  混在其中——只能按标题匹配 ``贷款投向统计报告``，不能按位次或 URL 形态。
- 单篇 ``.../113469/<docid>/index.html``，``<docid>`` 实测为 19 位数字串
  （如 ``2026102815121914211``）；正文在 ``<div id="zoom">`` 内。

正文抽取坑位（实测）：

- **脚注标记是 ``<sup>`` 元素**：``普惠小微贷款<sup>1</sup>余额38.50万亿元``。
  必须先删 ``<sup>…</sup>`` 再转文本，否则 ``贷款 1 余额`` 会把数值正则打断
  （且不能靠「删所有孤立数字」规避——``500万元``、``2026年`` 会被误伤）。
- **发布日期取 ``<meta name="PubDate" content="YYYY-MM-DD">``**；URL 里的
  ``2026102815121914211`` 是文章 id 内嵌时间戳，与发布日同源但格式不可靠
  （迁移件时间戳会失真，侦察簿路 3 坑位），故不解析 URL。
- 标题口径漂移：``2026年三季度金融机构贷款投向统计报告``；历史期含
  ``上半年/一季度/年度`` 等写法 → 期次解析覆盖 Q1..Q4 / H1 / 全年。
- 「增长/下降」必须带符号：房地产贷款是**同比下降 5.2%**，若只取绝对值会把
  收缩写成增长（本单元 growth 规则带 sign 分支，下降记负数）。

容错：单篇失败/期次不可解析/正文抽空 → 跳过并留痕，不阻塞其它期；
全部失败 → ``RuntimeError``（失败即红，绝不静默回空表）；抽不到的指标不出行
（绝不用 0 占位）；``limit`` 真实生效。

只依赖标准库：``urllib/json/re/datetime``。
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone

SOURCE = "pboc-loan-usage"
BASE = "https://www.pbc.gov.cn"
LISTING_URL = BASE + "/goutongjiaoliu/113456/113469/index.html"
PAGING_URL = BASE + "/goutongjiaoliu/113456/113469/11040-{page}.html"

TIMEOUT = 30
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": BASE + "/goutongjiaoliu/113456/113469/index.html",
}

# 目录回溯上限（每页 15 条混合新闻，季度报告每页约 0-2 条）。取到足够期次即止。
MAX_LISTING_PAGES = 8

TITLE_MARK = "贷款投向统计报告"

_CN_Q = {"一": 1, "二": 2, "三": 3, "四": 4}


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for {url}")
        return resp.read().decode("utf-8", "replace")


# ------------------------------------------------------------------ 目录发现

_LINK_RE = re.compile(
    r'<a\s[^>]*href="(/goutongjiaoliu/113456/113469/\d+/index\.html)"[^>]*>(.*?)</a>',
    re.S)
_TOTALPAGE_RE = re.compile(r"11040-(\d+)\.html")
_TAG_RE = re.compile(r"<[^>]+>")


def _period_of(title: str) -> str | None:
    """标题 → 期次 ``YYYYQn`` / ``YYYYH1`` / ``YYYY``。"""
    m = re.search(r"(\d{4})年", title)
    if not m:
        return None
    year = m.group(1)
    mq = re.search(r"([一二三四])季度", title)
    if mq:
        return f"{year}Q{_CN_Q[mq.group(1)]}"
    if "上半年" in title:
        return f"{year}H1"
    if "年度" in title or re.search(r"\d{4}年金融机构贷款投向", title):
        return year
    return None


def discover_reports(max_pages: int = MAX_LISTING_PAGES) -> tuple[list[dict], list[str]]:
    """目录回溯 → 报告清单（新→旧，按期次去重）+ 留痕。"""
    traces: list[str] = []
    seen: dict[str, dict] = {}
    total_pages = 1
    for page in range(1, max_pages + 1):
        url = LISTING_URL if page == 1 else PAGING_URL.format(page=page)
        try:
            html = _get(url)
        except Exception as exc:  # noqa: BLE001 - 单页失败只跳过留痕
            traces.append(f"listing p{page}: {type(exc).__name__}: {exc}"[:160])
            continue
        if page == 1:
            pages = [int(n) for n in _TOTALPAGE_RE.findall(html)]
            total_pages = max(pages) if pages else 1
        for href, raw_title in _LINK_RE.findall(html):
            title = _TAG_RE.sub("", raw_title).strip()
            if TITLE_MARK not in title:
                continue
            period = _period_of(title)
            if period is None:
                traces.append(f"period unparsable: {title}"[:160])
                continue
            if period in seen:
                continue
            seen[period] = {
                "url": href if href.startswith("http") else BASE + href,
                "title": title,
                "period": period,
            }
        if page >= total_pages:
            break
    reports = sorted(seen.values(), key=lambda r: r["period"], reverse=True)
    if not reports:
        traces.append("directory discovery found no 贷款投向统计报告 entries")
    return reports, traces


# ------------------------------------------------------------------ 正文抽取


def zoom_text(html: str) -> str:
    """``<div id="zoom">`` 正文纯文本；先删 ``<sup>`` 脚注再剥标签（关键坑位）。"""
    html = re.sub(r"<sup\b.*?</sup\s*>", "", html, flags=re.S)
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
    text = (seg.replace("&nbsp;", " ").replace("&ensp;", " ")
               .replace("&#160;", " ").replace("&amp;", "&"))
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", text)).strip()


def _published(html: str) -> str | None:
    m = re.search(r'<meta\s+name="PubDate"\s+content="(\d{4}-\d{2}-\d{2})"', html)
    return m.group(1) if m else None


# ---- 指标规则 -------------------------------------------------------------
# (indicator, unit, pattern) —— level 型：单捕获组为数值。
_LEVEL_RULES: tuple[tuple[str, str, str], ...] = (
    ("人民币各项贷款余额", "万亿元", r"人民币各项贷款余额\s*([\d,]+(?:\.\d+)?)\s*万亿元"),
    ("本外币企事业单位贷款余额", "万亿元",
     r"本外币企事业单位贷款余额\s*([\d,]+(?:\.\d+)?)\s*万亿元"),
    ("普惠小微贷款余额", "万亿元",
     r"普惠小微贷款\s*余额\s*([\d,]+(?:\.\d+)?)\s*万亿元"),
    ("涉农贷款余额", "万亿元",
     r"本外币涉农贷款\s*余额\s*([\d,]+(?:\.\d+)?)\s*万亿元"),
    ("农村贷款余额", "万亿元", r"农村贷款余额\s*([\d,]+(?:\.\d+)?)\s*万亿元"),
    ("农户贷款余额", "万亿元", r"农户贷款余额\s*([\d,]+(?:\.\d+)?)\s*万亿元"),
    ("房地产贷款余额", "万亿元",
     r"人民币房地产贷款余额\s*([\d,]+(?:\.\d+)?)\s*万亿元"),
    ("房地产开发贷款余额", "万亿元",
     r"房地产开发贷款余额\s*([\d,]+(?:\.\d+)?)\s*万亿元"),
    ("个人住房贷款余额", "万亿元",
     r"个人住房贷款余额\s*([\d,]+(?:\.\d+)?)\s*万亿元"),
    ("绿色贷款余额", "万亿元", r"本外币绿色贷款余额\s*([\d,]+(?:\.\d+)?)\s*万亿元"),
    ("工业中长期贷款余额", "万亿元",
     r"本外币工业中长期贷款余额\s*([\d,]+(?:\.\d+)?)\s*万亿元"),
    ("基础设施相关行业中长期贷款余额", "万亿元",
     r"基础设施相关行业中长期贷款余额\s*([\d,]+(?:\.\d+)?)\s*万亿元"),
)

# growth 型：(indicator, 锚点正则片段, 单位=%) —— 捕获 sign + 数值，下降记负数。
_GROWTH_RULES: tuple[tuple[str, str], ...] = (
    ("人民币各项贷款同比增速", r"人民币各项贷款余额\s*[\d,]+\s*万亿元"),
    ("普惠小微贷款同比增速", r"普惠小微贷款\s*余额\s*[\d,]+\s*万亿元"),
    ("涉农贷款同比增速", r"本外币涉农贷款\s*余额\s*[\d,]+\s*万亿元"),
    ("房地产贷款同比增速", r"人民币房地产贷款余额\s*[\d,]+\s*万亿元"),
    ("房地产开发贷款同比增速", r"房地产开发贷款余额\s*[\d,]+\s*万亿元"),
    ("绿色贷款同比增速", r"本外币绿色贷款余额\s*[\d,]+\s*万亿元"),
)

_GROWTH_RE = re.compile(
    r"同比(?P<sign>增长|下降|持平)\s*(?P<v>[\d,]+(?:\.\d+)?)\s*%")


def _num(raw: str) -> float | None:
    text = (raw or "").replace(",", "").replace("，", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _growth_after(text: str, anchor_pattern: str) -> float | None:
    """锚点之后 60 字符内的「同比增/降 X%」；下降记负数，持平记 0。"""
    m = re.search(anchor_pattern, text)
    if not m:
        return None
    tail = text[m.end():m.end() + 60]
    g = _GROWTH_RE.search(tail)
    if not g:
        return None
    value = _num(g.group("v"))
    if value is None:
        return None
    sign = g.group("sign")
    return -value if sign == "下降" else (0.0 if sign == "持平" else value)


def parse_rows(text: str, period: str, title: str, url: str,
               published_at: str | None) -> list[dict]:
    """正文 → 指标行（抽不到的不出行，绝不填 0）。"""
    rows: list[dict] = []
    for indicator, unit, pattern in _LEVEL_RULES:
        m = re.search(pattern, text)
        value = _num(m.group(1)) if m else None
        if value is None:
            continue
        rows.append({"indicator": indicator, "unit": unit, "value": value})
    for indicator, anchor in _GROWTH_RULES:
        value = _growth_after(text, anchor)
        if value is None:
            continue
        rows.append({"indicator": indicator, "unit": "%", "value": value})
    return [
        {**row, "period": period, "report_title": title, "report_url": url,
         "published_at": published_at or ""}
        for row in rows
    ]


# ------------------------------------------------------------------ 编排


def _collect(limit: int) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    traces: list[str] = []
    reports, traces = discover_reports()

    for report in reports:
        if len(rows) >= limit:
            traces.append(f"limit reached ({limit}) at {report['period']}")
            break
        try:
            html = _get(report["url"])
            text = zoom_text(html)
            parsed = parse_rows(text, report["period"], report["title"],
                                report["url"], _published(html))
        except Exception as exc:  # noqa: BLE001 - 单篇失败跳过，不阻塞其它期
            traces.append(f"{report['period']}: {type(exc).__name__}: {exc}"[:180])
            continue
        if not parsed:
            traces.append(f"{report['period']}: no indicator matched in body")
            continue
        for row in parsed:
            rows.append({**row, "source": SOURCE, "scraped_at": now})
            if len(rows) >= limit:
                break

    if traces:
        print(f"[{SOURCE}] traces({len(traces)}): " + " | ".join(traces[:8]))  # noqa: T201
    if not rows:
        raise RuntimeError(f"{SOURCE}: 全部报告取数失败 — " + "; ".join(traces)[:300])
    return rows[:limit]


def run_pboc_loan_usage(limit: int = 60) -> list[dict]:
    """取央行季度《金融机构贷款投向统计报告》指标行（新→旧）。

    Args:
        limit: 返回最大行数（真实生效；按期次倒序逐期取，取满即止）。

    Returns:
        行字典列表，键见 manifest ``functions[].columns``。

    Raises:
        ValueError: ``limit`` 非法。
        RuntimeError: 全部期次均未取到指标。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []
    return _collect(limit)


if __name__ == "__main__":  # 冒烟：python3 spiders/pboc-loan-usage/spider.py
    print(json.dumps(run_pboc_loan_usage(limit=20), ensure_ascii=False, indent=2))
