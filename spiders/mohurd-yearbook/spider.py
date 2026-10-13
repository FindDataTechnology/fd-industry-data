"""mohurd-yearbook — 住建部城市建设 / 城乡建设统计年鉴（历年时间序列）.

Source: 住房和城乡建设部 统计信息栏目
    https://www.mohurd.gov.cn/gongkai/fdzdgknr/sjfb/index.html

两个附件（实测 2026-10-13）：

  1. **城市建设统计年鉴.xls**（合并版，2,264,576 B，OLE2/BIFF，47 表）
     - 表名 ``1``..``11`` = **全国历年**序列（1981/1978–2025），列首为年份；
     - 表名 ``1-1``/``1-1-1`` 等 = 当年按省 / 按城市分列。
     本单元取**全国历年**表（``dataset="national"``）。

  2. **城乡建设统计年鉴.zip**（775,996 B，内 94 条目 / 90 个 ``.xls``）
     - ``01城市部分`` / ``02县城部分`` / ``03村镇部分``；
     - **村镇部分**（``3-1-*``/``3-2-*``）是普惠金融相关的关键口径：
       建制镇/乡/村庄的住宅、人口、市政公用设施与建设投入。
     本单元取历年表（``3-1-*``，``dataset="rural"``）。

**ZIP 内中文文件名需编码回退**：老打包工具不设 UTF-8 标志，GBK 名被按 CP437
解读成 ``2025─Ω│╟╧τ╜¿...``。共享层 ``tabular.unzip`` 已处理（实测还原为
``2025年城乡建设统计年鉴/03村镇部分/3-1-1  全国历年建制镇及住宅基本情况.xls``）。

**表头形态**：前置若干标题/单位/英文行，高度随表而异（实测 3–5 行，村镇表因
合并单元格可达 13 行）。本单元**不靠固定行号**——按「首列为 4 位年份」定位数据
起点，表头取数据起点之前最后一个非空行块，列名由该块各列文本拼接而成（含单位，
如 ``供水普及率（%）``）。

**单位**：从列名实读（表头自带 ``（%）``/``（万平方米）``/``（公里）`` 等），
不做单位假设。取不到单位记空串，**不猜**。

No auth, no captcha, no cookies. 直链下载，无频次对抗。
"""
from __future__ import annotations

import asyncio
import logging
import re
import urllib.request
from datetime import datetime, timezone

from fd_industry_data import tabular
from fd_industry_data.tabular import (
    Table,
    is_latin_heavy,
    squash_cjk,
    strip_latin_runs,
    unzip,
)

logger = logging.getLogger("mohurd-yearbook")

INDEX_URL = "https://www.mohurd.gov.cn/gongkai/fdzdgknr/sjfb/index.html"
BASE = "https://www.mohurd.gov.cn"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = 120

# 附件名 → 数据集（实测链接文本即「YYYY年城市建设统计年鉴」/「…城乡建设统计年鉴」）
_MERGED_RE = re.compile(r'href="([^"]+)"[^>]*>\s*(\d{4})年城市建设统计年鉴')
_RURAL_RE = re.compile(r'href="([^"]+)"[^>]*>\s*(\d{4})年城乡建设统计年鉴')

_YEAR_RE = re.compile(r"^\s*((?:19|20)\d{2})\s*$")
# 表名形态：合并版全国历年表 = 纯数字；村镇历年表 = 3-1-N
_NATIONAL_SHEET_RE = re.compile(r"^\d{1,2}$")
_RURAL_FILE_RE = re.compile(r"/(3-1-\d+)\s")

DATASETS = ("national", "rural")

# 单位提取：表头列名尾部的括号内容（中英文括号都认）
_UNIT_RE = re.compile(r"[（(]\s*([^（()）]{1,18}?)\s*[)）]\s*$")


def _decode(body: bytes) -> str:
    """页面解码（住建部站实测 UTF-8；回退 gb18030）。"""
    for enc in ("utf-8", "gb18030"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _squash(text: str) -> str:
    return re.sub(r"[\s\u00a0\u3000]+", " ", text or "").strip()


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
        return resp.read()


def _find_attachments(page_html: str) -> dict[str, tuple[str, str]]:
    """从栏目页取两个年鉴附件：``{"merged": (url, year), "rural": (url, year)}``。"""
    out: dict[str, tuple[str, str]] = {}
    m = _MERGED_RE.search(page_html)
    if m:
        out["merged"] = (BASE + m.group(1).replace("&amp;", "&"), m.group(2))
    m = _RURAL_RE.search(page_html)
    if m:
        out["rural"] = (BASE + m.group(1).replace("&amp;", "&"), m.group(2))
    return out


def _split_unit(label: str) -> tuple[str, str]:
    """列名 → (指标名, 单位)。

    单位优先取**中文括号**（``（%）``/``（平方米）``）；中文表头里的英文段
    （``Water Coverage Rate``/``sq.m``）先剥离，避免单位取成英文。
    """
    label = squash_cjk(strip_latin_runs(label))
    # 全角/半角括号都认；取**最后**一个括号（表头单位总在末尾）
    candidates = list(_UNIT_RE.finditer(label))
    if not candidates:
        return label, ""
    m = candidates[-1]
    unit = squash_cjk(m.group(1))
    name = label[: m.start()].strip()
    return (name or label), unit


def _column_labels(grid: list[list[str]], data_start: int) -> list[str]:
    """把数据起点之前的表头行按列拼接为列名（含单位）。

    只拼接**紧邻数据起点的连续非空块**——更早的行是标题/单位说明，不是列名。
    实测村镇表在标题与表头间有大量空行（合并单元格），故须从下往上找。

    中文年鉴的表头块常含**英文翻译行**（如 ``Water Coverage Rate``）：它按列
    与中文名重复，拼进去会得到「供水普及率（%） Water Coverage Rate」，且单位
    会取到英文的 ``sq.m``。故按列丢弃以拉丁字母为主的行，只留中文行。
    """
    if data_start <= 0:
        return []
    ncols = max((len(r) for r in grid[:data_start]), default=0)
    # 从 data_start-1 往上找连续非空行（至少一列有内容）
    block_end = data_start - 1
    while block_end >= 0 and not any(_squash(c) for c in grid[block_end]):
        block_end -= 1
    if block_end < 0:
        return []
    block_start = block_end
    while block_start - 1 >= 0 and any(_squash(c) for c in grid[block_start - 1]):
        block_start -= 1
    labels: list[str] = []
    for c in range(ncols):
        parts: list[str] = []
        for r in range(block_start, block_end + 1):
            raw = grid[r][c] if c < len(grid[r]) else ""
            if not _squash(raw):
                continue
            if is_latin_heavy(raw):
                continue  # 纯英文翻译行，与中文名同列重复
            cell = squash_cjk(strip_latin_runs(raw))
            if not cell or cell in parts:
                continue
            parts.append(cell)
        labels.append(" ".join(parts))
    # 首列通常只是「年份/Year」，统一为 年份 便于下游
    if labels and _YEAR_RE.match(grid[data_start][0] or ""):
        if re.search(r"年\s*份|Year", labels[0], re.IGNORECASE) or not labels[0]:
            labels[0] = "年份"
    return labels


def _melt(grid: list[list[str]], sheet: str, year: str, source_file: str,
          scraped_at: str) -> list[dict]:
    """一张「年份 × 指标」宽表 → 长行（每格一行）。

    数据起点按「首列为 4 位年份」定位；起点之后的年份列缺值不产行
    （**不填 0、不插值**）。数值原样保留为字符串，由下游按需转换。
    """
    data_start = None
    for i, row in enumerate(grid):
        if row and _YEAR_RE.match(row[0] or ""):
            data_start = i
            break
    if data_start is None:
        logger.warning("sheet %s: no year column found — skip", sheet)
        return []
    labels = _column_labels(grid, data_start)
    if len(labels) < 2:
        logger.warning("sheet %s: header too thin (%d cols) — skip", sheet, len(labels))
        return []

    rows: list[dict] = []
    for row in grid[data_start:]:
        if not row:
            continue
        m = _YEAR_RE.match(row[0] or "")
        if not m:
            continue  # 年份列非年份：注记行/空行
        y = m.group(1)
        for c in range(1, len(row)):
            value = _squash(row[c]) if c < len(row) else ""
            if not value:
                continue
            label = labels[c] if c < len(labels) else ""
            if not label:
                continue
            name, unit = _split_unit(label)
            rows.append({
                "dataset": "national" if _NATIONAL_SHEET_RE.match(sheet) else "rural",
                "table": sheet,
                "table_title": _squash(grid[0][0]) if grid and grid[0] else "",
                "year": y,
                "indicator": name,
                "unit": unit,
                "value": value,
                "yearbook_year": year,
                "source_file": source_file,
                "source": "mohurd-yearbook",
                "scraped_at": scraped_at,
            })
    return rows


async def _collect(limit: int, dataset: str) -> list[dict]:
    rows: list[dict] = []
    scraped_at = datetime.now(timezone.utc).isoformat()
    wanted = (dataset or "all").strip().lower()

    page = await asyncio.to_thread(_get, INDEX_URL)
    atts = _find_attachments(_decode(page))
    if not atts:
        logger.error("no yearbook attachments on index page — returning 0 rows")
        return rows
    logger.info("attachments: %s", {k: v[1] for k, v in atts.items()})

    async def _run_national() -> list[dict]:
        out: list[dict] = []
        if "merged" not in atts:
            logger.warning("merged yearbook link absent — skip national")
            return out
        url, year = atts["merged"]
        payload = await asyncio.to_thread(_get, url)
        fmt = tabular.detect(payload)
        logger.info("national yearbook: %d bytes, format=%s", len(payload), fmt.value)
        if fmt is not tabular.Format.XLS:
            logger.error("national yearbook is %s, expected xls — skip", fmt.value)
            return out
        for sn in tabular.sheets(payload):
            if not _NATIONAL_SHEET_RE.match(sn.strip()):
                continue  # 只取全国历年表（纯数字表名）
            try:
                grid = tabular.read_grid(payload, sheet=sn)
            except Exception as e:  # noqa: BLE001 — 单表失败跳过
                logger.warning("sheet %s read failed: %s — skip", sn, e)
                continue
            out.extend(_melt(grid, sn.strip(), year,
                             f"{year}年城市建设统计年鉴.xls", scraped_at))
        return out

    async def _run_rural() -> list[dict]:
        out: list[dict] = []
        if "rural" not in atts:
            logger.warning("rural yearbook link absent — skip rural")
            return out
        url, year = atts["rural"]
        payload = await asyncio.to_thread(_get, url)
        fmt = tabular.detect(payload)
        logger.info("rural yearbook: %d bytes, format=%s", len(payload), fmt.value)
        if fmt is not tabular.Format.ZIP:
            logger.error("rural yearbook is %s, expected zip — skip", fmt.value)
            return out
        entries = unzip(payload)
        targets = [e for e in entries if not e.is_dir
                   and e.name.endswith(".xls") and _RURAL_FILE_RE.search(e.name)]
        logger.info("rural: %d entries, %d 3-1-* files", len(entries), len(targets))
        for entry in sorted(targets, key=lambda e: e.name):
            m = _RURAL_FILE_RE.search(entry.name)
            table = m.group(1) if m else entry.name
            try:
                grid = tabular.read_grid(entry.data)
            except Exception as e:  # noqa: BLE001 — 单文件失败跳过
                logger.warning("rural file %s read failed: %s — skip", entry.name, e)
                continue
            out.extend(_melt(grid, table, year, entry.name.split("/")[-1], scraped_at))
        return out

    # 两个数据集各自完整取数后再**轮转交织**截断：顺序拼接会让 national 吃光
    # limit，使 all 模式下 rural 永远拿不到行（实测 limit=400 时 rural 为 0）。
    buckets: list[list[dict]] = []
    if wanted in ("all", "national"):
        buckets.append(await _run_national())
    if wanted in ("all", "rural"):
        buckets.append(await _run_rural())

    if len(buckets) == 1:
        rows = buckets[0][:limit]
    else:
        rows = []
        idx = 0
        while len(rows) < limit and any(idx < len(b) for b in buckets):
            for b in buckets:
                if idx < len(b):
                    rows.append(b[idx])
                    if len(rows) >= limit:
                        break
            idx += 1

    logger.info("mohurd-yearbook: %d rows (dataset=%s)", len(rows), wanted)
    return rows


def run_mohurd_yearbook(limit: int = 200, dataset: str = "all") -> list[dict]:
    """Entry point for fd-runner (spiders/mohurd-yearbook).

    Args:
        limit: maximum rows to return (hard cap).
        dataset: ``national``（城市建设年鉴·全国历年 11 表）/ ``rural``
            （城乡建设年鉴·村镇部分 3-1-* 历年表）/ ``all``（两者）。

    Returns:
        list of long-format rows (schema = manifest columns). Empty cells are
        skipped (no zero-fill, no interpolation); values stay strings.
    """
    try:
        from fd_industry_data.cancel_event import is_set as cancel_set
    except ImportError:  # standalone run outside the repo package
        def cancel_set() -> bool:
            return False

    ds = (dataset or "all").strip().lower()
    if ds not in DATASETS + ("all",):
        raise ValueError(f"unknown dataset {dataset!r}; valid: {DATASETS + ('all',)}")

    async def _run() -> list[dict]:
        if cancel_set():
            logger.info("cancel requested before start; returning empty")
            return []
        return await _collect(max(int(limit), 0), ds)

    return asyncio.run(_run())[: max(int(limit), 0)]


if __name__ == "__main__":
    for r in run_mohurd_yearbook(limit=5):
        print({k: (v[:60] + "…" if isinstance(v, str) and len(v) > 60 else v)
               for k, v in r.items()})
