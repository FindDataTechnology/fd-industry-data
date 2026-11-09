"""口径登记表与代码常量的一致性契约（add-hotel-vertical 任务 4.4）。

``docs/hotel-kpi-caliber.md`` 是口径的唯一真源；代码里的 ``CALIBER`` /
``AVAILABILITY`` / 宇宙表 ``lanes`` 必须与它同口径。这份测试钉三件事：

1. 宇宙 33 家在口径表里逐家有一行（不可得的也要有一行，不许静默缺失）；
2. 美股 14 家的可得性状态与 ``AVAILABILITY`` 常量逐条一致；
3. 五家可解析美股的口径主句在口径表里逐字出现（改了代码必须改文档）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "hotel-kpi-caliber.md"
UNIVERSE = ROOT / "docs" / "hotel-universe.yaml"
sys.path.insert(0, str(ROOT / "scripts"))

import hotel_extractors.sec_8k_operating as us  # noqa: E402


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def universe() -> list[dict]:
    return yaml.safe_load(UNIVERSE.read_text(encoding="utf-8"))["companies"]


def test_every_universe_entity_has_a_row(doc, universe):
    """33 家一家都不能少：不可得也要有显式行（不可得是结论，不是缺口）。"""
    assert len(universe) == 33
    missing = [c["code"] for c in universe if c["code"] not in doc]
    assert missing == []


def test_every_us_entity_status_matches_the_code_matrix(doc, universe):
    codes = {c["code"] for c in universe}
    for record in us.AVAILABILITY:
        code = record["entity_code"]
        assert code in codes, code
        # 该家的行里必须出现它的状态词
        line = next(l for l in doc.splitlines() if f" {code} " in l or l.startswith(f"| {code} "))
        assert record["status"] in line, (code, record["status"], line)


@pytest.mark.parametrize("issuer", ["MAR", "HLT", "WH", "H", "CHH"])
def test_caliber_headline_appears_verbatim(doc, issuer):
    """口径主句（「；」前那一段）必须逐字在文档里 —— 改代码不文档即红。"""
    headline = us.CALIBER[issuer].split("；")[0]
    assert headline in doc, headline


def test_us_availability_covers_all_fourteen(doc):
    assert len(us.AVAILABILITY) == 14
    statuses = {r["status"] for r in us.AVAILABILITY}
    assert statuses == {
        "parsed", "not-an-8k-filer", "no-revpar-disclosure", "ota-no-hotel-kpi",
    }


def test_htht_falsification_is_recorded(doc):
    """任务书「至少 4 家（MAR/HLT/WH/HTHT）」的 HTHT 一项必须是证伪记录，
    且带着 271 / 0 这组实测数字 —— 不是含糊的「不支持」。"""
    assert "HTHT" in doc
    assert "271 份 6-K" in doc and "0 份 8-K" in doc
    record = us.availability("HTHT")
    assert record["status"] == "not-an-8k-filer"
    assert "271 份 6-K" in record["reason"] and "0 份 8-K" in record["reason"]


def test_a_share_ytd_vs_month_is_stated(doc):
    """A 股最容易错的一条：1-11 月 = 年初至今，不是 11 月单月。"""
    assert "1-11 月" in doc and "单月" in doc
    assert "169.26" in doc and "231.375" in doc      # 加权 vs 算术平均的实测反例


def test_doc_matches_live_concept_counts(doc):
    """文档附录的计数与代码里的概念清单一致（库内计数见 4.1/4.2/4.3 任务账）。"""
    assert len(us.CONCEPTS) == 39
    assert "39" in doc
