"""中国饭店协会年度报告单元离线回归（add-hotel-vertical · 波1 · 任务 2.4）。

fixture 是两份真 PDF 的**逐字** ``pdftotext -layout -enc UTF-8`` 文本层
（``ctha_hotel2026.txt.gz`` / ``ctha_group2026.txt.gz``）；PDF 本体 32 MB，不入仓。
测试不联网：``_get`` 与 ``fd_industry_data.pdftext`` 全被替身接管。
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 每个 spider 测试用**唯一模块名**加载：多个测试同进程跑时 `import spider` 会互相顶掉 sys.modules["spider"]
_MODULE = "spider_ctha_hotel_report"
_spec = importlib.util.spec_from_file_location(
    _MODULE, ROOT / "spiders" / "ctha-hotel-report" / "spider.py")
lane = importlib.util.module_from_spec(_spec)
sys.modules[_MODULE] = lane
_spec.loader.exec_module(lane)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ctha"
HOTEL_PDF = "https://www.chinahotel.org.cn/sfs/cms/81683fdd45183df4d00c6bca03f23823.pdf"
GROUP_PDF = "https://www.chinahotel.org.cn/sfs/cms/826afcda742b568782ace069aed2202a.pdf"
REPORTS = (f"《2026年中国酒店业发展报告》={HOTEL_PDF},"
           f"《2026中国酒店集团及品牌发展报告》={GROUP_PDF}")


def _text(name: str) -> str:
    with gzip.open(FIXTURES / f"ctha_{name}.txt.gz", "rb") as handle:
        return handle.read().decode("utf-8", "replace")


def _pages(name: str) -> list[str]:
    return lane._pages(_text(name))


def _metric(rows, **kw):
    out = [r for r in rows if r["row_kind"] == "metric"]
    for key, value in kw.items():
        out = [r for r in out if r[key] == value]
    return out


def _one(rows, **kw):
    hits = _metric(rows, **kw)
    assert len(hits) == 1, f"{kw} 命中 {len(hits)} 行：{[h['dimension'] for h in hits]}"
    return hits[0]


def _value(rows, **kw):
    return _one(rows, **kw)["value"]


# ------------------------------------------------------------------ 文本层

def test_pages_split_on_form_feed():
    pages = _pages("hotel2026")
    assert len(pages) == 33                      # 32 页 + 末尾空段
    assert pages[-1].strip() == ""


def test_period_is_the_body_year_not_the_cover_year():
    assert lane._period(_pages("hotel2026")) == 2025
    assert lane._period(_pages("group2026")) == 2025


def test_period_missing_returns_none():
    assert lane._period(["没有年份的页"]) is None


def test_num_strips_thousands_separators():
    assert lane._num("1,368,057") == 1368057.0
    assert lane._num("593,086") == 593086.0
    assert lane._num("－") is None


def test_flat_kills_all_whitespace_including_ideographic_space():
    flat = lane._flat("门店连锁化率\n达28.37%\u3000 以及\xa0全角空格")
    assert flat == "门店连锁化率达28.37%以及全角空格"


# ------------------------------------------------------- 行业面（hotel2026）

def test_hotel_report_scale_anchors():
    rows, _ = lane.parse_report(_pages("hotel2026"), "hotel2026")
    assert _value(rows, table="industry_scale", metric="lodging_facilities") == 593086.0
    assert _value(rows, table="industry_scale", metric="lodging_rooms") == 20514799.0
    assert _value(rows, table="industry_scale", metric="hotel_facilities") == 374694.0
    assert _value(rows, table="industry_scale", metric="hotel_rooms") == 18736357.0
    assert _value(rows, table="industry_scale", metric="other_facilities") == 218392.0


def test_hotel_report_structure_tables_are_complete():
    rows, _ = lane.parse_report(_pages("hotel2026"), "hotel2026")
    for table, dims in (("grade_structure", lane.GRADES),
                        ("size_structure", lane.SIZES),
                        ("city_tier", lane.TIERS)):
        assert {r["dimension"] for r in _metric(rows, table=table)} == set(dims)
    assert len(_metric(rows, table="grade_structure")) == 16   # 4 档 × (设施/占比/客房/占比)
    assert len(_metric(rows, table="size_structure")) == 12    # 4 档 × (设施/设施占比/客房占比) —— 原文无细分档的绝对客房数
    assert len(_metric(rows, table="city_tier")) == 12         # 3 类 × (设施/占比/客房/占比)
    assert _value(rows, table="grade_structure", dimension="经济型",
                  metric="share_facilities") == 77.99
    assert _value(rows, table="size_structure", dimension="150间及以上",
                  metric="share_rooms") == 18.62
    assert _value(rows, table="city_tier", dimension="其它城市", metric="rooms") == 1142.2


def test_hotel_report_province_and_city_take_extremes_only():
    rows, _ = lane.parse_report(_pages("hotel2026"), "hotel2026")
    assert _value(rows, table="province", dimension="广东", metric="facilities") == 40449.0
    assert _value(rows, table="province", dimension="天津", metric="facilities") == 2630.0
    assert _value(rows, table="province", dimension="宁夏", metric="facilities") == 1967.0
    assert _value(rows, table="province", dimension="广东", metric="rooms") == 212.0
    assert _value(rows, table="city", dimension="重庆", metric="facilities") == 10158.0
    assert _value(rows, table="city", dimension="前十城", metric="share_rooms") == 20.88


def test_hotel_report_has_no_ranking_rows():
    rows, traces = lane.parse_report(_pages("hotel2026"), "hotel2026")
    assert [r for r in rows if r["row_kind"] == "ranking"] == []
    # 两份 PDF 各管各的面：hotel 报告不该出现「未发现表标题」这种跨报告的噪声留痕
    assert not any("未发现表标题" in t for t in traces)


def test_hotel_report_summary_page_is_skipped():
    rows, traces = lane.parse_report(_pages("hotel2026"), "hotel2026")
    assert any("跳过概要页" in t for t in traces)
    assert len(_metric(rows)) == 61


# ------------------------------------------------------- 企业面（group2026）

def test_group_report_chain_and_group_scale():
    rows, _ = lane.parse_report(_pages("group2026"), "group2026")
    assert _value(rows, metric="chain_hotels") == 10.63
    assert _value(rows, metric="chain_rooms") == 783.18
    assert _value(rows, metric="chain_rooms_delta") == 75.94
    assert _value(rows, metric="chain_rooms_yoy") == 10.74
    assert _value(rows, metric="chain_rate_rooms") == 41.8
    assert _value(rows, metric="chain_rate_hotels") == 28.37
    assert _value(rows, dimension="前50集团", metric="rooms") == 594.0
    assert _value(rows, dimension="前10集团", metric="rooms") == 465.0
    assert _value(rows, dimension="前10集团", metric="share_of_chain") == 59.38


def test_group_report_four_brand_tiers():
    rows, _ = lane.parse_report(_pages("group2026"), "group2026")
    assert {r["dimension"] for r in _metric(rows, table="brand_tier")} == {"豪华", "高档", "中档", "经济型"}
    assert _value(rows, dimension="豪华", metric="rooms") == 854039.0
    assert _value(rows, dimension="高档", metric="rooms_yoy") == 14.96
    assert _value(rows, dimension="经济型", metric="rooms") == 3148585.0


def test_group_report_rollups_only_for_wrapped_tables():
    rows, _ = lane.parse_report(_pages("group2026"), "group2026")
    assert _value(rows, table="brand_foreign_top30", metric="rooms_total") == 789099.0
    assert _value(rows, table="brand_luxury_top20", metric="rooms_total") == 287557.0


def test_group_top50_table_is_fully_extracted():
    rows, traces = lane.parse_report(_pages("group2026"), "group2026")
    top = [r for r in rows if r["table"] == "group_top50"]
    assert len(top) == 50
    assert [r["rank"] for r in top] == list(range(1, 51))
    assert (top[0]["entity_name"], top[0]["entity_hq"], top[0]["rooms"], top[0]["hotels"]) == (
        "上海锦江国际酒店股份有限公司", "上海", 1368057, 14132)
    assert (top[1]["entity_name"], top[1]["rooms"]) == ("华住集团", 1264419)
    assert top[2]["entity_name"] == "首旅如家酒店集团"
    assert any("group_top50: 50 行全取" in t for t in traces)


def test_group_top50_spans_two_pages():
    rows, _ = lane.parse_report(_pages("group2026"), "group2026")
    top = {r["rank"]: r for r in rows if r["table"] == "group_top50"}
    assert top[50]["page"] > top[1]["page"]


def test_brand_foreign_top30_does_not_absorb_other_tables():
    """豪华/高端/中端/经济型品牌榜的序号也是 1..30，绝不能被当成本表的续页。"""
    rows, traces = lane.parse_report(_pages("group2026"), "group2026")
    top = [r for r in rows if r["table"] == "brand_foreign_top30" and r["row_kind"] == "ranking"]
    assert len(top) == 30
    assert [r["rank"] for r in top] == list(range(1, 31))
    assert top[0]["entity_group"], "外资品牌榜要带所属集团"
    assert all(r["entity_group"] for r in top)
    assert any("brand_foreign_top30: 30 行全取" in t for t in traces)


def test_ranking_rows_carry_empty_metric_and_value():
    rows, _ = lane.parse_report(_pages("group2026"), "group2026")
    for row in (r for r in rows if r["row_kind"] == "ranking"):
        assert row["metric"] == "" and row["value"] is None and row["unit"] == ""
        assert row["rank"] is not None and row["rooms"] and row["hotels"]


# ------------------------------------------------------------- 取数纪律

_SYNTH_HEAD = ("中国前 50 家酒店集团规模\n"
               "序号 集团名称 GROUP 总部所在地 客房数 ROOMS 门店数 HOTELS\n"
               " 1       上海锦江国际酒店股份有限公司            上海         1368057   14132\n"
               " 2       华住集团                                  上海         1264419   12858\n")


def test_partial_ordinal_set_skips_the_whole_table():
    """序号 1..N 不全 = 单元格折行，列归属不可复原 → 整表不取，绝不取半张。"""
    rows, traces = lane.parse_rankings([_SYNTH_HEAD], "group2026")
    assert [r for r in rows if r["table"] == "group_top50"] == []
    assert any("单元格折行" in t and "2/50" in t for t in traces)


def test_continuation_page_with_overlapping_ranks_is_not_absorbed():
    cont = ("序号 品牌名称 BRAND 所属集团 GROUP 客房数 ROOMS 门店数 HOTELS\n"
            " 1       凤凰酒店                    凤悦酒店及度假村      38420     81\n"
            " 2       君澜                        君亭酒店集团          36036    143\n")
    rows, traces = lane.parse_rankings([_SYNTH_HEAD, cont], "group2026")
    assert [r for r in rows if r["table"] == "group_top50"] == []   # 仍缺 3..50
    assert not any("brand_foreign_top30: 3" in t for t in traces)


def test_rank_row_regex_requires_two_column_gaps():
    assert lane.RANK_ROW_RE.match(" 1  甲  乙  100  5")
    assert not lane.RANK_ROW_RE.match("锦江国际酒")            # 折行残句
    assert not lane.RANK_ROW_RE.match("  1 上海锦江国际 上海 1368057")   # 缺门店数


def test_required_anchor_missing_is_fatal():
    with pytest.raises(lane.UnmappedPhrase):
        lane.parse_metrics(["什么都没有"], "hotel2026")


def test_optional_anchor_missing_is_not_fatal():
    """可选锚定句（客房连锁化率那一句，在 P29）不在页集里时，只丢该组行，不中止整份报告。"""
    rows, _ = lane.parse_metrics([_pages("group2026")[6]], "group2026")
    assert rows, "必需锚定句齐全的 P7 应当产出行"
    assert all(r["metric"] not in {"chain_rate_rooms", "chain_rate_hotels"} for r in rows)


# ------------------------------------------------------------- 参数与入口

def test_reports_param_accepts_title_pairs():
    targets = lane._reports_from_param(REPORTS)
    assert targets == [("《2026年中国酒店业发展报告》", HOTEL_PDF),
                       ("《2026中国酒店集团及品牌发展报告》", GROUP_PDF)]


def test_reports_param_accepts_bare_urls():
    targets = lane._reports_from_param(HOTEL_PDF)
    assert len(targets) == 1 and targets[0][1] == HOTEL_PDF


def test_reports_param_empty_means_discover():
    assert lane._reports_from_param(None) == []
    assert lane._reports_from_param("  ") == []


def test_report_name_routes_by_title_hint():
    assert lane._report_name("《2026中国酒店集团及品牌发展报告》") == "group2026"
    assert lane._report_name("《2026年中国酒店业发展报告》") == "hotel2026"


def _fake_pdftext(monkeypatch, name: str):
    """把真件文本层伪装成 PDF 载荷：is_pdf 恒真，extract_text 返回 fixture 文本。"""
    from fd_industry_data import pdftext

    monkeypatch.setattr(pdftext, "is_pdf", lambda payload: payload.startswith(b"%PDF"))
    monkeypatch.setattr(pdftext, "extract_text", lambda payload, **kw: _text(name))


def test_full_run_offline(monkeypatch):
    payloads = {HOTEL_PDF: b"%PDF-hotel", GROUP_PDF: b"%PDF-group"}
    monkeypatch.setattr(lane, "_get", lambda url, timeout=0: payloads[url])
    from fd_industry_data import pdftext
    monkeypatch.setattr(pdftext, "is_pdf", lambda payload: payload.startswith(b"%PDF"))
    monkeypatch.setattr(pdftext, "extract_text",
                        lambda payload, **kw: _text("hotel2026" if payload == b"%PDF-hotel" else "group2026"))

    rows = lane.run_ctha_hotel_report(limit=1200, reports=REPORTS)
    assert len(rows) == 168
    assert {r["report"] for r in rows} == {"hotel2026", "group2026"}
    assert {r["report_period"] for r in rows} == {2025}
    assert sum(1 for r in rows if r["row_kind"] == "metric") == 88
    assert sum(1 for r in rows if r["row_kind"] == "ranking") == 80
    assert all(r["source"] == "ctha-hotel-report" and r["scraped_at"] for r in rows)
    assert {r["pdf_url"] for r in rows} == {HOTEL_PDF, GROUP_PDF}
    # metric 在前、ranking 在后；各自按 (report, table, …) 有序
    kinds = [r["row_kind"] for r in rows]
    assert kinds == sorted(kinds, key=lambda k: k != "metric")


def test_full_run_respects_limit(monkeypatch):
    monkeypatch.setattr(lane, "_get", lambda url, timeout=0: b"%PDF")
    from fd_industry_data import pdftext
    monkeypatch.setattr(pdftext, "is_pdf", lambda payload: True)
    monkeypatch.setattr(pdftext, "extract_text", lambda payload, **kw: _text("group2026"))

    rows = lane.run_ctha_hotel_report(limit=5, reports=f"《2026中国酒店集团及品牌发展报告》={GROUP_PDF}")
    assert len(rows) == 5


def test_full_run_rejects_bad_pages():
    with pytest.raises(ValueError):
        lane.run_ctha_hotel_report(pages=99)


def test_gated_index_without_reports_says_what_to_do(monkeypatch):
    def boom(url, timeout=0):
        raise lane.LoginWall(f"{url} → https://www.chinahotel.org.cn/account/login")
    monkeypatch.setattr(lane, "_get", boom)
    with pytest.raises(RuntimeError) as err:
        lane.run_ctha_hotel_report()
    assert "登录墙" in str(err.value) and "reports" in str(err.value)


def test_non_pdf_payload_is_traced_not_fatal(monkeypatch):
    monkeypatch.setattr(lane, "_get", lambda url, timeout=0: b"<html>nope</html>")
    from fd_industry_data import pdftext
    monkeypatch.setattr(pdftext, "is_pdf", lambda payload: False)
    with pytest.raises(RuntimeError) as err:
        lane.run_ctha_hotel_report(reports=f"《2026年中国酒店业发展报告》={HOTEL_PDF}")
    assert "全部报告解析失败" in str(err.value)


# --------------------------------------------------------------- golden 口径

def test_golden_sample_is_well_formed():
    path = Path(__file__).resolve().parent.parent / "spiders" / "ctha-hotel-report" / "golden" / "001-ctha-hotel-report.json"
    spec = json.loads(path.read_text(encoding="utf-8"))
    assert spec["target"] == "run_ctha_hotel_report"
    assert spec["expect"]["min_rows"] == 168
    assert HOTEL_PDF in spec["params"]["reports"] and GROUP_PDF in spec["params"]["reports"]
    for item in spec["expect"]["rows_contains"]:
        assert set(item) == {"field", "equals"}, item      # golden 只支持 {field, equals}
