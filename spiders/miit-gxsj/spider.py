"""miit-gxsj — 工信部（MIIT）运行监测协调局「软件业/通信业」月度运行情况。

数据链路（侦察簿 W2-7 + 本地实测 2026-10）：
1. 栏目列表来自 jpaas-publish-server `build/unit` 接口（GET，栏目页 HTML 内
   queryData 固化参数），返回 JSON，文章列表嵌在 data.html 字符串里；
2. 取每栏最新若干篇文章正文页（div#ccontent#con_con），用指标白名单正则
   抽取关键数字（软件业务收入/电信业务收入/流量/用户规模等）。

约束（_COMMON.md 契约）：
- 裸请求 403 —— UA + Referer（栏目页 URL）必带；
- 参数白名单：只发实测有效参数，多余参数一律不发；5xx/空响应记录后跳过，
  不重试、不绕反爬；
- 国内源，直连，不走代理。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import html as html_lib
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlencode, urljoin

from scrapling.fetchers import FetcherSession

logger = logging.getLogger("miit-gxsj")

BASE_URL = "https://www.miit.gov.cn"
UNIT_API = f"{BASE_URL}/api-gateway/jpaas-publish-server/front/page/build/unit"

# 栏目页 HTML 中固化的站点/模板参数（实测有效；GET；多余参数不发）
WEB_ID = "8d828e408d90447786ddbe128d495e9e"
TPL_SET_ID = "209741b2109044b5b7695700b2bec37e"

CHANNELS = (
    {
        "channel": "软件业",
        "page_id": "50bf589f36614b7394522245a13fedae",
        "list_url": f"{BASE_URL}/gxsj/tjfx/rjy/index.html",
    },
    {
        "channel": "通信业",
        "page_id": "1434685f08314ae8ae78f78b6a5a7915",
        "list_url": f"{BASE_URL}/gxsj/tjfx/txy/index.html",
    },
)

HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

_NUM = r"(\d[\d,]*(?:\.\d+)?)"
_UNIT = r"(万亿元|亿元|亿美元|万亿美元|亿GB|万GB|GB/户·月|亿户|万户|亿个|万个)"

# 指标白名单：正则各含 2 个捕获组（数值、单位）；按文中出现顺序输出。
METRIC_DEFS = (
    ("软件业务收入", re.compile(r"软件业务收入" + _NUM + _UNIT)),
    ("软件业利润总额", re.compile(r"软件业?利润总额" + _NUM + _UNIT)),
    ("软件业务出口", re.compile(r"软件业务出口" + _NUM + _UNIT)),
    ("软件产品收入", re.compile(r"软件产品收入" + _NUM + _UNIT)),
    ("信息技术服务收入", re.compile(r"信息技术服务收入" + _NUM + _UNIT)),
    ("信息安全收入", re.compile(r"信息安全(?:产品和服务)?收入" + _NUM + _UNIT)),
    ("云计算、大数据服务收入", re.compile(r"云计算、大数据服务收入" + _NUM + _UNIT)),
    ("电信业务收入", re.compile(r"电信业务收入[^0-9。]{0,12}?" + _NUM + _UNIT)),
    ("移动电话用户总数", re.compile(r"移动电话用户总数(?:达|达到)?" + _NUM + _UNIT)),
    ("5G移动电话用户", re.compile(r"5G移动电话用户[^0-9。]{0,8}?" + _NUM + _UNIT)),
    ("5G基站总数", re.compile(r"5G基站(?:总数)?[^0-9。]{0,8}?" + _NUM + _UNIT)),
    ("千兆及以上固定宽带接入用户", re.compile(r"千兆(?:及以上)?[^0-9。]{0,6}?(?:接入)?用户(?:达|达到)?" + _NUM + _UNIT)),
    ("固定互联网宽带接入用户", re.compile(r"固定互联网宽带(?:接入)?用户总数?(?:达|达到)?" + _NUM + _UNIT)),
    ("蜂窝物联网终端用户", re.compile(r"蜂窝物联网终端用户[^0-9。]{0,8}?" + _NUM + _UNIT)),
    ("移动物联网终端用户", re.compile(r"移动物联网终端用户[^0-9。]{0,8}?" + _NUM + _UNIT)),
    ("移动互联网累计流量", re.compile(r"移动互联网累计流量(?:达|达到|累计完成)?" + _NUM + _UNIT)),
    ("固定宽带接入流量", re.compile(r"固定宽带接入流量(?:达|达到)?" + _NUM + _UNIT)),
    ("蜂窝物联网终端接入流量", re.compile(r"蜂窝物联网终端(?:接入)?流量(?:达|达到)?" + _NUM + _UNIT)),
    ("户均移动互联网接入流量", re.compile(r"户均移动互联网(?:接入)?流量[^0-9。]{0,10}?" + _NUM + _UNIT)),
    ("移动互联网接入月均流量", re.compile(r"移动互联网接入月均流量[^0-9。]{0,10}?" + _NUM + _UNIT)),
    ("固定互联网宽带接入流量", re.compile(r"固定互联网宽带接入流量(?:达|达到)?" + _NUM + _UNIT)),
)

# 纯增速表述（无绝对值）：指标名 + 同比增长/下降 x%
PURE_GROWTH_DEFS = (
    ("软件产品收入同比增速", re.compile(r"软件产品收入[^。]{0,12}?同比增长([\d.]+)%")),
    ("基础软件产品收入同比增速", re.compile(r"基础软件产品收入[^。]{0,12}?同比增长([\d.]+)%")),
    ("工业软件产品收入同比增速", re.compile(r"工业软件产品收入[^。]{0,12}?同比增长([\d.]+)%")),
    ("信息技术服务收入同比增速", re.compile(r"信息技术服务收入[^。]{0,12}?同比增长([\d.]+)%")),
    ("云计算、大数据服务收入同比增速", re.compile(r"云计算、大数据服务收入[^。]{0,12}?同比增长([\d.]+)%")),
    ("电信业务总量同比增速", re.compile(r"电信业务总量[^。]{0,25}?同比增长([\d.]+)%")),
    ("移动互联网累计流量同比增速", re.compile(r"移动互联网累计流量[^。]{0,12}?同比增长([\d.]+)%")),
)

# 绝对值后紧跟的同比表述：配对窗口 45 字符
_GROWTH_AFTER = re.compile(r"[^%。]{0,18}?(?:同比)?(增长|下降|回落)([\d.]+)%")

_META_PUBDATE = re.compile(r'<meta[^>]+name="PubDate"[^>]+content="(\d{4}-\d{2}-\d{2})')

_LIST_ITEM = re.compile(
    r'<a[^>]+href="(?P<href>[^"]+)"[^>]*title="(?P<title>[^"]+)"[^>]*>'
    r'.*?</a>(?:\s*<span[^>]*>(?P<date>\d{4}-\d{2}-\d{2})</span>)?',
    re.S,
)

_BODY_ANCHORS = (
    re.compile(r'<div[^>]+id="con_con"[^>]*>'),
    re.compile(r'<div[^>]+class="[^"]*ccontent[^"]*"[^>]*>'),
    re.compile(r'<div[^>]+class="[^"]*article_fd_r[^"]*"[^>]*>'),
)


def _unit_api_url(page_id: str) -> str:
    """栏目列表接口 URL（参数白名单：全部为栏目页 queryData 实测固化值）。"""
    qs = urlencode({
        "parseType": "buildstatic",
        "webId": WEB_ID,
        "tplSetId": TPL_SET_ID,
        "pageType": "column",
        "tagId": "右侧内容",
        "editType": "null",
        "pageId": page_id,
    })
    return f"{UNIT_API}?{qs}"


def _clean(text: str) -> str:
    return re.sub(r"\s+", "", html_lib.unescape(text or "")).strip()


def _excerpt(text: str, start: int, end: int, pad: int = 24) -> str:
    return text[max(0, start - pad):min(len(text), end + pad + 4)].strip()


async def _fetch(session, url: str, referer: str) -> str | None:
    """GET 单页；非 200 / 异常记录后返回 None（log-and-skip，不重试）。"""
    headers = dict(HEADERS_BASE, Referer=referer)
    try:
        resp = await session.get(url, headers=headers)
    except Exception as exc:  # noqa: BLE001
        logger.warning("miit-gxsj: fetch error %s on %s -> skip", exc, url)
        return None
    if resp.status != 200:
        logger.warning("miit-gxsj: HTTP %s on %s -> skip", resp.status, url)
        return None
    # scrapling 0.4: 原始字节在 resp.body；resp.text 为解析后文本（对 JSON 为空）
    body = getattr(resp, "body", None) or b""
    return body.decode("utf-8", errors="replace")


def _list_articles_from_html(unit_html: str) -> list[dict]:
    """data.html 内的栏目文章列表（标题+链接+日期，最新在前）。"""
    items: list[dict] = []
    seen: set[str] = set()
    for m in _LIST_ITEM.finditer(unit_html):
        href = (m.group("href") or "").strip()
        if not (href.startswith("/gxsj/") and "/art/" in href and href.endswith(".html")):
            logger.info("miit-gxsj: skip non-article link %s", href)
            continue
        url = urljoin(BASE_URL, href)
        if url in seen:
            continue
        seen.add(url)
        items.append({
            "title": _clean(m.group("title")),
            "url": url,
            "date": m.group("date") or "",
        })
    return items


async def _list_articles(session, channel: dict) -> list[dict]:
    url = _unit_api_url(channel["page_id"])
    raw = await _fetch(session, url, referer=channel["list_url"])
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("miit-gxsj: non-JSON unit response for %s (%s) -> skip", channel["channel"], exc)
        return []
    if not data.get("success"):
        logger.warning("miit-gxsj: unit api success=%s for %s -> skip", data.get("success"), channel["channel"])
        return []
    unit_html = (data.get("data") or {}).get("html") or ""
    items = _list_articles_from_html(unit_html)
    logger.info("miit-gxsj: channel %s -> %d articles", channel["channel"], len(items))
    return items


def _article_text(raw: str) -> str:
    """正文区文本：优先 con_con/ccontent 容器（div 深度配对截取），否则整页兜底。"""
    anchor, start = None, -1
    for rx in _BODY_ANCHORS:
        m = rx.search(raw)
        if m:
            anchor, start = m, m.end()
            break
    seg = raw
    if anchor is not None:
        depth = 1
        for m in re.finditer(r"<div\b[^>]*>|</div>", raw[start:]):
            depth += 1 if m.group(0).startswith("<div") else -1
            if depth == 0:
                seg = raw[start:start + m.start()]
                break
        else:
            seg = raw[start:]
    seg = re.sub(r"<script.*?</script>", " ", seg, flags=re.S | re.I)
    seg = re.sub(r"<style.*?</style>", " ", seg, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", seg)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _rows_from_article(channel: str, art: dict, raw: str) -> list[dict]:
    text = _article_text(raw)
    if not text:
        logger.warning("miit-gxsj: empty article text for %s -> skip", art["url"])
        return []
    m_pub = _META_PUBDATE.search(raw)
    publish_date = art.get("date") or (m_pub.group(1) if m_pub else "")
    scraped_at = datetime.now(timezone.utc).isoformat()
    source_url = _unit_api_url(next(c["page_id"] for c in CHANNELS if c["channel"] == channel))

    rows: list[dict] = []
    seen: set[tuple] = set()

    def add(metric: str, value: float, unit: str, excerpt: str) -> None:
        key = (metric, value, unit)
        if key in seen:
            return
        seen.add(key)
        rows.append({
            "channel": channel,
            "title": art["title"],
            "publish_date": publish_date,
            "url": art["url"],
            "metric": metric,
            "value": value,
            "unit": unit,
            "excerpt": excerpt,
            "scraped_at": scraped_at,
            "source_url": source_url,
        })

    for metric, rx in METRIC_DEFS:
        for m in rx.finditer(text):
            try:
                value = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            unit = m.group(2)
            add(metric, value, unit, _excerpt(text, m.start(), m.end()))
            # 同句跟涨跌：配对生成增速行
            g = _GROWTH_AFTER.search(text, m.end(), min(len(text), m.end() + 45))
            if g:
                try:
                    gv = float(g.group(2))
                except ValueError:
                    continue
                if g.group(1) in ("下降", "回落"):
                    gv = -gv
                add(f"{metric}同比增速", gv, "%", _excerpt(text, g.start(), g.end()))

    for metric, rx in PURE_GROWTH_DEFS:
        for m in rx.finditer(text):
            try:
                gv = float(m.group(1))
            except ValueError:
                continue
            add(metric, gv, "%", _excerpt(text, m.start(), m.end()))

    return rows


async def _article_rows(session, channel: str, art: dict, referer: str) -> list[dict]:
    raw = await _fetch(session, art["url"], referer=referer)
    if not raw:
        return []
    return _rows_from_article(channel, art, raw)


async def _collect(limit: int) -> list[dict]:
    """两栏文章按最新优先轮询抓取，抽满 limit 行即停。"""
    rows: list[dict] = []
    async with FetcherSession(impersonate="chrome120", timeout=25, verify=False) as session:
        queues: dict[str, list[dict]] = {}
        referers: dict[str, str] = {}
        for ch in CHANNELS:
            queues[ch["channel"]] = await _list_articles(session, ch)
            referers[ch["channel"]] = ch["list_url"]

        i = 0
        while len(rows) < limit and any(queues.values()):
            ch_name = CHANNELS[i % len(CHANNELS)]["channel"]
            i += 1
            queue = queues.get(ch_name) or []
            if not queue:
                continue
            art = queue.pop(0)
            try:
                new_rows = await _article_rows(session, ch_name, art, referers[ch_name])
            except Exception as exc:  # noqa: BLE001
                logger.warning("miit-gxsj: article %s failed (%s) -> skip", art["url"], exc)
                continue
            rows.extend(new_rows)
            if len(rows) >= limit:
                rows = rows[:limit]
                break
    return rows


def run_miit_gxsj(limit: int = 100) -> list[dict]:
    """fd-runner 入口：工信部软件业/通信业月度关键指标行（≤limit）。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 100
    if limit <= 0:
        limit = 100

    async def _run() -> list[dict]:
        return await _collect(limit)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_run())
    # 已在事件循环内（如被异步框架调用）：放独立线程跑，避免嵌套 loop
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _run()).result()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    for row in run_miit_gxsj(limit=20):
        print(json.dumps(row, ensure_ascii=False))
