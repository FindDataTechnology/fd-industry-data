"""大公国际 银行跟踪评级（定期跟踪公告列表，openApi，无鉴权）。

端点（2026-11-08 从栏目页内联 Vue 源码实读 + 实测确认）：

``POST https://www.dagongcredit.com/api/construction/openApi/getContentInfoPageList``

JSON body::

    {"pageNum": 1, "size": 10, "channelCode": "A009003002",
     "startTime": "", "endTime": "", "topic": "商业银行", "content": ""}

- ``channelCode=A009003002`` = 评级公告「定期跟踪」栏目；``topic`` 是**报告名称**
  关键字（栏目页把它绑在「报告名称」输入框，不是行业/topic 字段）。
- 响应 ``{"code": 0, "data": {"records": [...], "total": 94, "size": 10,
  "current": 1, "pages": 10}}``；分页从 **1** 开始，``size=10`` 实测可用。
- 记录字段实测语义：``a1`` PDF 直链（**相对路径**，需补
  ``https://www.dagongcredit.com``）、``a8`` 主体信用等级、``a9`` 债项等级
  （可空，也可为 ``"AA+,AA"`` 这类多级别串）、``a12`` 评级展望（可空）、
  ``pubTime`` 评级时间、``topic`` 报告名称、``filePath`` 公告详情页、``infoId`` 主键。
- **2018 年及以前的历史条目 ``a8``/``a9``/``a12`` 全为空**——空值原样留空，
  不从标题或正文猜等级。

实读出来的三个坑：

- body 用 ``pageSize``（而非 ``size``）或缺少 ``channelCode``/``content`` → HTTP 500；
- 表单编码（``application/x-www-form-urlencoded``）→ HTTP 415；
- 列表按 ``pubTime`` 倒序，新公告会把旧条目往后推——所以入口提供 ``page``
  起始页参数，供 golden 钉页复算（见 README「为什么 golden 钉 page」）。

本单元只取列表页字段，不下载 PDF 正文（PDF 是评级报告原件，结构化等级已由
``a8``/``a9``/``a12`` 给出）。
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

BASE = "https://www.dagongcredit.com"
LIST_URL = BASE + "/api/construction/openApi/getContentInfoPageList"
REFERER = BASE + "/dggj/ratingNotice/regularTracking/A009003002index_1.htm"
HEADERS = {
    "Content-Type": "application/json;charset=UTF-8",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": REFERER,
    "Origin": BASE,
}

#: 评级公告「定期跟踪」栏目码（栏目页 A009003002index_1.htm 的 channelcode）。
CHANNEL_CODE = "A009003002"
#: 实测 size=10 可用；固定 10 使 page 与总页数的对应关系稳定、可复算。
PAGE_SIZE = 10
#: 网络礼节：相邻请求间隔 ≥1s。
SLEEP = 1.0

_LEAD_RE = re.compile(r"^[\s\-_—·、,，.]+")
_YEAR_RE = re.compile(r"^\d{4}(?=[^\d])")
_CORP_RE = re.compile(r"^(.+?(?:股份有限公司|有限责任公司|有限公司))")
_INST_RE = re.compile(r"^(.+?(?:农村商业银行|商业银行|村镇银行|银行|信用社|信用合作联社))")
_FALLBACK_RE = re.compile(r"^(.+?)\d{4}年")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _abs(path: str) -> str:
    """上游相对路径补成绝对 URL；空值返回空串（不伪造链接）。"""
    path = _text(path)
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path
    return BASE + (path if path.startswith("/") else "/" + path)


def _issuer(title: str) -> str:
    """从报告名截出发行人：截至「股份有限公司/有限责任公司/有限公司」。

    例：``浙江平阳农村商业银行股份有限公司2026年度跟踪评级报告``
    → ``浙江平阳农村商业银行股份有限公司``。
    极旧条目带前导年份/标点（``2014浙江民泰…``、``-广州农村…``）先清理；
    无公司后缀的机构（银行/信用社/合作联社）走第二组模式，仍匹配不到时
    退化为「年份之前」的整段。
    """
    name = _YEAR_RE.sub("", _LEAD_RE.sub("", _text(title)))
    for pattern in (_CORP_RE, _INST_RE):
        matched = pattern.match(name)
        if matched:
            return matched.group(1)
    matched = _FALLBACK_RE.match(name)
    return matched.group(1) if matched else name


def _post(page: int, size: int, topic: str, timeout: int = 60) -> dict:
    body = json.dumps(
        {
            "pageNum": int(page),
            "size": int(size),
            "channelCode": CHANNEL_CODE,
            "startTime": "",
            "endTime": "",
            "topic": topic,
            "content": "",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(LIST_URL, data=body, headers=HEADERS, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    payload = json.loads(raw.decode("utf-8", "replace"))
    if payload.get("code") not in (0, "0"):
        raise ValueError(f"openApi code={payload.get('code')!r} msg={payload.get('msg')!r}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("openApi data 不是对象")
    return data


def _row(record: dict, stamp: str) -> dict | None:
    title = _text(record.get("topic"))
    if not title:
        return None
    published = _text(record.get("pubTime"))
    return {
        "issuer": _issuer(title),
        "report_title": title,
        "subject_rating": _text(record.get("a8")),
        "debt_rating": _text(record.get("a9")),
        "outlook": _text(record.get("a12")),
        "rating_date": published.split(" ")[0] if published else "",
        "pdf_url": _abs(record.get("a1")),
        "detail_url": _abs(record.get("filePath")),
        "report_type": "regular_tracking",
        "info_id": _text(record.get("infoId")),
        "source": "dagong-ratings",
        "scraped_at": stamp,
    }


def run_dagong_ratings(
    limit: int = 200,
    topic: str = "商业银行",
    pages: int = 3,
    page: int = 1,
) -> list[dict]:
    """大公国际 银行（定期跟踪）评级公告，每份报告一行。

    Args:
        limit: 返回行数上限（按上游 ``pubTime`` 倒序累积后截断）。
        topic: 报告名称关键字；``商业银行`` 是银行跟踪评级的实测有效口径。
        pages: 从起始页往后取多少页（每页 10 条）；``3`` = 最新 30 条。
        page: 起始页（1 起）。用于 golden 钉页复算；默认 1 = 最新一页。

    Raises:
        RuntimeError: 所有请求页均失败或为空，一行都拿不到。
    """
    limit = int(limit)
    pages = int(pages)
    if limit <= 0 or pages <= 0:
        return []
    start_page = max(1, int(page))
    stamp = _now()
    rows: list[dict] = []
    for offset in range(pages):
        current = start_page + offset
        try:
            data = _post(current, PAGE_SIZE, topic)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            print(f"[dagong-ratings] 第 {current} 页失败：{exc}", flush=True)
            continue
        records = data.get("records") or []
        if not records:
            print(f"[dagong-ratings] 第 {current} 页为空（topic={topic!r}）", flush=True)
            break
        for record in records:
            if not isinstance(record, dict):
                continue
            row = _row(record, stamp)
            if row is None:
                continue
            rows.append(row)
            if len(rows) >= limit:
                return rows[:limit]
        total_pages = int(data.get("pages") or 0)
        print(
            f"[dagong-ratings] 第 {current} 页 {len(records)} 条"
            f"（total={data.get('total')} pages={total_pages}）",
            flush=True,
        )
        if total_pages and current >= total_pages:
            break
        time.sleep(SLEEP)
    if not rows:
        raise RuntimeError(
            f"dagong-ratings: topic={topic!r} page={start_page} pages={pages} 无任何数据"
        )
    return rows[:limit]
