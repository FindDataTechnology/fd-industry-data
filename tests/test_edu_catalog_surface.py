"""Wave 6 查询面测试（spec cap2 R1 闭集+写面边界 / R2 许可上脸+卡片边界 / R3 空果≠故障）。

工具直接用 register 返回的裸函数面测试（FastMCP 装配另在冒烟里验）。
"""
from __future__ import annotations

import json
import pytest

from edu_catalog import tools
from edu_catalog.store import CatalogStore, SubscriptionStore


def make_rows():
    ts = "2026-11-04T00:00:00+00:00"
    return [
        {"resource_id": "edu.paper.openalex.W1", "title": "How Students Learn",
         "education_source": "subfield_3304", "license": "cc0", "license_layer": "cc0_pd",
         "commercial_ok": True, "body_state": "stored_extraction",
         "source_url": "https://ocw.example/1", "source": "education-openalex", "scraped_at": ts},
        {"resource_id": "edu.card.coursera.0", "title": "Machine Learning Specialization",
         "platform": "Coursera", "education_source": "card_curated",
         "license": "metadata-only", "license_layer": "metadata_only", "commercial_ok": False,
         "body_state": "link_only", "fulltext_acquired": False,
         "source_url": "https://www.coursera.org/spec/ml", "source": "education-card-catalog",
         "scraped_at": ts},
        {"resource_id": "edu.episode.bad", "title": "缺溯源的行",
         "education_source": "podcast_rss", "body_state": "pending",
         "source": "education-podcast", "scraped_at": ts},  # 缺 source_url+license → 未发布
    ]


@pytest.fixture()
def stores(tmp_path):
    cat = tmp_path / "catalog.jsonl"
    cat.write_text("\n".join(json.dumps(r) for r in make_rows()))
    return CatalogStore(cat), SubscriptionStore(tmp_path / "subs.json")


@pytest.fixture()
def api(stores):
    """取回五工具的裸函数（闭包内注册后从 FastMCP 拿太绕，直接按注册顺序重建）。"""
    funcs = {}

    class Recorder:
        def tool(self, fn):
            funcs[fn.__name__] = fn

    tools.register(Recorder(), catalog=lambda: stores[0], subs=stores[1])
    return funcs


# ── R1 闭集与写面边界 ────────────────────────────────────────────────────────

def test_tool_closed_set(api, stores):
    assert set(api) == {"edu_search", "edu_item_detail", "edu_overview",
                        "edu_subscribe", "edu_unsubscribe"}


def test_subscribe_does_not_touch_catalog(api, stores):
    """spec cap2 R1 场景「写面边界可证」：订阅写入后目录文件逐字节不变。"""
    cat = stores[0]
    before = cat.bytes_snapshot()
    r = api["edu_subscribe"](kind="topic", target="physics education")
    assert r["status"] == "ok"
    assert cat.bytes_snapshot() == before  # 目录条目表逐字段不变（字节级更强）
    mid = cat.bytes_snapshot()
    r2 = api["edu_unsubscribe"](subscription_id=r["subscription"]["id"])
    assert r2["status"] == "ok"
    assert cat.bytes_snapshot() == mid == before
    assert api["edu_unsubscribe"](subscription_id="nope")["status"] == "not_indexed"


def test_subscribe_rejects_bad_kind(api):
    assert api["edu_subscribe"](kind="server", target="x")["status"] == "rejected"


# ── R2 许可/新鲜度上脸 + 卡片边界 + 发布态闸（6.2 端到端消费点）─────────────

def test_search_carries_license_and_freshness(api):
    res = api["edu_search"](query="learn")
    assert res["status"] == "ok"
    for it in res["items"]:
        assert it["license"], "结果行必须带许可"
        assert it["as_of"], "结果行必须带新鲜度"


def test_card_result_boundary_and_no_body(api):
    res = api["edu_search"](query="machine learning")
    cards = [i for i in res["items"] if i["resource_type"] == "card"]
    assert cards, "卡层检索必须出卡"
    for c in cards:
        assert "card_boundary" in c
        assert c["card_boundary"]["note"].startswith("正文在原站")
        assert c["body_state"] == "link_only"
        assert "body_markdown" not in json.dumps(c)  # 结果里无正文


def test_unpublished_row_invisible_in_search_and_detail(api):
    """6.2 端到端：注入缺陷条目（缺 source_url/license）→ 检索不可见、详情给可判别原因。"""
    res = api["edu_search"](query="缺溯源")
    assert res["status"] == "no_match", "未发布行 MUST NOT 出现在检索"
    d = api["edu_item_detail"](resource_id="edu.episode.bad")
    assert d["status"] == "not_indexed"
    assert "unpublished" in d["reason"]
    ov = api["edu_overview"]()
    assert ov["catalog"]["unpublished_excluded"] == 1
    assert ov["catalog"]["total_ingested"] == 3


# ── R3 空果与故障分离 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("reason", list(tools.REASONS))
def test_each_failure_reason_enum(stores, reason):
    """每 reason 一条单测：通道故障返回显式 reason，绝不 no_match 冒充。"""
    err = tools.ChannelError("central_store", reason, "injected")
    funcs = {}

    class Recorder:
        def tool(self, fn):
            funcs[fn.__name__] = fn
    tools.register(Recorder(), catalog=lambda: stores[0], subs=stores[1], error_hook=lambda: err)
    for name in ("edu_search", "edu_item_detail", "edu_overview"):
        r = funcs[name]("How Students Learn") if name == "edu_item_detail" else funcs[name](query="x") if name == "edu_search" else funcs[name]()
        assert r["status"] == "channel_failure"
        assert r["reason"] == reason
        assert r["channel"] == "central_store"


def test_no_match_reports_searched_scope(api):
    res = api["edu_search"](query="不存在的主题xyz")
    assert res["status"] == "no_match"
    assert res["searched"]["query"] == "不存在的主题xyz"  # 注明检索域
    assert "catalog_size" in res


# ── 2.2 撤回/合并传导（spec cap1 R5 场景端到端）─────────────────────────────

def test_tombstone_withdrawn_and_merged_propagate(tmp_path):
    """注入人造撤回/合并墓碑 → 检索不再以现行态出现，详情呈现标记态/重定向。"""
    ts = "2026-11-04T00:00:00+00:00"
    rows = [
        {"resource_id": "https://openalex.org/W1", "work_id": "https://openalex.org/W1",
         "title": "Alive Paper", "license": "cc0", "source_url": "https://x/1",
         "source": "education-openalex", "scraped_at": ts, "education_source": "subfield_3304"},
        {"resource_id": "https://openalex.org/W2", "work_id": "https://openalex.org/W2",
         "title": "Merged Paper", "license": "cc0", "source_url": "https://x/2",
         "source": "education-openalex", "scraped_at": ts, "education_source": "subfield_3304"},
        {"work_id": "https://openalex.org/W1", "status": "withdrawn",
         "source": "education-openalex", "scraped_at": ts},
        {"work_id": "https://openalex.org/W2", "status": "merged",
         "merged_into": "https://openalex.org/W3",
         "source": "education-openalex", "scraped_at": ts},
    ]
    cat = tmp_path / "catalog.jsonl"
    cat.write_text("\n".join(json.dumps(r) for r in rows))
    funcs = {}

    class Recorder:
        def tool(self, fn):
            funcs[fn.__name__] = fn
    tools.register(Recorder(), catalog=lambda: CatalogStore(cat),
                   subs=SubscriptionStore(tmp_path / "subs.json"))

    sr = funcs["edu_search"](query="paper")
    assert sr["status"] == "no_match"                      # 现行态退场
    d1 = funcs["edu_item_detail"](resource_id="https://openalex.org/W1")
    assert d1["item"]["marked_state"] == "withdrawn"       # 呈现标记而非消失
    d2 = funcs["edu_item_detail"](resource_id="https://openalex.org/W2")
    assert d2["item"]["marked_state"] == "merged"
    assert d2["item"]["redirect_to"] == "https://openalex.org/W3"
    ov = funcs["edu_overview"]()
    assert ov["catalog"]["withdrawn_or_merged_excluded"] == 2
