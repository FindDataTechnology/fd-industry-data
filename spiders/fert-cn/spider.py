"""中肥网（fert.cn）化肥月度进出口统计 —— 栏目 ``/10005/``。

口径（务必先读）：中肥网是**商业门户**，不是行业协会。本单元 ``authority``
标 **``industry``**（产业媒体/商业门户），**不得标 ``association``**；产出只作
成本链参考序列，**不得作为预警判据**。

数据面（2026-11-08 实测，国内直连、禁代理、不碰会员区）：

- 栏目页 ``https://www.fert.cn/10005/`` 是静态 HTML（UTF-8），前 23 条为第 1 页；
- 翻页是 POST ``/huafei_10005.htm``（``pageno``/``type=10005``/``title``/``q``）
  返回 JSON，``stateCode == "111"``，每页 23 条；实测 ``pagecount`` 报 44 但
  第 44 页返回空列表（真实有数据的最后一页是 43）；
- 详情页 ``https://www.fert.cn/news/YYYY/MM/DD/<id>.shtml`` 是 **GB18030** 编码，
  正文在 ``<div class="article-conte-infor">`` 与 ``<!--免责声明-->`` 之间，
  以自然语言句子给出量/金额/均价/环比/同比，可正则抽取，无表格、无附件。

产量与出厂价在 ``vip.fert.cn``（会员登录），**本单元不接**。

单位口径（2026-11-08 实测：同一天不同品种即不同单位，必须逐句取单位再换算）：

| 原文 | 单位 | 折万吨 |
|---|---|---|
| ``52.46万吨``（尿素出口） | 万吨 | ×1 |
| ``45.35吨``（尿素进口） | 吨 | ×1e-4 |
| ``925千克``（磷酸二铵进口） | 千克 | ×1e-7 |

输出同时保留 ``volume_published`` + ``volume_unit``（原文数值与单位）与归一化
``volume_wan_t``，换算留痕，不静默改单位。解析不出的字段写 ``None``，绝不填 0。
"""
from __future__ import annotations

import html as html_lib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://www.fert.cn"
COLUMN_ID = "10005"
LIST_URL = BASE + "/huafei_10005.htm"
LIST_PAGE_URL = BASE + "/10005/"
COLUMN_TITLE = "化肥产量统计数据_进出口统计数据"

#: 实测每页 23 条；``pagecount`` 44 但第 44 页返回空列表。
LIST_PAGE_SIZE = 23
MAX_LIST_PAGES = 43

SLEEP_PAGE = 0.6
SLEEP_DOC = 0.35
RETRY_DETAILS = 1

SOURCE = "fert-cn"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": LIST_PAGE_URL,
}

#: 原文单位 -> 万吨 的换算因子。1 万吨 = 1e4 吨 = 1e7 千克。
UNIT_TO_WAN_TON = {
    "万吨": 1.0,
    "吨": 1e-4,
    "千克": 1e-7,
    "公斤": 1e-7,
}

#: 品种归类：化肥 vs 成本链原料（本栏目混排了硫磺/磷矿石/硫酸/原油/无烟煤）。
FERTILIZER_KEYWORDS = (
    "尿素", "磷酸二铵", "磷酸一铵", "氯化钾", "硫酸钾", "硝酸钾", "硫酸铵",
    "氯化铵", "复合肥", "二元肥", "过磷酸钙", "钙镁磷肥",
)
RAW_MATERIAL_KEYWORDS = ("硫磺", "磷矿石", "硫酸", "原油", "无烟煤", "煤炭", "天然气")

_TITLE_RE = re.compile(
    r"^(?P<year>\d{4})年(?P<month>\d{1,2})月份中国"
    r"(?P<product>.+?)(?P<flow>进出口|进口|出口)数据统计$"
)

#: 一句数据陈述：``2026年8月份中国尿素出口量为52.46万吨``
_STATEMENT_RE = re.compile(
    r"(?P<year>\d{4})年(?P<month>\d{1,2})月份中国"
    r"(?P<product>[^，。；]{1,40}?)"
    r"(?P<flow>进口|出口)(?:量)?(?:为)?"
    r"(?P<volume>\d[\d,]*(?:\.\d+)?)(?P<unit>万吨|吨|千克|公斤)"
)

_UNIT_ALT = "万吨|吨|千克|公斤"
_AMOUNT_RE = re.compile(r"金额总计\s*([\d,]+(?:\.\d+)?)\s*美元")
#: 原文三种写法并存：「平均单价415.28」「平均单价为852.25」「平均18437.91」（漏「单价」）。
_AVG_RE = re.compile(
    r"平均(?:单价|价格)?(?:为)?\s*([\d,]+(?:\.\d+)?)\s*美元\s*/\s*吨"
)
_PREV_MONTH_RE = re.compile(
    rf"上个?月(?:进口|出口)量?(?:为)?\s*([\d,]+(?:\.\d+)?)\s*({_UNIT_ALT})"
)
_PREV_YEAR_RE = re.compile(
    rf"\d{{4}}年同期(?:进口|出口)量?(?:为)?\s*([\d,]+(?:\.\d+)?)\s*({_UNIT_ALT})"
)
#: 偶见「上个月出口量0。」「2025年同期进口量为0。」——原文只写 0、不带单位
#: （0 换任何单位都是 0，按真值 0 记录，不丢）。
_PREV_MONTH_ZERO_RE = re.compile(r"上个?月(?:进口|出口)量?(?:为)?\s*0(?:\.0+)?(?![.\d])")
_PREV_YEAR_ZERO_RE = re.compile(r"\d{4}年同期(?:进口|出口)量?(?:为)?\s*0(?:\.0+)?(?![.\d])")
_CHANGE_RE = {
    "mom": re.compile(r"环比\s*(增加|减少|下降|上升|持平)?\s*([\d.]+)\s*%"),
    "yoy": re.compile(r"同比\s*(增加|减少|下降|上升|持平)?\s*([\d.]+)\s*%"),
}
_NEGATIVE_DIRECTIONS = ("减少", "下降")
_POSITIVE_DIRECTIONS = ("增加", "上升")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _to_float(raw: str) -> float | None:
    try:
        return float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _first_float(pattern: re.Pattern[str], block: str, group: int = 1) -> float | None:
    m = pattern.search(block)
    return _to_float(m.group(group)) if m else None


def _decode(raw: bytes) -> str:
    """列表 JSON 是 UTF-8，详情页是 GB18030（实测），两端都要能吃。"""
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("gb18030", "replace")


def _request(url: str, data: bytes | None = None, *, timeout: int = 30) -> bytes:
    headers = dict(HEADERS)
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=utf-8"
        headers["X-Requested-With"] = "XMLHttpRequest"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _fetch_list_page(pageno: int) -> dict | None:
    """翻页接口；``stateCode != "111"`` 视为失败（实测正常恒 111）。"""
    data = urllib.parse.urlencode(
        {"pageno": pageno, "type": COLUMN_ID, "title": COLUMN_TITLE, "q": ""}
    ).encode("utf-8")
    try:
        payload = json.loads(_decode(_request(LIST_URL, data)))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        print(f"[fert-cn] list page {pageno} failed: {exc}", flush=True)
        return None
    if payload.get("stateCode") != "111":
        print(
            f"[fert-cn] list page {pageno} stateCode={payload.get('stateCode')!r}",
            flush=True,
        )
        return None
    return payload


def _title_meta(title: str) -> dict | None:
    """标题 -> {period, product, flow_hint}；非统计数据标题返回 None。"""
    m = _TITLE_RE.match(title.strip())
    if not m:
        return None
    month = int(m.group("month"))
    if not 1 <= month <= 12:
        return None
    flow = m.group("flow")
    hint = {"进出口": ("import", "export"), "进口": ("import",), "出口": ("export",)}[flow]
    return {
        "period": f"{m.group('year')}-{month:02d}",
        "product": m.group("product").strip(),
        "flow_hint": hint,
    }


def _product_kind(product: str) -> str:
    # 「非肥料用硝酸钾」虽含「硝酸钾」，但明示非肥料用途。
    if product.startswith("非肥料用"):
        return "other"
    if any(k in product for k in FERTILIZER_KEYWORDS):
        return "fertilizer"
    if any(k in product for k in RAW_MATERIAL_KEYWORDS):
        return "raw_material"
    return "other"


def _body_text(page: str) -> str | None:
    """只取正文容器，避免把右栏热文/免责声明里的数字当数据。"""
    start = page.find("article-conte-infor")
    if start < 0:
        return None
    end = page.find("<!--免责声明-->", start)
    if end < 0:
        end = page.find("<!--左边列表结束-->", start)
    if end < 0:
        end = start + 20000
    body = page[start:end]
    body = re.sub(r"<script.*?</script>", " ", body, flags=re.S | re.I)
    body = re.sub(r"<style.*?</style>", " ", body, flags=re.S | re.I)
    text = html_lib.unescape(re.sub(r"<[^>]+>", "", body))
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _change_pct(block: str, key: str) -> float | None:
    """环比/同比：减少/下降取负；方向词缺失时无法定符号 -> None（不猜）。"""
    m = _CHANGE_RE[key].search(block)
    if not m:
        return None
    value = _to_float(m.group(2))
    if value is None:
        return None
    direction = m.group(1)
    if direction == "持平":
        return 0.0
    if direction in _NEGATIVE_DIRECTIONS:
        return -value
    if direction in _POSITIVE_DIRECTIONS:
        return value
    return None


def _prev_volume(
    pattern: re.Pattern[str], zero_pattern: re.Pattern[str], block: str
) -> float | None:
    """上一期量 -> 万吨；原文只写裸 0（无单位）时按 0 记录，不丢真值。"""
    m = pattern.search(block)
    if m:
        value = _to_float(m.group(1))
        if value is None:
            return None
        return round(value * UNIT_TO_WAN_TON[m.group(2)], 8)
    if zero_pattern.search(block):
        return 0.0
    return None


def _parse_body(text: str, *, published: str, url: str) -> list[dict]:
    """正文 -> 行。一句陈述一行（进口/出口各一行）；段尾切到下一句起点防串味。"""
    matches = list(_STATEMENT_RE.finditer(text))
    rows: list[dict] = []
    for i, m in enumerate(matches):
        tail_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[m.end(): min(tail_end, m.end() + 400)]

        year = int(m.group("year"))
        month = int(m.group("month"))
        if not 1 <= month <= 12:
            continue
        period = f"{year}-{month:02d}"
        product = m.group("product").strip()
        flow = "import" if m.group("flow") == "进口" else "export"
        unit = m.group("unit")
        volume = _to_float(m.group("volume"))
        if volume is None or not product:
            continue
        factor = UNIT_TO_WAN_TON[unit]

        prev_month = _prev_volume(_PREV_MONTH_RE, _PREV_MONTH_ZERO_RE, block)
        prev_year = _prev_volume(_PREV_YEAR_RE, _PREV_YEAR_ZERO_RE, block)

        amount = _first_float(_AMOUNT_RE, block)
        rows.append({
            "record_id": f"{period}|{product}|{flow}",
            "period": period,
            "product": product,
            "product_kind": _product_kind(product),
            "flow": flow,
            "volume_wan_t": round(volume * factor, 8),
            "volume_published": volume,
            "volume_unit": unit,
            "amount_usd": round(amount, 2) if amount is not None else None,
            "avg_price_usd_t": _first_float(_AVG_RE, block),
            "mom_pct": _change_pct(block, "mom"),
            "yoy_pct": _change_pct(block, "yoy"),
            "prev_month_volume_wan_t": prev_month,
            "prev_year_volume_wan_t": prev_year,
            "title": None,
            "published_date": published,
            "source_url": url,
            "source": SOURCE,
            "scraped_at": _now_iso(),
        })
    return rows


def _fetch_detail(url: str) -> str | None:
    for attempt in range(RETRY_DETAILS + 1):
        try:
            return _decode(_request(url))
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt >= RETRY_DETAILS:
                print(f"[fert-cn] detail failed {url}: {exc}", flush=True)
                return None
            time.sleep(SLEEP_DOC * 4)
    return None


def _norm_window(value: str | None) -> str | None:
    """接受 ``YYYY-MM`` 或 ``YYYY-MM-DD``，统一成 ``YYYY-MM``。"""
    if not value:
        return None
    m = re.match(r"^(\d{4})-(\d{2})", str(value).strip())
    return f"{m.group(1)}-{m.group(2)}" if m else None


def run_fert_cn(
    limit: int = 50,
    window_start: str | None = None,
    window_end: str | None = None,
) -> list[dict]:
    """中肥网 ``/10005/`` 化肥月度进出口（authority=industry，成本链参考序列）。

    ``window_start`` / ``window_end`` 为 ``YYYY-MM``，作用于文章所属月份
    （标题/正文里的 ``YYYY年M月份``，不是发布日期），闭区间；窗口过滤先于
    ``limit`` 截断（列表 newest-first，遇早于窗口起点即停翻页）。
    """
    limit = max(1, int(limit))
    window_start = _norm_window(window_start or os.environ.get("FD_WINDOW_START"))
    window_end = _norm_window(window_end or os.environ.get("FD_WINDOW_END"))

    articles: list[dict] = []
    for pageno in range(1, MAX_LIST_PAGES + 1):
        if pageno > 1:
            time.sleep(SLEEP_PAGE)
        payload = _fetch_list_page(pageno)
        if not payload:
            break
        items = payload.get("list") or []
        if not items:
            break

        passed_window = False
        for item in items:
            title = (item.get("news_title") or "").strip()
            url = (item.get("newspath") or "").strip()
            meta = _title_meta(title)
            if not meta or not url:
                continue
            period = meta["period"]
            if window_end and period > window_end:
                continue
            if window_start and period < window_start:
                passed_window = True
                continue
            articles.append({
                **meta,
                "title": title,
                "url": url,
                "published": (item.get("news_date") or "").strip(),
            })
        if passed_window:
            # 列表 newest-first：一旦落到窗口起点之前，后面只会更老。
            break
        if len(articles) >= limit:
            break

    if not articles:
        return []

    rows: list[dict] = []
    seen: set[str] = set()
    for art in articles:
        if len(rows) >= limit:
            break
        page = _fetch_detail(art["url"])
        time.sleep(SLEEP_DOC)
        if not page:
            continue
        body = _body_text(page)
        if not body:
            print(f"[fert-cn] no article body: {art['url']}", flush=True)
            continue
        for row in _parse_body(body, published=art["published"], url=art["url"]):
            if row["record_id"] in seen:
                continue
            seen.add(row["record_id"])
            row["title"] = art["title"]
            rows.append(row)
            if len(rows) >= limit:
                break

    if not rows:
        raise RuntimeError(
            f"fert-cn: {len(articles)} article(s) seen but no import/export row parsed "
            f"(window {window_start or '-'}..{window_end or '-'})"
        )
    return rows
