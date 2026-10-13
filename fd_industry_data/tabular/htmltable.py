"""htmltable — HTML ``<table>`` 解析为二维网格（含 rowspan/colspan 展开）。

覆盖两类常见来源：

- 老站点把 HTML 表导出成 ``.xls``（Excel 能打开）——扩展名骗人，本模块按内容
  特征被 ``detect`` 选中后接管。
- 正文页内嵌数据表（如 NBS/协会统计页）。

**编码显式处理**：lxml 的 HTML 解析器在页面**未声明 charset** 时按 latin-1
处理，中文表格会静默解成 ``å¹´ä»½`` 乱码。本模块先用 ``detect.decode_text``
按「页面声明 → utf-8 → gb18030」解出字符串再交给 lxml（喂 str 不喂 bytes），
从根本上避开这个坑。

``rowspan``/``colspan`` 必须展开：不展开会让后续行整体左移，列对不齐。展开
规则按 HTML 规范——被 ``rowspan`` 占用的格子在其后续行里保留占位（同一值重复
填入），这样每行的列数一致、按列取数才可靠。
"""
from __future__ import annotations

import re

from lxml import html as lxml_html

from .detect import decode_text

__all__ = ["read_grid", "table_count", "HtmlTableError"]


class HtmlTableError(ValueError):
    """载荷里没有可解析的表格。"""


_WS_RE = re.compile(r"[\s\u00a0\u3000]+")


def _squash(text: str) -> str:
    """空白（含 &nbsp;/全角空格）折叠为单空格。"""
    return _WS_RE.sub(" ", text or "").strip()


def _parse(data: bytes, encoding: str | None = None):
    """字节 → lxml 文档。先显式解码为 str，避免 lxml 的 latin-1 兜底。"""
    text = decode_text(data, encoding=encoding)
    if text is None:
        # 全编码失败：交给 lxml 尽力而为（至少能拿到结构）
        return lxml_html.fromstring(data)
    return lxml_html.fromstring(text)


def _expand_table(table) -> list[list[str]]:
    """把单个 <table> 展开为规整网格（处理 rowspan/colspan）。

    算法：维护 ``pending``（上一行 rowspan 延续到本行的占位）。逐行先落
    pending，再按序填本行单元格；colspan 复制值到多个列位，rowspan 记入
    pending 供后续行使用。
    """
    grid: list[list[str]] = []
    # pending[col] = (remaining_rows, text)
    pending: dict[int, tuple[int, str]] = {}
    for tr in table.iter("tr"):
        row: list[str] = []
        col = 0

        def _fill_pending(upto: int) -> None:
            """把本行 col 之前的 rowspan 占位落进 row。"""
            nonlocal col
            while col < upto or (col in pending and col < upto):
                if col in pending:
                    remaining, text = pending[col]
                    row.append(text)
                    if remaining <= 1:
                        del pending[col]
                    else:
                        pending[col] = (remaining - 1, text)
                else:
                    row.append("")
                col += 1

        for cell in tr.iter("td", "th"):
            # 先把该单元格之前的列位（含被占用的）补齐
            while col in pending:
                remaining, text = pending[col]
                row.append(text)
                if remaining <= 1:
                    del pending[col]
                else:
                    pending[col] = (remaining - 1, text)
                col += 1
            text = _squash(cell.text_content())
            try:
                colspan = max(int(cell.get("colspan") or 1), 1)
            except ValueError:
                colspan = 1
            try:
                rowspan = max(int(cell.get("rowspan") or 1), 1)
            except ValueError:
                rowspan = 1
            for k in range(colspan):
                row.append(text)
                if rowspan > 1:
                    pending[col + k] = (rowspan - 1, text)
            col += colspan
        # 行尾可能还有未落地的 rowspan 占位
        while col in pending:
            remaining, text = pending[col]
            row.append(text)
            if remaining <= 1:
                del pending[col]
            else:
                pending[col] = (remaining - 1, text)
            col += 1
        grid.append(row)
    # 统一列宽（尾部补空），避免下游按列索引取数时越界
    width = max((len(r) for r in grid), default=0)
    for r in grid:
        while len(r) < width:
            r.append("")
    return grid


def _tables(doc):
    """按文档顺序取所有 <table>（含嵌套表：嵌套的也单独产出）。"""
    return doc.iter("table")


def table_count(data: bytes, encoding: str | None = None) -> int:
    """文档内 <table> 数量（用于选表/留痕）。"""
    doc = _parse(data, encoding=encoding)
    return sum(1 for _ in _tables(doc))


def read_grid(
    data: bytes,
    table: int | None = None,
    *,
    encoding: str | None = None,
) -> list[list[str]]:
    """解析 HTML 表格为网格。

    ``table`` 为表序号（0 基，按文档顺序）；None 取**首个**表。
    ``encoding`` 强制解码（缺省按页面声明 → utf-8 → gb18030）。
    表格缺失时抛 ``HtmlTableError``（不返回空网格——那会让调用方误以为表是空的）。
    """
    doc = _parse(data, encoding=encoding)
    tables = list(_tables(doc))
    if not tables:
        raise HtmlTableError("no <table> element in payload")
    idx = 0 if table is None else table
    try:
        target = tables[idx]
    except IndexError:
        raise HtmlTableError(
            f"table index {idx} out of range (document has {len(tables)} tables)"
        ) from None
    return _expand_table(target)
