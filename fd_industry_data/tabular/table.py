"""table — 网格包装：表头探测、列映射、记录化。

政府与协会表格的常态是**数据表前有若干标题/元信息行**（表名、英文名、单位
说明），真表头在第 3–6 行；``edgar-emissions`` 已为这种形态手写过一遍表头探测。
本模块把它做成共享能力。

关键约定：

- ``find_header`` **定位失败必须报错**，不返回猜测的列映射——错位的列比报错
  危险得多（会把「产量」当「销量」落库）。
- 多行表头（中文名一行、英文名一行、单位一行）由调用方按行拼接后传入
  ``find_header(..., rows=[0,1])``；本模块不猜哪几行是表头。
- ``to_records`` 按列映射产 dict，空行跳过，不做数值转换。
"""
from __future__ import annotations

import re

__all__ = ["Table", "HeaderNotFound"]

_WS_RE = re.compile(r"[\s\u00a0\u3000\n\r]+")


class HeaderNotFound(LookupError):
    """在给定行范围内找不到所需列名。"""


def _squash(text: str) -> str:
    return _WS_RE.sub(" ", text or "").strip()


class Table:
    """二维字符串网格 + 表头/列操作。"""

    __slots__ = ("rows", "header_row")

    def __init__(self, rows: list[list[str]], header_row: int | None = None):
        self.rows = rows
        self.header_row = header_row

    def __len__(self) -> int:
        return len(self.rows)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        ncols = len(self.rows[0]) if self.rows else 0
        return f"Table({len(self.rows)}x{ncols}, header_row={self.header_row})"

    # ---- 构造 ----------------------------------------------------------

    @classmethod
    def from_grid(cls, rows: list[list[str]]) -> "Table":
        return cls(rows)

    @classmethod
    def from_bytes(cls, data: bytes, **opts) -> "Table":
        """按内容探测格式并读表（统一入口，见 ``tabular.read_table``）。"""
        from . import read_grid  # 延迟导入避免循环

        return cls(read_grid(data, **opts))

    # ---- 表头 ----------------------------------------------------------

    def find_header(
        self,
        names: list[str] | tuple[str, ...],
        *,
        rows: list[int] | None = None,
        max_scan: int = 20,
        exact: bool = False,
        require_all: bool = True,
    ) -> dict[str, int]:
        """定位真实表头行，返回 ``{列名: 列号}``。

        Args:
            names: 期望出现的列名（用于定位；返回映射含该行**所有**非空列）。
            rows: 限定候选行号（如多行表头 ``[0, 1]`` 拼接后匹配）。给定时
                只在其中找；不给则扫前 ``max_scan`` 行。
            max_scan: 未指定 ``rows`` 时扫描的行数上限。
            exact: 是否要求单元格文本与列名完全相等（默认包含匹配，容忍
                「年份 Year」这类中英混排表头）。
            require_all: 是否要求 ``names`` 全部命中才算找到。

        Raises:
            HeaderNotFound: 未找到满足条件的行（不返回猜测结果）。
        """
        if not names:
            raise ValueError("names must be non-empty")
        candidates = rows if rows is not None else list(range(min(max_scan, len(self.rows))))
        for r in candidates:
            if not 0 <= r < len(self.rows):
                continue
            if rows is not None and len(rows) > 1:
                # 多行表头：按列把候选行文本拼接（列数可能不一致，取最长）
                width = max(len(self.rows[i]) for i in rows if 0 <= i < len(self.rows))
                cells = []
                for c in range(width):
                    parts = [
                        _squash(self.rows[i][c]) if c < len(self.rows[i]) else ""
                        for i in rows
                    ]
                    cells.append(" ".join(p for p in parts if p))
            else:
                cells = [_squash(x) for x in self.rows[r]]
            mapping: dict[str, int] = {}
            for c, text in enumerate(cells):
                if text and text not in mapping:
                    mapping[text] = c
            hits = 0
            for want in names:
                w = _squash(want)
                if any(
                    (cell == w) if exact else (w in cell or cell in w)
                    for cell in mapping
                ):
                    hits += 1
            if (hits == len(names)) if require_all else hits:
                self.header_row = r
                return mapping
        raise HeaderNotFound(
            f"no header row containing {list(names)!r} "
            f"within rows {candidates[:max_scan]!r}"
        )

    def col_index(self, mapping: dict[str, int], name: str, *, exact: bool = False) -> int:
        """从列映射取列号；支持包含匹配。取不到抛 ``HeaderNotFound``。"""
        want = _squash(name)
        for cell, idx in mapping.items():
            if (cell == want) if exact else (want in cell or cell in want):
                return idx
        raise HeaderNotFound(f"column {name!r} not in header mapping {list(mapping)!r}")

    # ---- 记录化 --------------------------------------------------------

    def to_records(
        self,
        mapping: dict[str, int] | None = None,
        *,
        start: int | None = None,
        skip_empty: bool = True,
    ) -> list[dict[str, str]]:
        """按列映射产出记录列表。

        ``start`` 缺省为表头行之后；``skip_empty`` 跳过全空行（表格尾部的
        统计注记行常为单列文本，仍会保留——只在整行皆空时跳过）。
        """
        if mapping is None:
            if self.header_row is None:
                raise HeaderNotFound("no header row located; call find_header first")
            mapping = {
                _squash(x): c
                for c, x in enumerate(self.rows[self.header_row])
                if _squash(x)
            }
        begin = (self.header_row + 1) if start is None else start
        out: list[dict[str, str]] = []
        for row in self.rows[begin:]:
            if skip_empty and not any(_squash(c) for c in row):
                continue
            rec: dict[str, str] = {}
            for name, idx in mapping.items():
                rec[name] = row[idx] if 0 <= idx < len(row) else ""
            out.append(rec)
        return out
