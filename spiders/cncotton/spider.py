"""spiders/cncotton —— 中国棉花网《棉花国际市场价格与贸易分析报告》月度数值。

数据表面（2026-11-08 实测）：

- **必须用 ``http://``**：``https://www.chinacotton.org/`` 的 443 端口从未部署
  （连接被 RST），``http://www.chinacotton.org/`` 返回 200（IIS 7.5 / ASP.NET）。
  改 https 会直接断流。
- 列表：``GET http://www.chinacotton.org/articleList.aspx?sort=6``
  = 「国际动态 / 棉花国际市场价格与贸易分析报告」。**单页列全 129 条**
  （articleID 408 起倒序到 30），无分页，一次取回。
  兄弟栏目：``sort=1`` 生产监测报告（月更）、``sort=2`` 供需平衡表（仅到 2023-09）、
  ``sort=4/5`` 宏观与研究（无数值）。
- 详情：``GET .../articlePage.aspx?articleID=N`` —— **正文为空**，只有标题、
  发布时间与一个 PDF 附件链接 ``/article/<ts>.pdf``。
- PDF：``GET http://www.chinacotton.org/article/<ts>.pdf``（实测 260–280KB / 3 页）。

**PDF 走文本层，不做 OCR**：实测这些 PDF 带真文本层，用平台共享层
``fd_industry_data.pdftext``（``pdftotext`` 单层包装）即可抽出中文正文；
``pdftext`` 明确不做 OCR（图片型 PDF 返回空串），本单元遇空文本层即跳过并计数。
这符合 2026-10-13「OCR 类源暂不处理」的裁决——本单元不引入任何 OCR。

**本单元产数值，不是事件面**：正文虽在 PDF 里，但措辞跨月稳定（2025-06/07/08
三期逐条实测），可正则抽出 9 类指标。

**短窗与停更（如实声明）**：站点最新一期是 **2025 年 8 月**，即自 2025-09 起已停更
（截至 2026-11 约 14 个月）。历史可回溯到 articleID 30 一代（2018 年前后），但每期
都要下一份 PDF，所以按 ``limit`` 从最新往旧取。**不伪造 2025-09 之后的期次。**

只解析本期报告口径（美国农业部对 ``YYYY/YY`` 年度的全球预测 + Cotlook A 指数 +
我国棉花进口）。只取能稳定定位的字段；抽不到就跳过并计数，**绝不补 0**。

只用标准库 ``urllib`` + ``re``，外加上面的平台共享层。
"""
from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

from fd_industry_data import pdftext

SOURCE = "cncotton"
BASE = "http://www.chinacotton.org/"
LIST_URL = BASE + "articleList.aspx?sort=6"
DETAIL_URL = BASE + "articlePage.aspx?articleID={aid}"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 60

_SEASON_RE = re.compile(r"(\d{4}/\d{2})年度")
_ITEM_RE = re.compile(r"(\d{4})年(\d{1,2})月")

#: 列表锚点：href 未加引号（``href=articlePage.aspx?articleID=408``），必须容忍
_LIST_ANCHOR_RE = re.compile(
    r"href=[\"']?articlePage\.aspx\?articleID=(\d+)[\"']?[^>]*>(.*?)</a>", re.S
)
_PDF_HREF_RE = re.compile(r"href=[\"']?(/article/[^\"'\s>]+\.pdf)", re.I)
_PUB_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})\s+\d{1,2}:\d{2}:\d{2}")

#: 方向词（多字在前：正则交替按书写顺序优先匹配长的，「同比减少」不会被当成「减少」+「少」）
_DIR = r"(增加|减少|上涨|下跌|增|减|涨|跌|降|持平)"
_SIGN_UP = {"增", "增加", "涨", "上涨"}
_SIGN_DOWN = {"减", "减少", "跌", "下跌", "降"}

#: 指标正则：都在「去空白后的紧凑串」上匹配（PDF 会把句子折行切开）
_M = {
    "planted_area": re.compile(
        r"全球棉花播种面积([\d.]+)万亩[（(]([\d.]+)万公顷[）)][，,]?同比" + _DIR + r"([\d.]*)%?"
    ),
    "production": re.compile(r"产量([\d.]+)万吨[，、,]?同比" + _DIR + r"([\d.]*)%?"),
    "consumption": re.compile(r"消费量([\d.]+)万吨[，、,]?同比" + _DIR + r"([\d.]*)%?"),
    "ending_stocks": re.compile(r"期末库存([\d.]+)万吨[，、,]?同比" + _DIR + r"([\d.]*)%?"),
    "stock_use_ratio": re.compile(
        r"库存消费比([\d.]+)%，?同比" + _DIR + r"([\d.]*)个百分点"
    ),
    "cotlook": re.compile(
        # 第一处带年份（「2025年7月Cotlook A指数…」），第二处只写月（「8月Cotlook A指数…」），
        # 所以年份必须可选，缺年份时用报告期年份补。
        r"(?:(\d{4})年)?(\d{1,2})月Cotlook\s*A?\s*指数月均价"
        r"(?:（截至[^）]*）|\(截至[^)]*\))?(?:为)?每磅([\d.]+)美分"
        r"[，,]?环比" + _DIR + r"([\d.]*)%[，、,]?同比" + _DIR + r"([\d.]*)%"
    ),
    "import_cum": re.compile(
        r"1至(\d{1,2})月我国棉花进口量([\d.]+)万吨[，,]?同比" + _DIR + r"([\d.]*)%"
    ),
    "import_month": re.compile(
        r"当月进口([\d.]+)万吨[，,]?同比" + _DIR + r"([\d.]*)%[，,]?环比" + _DIR + r"([\d.]*)%"
    ),
}

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# HTTP（国内直连，禁代理）
# --------------------------------------------------------------------------
def _build_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get_bytes(opener: urllib.request.OpenerDirector, url: str) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            status = getattr(resp, "status", 200)
            if status != 200:
                logger.warning("%s: HTTP %s — skip", url, status)
                return None
            return resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as exc:
        logger.warning("%s: fetch failed (%s) — skip", url, exc)
        return None


def _decode(payload: bytes) -> str:
    for enc in ("utf-8", "gb18030"):
        try:
            return payload.decode(enc)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------
def _compact(text: str) -> str:
    """去所有空白：PDF 折行会把「44415 万\\n亩」切成两半。"""
    return re.sub(r"\s+", "", text)


def _signed(value: str, direction: str) -> float | None:
    """方向词 + 数字 → signed float；「持平」= 0.0；无数字且非持平 → None。"""
    if direction == "持平":
        return 0.0
    if not value:
        return None
    num = float(value)
    if direction in _SIGN_UP:
        return num
    if direction in _SIGN_DOWN:
        return -num
    return num


def _period_from_title(title: str) -> str:
    """标题「2025年8月棉花国际市场价格与贸易分析报告」→ ``2025-08``。"""
    m = _ITEM_RE.search(title or "")
    if not m:
        return ""
    year, month = int(m.group(1)), int(m.group(2))
    return f"{year:04d}-{month:02d}" if 1 <= month <= 12 else ""


def _parse_list(page: str) -> list[dict]:
    out: list[dict] = []
    seen: set[int] = set()
    for m in _LIST_ANCHOR_RE.finditer(page):
        aid = int(m.group(1))
        if aid in seen:
            continue
        title = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", m.group(2))).strip()
        if not title:
            continue
        seen.add(aid)
        out.append({"article_id": aid, "title": title})
    # 列表本身倒序（新→旧）；再按 article_id 兜底排序，保证确定性
    out.sort(key=lambda r: r["article_id"], reverse=True)
    return out


def _parse_detail(page: str) -> tuple[str, str]:
    """详情页 → (pdf 绝对 URL, 发布日 YYYY-MM-DD)。取不到的部分为空串。"""
    pdf = ""
    m = _PDF_HREF_RE.search(page)
    if m:
        pdf = BASE.rstrip("/") + m.group(1)
    pub = ""
    pm = _PUB_RE.search(page)
    if pm:
        pub = f"{int(pm.group(1)):04d}-{int(pm.group(2)):02d}-{int(pm.group(3)):02d}"
    return pdf, pub


def _row(item: str, value: float, unit: str, *, sub_period: str,
         period: str, report_title: str, forecast_season: str,
         yoy: float | None = None, mom: float | None = None,
         quote_month: str | None = None,
         article_url: str, pdf_url: str, published_at: str,
         scraped_at: str) -> dict:
    return {
        "period": period,
        "report_title": report_title,
        "forecast_season": forecast_season,
        "item": item,
        "sub_period": sub_period,
        "quote_month": quote_month,
        "value": value,
        "unit": unit,
        "yoy_pct": yoy,
        "mom_pct": mom,
        "article_url": article_url,
        "pdf_url": pdf_url,
        "published_at": published_at,
        "scraped_at": scraped_at,
    }


def _extract_rows(text: str, *, period: str, report_title: str,
                  article_url: str, pdf_url: str,
                  published_at: str, scraped_at: str) -> list[dict]:
    """紧凑正文 → 长表行。抽不到的指标跳过并 warning，不补 0。"""
    body = _compact(text)
    sm = _SEASON_RE.search(body)
    season = sm.group(1) if sm else ""
    common = dict(
        period=period, report_title=report_title, forecast_season=season,
        article_url=article_url, pdf_url=pdf_url,
        published_at=published_at, scraped_at=scraped_at,
    )
    rows: list[dict] = []

    m = _M["planted_area"].search(body)
    if m:
        mu, ha, direction, pct = m.groups()
        yoy = _signed(pct, direction)
        rows.append(_row("planted_area", float(mu), "万亩",
                         sub_period="forecast_season", yoy=yoy, **common))
        rows.append(_row("planted_area", float(ha), "万公顷",
                         sub_period="forecast_season", yoy=yoy, **common))
    else:
        logger.warning("cncotton: %s: 播种面积 not found — skipped", period)

    for key, item in (
        ("production", "production"),
        ("consumption", "consumption"),
        ("ending_stocks", "ending_stocks"),
    ):
        m = _M[key].search(body)
        if not m:
            logger.warning("cncotton: %s: %s not found — skipped", period, item)
            continue
        value, direction, pct = m.groups()
        rows.append(_row(item, float(value), "万吨", sub_period="forecast_season",
                         yoy=_signed(pct, direction), **common))

    m = _M["stock_use_ratio"].search(body)
    if m:
        value, direction, pts = m.groups()
        rows.append(_row("stock_use_ratio", float(value), "%",
                         sub_period="forecast_season",
                         yoy=_signed(pts, direction), **common))
    else:
        logger.warning("cncotton: %s: 库存消费比 not found — skipped", period)

    # Cotlook A：第一篇 = 上月月均价；第二篇 = 本月（截至某日）均价
    cotlook = list(_M["cotlook"].finditer(body))
    if not cotlook:
        logger.warning("cncotton: %s: Cotlook A 指数 not found — skipped", period)
    for idx, cm in enumerate(cotlook[:2]):
        year, month, price, mom_dir, mom_pct, yoy_dir, yoy_pct = cm.groups()
        # 第二处（本月截至某日均价）原文不带年份 → 用报告期年份补
        quote_year = int(year) if year else int(period[:4] or 0)
        quote_month = (
            f"{quote_year:04d}-{int(month):02d}" if quote_year else None
        )
        rows.append(_row(
            "cotlook_a_index", float(price), "美分/磅",
            sub_period="prev_month_avg" if idx == 0 else "current_mtd_avg",
            quote_month=quote_month,
            yoy=_signed(yoy_pct, yoy_dir), mom=_signed(mom_pct, mom_dir),
            **common,
        ))

    m = _M["import_cum"].search(body)
    if m:
        upto, value, direction, pct = m.groups()
        rows.append(_row("import_cum_volume", float(value), "万吨",
                         sub_period=f"cumulative_{int(upto)}m",
                         yoy=_signed(pct, direction), **common))
    else:
        logger.warning("cncotton: %s: 累计进口 not found — skipped", period)

    m = _M["import_month"].search(body)
    if m:
        value, yoy_dir, yoy_pct, mom_dir, mom_pct = m.groups()
        rows.append(_row("import_month_volume", float(value), "万吨",
                         sub_period="month",
                         yoy=_signed(yoy_pct, yoy_dir), mom=_signed(mom_pct, mom_dir),
                         **common))
    else:
        logger.warning("cncotton: %s: 当月进口 not found — skipped", period)
    return rows


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def run_cncotton(
    limit: int = 30,
    window_start: str | None = None,
    window_end: str | None = None,
) -> list[dict]:
    """取《棉花国际市场价格与贸易分析报告》月度数值（长表：期 × 指标 × 口径）。

    ``limit`` 计**行数**；``window_start``/``window_end``（YYYY-MM 或 YYYY-MM-DD）
    按报告期（标题里的「YYYY年M月」）过滤，**先过滤后截断**。
    """
    if (window_start is None) != (window_end is None):
        raise ValueError(
            f"cncotton: window_start/window_end 须成对出现，"
            f"got start={window_start!r} end={window_end!r}"
        )
    lo = window_start.strip()[:7] if window_start else None
    hi = window_end.strip()[:7] if window_end else None
    if lo and hi and lo > hi:
        raise ValueError(f"cncotton: window_start {lo} 晚于 window_end {hi}")

    if not pdftext.available():
        raise RuntimeError(
            "cncotton: pdftotext 不可用（runner 镜像需装 poppler-utils）——"
            "本单元正文在 PDF 里，缺它无法出数"
        )

    opener = _build_opener()
    page = _get_bytes(opener, LIST_URL)
    if page is None:
        raise RuntimeError(f"cncotton: list page fetch failed: {LIST_URL}")
    entries = _parse_list(_decode(page))
    if not entries:
        raise RuntimeError(f"cncotton: 0 entries parsed from {LIST_URL}")

    scraped_at = datetime.now(timezone.utc).isoformat()
    collected: list[dict] = []
    fetched = skipped = 0
    for entry in entries:
        if limit is not None and len(collected) >= limit:
            break
        period = _period_from_title(entry["title"])
        if not period:
            # 标题里没有「YYYY年M月」→ 不是本单元的目标报告（栏目里混有评论/转载）
            logger.info("cncotton: skip non-report title: %s", entry["title"][:40])
            skipped += 1
            continue
        if lo and period < lo:
            continue
        if hi and period > hi:
            continue
        article_url = DETAIL_URL.format(aid=entry["article_id"])
        detail = _get_bytes(opener, article_url)
        if detail is None:
            skipped += 1
            continue
        pdf_url, published_at = _parse_detail(_decode(detail))
        if not pdf_url:
            logger.warning("cncotton: %s: no pdf attachment — skipped", article_url)
            skipped += 1
            continue
        payload = _get_bytes(opener, pdf_url)
        if payload is None or not pdftext.is_pdf(payload):
            logger.warning("cncotton: %s: pdf not fetched / not a PDF — skipped", pdf_url)
            skipped += 1
            continue
        try:
            text = pdftext.extract_text(payload, layout=False)
        except pdftext.PdfTextError as exc:
            logger.warning("cncotton: %s: pdf text layer failed (%s) — skipped",
                           pdf_url, exc)
            skipped += 1
            continue
        if not text.strip():
            # 图片型 PDF：无文本层。不 OCR，跳过并计数。
            logger.warning("cncotton: %s: empty text layer (image-only PDF) — skipped",
                           pdf_url)
            skipped += 1
            continue
        rows = _extract_rows(
            text, period=period, report_title=entry["title"],
            article_url=article_url, pdf_url=pdf_url,
            published_at=published_at, scraped_at=scraped_at,
        )
        if not rows:
            skipped += 1
            continue
        fetched += 1
        collected.extend(rows)

    if not collected:
        raise RuntimeError(
            f"cncotton: 0 rows parsed from {len(entries)} listed article(s) "
            f"({fetched} report(s) used, {skipped} skipped)"
        )
    logger.info("cncotton: %d rows from %d report(s); %d skipped",
                len(collected), fetched, skipped)
    return collected[: max(int(limit), 0)] if limit is not None else collected


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for row in run_cncotton(limit=12):
        print(row)
