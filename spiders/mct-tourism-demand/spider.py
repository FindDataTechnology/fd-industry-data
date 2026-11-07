"""spiders/mct-tourism-demand —— 文化和旅游部「国内居民出游数据」季度稿 + 年度统计公报。

数据表面（公开静态页，免鉴权；2026-11-08 实测直连 200，与 mct-star-hotel 共用栏目）：

- 栏目：``GET https://zwgk.mct.gov.cn/zfxxgkml/447/465/index_3081.html``
- 季度稿：**纯 HTML 正文，无附件、无表格** —— 数字全在中文句子里的数词
  （``53.07亿`` / ``4.95万亿元`` / ``15450万人次``），是自然语言面，不是结构化表。
- 年度公报：同栏目《YYYY年文化和旅游发展统计公报》，正文一节里给需求侧（出游/入境/出境）
  与供给侧（旅行社/星级饭店/A 级景区）的同年同源单点。

本单元产出两种文档行（``source_kind``）：

============  ==============================  ==========================================
source_kind   文档                             粒度
============  ==============================  ==========================================
dispatch      《…国内居民出游数据情况》/《…国内旅游市场数据情况》  fy / ytd_h1 / ytd_9m / quarter
bulletin      《YYYY年文化和旅游发展统计公报》        fy（仅年度）
============  ==============================  ==========================================

口坑（侦察实测，逐一落实）：

1. **不是表，是句子**：抽取器必须做「中文数词 + 单位」解析，且 ``亿``（人次）与
   ``万亿元``（金额）是不同单位；``万人次`` 又是一档。本单元**保留上游单位原样**，
   不做量纲换算（换算在下游板面显式做，并在 caveat 里写清系数）。
2. **期次必须从标题解析，不能按发布日推算**：发布滞后 2–5 个月（2025 前三季度稿
   2025-10-21 发布，2025 年度稿 2026-01-26 发布）。
3. **累计 vs 单季两个粒度并存**：上半年稿除了 ``H1 = 34.63亿``，还额外给
   ``分季度看，一季度…19.01亿…；二季度…15.62亿…``。同一个 2026Q1 会在两份文档里出现
   （Q1 稿与 H1 稿），**必须按 (期间, 粒度, 口径, 指标) 去重并校验一致**：同值→留痕
   ``同值复现``，异值→留痕 ``口径冲突``（两行都留，绝不悄悄择一）。
4. **同一句话里两档口径**：``全国`` 永远带 ``城镇`` / ``农村`` 两档，三档都入行
   （``scope``），不是只取总数。
5. **公报与季报的星级饭店口径不同**：公报的「星级旅游饭店营业收入」是**统计直报系统**
   口径（2025 年全年 1448.0 亿元 / 7586 家 / ADR 360.2 / OCC 46.8%），与季报
   「全国星级饭店经营情况统计表」不是同一套汇总（2024 公报 1557.3 亿 vs 季报 2024 四期
   之和更高）。故公报行 ``source_kind=bulletin``，**不得与季报同行合并或互相补齐**。
6. **正文页含大量内联 CSS**：``<style>`` / ``<script>`` 必须先整块剥掉，否则样式文本
   会混进正文并污染正则。
7. **更早年份公报用另一套措辞**（``国内旅游人数``，不是 ``国内居民出游``）——
   ``BULLETIN_MIN_YEAR`` 之前整份跳过并留痕，不做跨口径拼补。
8. **硬失败**：任一 dispatch 稿解析不出「国内居民出游人次」即 ``UnmappedPhrase`` →
   整次运行 ``RuntimeError``（上游改措辞必须炸，不能静默少期）；公报同样要求出游人次。
"""
from __future__ import annotations

import html as _html
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SOURCE = "mct-tourism-demand"
BASE = "https://zwgk.mct.gov.cn"
COLUMN_URL = BASE + "/zfxxgkml/447/465/index_3081.html"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/128.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9",
}
TIMEOUT = 30

# 标题命中（栏目标题随年份漂移：2025 前三季度叫「国内旅游市场数据情况」）
TITLE_HINTS = ("国内居民出游数据情况", "国内旅游市场数据情况",
               "国内居民出游抽样调查", "文化和旅游发展统计公报")
BULLETIN_HINT = "文化和旅游发展统计公报"
# 更早的公报用「国内旅游人数」表述，口径不同，整份跳过
BULLETIN_MIN_YEAR = 2024

DISPATCH_TITLE_RE = re.compile(
    r"(?P<year>\d{4})\s*年\s*(?P<part>前三季度|上半年|一季度|年度|全年)?")
BULLETIN_TITLE_RE = re.compile(r"(?P<year>\d{4})\s*年\s*文化和旅游发展统计公报")
PART_KIND = {"前三季度": ("Q1-Q3", "ytd_9m"), "上半年": ("H1", "ytd_h1"),
             "一季度": ("Q1", "quarter"), "年度": ("", "fy"), "全年": ("", "fy"),
             None: ("", "fy")}

# 指标口径：多在句子里，逐条锚定上游原话，禁止跨口径借用
PATTERNS = (
    # (正则, 指标, 口径 scope, 单位, 说明)
    (r"国内居民出游(?:人次)?([\d.]+)亿", "domestic_trips", "全国", "亿人次", "国内居民出游人次"),
    (r"城镇居民国内出游(?:人次)?([\d.]+)亿", "domestic_trips", "城镇", "亿人次", "城镇居民出游人次"),
    (r"农村居民国内出游(?:人次)?([\d.]+)亿", "domestic_trips", "农村", "亿人次", "农村居民出游人次"),
    (r"国内居民出游(?:总)?花费([\d.]+)万亿元", "domestic_spending", "全国", "万亿元", "国内居民出游总花费"),
    (r"城镇居民出游花费([\d.]+)万亿元", "domestic_spending", "城镇", "万亿元", "城镇居民出游花费"),
    (r"农村居民出游花费([\d.]+)万亿元", "domestic_spending", "农村", "万亿元", "农村居民出游花费"),
)
BULLETIN_PATTERNS = (
    (r"入境游客([\d.]+)万人次", "inbound_trips", "全国", "万人次"),
    (r"外国游客入境([\d.]+)万人次", "foreign_inbound_trips", "外国", "万人次"),
    (r"香港[、,和及]?.*?台湾居民入境([\d.]+)万人次", "hkmotw_inbound_trips", "港澳台", "万人次"),
    (r"内地居民出境旅游([\d.]+)万人次", "outbound_trips", "内地", "万人次"),
    (r"全国共有旅行社([\d.]+)家", "travel_agencies", "全国", "家"),
    (r"星级饭店([\d.]+)家", "star_hotels", "全国", "家"),
    (r"星级(?:旅游)?饭店营业收入([\d.]+)亿元", "star_hotel_revenue", "全国", "亿元"),
    (r"平均房价([\d.]+)元", "star_hotel_adr", "全国", "元/间夜"),
    (r"平均出租率([\d.]+)%", "star_hotel_occ", "全国", "%"),
    (r"A级(?:旅游)?景区([\d.]+)个", "a_level_scenic", "全国", "个"),
    (r"全年接待游客([\d.]+)亿人次", "scenic_visits", "全国", "亿人次"),
    (r"实现旅游收入([\d.]+)亿元", "scenic_revenue", "全国", "亿元"),
)
QUARTER_SPLIT_RE = re.compile(r"([一二三四])季度国内居民出游人次([\d.]+)亿")
QUARTER_NUM = {"一": 1, "二": 2, "三": 3, "四": 4}

LAST_TRACES: list[str] = []

# 增量的单位必须与指标同族，否则宁可不给（跨句串味会把「花费增量」挂到「人次」行上）
DELTA_UNIT_FAMILY = {
    "亿人次": {"亿", "亿人次"},
    "万人次": {"万", "万人次"},
    "万亿元": {"万亿元", "万亿"},
    "亿元": {"亿元", "亿"},
    "家": {"家"},
    "个": {"个"},
    "元/间夜": {"元"},
    "%": set(),          # 百分数没有「增加 X%」这种带单位的增量，一律不给
}


class UnmappedPhrase(ValueError):
    """正文措辞变了，锚定短语失配 —— 上游改版，必须硬失败。"""


def _get(url: str, timeout: int = TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 固定 https 主机
        return resp.read()


def _absolute(href: str, base: str = BASE) -> str:
    """相对链接 → 绝对（``urljoin``，base 不补尾斜杠；同 mct-star-hotel 坑位）。"""
    return urllib.parse.urljoin(base, href)


def _page_url(page: int) -> str:
    return COLUMN_URL if page == 0 else COLUMN_URL.replace(
        "index_3081.html", f"index_3081_{page}.html")


def _num(text: str) -> float | None:
    try:
        return float(text.replace(",", ""))
    except (TypeError, ValueError):
        return None


def _flatten(raw: str) -> str:
    """HTML → 纯正文：**先整块剥 style/script**，再剥标签、反转义、压掉全部空白。

    为什么压空白而不是保留空格：正文里的数字与单位会被标签切开
    （``49.98<!-- -->亿``、``同比增长1\\n8.0%``），只要留空白就会假失配。
    代价是所有空格消失，故锚定短语必须写成无空格形式。
    """
    body = re.sub(r"(?is)<style.*?</style>", " ", raw)
    body = re.sub(r"(?is)<script.*?</script>", " ", body)
    body = re.sub(r"(?s)<!--.*?-->", " ", body)
    text = re.sub(r"(?s)<[^>]+>", " ", body)
    text = _html.unescape(text)
    return re.sub(r"[ \t\r\n\u3000\xa0]+", "", text)


def _tail(text: str, end: int, unit: str, window: int = 90) -> tuple[float | None, float | None, str]:
    """取值点之后**同一句**里找「同比 ±x%」与「比上年增加/减少 x 单位」。

    句界 = ``。`` 或 ``；``。为什么必须截断：``…旅行社64616家。…2024年，国内居民出游
    56.2亿人次，同比增长14.8%`` —— 不截断就会把隔句的 14.8% 挂在旅行社家数上；
    ``农村居民国内出游15.26亿，同比增长22.6%。国内居民出游花费6.30万亿元，比上年增加
    0.55万亿元`` —— 不截断会把「花费的 0.55 万亿元」当成「人次的增量」。

    第二道闸：增量单位必须与指标同族（``DELTA_UNIT_FAMILY``），不同族一律不给。
    """
    seg = text[end:end + window]
    cut = len(seg)
    for mark in ("。", "；"):
        pos = seg.find(mark)
        if pos != -1:
            cut = min(cut, pos)
    seg = seg[:cut]
    yoy = None
    m = re.search(r"同比(增长|下降|减少)([\d.]+)%", seg)
    if m:
        yoy = _num(m.group(2))
        if m.group(1) in ("下降", "减少") and yoy is not None:
            yoy = -yoy
    delta = None
    delta_unit = ""
    allowed = DELTA_UNIT_FAMILY.get(unit, set())
    if allowed:
        m = re.search(r"比上年(?:同期)?(增加|减少|下降)([\d.]+)(万亿元|亿元|亿人次|万人次|亿|万亿)", seg)
        if m and m.group(3) in allowed:
            delta = _num(m.group(2))
            if m.group(1) in ("减少", "下降") and delta is not None:
                delta = -delta
            delta_unit = m.group(3)
    return yoy, delta, delta_unit


def _detail_links(raw: str, page_url: str = COLUMN_URL) -> list[tuple[str, str, str]]:
    """栏目页 → [(标题, 详情页 URL, source_kind)]，只留本单元两种文档。"""
    out: list[tuple[str, str, str]] = []
    for m in re.finditer(r'<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', raw, re.S | re.I):
        href, inner = m.group(1), m.group(2)
        title = re.sub(r"<[^>]+>|\s+", " ", inner).strip()
        if not any(hint in title for hint in TITLE_HINTS):
            continue
        kind = "bulletin" if BULLETIN_HINT in title else "dispatch"
        out.append((title, _absolute(href, page_url), kind))
    return out


def _period_of(title: str, kind: str) -> tuple[str, str, int] | None:
    """标题 → (期间, 粒度, 年份)；不是本单元的期次返回 None。"""
    if kind == "bulletin":
        m = BULLETIN_TITLE_RE.search(title)
        if not m:
            return None
        year = int(m.group("year"))
        return f"{year}", "fy", year
    m = DISPATCH_TITLE_RE.search(title)
    if not m:
        return None
    year = int(m.group("year"))
    part = m.group("part")
    suffix, period_kind = PART_KIND.get(part, ("", "fy"))
    return f"{year}{suffix}", period_kind, year


def _row(period, period_kind, scope, indicator, value, unit,
         yoy, delta, delta_unit, source_kind, title, doc_url, note) -> dict:
    return {
        "period": period, "period_kind": period_kind, "scope": scope,
        "indicator": indicator, "value": value, "unit": unit,
        "yoy_pct": yoy, "delta": delta, "delta_unit": delta_unit,
        "source_kind": source_kind, "indicator_note": note,
        "report_title": title, "doc_url": doc_url, "source": SOURCE,
    }


def _scan(text, period, period_kind, source_kind, title, doc_url,
          patterns, require: tuple[str, ...]) -> list[dict]:
    rows: list[dict] = []
    found: set[str] = set()
    for pattern, indicator, scope, unit, *rest in patterns:
        note = rest[0] if rest else indicator
        m = re.search(pattern, text)
        if not m:
            continue
        value = _num(m.group(m.lastindex or 1))
        if value is None:
            continue
        yoy, delta, delta_unit = _tail(text, m.end(), unit)
        found.add(indicator)
        rows.append(_row(period, period_kind, scope, indicator, value, unit,
                         yoy, delta, delta_unit, source_kind, title, doc_url, note))
    missing = [key for key in require if key not in found]
    if missing:
        raise UnmappedPhrase(f"{period} 缺少锚定指标 {missing}（上游改措辞）")
    return rows


def parse_dispatch(text: str, period: str, period_kind: str,
                   title: str, doc_url: str) -> list[dict]:
    """季度稿正文 → 长表行（含城镇/农村两档与分季度拆分）。"""
    rows = _scan(text, period, period_kind, "dispatch", title, doc_url,
                 PATTERNS, require=("domestic_trips",))
    year = period[:4]
    for m in QUARTER_SPLIT_RE.finditer(text):
        value = _num(m.group(2))
        if value is None:
            continue
        yoy, delta, delta_unit = _tail(text, m.end(), "亿人次")
        rows.append(_row(f"{year}Q{QUARTER_NUM[m.group(1)]}", "quarter", "全国",
                         "domestic_trips", value, "亿人次", yoy, delta, delta_unit,
                         "dispatch", title, doc_url, "分季度拆分"))
    return rows


def parse_bulletin(text: str, period: str, title: str, doc_url: str) -> list[dict]:
    """年度公报正文 → 长表行（出游/入境/出境 + 旅行社/星级饭店/A 级景区）。

    ``source_kind=bulletin``：星级饭店营收/ADR/OCC 是**统计直报口径**，与季报不同源，
    下游不得与季报同行合并。
    """
    rows = _scan(text, period, "fy", "bulletin", title, doc_url,
                 PATTERNS, require=("domestic_trips",))
    rows += _scan(text, period, "fy", "bulletin", title, doc_url,
                  BULLETIN_PATTERNS, require=())
    return rows


def _merge(rows: list[dict], traces: list[str]) -> list[dict]:
    """跨文档去重：同 (期间, 粒度, 口径, 指标) 只留一条；异值留两条并留痕。"""
    kept: dict[tuple, dict] = {}
    out: list[dict] = []
    for row in rows:
        key = (row["period"], row["period_kind"], row["scope"], row["indicator"])
        prior = kept.get(key)
        if prior is None:
            kept[key] = row
            out.append(row)
            continue
        if prior["value"] == row["value"] and prior["yoy_pct"] == row["yoy_pct"]:
            traces.append(f"{key}: 同值复现（{row['source_kind']} 与 "
                          f"{prior['source_kind']} 一致）")
            continue
        traces.append(f"{key}: 口径冲突 prior={prior['value']}/{prior['yoy_pct']} "
                      f"new={row['value']}/{row['yoy_pct']}（两行都留）")
        out.append(row)
    return out


# --------------------------------------------------------------------- 入口


def run_mct_tourism_demand(limit: int = 2000, pages: int = 3,
                           periods: str | None = None) -> list[dict]:
    """抓文旅部国内居民出游季报 + 年度统计公报，产出 (期间, 粒度, 口径, 指标) 长表行。

    Args:
        limit: 最大返回行数。
        pages: 栏目页翻页数（默认 3；第 1 页已覆盖 2024-09-02 以后各期）。
        periods: 逗号分隔的期间过滤（如 ``"2026Q1-Q3,2025"``）；缺省取全部命中。

    Raises:
        ValueError: 参数非法。
        RuntimeError: 全部文档解析失败，或出现 ``UnmappedPhrase``（上游改措辞）。
    """
    try:
        limit = int(limit)
        pages = int(pages)
    except (TypeError, ValueError):
        raise ValueError(f"limit/pages must be ints, got {limit!r}/{pages!r}") from None
    if limit <= 0:
        return []
    if not 1 <= pages <= 12:
        raise ValueError(f"pages must be 1..12, got {pages}")
    want = {p.strip().upper() for p in periods.split(",")} if periods else None

    now = datetime.now(timezone.utc).isoformat()
    traces: list[str] = []
    LAST_TRACES.clear()
    rows: list[dict] = []
    fatal: list[str] = []

    docs: list[tuple[str, str, str, str, str, int]] = []
    for page in range(pages):
        try:
            page_url = _page_url(page)
            for title, url, kind in _detail_links(
                    _get(page_url).decode("utf-8", "replace"), page_url):
                parsed = _period_of(title, kind)
                if not parsed:
                    traces.append(f"跳过（期次不可解析）：{title[:40]}")
                    continue
                period, period_kind, year = parsed
                if kind == "bulletin" and year < BULLETIN_MIN_YEAR:
                    traces.append(f"跳过（{year} 公报口径不同）：{title[:40]}")
                    continue
                if want and period.upper() not in want:
                    continue
                docs.append((period, period_kind, kind, title, url, year))
        except Exception as exc:  # noqa: BLE001 单页失败只留痕
            traces.append(f"page {page}: {type(exc).__name__}: {exc}"[:200])

    # 同期可能被栏目重复挂载；同 (期间, 文档类型) 只留先出现的
    seen: set[tuple[str, str]] = set()
    picked: list[tuple[str, str, str, str, str, int]] = []
    for doc in docs:
        key = (doc[0], doc[2])
        if key in seen:
            continue
        seen.add(key)
        picked.append(doc)
    if not picked:
        raise RuntimeError("mct-tourism-demand: 栏目页未命中任何出游数据/公报 — "
                           + "; ".join(traces)[:300])

    # dispatch 先于 bulletin：同一 (期间, 指标) 以季报为准，公报同值只留痕
    ordered = sorted(picked, key=lambda d: (d[0], d[2] != "dispatch"))
    for period, period_kind, kind, title, url, _year in ordered:
        try:
            text = _flatten(_get(url).decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            traces.append(f"{period}: detail {type(exc).__name__}: {exc}"[:200])
            continue
        try:
            if kind == "dispatch":
                doc_rows = parse_dispatch(text, period, period_kind, title, url)
            else:
                doc_rows = parse_bulletin(text, period, title, url)
        except UnmappedPhrase as exc:
            fatal.append(str(exc))
            continue
        except Exception as exc:  # noqa: BLE001
            traces.append(f"{period}: {type(exc).__name__}: {exc}"[:200])
            continue
        for row in doc_rows:
            rows.append({**row, "scraped_at": now})
        traces.append(f"{period}（{kind}）: {len(doc_rows)} 行")

    merged = _merge(rows, traces)
    merged.sort(key=lambda r: (r["period"], r["period_kind"], r["indicator"], r["scope"]))
    LAST_TRACES.extend(traces)
    if fatal:
        raise RuntimeError("mct-tourism-demand: 正文锚定短语失配，已中止（上游改措辞）— "
                           + "; ".join(fatal)[:400])
    if not merged:
        raise RuntimeError("mct-tourism-demand: 全部文档解析失败 — " + "; ".join(traces)[:400])
    return merged[:limit]


if __name__ == "__main__":  # 冒烟：python3 spiders/mct-tourism-demand/spider.py
    print(json.dumps(run_mct_tourism_demand(limit=8), ensure_ascii=False, indent=2))
