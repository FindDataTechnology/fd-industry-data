"""xlsx / xls 读取测试（tabular 的两种 Excel 路径）。

xlsx 用例自造最小工作簿字节（不依赖外部样本）；xls 用例用 xlrd 生成器不可用，
故走「真实文件若存在则测、否则跳过」+ 纯逻辑单测（日期格式化）。
"""
from __future__ import annotations

import io
import zipfile

import pytest

from fd_industry_data.tabular import Format, TabularError, detect, read_grid, sheets
from fd_industry_data.tabular.xls import XlsError, _fmt_date
from fd_industry_data.tabular.xlsx import XlsxError

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _workbook_bytes(
    sheets_xml: dict[str, str],
    shared: list[str] | None = None,
) -> bytes:
    """构造最小 OOXML 工作簿字节。

    ``sheets_xml``: 表名 → ``<sheetData>`` 内的行 XML。
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        sheet_tags = "".join(
            f'<sheet name="{name}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
            for i, name in enumerate(sheets_xml)
        )
        z.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{sheet_tags}</sheets></workbook>",
        )
        rels = "".join(
            f'<Relationship Id="rId{i + 1}" '
            f'Target="worksheets/sheet{i + 1}.xml"/>'
            for i in range(len(sheets_xml))
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{rels}</Relationships>",
        )
        if shared is not None:
            items = "".join(f"<si><t>{s}</t></si>" for s in shared)
            z.writestr(
                "xl/sharedStrings.xml",
                f'<sst xmlns="{NS}">{items}</sst>',
            )
        for i, rows_xml in enumerate(sheets_xml.values()):
            z.writestr(
                f"xl/worksheets/sheet{i + 1}.xml",
                f'<worksheet xmlns="{NS}"><sheetData>{rows_xml}</sheetData></worksheet>',
            )
    return buf.getvalue()


class TestXlsxSharedStrings:
    def test_shared_string_cells(self):
        payload = _workbook_bytes(
            {"Sheet1": '<row r="1"><c r="A1" t="s"><v>0</v></c>'
                       '<c r="B1" t="s"><v>1</v></c></row>'},
            shared=["年份", "供水"],
        )
        assert read_grid(payload) == [["年份", "供水"]]

    def test_inline_string_cells(self):
        """inlineStr 是既有 pboc 手写实现缺失的形态。"""
        payload = _workbook_bytes(
            {"Sheet1": '<row r="1"><c r="A1" t="inlineStr"><is><t>内联文本</t></is></c></row>'},
        )
        assert read_grid(payload) == [["内联文本"]]

    def test_rich_text_in_shared_string_concatenated(self):
        """富文本把文本切在多个 <t> 里，必须拼接。"""
        payload = _workbook_bytes(
            {"Sheet1": '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'},
            shared=[],
        )
        # 手工替换 sharedStrings 为富文本
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            names = z.namelist()
            with zipfile.ZipFile(buf, "w") as out:
                for n in names:
                    if n == "xl/sharedStrings.xml":
                        out.writestr(
                            n,
                            f'<sst xmlns="{NS}"><si><r><t>前</t></r><r><t>后</t></r></si></sst>',
                        )
                    else:
                        out.writestr(n, z.read(n))
        assert read_grid(buf.getvalue()) == [["前后"]]

    def test_number_cell(self):
        payload = _workbook_bytes(
            {"Sheet1": '<row r="1"><c r="A1"><v>2025</v></c></row>'},
        )
        assert read_grid(payload) == [["2025"]]


class TestXlsxColumnAlignment:
    """Excel 会省略空单元格，不按 r 引用补齐就会错列。"""

    def test_skipped_empty_cell_does_not_shift(self):
        # A1 有值、B1 省略、C1 有值 → 必须得到 ['x', '', 'y']
        payload = _workbook_bytes(
            {"Sheet1": '<row r="1"><c r="A1" t="inlineStr"><is><t>x</t></is></c>'
                       '<c r="C1" t="inlineStr"><is><t>y</t></is></c></row>'},
        )
        assert read_grid(payload) == [["x", "", "y"]]

    def test_leading_gap(self):
        payload = _workbook_bytes(
            {"Sheet1": '<row r="1"><c r="C1" t="inlineStr"><is><t>z</t></is></c></row>'},
        )
        assert read_grid(payload) == [["", "", "z"]]

    def test_multi_row_grid(self):
        payload = _workbook_bytes(
            {"Sheet1": '<row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row>'
                       '<row r="2"><c r="A2" t="inlineStr"><is><t>b</t></is></c></row>'},
        )
        assert read_grid(payload) == [["a"], ["b"]]


class TestXlsxMultiSheet:
    def test_sheets_listed_in_workbook_order(self):
        payload = _workbook_bytes({
            "1-1": '<row r="1"><c r="A1" t="inlineStr"><is><t>甲</t></is></c></row>',
            "1-2": '<row r="1"><c r="A1" t="inlineStr"><is><t>乙</t></is></c></row>',
        })
        assert sheets(payload) == ["1-1", "1-2"]

    def test_select_by_name(self):
        payload = _workbook_bytes({
            "1-1": '<row r="1"><c r="A1" t="inlineStr"><is><t>甲</t></is></c></row>',
            "1-2": '<row r="1"><c r="A1" t="inlineStr"><is><t>乙</t></is></c></row>',
        })
        assert read_grid(payload, sheet="1-2") == [["乙"]]

    def test_select_by_index(self):
        payload = _workbook_bytes({
            "1-1": '<row r="1"><c r="A1" t="inlineStr"><is><t>甲</t></is></c></row>',
            "1-2": '<row r="1"><c r="A1" t="inlineStr"><is><t>乙</t></is></c></row>',
        })
        assert read_grid(payload, sheet=1) == [["乙"]]

    def test_unknown_sheet_raises(self):
        payload = _workbook_bytes(
            {"S": '<row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row>'},
        )
        with pytest.raises(TabularError):
            read_grid(payload, sheet="nope")


class TestXlsDateFormatting:
    """日期单元格不能落成裸浮点数（xlrd 返回序列号）。"""

    def test_integer_serial_is_date(self):
        # Excel 序列号 45000 ≈ 2023-03-15（datemode=0）
        assert _fmt_date(45000, 0) == "2023-03-15"

    def test_serial_with_time(self):
        out = _fmt_date(45000.5, 0)
        assert out.startswith("2023-03-15 ")
        assert "12:00:00" in out

    def test_out_of_range_returns_string(self):
        assert _fmt_date(-1e9, 0) == str(-1e9)

    def test_xls_rejects_non_ole2(self):
        with pytest.raises(XlsError):
            from fd_industry_data.tabular.xls import read_grid as xls_read

            xls_read(b"not an ole2 payload")


class TestCrossFormat:
    def test_detect_and_read_agree_on_xlsx(self):
        payload = _workbook_bytes(
            {"S": '<row r="1"><c r="A1" t="inlineStr"><is><t>ok</t></is></c></row>'},
        )
        assert detect(payload) is Format.XLSX
        assert read_grid(payload) == [["ok"]]

    def test_zip_container_points_to_unzip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("a.xls", b"\xd0\xcf\x11\xe0")
        with pytest.raises(TabularError) as ei:
            read_grid(buf.getvalue())
        assert "unzip" in str(ei.value)

    def test_unsupported_format_message(self):
        with pytest.raises(TabularError):
            read_grid(b"\x00\x01\x02\xff\xfe")
