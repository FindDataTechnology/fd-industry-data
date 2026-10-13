"""HTML 表格解析测试（含 rowspan/colspan 展开）。

重点：不展开合并单元格会让后续行整体左移——按列取数就全错。
"""
from __future__ import annotations

import pytest

from fd_industry_data.tabular import TabularError, detect, read_grid
from fd_industry_data.tabular import Format
from fd_industry_data.tabular.htmltable import HtmlTableError, table_count


def _html(inner: str) -> bytes:
    return f"<html><body>{inner}</body></html>".encode("utf-8")


class TestBasic:
    def test_simple_table(self):
        payload = _html("<table><tr><td>a</td><td>b</td></tr><tr><td>1</td><td>2</td></tr></table>")
        assert read_grid(payload) == [["a", "b"], ["1", "2"]]

    def test_th_treated_as_cell(self):
        payload = _html("<table><tr><th>年份</th><th>值</th></tr><tr><td>2020</td><td>1</td></tr></table>")
        assert read_grid(payload) == [["年份", "值"], ["2020", "1"]]

    def test_whitespace_squashed(self):
        payload = _html("<table><tr><td>  多   空格\n 值 </td></tr></table>")
        assert read_grid(payload) == [["多 空格 值"]]

    def test_nbsp_squashed(self):
        payload = _html("<table><tr><td>a&nbsp;&nbsp;b</td></tr></table>")
        assert read_grid(payload) == [["a b"]]

    def test_no_table_raises(self):
        with pytest.raises(TabularError):
            read_grid(_html("<p>no table here</p>"))

    def test_table_count(self):
        payload = _html("<table><tr><td>1</td></tr></table><table><tr><td>2</td></tr></table>")
        assert table_count(payload) == 2

    def test_select_table_by_index(self):
        payload = _html("<table><tr><td>first</td></tr></table><table><tr><td>second</td></tr></table>")
        assert read_grid(payload, table=1) == [["second"]]

    def test_table_index_out_of_range(self):
        payload = _html("<table><tr><td>only</td></tr></table>")
        with pytest.raises(TabularError):
            read_grid(payload, table=5)


class TestColspan:
    def test_colspan_repeats_value(self):
        payload = _html(
            "<table><tr><td colspan='2'>合并</td><td>x</td></tr>"
            "<tr><td>1</td><td>2</td><td>3</td></tr></table>"
        )
        assert read_grid(payload) == [["合并", "合并", "x"], ["1", "2", "3"]]

    def test_colspan_keeps_row_width(self):
        payload = _html(
            "<table><tr><td colspan='3'>标题</td></tr>"
            "<tr><td>a</td><td>b</td><td>c</td></tr></table>"
        )
        grid = read_grid(payload)
        assert len(grid[0]) == 3
        assert len(grid[1]) == 3


class TestRowspan:
    def test_rowspan_repeats_in_following_rows(self):
        payload = _html(
            "<table><tr><td rowspan='2'>跨行</td><td>a</td></tr>"
            "<tr><td>b</td></tr></table>"
        )
        assert read_grid(payload) == [["跨行", "a"], ["跨行", "b"]]

    def test_rowspan_three_rows(self):
        payload = _html(
            "<table><tr><td rowspan='3'>x</td><td>1</td></tr>"
            "<tr><td>2</td></tr><tr><td>3</td></tr></table>"
        )
        assert read_grid(payload) == [["x", "1"], ["x", "2"], ["x", "3"]]

    def test_rowspan_does_not_shift_following_cells(self):
        """跨行占用后，本行后续单元格不能左移。"""
        payload = _html(
            "<table><tr><td rowspan='2'>A</td><td>B</td><td>C</td></tr>"
            "<tr><td>D</td><td>E</td></tr></table>"
        )
        grid = read_grid(payload)
        assert grid[1] == ["A", "D", "E"]


class TestMixedSpan:
    def test_rowspan_and_colspan_together(self):
        payload = _html(
            "<table>"
            "<tr><td rowspan='2'>角</td><td colspan='2'>横</td></tr>"
            "<tr><td>1</td><td>2</td></tr>"
            "<tr><td>x</td><td>y</td><td>z</td></tr>"
            "</table>"
        )
        grid = read_grid(payload)
        # 每行列数一致
        widths = {len(r) for r in grid}
        assert len(widths) == 1
        assert grid[1][0] == "角"   # rowspan 延续
        assert grid[0][1] == "横" and grid[0][2] == "横"  # colspan 复制

    def test_ragged_table_padded_to_uniform_width(self):
        payload = _html(
            "<table><tr><td>a</td><td>b</td><td>c</td></tr>"
            "<tr><td>1</td></tr></table>"
        )
        grid = read_grid(payload)
        assert all(len(r) == 3 for r in grid)
        assert grid[1] == ["1", "", ""]


class TestDisguisedAsXls:
    """老站点把 HTML 表导出为 .xls —— 走同一路径解析。"""

    def test_detected_as_html_and_parsed(self):
        payload = (
            '<html xmlns:x="urn:schemas-microsoft-com:office:excel">'
            "<body><table><tr><td>年份</td><td>供水</td></tr>"
            "<tr><td>2020</td><td>629</td></tr></table></body></html>"
        ).encode("utf-8")
        assert detect(payload) is Format.HTML
        assert read_grid(payload) == [["年份", "供水"], ["2020", "629"]]

    def test_gbk_encoded_table(self):
        payload = "<table><tr><td>年份</td><td>值</td></tr></table>".encode("gb18030")
        assert read_grid(payload) == [["年份", "值"]]
