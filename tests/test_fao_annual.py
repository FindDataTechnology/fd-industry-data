"""fao-food-price 年度 XLSX 路径测试（2026-10-13 新增 scope="annual"）。

用**自造的最小工作簿字节**测解析逻辑（不触网），覆盖实测踩到的坑：
首格标 `Month` 实为年份、列号须按 Nominal/Real 实读、空值不造数。
"""
from __future__ import annotations

import io
import zipfile

import pytest

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _workbook(rows_xml: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr(
            "xl/workbook.xml",
            '<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="FFPI_Historical" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        z.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{NS}"><sheetData>{rows_xml}</sheetData></worksheet>',
        )
    return buf.getvalue()


def _row(idx: int, cells: list[str]) -> str:
    cs = "".join(
        f'<c r="{chr(65 + i)}{idx}" t="inlineStr"><is><t>{v}</t></is></c>'
        for i, v in enumerate(cells)
    )
    return f'<row r="{idx}">{cs}</row>'


@pytest.fixture()
def annual_bytes() -> bytes:
    return _workbook(
        _row(1, ["", "FAO Food Price Index", ""])
        + _row(2, ["", "2014-2016=100", ""])
        + _row(3, ["Month", "Nominal", "Real"])
        + _row(4, ["", "", ""])
        + _row(5, ["1961", "20.351527128964303", "104.33692291961376"])
        + _row(6, ["1962", "20.096067272996237", "101.13998732334912"])
    )


class TestAnnualParse:
    def test_parses_both_series(self, annual_bytes):
        from fd_industry_data.loader import load_spider_module

        m = load_spider_module("fao-food-price", "spiders")
        out = m._parse_annual_xlsx(annual_bytes)
        assert ("1961", "Food Price Index (nominal)", 20.351527128964303) in out
        assert ("1961", "Food Price Index (real)", 104.33692291961376) in out
        assert len(out) == 4  # 2 年 × 2 序列

    def test_year_column_not_month_label(self, annual_bytes):
        """首格标 Month 但值是年份——必须按 4 位数字收，不按表头文字。"""
        from fd_industry_data.loader import load_spider_module

        m = load_spider_module("fao-food-price", "spiders")
        out = m._parse_annual_xlsx(annual_bytes)
        assert all(len(y) == 4 and y.isdigit() for y, _, _ in out)

    def test_missing_value_skipped_not_zero_filled(self):
        """空值格跳过，绝不造 0。"""
        from fd_industry_data.loader import load_spider_module

        m = load_spider_module("fao-food-price", "spiders")
        data = _workbook(
            _row(1, ["Month", "Nominal", "Real"])
            + _row(2, ["1961", "20.35", ""])
            + _row(3, ["1962", "", "101.13"])
        )
        out = m._parse_annual_xlsx(data)
        assert ("1961", "Food Price Index (nominal)", 20.35) in out
        assert not any(y == "1961" and "real" in n for y, n, _ in out)
        assert ("1962", "Food Price Index (real)", 101.13) in out
        assert not any(y == "1962" and "nominal" in n for y, n, _ in out)

    def test_columns_read_by_name_not_position(self):
        """列序颠倒也应正确（按表头名取列号，不写死）。"""
        from fd_industry_data.loader import load_spider_module

        m = load_spider_module("fao-food-price", "spiders")
        data = _workbook(
            _row(1, ["Month", "Real", "Nominal"])
            + _row(2, ["1961", "104.33", "20.35"])
        )
        out = m._parse_annual_xlsx(data)
        assert ("1961", "Food Price Index (nominal)", 20.35) in out
        assert ("1961", "Food Price Index (real)", 104.33) in out

    def test_header_missing_raises(self):
        from fd_industry_data.loader import load_spider_module

        m = load_spider_module("fao-food-price", "spiders")
        data = _workbook(_row(1, ["Year", "A", "B"]) + _row(2, ["1961", "1", "2"]))
        with pytest.raises(RuntimeError):
            m._parse_annual_xlsx(data)


class TestScopeContract:
    def test_bad_scope_raises(self):
        from fd_industry_data.loader import load_spider_module

        m = load_spider_module("fao-food-price", "spiders")
        with pytest.raises(ValueError):
            m.run_fao_food_price(limit=1, scope="weekly")

    def test_zero_limit_short_circuits(self):
        from fd_industry_data.loader import load_spider_module

        m = load_spider_module("fao-food-price", "spiders")
        assert m.run_fao_food_price(limit=0, scope="annual") == []
