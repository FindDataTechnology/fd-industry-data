"""cninfo-operating 酒店类目契约（add-hotel-vertical 任务 3.5）。

实证前提（reports/3.5-ashare-disclosure-evidence.md）：
- A 股酒店唯一可命中的载体是锦江 2023 年「部分经营数据简报」；
- `主要经营数据` / `经营数据` 两个候选词在全市场都先命中「非经营性资金占用」，
  是假阳性来源，因此不进扩展类目；
- 默认 5 词 SEARCHKEYS 不动（该单元在产，改变默认词表会改变 limit 内的行构成）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

UNIT = Path(__file__).resolve().parents[1] / "spiders" / "cninfo-operating" / "spider.py"

PRODUCTION_SEARCHKEYS = ("产销快报", "销售简报", "发电量", "主要运营数据", "经营简报")


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("_cninfo_operating", UNIT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_cninfo_operating"] = module
    spec.loader.exec_module(module)
    return module


def test_default_searchkeys_unchanged(mod):
    """生产默认词表必须逐字不动。"""

    assert mod.SEARCHKEYS == PRODUCTION_SEARCHKEYS
    assert mod._normalize_keywords(None) == PRODUCTION_SEARCHKEYS


def test_extension_category_is_opt_in(mod):
    assert mod.EXTENSION_SEARCHKEYS == ("经营数据简报",)
    # 扩展词默认不出现在生产词表里
    assert "经营数据简报" not in mod.SEARCHKEYS


def test_hotel_brief_classified_as_operating_brief(mod):
    title = (
        "锦江酒店:上海锦江国际酒店股份有限公司有限服务型连锁酒店"
        "2023年1-11月部分经营数据简报"
    )
    assert mod.classify(title) == "经营简报"


def test_false_positive_is_not_classified(mod):
    """"非经营性资金占用" 不得被判成经营类目。"""

    title = "华天酒店:2026年半年度非经营性资金占用及其他关联资金往来情况汇总表"
    assert mod.classify(title) == ""


def test_period_extracted_from_hotel_brief(mod):
    title = "锦江酒店:有限服务型连锁酒店2023年1-11月部分经营数据简报"
    assert mod.extract_period(title, "2023-12-22") == "2023-11"


def test_keywords_accepts_list_and_csv(mod):
    assert mod._normalize_keywords(["经营数据简报"]) == ("经营数据简报",)
    assert mod._normalize_keywords("经营数据简报, 经营简报") == ("经营数据简报", "经营简报")


def test_empty_keywords_is_hard_failure(mod):
    with pytest.raises(ValueError):
        mod._normalize_keywords([])
    with pytest.raises(ValueError):
        mod._normalize_keywords("")


def test_golden_002_targets_the_hotel_brief():
    import json

    golden = json.loads(
        (UNIT.parent / "golden" / "002-cninfo-operating.json").read_text(encoding="utf-8")
    )
    assert golden["target"] == "run_cninfo_operating"
    assert golden["params"]["keyword"] == "经营数据简报"
    # golden 契约只支持 {field, equals}（见 golden-expect-equals-only 教训）
    for cond in golden["expect"]["rows_contains"]:
        assert set(cond) == {"field", "equals"}
