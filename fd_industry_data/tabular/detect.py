"""detect — 表格类载荷的格式探测（以内容为准，不以扩展名为准）。

老站点普遍把 HTML 表格导出成 ``.xls``（Excel 能打开、误导性强），年鉴类
分册包则是 **zip 容器而非 xlsx**。两者按扩展名判都会错，故本模块只看字节。

判定优先级（首个命中即返回）::

    xlsx  PK\\x03\\x04 且包内含 ``xl/`` 前缀（workbook.xml 或 worksheets）
    xls   OLE2 复合文档魔数 ``\\xd0\\xcf\\x11\\xe0\\xa1\\xb1\\x1a\\xe1``
    html  文本特征 ``<table`` / ``<html`` / ``<!doctype html``
    zip   PK\\x03\\x04 但不是 xlsx（如分册打包的年鉴）
    csv   分隔符嗅探通过（多行、列数一致、含分隔符）
    text  可解码但非上表形态
    unknown

csv 判定刻意保守：只有**多行且列数一致**才认，避免把普通文本误判为表格。
"""
from __future__ import annotations

import enum
import re

__all__ = [
    "Format",
    "detect",
    "decode_text",
    "squash_cjk",
    "is_latin_heavy",
    "strip_latin_runs",
    "OLE2_MAGIC",
    "ZIP_MAGIC",
]

OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"

# 前 N 字节足够判 HTML（真实页面 <table 可能靠后，故放宽扫描窗口）
_SNIFF_WINDOW = 8192
_HTML_RE = re.compile(rb"<\s*(?:table|html|!doctype\s+html)", re.IGNORECASE)
# 页面自带编码声明（XML 声明 / meta charset / meta http-equiv）
_CHARSET_RE = re.compile(
    rb"""(?:charset\s*=\s*["']?\s*|encoding\s*=\s*["'])([A-Za-z0-9_\-]+)""",
    re.IGNORECASE,
)
# csv 嗅探：至少 2 行、至少 2 列，且各行列数一致
_CSV_CANDIDATES = (b",", b"\t", b";", b"|")
_CSV_MIN_ROWS = 2
_CSV_MIN_COLS = 2

# 常见声明别名 → Python 编码名
_CHARSET_ALIASES = {
    "gb2312": "gb18030",   # gb2312 是 GBK 子集，用超集解码更宽容
    "gbk": "gb18030",
    "gb18030": "gb18030",
    "utf8": "utf-8",
    "utf-8": "utf-8",
    "big5": "big5",
}


class Format(str, enum.Enum):
    """探测到的载荷格式。"""

    XLSX = "xlsx"      # OOXML：zip + XML
    XLS = "xls"        # BIFF：OLE2 复合文档
    HTML = "html"      # HTML 表格（含伪装成 .xls 的）
    ZIP = "zip"        # zip 容器但不是 xlsx（分册包等）
    CSV = "csv"        # 分隔符文本（含 tsv）
    TEXT = "text"      # 可解码文本但非表格形态
    UNKNOWN = "unknown"


def _looks_like_xlsx(data: bytes) -> bool:
    """zip 魔数 + 包内含 ``xl/`` 条目（workbook.xml 或 worksheets）。"""
    if not data.startswith(ZIP_MAGIC):
        return False
    head = data[:_SNIFF_WINDOW]
    # 中央目录通常不在头部窗口内，故同时扫前段与整体尾部
    if b"xl/workbook.xml" in data or b"xl/worksheets/" in data:
        return True
    return b"xl/" in head and b"[Content_Types].xml" in head


def decode_text(data: bytes, encoding: str | None = None) -> str | None:
    """文本解码：显式声明优先 → 页面自声明 → utf-8 → gb18030 回退。

    为什么不能只靠 ``bytes.decode`` 猜：中文站点的页面**常常不声明 charset**
    （尤其老政府/协会站的表格导出），而 lxml 的 HTML 解析器在无声明时按
    **latin-1** 处理，会把中文解成 ``å¹´ä»½`` 这类乱码——静默错值比报错更危险。
    本函数显式按「声明 → utf-8 → gb18030」顺序解码，解不出返回 None。
    """
    if encoding:
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            return None
    declared = detect_charset(data)
    if declared:
        try:
            return data.decode(declared)
        except UnicodeDecodeError:
            pass  # 声明与实际不符，继续回退
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def detect_charset(data: bytes) -> str | None:
    """从页面声明里取编码（meta charset / XML 声明 / http-equiv）；无声明返回 None。"""
    m = _CHARSET_RE.search(data[:4096])
    if not m:
        return None
    name = m.group(1).decode("ascii", errors="replace").lower()
    return _CHARSET_ALIASES.get(name, name)


def _decode_text(data: bytes) -> str | None:
    """内部别名（保持旧调用点可读）。"""
    return decode_text(data)


def _csv_delimiter(text: str) -> bytes | None:
    """嗅探分隔符：多行、列数一致且列数 >= 2 才认。"""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < _CSV_MIN_ROWS:
        return None
    sample = lines[:20]
    for delim in _CSV_CANDIDATES:
        d = delim.decode("ascii")
        counts = {ln.count(d) for ln in sample}
        if len(counts) != 1:
            continue  # 列数不一致 → 不是规整分隔文本
        ncols = counts.pop() + 1
        if ncols >= _CSV_MIN_COLS:
            return delim
    return None


def detect(data: bytes) -> Format:
    """按字节内容判定格式。空载荷返回 ``Format.UNKNOWN``。"""
    if not data:
        return Format.UNKNOWN
    if data.startswith(ZIP_MAGIC):
        return Format.XLSX if _looks_like_xlsx(data) else Format.ZIP
    if data.startswith(OLE2_MAGIC):
        return Format.XLS
    if _HTML_RE.search(data[:_SNIFF_WINDOW]):
        return Format.HTML
    text = _decode_text(data)
    if text is not None:
        # HTML 特征也可能出现在解码后才可见的位置，再判一次
        if _HTML_RE.search(data[: 64 * 1024]):
            return Format.HTML
        if _csv_delimiter(text) is not None:
            return Format.CSV
        return Format.TEXT
    return Format.UNKNOWN


# CJK 字符区间（含扩展 A 与兼容表意文字）
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
# 中文之间的空白（对齐填充），如「供 水 普及率」→「供水普及率」
_CJK_SPACE_RE = re.compile(r"(?<=[\u3400-\u9fff\uf900-\ufaff])\s+(?=[\u3400-\u9fff\uf900-\ufaff])")


def squash_cjk(text: str) -> str:
    """压缩中文表头里的**对齐填充空格**。

    中文表格为了对齐常用「供 水 普及率」这类字间空格（有时还有换行）。这不是
    语义空格，直接当列名会污染下游（无法与「供水普及率」匹配）。本函数只删
    **两个 CJK 字符之间**的空白，中英之间的空格保留（如「年份 Year」）。
    """
    out = _CJK_SPACE_RE.sub("", text or "")
    return re.sub(r"[\s\u00a0\u3000]+", " ", out).strip()


def is_latin_heavy(text: str, threshold: float = 0.8) -> bool:
    """判断一段文本是否以拉丁字母为主（表头的英文翻译行）。

    中文年鉴的列名常有两行：中文名 + 英文翻译。英文行不是额外列名，应丢弃——
    否则列名会变成「供水普及率（%） Water Coverage Rate」，单位也会取到英文的
    ``sq.m`` 而非中文的 ``平方米``。
    """
    t = (text or "").strip()
    if not t:
        return False
    cjk = len(_CJK_RE.findall(t))
    if cjk:
        return False
    latin = sum(1 for ch in t if ch.isascii() and ch.isalpha())
    return latin / max(len(t), 1) >= threshold


# 拉丁词/短语（含内部空格与点号）。用于剥离中文单元格里的英文翻译段。
_LATIN_RUN_RE = re.compile(r"[A-Za-z][A-Za-z0-9 .\-]*")
# 纯拉丁的括号补注（英文单位）——**括号内不能含中文**，否则会误删中文单位。
# 允许未闭合（源里存在 ``(unit`` 这类截断）；**必须允许内部换行**，实测存在
# ``(100 \n million persons)`` 这种折行写法，不容换行则整段残留成 ``(100``。
_LATIN_PAREN_RE = re.compile(
    r"[(（](?![^()（）]*[\u3400-\u9fff\uf900-\ufaff])"
    r"[A-Za-z0-9 .,\-\s]*[A-Za-z][A-Za-z0-9 .,\-\s]*[)）]?"
)


def strip_latin_runs(text: str) -> str:
    """从**中英混合**的单元格里剥离英文翻译段，只留中文（含中文单位括号）。

    中文年鉴的表头单元格把中文名与英文翻译放在同一格里，形如::

        建 制 镇\n统计个数\n(万个)\nNumber of\nTowns\n\n(10000 units)

    英文段不是额外信息，留着会污染指标名与单位（单位会取到 ``10000 units``
    而非中文的 ``万个``）。处理顺序：先删**纯拉丁括号补注**（``(10000 units)``，
    负向断言保证含中文的括号如 ``(万个)`` 不被误删），再删拉丁词串。

    只在单元格**含中文**时剥离——纯英文单元格（有些源表头本就是英文）由
    ``is_latin_heavy`` 单独判断，不在此处处理。
    """
    t = text or ""
    if not _CJK_RE.search(t):
        return re.sub(r"[\s\u00a0\u3000]+", " ", t).strip()
    t = _LATIN_PAREN_RE.sub(" ", t)
    t = _LATIN_RUN_RE.sub(" ", t)
    # 清理剥离后留下的孤立括号：空括号、以及**紧跟在空白后的单侧括号**
    # （英文被删后可能只剩 ``)``，如 ``(100 million\npersons)`` 换行导致未配对）
    t = re.sub(r"[(（]\s*[)）]", " ", t)
    t = re.sub(r"\s+[)）](?=\s|$)", " ", t)
    t = re.sub(r"[(（](?=\s|$)", " ", t)
    # 英文单位前半被删后可能只剩「(数字」或「数字)」；中文单位括号不会被此规则命中
    # （其内容为中文，且已在上方保留）
    t = re.sub(r"[(（]\s*[\d.,\s]*$", " ", t)
    t = re.sub(r"^\s*[\d.,\s]*[)）]", " ", t)
    return re.sub(r"[\s\u00a0\u3000]+", " ", t).strip()


def is_latin_heavy(text: str, threshold: float = 0.8) -> bool:
    """判断一段文本是否以拉丁字母为主（表头的英文翻译行）。

    中文年鉴的列名常有两行：中文名 + 英文翻译。英文行不是额外列名，应丢弃——
    否则列名会变成「供水普及率（%） Water Coverage Rate」，单位也会取到英文的
    ``sq.m`` 而非中文的 ``平方米``。
    """
    t = (text or "").strip()
    if not t:
        return False
    cjk = len(_CJK_RE.findall(t))
    if cjk:
        return False
    latin = sum(1 for ch in t if ch.isascii() and ch.isalpha())
    return latin / max(len(t), 1) >= threshold
