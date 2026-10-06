"""spiders/gb-standards —— 全国标准信息公共服务平台（国标 GB）检索取数。

数据表面（公开 JSON，免鉴权；**必须带浏览器 UA**，见工单 brief.notes「侦察簿 W1-F」）：

- 检索： ``GET https://std.samr.gov.cn/gb/search/gbQueryPage?searchText=<关键词>&pageNumber=<页>&pageSize=<页大小>``
  → ``{"total": N, "pageNumber": P, "rows": [...]}``，默认按发布日期新→旧。

行字段（工单 expectations：标准号/名称/发布日期/实施日期/状态）：

- ``C_STD_CODE`` → ``std_code``；``C_C_NAME`` → ``title``；``ISSUE_DATE`` → ``issue_date``；
  ``ACT_DATE`` → ``act_date``；``STATE`` → ``state``（现行/即将实施/废止…）；
  ``STD_NATURE`` → ``std_nature``（强制性/推荐性）；``id`` → ``record_id``。
- 月度「新发布/废止国标数量」序列由明细行 ``issue_date``/``state`` 派生聚合，
  本单元交付明细行。

口坑（brief.notes / 实测踩坑，逐一落实）：

- **必须带浏览器 UA**（实测无 UA 也能回包，但 notes 点名要求，按纪律照带；
  只带常规 UA，不做指纹伪装与频次对抗）。
- **高亮标签**：``searchText`` 非空时命中字段会包 ``<sacinfo>…</sacinfo>``
  高亮标签（实测），一律剥离后再透出。
- **只发实测有效参数**：``searchText``（空=全部）、``pageNumber``、``pageSize``
  （实测 ≤20 生效，页大小封顶 20）；未实测的分页参数一概不发。
- ``limit`` 语义 = 返回明细行数上限（不足时按 ``pageNumber`` 翻页补齐）；
  golden 用 ``limit=5 + search_text="GB/T 1.1-2020"`` 锚固定标准号，跨期稳定。
- 容错：单页失败只跳过该页并留痕；**全部页失败则抛 ``RuntimeError``**（失败即红，
  不静默返回空列表，避免「空洞通过」）。

工单：``reports/health-tickets/20261006-gb-standards-ca4f8b1f.yaml``（``kind=generate``）
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SOURCE = "gb-standards"
QUERY_URL = "https://std.samr.gov.cn/gb/search/gbQueryPage"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://std.samr.gov.cn/gb",
    "Accept": "application/json, text/plain, */*",
}
TIMEOUT = 20  # 秒；轻量 JSON，超时即跳过该页（不重试、不加频次）
PAGE_SIZE = 20  # 实测生效的页大小，封顶使用
REQUEST_GAP = 0.8  # 秒；政府站点，串行低频不并发
_HIGHLIGHT_RE = re.compile(r"</?sacinfo>")


def _get_json(url: str):
    """GET 一个 JSON 端点（仅固定 https 主机；无鉴权、无重试放大）。"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        return json.loads(resp.read().decode("utf-8", "replace"))


def _text(value) -> str | None:
    """文本归一：剥 ``<sacinfo>`` 高亮标签，空串 → ``None``。"""
    if value is None:
        return None
    text = _HIGHLIGHT_RE.sub("", str(value)).strip()
    return text or None


def fetch_page(search_text: str, page_number: int) -> list[dict]:
    """取一页检索结果，返回明细行列表。"""
    params = urllib.parse.urlencode({
        "searchText": search_text,
        "pageNumber": page_number,
        "pageSize": PAGE_SIZE,
    })
    url = f"{QUERY_URL}?{params}"
    payload = _get_json(url)
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise ValueError(f"page {page_number}: unexpected payload (no rows list)")
    rows: list[dict] = []
    for item in payload["rows"]:
        if not isinstance(item, dict):
            continue
        rows.append({
            "std_code": _text(item.get("C_STD_CODE")),
            "title": _text(item.get("C_C_NAME")),
            "std_nature": _text(item.get("STD_NATURE")),
            "issue_date": _text(item.get("ISSUE_DATE")),
            "act_date": _text(item.get("ACT_DATE")),
            "state": _text(item.get("STATE")),
            "record_id": _text(item.get("id")),
            "url": url,
            "source": SOURCE,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })
    return rows


def run_gb_standards(limit: int = 100, search_text: str = "") -> list[dict]:
    """取 ``limit`` 条国标明细行（默认全库按发布日期新→旧；``search_text`` 定向检索，
    如固定标准号 ``GB/T 1.1-2020``）；全部页失败 → 抛 ``RuntimeError``（失败即红）。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []

    rows: list[dict] = []
    errors: list[str] = []
    page = 1
    while len(rows) < limit:
        if page > 1:
            time.sleep(REQUEST_GAP)  # 串行低频，不并发
        try:
            page_rows = fetch_page(search_text, page)
        except Exception as exc:  # noqa: BLE001 - 单页失败只跳过并留痕
            errors.append(f"page {page}: {type(exc).__name__}: {exc}")
            page += 1
            if page > limit:  # 防御：避免空转翻页
                break
            continue
        if not page_rows:
            break  # 翻到底
        rows.extend(page_rows)
        page += 1
    if not rows:
        raise RuntimeError("gb-standards: 全部页取数失败 — " + "; ".join(errors)[:300])
    return rows[:limit]


if __name__ == "__main__":  # 冒烟：python3 spiders/gb-standards/spider.py
    print(json.dumps(run_gb_standards(limit=3), ensure_ascii=False, indent=2))
