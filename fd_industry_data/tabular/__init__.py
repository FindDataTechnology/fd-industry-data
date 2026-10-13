"""tabular — 表格类数据的通用抽取层（平台共享能力）。

**为什么存在**：同一件事在舰队里被反复手写——`pboc-inclusive` 与
`edgar-emissions` 各自写了一份 xlsx 解析（前者不支持 ``inlineStr``，后者绑定
单表名），BIFF ``.xls`` 完全没有路径，中文 ZIP 文件名乱码每个源重踩一遍。
按 `unified-source-onboarding` 契约，加源应只需内容仓 git 操作；当「取表格」
每次都要重写解析器时，契约实际被破坏。本层把这件事变回平台能力。

统一出口::

    from fd_industry_data import tabular

    fmt = tabular.detect(payload)                    # 内容为准，不看扩展名
    rows = tabular.read_grid(payload)                # 任意格式 → 二维字符串网格
    rows = tabular.read_grid(payload, sheet="1-1")   # 多表选择
    for entry in tabular.unzip(payload):             # ZIP 内枚举（中文名已还原）
        if entry.name.endswith(".xls"):
            rows = tabular.read_grid(entry.data)

    table = tabular.Table(rows)
    mapping = table.find_header(["年份", "供水"])     # 跳过前置元信息行
    records = table.to_records(mapping)

支持格式：``xlsx``（stdlib）、``xls``（BIFF，经 xlrd）、``html``（含伪装成
``.xls`` 的）、``csv/tsv``、``zip`` 容器。

**不做的事**：不做 OCR（图片表按用户裁决不处理）、不做数值语义转换（一律返回
字符串，需要数值的单元自行转换）、不猜表头（定位失败即报错）。
"""
from __future__ import annotations

from .detect import (
    Format,
    decode_text,
    detect,
    is_latin_heavy,
    squash_cjk,
    strip_latin_runs,
)
from .table import HeaderNotFound, Table
from .xls import XlsError
from .xlsx import XlsxError
from .ziptool import ZipEntry, ZipError, unzip

__all__ = [
    "Format",
    "iter_rows",
    "HeaderNotFound",
    "Table",
    "TabularError",
    "XlsError",
    "XlsxError",
    "ZipEntry",
    "ZipError",
    "decode_text",
    "detect",
    "is_latin_heavy",
    "squash_cjk",
    "strip_latin_runs",
    "read_grid",
    "read_table",
    "sheets",
    "unzip",
]


class TabularError(ValueError):
    """统一抽取失败（格式不支持或载荷损坏）。"""


def _csv_grid(text: str, delim: bytes) -> list[list[str]]:
    """分隔文本 → 网格（用 csv 模块，正确处理引号内分隔符）。"""
    import csv
    import io

    reader = csv.reader(io.StringIO(text), delimiter=delim.decode("ascii"))
    return [list(row) for row in reader]


def sheets(data: bytes, **opts) -> list[str]:
    """多表工作簿的表名列表；非多表格式返回空列表。"""
    fmt = detect(data)
    if fmt is Format.XLSX:
        from . import xlsx

        return xlsx.sheets(data)
    if fmt is Format.XLS:
        from . import xls

        return xls.sheets(data)
    return []


def read_grid(
    data: bytes,
    sheet: str | int | None = None,
    *,
    table: int | None = None,
    encoding: str | None = None,
) -> list[list[str]]:
    """任意受支持格式 → 二维字符串网格。

    Args:
        data: 原始字节。
        sheet: 工作簿表名或序号（xlsx/xls 有效；其它格式忽略）。
        table: HTML 文档内的表序号（html 有效；``None`` 取首个表）。
        encoding: 强制文本编码（csv/text；缺省按 utf-8 → gb18030 回退）。

    Raises:
        TabularError: 格式不受支持或载荷损坏（含各格式的具体原因）。
    """
    fmt = detect(data)
    if fmt is Format.XLSX:
        from . import xlsx

        try:
            return xlsx.read_grid(data, sheet=sheet)
        except XlsxError as e:
            raise TabularError(str(e)) from e
    if fmt is Format.XLS:
        from . import xls

        try:
            return xls.read_grid(data, sheet=sheet)
        except XlsError as e:
            raise TabularError(str(e)) from e
    if fmt is Format.HTML:
        from . import htmltable

        try:
            return htmltable.read_grid(data, table=table, encoding=encoding)
        except htmltable.HtmlTableError as e:
            raise TabularError(str(e)) from e
    if fmt is Format.CSV:
        from .detect import decode_text

        text = decode_text(data, encoding=encoding)
        if text is None:  # pragma: no cover - detect 已保证可解码
            raise TabularError("csv payload is not decodable as text")
        delim = None
        from .detect import _csv_delimiter

        delim = _csv_delimiter(text)
        if delim is None:
            raise TabularError("csv delimiter could not be determined")
        return _csv_grid(text, delim)
    if fmt is Format.ZIP:
        raise TabularError(
            "payload is a zip container, not a workbook; "
            "enumerate with unzip() and read inner entries individually"
        )
    raise TabularError(f"unsupported payload format: {fmt.value}")


def iter_rows(data: bytes, sheet: str | int | None = None):
    """流式逐行产出 ``list[str]``（xlsx/xls；超大表用）。

    非工作簿格式抛 ``TabularError``——流式只对 Excel 两族有意义。
    """
    fmt = detect(data)
    if fmt is Format.XLSX:
        from . import xlsx

        return xlsx.iter_rows(data, sheet=sheet)
    if fmt is Format.XLS:
        from . import xls

        # BIFF 经 xlrd 一次性载入（其 API 无流式）；以生成器形态对齐接口
        def _gen():
            yield from xls.read_grid(data, sheet=sheet)

        return _gen()
    raise TabularError(
        f"iter_rows supports workbooks only, got {fmt.value}; "
        "use read_grid() for other formats"
    )


def read_table(data: bytes, **opts) -> Table:
    """``read_grid`` + ``Table`` 包装（便捷入口）。"""
    return Table(read_grid(data, **opts))
