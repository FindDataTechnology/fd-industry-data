"""多语言解析层与 edu_search 接线测（add-multilang-mainstream）。

HTTP 全部离线 stub——不打真 Wikipedia；路由/缓存/降级/集成全覆盖。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from edu_catalog import multilang, tools as T
from edu_catalog.multilang import _script_lang, resolve_query


# ── 路由 ──

def test_script_routing():
    assert _script_lang("データ") == "ja"        # 假名 → ja
    assert _script_lang("微積分") == "zh"        # 纯汉字（无假名）→ zh 词表/zh wiki
    assert _script_lang("미적분") == "ko"
    assert _script_lang("математический анализ") == "ru"
    assert _script_lang("حساب التفاضل") == "ar"
    assert _script_lang("cálculo diferencial") == "latin"
    assert _script_lang("calculus") is None      # 纯 ASCII 不翻译
    assert _script_lang("") is None


def _fake_wiki(monkeypatch, table):
    """table: {(lang, q): en_title | None}——未列入的 (lang,q) 返回 None。"""
    def fake(lang, q):
        return table.get((lang, q))
    monkeypatch.setattr(multilang, "_wiki_en_title", fake)
    multilang._CACHE.clear()


def test_resolve_ja_via_wikipedia(monkeypatch):
    _fake_wiki(monkeypatch, {("ja", "データサイエンス"): "Data science"})
    assert resolve_query("データサイエンス") == ("data science", "wiki-ja")


def test_resolve_pure_kanji_falls_to_zh_wiki(monkeypatch):
    # 「微積分」纯汉字（无假名）→ zh 词表未中 → zh.wikipedia langlink
    _fake_wiki(monkeypatch, {("zh", "微積分"): "Calculus"})
    assert resolve_query("微積分") == ("calculus", "wiki-zh")


def test_resolve_ko_via_wikipedia(monkeypatch):
    _fake_wiki(monkeypatch, {("ko", "미적분"): "Calculus"})
    assert resolve_query("미적분") == ("calculus", "wiki-ko")


def test_resolve_latin_tries_sequential(monkeypatch):
    _fake_wiki(monkeypatch, {("fr", "calcul différentiel"): "Differential calculus"})
    assert resolve_query("calcul différentiel") == ("differential calculus", "wiki-fr")


def test_resolve_uses_zh_lexicon_first(monkeypatch):
    called = []
    monkeypatch.setattr(multilang, "_wiki_en_title", lambda lang, q: called.append((lang, q)) or None)
    multilang._CACHE.clear()
    assert resolve_query("微积分") == ("calculus", "zh-lexicon")
    assert called == []  # 词表命中零出网


def test_negative_cache_prevents_repeat_lookup(monkeypatch):
    calls = []

    def fake(lang, q):
        calls.append((lang, q))
        return None
    monkeypatch.setattr(multilang, "_wiki_en_title", fake)
    multilang._CACHE.clear()
    assert resolve_query("ウィジェット") is None   # 假名 → ja，stub 无覆盖
    assert resolve_query("ウィジェット") is None
    assert len(calls) == 1                        # 负缓存：第二次不再出网


def test_wiki_failure_returns_none(monkeypatch):
    def boom(lang, q):
        raise RuntimeError("proxy down")
    monkeypatch.setattr(multilang, "_wiki_en_title", boom)
    multilang._CACHE.clear()
    assert resolve_query("미적분") is None


def test_wiki_get_http_failure_is_none(monkeypatch):
    # 真 _wiki_get 路径：网络层异常吞掉返回 None（不阻塞检索）
    class _Boom:
        def open(self, *a, **kw):
            raise OSError("unreachable")
    monkeypatch.setattr(multilang, "_proxy_opener", lambda: _Boom())
    assert multilang._wiki_get("ja", {"action": "query"}) is None


# ── edu_search 集成 ──

class _MemCatalog:
    def __init__(self, rows):
        self._rows = rows

    def rows(self):
        return list(self._rows)


class _Recorder:
    def __init__(self):
        self.tools = {}

    def tool(self, fn):
        self.tools[fn.__name__] = fn


ROWS = [
    {"resource_id": "edu.course.ocw.18-01", "title": "18-01-single-variable-calculus-fall-2006",
     "education_source": "ocw_sitemap", "license": "CC BY-NC-SA", "license_layer": "cc-by-nc",
     "commercial_ok": False, "body_state": "link_only", "source_url": "https://ocw.mit.edu/18-01",
     "source": "education-cc-courses", "scraped_at": "2026-11-05T00:00:00+00:00"},
]


def _search(monkeypatch, resolve):
    monkeypatch.setattr(multilang, "resolve_query", resolve)
    mcp = _Recorder()
    T.register(mcp, catalog=lambda: _MemCatalog(ROWS), subs=None, live_search=None)
    return mcp.tools["edu_search"]


def test_edu_search_ja_query_hits_with_via(monkeypatch):
    search = _search(monkeypatch, lambda q: ("calculus", "wiki-ja") if q == "微積分" else None)
    out = search(query="微積分")
    assert out["status"] == "ok"
    assert any("calculus" in it["title"] for it in out["items"])
    assert out["query_translated"]["via"] == "wiki-ja"
    assert out["query_translated"]["to"] == "calculus"


def test_edu_search_resolver_failure_degrades_gracefully(monkeypatch):
    search = _search(monkeypatch, lambda q: None)
    out = search(query="미적분")
    assert out["status"] == "no_match"        # 解析失败 → 原查询如实 no_match
    assert "query_translated" not in out
    assert out["catalog_size"] == 1           # 目录照常可检索，非内部错误


def test_edu_search_zh_still_translated(monkeypatch):
    search = _search(monkeypatch, lambda q: ("calculus", "zh-lexicon") if q == "微积分" else None)
    out = search(query="微积分")
    assert out["status"] == "ok"
    assert out["query_translated"]["via"] == "zh-lexicon"
