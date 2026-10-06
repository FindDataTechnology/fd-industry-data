"""spiders/worldbank-procnotices —— 世界银行采购公告（Procurement Notices）取数。

数据表面（公开 REST API v2，免鉴权；侦察簿 W1-M，见工单 brief.notes）：

- ``GET https://search.worldbank.org/api/v2/procnotices`` → JSON
  ``{"rows":..,"os":..,"page":..,"total":..,"procnotices":[{...}]}``

实测确认的参数口径（2026-10-07 直连冒烟，只发这些参数）：

- ``rows``：每页条数（实测 50 可用）；``os``：偏移（分页游标）
- ``srt=noticedate&order=desc``：按公告日期倒序（默认即最新在前，显式固化）
- ``fl=<字段列表>``：字段白名单——**必须显式列**，缺省响应含巨型 HTML 字段
  ``notice_text``（单条数十 KB），白名单化后单页 <100 KB
- ``qterm=<项目号>``：按项目全文过滤（golden 重放走固定历史项目，结果恒定）

口坑（brief.notes 逐条落实）：

- ``fl=docna,pdate``（工单 source_urls 原样字段名）实测**不生效**——返回仅含
  ``project_id``；真实字段名是 ``noticedate`` / ``bid_description`` 等 camelCase，
  本单元只用实测过的字段名。
- 无金额字段（42 万条公告为「量」口径，金额须下游另行匹配），不虚造。
- ``noticedate`` 上游格式为 ``05-Oct-2026``；本单元原样透出 ``pdate`` 并派生 ISO
  ``date``，解析失败 ``date=None`` 不猜。
- ``limit`` 语义 = 返回行数上限；单页失败跳过留痕、继续下一页；**全部页失败抛
  ``RuntimeError``**（失败即红，不静默返回空列表）。

工单：``reports/health-tickets/20261006-worldbank-procnotices-50c670be.yaml``
（``kind=generate``）
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SOURCE = "worldbank-procnotices"
BASE = "https://search.worldbank.org/api/v2/procnotices"

# 字段白名单（实测有效 camelCase 名；刻意排除 notice_text 巨型 HTML 字段）
FL = ",".join([
    "id", "notice_type", "noticedate", "notice_status", "notice_lang_name",
    "project_id", "project_name", "project_ctry_name", "bid_reference_no",
    "bid_description", "procurement_method_name", "submission_deadline_date",
])
PAGE_SIZE = 50
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
TIMEOUT = 30  # 秒；单页失败跳过留痕，不重试、不加频次

_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _get_json(url: str):
    """GET 一个 JSON 端点（仅 https 数据表面；无鉴权、无重试放大）。"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _iso_date(raw):
    """``05-Oct-2026`` → ``2026-10-05``；缺失/异常 → ``None``（不猜日期）。"""
    if not raw or not isinstance(raw, str):
        return None
    parts = raw.strip().split("-")
    if len(parts) != 3:
        return None
    day, mon, year = parts
    month = _MONTHS.get(mon.capitalize())
    if month is None or not (day.isdigit() and year.isdigit()):
        return None
    return f"{year}-{month:02d}-{int(day):02d}"


def _text(value):
    """文本字段归一：空串 → ``None``。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _notice_row(notice: dict, page_url: str) -> dict:
    """一条公告 → 一行明细（notice_id / title / pdate 口径，brief 认可明细形态）。"""
    title = _text(notice.get("bid_description")) or _text(notice.get("project_name"))
    return {
        "notice_id": _text(notice.get("id")),
        "notice_type": _text(notice.get("notice_type")),
        "notice_status": _text(notice.get("notice_status")),
        "notice_lang": _text(notice.get("notice_lang_name")),
        "pdate": _text(notice.get("noticedate")),
        "date": _iso_date(notice.get("noticedate")),
        "project_id": _text(notice.get("project_id")),
        "project_name": _text(notice.get("project_name")),
        "country": _text(notice.get("project_ctry_name")),
        "bid_reference_no": _text(notice.get("bid_reference_no")),
        "title": title,
        "procurement_method": _text(notice.get("procurement_method_name")),
        "submission_deadline": _text(notice.get("submission_deadline_date")),
        "url": page_url,
        "source": SOURCE,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def _page_url(os_: int, qterm: str | None) -> str:
    params = [
        ("rows", str(PAGE_SIZE)),
        ("os", str(os_)),
        ("srt", "noticedate"),
        ("order", "desc"),
        ("fl", FL),
    ]
    if qterm:
        params.append(("qterm", qterm))
    return BASE + "?" + urllib.parse.urlencode(params)


def run_worldbank_procnotices(limit: int = 100, qterm: str | None = None) -> list[dict]:
    """取最新 ``limit`` 条采购公告明细；全部页失败 → 抛 ``RuntimeError``（失败即红）。

    ``qterm`` 可选：按项目号过滤（golden 重放用固定历史项目 P099833，结果恒定）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []
    if qterm is not None:
        qterm = str(qterm).strip() or None

    rows: list[dict] = []
    errors: list[str] = []
    os_ = 0
    while len(rows) < limit:
        url = _page_url(os_, qterm)
        try:
            payload = _get_json(url)
        except Exception as exc:  # noqa: BLE001 - 单页失败跳过留痕
            errors.append(f"os={os_}: {type(exc).__name__}: {exc}")
            os_ += PAGE_SIZE
            if os_ > limit * 4 + PAGE_SIZE * 4:  # 防御：连续失败不无限翻页
                break
            continue
        notices = payload.get("procnotices") if isinstance(payload, dict) else None
        if not isinstance(notices, list):
            errors.append(f"os={os_}: unexpected payload shape")
            break
        for notice in notices:
            try:
                rows.append(_notice_row(notice, url))
            except Exception as exc:  # noqa: BLE001 - 单条脏行跳过留痕
                errors.append(f"os={os_} row: {type(exc).__name__}: {exc}")
        if len(notices) < PAGE_SIZE:  # 末页（或 qterm 命中集取尽）
            break
        os_ += PAGE_SIZE

    if errors:
        # 单页/单条失败留痕到 stderr（stdout 保持样例行输出纯净）
        print(f"{SOURCE}: skipped {len(errors)} page/row failure(s): "
              + "; ".join(errors[:3]), file=sys.stderr)
    if not rows:
        raise RuntimeError(
            f"{SOURCE}: 全部取数失败 — " + "; ".join(errors)[:300]
        )
    return rows[:limit]


if __name__ == "__main__":  # 冒烟：python3 spiders/worldbank-procnotices/spider.py
    print(json.dumps(run_worldbank_procnotices(limit=3), ensure_ascii=False, indent=2))
