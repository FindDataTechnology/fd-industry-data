"""eastmoney-disclosure-plan 宇宙参数化契约（vertical-lane-generalization 任务 3.4）。

要点：
- 缺省宇宙由注册表 `universes/cn-a.yaml` 的 `verticals: [bank]` 决定，且与
  改造前内嵌的 42 家银行清单**逐项一致**（该单元在产，默认行为不得漂移）；
- 其它垂直线以 `codes` 显式传入（酒店/餐饮），不并入默认值；
- 空集合与非法代码硬失败，不静默回落。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

UNIT = Path(__file__).resolve().parents[1] / "spiders" / "eastmoney-disclosure-plan" / "spider.py"

# 改造前内嵌的 42 家银行（顺序即当时元组顺序）。零回归以此为准。
PRE_CHANGE_BANKS = (
    "601398", "601288", "601988", "601939", "601328", "601658", "600036",
    "601166", "601998", "600016", "601818", "600000", "600015", "601916",
    "601838", "601860", "603323", "600919", "600926", "600928", "600908",
    "601009", "601169", "601187", "601229", "601128", "601077", "601825",
    "601997", "601963", "601665", "601577", "601528",
    "002142", "000001", "001227", "002807", "002839", "002966", "002948",
    "002936", "002958",
)


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("_em_disclosure_plan", UNIT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_em_disclosure_plan"] = module
    spec.loader.exec_module(module)
    return module


def test_pre_change_snapshot_has_42():
    assert len(PRE_CHANGE_BANKS) == 42 == len(set(PRE_CHANGE_BANKS))


def test_default_codes_match_pre_change_bank_list(mod):
    assert mod._default_codes() == PRE_CHANGE_BANKS


def test_default_vertical_is_bank(mod):
    assert mod.DEFAULT_VERTICALS == ("bank",)


def test_explicit_codes_override_wins(mod):
    assert mod._normalize_codes(["600754", "600258"]) == ("600754", "600258")


def test_comma_separated_string_accepted(mod):
    assert mod._normalize_codes("600754, 600258") == ("600754", "600258")


def test_empty_collection_is_hard_failure(mod):
    with pytest.raises(ValueError):
        mod._normalize_codes([])
    with pytest.raises(ValueError):
        mod._normalize_codes("")


def test_non_six_digit_code_is_hard_failure(mod):
    with pytest.raises(ValueError):
        mod._normalize_codes(["ABC"])
    with pytest.raises(ValueError):
        mod._normalize_codes(["60075"])


def test_filter_uses_supplied_universe(mod):
    expr = mod._build_filter("2025-01-01", ("600754", "600258"))
    assert '"600754"' in expr and '"600258"' in expr
    assert "601398" not in expr
    assert "FIRST_APPOINT_DATE>='2025-01-01'" in expr


def test_manifest_declares_codes_parameter():
    """manifest 的参数声明必须与实现同步，否则派发面传不进 codes。"""

    text = (UNIT.parent / "manifest.yaml").read_text(encoding="utf-8")
    assert "- name: codes" in text
