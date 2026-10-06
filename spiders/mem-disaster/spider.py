"""spiders/mem-disaster —— 应急管理部（MEM）月度灾情通报取数。

数据表面（公开 HTML 栏目，无鉴权；工单 brief.notes「侦察簿 W3」）：

- 栏目列表： ``GET https://www.mem.gov.cn/gk/tjsj/`` → HTML，正文内联每月一篇
  「XX月全国自然灾害情况」通报链接（新→旧，首页约 19 个月度通报；翻页页为 JS 渲染壳，
  本单元只取首页）。
- 通报正文： 每条链接指向 ``/xw/yjglbgzdt/<yyyymm>/t*.shtml`` 文章页，正文含汇总句
  （受灾人次 / 死亡失踪 / 直接经济损失）。

口坑（brief.notes + 2026-10-06 实测，逐一落实）：

- **栏目 HTML 表正则解析**：列表项 ``<a href=…>标题<span>发布时间</span></a>``，
  标题正则 ``发布(\\d{4})年(\\d{1,2})月全国自然灾害情况`` 识别月度通报——半年/季度/
  全年/十大等特刊天然不匹配，全部排除（本单元只出月度行）。
- **响应 gzip**：部分文章页（尤其旧文）**不协商直接回 gzip**（无 ``Accept-Encoding``
  也压缩），统一按魔数 ``1f 8b`` 嗅探后用标准库 ``gzip`` 解压，两种形态都兼容。
- **月中发布——当月未出属正常容错**：默认只解析列表上**有**的通报；``month`` 参数
  指定的月份若不在首页列表（当月未发布或已翻出首页）→ 该月 0 行并抛 ``RuntimeError``
  （确定性红，留给人工/下次重试），绝不造数。
- 指标口径：正文**第一个**同时含「受灾」与「经济损失」的句段即全国汇总句（分灾种明细
  在其后，不取）；受灾人次以「万人次」原值透出，直接经济损失统一归一到「亿元」
  （原文「近4800万元」→ 0.48，修饰词「近/约/超/达」舍入留痕见 README）。
- ``limit`` 语义 = 最多解析的通报篇数（列表序，新→旧）；单篇失败只跳过并留痕，
  **全部篇目失败抛 ``RuntimeError``**（失败即红）。

工单：``reports/health-tickets/20261006-mem-disaster-eccf1032.yaml``（``kind=generate``）
"""
from __future__ import annotations

import gzip
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SOURCE = "mem-disaster"
LIST_URL = "https://www.mem.gov.cn/gk/tjsj/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.mem.gov.cn/",
}
TIMEOUT = 30  # 秒；单页 HTML ~10KB，超时即跳过该篇（不重试、不加频次）

LISTING_ITEM_RE = re.compile(
    r'<a[^>]+href="([^"]+\.shtml)"[^>]*>\s*([^<>]+?)\s*'
    r"<span>\s*([\d\- :]+?)\s*</span>\s*</a>", re.S)
MONTHLY_TITLE_RE = re.compile(r"发布(\d{4})年(\d{1,2})月全国自然灾害情况")
AFFECTED_RE = re.compile(r"共造成(?:全国)?([\d\.]+)万人次(?:不同程度)?受灾")
DEATHS_RE = re.compile(r"死亡失踪([\d\.]+)人")
LOSS_YI_RE = re.compile(r"直接经济损失(?:约|近|超|达)?([\d\.]+)亿元")
LOSS_WAN_RE = re.compile(r"直接经济损失(?:约|近|超|达)?([\d\.]+)万元")


def _fetch(url: str) -> str:
    """GET 一个 HTML 页（魔数嗅探 gzip；无鉴权、无重试放大）。"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        raw = resp.read()
    if raw[:2] == b"\x1f\x8b":  # 部分页面不协商直接回 gzip
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def _collapse(html: str) -> str:
    """剥脚本/样式/标签并压缩所有空白（正文中文无空格，便于正则取数）。"""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", "", text)


def _num(match) -> float | None:
    try:
        return float(match.group(1))
    except (AttributeError, ValueError):
        return None


def parse_monthly_report(html: str) -> dict:
    """通报文章页 → 指标行（解析失败的字段为 ``None``，不造数）。"""
    text = _collapse(html)
    # 全国汇总句 = 第一个同时含「受灾」与「经济损失」的句段（分灾种明细在其后）
    summary = next(
        (seg for seg in text.split("。") if "受灾" in seg and "经济损失" in seg),
        text,
    )
    loss = _num(LOSS_YI_RE.search(summary))
    if loss is None:  # 小灾月份以「万元」计，归一到亿元
        wan = _num(LOSS_WAN_RE.search(summary))
        if wan is not None:
            loss = round(wan / 1e4, 4)
    return {
        "affected_person": _num(AFFECTED_RE.search(summary)),   # 万人次（原值）
        "deaths_missing": _num(DEATHS_RE.search(summary)),      # 人
        "economic_loss": loss,                                  # 亿元
        "summary": summary if summary is not text else None,
    }


def _listing_items(html: str) -> list[dict]:
    """栏目首页 → 月度通报条目（新→旧，保持列表序）。"""
    items: list[dict] = []
    for href, title, published in LISTING_ITEM_RE.findall(html):
        m = MONTHLY_TITLE_RE.search(title)
        if not m:
            continue  # 半年/季度/全年/十大等特刊不进月度行
        url = urllib.parse.urljoin(LIST_URL, href)
        items.append({
            "month": f"{int(m.group(1))}-{int(m.group(2)):02d}",
            "title": re.sub(r"\s+", "", title),
            "publish_time": published,
            "url": url,
        })
    return items


def run_mem_disaster(limit: int = 100, month: str | None = None) -> list[dict]:
    """取栏目首页的月度灾情通报行（≤ ``limit``）；``month='YYYY-MM'`` 过滤单月。

    - 列表/全部文章失败 → 抛 ``RuntimeError``（失败即红，不静默空返回）；
    - 单篇文章失败只跳过并留痕（偶发压缩/改版）；
    - 指定 ``month`` 无行 → 抛 ``RuntimeError``（当月未发布属正常，确定性红）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    html = _fetch(LIST_URL)
    items = _listing_items(html)
    if month is not None:
        items = [it for it in items if it["month"] == month]
        if not items:
            raise RuntimeError(
                f"mem-disaster: month {month} not on the listing page "
                "(当月未发布或已翻出首页属正常，绝不造数)")
    if not items:
        raise RuntimeError("mem-disaster: no monthly bulletins on the listing page")

    rows: list[dict] = []
    errors: list[str] = []
    scraped_at = datetime.now(timezone.utc).isoformat()
    for item in items:
        if len(rows) >= limit:
            break
        try:
            metrics = parse_monthly_report(_fetch(item["url"]))
        except Exception as exc:  # noqa: BLE001 - 单篇失败只跳过并留痕
            errors.append(f"{item['month']}: {type(exc).__name__}: {exc}")
            continue
        rows.append({**item, **metrics,
                     "source": SOURCE, "scraped_at": scraped_at})
    if not rows:
        raise RuntimeError("mem-disaster: 全部通报解析失败 — " + "; ".join(errors)[:300])
    return rows[:limit]


if __name__ == "__main__":  # 单页冒烟：python3 spiders/mem-disaster/spider.py
    print(json.dumps(run_mem_disaster(limit=2), ensure_ascii=False, indent=2))
