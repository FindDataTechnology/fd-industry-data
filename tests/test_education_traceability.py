"""教育垂域蜘蛛溯源契约测试（spec cap1 R4「每条目录条目可溯源」的蜘蛛侧断言）。

四字段非空：source_url / source(通道) / scraped_at(采集时间) / license。
缺一者 MUST NOT 进入已发布目录态（发布态闸由 6.2 实现，这里钉蜘蛛侧输出契约）。

跑法：``pytest tests/test_education_traceability.py -v``
（在线测试：礼貌限速，默认只拉 10+10 行元数据；不取正文。）
"""
from __future__ import annotations

import pytest

MANDATORY_FIELDS = ("source_url", "source", "scraped_at", "license")


def _assert_traceability(rows: list[dict], label: str) -> None:
    assert rows, f"{label}: returned empty — traceability test needs real rows"
    for i, row in enumerate(rows):
        for field in MANDATORY_FIELDS:
            value = row.get(field)
            assert value, f"{label} row[{i}] missing mandatory field {field!r} (spec cap1 R4)"
            assert str(value).strip(), f"{label} row[{i}] field {field!r} is blank"


@pytest.mark.online
def test_openalex_education_traceability():
    from spiders.education_openalex.spider import run_education_openalex

    rows = run_education_openalex(limit=10, phase="skeleton", education_source="subfield_3304")
    _assert_traceability(rows, "openalex-skeleton")
    # OpenAlex 元数据骨架固定口径：license=cc0、body_state=link_only
    for row in rows:
        assert row["license"] == "cc0"
        assert row["body_state"] == "link_only"
        assert row["work_id"].startswith("https://openalex.org/")


@pytest.mark.online
def test_epmc_oa_traceability():
    from spiders.education_oa_fulltext.spider import run_education_epmc_oa

    rows = run_education_epmc_oa(limit=10)
    _assert_traceability(rows, "epmc-oa")
    for row in rows:
        assert row["source_url"].startswith("https://europepmc.org/article/")
        assert row["body_state"] == "link_only"  # fetch_bodies=False


@pytest.mark.online
def test_arxiv_new_traceability():
    from spiders.education_oa_fulltext.spider import run_education_arxiv_new

    rows = run_education_arxiv_new(limit=10)
    _assert_traceability(rows, "arxiv-new")
    for row in rows:
        assert row["work_id"].startswith("arxiv:")
        assert row["source_url"].startswith("https://arxiv.org/abs/")
