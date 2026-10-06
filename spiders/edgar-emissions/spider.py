"""spiders/edgar-emissions —— EDGAR 全球碳排放（JRC 开放目录直链，按册取）。

数据表面（公开 FTP-over-HTTP 目录，无鉴权、无频次对抗；工单 brief.notes「侦察簿 W3」）：

- 年度册： ``GET {BASE}/datasets/EDGAR_2026_GHG/IEA_EDGAR_CO2_1970_2025.zip``（~4.7 MB）
- 月度册： ``GET {BASE}/datasets/EDGAR_2026_GHG/IEA_EDGAR_CO2_m_1970_2025.zip``（~71 MB，重）
- 目录：   ``GET {BASE}/datasets/EDGAR_2026_GHG/``（v2026 目录；升版需换路径，见 README）

口坑（brief.notes / 2026-10-06 实测，逐一落实）：

- **册内不是 CSV 而是嵌套 XLSX**（v2026 起全改版；ZIP 里是 ``*.xlsx`` + readme）。
  XLSX 本质是 zip+XML，本单元用标准库 ``zipfile + xml.etree`` 流式解析（iterparse，
  逐行 clear，内存不随行数膨胀），共享字符串表（``t=\"s\"``）与内联字符串
  （``t=\"inlineStr\"``）两种单元格都处理；**csv 模块用不上（v2026 无 CSV 册）**。
- **文件较大按需取单册**：``book`` 参数二选一，默认轻的年度册；月度册 71 MB 仅显式指定时才取。
- 表结构（两册同构）：工作表 **IPCC 2006**；表头行 ``IPCC_annex / C_group_IM24_sh /
  Country_code_A3 / Name / ipcc_code_2006_for_standard_report /
  ipcc_code_2006_for_standard_report_name / Substance / fossil_bio`` + 年度册
  ``Y_1970..Y_2025`` 宽列 / 月度册 ``Year + Jan..Dec``；单位 Gg；前 9 行是元信息脚注。
- **行序即文件序**：行内年份升序融化（宽转长），到 ``limit`` 即停——同册同 limit 行集
  确定（golden 重放可复现；首行恒为 ABW 1970 等定版历史值）。
- 容错：单册下载/解析失败直接抛 ``RuntimeError``（每轮只取一册，失败即红，不静默空返回）。

工单：``reports/health-tickets/20261006-edgar-emissions-3e495c53.yaml``（``kind=generate``）
"""
from __future__ import annotations

import io
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone

SOURCE = "edgar-emissions"
BASE = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/EDGAR"
RELEASE = "EDGAR_2026_GHG"  # v2026 目录（brief 指定）；升版在此换路径

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

HEADERS = {"User-Agent": "Mozilla/5.0 Chrome/124.0"}
TIMEOUT = 300  # 秒；月度册 71 MB，留足下载时间（不重试、不加频次）

LABEL_COLS = [
    "IPCC_annex",
    "C_group_IM24_sh",
    "Country_code_A3",
    "Name",
    "ipcc_code_2006_for_standard_report",
    "ipcc_code_2006_for_standard_report_name",
    "Substance",
    "fossil_bio",
]
YEAR_COL_RE = re.compile(r"^Y_(\d{4})$")
MONTH_COLS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# 单册白名单：URL 与册内 xlsx 文件名均为 2026-10-06 实测（直链 200）。
BOOKS = {
    "co2_annual": {
        "url": f"{BASE}/datasets/{RELEASE}/IEA_EDGAR_CO2_1970_2025.zip",
        "xlsx": "IEA_EDGAR_CO2_1970_2025.xlsx",
        "monthly": False,
    },
    "co2_monthly": {
        "url": f"{BASE}/datasets/{RELEASE}/IEA_EDGAR_CO2_m_1970_2025.zip",
        "xlsx": "IEA_EDGAR_CO2_m_1970_2025.xlsx",
        "monthly": True,
    },
}


def _download(url: str) -> zipfile.ZipFile:
    """直链下载一册并打开为内存 ZIP（无鉴权；失败抛错，不静默）。"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
        payload = resp.read()
    try:
        return zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise RuntimeError(f"edgar-emissions: not a valid zip ({url}): {exc}") from exc


def _sheet_path(zin: zipfile.ZipFile, sheet_name: str) -> str:
    """按工作表名解析 xlsx 内的 sheet XML 路径（workbook.xml + rels，不猜文件名）。"""
    wb = ET.fromstring(zin.read("xl/workbook.xml"))
    rid = None
    for sheet in wb.iter(NS + "sheet"):
        if sheet.get("name") == sheet_name:
            rid = sheet.get(NS_R + "id")
            break
    if not rid:
        raise RuntimeError(f"edgar-emissions: workbook has no sheet {sheet_name!r}")
    rels = ET.fromstring(zin.read("xl/_rels/workbook.xml.rels"))
    for rel in rels:
        if rel.get("Id") == rid:
            target = rel.get("Target") or ""
            return ("xl/" + target) if not target.startswith("/") else target.lstrip("/")
    raise RuntimeError(f"edgar-emissions: no workbook rel for {rid!r}")


def _shared_strings(zin: zipfile.ZipFile) -> list[str]:
    """读共享字符串表（月度册用 ``t=\"s\"``；年度册无此表 → 空表）。"""
    if "xl/sharedStrings.xml" not in zin.namelist():
        return []
    tree = ET.fromstring(zin.read("xl/sharedStrings.xml"))
    return [
        "".join(t.text or "" for t in si.iter(NS + "t"))
        for si in tree.findall(NS + "si")
    ]


def _cell_text(cell, shared: list[str]):
    """单元格取值：shared/inline/纯数字三态；空单元格返回 ``None``。"""
    kind = cell.get("t")
    if kind == "s":
        v = cell.find(NS + "v")
        return shared[int(v.text)] if v is not None and v.text is not None else None
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.iter(NS + "t")) or None
    v = cell.find(NS + "v")
    return v.text if v is not None else None


def run_edgar_emissions(limit: int = 100, book: str = "co2_annual") -> list[dict]:
    """取单册 EDGAR 排放表并融化成 ``year/sector/country/emissions`` 长行（≤ ``limit``）。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    if limit <= 0:
        return []
    spec = BOOKS.get(book)
    if spec is None:
        raise ValueError(f"unknown book {book!r}; valid: {sorted(BOOKS)}")

    outer = _download(spec["url"])
    try:
        inner = zipfile.ZipFile(io.BytesIO(outer.read(spec["xlsx"])))
    except KeyError as exc:
        raise RuntimeError(f"edgar-emissions: {spec['xlsx']} not in zip: {exc}") from exc

    shared = _shared_strings(inner)
    path = _sheet_path(inner, "IPCC 2006")

    rows: list[dict] = []
    header: dict[str, int] = {}
    scraped_at = datetime.now(timezone.utc).isoformat()
    for _event, elem in ET.iterparse(inner.open(path), events=("end",)):
        if elem.tag != NS + "row":
            continue
        cells = [(_cell_text(c, shared)) for c in elem.iter(NS + "c")]
        elem.clear()
        if not header:
            # 表头行以 IPCC_annex 开头（前 9 行为元信息脚注）
            if cells and cells[0] == "IPCC_annex":
                header = {name: i for i, name in enumerate(cells) if name}
                missing = [c for c in LABEL_COLS + ["Year"] if c not in header] \
                    if spec["monthly"] else \
                    [c for c in LABEL_COLS if c not in header]
                if missing:
                    raise RuntimeError(
                        f"edgar-emissions: header missing columns {missing}")
            continue
        if not any(cells):
            continue
        try:
            meta = {col: cells[header[col]] for col in LABEL_COLS}
            year = int(float(cells[header["Year"]])) if spec["monthly"] else None
        except (ValueError, TypeError, IndexError):
            continue  # 非数据行（空行/汇总说明行），跳过留痕于行序外
        melted: list[tuple[int, int, float]]  # (year, month_or_0, value)，行内年份升序
        if spec["monthly"]:
            melted = []
            for mi, name in enumerate(MONTH_COLS, start=1):
                idx = header.get(name)
                raw = cells[idx] if idx is not None and idx < len(cells) else None
                if raw in (None, ""):
                    continue
                try:
                    melted.append((year, mi, float(raw)))
                except ValueError:
                    continue
        else:
            melted = []
            for idx, raw in enumerate(cells):
                name = header_name(header, idx) or ""
                m = YEAR_COL_RE.match(name)
                if m is None or raw in (None, ""):
                    continue
                try:
                    melted.append((int(m.group(1)), 0, float(raw)))
                except ValueError:
                    continue
            melted.sort(key=lambda t: t[0])
        for y, month, value in melted:
            rows.append({
                "book": book,
                "year": y,
                "month": month if spec["monthly"] else None,
                "country_code": meta["Country_code_A3"],
                "country_name": meta["Name"],
                "sector_code": meta["ipcc_code_2006_for_standard_report"],
                "sector": meta["ipcc_code_2006_for_standard_report_name"],
                "substance": meta["Substance"],
                "fossil_bio": meta["fossil_bio"],
                "emissions": value,
                "unit": "Gg",
                "url": spec["url"],
                "source": SOURCE,
                "scraped_at": scraped_at,
            })
            if len(rows) >= limit:
                return rows[:limit]
    if not rows:
        raise RuntimeError(f"edgar-emissions: book {book!r} parsed 0 rows (header "
                           f"found: {bool(header)})")
    return rows


def header_name(header: dict[str, int], index: int):
    """列号 → 表头名（年度册 ``Y_YYYY`` 列扫描用）；越界返回 ``None``。"""
    for name, i in header.items():
        if i == index:
            return name
    return None


if __name__ == "__main__":  # 单页冒烟：python3 spiders/edgar-emissions/spider.py
    print(json.dumps(run_edgar_emissions(limit=3), ensure_ascii=False, indent=2))
