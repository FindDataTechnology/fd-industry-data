"""spb-express：国家邮政局（spb.gov.cn）快递行业月度数据爬虫。

数据来源：
- 运行情况栏目列表 API：
  https://www.spb.gov.cn/common/search/ce2d4d5628314ff7a81553529fb7f6a0?_isJson=true
- 快递发展指数栏目列表 API：
  https://www.spb.gov.cn/common/search/49357e62dbcc4693aa91b3685619cb88?_isJson=true
- 文章正文页：列表返回 http 链接，301 到 https，必须 follow_redirects=True。

容错纪律：5xx/空响应 → 记录后跳过，不重试风暴；解析失败字段留 None，不写脏行。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from scrapling.fetchers import FetcherSession

LOGGER = logging.getLogger("spb-express")

BASE = "https://www.spb.gov.cn"
SEARCH_URL = BASE + "/common/search/{channel}?_isJson=true"
CHANNEL_RUN = "ce2d4d5628314ff7a81553529fb7f6a0"  # 运行情况
CHANNEL_DEV = "49357e62dbcc4693aa91b3685619cb88"  # 快递发展指数

RUN_TITLE_KEYWORD = "邮政行业运行情况"
DEV_TITLE_KEYWORD = "快递发展指数"

HDRS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

PAGE_SIZE = 20  # 服务端固定每页 20 行；rows/pageSize 等参数实测无效，一律不发
MAX_PAGES_PER_CHANNEL = 30
ARTICLE_DELAY_S = 0.2  # 文章抓取间隔，礼貌抓取

# CR8 口径优先级：新版月报分列业务量/收入集中度，取业务量口径；
# 旧版只有单一「快递与包裹服务品牌集中度指数CR8」，直接取该值。
CR8_PATTERNS = (
    r"快递业务量品牌集中度指数CR8为([0-9]+(?:\.[0-9]+)?)",
    r"快递与包裹服务品牌集中度指数CR8为([0-9]+(?:\.[0-9]+)?)",
    r"品牌集中度指数CR8为([0-9]+(?:\.[0-9]+)?)",
)

PERIOD_PATTERNS = (
    (re.compile(r"(\d{4})年1[-–—](\d{1,2})月"), None),  # 1-N月累计，N 由正则给出
    (re.compile(r"(\d{4})年上半年"), {"fixed": 6}),
    (re.compile(r"(\d{4})年下半年"), {"fixed": 12}),
    (re.compile(r"(\d{4})年一季度"), {"fixed": 3}),
    (re.compile(r"(\d{4})年二季度"), {"fixed": 6}),
    (re.compile(r"(\d{4})年三季度"), {"fixed": 9}),
    (re.compile(r"(\d{4})年四季度"), {"fixed": 12}),
    (re.compile(r"(\d{4})年(\d{1,2})月"), None),  # 单月
    (re.compile(r"(\d{4})年"), {"fixed": 12}),  # 全年
)


def _https(url: str) -> str:
    return url.replace("http://", "https://", 1) if url.startswith("http://") else url


def _resp_html(resp) -> str:
    """从响应解码 HTML：scrapling 0.4.x 下 .text 可能为空，.body(bytes) 才是正体。"""
    body = getattr(resp, "body", None) or b""
    if not body:
        return getattr(resp, "text", "") or ""
    head = body[:2048].lower()
    if b"charset=gb" in head:  # 个别历史页面声明 gbk/gb2312
        return body.decode("gb18030", errors="replace")
    return body.decode("utf-8", errors="replace")


def _period_from_title(title: str) -> str | None:
    for pattern, extra in PERIOD_PATTERNS:
        m = pattern.search(title)
        if not m:
            continue
        year = int(m.group(1))
        if extra and "fixed" in extra:
            month = extra["fixed"]
        else:
            month = int(m.group(2))
        if 1 <= month <= 12:
            return f"{year}-{month:02d}"
    return None


def _strip_html(html: str) -> str:
    text = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def _yoy_after(anchor: str, text: str) -> str | None:
    """在锚点匹配之后的小窗口内找「同比增长/下降X%」。"""
    m = re.search(anchor, text)
    if not m:
        return None
    g = re.search(r"同比(增长|下降)([0-9]+(?:\.[0-9]+)?)%", text[m.end(): m.end() + 40])
    if not g:
        return None
    sign = "-" if g.group(1) == "下降" else ""
    return f"{sign}{g.group(2)}%"


def _parse_run_article(text: str) -> dict:
    out = {"expr_volume": None, "revenue": None, "cr8": None, "growth_yoy": None}

    m = re.search(r"快递业务量累计完成([0-9]+(?:\.[0-9]+)?)亿件", text)
    if m:
        out["expr_volume"] = float(m.group(1))
    m = re.search(r"快递业务收入累计完成([0-9]+(?:\.[0-9]+)?)亿元", text)
    if m:
        out["revenue"] = float(m.group(1))

    for pattern in CR8_PATTERNS:
        m = re.search(pattern, text)
        if m:
            out["cr8"] = float(m.group(1))
            break

    parts = []
    vol_yoy = _yoy_after(r"快递业务量累计完成[0-9]+(?:\.[0-9]+)?亿件", text)
    rev_yoy = _yoy_after(r"快递业务收入累计完成[0-9]+(?:\.[0-9]+)?亿元", text)
    if vol_yoy:
        parts.append(f"业务量同比{vol_yoy}")
    if rev_yoy:
        parts.append(f"收入同比{rev_yoy}")
    out["growth_yoy"] = "，".join(parts) if parts else None
    return out


async def _get(session, url: str):
    """GET 一次；失败/5xx/空响应记录后返回 None，不重试。"""
    try:
        resp = await session.get(url, headers=HDRS)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("spb-express: GET %s failed: %s", url, exc)
        return None
    if getattr(resp, "status", 0) != 200 or not getattr(resp, "body", None):
        LOGGER.warning(
            "spb-express: GET %s -> status=%s empty_body=%s; skip",
            url, getattr(resp, "status", None), not getattr(resp, "body", None),
        )
        return None
    return resp


async def _collect_list(session, channel: str, keyword: str, want: int) -> list[dict]:
    """翻列表页收集标题命中的文章元数据；只发实测有效的 page 参数。"""
    items: list[dict] = []
    seen: set[str] = set()
    for page in range(1, MAX_PAGES_PER_CHANNEL + 1):
        url = SEARCH_URL.format(channel=channel) + f"&page={page}"
        resp = await _get(session, url)
        if resp is None:
            break
        try:
            data = (json.loads(resp.body).get("data") or {})
        except (ValueError, AttributeError) as exc:
            LOGGER.warning("spb-express: list %s page=%s bad JSON: %s", channel, page, exc)
            break
        results = data.get("results") or []
        if not results:
            break
        for it in results:
            title = (it.get("title") or "").strip()
            link = (it.get("url") or "").strip()
            if keyword not in title or not link or link in seen:
                continue
            seen.add(link)
            items.append({
                "title": title,
                "url": _https(link),
                "published": (it.get("publishedTimeStr") or "").strip(),
            })
            if len(items) >= want:
                return items
        if len(results) < PAGE_SIZE:
            break
    return items


def _row(article: dict, channel: str, scraped_at: str, values: dict | None) -> dict:
    url = article["url"]
    row = {
        "period": _period_from_title(article["title"]),
        "title": article["title"],
        "url": url,
        "expr_volume": None,
        "revenue": None,
        "cr8": None,
        "growth_yoy": None,
        "channel": channel,
        "scraped_at": scraped_at,
        "source_url": url,
    }
    if values:
        row.update(values)
    return row


async def _run(limit: int) -> list[dict]:
    scraped_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    async with FetcherSession(
        impersonate="chrome120",
        timeout=25,
        verify=False,
        follow_redirects=True,  # 正文链接 http -> https 301
        retries=1,              # 不做重试风暴
    ) as session:
        # 1) 运行情况：逐篇抓正文解析业务量/收入/CR8
        articles = await _collect_list(session, CHANNEL_RUN, RUN_TITLE_KEYWORD, limit)
        for article in articles:
            if len(rows) >= limit:
                break
            resp = await _get(session, article["url"])
            if resp is None:
                continue  # 记录后跳过该期，不写脏行
            values = _parse_run_article(_strip_html(_resp_html(resp)))
            row = _row(article, "运行情况", scraped_at, values)
            if row["period"] is None:
                LOGGER.warning("spb-express: cannot derive period from %r; skip", article["title"])
                continue
            rows.append(row)
            await asyncio.sleep(ARTICLE_DELAY_S)

        # 2) 快递发展指数：列表元数据即可成行（正文无 CR8，数值字段留 None）
        if len(rows) < limit:
            metas = await _collect_list(session, CHANNEL_DEV, DEV_TITLE_KEYWORD, limit - len(rows))
            for meta in metas:
                row = _row(meta, "发展指数", scraped_at, None)
                if row["period"] is None:
                    LOGGER.warning("spb-express: cannot derive period from %r; skip", meta["title"])
                    continue
                rows.append(row)
                if len(rows) >= limit:
                    break

    rows = [r for r in rows if r["period"]]
    return rows[:limit]


def run_spb_express(limit: int = 100) -> list[dict]:
    """fd-runner 入口：国家邮政局快递月度运行情况 + 发展指数。"""
    return asyncio.run(_run(max(1, int(limit))))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for r in run_spb_express(limit=5):
        print(r)
