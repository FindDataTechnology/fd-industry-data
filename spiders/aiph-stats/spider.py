"""aiph-stats — AIPH《International Statistics — Flowers and Plants》年鉴的
**分品种·分国别种植面积**抽取.

Source: AIPH (International Association of Horticultural Producers) / Union Fleurs
联合出版的年度统计年鉴，公开 XLSX 附件挂在
https://aiph.org/giic/international-statistical-yearbook/<年>-yearbook-data-links/
（首页 → GIIC → Statistical Yearbook → 「<年> Yearbook Data Links」）。

Live paths (measured 2026-11-08, UTC+8):
  1. GET  https://aiph.org/giic/international-statistical-yearbook/            (200, 248 KB)
        -> 页内列出各年版数据页链接 `<年>-yearbook-data-links/`；实测有 2022/2023/2024/2025。
           **按年份取最大**，不硬编码年份。
  2. GET  https://aiph.org/giic/international-statistical-yearbook/<年>-yearbook-data-links/   (200, 196 KB)
        -> 公开 XLSX 直链。**命名逐年版漂移**（实测）：
             2025: ISYB_2025_production.xlsx                （27 个工作表）
             2024: SYB_2024_chapter2_products-_pages.xlsx   （27 个工作表）
             2023: SYB_2023_chapter2_products-_pages.xlsx
           同页另有 Eurostat 贸易表与 Summary 小表 —— **本单元只取「分品种章节本」**，
           按语义挑选（含 chapter2/products/production，且不含 eurostat/summary/trade-only）。
  3. GET  XLSX（约 130–140 KB）
        !! **实测传输会被中途截断**：同一 URL 三次下载分别落到 119679/131982 字节，
           而 `content-length` 恒为 136164。截断的 xlsx 会是 `BadZipFile`。
           本单元因此：校验 `len(body) == content-length` → 不等则带 `Range: bytes=N-`
           续传补齐（实测 206 续传两次即对齐）→ 再 `zipfile` 验完整性 → 仍坏则整版跳过，
           **绝不部分解析**。

口径要点：
  * **只做种植面积，不做贸易**。同一年鉴的 Trade 表脚注写着
    `Source: Euroststat, provisional data 2024`（例：Roses 表 foot 行）——那是 Eurostat
    临时数据的二次发布，本仓既有 `eurostat-api` / `un-comtrade` 已是更干净的一手车道，
    重复接入只会在贸易板上造成双计。**面积是真正空白**：EU 以外的印度/中国/厄瓜多尔/
    哥伦比亚/肯尼亚的面积口径别处拿不到。
  * **每一行自带参考年**（`ref_year`），不同国家的年份可以差十几年：实测玫瑰花面积
    India=2023、China=2020、Ecuador=2024、**Kenya=2012**、Italy=2005。
    因此这不是时间序列，而是**跨截面 + 古董年份混装**。单元如实输出 `ref_year`，
    **绝不把年鉴年（2025）当成数据年**；下游画图必须按 `ref_year` 标注。
  * **表头列序逐表不同**（实测）：多数表为
    `Area in ha | Total | In the open | Under protection | Year`，
    但 Lilies 表是 `Area in ha | Total | Under protection | In the open | Year`，
    Christmas trees / Cut Foliage 只有 `Area in ha | Total | Year`。
    ⇒ 一律**按表头文字定位列**，不按固定列序取数。
  * **国名带脚注上标**（如 `Netherlands 3`、`Italy `），脚注正文在表尾
    （`1 As far as provided.` / `2 Including unspecified area` / `3 revised data` 等）。
    单元先收脚注索引，再剥掉国名尾部的同名数字；`country_raw` 保留原始串。
  * **上游自身有不一致**：Orchids 表 `Netherlands 3` 的 Total=80 但 Under protection=90
    （保护地大于合计）。单元**照录不对账**，不修正、不重算合计。
  * 数值缺失一律 None，**不填 0**；`Total` 表头带脚注上标（`Total 2`）表示含未分类面积，
    照录。

No auth, no cookies, no WAF (实测 200)。海外源：直连可达（本机实测无系统代理）。

Params: `editions` 控制回看几版（1=仅最新版）。年鉴为年度出版物，因此
**不声明 window_start / window_end**（首爬的 12 个月回补窗对本源无意义：一版即全年），
历史深度靠 `editions` 显式控制。
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import zipfile
from datetime import datetime, timezone

from scrapling.fetchers import FetcherSession

logger = logging.getLogger("aiph-stats")

INDEX_URL = "https://aiph.org/giic/international-statistical-yearbook/"
SOURCE = "aiph-stats"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 年鉴分品种章节本的语义挑选：含 chapter2/products/production，排除 eurostat/summary
_CANDIDATE_HINTS = ("chapter2", "products", "production")
_EXCLUDE_HINTS = ("eurostat", "summary")

# 工作表名 → (product 键, 展示名)。逐条人工映射，**不做名称猜测**
# 注意 'Chysanthemum Prod.' 是上游拼写错误，必须显式收录。
_PRODUCT_MAP: tuple[tuple[str, str, str], ...] = (
    ("roses", "rose", "Roses"),
    ("carnation", "carnation", "Carnations"),
    ("orchids", "orchid", "Orchids"),
    ("chrysanthemum", "chrysanthemum", "Chrysanthemums"),
    ("chysanthemum", "chrysanthemum", "Chrysanthemums"),
    ("lilies", "lily", "Lilies"),
    ("christmas trees", "christmas_tree", "Christmas trees"),
    ("cut foliage", "cut_foliage", "Cut foliage"),
    ("flowering plants", "flowering_plant", "Flowering plants"),
    ("green and foliage plants", "green_foliage_plant", "Green and foliage plants"),
)

_FOOTNOTE_RE = re.compile(r"^(\d)\s+\S")
_YEAR_RE = re.compile(r"^\s*(\d{4})\s*$")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _num(text: str) -> float | None:
    """解析面积数值；不可解析一律 None（绝不 0 填充）。"""
    raw = _norm(text).replace(",", "")
    if not raw or raw in {"-", "—", "n/a", "N/A", "na", ".."}:
        return None
    match = re.match(r"^-?\d+(?:\.\d+)?$", raw)
    if match is None:
        match = re.match(r"^-?\d+(?:\.\d+)?", raw)
        if match is None:
            return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _year(text: str) -> int | None:
    match = _YEAR_RE.match(text or "")
    return int(match.group(1)) if match else None


def _classify_sheet(sheet_name: str) -> tuple[str, str, str] | None:
    """工作表名 → (product 键, 展示名, 类别)；非面积生产表返回 None。

    类别只有 'area'（种植面积）；Trade 表与 Graph 表一律不接（见模块 docstring 的口径）。
    """
    low = _norm(sheet_name).lower()
    if "graph" in low or "trade" in low:
        return None
    stem = low
    for noise in ("production", "area"):
        stem = stem.replace(noise, " ")
    stem = _norm(stem)
    for needle, key, label in _PRODUCT_MAP:
        if stem.startswith(needle):
            return key, label, "area"
    logger.warning("unmapped production sheet %r — skip", sheet_name)
    return None


def _year_pages(html: str) -> list[tuple[int, str]]:
    """年鉴索引页 → [(年份, 数据页绝对 URL)]，按年份升序。"""
    found: dict[int, str] = {}
    for href, year in re.findall(
        r'href="(https://aiph\.org/giic/international-statistical-yearbook/(\d{4})-yearbook-data-links/)"',
        html,
    ):
        found[int(year)] = href
    return sorted(found.items())


def _pick_workbook(html: str) -> str | None:
    """年版数据页 → 分品种章节本 XLSX 直链（按语义挑选，不硬编码文件名）。"""
    candidates: list[str] = []
    for href in re.findall(r'href="(https?://[^"]+\.xlsx?)"', html, re.I):
        low = href.lower()
        if any(bad in low for bad in _EXCLUDE_HINTS):
            continue
        if any(hint in low for hint in _CANDIDATE_HINTS):
            candidates.append(href)
    if not candidates:
        return None
    # 同一文件可能被多个 <a> 引用；去重后取第一个（页面上即为该年章节本）
    seen: list[str] = []
    for url in candidates:
        if url not in seen:
            seen.append(url)
    return seen[0]


def _parse_area_sheet(
    grid: list[list[str]],
    product: str,
    product_label: str,
    yearbook: int,
    sheet_name: str,
    workbook_url: str,
    scraped_at: str,
) -> list[dict]:
    """一张生产表 → 面积行。按表头文字定位列，不按固定列序。"""
    header_idx: int | None = None
    col_total = col_open = col_protected = col_year = col_country = -1
    for i, row in enumerate(grid):
        cells = [_norm(c) for c in row]
        if any(c.lower().startswith("area in ha") for c in cells):
            header_idx = i
            for j, cell in enumerate(cells):
                low = cell.lower()
                if low.startswith("total"):
                    col_total = j
                elif low.startswith("in the open"):
                    col_open = j
                elif low.startswith("under protection"):
                    col_protected = j
                elif low.startswith("year"):
                    col_year = j
            if col_total >= 0:
                col_country = col_total - 1
                while col_country >= 0 and not cells[col_country]:
                    col_country -= 1
            break
    if header_idx is None or col_total < 0 or col_country < 0:
        logger.warning("no 'Area in ha' header in sheet %r — skip", sheet_name)
        return []

    body = grid[header_idx + 1 :]

    # 先收脚注索引（`1 As far as provided.` → {1}），再剥国名尾部同名数字
    footnote_ids: set[str] = set()
    for row in body:
        first = next((_norm(c) for c in row if _norm(c)), "")
        m = _FOOTNOTE_RE.match(first)
        if m:
            footnote_ids.add(m.group(1))

    def cell(row: list[str], idx: int) -> str:
        return row[idx] if 0 <= idx < len(row) else ""

    out: list[dict] = []
    for row in body:
        country_raw = _norm(cell(row, col_country))
        if not country_raw:
            continue
        if country_raw.lower().startswith("other important producing"):
            break  # 表尾说明行，其后无数据
        if _FOOTNOTE_RE.match(country_raw):
            continue
        total = _num(cell(row, col_total))
        ref_year = _year(cell(row, col_year)) if col_year >= 0 else None
        if total is None and ref_year is None:
            continue  # 说明行 / 空行
        country = country_raw
        for fid in footnote_ids:
            if country.endswith(" " + fid):
                country = country[: -(len(fid) + 1)].strip()
                break
        out.append(
            {
                "product": product,
                "product_label": product_label,
                "country": country,
                "country_raw": country_raw,
                "area_ha": total,
                "area_open_ha": _num(cell(row, col_open)) if col_open >= 0 else None,
                "area_protected_ha": _num(cell(row, col_protected)) if col_protected >= 0 else None,
                "ref_year": ref_year,
                "yearbook": yearbook,
                "source_sheet": sheet_name,
                "workbook_url": workbook_url,
                "source": SOURCE,
                "scraped_at": scraped_at,
            }
        )
    if not out:
        logger.warning("no area row parsed from sheet %r", sheet_name)
    return out


async def _fetch(session: FetcherSession, url: str):
    """单次 GET；异常/非 200 返回 None（记 warning，不重试风暴）。"""
    try:
        resp = await session.get(url, headers={"User-Agent": USER_AGENT})
    except Exception as e:  # noqa: BLE001 — per-URL log-and-skip
        logger.warning("fetch failed %s: %s — skip", url, e)
        return None
    if resp.status != 200:
        logger.warning("status=%s %s — skip", resp.status, url)
        return None
    return resp


def _html(resp) -> str:
    """Response → 页面文本。

    scrapling 的 `Response.text` 对 HTML 实测为空串（body 有 267 KB），
    因此统一走 body 解码；UTF-8 为主，gb18030 兜底。
    """
    body = getattr(resp, "body", None) or b""
    if isinstance(body, str):
        return body
    for enc in ("utf-8", "gb18030"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _declared_length(headers) -> int | None:
    if headers is None:
        return None
    try:
        raw = headers.get("content-length") or headers.get("Content-Length")
    except AttributeError:  # pragma: no cover - 防御字典式实现
        raw = None
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


async def _download_workbook(session: FetcherSession, url: str) -> list | None:
    """下载 XLSX 并保证完整：短收则按 Range 续传，最后用 zipfile 验完整性。

    实测该站会中途截断（119679/131982 vs content-length 136164），
    截断的 xlsx 是 BadZipFile —— 必须补齐后整体校验，绝不部分解析。
    返回：`(工作簿字节, 工作表名)` 或 None（失败）。
    """
    from fd_industry_data.tabular import xlsx as xlsx_mod

    chunk = b""
    for attempt in range(4):
        headers = {"User-Agent": USER_AGENT}
        if chunk:
            headers["Range"] = f"bytes={len(chunk)}-"
        try:
            resp = await session.get(url, headers=headers)
        except Exception as e:  # noqa: BLE001
            logger.warning("workbook fetch failed (attempt %d) %s: %s", attempt + 1, url, e)
            continue
        if resp.status not in (200, 206):
            logger.warning("workbook status=%s (attempt %d) %s", resp.status, attempt + 1, url)
            continue
        body = resp.body or b""
        if resp.status == 200 and attempt > 0 and chunk:
            # 服务器忽略了 Range，整包重发
            chunk = body
        else:
            chunk = chunk + body
        expected = _declared_length(resp.headers)
        if expected is None or len(chunk) >= expected:
            break
        logger.info(
            "workbook short read: %d/%d bytes (attempt %d) — resuming",
            len(chunk), expected, attempt + 1,
        )

    if not chunk:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(chunk)) as z:
            bad = z.testzip()
        if bad is not None:
            raise zipfile.BadZipFile(f"corrupt member {bad}")
        sheets = xlsx_mod.sheets(chunk)
    except Exception as e:  # noqa: BLE001 — BadZipFile / XlsxError / 任何坏包
        logger.warning("workbook unusable (%s) %s — skip edition", type(e).__name__, url)
        return None
    return [chunk, sheets]


async def _collect(limit: int, editions: int) -> list[dict]:
    rows: list[dict] = []
    scraped_at = datetime.now(timezone.utc).isoformat()

    async with FetcherSession(
        impersonate="chrome120",
        timeout=60,
        verify=False,
        # retries=2：整轮只有 3 个请求（索引页 + 年版页 + 工作簿），不构成重试风暴；
        # 实测该站会偶发 40s 卡在 9 KB（2026-11-08 复现一次），单次失败会让整轮零产出。
        retries=2,
    ) as session:
        index_resp = await _fetch(session, INDEX_URL)
        if index_resp is None:
            logger.error("yearbook index unavailable — return empty")
            return []
        pages = _year_pages(_html(index_resp))
        if not pages:
            logger.error("no <year>-yearbook-data-links link on index page — return empty")
            return []
        wanted = pages[-max(int(editions), 1) :]
        logger.info("editions selected: %s", [y for y, _ in wanted])

        seen: set[tuple[int, str, str]] = set()
        for yearbook, page_url in reversed(wanted):  # 新版优先，便于 limit 截断
            page_resp = await _fetch(session, page_url)
            if page_resp is None:
                continue
            wb_url = _pick_workbook(_html(page_resp))
            if wb_url is None:
                logger.warning("no product-chapter workbook on %s — skip edition", page_url)
                continue
            logger.info("yearbook %d workbook: %s", yearbook, wb_url)

            got = await _download_workbook(session, wb_url)
            if got is None:
                continue
            data, sheets = got
            for sheet_name in sheets:
                hint = _classify_sheet(sheet_name)
                if hint is None:
                    continue
                product, label, _kind = hint
                try:
                    grid = xlsx_read(data, sheet_name)
                except Exception as e:  # noqa: BLE001
                    logger.warning("sheet %r unreadable (%s) — skip", sheet_name, e)
                    continue
                for row in _parse_area_sheet(
                    grid, product, label, yearbook, sheet_name, wb_url, scraped_at
                ):
                    key = (row["yearbook"], row["product"], row["country"])
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(row)
                if len(rows) >= limit:
                    break
            if len(rows) >= limit:
                break

    rows.sort(
        key=lambda r: (r["product"], -(r["area_ha"] or 0.0), r["country"]),
    )
    logger.info("aiph-stats: %d area rows over %d edition(s)", len(rows), len(wanted))
    return rows


def xlsx_read(data: bytes, sheet: str) -> list[list[str]]:
    """薄封装：延后导入，避免模块加载期拉 tabular 依赖。"""
    from fd_industry_data.tabular import xlsx as xlsx_mod

    return xlsx_mod.read_grid(data, sheet)


def run_aiph_stats(limit: int = 2000, editions: int = 1) -> list[dict]:
    """Entry point for fd-runner (spiders/aiph-stats).

    Args:
        limit: maximum rows to return (newest edition first, then per product by
            area desc). One edition yields roughly 60–70 country rows; the
            default 2000 covers every edition still linked on the index page.
        editions: how many yearly editions to ingest, newest first (default 1 =
            the latest edition only). The index page currently links 2022–2025,
            so `editions=4` gives a four-edition panel.

    Returns:
        list of area-row dicts (schema = manifest columns). Missing values are
        None; nothing is zero-filled, averaged, or reconciled across columns.
        Every row carries its own `ref_year` — the data vintage differs per
        country (2005–2024 in the 2025 edition), so this is a cross-section, not
        a time series.
    """
    try:
        from fd_industry_data.cancel_event import is_set as cancel_set
    except ImportError:  # standalone run outside the repo package

        def cancel_set() -> bool:
            return False

    async def _run() -> list[dict]:
        if cancel_set():
            logger.info("cancel requested before start; returning empty")
            return []
        return await _collect(max(int(limit), 0), editions)

    return asyncio.run(_run())[: max(int(limit), 0)]


if __name__ == "__main__":
    for record in run_aiph_stats(limit=20):
        print(record)
