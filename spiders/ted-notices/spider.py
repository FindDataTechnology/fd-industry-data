"""spiders/ted-notices —— TED（Tenders Electronic Daily，欧盟公报）公告取数。

数据表面（公开 REST，免 key；**匿名配额极紧**，见工单 brief.notes「侦察簿 W1-M」）：

- 检索： ``POST https://api.ted.europa.eu/v3/notices/search``，JSON body（只发实测有效键）::

      {"query": "publication-date = YYYYMMDD",
       "fields": [公告字段白名单, 见 FIELDS],
       "limit": N, "scope": "ALL"}

  → ``{"notices": [...], "totalNoticeCount": 当日公告总量, ...}``。
  单次运行只发 **1 个请求**（单日、单页），串行不并发。

口坑（brief.notes / 实测踩坑，逐一落实）：

- **查询语法**：专家检索日期字段 ``publication-date``，字面量必须是 ``YYYYMMDD``
  （上游 pattern 实测：``20[0-9]{2}(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])``）；
  ``=`` 实测有效。``fields`` 省略或为空直接 400（"must not be empty"）。
- **scope**：枚举实测 ``ALL/ACTIVE/LATEST``；历史日查 ``ACTIVE`` 为空（公告已归档），
  必须 ``scope=ALL``。
- **字段白名单**：只发实测被接受的字段名（``publication-number`` / ``publication-date``
  / ``notice-type`` / ``notice-title`` / ``organisation-name-buyer`` / ``tendering-party-name``
  / ``winner-touchpoint-name`` / ``tender-value`` / ``tender-value-highest`` /
  ``BT-1118-NoticeResult-Currency`` / ``classification-cpv``）。实测多数历史（非 eForms）
  公告的 buyer/value 投影为空——行字段如实置 ``None``，绝不编造；``cpv``/``title``
  实测稳定回填。
- **匿名配额**：实测间歇 429。单次运行 1 请求；429 时**退避重试**（30s → 60s，最多
  3 次尝试），不加密频率、不并发——退避而非对抗。
- ``limit`` 语义 = 返回公告行数（即请求页大小，封顶 100）；``date`` 参数指定公告日
  （默认 UTC 昨天，已完结日）。
- 容错：**该日取数彻底失败则抛 ``RuntimeError``**（失败即红）；上游合法返回 0 条
  （极冷门日期）则返回空列表——这是真实空，不是失败。

工单：``reports/health-tickets/20261006-ted-notices-0438b7ca.yaml``（``kind=generate``）
"""
from __future__ import annotations

import ast
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

SOURCE = "ted-notices"
SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"

# 实测被 API 接受的字段名（未在白名单内的字段名会导致整个请求 400）
FIELDS = [
    "publication-number",
    "publication-date",
    "notice-type",
    "notice-title",
    "organisation-name-buyer",
    "tendering-party-name",
    "winner-touchpoint-name",
    "tender-value",
    "tender-value-highest",
    "BT-1118-NoticeResult-Currency",
    "classification-cpv",
]

HEADERS = {
    "User-Agent": "fd-industry-data/1.0",
    "Content-Type": "application/json",
    "Accept": "application/json",
}
TIMEOUT = 45  # 秒；匿名通道慢，超时即本轮失败
BACKOFFS = (30, 60)  # 秒；429 退避序列（共最多 1 + len(BACKOFFS) 次尝试）
_MAX_LIMIT = 100  # 请求页大小封顶（上游上限 250，主动收紧）
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _post_json(body: dict):
    """POST 一个 JSON 请求（仅固定 https 主机；429 退避重试，不加密频率）。"""
    data = json.dumps(body).encode("utf-8")
    last_err: Exception | None = None
    for attempt in range(1 + len(BACKOFFS)):
        if attempt:
            time.sleep(BACKOFFS[attempt - 1])  # 退避，绝不加速
        req = urllib.request.Request(SEARCH_URL, data=data, method="POST", headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                last_err = exc  # 匿名配额：退避后重试
                continue
            raise
    raise last_err if last_err else RuntimeError("unreachable")


def _norm_date(date: str | None) -> tuple[str, str]:
    """日期归一：'YYYY-MM-DD'（或 'YYYYMMDD'）→ ('YYYY-MM-DD', 'YYYYMMDD')。"""
    if date is None:
        day = datetime.now(timezone.utc).date() - timedelta(days=1)  # 默认 UTC 昨天
        iso = day.isoformat()
        return iso, iso.replace("-", "")
    text = str(date).strip()
    if _DATE_RE.match(text):
        return text, text.replace("-", "")
    if re.match(r"^\d{8}$", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}", text
    raise ValueError(f"date must be YYYY-MM-DD or YYYYMMDD, got {date!r}")


def _pick_lang(value, depth: int = 0) -> str | None:
    """多语言/嵌套字段归一：list 取首元素，dict 取 eng 优先、`name` 次之、再首真值；
    上游偶发把列表序列化成字符串（``"['Name']"`` 形态，实测），解析还原后取首元素。
    超深嵌套放弃（绝不编造）。"""
    if value is None or depth > 4:
        return None
    if isinstance(value, list):
        return _pick_lang(value[0], depth + 1) if value else None
    if isinstance(value, dict):
        for key in ("eng", "en", "name"):
            if value.get(key):
                got = _pick_lang(value[key], depth + 1)
                if got:
                    return got
        for v in value.values():
            if v:
                got = _pick_lang(v, depth + 1)
                if got:
                    return got
        return None
    text = str(value).strip()
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return text or None
        if isinstance(parsed, (list, tuple)) and parsed:
            return _pick_lang(parsed[0], depth + 1)
    return text or None


def _first_text(*values) -> str | None:
    """多候选字段取首个非空文本（buyer 等投影为空的字段如实落 None）。"""
    for v in values:
        text = _pick_lang(v)
        if text:
            return text
    return None


def _first_num(*values):
    for v in values:
        if isinstance(v, bool) or v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def fetch_date(day_iso: str, day_compact: str, limit: int) -> list[dict]:
    """取单日公告（单请求），返回明细行列表；带当日公告总量。"""
    body = {
        "query": f"publication-date = {day_compact}",
        "fields": FIELDS,
        "limit": max(1, min(int(limit), _MAX_LIMIT)),
        "scope": "ALL",
    }
    payload = _post_json(body)
    if not isinstance(payload, dict) or not isinstance(payload.get("notices"), list):
        raise ValueError(f"date {day_iso}: unexpected payload (no notices list)")
    total = payload.get("totalNoticeCount")

    rows: list[dict] = []
    for item in payload["notices"]:
        if not isinstance(item, dict):
            continue
        cpv = item.get("classification-cpv")
        cpv_codes: list[str] = []
        if isinstance(cpv, list):
            for c in cpv:
                code = _pick_lang(c)
                if code and code not in cpv_codes:
                    cpv_codes.append(code)  # 去重保序（上游实测有重复码）
        rows.append({
            "date": str(item.get("publication-date") or day_iso)[:10],
            "notice_id": _pick_lang(item.get("publication-number")),
            "notice_type": _pick_lang(item.get("notice-type")),
            "buyer": _first_text(
                item.get("organisation-name-buyer"),
                item.get("tendering-party-name"),
                item.get("winner-touchpoint-name"),
            ),
            "cpv": ";".join(cpv_codes) if cpv_codes else None,
            "value": _first_num(item.get("tender-value"), item.get("tender-value-highest")),
            "value_currency": _pick_lang(item.get("BT-1118-NoticeResult-Currency")),
            "title": _pick_lang(item.get("notice-title")),
            "total_notices": total if isinstance(total, int) else None,
            "url": SEARCH_URL,
            "source": SOURCE,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })
    return rows


def run_ted_notices(limit: int = 20, date: str | None = None) -> list[dict]:
    """取 ``date``（默认 UTC 昨天）当日的 TED 公告明细（最多 ``limit`` 行，含当日
    公告总量）；该日取数彻底失败（含配额退避耗尽）→ 抛 ``RuntimeError``（失败即红）。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []
    day_iso, day_compact = _norm_date(date)
    rows = fetch_date(day_iso, day_compact, limit)
    return rows[:limit]


if __name__ == "__main__":  # 冒烟：python3 spiders/ted-notices/spider.py
    print(json.dumps(run_ted_notices(limit=3), ensure_ascii=False, indent=2))
