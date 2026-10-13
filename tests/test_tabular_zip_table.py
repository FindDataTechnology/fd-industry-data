"""ZIP 文件名编码回退 + Table 表头探测测试。

ZIP 用例自造（不设 UTF-8 标志位的 GBK 名）；Table 用例覆盖政府表格的
「前置元信息行 + 多行表头」形态。
"""
from __future__ import annotations

import io
import zipfile

import pytest

from fd_industry_data.tabular import HeaderNotFound, Table, ZipError, unzip
from fd_industry_data.tabular.ziptool import _decode_name


def _zip_gbk_names(names: list[str], payload: bytes = b"x") -> bytes:
    """构造**不设 UTF-8 标志**、文件名为 GBK 字节的 zip（模拟老打包工具）。

    注意：``zipfile`` 对非 ASCII 名会**强制**置 UTF-8 标志
    （见 ``ZipInfo._encodeFilenameFlags``），所以必须打补丁才能造出这种
    「GBK 名 + 无标志」的历史形态——真实老站点正是这么打包的。
    """
    buf = io.BytesIO()
    original = zipfile.ZipInfo._encodeFilenameFlags

    def _patched(self):
        return self.filename.encode("cp437"), self.flag_bits & ~0x800

    zipfile.ZipInfo._encodeFilenameFlags = _patched
    try:
        with zipfile.ZipFile(buf, "w") as z:
            for name in names:
                info = zipfile.ZipInfo(name.encode("gbk").decode("cp437"))
                info.flag_bits &= ~0x800
                z.writestr(info, payload)
    finally:
        zipfile.ZipInfo._encodeFilenameFlags = original
    return buf.getvalue()


def _zip_utf8_names(names: list[str], payload: bytes = b"x") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in names:
            z.writestr(name, payload)  # zipfile 默认设 UTF-8 标志
    return buf.getvalue()


class TestZipFilenameEncoding:
    def test_gbk_names_decoded(self):
        """住建部年鉴实测形态：无 UTF-8 标志的 GBK 中文名。"""
        names = ["2025年城乡建设统计年鉴/01城市部分/1-1-1 全国历年城市供水情况.xls"]
        entries = unzip(_zip_gbk_names(names))
        assert entries[0].name == names[0]

    def test_utf8_names_untouched(self):
        names = ["目录/中文名.xls"]
        entries = unzip(_zip_utf8_names(names))
        assert entries[0].name == names[0]

    def test_ascii_names_untouched(self):
        entries = unzip(_zip_utf8_names(["a/b.xls"]))
        assert entries[0].name == "a/b.xls"

    def test_utf8_flag_wins_over_gbk(self):
        """设了 UTF-8 标志就必须按 UTF-8 解，不能强行 GBK。"""
        names = ["日本語ファイル.xls"]  # 非 GBK 可编码
        entries = unzip(_zip_utf8_names(names))
        assert entries[0].name == names[0]

    def test_directory_entries_kept(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("d/", b"")
            z.writestr("d/f.xls", b"data")
        entries = unzip(buf.getvalue())
        dirs = [e for e in entries if e.is_dir]
        files = [e for e in entries if not e.is_dir]
        assert [d.name for d in dirs] == ["d/"]
        assert [f.name for f in files] == ["d/f.xls"]

    def test_payload_preserved(self):
        entries = unzip(_zip_utf8_names(["f.xls"], payload=b"\xd0\xcf\x11\xe0abc"))
        assert entries[0].data == b"\xd0\xcf\x11\xe0abc"

    def test_non_zip_raises(self):
        with pytest.raises(ZipError):
            unzip(b"not a zip at all")

    def test_raw_name_retained_for_tracing(self):
        names = ["中文.xls"]
        entries = unzip(_zip_gbk_names(names))
        assert entries[0].raw_name != entries[0].name  # 原始（乱码）名可留痕


class TestTableHeader:
    def test_header_after_metadata_rows(self):
        """政府表格常态：前 3 行标题/英文名/单位，第 4 行才是真表头。"""
        rows = [
            ["1-1-1  全国历年城市供水情况"],
            ["National Urban Water Supply"],
            [""],
            ["年份", "供水总量", "用水人口"],
            ["2020", "629", "3.5"],
            ["2021", "673", "3.6"],
        ]
        t = Table(rows)
        mapping = t.find_header(["年份", "供水总量"])
        assert mapping["年份"] == 0
        assert mapping["供水总量"] == 1
        assert t.header_row == 3

    def test_records_start_after_header(self):
        rows = [
            ["标题"],
            ["年份", "值"],
            ["2020", "1"],
            ["2021", "2"],
        ]
        t = Table(rows)
        m = t.find_header(["年份", "值"])
        recs = t.to_records(m)
        assert recs == [{"年份": "2020", "值": "1"}, {"年份": "2021", "值": "2"}]

    def test_multi_row_header_joined(self):
        """中文名一行、单位一行——按列拼接后匹配，键为拼接名。"""
        rows = [
            ["年份", "供水总量", "用水人口"],
            ["Year", "（万立方米）", "（万人）"],
            ["2020", "629", "3.5"],
        ]
        t = Table(rows)
        mapping = t.find_header(["年份", "供水总量"], rows=[0, 1])
        # 拼接后键含中英两段
        assert "年份 Year" in mapping
        assert any("万立方米" in k for k in mapping)
        # 调用方按包含匹配取列号（不必知道拼接后的确切文本）
        assert t.col_index(mapping, "年份") == 0
        assert t.col_index(mapping, "供水总量") == 1

    def test_header_not_found_raises(self):
        t = Table([["a", "b"], ["1", "2"]])
        with pytest.raises(HeaderNotFound):
            t.find_header(["不存在的列"])

    def test_header_not_found_does_not_set_state(self):
        t = Table([["a", "b"], ["1", "2"]])
        with pytest.raises(HeaderNotFound):
            t.find_header(["nope"])
        assert t.header_row is None

    def test_to_records_without_header_raises(self):
        t = Table([["a", "b"]])
        with pytest.raises(HeaderNotFound):
            t.to_records()

    def test_exact_match_mode(self):
        rows = [["年份 Year", "值"], ["2020", "1"]]
        t = Table(rows)
        with pytest.raises(HeaderNotFound):
            t.find_header(["年份"], exact=True)  # 「年份 Year」!= 「年份」

    def test_partial_require(self):
        rows = [["年份", "值"], ["2020", "1"]]
        t = Table(rows)
        m = t.find_header(["年份", "缺失列"], require_all=False)
        assert "年份" in m

    def test_skip_empty_rows_in_records(self):
        rows = [["年份", "值"], ["2020", "1"], ["", ""], ["2021", "2"]]
        t = Table(rows)
        m = t.find_header(["年份"])
        recs = t.to_records(m)
        assert len(recs) == 2

    def test_ragged_rows_padded(self):
        rows = [["年份", "值", "备注"], ["2020", "1"], ["2021", "2", "x"]]
        t = Table(rows)
        m = t.find_header(["年份"])
        recs = t.to_records(m)
        assert recs[0]["备注"] == ""


class TestDecodeNameUnit:
    def test_cp437_ascii_passthrough(self):
        info = zipfile.ZipInfo("plain.xls")
        info.flag_bits &= ~0x800
        assert _decode_name(info) == "plain.xls"

    def test_utf8_flag_respected(self):
        info = zipfile.ZipInfo("中文.xls")
        info.flag_bits |= 0x800
        assert _decode_name(info) == "中文.xls"
