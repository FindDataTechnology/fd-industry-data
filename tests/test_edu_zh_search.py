"""zh→en 学科词表与 edu_search 多语言检索集成测（add-multilingual-zh-search）。

纯本地目录 fixture，不打真 OpenAlex。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from edu_catalog import tools as T
from edu_catalog import zh_lexicon

# ── 词表翻译层 ──

def test_translate_basic_subject():
    assert zh_lexicon.translate_query("微积分") == "calculus"
    assert zh_lexicon.translate_query("线性代数") == "linear algebra"
    assert zh_lexicon.translate_query("量子力学") == "quantum mechanics"


def test_translate_mixed_drops_uncovered():
    # 「入门」不在词表——丢弃不编造；已覆盖词照译
    assert zh_lexicon.translate_query("线性代数 入门") == "linear algebra"
    # 全未覆盖 → None（不翻译，按原样查）
    assert zh_lexicon.translate_query("鲁迅") is None


def test_translate_non_cjk_is_none():
    assert zh_lexicon.translate_query("calculus") is None
    assert zh_lexicon.translate_query("") is None


def test_has_cjk():
    assert zh_lexicon.has_cjk("微积分")
    assert not zh_lexicon.has_cjk("calculus")
    assert not zh_lexicon.has_cjk("")


# ── edu_search 集成 ──

class _MemCatalog:
    def __init__(self, rows):
        self._rows = rows

    def rows(self):
        return list(self._rows)


class _MemSubs:
    def add(self, sub):
        return sub

    def remove(self, sid):
        return False

    def list(self):
        return []


ROWS = [
    # slug 标题（连字符）——空格英文查询须靠归一命中
    {"resource_id": "edu.course.ocw.18-06", "title": "18-06-linear-algebra-spring-2010",
     "education_source": "ocw_sitemap", "license": "CC BY-NC-SA", "license_layer": "cc-by-nc",
     "commercial_ok": False, "body_state": "link_only", "source_url": "https://ocw.mit.edu/18-06",
     "source": "education-cc-courses", "scraped_at": "2026-11-05T00:00:00+00:00"},
    {"resource_id": "edu.course.ocw.18-01", "title": "18-01-single-variable-calculus-fall-2006",
     "education_source": "ocw_sitemap", "license": "CC BY-NC-SA", "license_layer": "cc-by-nc",
     "commercial_ok": False, "body_state": "link_only", "source_url": "https://ocw.mit.edu/18-01",
     "source": "education-cc-courses", "scraped_at": "2026-11-05T00:00:00+00:00"},
]


class _Recorder:
    def __init__(self):
        self.tools = {}

    def tool(self, fn):
        self.tools[fn.__name__] = fn


def _svc(live=None):
    mcp = _Recorder()
    T.register(mcp, catalog=lambda: _MemCatalog(ROWS), subs=None, live_search=live)
    return mcp.tools["edu_search"]


def test_zh_query_hits_english_material_with_translation_visible():
    search = _svc()
    out = search(query="微积分")
    assert out["status"] == "ok"
    assert any("calculus" in (it["title"] or "") for it in out["items"])
    qt = out["query_translated"]
    assert qt["from"] == "微积分" and qt["to"] == "calculus"
    assert qt["via"] == "zh-lexicon"  # add-multilang-mainstream 起对照带 via


def test_zh_query_no_coverage_is_honest_no_match():
    search = _svc()
    out = search(query="鲁迅")
    assert out["status"] == "no_match"
    assert out["catalog_size"] == 2
    assert "query_translated" not in out  # 未翻译就不许伪造对照


def test_zh_query_uncovered_word_translated_sibling_still_hits():
    search = _svc()
    # 「线性代数 导论」：导论丢弃，线性代数照译——对照必须在场
    out = search(query="线性代数 导论")
    qt = out["query_translated"]
    assert qt["from"] == "线性代数 导论" and qt["to"] == "linear algebra"
    assert out["status"] == "ok" and any("linear-algebra" in it["title"] for it in out["items"])


def test_spaced_english_matches_slug_title_via_normalization():
    search = _svc()
    out = search(query="linear algebra")  # 空格词 vs slug 标题
    assert out["status"] == "ok"
    assert any("linear-algebra" in it["title"] for it in out["items"])


def test_english_query_carries_no_translation_field():
    search = _svc()
    out = search(query="calculus")
    assert out["status"] == "ok"
    assert "query_translated" not in out


def test_live_channel_receives_translated_query():
    got = {}

    def _live(q, limit):
        got["q"] = q
        return []

    search = _svc(live=_live)
    search(query="微积分")
    assert got["q"] == "calculus"
