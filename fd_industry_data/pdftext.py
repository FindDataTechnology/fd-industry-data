"""pdftext — PDF 文本层抽取（平台共享能力，poppler ``pdftotext`` 单层包装）。

**为什么存在**：批次六（``add-banking-industry-sources``）有四个上游只发 PDF
——NFRA 批量公示处罚表、上清所月报表四/表五、银行业协会理财指数 2026 期、
银登不良转让公告。侦察实测这些 PDF **带文本层**（无需 OCR），但文本层抽取是
同一件事：起子进程、``-layout`` 保版面、非 PDF 载荷拦在门外。四个单元各写一份
就是第五次手写（``tabular`` 层正是为消灭这类重复而生）。

契约::

    from fd_industry_data import pdftext

    pdftext.available()                      # pdftotext 在当前镜像里吗
    pdftext.is_pdf(payload)                  # 魔数 ```%PDF``` 判定（不信扩展名/Content-Type）
    text = pdftext.extract_text(payload)     # 默认 -layout（保版面，表格切行靠它）
    text = pdftext.extract_text(payload, layout=False)   # -raw（阅读序，正文叙述用）
    pdftext.extract_words(payload)           # 词级 bbox（列交错时必须靠 x 归列）

``extract_words`` 是同一二进制的另一个开关（``-bbox-layout``），给**列交错**的
表格用：医保目录谈判药品段里药名跨三行、金额列可能只有一个 ``*``，文本序归列必然
出错，只能靠 x 坐标。它返回词级坐标，不做任何语义判断——归列规则留在单元里。

**不做的事**：不做 OCR（图片型 PDF 抽取结果为空串，由单元按「无文本层」留痕
跳过）；不代替单元做版面语义（只给坐标，不判断哪列是什么）；不做缓存（同一载荷
重复抽取由调用方自己避免）。

依赖：``pdftotext`` 二进制（Debian ``poppler-utils``）。runner 镜像已装，见
``Dockerfile`` 的 runner 阶段注释；``available()`` 为假时 ``extract_text`` 抛
``PdfTextError``——单元据此**失败即红**，绝不静默回空表。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

__all__ = ["PdfTextError", "available", "extract_text", "extract_words", "is_pdf"]

# ``pdftotext -bbox-layout`` 的 HTML 输出：每个 <word> 带四点坐标 + 文本
_WORD_RE = re.compile(
    r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>'
)

PDF_MAGIC = b"%PDF-"
DEFAULT_TIMEOUT = 120  # 秒；20 页中文 PDF 实测 < 3s，留足大表余量


class PdfTextError(RuntimeError):
    """PDF 文本层抽取失败（无二进制 / 非 PDF / pdftotext 非零退出）。"""


def available() -> bool:
    """``pdftotext`` 是否可用（runner 镜像装了 poppler-utils 即为真）。"""
    return shutil.which("pdftotext") is not None


def is_pdf(payload: bytes | bytearray | memoryview) -> bool:
    """按魔数判定是否 PDF（容忍前导空白/BOM）。"""
    head = bytes(payload[:1024]).lstrip()
    return head.startswith(PDF_MAGIC)


def _decode(raw: bytes) -> str:
    """pdftotext 输出统一按 UTF-8 解，坏字节替换而不抛（中文正文实测 UTF-8）。"""
    return raw.decode("utf-8", "replace")


def extract_text(payload: bytes, *, layout: bool = True,
                 timeout: int = DEFAULT_TIMEOUT, page: int | None = None) -> str:
    """PDF 载荷 → 纯文本。

    Args:
        payload: 完整 PDF 字节串（调用方负责已经过 ``is_pdf`` 判断）。
        layout: ``True`` = ``-layout``（保留版面列位，适合表格逐行切；
            ``False`` = ``-raw``（阅读序，适合正文叙述）。
        timeout: 子进程硬超时（秒），超时抛 ``PdfTextError``。
        page: 只抽单页（1 起）；``None`` = 全部页。

    Returns:
        抽取出的文本（未做任何清洗/去水印；空串表示无文本层）。

    Raises:
        PdfTextError: 二进制缺失、载荷非 PDF、或 pdftotext 非零退出。
    """
    if not is_pdf(payload):
        raise PdfTextError("payload is not a PDF (magic %PDF- absent)")
    if not available():
        raise PdfTextError(
            "pdftotext not found — install poppler-utils in the runtime image "
            "(Dockerfile runner stage)"
        )

    args = ["pdftotext", "-layout" if layout else "-raw", "-enc", "UTF-8"]
    if page is not None:
        args += ["-f", str(int(page)), "-l", str(int(page))]

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)
        proc = subprocess.run(  # noqa: S603 - 固定二进制 + 常量参数
            [*args, str(tmp_path), "-"],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PdfTextError(f"pdftotext timed out after {timeout}s") from exc
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    if proc.returncode != 0:
        detail = _decode(proc.stderr).strip()[:200]
        raise PdfTextError(f"pdftotext exited {proc.returncode}: {detail}")
    return _decode(proc.stdout)


def extract_words(payload: bytes, *, first_page: int | None = None,
                  last_page: int | None = None,
                  timeout: int = DEFAULT_TIMEOUT) -> list[tuple[int, float, float, float, float, str]]:
    """PDF 载荷 → 词级坐标 ``[(page, xMin, yMin, xMax, yMax, word), ...]``。

    Args:
        payload: 完整 PDF 字节串。
        first_page / last_page: 页范围（1 起，含两端）；均省略 = 全部页。
        timeout: 子进程硬超时（秒）。

    Returns:
        词列表，坐标为 PDF 点（1/72 inch），``page`` 是本次抽取范围内的 1 起序号
        （不是 PDF 绝对页号）。空列表表示无文本层或该范围无词。

    Raises:
        PdfTextError: 二进制缺失、载荷非 PDF、或 pdftotext 非零退出。
    """
    if not is_pdf(payload):
        raise PdfTextError("payload is not a PDF (magic %PDF- absent)")
    if not available():
        raise PdfTextError(
            "pdftotext not found — install poppler-utils in the runtime image "
            "(Dockerfile runner stage)"
        )

    args = ["pdftotext", "-bbox-layout", "-enc", "UTF-8"]
    if first_page is not None or last_page is not None:
        start = int(first_page if first_page is not None else 1)
        end = int(last_page if last_page is not None else start)
        args += ["-f", str(start), "-l", str(end)]

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)
        proc = subprocess.run(  # noqa: S603 - 固定二进制 + 常量参数
            [*args, str(tmp_path), "-"],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PdfTextError(f"pdftotext -bbox-layout timed out after {timeout}s") from exc
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    if proc.returncode != 0:
        detail = _decode(proc.stderr).strip()[:200]
        raise PdfTextError(f"pdftotext -bbox-layout exited {proc.returncode}: {detail}")

    html = _decode(proc.stdout)
    # 页号必须带上：y 坐标每页重新起算，只按 y 聚行会把不同页的同一高度并成一行
    # （实测后果：66 页的备注被拼进同两条记录里）。
    out: list[tuple[int, float, float, float, float, str]] = []
    chunks = html.split("<page")
    for page_no, chunk in enumerate(chunks[1:], start=1):  # chunks[0] 是首个 <page> 之前
        for xmin, ymin, xmax, ymax, word in _WORD_RE.findall(chunk):
            out.append((page_no, float(xmin), float(ymin), float(xmax), float(ymax), word))
    return out
