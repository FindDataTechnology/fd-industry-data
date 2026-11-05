"""多语言查询解析层（add-multilang-mainstream D1/D2/D3）。

中文词表（zh_lexicon）为快路径；其余主流语言按**文字系统**路由源语言，经
Wikipedia langlink 解析英文主题词（源语言搜索首条 → 英文对应条目标题）。
确定性、可审计、不编造：无对应英文条目/出网故障 → None（如实不翻译）。

韧性口径（D3）：
- 单次 4s 超时、失败即 None，不重试不阻塞检索（外部故障 MUST NOT 伪装目录故障）。
- 进程内缓存含负缓存，上限 2 万键，满则清空。
- 出网代理 env 链：EDU_MULTILANG_PROXY → EDU_OER_PROXY → 直连（pod 在墙内，Wikipedia 须代理）。
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from .zh_lexicon import has_cjk, translate_query

WIKI_TIMEOUT_S = 4.0
_UA = "qicai-edu-catalog/1.0 (finddata; multilang resolver)"
_CACHE: dict[str, str | None] = {}
_CACHE_CAP = 20_000

# 文字系统 → Wikipedia 语言版
_LANG_BY_SCRIPT = (
    ("kana", "ja"),
    ("hangul", "ko"),
    ("cyrillic", "ru"),
    ("arabic", "ar"),
    ("hebrew", "he"),
    ("thai", "th"),
    ("greek", "el"),
)
# 拉丁带变音符候选（按主流度排序，命中即停）
_LATIN_WIKIS = ("es", "fr", "de", "pt")


def _script_lang(q: str) -> str | None:
    """按文字系统判源语言；纯 ASCII → None（不翻译）。"""
    if not q or q.isascii():
        return None
    if any(0x3040 <= ord(c) <= 0x30FF for c in q):  # 假名（含片假名）
        return "ja"
    if any(0xAC00 <= ord(c) <= 0xD7AF for c in q):  # 谚文
        return "ko"
    if any(0x0400 <= ord(c) <= 0x04FF for c in q):  # 西里尔
        return "ru"
    if any(0x0600 <= ord(c) <= 0x06FF for c in q):  # 阿拉伯
        return "ar"
    if any(0x0590 <= ord(c) <= 0x05FF for c in q):  # 希伯来
        return "he"
    if any(0x0E00 <= ord(c) <= 0x0E7F for c in q):  # 泰文
        return "th"
    if any(0x0370 <= ord(c) <= 0x03FF for c in q):  # 希腊
        return "el"
    if has_cjk(q):
        return "zh"
    if any(ord(c) > 0x007F for c in q):
        return "latin"  # 非 ASCII 拉丁（é ñ ü ö…）
    return None


def _proxy_opener() -> urllib.request.OpenerDirector | None:
    proxy = os.environ.get("EDU_MULTILANG_PROXY") or os.environ.get("EDU_OER_PROXY")
    if not proxy:
        return None
    return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))


def _wiki_get(lang: str, params: dict) -> dict | None:
    qs = urllib.parse.urlencode(params)
    url = f"https://{lang}.wikipedia.org/w/api.php?{qs}"
    opener = _proxy_opener() or urllib.request.build_opener()
    req = urllib.request.Request(url, headers={"user-agent": _UA})
    try:
        with opener.open(req, timeout=WIKI_TIMEOUT_S) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None  # 超时/断网/被墙 → 不翻译，不阻塞


def _wiki_en_title(lang: str, q: str) -> str | None:
    """源语言维基搜索首条 → 英文对应条目标题；任一步无果/故障 → None。"""
    s = _wiki_get(lang, {"action": "query", "list": "search", "srsearch": q,
                         "srlimit": 1, "format": "json"})
    hits = (s or {}).get("query", {}).get("search") or []
    if not hits:
        return None
    title = hits[0].get("title")
    if not title:
        return None
    l = _wiki_get(lang, {"action": "query", "titles": title, "prop": "langlinks",
                         "lllang": "en", "redirects": 1, "format": "json"})
    pages = (l or {}).get("query", {}).get("pages") or {}
    for _pid, page in pages.items():
        for ll in page.get("langlinks") or []:
            if ll.get("lang") == "en":
                return (ll.get("*") or "").strip() or None
    return None


def _safe_en_title(lang: str, q: str) -> str | None:
    """韧性口径（D3）：解析层任何异常吞掉返回 None——外部故障不阻塞检索。"""
    try:
        return _wiki_en_title(lang, q)
    except Exception:
        return None


def resolve_query(query: str) -> tuple[str, str] | None:
    """解析英文主题词。返回 (英文词组, via) 或 None（不翻译/故障/无覆盖）。

    via ∈ zh-lexicon / wiki-<lang> / wiki-latin。结果含负缓存。
    """
    q = (query or "").strip()
    if not q or q.isascii():
        return None
    # 快路径：中文词表（零出网）
    if has_cjk(q) and not any(0x3040 <= ord(c) <= 0x30FF for c in q):
        t = translate_query(q)
        if t:
            return (t, "zh-lexicon")
    lang = _script_lang(q)
    if lang is None:
        return None
    key = f"{lang}:{q}"
    if key in _CACHE:
        en = _CACHE[key]
        return (en, f"wiki-{lang}") if en else None
    if lang == "latin":
        en = None
        for wl in _LATIN_WIKIS:
            en = _safe_en_title(wl, q)
            if en:
                lang = wl  # 记实际命中的语言
                break
    else:
        en = _safe_en_title(lang, q)
    if len(_CACHE) >= _CACHE_CAP:
        _CACHE.clear()
    _CACHE[key] = en
    if not en:
        return None
    # 英文标题词组小写化，与 hay 归一后子串匹配
    return (en.lower(), f"wiki-{lang}")
