"""港股酒店营运数据通道契约（add-hotel-vertical 任务 4.2）。

两份 fixture 都是**上游真件 PDF 的文本层**（poppler `-layout` 抽取，未改写）：

* `hkex_00045_2026_q3.txt` — 大酒店《未經審核營運數據 – 2026 年第三季度》
  中文版，`.../2026/1104/2026110401257_c.pdf`（502,292 字节）。
* `hkex_00045_2026_q3_en.txt` — 同一公告英文版
  `.../2026/1104/2026110401256.pdf`（254,143 字节）。

英文版**不是**可有可无的第二份：车道的 `doc_url` 只存英文版（中文版是另一个
`NEWS_ID`，无法从英文编号推导），所以运行时读的是英文 PDF，而中文版是校验基准。
两版逐条比对一致是本通道最硬的证据（`test_zh_and_en_parse_identically`）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))

import hotel_extractors.hkex_operating as mod  # noqa: E402

ZH_TEXT = (FIXTURES / "hkex_00045_2026_q3.txt").read_text(encoding="utf-8")
EN_TEXT = (FIXTURES / "hkex_00045_2026_q3_en.txt").read_text(encoding="utf-8")
ZH_TITLE = "未經審核營運數據 – 2026 年第三季度"
EN_TITLE = "Unaudited Operating Statistics – Third Quarter of 2026"


def _keyed(records):
    return {(r["metric"], r["dimension"], r["date"]): r for r in records}


@pytest.fixture(scope="module")
def zh():
    return mod.parse_operating_text(ZH_TEXT, ZH_TITLE)


@pytest.fixture(scope="module")
def en():
    return mod.parse_operating_text(EN_TEXT, EN_TITLE)


# ── 结构与口径 ────────────────────────────────────────────────────────────
def test_three_tables_ten_series_thirty_quarters(zh):
    assert len(zh) == 30                    # (4 revpar + 3 rent + 3 occupancy) × 3 季
    assert {r["metric"] for r in zh} == {"revpar", "rent", "occupancy"}
    assert len({(r["metric"], r["dimension"]) for r in zh}) == 10


def test_only_current_year_quarters_are_written(zh):
    """只写当年列；上年列只用于核对，不重复入库。"""
    assert {r["date"] for r in zh} == {"2026-03-31", "2026-06-30", "2026-09-30"}


def test_blank_quarter_is_absent_not_zero(zh):
    """2026Q4 表内为空白；空白就是未披露，不能落 0。"""
    assert not [r for r in zh if r["date"] == "2026-12-31"]
    assert all(r["value"] != "0" for r in zh)


@pytest.mark.parametrize(
    "dimension,q1,q2,q3",
    [
        ("greater_china", "2902", "3108", "2912"),
        ("europe", "4886", "8606", "10616"),
        ("usa", "4377", "6189", "6534"),
        ("asia_ex_greater_china", "2856", "2569", "2046"),
    ],
)
def test_revpar_by_region_matches_the_announcement(zh, dimension, q1, q2, q3):
    recs = _keyed(zh)
    got = [recs[("revpar", dimension, d)]["value"] for d in
           ("2026-03-31", "2026-06-30", "2026-09-30")]
    assert got == [q1, q2, q3]


@pytest.mark.parametrize(
    "metric,dimension,q1,q2,q3",
    [
        ("rent", "residential", "48", "48", "49"),
        ("rent", "retail_arcades", "142", "146", "141"),
        ("rent", "office", "50", "61", "62"),
        ("occupancy", "residential", "97", "98", "94"),
        ("occupancy", "retail_arcades", "89", "90", "91"),
        ("occupancy", "office", "64", "63", "61"),
    ],
)
def test_leasing_tables_match_the_announcement(zh, metric, dimension, q1, q2, q3):
    recs = _keyed(zh)
    got = [recs[(metric, dimension, d)]["value"] for d in
           ("2026-03-31", "2026-06-30", "2026-09-30")]
    assert got == [q1, q2, q3]


def test_every_row_carries_its_caliber(zh):
    for r in zh:
        assert r["caliber"]
        assert "未经审核" in r["caliber"]
    assert "客房總收入" in _keyed(zh)[("revpar", "europe", "2026-09-30")]["caliber"]
    assert "已出租面積" in _keyed(zh)[("rent", "office", "2026-09-30")]["caliber"]
    assert "可出租面積" in _keyed(zh)[("occupancy", "office", "2026-09-30")]["caliber"]


def test_units_follow_the_source_notes(zh):
    recs = _keyed(zh)
    assert recs[("revpar", "usa", "2026-03-31")]["unit"] == "HKD"          # 附注 1：港元
    assert recs[("rent", "office", "2026-03-31")]["unit"] == "HKD/sqft/month"
    assert recs[("occupancy", "office", "2026-03-31")]["unit"] == "%"


# ── 跨入口一致性（中文版 vs 英文版）──────────────────────────────────────
def test_zh_and_en_parse_identically(en, zh):
    zh_vals = {(r["metric"], r["dimension"], r["date"]): r["value"] for r in zh}
    en_vals = {(r["metric"], r["dimension"], r["date"]): r["value"] for r in en}
    assert en_vals == zh_vals


def test_en_layout_falls_back_to_sequential_and_says_so():
    """英文版空白列把间距压扁，等距锚点被拒后走顺序规则——必须留痕。"""
    notes: list[str] = []
    recs = mod.parse_operating_text(EN_TEXT, EN_TITLE, notes)
    assert len(recs) == 30
    assert [n for n in notes if "列对齐不足" in n]
    assert not [n for n in notes if "不可解析" in n]


# ── 交叉校验必须真的会拦 ──────────────────────────────────────────────────
def test_cross_check_drops_a_tampered_value():
    """改掉一个数字使「+18%」对不上：该季必须被丢弃并留证，而不是照写。"""
    tampered = ZH_TEXT.replace("2,912", "5,912", 1)
    notes: list[str] = []
    recs = mod.parse_operating_text(tampered, ZH_TITLE, notes)
    assert not [r for r in recs
                if r["metric"] == "revpar" and r["dimension"] == "greater_china"
                and r["date"] == "2026-09-30"]
    assert [n for n in notes if "列归属不可信" in n]
    # 同表其他季不受牵连
    assert [r for r in recs
            if r["metric"] == "revpar" and r["dimension"] == "greater_china"
            and r["date"] == "2026-03-31"]


def test_untampered_parse_has_no_cross_check_failures(zh):
    notes: list[str] = []
    mod.parse_operating_text(ZH_TEXT, ZH_TITLE, notes)
    assert not [n for n in notes if "列归属不可信" in n]


# ── 选择与可得性 ──────────────────────────────────────────────────────────
def test_operating_title_and_placeholder_are_distinguished():
    assert mod._OPERATING_TITLE.search("未經審核營運數據 – 2026年第三季度")
    assert mod._OPERATING_TITLE.search("Unaudited Operating Statistics – Third Quarter of 2026")
    placeholder = ("發行人剛在本網站英文版面發布一項公告，"
                   "相應中文版本或會／或不會在本版面發布。")
    assert mod._PLACEHOLDER_TITLE.search(placeholder)


def test_title_period_both_languages():
    assert mod.title_period(ZH_TITLE) == (2026, 3)
    assert mod.title_period(EN_TITLE) == (2026, 3)
    assert mod.title_period("未經審核營運數據 – 2025 年第一季度") == (2025, 1)
    assert mod.title_period("沒有期次的標題") is None


def test_unparseable_title_is_a_note_not_a_guess():
    notes: list[str] = []
    assert mod.parse_operating_text(ZH_TEXT, "沒有期次的標題", notes) == []
    assert [n for n in notes if "卡在标题期次" in n]


def test_quarter_end_dates():
    assert mod.quarter_end(2026, 1) == "2026-03-31"
    assert mod.quarter_end(2026, 2) == "2026-06-30"
    assert mod.quarter_end(2026, 3) == "2026-09-30"
    assert mod.quarter_end(2026, 4) == "2026-12-31"


def test_availability_covers_all_five_hk_hotels():
    codes = {r["entity_code"] for r in mod.AVAILABILITY}
    assert codes == {"00045.HK", "01179.HK", "00069.HK", "01270.HK", "00071.HK"}
    by = {r["entity_code"]: r for r in mod.AVAILABILITY}
    assert by["00045.HK"]["status"] == "parsed"
    assert by["01179.HK"]["status"] == "embedded-in-results"
    assert by["00069.HK"]["status"] == "no-operating-data-table"
    assert by["01270.HK"]["status"] == "no-operating-disclosure"
    assert by["00071.HK"]["status"] == "no-operating-disclosure"
    for rec in mod.AVAILABILITY:
        assert rec["reason"] and rec["evidence"]
        # 不可得的必须给恢复条件（"无"也算显式结论），不得留空
        assert rec["recovery"]


def test_availability_never_silently_empty():
    rec = mod.availability("01179.HK")
    assert rec["status"] == "embedded-in-results"
    assert rec["reason"]
    unknown = mod.availability("09988.HK")
    assert unknown["status"] == "not-in-hk-hotel-universe"
    assert unknown["reason"]


# ── 概念表 ────────────────────────────────────────────────────────────────
def test_concepts_cover_ten_series():
    assert len(mod.CONCEPTS) == 10
    codes = [c[0] for c in mod.CONCEPTS]
    assert len(set(codes)) == 10
    for c in mod.CONCEPTS:
        assert len(c) == 9
        assert c[6] == "quarter" and c[7] == "stock" and c[8] == mod.SOURCE
    assert set(mod.CONCEPT_BY_KEY) == {
        ("revpar", "greater_china"), ("revpar", "europe"),
        ("revpar", "usa"), ("revpar", "asia_ex_greater_china"),
        ("rent", "residential"), ("rent", "retail_arcades"), ("rent", "office"),
        ("occupancy", "residential"), ("occupancy", "retail_arcades"),
        ("occupancy", "office"),
    }
