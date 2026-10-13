"""xls — BIFF 工作簿读取（经 ``xlrd``）。

``.xls`` 是 OLE2 复合文档（magic ``\\xd0\\xcf\\x11\\xe0``），与 xlsx 的
zip+XML 完全不同族——标准库无路径，这是本层唯一需要第三方库的格式。

为什么是 ``xlrd``：2.x **只支持 .xls**（xlsx 支持已在 3.x 移除），正好与本层
的 xlsx 走 stdlib 路径互补、不重复；wheel 约 96KB 纯 Python **零传递依赖**。

日期处理是本模块的关键正确性点：xlrd 把日期单元格读成**序列号浮点数**
（``XL_CELL_DATE``），直接落成字符串会得到 ``45000.0`` 这种无意义值。
本模块按 xlrd 的 datemode 转成 ``YYYY-MM-DD``（含时间则带 ``HH:MM:SS``）。

与 xlsx 路径一致：**不做数值语义转换**，一律返回字符串。
"""
from __future__ import annotations

import io

from .detect import OLE2_MAGIC

__all__ = ["read_grid", "sheets", "XlsError"]

try:  # 依赖缺失时给出可操作的错误，而不是 ImportError 栈
    import xlrd
    import xlrd.xldate as _xldate
except ImportError:  # pragma: no cover - 由准入门/构建期兜住
    xlrd = None  # type: ignore[assignment]
    _xldate = None  # type: ignore[assignment]


class XlsError(ValueError):
    """BIFF 结构不合法，或运行环境缺少 xlrd 依赖。"""


def _require_xlrd():
    if xlrd is None:  # pragma: no cover
        raise XlsError(
            "xlrd is required to read .xls (BIFF) workbooks; "
            "install it via the runtime image (see pyproject.toml dependencies)"
        )


def _open(data: bytes):
    _require_xlrd()
    if not data.startswith(OLE2_MAGIC):
        raise XlsError("payload is not an OLE2 (.xls) container")
    try:
        return xlrd.open_workbook(file_contents=data)
    except Exception as e:  # noqa: BLE001 - 统一收敛为 XlsError
        raise XlsError(f"unreadable BIFF workbook: {e}") from e


def _fmt_date(value: float, datemode: int) -> str:
    """序列号 → ``YYYY-MM-DD`` 或 ``YYYY-MM-DD HH:MM:SS``。

    整日不补时间部分（政府年鉴的年份列多为整日）；越界序列号回退为原值字符串。
    """
    try:
        parts = _xldate.xldate_as_tuple(value, datemode)
    except Exception:  # noqa: BLE001 - 越界/非法值按原样返回
        return str(value)
    y, mo, d, h, mi, s = parts
    if (h, mi, s) == (0, 0, 0):
        return f"{y:04d}-{mo:02d}-{d:02d}"
    return f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}:{s:02d}"


def _cell_text(sheet, r: int, c: int, datemode: int) -> str:
    """单元格取值：文本/数字/日期/布尔/空 五态 → 字符串。"""
    ctype = sheet.cell_type(r, c)
    if ctype == xlrd.XL_CELL_EMPTY or ctype == xlrd.XL_CELL_BLANK:
        return ""
    if ctype == xlrd.XL_CELL_DATE:
        return _fmt_date(sheet.cell_value(r, c), datemode)
    if ctype == xlrd.XL_CELL_BOOLEAN:
        return "TRUE" if sheet.cell_value(r, c) else "FALSE"
    if ctype == xlrd.XL_CELL_ERROR:
        return ""
    value = sheet.cell_value(r, c)
    if ctype == xlrd.XL_CELL_NUMBER:
        # 整数值不显示小数点（年鉴里 2025 年不该写成 2025.0）
        f = float(value)
        if f.is_integer():
            return str(int(f))
        return repr(f) if abs(f) < 1e-4 else str(f)
    return str(value)


def sheets(data: bytes) -> list[str]:
    """返回工作簿内所有表名（含尾部空格原样保留，便于按名精确取）。"""
    book = _open(data)
    return list(book.sheet_names())


def read_grid(data: bytes, sheet: str | int | None = None) -> list[list[str]]:
    """读整表为二维字符串网格。

    ``sheet`` 为表名、序号或 None（取第一张表）。表名按**原样**匹配，
    同时也接受去空白后的匹配（年鉴表名实测带尾随空格，如 ``"2-1  "``）。
    """
    book = _open(data)
    if book.nsheets == 0:
        raise XlsError("workbook has no sheets")
    if sheet is None:
        sh = book.sheet_by_index(0)
    elif isinstance(sheet, int):
        if not 0 <= sheet < book.nsheets:
            raise XlsError(
                f"sheet index {sheet} out of range (workbook has {book.nsheets} sheets)"
            )
        sh = book.sheet_by_index(sheet)
    else:
        names = book.sheet_names()
        if sheet in names:
            sh = book.sheet_by_name(sheet)
        else:
            stripped = {n.strip(): n for n in names}
            if sheet in stripped:
                sh = book.sheet_by_name(stripped[sheet])
            else:
                raise XlsError(f"sheet {sheet!r} not found in {names!r}")
    datemode = book.datemode
    return [
        [_cell_text(sh, r, c, datemode) for c in range(sh.ncols)]
        for r in range(sh.nrows)
    ]
