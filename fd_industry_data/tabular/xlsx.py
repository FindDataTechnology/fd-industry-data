"""xlsx — OOXML 工作簿读取（纯标准库 ``zipfile`` + ``xml.etree``）。

本模块合并了此前分散在两个单元里的手写实现能力：

- 共享字符串表（``t="s"``）——来自 ``pboc-inclusive`` 的 ``read_sheet``
- 内联字符串（``t="inlineStr"``）——来自 ``edgar-emissions`` 的 ``_cell_text``
- 空单元格按 ``r`` 引用对齐（Excel 会省略空单元格，不补齐就会错列）

设计要点：

- **不做数值语义转换**：一律返回字符串。既有两个单元都返回字符串，保持行为
  一致；需要数值的单元自行转换（它们本来就在这么做）。
- **表名解析走 workbook + rels**：不靠 ``sheet1.xml`` 文件名猜顺序。
- **流式可选**：``read_grid`` 默认整表载入（政府表格量级下最简），
  ``iter_rows`` 供超大表用。

命名空间按 ECMA-376 固定串匹配；遇到未声明的变体时回退到「本地名匹配」。
"""
from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET

from .detect import ZIP_MAGIC

__all__ = ["read_grid", "sheets", "iter_rows", "XlsxError"]

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

_CELL_REF_RE = re.compile(r"^([A-Z]+)(\d+)$")


class XlsxError(ValueError):
    """xlsx 结构不合法或缺少必要部件。"""


def _localname(tag: str) -> str:
    """去掉命名空间前缀，便于兼容未声明默认命名空间的变体。"""
    return tag.rsplit("}", 1)[-1]


def _col_index(ref: str) -> int:
    """单元格引用（如 ``AB12``）→ 0 基列号；无引用返回 -1。"""
    m = _CELL_REF_RE.match(ref or "")
    if not m:
        return -1
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _shared_strings(z: zipfile.ZipFile) -> list[str]:
    """读共享字符串表；无该部件返回空表（合法：全内联的工作簿）。"""
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    out: list[str] = []
    for si in root.iter():
        if _localname(si.tag) != "si":
            continue
        # 富文本把文本切在多个 <t> 里，需拼接
        out.append("".join(t.text or "" for t in si.iter() if _localname(t.tag) == "t"))
    return out


def _find_child(elem: ET.Element, name: str) -> ET.Element | None:
    """按本地名取直接子元素（命名空间容错）。"""
    for child in elem:
        if _localname(child.tag) == name:
            return child
    return None


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    """单元格取值：shared / inlineStr / 直值 三态；空单元格返回空串。"""
    kind = cell.get("t")
    if kind == "s":
        v = _find_child(cell, "v")
        if v is None or v.text is None:
            return ""
        try:
            return shared[int(v.text)]
        except (ValueError, IndexError):
            return ""
    if kind == "inlineStr":
        return "".join(
            t.text or "" for t in cell.iter() if _localname(t.tag) == "t"
        )
    if kind == "str":
        # 公式结果字符串
        v = _find_child(cell, "v")
        return (v.text or "") if v is not None else ""
    v = _find_child(cell, "v")
    return (v.text or "") if v is not None else ""


def _workbook_sheet_paths(z: zipfile.ZipFile) -> dict[str, str]:
    """表名 → 包内 sheet 路径（经 workbook + rels 解析，不靠文件名猜）。

    命名空间容错：部分导出器（及手工生成的 workbook）不声明默认命名空间，
    故元素匹配按**本地名**回退；``r:id`` 亦按属性名后缀匹配。
    """
    if "xl/workbook.xml" not in z.namelist():
        raise XlsxError("no xl/workbook.xml — not an OOXML workbook")
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels_path = "xl/_rels/workbook.xml.rels"
    rels: dict[str, str] = {}
    if rels_path in z.namelist():
        rroot = ET.fromstring(z.read(rels_path))
        for rel in rroot.iter():
            if _localname(rel.tag) != "Relationship":
                continue
            rid, target = rel.get("Id"), rel.get("Target") or ""
            if not rid:
                continue
            if target.startswith("/"):
                target = target.lstrip("/")
            elif not target.startswith("xl/"):
                target = "xl/" + target
            rels[rid] = target

    def _rid_of(elem: ET.Element) -> str | None:
        """取关系 id：优先带命名空间，回退到任意以 ``}id``/``id`` 结尾的属性。"""
        v = elem.get(NS_R + "id")
        if v:
            return v
        for key, val in elem.attrib.items():
            if key == "id" or key.endswith("}id"):
                return val
        return None

    out: dict[str, str] = {}
    for sh in wb.iter():
        if _localname(sh.tag) != "sheet":
            continue
        name = sh.get("name") or ""
        target = rels.get(_rid_of(sh) or "")
        if not target:
            continue
        out[name] = target
    return out


def _sheet_names_from_zip(z: zipfile.ZipFile) -> list[str]:
    """兜底：workbook 缺失时按 ``xl/worksheets/sheet*.xml`` 排序给名。"""
    names = sorted(
        n for n in z.namelist()
        if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
    )
    return names


def sheets(data: bytes) -> list[str]:
    """返回工作簿内所有表名（按工作簿声明顺序）。"""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        try:
            paths = _workbook_sheet_paths(z)
        except XlsxError:
            return _sheet_names_from_zip(z)
    return list(paths.keys())


def _resolve_sheet(z: zipfile.ZipFile, sheet: str | int | None) -> str:
    """把表名或序号解析为包内路径。"""
    if sheet is None:
        paths = None
        try:
            paths = _workbook_sheet_paths(z)
        except XlsxError:
            pass
        if paths:
            return next(iter(paths.values()))
        fallback = _sheet_names_from_zip(z)
        if not fallback:
            raise XlsxError("no worksheets in workbook")
        return fallback[0]
    if isinstance(sheet, int):
        paths = None
        try:
            paths = _workbook_sheet_paths(z)
        except XlsxError:
            pass
        seq = list(paths.values()) if paths else _sheet_names_from_zip(z)
        try:
            return seq[sheet]
        except IndexError:
            raise XlsxError(
                f"sheet index {sheet} out of range (workbook has {len(seq)} sheets)"
            ) from None
    paths = None
    try:
        paths = _workbook_sheet_paths(z)
    except XlsxError:
        pass
    if paths and sheet in paths:
        return paths[sheet]
    # 兜底：按文件名匹配（部分导出器不带 workbook 关系）
    for n in _sheet_names_from_zip(z):
        if n.endswith(f"/{sheet}.xml"):
            return n
    raise XlsxError(f"sheet {sheet!r} not found")


def iter_rows(data: bytes, sheet: str | int | None = None):
    """流式逐行产出 ``list[str]``（大表用；``read_grid`` 是其物化版本）。"""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        path = _resolve_sheet(z, sheet)
        shared = _shared_strings(z)
        with z.open(path) as fh:
            for _event, elem in ET.iterparse(fh, events=("end",)):
                if _localname(elem.tag) != "row":
                    continue
                cells: list[str] = []
                for c in elem.iter():
                    if _localname(c.tag) != "c":
                        continue
                    idx = _col_index(c.get("r") or "")
                    text = _cell_text(c, shared)
                    if idx < 0:
                        cells.append(text)
                        continue
                    while len(cells) <= idx:
                        cells.append("")
                    cells[idx] = text
                yield cells
                elem.clear()


def read_grid(data: bytes, sheet: str | int | None = None) -> list[list[str]]:
    """读整表为二维字符串网格（空单元格已按引用补齐）。"""
    if not data.startswith(ZIP_MAGIC):
        raise XlsxError("payload is not a zip/OOXML container")
    return list(iter_rows(data, sheet=sheet))
