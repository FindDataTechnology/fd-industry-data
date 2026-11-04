"""Wave 5 实体与发布态测试（spec cap1 R3 合并/ R4 缺溯源不入发布态）。

6.1：三键合并（官方+CC 转载同 DOI→一实体两呈现）；歧义样本不合并（一边缺 DOI→双条）；
     与 papers 命名空间对账（linkage.doi 关联、不共用行）。
6.2：publish_gate 四字段逐缺断言（机制真源；查询面端到端在 7.x 消费）。
"""
from __future__ import annotations

import pytest


# ── 6.1 三类实体与合并三键 ──────────────────────────────────────────────────

def test_make_code_edu_prefix():
    from spiders.education_catalog_core import make_code
    assert make_code("resource", "arxiv.2601.00001") == "edu.resource.arxiv.2601.00001"
    assert make_code("creator", "stanford") == "edu.creator.stanford"
    for bad_kind in ("paper", "thing", ""):  # kind 闭集之外一律拒
        with pytest.raises(ValueError):
            make_code(bad_kind, "x")
    with pytest.raises(ValueError):
        make_code("resource", "")  # native_code 必填


def test_cross_platform_merge_same_doi():
    """spec cap1 R3 场景：同一资源多平台呈现挂同一实体，各保留平台与许可。"""
    from spiders.education_catalog_core import group_presentations
    official = {"resource_id": "r1", "title": "Deep Learning for Students",
                "doi": "https://doi.org/10.1234/DL4S", "creators": ["Ada Lovelace"],
                "platform": "coursera", "source_url": "https://x.com/c/1",
                "license": "metadata-only", "scraped_at": "2026-11-04T00:00:00+00:00", "source": "s1"}
    repost = {"resource_id": "r2", "title": "Deep Learning for Students!",  # 标点差异
              "doi": "10.1234/dl4s",  # 大小写/前缀差异
              "creators": ["ada lovelace"],
              "platform": "OCW", "source_url": "https://y.edu/2",
              "license": "CC BY-NC-SA", "scraped_at": "2026-11-04T00:00:00+00:00", "source": "s2"}
    ents = group_presentations([official, repost])
    assert len(ents) == 1, "同 DOI 同题同人 MUST 合并为一实体"
    e = ents[0]
    assert e["code"].startswith("edu.resource.")
    platforms = {p["platform"] for p in e["presentations"]}
    assert platforms == {"coursera", "OCW"}
    licenses = {p["license"] for p in e["presentations"]}
    assert licenses == {"metadata-only", "CC BY-NC-SA"}  # 各呈现保留自己的许可


def test_ambiguous_no_merge_when_one_side_lacks_ext_id():
    """歧义样本：一边有 DOI 一边没有 → 宁挂双条不错合。"""
    from spiders.education_catalog_core import group_presentations
    a = {"resource_id": "a", "title": "Same Title", "doi": "10.1/x", "creators": ["C"],
         "platform": "p1", "source_url": "u1", "license": "cc0", "scraped_at": "t", "source": "s"}
    b = {"resource_id": "b", "title": "Same Title", "creators": ["C"],  # 无 doi
         "platform": "p2", "source_url": "u2", "license": "cc0", "scraped_at": "t", "source": "s"}
    ents = group_presentations([a, b])
    assert len(ents) == 2, "外部标识缺位 MUST NOT 触发合并（宁挂双条）"


def test_papers_namespace_linkage_not_shared_rows():
    """与 papers 域对账：同篇论文 edu.* 行以 linkage.doi 关联，不共用实体行。"""
    from spiders.education_catalog_core import linkage_keys, make_code
    openalex_row = {"openalex_id": "https://openalex.org/W1", "doi": "https://doi.org/10.5/a",
                    "title": "T", "creators": None, "source_url": "u",
                    "license": "cc0", "scraped_at": "t", "source": "education-openalex"}
    lk = linkage_keys(openalex_row)
    assert lk["doi"] == "10.5/a" and lk["work"].startswith("https://openalex.org/")
    code = make_code("resource", "openalex.W1")
    assert code == "edu.resource.openalex.W1"  # edu 前缀新命名空间，非 papers 行


# ── 6.2 发布态闸 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("drop", ["source_url", "source", "scraped_at", "license"])
def test_publish_gate_each_required_field(drop):
    """四字段逐缺断言：缺一者 MUST NOT 进已发布目录态（spec cap1 R4 场景）。"""
    from spiders.education_catalog_core import publish_gate
    row = {"title": "T", "resource_id": "r",
           "source_url": "https://x/1", "source": "s", "scraped_at": "t", "license": "cc0"}
    row[drop] = None
    ok, reasons = publish_gate(row)
    assert not ok
    assert reasons == [drop]
    # 补回即过
    full = {"title": "T", "resource_id": "r",
            "source_url": "https://x/1", "source": "s", "scraped_at": "t", "license": "cc0"}
    ok2, r2 = publish_gate(full)
    assert ok2 and r2 == []
