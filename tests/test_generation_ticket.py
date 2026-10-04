"""生成单契约（source-generation-flow）：kind/brief/slug 校验与存量兼容。"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.health.ticket import new_ticket, validate_ticket  # noqa: E402


def _gen(**over):
    kw = dict(
        source="nmc-weather",
        unit="spiders/nmc-weather/",
        kind="generate",
        brief={
            "source_urls": ["https://www.nmc.cn/rest/weather?stationid=Wqsps"],
            "expectations": "北京站实况：温度/湿度/天气描述",
            "cadence": "hourly",
            "notes": "",
        },
        evidence={},
        verify_commands=["python3 scripts/health_verify.py --ticket x.yaml"],
        golden_paths=["spiders/nmc-weather/golden/"],
    )
    kw.update(over)
    return new_ticket(**kw)


def test_valid_generation_ticket():
    assert validate_ticket(_gen()) == []


def test_generation_requires_empty_category():
    errs = validate_ticket(_gen(category="contract"))
    assert any("category 必须为空" in e for e in errs)


def test_generation_requires_brief_fields():
    errs = validate_ticket(_gen(brief={"source_urls": [], "expectations": "  "}))
    assert any("source_urls" in e for e in errs)
    assert any("expectations" in e for e in errs)
    errs2 = validate_ticket(_gen(brief=None))
    assert any("brief 必填" in e for e in errs2)


def test_bad_kind_and_unit():
    assert any("kind 非法" in e for e in validate_ticket(_gen(kind="gen")))
    assert any("slug 非法" in e for e in validate_ticket(_gen(unit="spiders/Bad_Slug/")))
    assert any("unit 必须形如" in e for e in validate_ticket(_gen(unit="spiders/nmc-weather")))


def test_repair_default_unchanged():
    doc = new_ticket(source="alpha", unit="spiders/alpha/", evidence={},
                     verify_commands=["x"], category="structure")
    assert doc["kind"] == "repair"           # 缺省修复单
    assert validate_ticket(doc) == []
    doc2 = new_ticket(source="alpha", unit="spiders/alpha/", evidence={}, verify_commands=["x"])
    assert any("category 不能为空" in e for e in validate_ticket(doc2))  # 既有规则保持


def test_legacy_ticket_without_kind_is_repair():
    doc = new_ticket(source="alpha", unit="spiders/alpha/", evidence={},
                     verify_commands=["x"], category="network")
    del doc["kind"]                           # 模拟存量工单
    assert validate_ticket(doc) == []         # 按 repair 处理，行为不变