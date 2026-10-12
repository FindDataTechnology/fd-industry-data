"""cninfo-operating —— 上市公司月度经营数据（产销快报/销售简报/发电量/运营数据/经营简报）.

覆盖农业（牧原/天邦生猪销售简报等）、房地产（保利/新城月度销售与经营简报）、
金融与能源投资（龙源电力发电量、中国核电、REITs 运营数据等）三个普惠金融优先方向。

数据来源 = 两段式文本通道（**全链路不解析 PDF**，spec 硬约束）：

第一段 · 发现（巨潮资讯网 cninfo 公告检索 API，免鉴权）::

    POST http://www.cninfo.com.cn/new/hisAnnouncement/query
    Content-Type: application/x-www-form-urlencoded; charset=UTF-8
    X-Requested-With: XMLHttpRequest
    body: pageNum / pageSize / tabName=fulltext / searchkey / seDate=<a>~<b> / isHLtitle=true

    响应 announcements[] 关键字段：
      announcementId, secCode, secName, announcementTitle（含 <em> 高亮标签，须剥离）,
      announcementTime（毫秒时间戳）, adjunctUrl（相对路径，完整 URL =
      "http://static.cninfo.com.cn/" + adjunctUrl）

第二段 · 正文（东方财富公告正文 API，返回纯文本）::

    GET https://np-cnotice-stock.eastmoney.com/api/content/ann
          ?art_code=<art_code>&client_source=web&page_index=1
    响应 data.notice_content = 正文纯文本；data.attach_url = PDF 直链

坑位（实测 2026-10-12，逐条落实在此实现里）：

1. **cninfo 详情页没有正文**。``bulletin_detail?announcementId=`` 返回的 HTML 里没有
   正文文本（实测搜不到「产销」「万辆」等字样），只有 meta 与下载入口；
   ``static.cninfo.com.cn/<adjunctUrl>`` 是 PDF。因此正文**必须**走第二段。
   本单元把 ``adjunct_url`` 仅作为溯源列输出，**从不下载、从不解析**它。

2. **cninfo 给 announcementId，东财要 art_code**——两者不是同一个 id，无法换算。
   桥接办法：用东财公告列表 API 按股票代码检索::
       GET https://np-anotice-stock.eastmoney.com/api/security/ann
             ?sr=-1&page_size=<n>&page_index=1&ann_type=A&client_source=web
             &stock_list=<secCode>&f_node=0&s_node=0
   再按标题匹配出 art_code。匹配用「去 <em>、去标点空白」的标题包含关系
   ``cninfo 标题 ⊂ 东财标题``（东财标题形如 ``广汽集团:广汽集团2026年9月产销快报``，
   前缀是股票简称 + 冒号），比裸子串匹配命中率高一个量级。

3. **港股/REITs 代码在东财列表里查不到**。``stock_list=02238 / 02714 / 09927 /
   00295 / 00916 / 180201`` 等返回 total_hits=0。这不是失败，是本源的已知覆盖边界
   ——遇到就记 warning 跳过，**绝不猜数**。

4. **标题含 <em> 标签**（``isHLtitle=true`` 的高亮），必须剥离后再落 title 列
   和再做上下游匹配，否则命中率归零。

5. **同一公司同标题在东财列表里可能有多条**（A/H 双份、更正稿）。取 notice_date
   最新的一条，并用 ``(sec_code, art_code)`` 跨关键词去重。

6. **cninfo 偶发 504 Gateway Time-out**（全文检索后端不稳）。按「网络失败 = 记
   warning 跳过、不重试」契约处理，见 ``_fetch_json`` 的 ``attempts=1``。

7. 有些标题词与实际公告不完全一致（如搜索「销售简报」会命中
   「…商品猪销售情况简报」）。因此类别判定按标题**内容**而非搜索词。

依赖：仅标准库 ``json / urllib / re / datetime / time``。无 PDF 库，无三方库。
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("cninfo-operating")

SOURCE = "cninfo-operating"

# ---------------------------------------------------------------- endpoints
CNINFO_SEARCH_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STATIC_BASE = "http://static.cninfo.com.cn/"
EM_ANN_LIST_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
EM_ANN_CONTENT_URL = "https://np-cnotice-stock.eastmoney.com/api/content/ann"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------- 参数白名单
# 巨潮：只发这 6 个字段（不多发 none；实测多发会失真或不生效）。
CNINFO_PARAM_KEYS = frozenset({
    "pageNum", "pageSize", "tabName", "searchkey", "seDate", "isHLtitle",
})
# 东财列表：只在审计过的白名单里取值。
EM_LIST_PARAMS = {"ann_type": "A", "client_source": "web", "sr": "-1",
                  "f_node": "0", "s_node": "0"}
EM_CONTENT_PARAMS = {"client_source": "web", "page_index": "1"}

EM_LIST_MAX_PAGES = 6      # 东财列表翻页硬上限（礼貌 + 有界；命中即停）
LIST_WINDOW_PAD_DAYS = 7  # 列表翻页下限相对 seDate 起点再往前留一周冗余
WINDOW_DAYS = 45          # 未显式给 start_date 时的默认滚动窗口天数

TIMEOUT_S = 25
SAME_HOST_GAP_S = 0.4          # 礼貌间隔：同源请求之间的最小间隔
MIN_CONTENT_CHARS = 50         # 正文过短 -> 视为取数失败，跳过
CONTENT_TRUNCATE = 4000        # content 列硬上限（>60KB 的公告会撑爆下游）

# ---------------------------------------------------------------- probes
# 平面化标题词 -> 类别。顺序即优先级（先命中先分类）。
KEYWORD_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("产销快报", "产销快报"),
    ("产销数据", "产销快报"),
    ("销售简报", "销售简报"),
    ("销售情况简报", "销售简报"),
    ("发电量", "发电量"),
    ("主要运营数据", "主要运营数据"),
    ("运营数据", "主要运营数据"),
    ("经营简报", "经营简报"),
    ("经营情况简报", "经营简报"),
    ("生产经营数据", "经营简报"),
)
SEARCHKEYS: tuple[str, ...] = (
    "产销快报", "销售简报", "发电量", "主要运营数据", "经营简报",
)

_EM_TAG_RE = re.compile(r"</?em>", re.IGNORECASE)
_STRIP_WS_RE = re.compile(r"\s+")
# 标题归一化：去掉所有标点/空白/冒号的中英文变体，避免「东财前缀简称+冒号」与
# 「cninfo 全角空格」带来的假失配。
_NORMALIZE_RE = re.compile(
    r"[\s:：,，。、；;（）()\[\]【】《》<>“\"'’`·\-—–_/\\|+*#]+"
)
_PERIOD_RE = re.compile(r"(20\d{2})\s*[年\-/.]\s*(\d{1,2})\s*月")
_PERIOD_NUM_RE = re.compile(r"(20\d{2})(\d{2})")


def _http_get_json(url: str, params: dict[str, str]) -> dict:
    """GET JSON，单次尝试、不重试。失败抛异常由调用方 log-and-skip。"""
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        full,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _http_post_form(url: str, params: dict[str, str]) -> dict:
    """POST 表单 JSON，单次尝试、不重试。"""
    body = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def strip_em_tags(title: str) -> str:
    """剥离 isHLtitle=true 注入的 <em> 高亮标签并压平空白。"""
    return _STRIP_WS_RE.sub(" ", _EM_TAG_RE.sub("", title or "")).strip()


def normalize_title(title: str) -> str:
    """标题归一化：去 <em>、去标点空白，用于跨站标题包含匹配。"""
    return _NORMALIZE_RE.sub("", strip_em_tags(title)).upper()


def _shift_days(iso_date: str, days: int) -> str:
    """``YYYY-MM-DD`` 平移若干天；输入非法则返回原值（不放大为异常）。"""
    try:
        d = datetime.strptime(iso_date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return iso_date
    return (d + timedelta(days=days)).strftime("%Y-%m-%d")


def _sleep_gap(last_at: float) -> float:
    """同源节流：保证两次请求间隔 >= SAME_HOST_GAP_S，返回新时间戳。"""
    elapsed = time.monotonic() - last_at
    if 0 <= elapsed < SAME_HOST_GAP_S:
        time.sleep(SAME_HOST_GAP_S - elapsed)
    return time.monotonic()


def ms_to_iso(ms: int | float | None) -> str:
    """cninfo announcementTime（毫秒时间戳）-> ISO date ``YYYY-MM-DD``。"""
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def classify(title: str) -> str:
    """按标题内容判定披露类别；未命中返回空串（调用方跳过）。"""
    plain = strip_em_tags(title)
    for needle, category in KEYWORD_CATEGORIES:
        if needle in plain:
            return category
    return ""


def extract_period(title: str, iso_date: str) -> str:
    """推断数据期 ``YYYY-MM``。

    优先取标题里的 ``2026 年 9 月`` 型炸链；标题无明文日期时回退到公告日的
    **上一个自然月**（月度经营数据披露惯例：9 月数据在 10 月发布），
    并绝不猜测具体数值。
    """
    m = _PERIOD_RE.search(strip_em_tags(title))
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = _PERIOD_NUM_RE.search(strip_em_tags(title))
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    if iso_date:
        try:
            d = datetime.strptime(iso_date, "%Y-%m-%d")
            y, mo = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
            return f"{y:04d}-{mo:02d}"
        except ValueError:
            return ""
    return ""


def _cninfo_search(searchkey: str, start_date: str, end_date: str,
                   page_size: int) -> list[dict]:
    """第一段：巨潮全文检索。失败抛异常由调用方处理。"""
    params = {
        "pageNum": "1",
        "pageSize": str(page_size),
        "tabName": "fulltext",
        "searchkey": searchkey,
        "seDate": f"{start_date}~{end_date}",
        "isHLtitle": "true",
    }
    assert frozenset(params) == CNINFO_PARAM_KEYS  # 守住"只发实测字段"
    payload = _http_post_form(CNINFO_SEARCH_URL, params)
    return payload.get("announcements") or []


def _em_ann_list(sec_code: str, page_size: int = 100,
                 floor_date: str = "") -> list[dict]:
    """第二段前半：按股票代码取东财公告列表。

    列表按 ``notice_date`` 倒序（``sr=-1``），因此只要翻到本批公告所属的报告期
    的最早日期即可停。``floor_date`` = 本次巨潮窗口的起始日 **前推 7 天**（留
    一条冗余），保证即使 ``seDate`` 窗口被拨到很早（如 golden 重放的固定历史窗），
    也能翻到对应的那份公告，而不是只认最近两页。

    仍有硬上限 ``EM_LIST_MAX_PAGES``：既是对上游的礼貌，也让最坏情况有界。
    失败（单个分页请求异常）即停，已取到的部分照常返回。
    """
    out: list[dict] = []
    for page in range(1, EM_LIST_MAX_PAGES + 1):
        params = dict(EM_LIST_PARAMS)
        params.update({
            "stock_list": sec_code,
            "page_size": str(page_size),
            "page_index": str(page),
        })
        try:
            payload = _http_get_json(EM_ANN_LIST_URL, params)
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
            logger.warning("em list failed %s page=%d: %s — skip", sec_code, page, e)
            break
        items = ((payload.get("data") or {}).get("list")) or []
        if not items:
            break
        out.extend(items)
        oldest = str(items[-1].get("notice_date") or "")[:10]
        if len(items) < page_size:
            break
        if floor_date and oldest and oldest <= floor_date:
            break   # 已覆盖到窗口起点，不必再翻
    return out


def match_art_code(cninfo_title: str, em_items: list[dict]) -> dict | None:
    """在东财列表里找 cninfo 公告对应的条目。

    匹配口径：归一化后 ``cninfo 标题 ⊂ 东财标题``（或反之），去 <em> 与标点。
    多命中取 ``notice_date`` 最新的那条（A/H 双份、更正稿场景）。
    匹配不到返回 None —— 调用方必须跳过，**绝不猜怒 art_code**。
    """
    target = normalize_title(cninfo_title)
    if not target:
        return None
    hits: list[dict] = []
    for item in em_items:
        cand = normalize_title(str(item.get("title") or ""))
        if cand and (target in cand or cand in target):
            hits.append(item)
    if not hits:
        return None
    hits.sort(key=lambda x: str(x.get("notice_date") or ""), reverse=True)
    chosen = hits[0]
    art_code = str(chosen.get("art_code") or "").strip()
    return chosen if art_code else None


def _em_content(art_code: str) -> tuple[str, str]:
    """第二段后半：取东财公告正文纯文本，返回 ``(content, attach_url)``。"""
    params = dict(EM_CONTENT_PARAMS)
    params["art_code"] = art_code
    payload = _http_get_json(EM_ANN_CONTENT_URL, params)
    data = payload.get("data") or {}
    raw = str(data.get("notice_content") or "")
    content = _STRIP_WS_RE.sub(" ", raw).strip()
    attach = str(data.get("attach_url") or "").strip()
    return content, attach


def _collect_rows(limit: int, category: str, searchkeys: tuple[str, ...],
                  start_date: str, end_date: str, page_size: int) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    em_cache: dict[str, list[dict]] = {}
    cninfo_gate = 0.0
    em_gate = 0.0
    skipped = 0
    # 东财列表翻页下限：窗口起点前推一周，保证历史窗口也能翻到对应的那份公告
    floor_date = _shift_days(start_date, -LIST_WINDOW_PAD_DAYS) if start_date else ""

    # —— 第一段：把 5 个搜索词的检索结果先全部落地（每词 page_size 条）
    announcements_by_key: dict[str, list[dict]] = {}
    for searchkey in searchkeys:
        cninfo_gate = _sleep_gap(cninfo_gate)
        try:
            announcements_by_key[searchkey] = _cninfo_search(
                searchkey, start_date, end_date, page_size
            )
        except (urllib.error.URLError, urllib.error.HTTPError,
                json.JSONDecodeError, TimeoutError) as e:
            # 巨潮偶发 504；按契约 log-and-skip，不重试、不绕过
            logger.warning("cninfo search '%s' failed: %s — skip", searchkey, e)
            announcements_by_key[searchkey] = []
    if not any(announcements_by_key.values()):
        logger.warning("cninfo-operating: every searchkey failed — returning empty")
        return []

    # —— 轮转交织（round-robin）：否则 limit 小时永远只有第一个搜索词的类目，
    #     而本源的价值恰在于农业/房地产/金融三领域同批覆盖。
    cursors: dict[str, int] = dict.fromkeys(announcements_by_key, 0)
    pending = [k for k, v in announcements_by_key.items() if v]

    while pending and len(rows) < limit:
        for searchkey in list(pending):
            if len(rows) >= limit:
                break
            i = cursors[searchkey]
            announcements = announcements_by_key[searchkey]
            if i >= len(announcements):
                pending.remove(searchkey)
                continue
            cursors[searchkey] = i + 1
            ann = announcements[i]

            sec_code = str(ann.get("secCode") or "").strip()
            sec_name = str(ann.get("secName") or "").strip()
            raw_title = str(ann.get("announcementTitle") or "")
            title = strip_em_tags(raw_title)
            announcement_id = str(ann.get("announcementId") or "").strip()
            if not (sec_code and title and announcement_id):
                skipped += 1
                continue

            # —— 坑 1：标题 -> 披露类别，不在白名单就跳过（不猜数）
            cat = classify(title)
            if not cat:
                skipped += 1
                continue
            if category and cat != category:
                continue

            # —— 坑 2：按 (sec_code, announcement_id) 去重（同一份跨关键词重复）
            dedupe_key = (sec_code, announcement_id)
            if dedupe_key in seen:
                continue

            # —— 坑 3：桥接 art_code（每段请求失败 -> 跳过，不重试）
            if sec_code not in em_cache:
                em_gate = _sleep_gap(em_gate)
                em_cache[sec_code] = _em_ann_list(sec_code, floor_date=floor_date)
            match = match_art_code(title, em_cache[sec_code])
            if match is None:
                # 港股 / REITs / 东财未收录 -> 已知盲区，留痕跳过
                logger.info(
                    "no eastmoney art_code for %s %s «%.40s» — skip" %
                    (sec_code, sec_name, title),
                )
                skipped += 1
                continue
            art_code = str(match.get("art_code") or "").strip()

            em_gate = _sleep_gap(em_gate)
            try:
                content, attach_url = _em_content(art_code)
            except (urllib.error.URLError, urllib.error.HTTPError,
                    json.JSONDecodeError, TimeoutError) as e:
                logger.warning("content fetch failed %s/%s: %s — skip",
                               sec_code, art_code, e)
                skipped += 1
                continue

            # —— 坑 4：正文为空/过短 -> 跳过（绝不造值）
            if len(content) < MIN_CONTENT_CHARS:
                logger.info("content too short (%d chars) %s/%s — skip",
                            len(content), sec_code, art_code)
                skipped += 1
                continue

            iso_date = ms_to_iso(ann.get("announcementTime"))
            adjunct_url = str(ann.get("adjunctUrl") or "").strip()
            seen.add(dedupe_key)
            rows.append({
                "sec_code": sec_code,
                "sec_name": sec_name,
                "announcement_title": title,
                "announcement_id": announcement_id,
                "art_code": art_code,
                "announce_date": iso_date,
                "period": extract_period(title, iso_date),
                "category": cat,
                "content": content[:CONTENT_TRUNCATE],
                "adjunct_url": (CNINFO_STATIC_BASE + adjunct_url) if adjunct_url else "",
                "source": SOURCE,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            })

    logger.info(
        "cninfo-operating: %d rows (skipped/unmatched: %d, keys: %s)",
        len(rows), skipped, ",".join(searchkeys),
    )
    return rows


def run_cninfo_operating(
    limit: int = 100,
    category: str = "",
    keyword: str = "",
    start_date: str = "",
    end_date: str = "",
    page_size: int = 30,
) -> list[dict]:
    """Entry point for fd-runner (spiders/cninfo-operating).

    Args:
        limit: 返回行数硬上限（必真实生效，出口再截一次）。
        category: 只保留该披露类别（产销快报/销售简报/发电量/主要运营数据/经营简报）；
            空 = 全类别。
        keyword: 只跑这一个巨潮搜索词；空 = 跑全部 5 个白名单词。
        start_date / end_date: 巨潮 ``seDate`` 窗口 ``YYYY-MM-DD``；空则由
            ``window_days`` 推导为一个滚动窗口（缺省近 45 天）。
        page_size: 每个搜索词向巨潮请求的公告数（注意-load 影响东财列表命中率）。

    Returns:
        list of row dicts（schema = manifest columns）。
    """
    from fd_industry_data.cancel_event import is_set as cancel_set

    if cancel_set():
        logger.info("cancel requested before start; returning empty")
        return []

    today = datetime.now(timezone.utc)
    end = end_date or today.strftime("%Y-%m-%d")
    start = start_date or (today - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")

    keys = (keyword,) if keyword else SEARCHKEYS
    rows = _collect_rows(
        max(int(limit), 0), category or "", tuple(keys),
        start, end, max(int(page_size), 1),
    )
    return rows[: max(int(limit), 0)]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for r in run_cninfo_operating(limit=3):
        print(json.dumps(r, ensure_ascii=False)[:400])
