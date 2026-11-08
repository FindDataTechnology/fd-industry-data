"""spiders/moa-machinery —— 农业农村部《全国农业机械化发展统计公报》（年度）取数。

数据表面（2026-11-08 实测，国内直连，禁代理）：

- 列表页 ``GET https://njhs.moa.gov.cn/nyjxhqk/``（charset UTF-8，单页静态、无分页）
  每条公报一个 ``<li>``：

      <li><a href="./202610/t20261019_6488733.htm" title="2025年全国农业机械化发展统计公报">
          <span class="sj_gztzle">2025年全国农业机械化发展统计公报</span>
          <span class="sj_gztzri">2026-10-19</span></a></li>

  实测可见期数 2022–2025 共四期（2025 期发布于 2026-10-19）。
- 正文页 ``GET https://njhs.moa.gov.cn/nyjxhqk/<yyyymm>/t<yyyymmdd>_<id>.htm``：
  标题 ``<h1 class="sj_xiang_biaoti">``，发布日期「日期：YYYY-MM-DD」，
  正文 ``div.sj_arc_body > div.TRS_Editor``，含 1 张作物机械化率表 + 三段文字。

抽取口径（只抽措辞跨年稳定的指标；2022/2024/2025 三期逐条实测一致）：

  段一 全国农业机械化发展水平
    综合机械化率 / 机耕率 / 机播率 / 机收率，以及 8 个作物的耕种收综合机械化率（表）
  段二 全国农业机械拥有量发展情况
    拖拉机拥有量 / 配套农具 / 农用植保无人驾驶航空器
  段三 全国农机服务组织及人员情况
    农机专业合作社 / 乡村农机从业人员 / 农机服务收入 / 五项作业面积

刻意不抽的：谷物联合收割机、玉米收获机、水稻插秧机的「拥有量分别达到…」——
三个品种的中文枚举顺序逐年在换（2022 期收割机在前，2024/2025 期插秧机在前），
按位置抽会张冠李戴；农业机械总动力只有 2022 期给（统计口径已变）。宁缺不错。

坑位：

- 同一列表 URL 偶发返回同模板的其它栏目（实测出现过「通知公告」频道页面，
  正文无公报链接）。``_fetch_list`` 校验候选链接，取空即重试，重试仍空抛
  ``RuntimeError``——不静默返回空列表。
- 指标句在 HTML 里被 ``<font>``/``<span>`` 切碎，直接对原始 HTML 跑正则会漏；
  必须先剥标签、反转义、去空白，再匹配。
- ``period`` = 期号年份（标题里的「2025年」），不是发布日期年份（2025 期发布于
  2026-10-19）。
- 解析失败一律跳过并留痕（``logger.warning``），**绝不写 0**；某期一个指标都没
  抽到即整期跳过。全部期次为空 → ``RuntimeError``（失败即红）。
- TLS：``*.moa.gov.cn`` 为 CFCA OV 公开站点，无凭据可泄；容器信任库不保证，
  按 ``moa-hog-price`` 同源惯例不对该 host 做证书校验。一律禁用代理。

入口：``run_moa_machinery(limit=20, window_start=None, window_end=None)``。
``limit`` 计「期数」（每期多行），最新期在前。首爬回补窗口按**发布日期**过滤，
与平台 dispatcher 的「滚动窗口」语义一致；不带窗口时行为不变。
"""
from __future__ import annotations

import html as html_lib
import logging
import re
import ssl
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from urllib.parse import urljoin

logger = logging.getLogger("moa-machinery")

SOURCE = "moa-machinery"
LIST_URL = "https://njhs.moa.gov.cn/nyjxhqk/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 45  # 秒；国内直连，实测单页 50–80 KB
LIST_ATTEMPTS = 3

# ---------------------------------------------------------------- 列表 / 正文

_A_RE = re.compile(r"(?is)<a\b([^>]*)>(.*?)</a>")
_HREF_ATTR_RE = re.compile(r"""(?is)\bhref\s*=\s*["']([^"']+)["']""")
_TITLE_ATTR_RE = re.compile(r"""(?is)\btitle\s*=\s*["']([^"']*)["']""")
_ARTICLE_HREF_RE = re.compile(r"(?:^|/)\d{6}/t\d{8}_\d+\.htm$")
_TITLE_SPAN_RE = re.compile(
    r"""(?is)<span[^>]*class=["'][^"']*sj_gztzle[^"']*["'][^>]*>(.*?)</span>""")
_DATE_SPAN_RE = re.compile(
    r"""(?is)<span[^>]*class=["'][^"']*sj_gztzri[^"']*["'][^>]*>(.*?)</span>""")
_H1_RE = re.compile(
    r"""(?is)<h1[^>]*class=["'][^"']*sj_xiang_biaoti[^"']*["'][^>]*>(.*?)</h1>""")
_PAGE_TITLE_RE = re.compile(r"(?is)<title>(.*?)</title>")
_PUB_DATE_RE = re.compile(r"日期[：:]\s*(\d{4})-(\d{1,2})-(\d{1,2})")
_BULLETIN_TITLE_RE = re.compile(r"^(\d{4})年全国农业机械化发展统计公报")
_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style|noscript)\b.*?</\1>")
_COMMENT_RE = re.compile(r"(?s)<!--.*?-->")
_BLOCK_TAG_RE = re.compile(
    r"(?is)</?(?:p|div|br|tr|td|th|li|ul|ol|table|h[1-6]|section)\b[^>]*>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_TABLE_RE = re.compile(r"(?is)<table\b.*?</table>")
_TR_RE = re.compile(r"(?is)<tr\b.*?</tr>")
_CELL_RE = re.compile(r"(?is)<t[dh]\b[^>]*>(.*?)</t[dh]>")

# ---------------------------------------------------------------- 指标定义

# (item, 正则, unit)；正则跑在「去空白」的正文段上。
_YEAR_METRICS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("operated_area_mu_times", re.compile(r"五项作业面积达(?:到)?([\d.]+)亿亩次"), "亿亩次"),
    ("mechanization_rate", re.compile(r"农作物耕种收综合机械化率达(?:到)?([\d.]+)%"), "%"),
    ("tractor_count", re.compile(r"拖拉机拥有量([\d.]+)万台"), "万台"),
    ("tractor_implement_count", re.compile(r"配套农具([\d.]+)万部"), "万部"),
    ("uav_count", re.compile(r"无人驾驶航空器拥有量达(?:到)?([\d.]+)万架"), "万架"),
    ("cooperative_count", re.compile(r"农机专业合作社([\d.]+)万个"), "万个"),
    ("operator_count", re.compile(r"乡村农机从业人员([\d.]+)万人"), "万人"),
    ("service_income", re.compile(r"农机服务收入([\d.]+)亿元"), "亿元"),
)

# 机耕率 / 机播率 / 机收率 同句三值。
_TILLAGE_SENTENCE_RE = re.compile(
    r"机耕率、机播率、机收率分别达到([\d.]+)%、([\d.]+)%、([\d.]+)%")
_TILLAGE_ITEMS = ("tillage_rate", "seeding_rate", "harvest_rate")

# 作物表：行 = [作物, 当年耕种收综合机械化率(%), 较上年提高(百分点)]，8 行四年不变。
_CROP_SLUGS = {
    "小麦": "wheat",
    "水稻": "rice",
    "玉米": "corn",
    "大豆": "soybean",
    "油菜": "rapeseed",
    "马铃薯": "potato",
    "花生": "peanut",
    "棉花": "cotton",
}

_BODY_START = "一、全国农业机械化发展水平"
_BODY_END = "机关子站"


# ---------------------------------------------------------------- 工具


def _decode(body: bytes) -> str:
    """实测 UTF-8；防御性回退 gb18030。"""
    for enc in ("utf-8", "gb18030"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _compact(text: str) -> str:
    """去全部空白（含 \\u3000 / \\xa0），便于跨标签句子正则。"""
    return re.sub(r"\s+", "", text or "")


def _strip_tags(fragment: str) -> str:
    return html_lib.unescape(_TAG_RE.sub("", fragment or ""))


def _open(url: str) -> str:
    """GET → 文本。显式空 ProxyHandler：仓规「国内源禁代理」。"""
    handlers: list[urllib.request.BaseHandler] = [urllib.request.ProxyHandler({})]
    if "moa.gov.cn" in url:
        # 不假设容器信任库；公开数据站点，无凭据可泄。
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
    )
    with opener.open(req, timeout=TIMEOUT) as resp:  # noqa: S310
        return _decode(resp.read())


def _page_title(page: str) -> str:
    m = _PAGE_TITLE_RE.search(page or "")
    return _squash(_strip_tags(m.group(1))) if m else ""


def _parse_date(text: str) -> str | None:
    m = re.search(r"(\d{4})[-./年](\d{1,2})[-./月](\d{1,2})", text or "")
    if not m:
        return None
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


# ---------------------------------------------------------------- 列表解析


def _parse_list(page: str) -> list[dict]:
    """列表页 → 公报条目（url/title/pub_date），按发布日期倒序。"""
    items: dict[str, dict] = {}
    for m in _A_RE.finditer(page or ""):
        attrs, inner = m.group(1), m.group(2)
        hm = _HREF_ATTR_RE.search(attrs)
        if not hm:
            continue
        href = hm.group(1).strip()
        if not _ARTICLE_HREF_RE.search(href):
            continue
        title = ""
        tm = _TITLE_SPAN_RE.search(inner)
        if tm:
            title = _squash(_strip_tags(tm.group(1)))
        if not title:
            am = _TITLE_ATTR_RE.search(attrs)
            title = _squash(html_lib.unescape(am.group(1))) if am else ""
        if not title:
            title = _squash(_strip_tags(inner))
        if not _BULLETIN_TITLE_RE.match(title):
            continue
        dm = _DATE_SPAN_RE.search(inner)
        pub = _parse_date(_strip_tags(dm.group(1))) if dm else None
        url = urljoin(LIST_URL, href)
        items[url] = {"url": url, "title": title, "pub_date": pub}
    return sorted(
        items.values(),
        key=lambda r: (r["pub_date"] or "", r["title"], r["url"]),
        reverse=True,
    )


def _fetch_list() -> list[dict]:
    """列表页取数；偶发频道串页（无公报链接）→ 重试，仍空则响亮失败。"""
    last: object = None
    for attempt in range(1, LIST_ATTEMPTS + 1):
        try:
            page = _open(LIST_URL)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last = exc
            logger.warning("moa-machinery: list fetch failed (attempt %d/%d): %s",
                           attempt, LIST_ATTEMPTS, exc)
            time.sleep(1)
            continue
        items = _parse_list(page)
        if items:
            return items
        last = "no bulletin links"
        logger.warning("moa-machinery: list page has no bulletin links "
                       "(attempt %d/%d, page title=%r)",
                       attempt, LIST_ATTEMPTS, _page_title(page))
        time.sleep(1)
    raise RuntimeError(f"moa-machinery: cannot read bulletin list from {LIST_URL}: {last}")


# ---------------------------------------------------------------- 正文解析


def _body_text(page: str) -> str:
    """正文段（一、…→机关子站）的去空白纯文本；找不到标记则回退全文。"""
    text = _COMMENT_RE.sub("", page or "")
    text = _SCRIPT_STYLE_RE.sub("", text)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = html_lib.unescape(_TAG_RE.sub("", text))
    compact = _compact(text)
    start = compact.find(_BODY_START)
    if start < 0:
        return compact
    end = compact.find(_BODY_END, start)
    return compact[start:end] if end > start else compact[start:]


def _parse_crop_rows(page: str) -> list[tuple[str, float]]:
    """作物表 → [(作物名, 机械化率)]，跳过表头与非数值行。"""
    table = _TABLE_RE.search(page or "")
    if not table:
        return []
    out: list[tuple[str, float]] = []
    for tr in _TR_RE.finditer(table.group(0)):
        cells = [_squash(_strip_tags(c)) for c in _CELL_RE.findall(tr.group(0))]
        if len(cells) < 2 or cells[0] not in _CROP_SLUGS:
            continue
        try:
            value = float(cells[1])
        except ValueError:
            continue
        out.append((cells[0], value))
    return out


def _extract_page(page: str, url: str, list_title: str,
                  scraped_at: str) -> list[dict]:
    """一期公报 → 行列表；一个指标都没抽到返回 []（由调用方整期跳过）。"""
    h1 = _H1_RE.search(page or "")
    title = _squash(_strip_tags(h1.group(1))) if h1 else list_title
    title = title or list_title

    pm = _BULLETIN_TITLE_RE.match(title)
    if pm:
        period = pm.group(1)
    else:
        dm = _PUB_DATE_RE.search(page or "")
        if not dm:
            logger.warning("moa-machinery: %s: no edition year in title %r — skip",
                           url, title)
            return []
        period = f"{int(dm.group(1)):04d}"

    body = _body_text(page)
    rows: list[dict] = []

    def emit(item: str, value: float, unit: str) -> None:
        rows.append({
            "period": period,
            "item": item,
            "value": value,
            "unit": unit,
            "title": title,
            "source_url": url,
            "scraped_at": scraped_at,
        })

    for item, pattern, unit in _YEAR_METRICS:
        m = pattern.search(body)
        if not m:
            logger.warning("moa-machinery: %s (%s): item %s not found — skipped",
                           url, period, item)
            continue
        try:
            emit(item, float(m.group(1)), unit)
        except ValueError:
            logger.warning("moa-machinery: %s (%s): item %s value %r unparsable — skipped",
                           url, period, item, m.group(1))

    m = _TILLAGE_SENTENCE_RE.search(body)
    if m:
        for item, raw in zip(_TILLAGE_ITEMS, m.groups()):
            try:
                emit(item, float(raw), "%")
            except ValueError:
                logger.warning("moa-machinery: %s (%s): item %s value %r unparsable — skipped",
                               url, period, item, raw)
    else:
        logger.warning("moa-machinery: %s (%s): 机耕/机播/机收率 sentence not found — skipped",
                       url, period)

    crops = _parse_crop_rows(page)
    if not crops:
        logger.warning("moa-machinery: %s (%s): crop mechanization table not found — skipped",
                       url, period)
    for crop, value in crops:
        emit(f"crop_mechanization_rate_{_CROP_SLUGS[crop]}", value, "%")

    if not rows:
        logger.warning("moa-machinery: %s (%s): no metric parsed — edition skipped",
                       url, period)
    return rows


# ---------------------------------------------------------------- 窗口


def _window(window_start: str | None, window_end: str | None) -> tuple[date, date] | None:
    """首爬回补窗口（YYYY-MM-DD，平台注入）；须成对出现，非法即抛。"""
    if window_start is None and window_end is None:
        return None
    if not window_start or not window_end:
        raise ValueError(
            "moa-machinery: window 参数须成对出现，"
            f"got start={window_start!r} end={window_end!r}")
    try:
        start = datetime.strptime(str(window_start), "%Y-%m-%d").date()
        end = datetime.strptime(str(window_end), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(
            "moa-machinery: window 须为 YYYY-MM-DD，"
            f"got start={window_start!r} end={window_end!r}") from exc
    if start > end:
        raise ValueError(f"moa-machinery: window_start {start} > window_end {end}")
    return start, end


def _in_window(pub_date: str | None, window: tuple[date, date] | None) -> bool:
    if window is None:
        return True
    if not pub_date:
        # 发布日期缺失时无法判定：保留该期（行内 source_url 可回溯）。
        return True
    return window[0] <= date.fromisoformat(pub_date) <= window[1]


# ---------------------------------------------------------------- 入口


def run_moa_machinery(
    limit: int = 20,
    window_start: str | None = None,
    window_end: str | None = None,
) -> list[dict]:
    """最新在前取 ≤limit 期《全国农业机械化发展统计公报》的年度指标行。

    ``limit`` 计期数（每期约 20 行）。``window_start``/``window_end`` 为平台
    首爬回补窗口（YYYY-MM-DD），按公报**发布日期**过滤，且先过滤后截断。
    """
    win = _window(window_start, window_end)
    items = _fetch_list()
    if win is not None:
        items = [r for r in items if _in_window(r["pub_date"], win)]
    items = items[: max(int(limit), 0)] if limit is not None else items
    if not items:
        logger.warning("moa-machinery: no bulletin in requested window/limit "
                       "(window=%s, limit=%s)", win, limit)
        return []

    scraped_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    failures = 0
    for entry in items:
        try:
            page = _open(entry["url"])
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            failures += 1
            logger.warning("moa-machinery: fetch failed for %s — skipped: %s",
                           entry["url"], exc)
            continue
        rows.extend(_extract_page(page, entry["url"], entry["title"], scraped_at))

    if not rows:
        raise RuntimeError(
            f"moa-machinery: 0 rows parsed from {len(items)} bulletin(s) "
            f"({failures} fetch failure(s))")
    if failures:
        logger.warning("moa-machinery: %d/%d bulletin page(s) failed to fetch",
                       failures, len(items))
    return rows


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    got = run_moa_machinery(limit=1)
    print(f"rows={len(got)}")
    for row in got[:5]:
        print(row)
