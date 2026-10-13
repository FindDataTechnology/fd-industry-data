"""nbs-stats 入口参数接线测试（declared-param-reachability 的落地验证）。

manifest 声明了 categories/start_year，此前入口是 run_nbs_stats(limit) ——
声明不可达。接上之后必须证明两件事：**缺省行为与改动前一致**（全类别 + 2010），
以及参数真的改变取数范围（否则「接上了」只是签名好看）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_SPIDER = REPO / "spiders" / "nbs-stats" / "spider.py"


def _load():
    name = "nbs_stats_under_test"
    spec = importlib.util.spec_from_file_location(name, _SPIDER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def spider():
    return _load()


@pytest.fixture
def captured(monkeypatch, spider):
    """Capture which (category, indicator, start_year) triples the entry requests."""
    seen: list[tuple[str, str, int]] = []

    async def fake_fetch(category, indicator, start_year):
        seen.append((category, indicator, start_year))
        return [{"category": category, "indicator_type": indicator,
                 "value": 1.0, "period": "2024"}]

    monkeypatch.setattr(spider, "_fetch_nbs_indicator", fake_fetch)
    # keep the circulation path out of the way: this test is about path 1 wiring
    monkeypatch.setattr(spider, "get_circulation_prices",
                        lambda limit=100: [])
    # politeness delay between indicators: irrelevant here, and 2s x 8 indicators
    # would dominate the suite's runtime
    monkeypatch.setattr(spider.NbsStatsSpider, "download_delay", 0)
    return seen


class TestDefaultBehaviourUnchanged:
    def test_defaults_cover_every_category(self, spider, captured):
        spider.run_nbs_stats(limit=100)
        got = {c for c, _, _ in captured}
        assert got == set(spider.INDICATOR_CATEGORIES)

    def test_default_start_year_matches_class_default(self, spider, captured):
        spider.run_nbs_stats(limit=100)
        assert {y for _, _, y in captured} == {spider.NbsStatsSpider.start_year}

    def test_limit_still_caps_rows(self, spider, captured):
        assert len(spider.run_nbs_stats(limit=2)) <= 2


class TestDeclaredParamsTakeEffect:
    def test_categories_narrows_path_one(self, spider, captured):
        spider.run_nbs_stats(limit=100, categories=["gdp"])
        assert {c for c, _, _ in captured} == {"gdp"}

    def test_unknown_category_is_ignored_not_fatal(self, spider, captured):
        rows = spider.run_nbs_stats(limit=100, categories=["gdp", "nope"])
        assert {c for c, _, _ in captured} == {"gdp"}
        assert rows  # still produced data rather than raising

    def test_start_year_propagates(self, spider, captured):
        spider.run_nbs_stats(limit=100, categories=["gdp"], start_year=2015)
        assert {y for _, _, y in captured} == {2015}

    def test_empty_categories_requests_nothing(self, spider, captured):
        spider.run_nbs_stats(limit=100, categories=[])
        assert captured == []
