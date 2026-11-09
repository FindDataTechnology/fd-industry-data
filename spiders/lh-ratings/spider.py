"""联合资信 定期跟踪评级 —— 银行跟踪评级条目（lhratings.com 静态分页）。

端点（2026-11-08 从列表页实读）：

- ``GET https://www.lhratings.com/lists/111.html?p=<N>``
  定期跟踪评级栏目（导航「评级结果 → 定期跟踪评级」）的**静态 HTML 分页**。
  **分页参数是 ``p``，不是 ``page``**（页脚 ``end_page`` 与跳转脚本都写
  ``?p=``）。未过滤时 ``end_page=2927``、**12 条/页**。
- ``GET .../lists/111.html?title=银行&p=<N>``
  ``title`` 是**服务端生效**的标题过滤：``end_page`` 由 2927 降到 **7**
  （2026-11-08 实测），说明过滤在服务端完成、不是前端隐藏。
  代价：它是**标题子串匹配**，会同时命中「银行**间**票据」等非银行发行人
  （实测 1 条：烟台市财金发展投资集团有限公司 2025 年公开发行银行间票据
  跟踪评级报告），故本单元**不**做本地二次收窄——收窄会掩盖上游过滤口径。
- 越界页（``p`` > 末页）返回 **HTTP 200 且 0 条**，不是 404；因此分页循环
  遇到空页即停，不靠页数硬编码。

列表项结构（78/78 条完全一致，2026-11-08 全量 7 页实测）：

```html
<li>
  <a href="https://www.lhratings.com/reports/<id>.pdf" target="_blank">
    <div class="dqgzbox">
      <div class="dqleft">
        <h2>债项/债券名称</h2>
        <h2>跟踪评级报告标题（含发行人）</h2>
        <p>YYYY-MM-DD</p>
      </div>
      <div class="dqright">
        <p>主体级别：AAA</p>
        <p>展望：稳定</p>
        <p>债项级别：AAA</p>
      </div>
    </div>
  </a>
</li>
```

不是表格（div/li 布局），按字符串 + 正则解析即可，不涉及 ``tabular``。

增量口径：栏目共 2927 页（约 3.5 万条历史），本单元**只巡检前 ``max_pages``
页**（默认 5 页 = 最近 60 条列表项）。全量回扫既不必要（跟踪评级每年一批、
前几页已覆盖数月到数年的新增）也不礼貌（2927 次请求），历史回补应改
``max_pages`` 显式发起，而不是默认全扫。

去重：上游列表存在**完全相同的 PDF URL 被列两次**的情况（7 页 78 条中 8 个
重复 URL，去重后 70 条）。本单元按 URL 去重并留痕（同一份披露只落一行），
不按（发行人名, 年份）去重——同一发行人同一年份的不同债券确实是不同 PDF。
"""
from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://www.lhratings.com"
LIST_PATH = "/lists/111.html"
#: 实测分页参数名是 ``p``（页脚 end_page + 跳转脚本一致）。
PAGE_PARAM = "p"
#: 实测服务端标题过滤参数名是 ``title``。
KEYWORD_PARAM = "title"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": BASE + LIST_PATH,
}

#: 网络礼节：请求间隔 ≥1s。
SLEEP_SECONDS = 1.2

#: 列表项：<li><a href="…"><div class="dqgzbox">…</div></a></li>。
#: 上游 ``target="_blank""`` 有双引号笔误，故 href 之后一律 ``[^>]*`` 吃掉。
_ITEM_RE = re.compile(
    r'<li>\s*<a\s+href="([^"]+)"[^>]*>\s*<div class="dqgzbox">(.*?)</div>\s*</a>\s*</li>',
    re.S,
)
_H2_RE = re.compile(r"<h2>(.*?)</h2>", re.S)
_P_RE = re.compile(r"<p>(.*?)</p>", re.S)
_END_PAGE_RE = re.compile(r'end_page="(\d+)"')
#: 公司名后缀是发行人抽取的锚点（中文公司名以这两个后缀收尾）。
_COMPANY_RE = re.compile(r"(.+?(?:股份有限公司|有限责任公司|有限公司))")
_REPORT_YEAR_RE = re.compile(r"(\d{4})年")
#: 公告类标题以评级机构开头，发行人要从「关于」之后取。
_AGENCY_PREFIXES = ("联合资信评估股份有限公司", "联合资信评估有限公司", "联合资信")
#: 「关于」之后可能仍带动作动词（上调/将…列入等），抽取前剥掉。
_LEADING_VERBS = ("将", "对", "上调", "下调", "维持", "终止", "调整", "列入")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _get(url: str, *, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")


def _list_url(keyword: str, page: int) -> str:
    params: dict[str, str] = {}
    if keyword.strip():
        params[KEYWORD_PARAM] = keyword.strip()
    params[PAGE_PARAM] = str(page)
    return f"{BASE}{LIST_PATH}?{urllib.parse.urlencode(params)}"


def _issuer_of(report_title: str, bond_name: str, rating_date: str) -> str:
    """从报告标题取发行人；公告类跳过开头的评级机构与动作动词。"""
    title = report_title or bond_name
    for prefix in _AGENCY_PREFIXES:
        if title.startswith(prefix):
            tail = title[len(prefix):]
            if tail.startswith("关于"):
                tail = tail[2:]
            tail = tail.lstrip("：: ")
            changed = True
            while changed:
                changed = False
                for verb in _LEADING_VERBS:
                    if tail.startswith(verb):
                        tail = tail[len(verb):].lstrip("：: ")
                        changed = True
            title = tail
            break
    matched = _COMPANY_RE.search(title)
    if matched:
        return matched.group(1)
    matched = _COMPANY_RE.search(bond_name or "")
    if matched:
        return matched.group(1)
    return ""


def _after(text: str, label: str) -> str:
    for sep in ("：", ":"):
        marker = label + sep
        if marker in text:
            return text.split(marker, 1)[1].strip()
    return ""


def _report_year(report_title: str, rating_date: str) -> int | None:
    matched = _REPORT_YEAR_RE.search(report_title or "")
    if matched:
        return int(matched.group(1))
    if len(rating_date) >= 4 and rating_date[:4].isdigit():
        return int(rating_date[:4])
    return None


def _report_kind(report_title: str) -> str:
    if "公告" in (report_title or ""):
        return "公告"
    if "跟踪" in (report_title or "") and "评级" in (report_title or ""):
        return "跟踪评级报告"
    return "其他"


def parse_list_page(html: str) -> list[dict]:
    """解析一页列表 HTML → 条目列表（不含 source/scraped_at 等信封字段）。"""
    items: list[dict] = []
    for href, body in _ITEM_RE.findall(html):
        heads = [_clean(x) for x in _H2_RE.findall(body)]
        paras = [_clean(x) for x in _P_RE.findall(body)]
        if len(heads) < 2:
            # 版式漂移：留痕跳过，不猜字段
            print(f"[lh-ratings] 条目版式异常（h2={len(heads)}）已跳过：{href}", flush=True)
            continue
        bond_name, report_title = heads[0], heads[1]
        rating_date = paras[0] if paras else ""
        entity_rating = _after(paras[1], "主体级别") if len(paras) > 1 else ""
        outlook = _after(paras[2], "展望") if len(paras) > 2 else ""
        debt_rating = _after(paras[3], "债项级别") if len(paras) > 3 else ""
        url = href if href.startswith("http") else BASE + href
        items.append(
            {
                "issuer": _issuer_of(report_title, bond_name, rating_date),
                "bond_name": bond_name,
                "report_title": report_title,
                "report_year": _report_year(report_title, rating_date),
                "report_kind": _report_kind(report_title),
                "rating_date": rating_date,
                "entity_rating": entity_rating,
                "outlook": outlook,
                "debt_rating": debt_rating,
                "url": url,
            }
        )
    return items


def run_lh_ratings(
    limit: int = 200,
    keyword: str = "银行",
    max_pages: int = 5,
) -> list[dict]:
    """联合资信定期跟踪评级栏目 → 每份跟踪评级报告一行。

    Args:
        limit: 返回行数上限（列表日期倒序，先去重后截断）。
        keyword: 服务端标题过滤词（``?title=``）；空串 = 不过滤（全量 2927 页，
            默认值 ``银行`` 实测过滤后 7 页、78 条）。
        max_pages: 最多巡检前几页（默认 5 = 最近 60 条列表项）；只做增量巡检，
            不回扫全量，历史回补请显式调大。

    Raises:
        RuntimeError: 一页都没抓到 / 抓到的页面里没有任何条目（视为版式漂移或
            过滤词失效，而不是静默返回空）。
    """
    if limit <= 0 or max_pages <= 0:
        return []
    stamp = _now()
    keyword = "" if keyword is None else str(keyword)
    rows: list[dict] = []
    seen: set[str] = set()
    duplicated = 0
    fetched_pages = 0
    total_pages: int | None = None
    for page in range(1, int(max_pages) + 1):
        url = _list_url(keyword, page)
        try:
            html = _get(url)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"[lh-ratings] 第 {page} 页请求失败：{exc}", flush=True)
            continue
        fetched_pages += 1
        if total_pages is None:
            matched = _END_PAGE_RE.search(html)
            total_pages = int(matched.group(1)) if matched else None
        page_items = parse_list_page(html)
        if not page_items:
            # 越界页 HTTP 200 且 0 条（实测 p=8/9/9999）；正常巡检到此为止。
            print(f"[lh-ratings] 第 {page} 页 0 条，停止分页（末页={total_pages}）", flush=True)
            break
        for item in page_items:
            if item["url"] in seen:
                duplicated += 1
                continue
            seen.add(item["url"])
            item.update(
                {
                    "doc_type": "定期跟踪评级",
                    "keyword": keyword,
                    "source": "lh-ratings",
                    "scraped_at": stamp,
                }
            )
            rows.append(item)
        print(
            f"[lh-ratings] 第 {page} 页 {len(page_items)} 条（累计去重后 {len(rows)}，"
            f"末页={total_pages}）",
            flush=True,
        )
        if page < int(max_pages):
            time.sleep(SLEEP_SECONDS)
    if fetched_pages == 0 or not rows:
        raise RuntimeError(
            f"lh-ratings: 前 {max_pages} 页无可解析条目（keyword={keyword!r}）"
        )
    if duplicated:
        print(f"[lh-ratings] 上游重复条目（同 URL）已去重 {duplicated} 条", flush=True)
    return rows[: int(limit)]
