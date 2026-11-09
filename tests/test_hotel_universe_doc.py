"""酒店监控宇宙真源契约（add-hotel-vertical 任务 3.6）。

真源 `docs/hotel-universe.yaml` 同时被 `scripts/register_hotel_universe.py`
消费，所以这里的断言与脚本自检逐条对齐 —— 防止有人只改 yaml 不改口径、
或把「未核实」悄悄改成「已核实」。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
import yaml

DOC = Path(__file__).resolve().parents[1] / "docs" / "hotel-universe.yaml"

REGIONS = {"CN": 6, "HK": 5, "US": 14, "TW": 4, "JP": 2, "EU": 2}
SEGMENTS = {
    "luxury-upscale", "midscale", "economy-budget",
    "serviced-apartment", "resort-casino", "OTA-platform",
}
REGION_LANES = {
    "CN": {"cninfo-operating", "fd-cn-report"},
    "HK": {"hkex-disclosure"},
    # 美股：财报车道人人走；经营细分（8-K exhibit 99.1）只有 5 家有载体，
    # 属「允许的附加车道」而非每家必备（wave 3 实测：MAR/HLT/WH/H/CHH）。
    "US": {"sec-edgar-facts", "sec-edgar-8k-operating"},
    "TW": {"twse-monthly-revenue"},
    "JP": {"edinet-api"},
    "EU": set(),
}


@pytest.fixture(scope="module")
def doc():
    return yaml.safe_load(DOC.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def companies(doc):
    return doc["companies"]


def test_thirty_three_entities_with_region_split(companies):
    assert len(companies) == 33
    assert Counter(c["region"] for c in companies) == REGIONS


def test_codes_unique(companies):
    codes = [c["code"] for c in companies]
    assert len(set(codes)) == len(codes)


def test_segments_within_vocabulary(companies):
    for c in companies:
        assert c["segment"] in SEGMENTS, c["code"]
    # 本批 resort-casino 无实体（原唯一候选 01992 已退市）
    assert "resort-casino" not in {c["segment"] for c in companies}


def test_platform_side_is_four(companies):
    assert sum(1 for c in companies if c["segment"] == "OTA-platform") == 4


def test_lane_set_matches_region(companies):
    """每条车道都必须是该 region 的已知车道；美股须含财报车道。

    不能要求「逐家完全相同」：美股经营数据车道按载体可得性挂（5 家有 8-K
    exhibit，其余只有财报线），逐家相等会把真实差异判成漂移。
    """
    for c in companies:
        lanes = set(c["lanes"])
        assert lanes <= REGION_LANES[c["region"]], c["code"]
        if c["region"] == "US":
            assert "sec-edgar-facts" in lanes, c["code"]
    # 8-K 经营细分车道确实只挂在有载体的 5 家
    assert sum(1 for c in companies if "sec-edgar-8k-operating" in c["lanes"]) == 5


def test_eu_entities_declare_no_lane(companies):
    eu = [c for c in companies if c["region"] == "EU"]
    assert {c["code"] for c in eu} == {"AC.PA", "WTB.L"}
    for c in eu:
        assert c["lanes"] == []
        assert c["verified"] == {"ticker": False, "lanes": False}


def test_verified_flags_are_consistent(companies):
    """无车道不得标 lanes 已核实；未核实集合固定为台股 4 + 日股 2 + 欧股 2。"""
    for c in companies:
        if not c["lanes"]:
            assert c["verified"]["lanes"] is False, c["code"]
    unverified = {c["code"] for c in companies if not c["verified"]["ticker"]}
    assert unverified == {
        "2707.TW", "2748.TW", "2739.TW", "2704.TW",
        "9722.T", "9616.T", "AC.PA", "WTB.L",
    }


def test_delisted_entity_is_excluded_not_counted(doc, companies):
    codes = {c["code"] for c in companies}
    excluded = {e["code"]: e for e in doc.get("excluded") or []}
    assert "01992.HK" in excluded
    assert "01992.HK" not in codes
    assert "00071.HK" in codes  # 顶替者
    assert "退市" in excluded["01992.HK"]["reason"]


def test_required_fields_present(companies):
    for c in companies:
        for field in ("code", "name_zh", "name_en", "region", "exchange", "segment"):
            assert c.get(field), f"{c.get('code')}: 缺 {field}"
        assert "lanes" in c, c["code"]
        assert set(c["verified"]) == {"ticker", "lanes"}, c["code"]
