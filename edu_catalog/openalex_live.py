"""OpenAlex 实时查询 facade（spec cap2 新增 R「论文通道实时优先/本地回落」）。

2026-11-04 晚 Owner 架构纠偏：OpenAlex 本身即完整公开搜索 API，论文层不自建
全量影子索引——edu_search 论文通道改服务端实时直查，本地目录退为回落+种子对账。

口径（2026-11-04 cheap-1 实证）：
- 匿名 search 会 429（search 集群限流）→ key 必带：env ``OPENALEX_API_KEY``（不落码）；
- 子域过滤 ``topics.subfield.id:3304``（**数字无 S 前缀**——"S3304" 会静默 0 命中）；
- 直连可达（学术线先例）；超时/429/5xx → ChannelError，reason 对齐既有枚举
  （429/5xx=upstream_unreachable，detail 带状态码；401/403=unauthorized；socket=timeout）。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .tools import ChannelError

CHANNEL = "openalex-live"
API_URL = "https://api.openalex.org/works"
MAILTO = "aloadtree@gmail.com"
EDUCATION_SUBFIELD = 3304  # 数字无前缀（"S3304" 静默 0 命中——2026-11-04 实证）
TIMEOUT = 20


def _request(url: str) -> dict:
    """单次 GET（key 在场必带；异常归一为 ChannelError，reason 对齐枚举）。"""
    headers = {"User-Agent": f"fd-industry-data/1.0 (mailto:{MAILTO})", "Accept": "application/json"}
    key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if key:
        headers["api_key"] = key
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ChannelError(CHANNEL, "unauthorized", f"HTTP {e.code}") from e
        if e.code == 429:
            raise ChannelError(CHANNEL, "upstream_unreachable", "HTTP 429 rate-limited (search cluster)") from e
        raise ChannelError(CHANNEL, "upstream_unreachable", f"HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise ChannelError(CHANNEL, "timeout", f"url error: {e.reason}") from e
    except TimeoutError as e:
        raise ChannelError(CHANNEL, "timeout", str(e)) from e
    except (json.JSONDecodeError, OSError) as e:
        raise ChannelError(CHANNEL, "upstream_unreachable", str(e)) from e


def _map_work(work: dict) -> dict:
    """OpenAlex work → 目录统一 schema（cap2 R「结果携带许可层与新鲜度」）。"""
    oa = work.get("open_access") or {}
    pl = work.get("primary_location") or {}
    source_url = pl.get("landing_page_url") or work.get("doi") or work.get("id")
    license_field = oa.get("license") or pl.get("license")
    authors = [a.get("author", {}).get("display_name")
               for a in (work.get("authorships") or []) if (a.get("author") or {}).get("display_name")]
    return {
        "resource_id": work.get("id"),
        "title": work.get("display_name") or work.get("title"),
        "resource_type": "paper",
        "platform": "OpenAlex",
        "channel": CHANNEL,
        "license": license_field or "cc0",  # 元数据本体 CC0；逐篇 OA 许可见 license 字段
        "license_layer": "cc0_pd",
        "commercial_ok": True,
        "body_state": "stored_extraction" if (oa.get("is_oa") and oa.get("oa_url")) else "link_only",
        "as_of": work.get("updated_date"),
        "source_url": source_url,
        "doi": work.get("doi"),
        "publication_year": work.get("publication_year"),
        "open_access_oa_status": oa.get("oa_status"),
        "creators": authors,
    }


def search_openalex(query: str, limit: int = 10, subject: str = "") -> list[dict]:
    """实时检索论文：search 全文 + Education 子域过滤；返回目录 schema 行。

    limit 直传 per-page（OpenAlex 上限 200，此处帽 50——facade 是单次问答不是翻页作业）。
    """
    q = (query or "").strip()
    if not q:
        return []
    params = {
        "search": q,
        "filter": f"topics.subfield.id:{EDUCATION_SUBFIELD}",
        "per-page": str(min(max(int(limit), 1), 50)),
        "mailto": MAILTO,
        "select": "id,doi,title,display_name,publication_year,updated_date,authorships,"
                  "open_access,primary_location",
    }
    page = _request(f"{API_URL}?{urllib.parse.urlencode(params)}")
    works = page.get("results") or []
    rows = [_map_work(w) for w in works if (w.get("display_name") or w.get("title")) and w.get("id")]
    if subject:
        s = subject.lower()
        rows = [r for r in rows if s in json.dumps(r, ensure_ascii=False).lower()]
    return rows
