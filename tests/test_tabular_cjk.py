"""CJK 表头清理测试（squash_cjk / strip_latin_runs / is_latin_heavy）。

用例取自住建部年鉴**真实单元格文本**（2026-10-13 实测），不是构造的简化样本——
中文年鉴的表头形态（对齐空格 + 中英同格 + 英文单位括号补注）是这组函数的
唯一存在理由，用真实文本才验得住。
"""
from __future__ import annotations

import pytest

from fd_industry_data.tabular import is_latin_heavy, squash_cjk, strip_latin_runs

# (原始单元格, 期望的清理结果)
REAL_CELLS = [
    # 中文名 + 英文翻译 + 英文单位补注（换行分隔）
    ("建 制 镇\n统计个数\n(万个)\nNumber of\nTowns\n\n\n(10000 units)",
     "建制镇统计个数 (万个)"),
    ("建成区面积\n\n(万公顷)\nSurface Area of\nBuild Districts\n\n\n(10000 hectare)",
     "建成区面积 (万公顷)"),
    ("建 成 区\n户籍人口\n(亿人)\nRegistered\nPermanent\nPopulation\n\n(100 million persons)",
     "建成区户籍人口 (亿人)"),
    # 英文单位括号内含换行（曾导致残留孤立括号）
    ("建 成 区\n暂住人口\n(亿人)\nTemporary \nPopulation\n\n\n(100 million\npersons)",
     "建成区暂住人口 (亿人)"),
    # 只有年份列（纯中英对照，无单位）
    ("年 份\n\n\n\nYear", "年份"),
    # 纯中文列名（无英文）
    ("供水普及率 （%）", "供水普及率 （%）"),
    # 英文与中文单位括号**紧贴**（无空格）
    ("建成区户籍人口(亿人)RegisteredPermane", "建成区户籍人口(亿人)"),
    # 英文被截断（源里实际存在未闭合括号）
    ("每 万 人       拥有公厕          （座）Number of Public Lavatories (unit",
     "每万人拥有公厕 （座）"),
    # 英文在中文之后但同列无单位
    ("非农人口\n\nNonagriculture\nPopulation", "非农人口"),
    # 中英混排且中文单位在**中间**（列名继续在单位之后）
    ("人均道路     面        积       （平方米）Road Surface Area Per Capita",
     "人均道路面积 （平方米）"),
]


class TestRealYearbookCells:
    @pytest.mark.parametrize("raw,expected", REAL_CELLS)
    def test_cleaned(self, raw, expected):
        assert squash_cjk(strip_latin_runs(raw)) == expected

    @pytest.mark.parametrize("raw,expected", REAL_CELLS)
    def test_chinese_unit_preserved(self, raw, expected):
        """中文单位括号绝不能被当成英文补注删掉。"""
        out = squash_cjk(strip_latin_runs(raw))
        if "（" in expected or "(" in expected:
            assert ")" in out or "）" in out

    @pytest.mark.parametrize("raw,_", REAL_CELLS)
    def test_no_stray_parens(self, raw, _):
        """剥离后不应留下孤立/未配对的括号（合法的中文单位括号除外）。"""
        out = squash_cjk(strip_latin_runs(raw))
        assert "()" not in out and "（）" not in out
        # 半角与全角括号都必须配对
        assert out.count("(") == out.count(")")
        assert out.count("（") == out.count("）")


class TestSquashCjk:
    def test_cjk_padding_removed(self):
        assert squash_cjk("供    水            普及率 （%）") == "供水普及率 （%）"

    def test_cjk_newline_removed(self):
        assert squash_cjk("年 份\n\n\nYear") == "年份 Year"

    def test_space_between_cjk_and_latin_kept(self):
        """中英之间的空格是语义分隔，不能删。"""
        assert squash_cjk("年份 Year") == "年份 Year"

    def test_fullwidth_space_treated_as_padding(self):
        assert squash_cjk("供\u3000水") == "供水"

    def test_empty(self):
        assert squash_cjk("") == ""


class TestIsLatinHeavy:
    def test_pure_english(self):
        assert is_latin_heavy("Water Coverage Rate") is True

    def test_pure_chinese(self):
        assert is_latin_heavy("供水普及率") is False

    def test_mixed_is_not_latin_heavy(self):
        """含中文就不算「英文行」——混合格由 strip_latin_runs 处理。"""
        assert is_latin_heavy("供水普及率 （%） Water Coverage Rate") is False

    def test_english_with_unit(self):
        assert is_latin_heavy("Number of Public Lavatories (unit)") is True

    def test_empty_is_not(self):
        assert is_latin_heavy("") is False

    def test_numeric_not_latin_heavy(self):
        assert is_latin_heavy("12345") is False


class TestStripLatinRuns:
    def test_pure_english_untouched(self):
        """纯英文单元格不剥离（有些源表头本就是英文）。"""
        assert strip_latin_runs("Water Coverage Rate") == "Water Coverage Rate"

    def test_pure_chinese_untouched(self):
        assert strip_latin_runs("供水普及率") == "供水普及率"

    def test_mixed_strips_latin(self):
        out = strip_latin_runs("供水普及率 Water Coverage Rate")
        assert "Water" not in out
        assert "供水普及率" in out
