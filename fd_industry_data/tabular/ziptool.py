"""ziptool — ZIP 容器枚举与**内层文件名编码回退**。

中文站点（尤其政府/协会）用旧工具打包时**不设 UTF-8 标志位**，ZIP 规范要求
文件名按 CP437 解读——于是 GBK 中文名变成乱码：

    实测（住建部 2025 年城乡建设统计年鉴）
    原始:  2025─Ω│╟╧τ╜¿╔Φ═│╝╞─Ω╝°/01│╟╩╨▓┐╖╓/1-10-1  ╚½╣·└·─Ω...
    还原:  2025年城乡建设统计年鉴/01城市部分/1-10-1  全国历年...

本模块的处理顺序：**UTF-8 标志位优先**（标准行为），无标志则按 cp437 取回原始
字节再以 GBK/GB18030 解码（回退链 GBK → GB18030 → 放弃并保留 CP437 结果）。
只要有一条路径能解出可读中文就用它——乱码名会导致按名取内层文件失败。

不做递归解包：年鉴分册是「zip 内 90 个 .xls」，但下一层是 OLE2 而非 zip，
递归与否由调用方按需决定（``unzip`` 返回字节，可再喂给 ``detect``）。
"""
from __future__ import annotations

import io
import zipfile

__all__ = ["ZipEntry", "unzip", "ZipError"]


class ZipError(ValueError):
    """不是合法 zip 容器。"""


class ZipEntry:
    """ZIP 内一个条目：已还原的文件名 + 原始字节 + 目录标记。"""

    __slots__ = ("name", "data", "is_dir", "raw_name")

    def __init__(self, name: str, data: bytes, is_dir: bool, raw_name: str):
        self.name = name
        self.data = data
        self.is_dir = is_dir
        self.raw_name = raw_name

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        kind = "dir" if self.is_dir else f"{len(self.data)}B"
        return f"ZipEntry({self.name!r}, {kind})"


def _decode_name(info: zipfile.ZipInfo) -> str:
    """文件名还原：UTF-8 标志优先，否则 cp437→GBK/GB18030 回退。"""
    raw = info.filename
    if info.flag_bits & 0x800:
        # 打包方声明了 UTF-8，zipfile 已按 UTF-8 解好
        return raw
    try:
        # zipfile 无标志时按 cp437 解，先还原成原始字节
        original = raw.encode("cp437")
    except UnicodeEncodeError:
        # 无法回环说明名里已有非 CP437 字符（zipfile 可能已用 UTF-8 兜底）
        return raw
    for enc in ("gbk", "gb18030"):
        try:
            decoded = original.decode(enc)
        except UnicodeDecodeError:
            continue
        # 解出可读中文才采纳：含 CJK 或原本就是 ASCII 名
        if decoded.isascii() or any("\u4e00" <= ch <= "\u9fff" for ch in decoded):
            return decoded
    return raw


def unzip(data: bytes) -> list[ZipEntry]:
    """枚举 zip 内条目（含目录），文件名已做编码回退还原。

    目录条目也会返回（``is_dir=True``、``data=b""``），因为年鉴分册的目录结构
    本身就是取数线索（``01城市部分/`` vs ``02村镇部分/``）。
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ZipError(f"not a zip container: {e}") from e
    out: list[ZipEntry] = []
    for info in z.infolist():
        name = _decode_name(info)
        is_dir = info.is_dir()
        payload = b"" if is_dir else z.read(info)
        out.append(ZipEntry(name=name, data=payload, is_dir=is_dir,
                            raw_name=info.filename))
    return out
