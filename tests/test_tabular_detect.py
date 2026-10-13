"""格式探测矩阵测试（tabular.detect）。

重点覆盖「按扩展名会判错」的两类：HTML 伪装 .xls、zip 容器非 xlsx。
"""
from __future__ import annotations

import io
import zipfile

import pytest

from fd_industry_data.tabular import Format, detect


def _zip_of(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


class TestExcelFormats:
    def test_xlsx_detected_by_content(self):
        payload = _zip_of({
            "[Content_Types].xml": b"<Types/>",
            "xl/workbook.xml": b"<workbook/>",
            "xl/worksheets/sheet1.xml": b"<worksheet/>",
        })
        assert detect(payload) is Format.XLSX

    def test_biff_detected_by_ole2_magic(self):
        # OLE2 魔数 + 任意尾部（探测只看魔数）
        assert detect(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64) is Format.XLS

    def test_zip_without_xl_is_not_xlsx(self):
        """年鉴分册包：zip 容器但内层是 .xls，不能当 xlsx。"""
        payload = _zip_of({"2025年城乡建设统计年鉴/1-1.xls": b"\xd0\xcf\x11\xe0"})
        assert detect(payload) is Format.ZIP


class TestHtmlDisguisedAsXls:
    """老站点把 HTML 表导出为 .xls —— 扩展名骗人，必须按内容判。"""

    def test_html_table_detected(self):
        payload = b"<html><body><table><tr><td>1</td></tr></table></body></html>"
        assert detect(payload) is Format.HTML

    def test_html_with_declaration(self):
        payload = b"<!DOCTYPE html>\n<table><tr><td>x</td></tr></table>"
        assert detect(payload) is Format.HTML

    def test_html_table_far_into_document(self):
        """<table 出现在前 8KB 之后仍应识别（扫描窗口放宽）。"""
        payload = b"<!--" + b"x" * 9000 + b"--><table><tr><td>1</td></tr></table>"
        assert detect(payload) is Format.HTML

    def test_gbk_html_detected(self):
        body = "<html><table><tr><td>年份</td></tr></table></html>".encode("gb18030")
        assert detect(body) is Format.HTML


class TestCsv:
    def test_comma_csv(self):
        assert detect(b"a,b,c\n1,2,3\n") is Format.CSV

    def test_tab_tsv(self):
        assert detect(b"a\tb\n1\t2\n") is Format.CSV

    def test_single_column_is_not_csv(self):
        """单列文本不是表格（避免把普通文本误判）。"""
        assert detect(b"hello\nworld\n") is Format.TEXT

    def test_inconsistent_columns_is_not_csv(self):
        assert detect(b"a,b,c\n1,2\n") is Format.TEXT

    def test_single_row_is_not_csv(self):
        assert detect(b"a,b,c\n") is Format.TEXT


class TestEdges:
    def test_empty_is_unknown(self):
        assert detect(b"") is Format.UNKNOWN

    def test_binary_garbage_is_unknown(self):
        assert detect(b"\x00\x01\x02\x03\xff\xfe\xfd") is Format.UNKNOWN

    def test_plain_text(self):
        assert detect("普通中文说明文字".encode("utf-8")) is Format.TEXT
