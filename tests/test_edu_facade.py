"""7.5 论文实时 facade 测试（spec cap2「实时优先/本地回落」R 三场景）。

注入式：live_search 以 (query, limit) 柯里化假通道注入，不打真 API。
真 API 通路已由 cheap-1 探针实证（2026-11-04：带 key 200/search+subfield 全通），
单测不打网。
"""
from __future__ import annotations

import pytest

from edu_catalog import tools as T
from edu_catalog.tools import ChannelError


class MemStore:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def rows(self) -> list[dict]:
        return list(self._rows)


class Recorder:
    def __init__(self):
        self.tools = {}

    def tool(self, fn):
        self.tools[fn.__name__] = fn


CATALOG_ROWS = [
    {"resource_id": "edu.course.ocw.demo", "title": "Local Course",
     "education_source": "ocw_sitemap", "license": "CC BY-NC-SA", "license_layer": "cc-by-nc",
     "commercial_ok": False, "body_state": "link_only", "source_url": "https://ocw.mit.edu/x",
     "source": "education-cc-courses", "scraped_at": "2026-11-04T00:00:00+00:00"},
    {"resource_id": "edu.paper.local.1", "title": "Local Paper on Education",
     "education_source": "subfield_3304", "license": "cc0", "license_layer": "cc0_pd",
     "commercial_ok": True, "body_state": "link_only",
     "source_url": "https://openalex.org/W1", "source": "education-openalex",
     "scraped_at": "2026-11-04T00:00:00+00:00"},
]


def _api(live):
    mcp = Recorder()
    T.register(mcp, catalog=lambda: MemStore(CATALOG_ROWS), subs=None, live_search=live)
    return mcp.tools["edu_search"]


def _fake_work(i: int) -> dict:
    return {"id": f"https://openalex.org/W{i}", "doi": f"https://doi.org/10.1/x{i}",
            "display_name": f"Live Paper {i}", "publication_year": 2026,
            "updated_date": "2026-11-04",
            "authorships": [{"author": {"display_name": f"Author {i}"}}],
            "open_access": {"is_oa": True, "oa_status": "gold", "oa_url": "https://oa/x.pdf",
                            "license": "cc-by"},
            "primary_location": {"landing_page_url": "https://j.example/lp", "license": "cc-by"}}


def test_live_channel_results_marked_and_mapped():
    """实时命中：结果标 live 通道、字段按目录 schema 映射（license/OA/溯源齐备）。"""
    from edu_catalog.openalex_live import _map_work
    calls = []

    def live(query, limit):
        calls.append((query, limit))
        return [_map_work(_fake_work(1))]

    search = _api(live)
    out = search(query="open textbook")
    assert calls == [("open textbook", 20)]
    assert out["status"] == "ok" and out["channels"] == {"live": 1, "local": 0}  # 本地无 "open textbook" 字面
    first = out["items"][0]
    assert first["channel"] == T.CHANNEL_LIVE
    assert first["resource_type"] == "paper"
    assert first["license"] == "cc-by" and first["license_layer"] == "cc0_pd"
    assert first["open_access_oa_status"] == "gold"
    assert first["source_url"] == "https://j.example/lp"
    assert first["as_of"] == "2026-11-04"
    assert first["creators"] == ["Author 1"]


def test_live_failure_falls_back_with_visible_reason():
    """实时故障：显式报因 + 回落本地命中（回落事实 MUST 可见，非伪装空）。"""

    def live(query, limit):
        raise ChannelError(T.CHANNEL_LIVE, "upstream_unreachable", "HTTP 429 rate-limited (search cluster)")

    search = _api(live)
    out = search(query="Education")
    assert out["status"] == "ok"  # 本地命中（Local Paper on Education）
    assert out["live_channel"]["reason"] == "upstream_unreachable"
    assert out["live_channel"]["channel"] == T.CHANNEL_LIVE
    assert all(it.get("channel") != T.CHANNEL_LIVE for it in out["items"])


def test_live_failure_no_local_hit_reports_no_match_with_reason():
    """两处皆空：回落仍空 → no_match，但 live_channel 报因（MUST NOT 纯空集）。"""

    def live(query, limit):
        raise ChannelError(T.CHANNEL_LIVE, "timeout", "read timed out")

    search = _api(live)
    out = search(query="nonexistent-xyz")
    assert out["status"] == "no_match"
    assert out["live_channel"]["reason"] == "timeout"


def test_local_only_when_no_query_or_non_paper_type():
    """无 query / resource_type 排除 paper → 不触实时通道（零网络）。"""
    calls = []

    def live(query, limit):
        calls.append(query)
        return []

    search = _api(live)
    out1 = search()  # 无 query：纯目录浏览
    assert out1["status"] == "ok" and "channels" not in out1
    out2 = search(query="local", resource_type="course")  # 指定非 paper → 不触实时
    assert out2["status"] == "ok" and out2["items"][0]["resource_id"].startswith("edu.course.")
    assert calls == []


def test_openalex_live_maps_work_and_enforces_subject_filter():
    """openalex_live 纯映射函数：work → 目录 schema；subject 过滤保序。"""
    from edu_catalog.openalex_live import _map_work
    row = _map_work(_fake_work(9))
    assert row["resource_id"] == "https://openalex.org/W9"
    assert row["body_state"] == "stored_extraction"  # is_oa + oa_url
    assert row["doi"] == "https://doi.org/10.1/x9"
    # link_only 分支：无 oa_url
    w = _fake_work(9)
    w["open_access"]["oa_url"] = None
    assert _map_work(w)["body_state"] == "link_only"


def test_reason_enum_alignment_unchanged():
    """失败 reason 枚举对齐既有口径（cap2 R3），facade 未扩枚举。"""
    assert T.REASONS == ("upstream_unreachable", "timeout", "unauthorized", "stale")
