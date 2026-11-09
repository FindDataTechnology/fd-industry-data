"""A 股酒店经营数据通道契约（add-hotel-vertical 任务 4.1）。

两份 fixture 都是**上游真件正文**，从 `crawl_items` 里原样导出，未经改写：

* `cninfo_jinjiang_2023_11.txt` — 锦江 2023-12-22《…2023 年 1-11 月部分经营
  数据简报》，art_code `AN202312221614813250`，两张表，1-11 月为**年初至今累计**。
* `cninfo_jinjiang_2023_08.txt` — 锦江 2023-09-27《…2023 年 7、8 月部分经营
  数据简报》，art_code `AN202309261599895864`，四张表（境内/境外 × 7月/8月），
  每张表为**单月**。

两条口径必须严格区分：把 1-11 月当单季、把 7 月表并进 8 月，都是造数。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))

import hotel_extractors.ashare_operating as mod  # noqa: E402

YTD_BRIEF = (FIXTURES / "cninfo_jinjiang_2023_11.txt").read_text(encoding="utf-8")
MONTHLY_BRIEF = (FIXTURES / "cninfo_jinjiang_2023_08.txt").read_text(encoding="utf-8")
YTD_TITLE = "锦江酒店有限服务型连锁酒店2023年1-11月部分经营数据简报"
MONTHLY_TITLE = "锦江酒店有限服务型连锁酒店2023年7、8月部分经营数据简报"


def _by_key(records):
    return {(r["table"], r["grade"]): r for r in records}


# ── 累计口径（2023-12-22 简报）─────────────────────────────────────────────
def test_ytd_brief_yields_two_tables_three_grades():
    records = mod.parse_brief(YTD_BRIEF, YTD_TITLE)
    assert len(records) == 6
    assert {r["table"] for r in records} == {"表一", "表二"}
    assert {r["grade"] for r in records} == {"midscale", "economy", "overall"}
    assert {r["caliber"] for r in records} == {"ytd"}
    assert {r["date"] for r in records} == {"2023-11"}


@pytest.mark.parametrize(
    "table,grade,adr,occ,revpar",
    [
        ("表一", "midscale", "278.70", "69.66", "194.14"),
        ("表一", "economy", "184.05", "60.29", "110.96"),
        ("表一", "overall", "253.15", "66.86", "169.26"),
        ("表二", "midscale", "77.42", "60.57", "46.89"),
        ("表二", "economy", "62.98", "64.15", "40.40"),
        ("表二", "overall", "66.35", "63.28", "41.99"),
    ],
)
def test_ytd_values_match_the_brief(table, grade, adr, occ, revpar):
    rec = _by_key(mod.parse_brief(YTD_BRIEF, YTD_TITLE))[(table, grade)]
    # 只取 2023 当期列；2022 对比列与同比列不写
    assert (rec["adr"], rec["occ"], rec["revpar"]) == (adr, occ, revpar)


def test_ytd_period_label_is_not_a_quarter():
    rec = _by_key(mod.parse_brief(YTD_BRIEF, YTD_TITLE))[("表一", "overall")]
    assert "1-11" in rec["period_label"]
    assert rec["caliber"] == "ytd"


def test_overall_grade_is_published_not_recomputed():
    """平均行是房间数加权值，不是两档简单均值——重算就等于造数。"""
    recs = _by_key(mod.parse_brief(YTD_BRIEF, YTD_TITLE))
    published = float(recs[("表一", "overall")]["revpar"])
    naive = (float(recs[("表一", "midscale")]["revpar"])
             + float(recs[("表一", "economy")]["revpar"])) / 2
    assert published == 169.26
    assert abs(published - naive) > 1.0


def test_ytd_parses_without_any_unparsed_note():
    notes: list[str] = []
    mod.parse_brief(YTD_BRIEF, YTD_TITLE, notes)
    assert [n for n in notes if "不可解析" in n] == []


# ── 单月口径（2023-09-27 简报）─────────────────────────────────────────────
def test_monthly_brief_yields_four_tables():
    records = mod.parse_brief(MONTHLY_BRIEF, MONTHLY_TITLE)
    assert len(records) == 12                       # 4 表 × 3 分档
    assert {r["caliber"] for r in records} == {"monthly"}
    assert {r["date"] for r in records} == {"2023-07", "2023-08"}
    assert sum(1 for r in records if r["scope"] == "domestic") == 6
    assert sum(1 for r in records if r["scope"] == "overseas") == 6


def test_monthly_july_table_is_not_smeared_into_august():
    """7 月表落 2023-07、8 月表落 2023-08：payload 的 period 会把两者压成一个。"""
    recs = mod.parse_brief(MONTHLY_BRIEF, MONTHLY_TITLE)
    july = {(r["table"], r["grade"]): r for r in recs if r["date"] == "2023-07"}
    august = {(r["table"], r["grade"]): r for r in recs if r["date"] == "2023-08"}
    assert july[("表一", "midscale")]["revpar"] == "229.87"
    assert august[("表二", "midscale")]["revpar"] == "231.34"
    assert july[("表一", "midscale")]["revpar"] != august[("表二", "midscale")]["revpar"]


def test_monthly_overseas_unit_label_mismatch_is_recorded_not_silently_fixed():
    """境外表单位栏印作「元/间」而口径是欧元——数值不改，差异必须留痕。"""
    notes: list[str] = []
    recs = mod.parse_brief(MONTHLY_BRIEF, MONTHLY_TITLE, notes)
    overseas = [r for r in recs if r["scope"] == "overseas"]
    assert overseas and all(r["unit_label"] == "元" for r in overseas)
    assert [n for n in notes if "单位栏印作" in n]
    # 数值本身照抄原文，不做汇率换算
    assert {r["revpar"] for r in overseas} >= {"50.03", "40.64"}


# ── 显式不可得矩阵 ────────────────────────────────────────────────────────
def test_unavailable_covers_five_never_disclosed_issuers():
    by_code = {r["entity_code"]: r for r in mod.UNAVAILABLE}
    for code in ("600258", "601007", "000428", "301073", "000610"):
        assert by_code[code]["periods"] == "all"
        assert by_code[code]["status"] == "unavailable"
        assert by_code[code]["reason"]
        assert by_code[code]["recovery"]


def test_unavailable_marks_jinjiang_from_2024():
    rec = {r["entity_code"]: r for r in mod.UNAVAILABLE}["600754"]
    assert rec["periods"] == ">=2024-01"


@pytest.mark.parametrize(
    "code,period,status",
    [
        ("600258", "2026-09", "unavailable"),
        ("000610", "2019-06", "unavailable"),
        ("600754", "2024-01", "unavailable"),
        ("600754", "2026-09", "unavailable"),
        ("600754", "2023-11", "query_observations"),
        ("600754", "2023-12", "query_observations"),   # 边界：仍早于 2024-01
    ],
)
def test_availability_never_returns_a_silent_empty(code, period, status):
    assert mod.availability(code, period)["status"] == status


def test_unavailable_records_point_at_the_evidence():
    for rec in mod.UNAVAILABLE:
        assert "3.5-ashare-disclosure-evidence.md" in rec["evidence"]
        assert "cn-report" in rec["recovery"]


# ── 概念表与共用写纪律 ────────────────────────────────────────────────────
def test_concepts_are_complete_and_unique():
    assert len(mod.CONCEPTS) == 3 * 2 * 3 * 2          # metric × scope × grade × caliber
    codes = [c[0] for c in mod.CONCEPTS]
    assert len(set(codes)) == len(codes)
    assert mod.CONCEPT_SET == frozenset(codes)
    for c in mod.CONCEPTS:
        assert len(c) == 9, c
        assert c[7] == "stock"
        assert c[8] == mod.SOURCE


def test_ytd_concept_carries_the_cumulative_token():
    ytd = [c for c in mod.CONCEPTS if "ytd" in c[0]]
    assert ytd
    for c in ytd:
        assert c[5] == "cumulative-ytd"      # measure
        assert c[6] == "cumulative-ytd"      # frequency


def test_common_shim_reexports_the_shared_discipline():
    from hotel_extractors import _common
    from restaurant_extractors import _common as restaurant_common

    for name in ("ensure_unit_concepts", "insert_rows", "unit_items",
                 "obs_month_of", "gate_periods", "dedup_latest"):
        assert getattr(_common, name) is getattr(restaurant_common, name), name
