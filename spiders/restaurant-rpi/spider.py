"""spiders/restaurant-rpi —— 美国全国餐馆协会（NRA）餐馆业绩指数（Restaurant Performance Index, RPI）。

数据表面（2026-11-07 实测；见 spiders/restaurant-rpi/README.md）：

- 官方页（工单给的 performance-index/ 旧链已 404，404 页导航给出现行页）：
  ``GET https://restaurant.org/research-and-media/research/restaurant-economic-insights/restaurant-performance-index/``
  （旧 economists-notebook 同名页与它同内容，双 200）
- 结构化数据 = 页内「Read the full RPI report」链接的月度 PDF（免鉴权）：
  ``https://restaurant.org/NRA/media/Research/RPI/2024/RPI-<Month>-<Year>.pdf``
  （2026-11-07 实测 RPI-September-2026.pdf，3 页，200 直连）
- PDF 第 3 页有数据表「Restaurant Performance Index and Its Components」：
  列 = 最近 5 个月（Sep-2026 版实测 2026-05…2026-09）+ 环比变化列（带 %，弃用，
  可由两月值导出）；行 = 11 条序列：RPI、Current Situation Index 及其 4 项指标
  （Same-Store Sales / Customer Traffic / Labor / Capital Expenditures）、
  Expectations Index 及其 4 项指标（Same-Store Sales / Staffing / Capital
  Expenditures / Business Conditions）。
  **单版只有这 5 个月滚动窗，无 2007 年以来全史**——不造史，取多少算多少。
  （同目录/URL 规律可回取旧版 PDF：``RPI-<Month>-<Year>.pdf``，目录段恒为
  ``2024``；2026-11-07 实测 Sep-2025…Sep-2026 共 13 版全部 200，
  RPI-January-2020 及更早 404。）
- **有界历史回补（``months_back``，缺省 0 = 只取当期）**：除当期报告外，按 URL
  规律再取往前 N 期月报（N ≤ 18 硬上限），把各版的 5 个月窗合并、按 ``month``
  去重后升序输出。每期只发**一次**请求，404/网络失败/解析不出表即跳过并在
  stderr 留痕，不重试、不判红；回补失败不影响当期数据。去重取舍：当期版优先，
  其余按「越新的报告版越优先」保留首次出现的值（``setdefault``）——新版权威，
  可覆盖旧版对最近月份的修订；``report_month``/``url`` 记录实际供值的版次。

口坑（实测逐一落实）：

- **PDF 文本流里数值被切块**：``100.7`` 常拆成 ``100.`` + ``7``，``2026`` 拆成
  ``20`` + ``2`` + ``6``——按 Tm 坐标聚类行、按 x 间距（<25pt）合并单元格后再解析。
- **同名双序列**：Same-Store Sales / Capital Expenditures 在 Current Situation 与
  Expectations 两个板块各一条，按「Current Situation Indicators: / Expectations
  Indicators:」分节标记区分，列名分别挂 ``cs_*`` / ``exp_*`` 前缀。
- **表页定位不写死页码**：逐个 FlateDecode 内容流试解析，认准「同时出齐 RPI +
  Current Situation Index + Expectations Index 三条序列」的流才是数据表（页 1/2
  图表页不满足该条件）。
- **环比列带 %**：数值单元格校验 ``-?\\d+(\\.\\d+)?`` 且不含 %，变化列自然被排除
  （另有 x 中心 30pt 归列阈值兜底）。
- **基期口径**：源文明确「以 100 为中性水平，>100 扩张、<100 收缩」（非旧版
  1995=100）。
- **行模型**：宽表——每月一行，11 条序列各一列；缺值 = None，不补 0。
- **limit 语义** = 返回行数上限：按 month 升序确定性排序后截取。
- **网络策略**：先直连；网络级失败再经代理 ``http://127.0.0.1:7890`` 重试一次
  （2026-11-07 实测直连即通，代理路径为兜底）。每 URL 至多两次尝试、30s 超时。
  历史回补例外：每期只发一次直连请求（不做代理重试），保证回补有界。
- **容错**：当期报告页面/PDF 两跳全失败或解析不出数据表 → 抛 ``RuntimeError``
  （失败即红）；历史回补各期失败只 stderr 留痕并跳过，不判红。
- **回补上限**：``months_back`` 超过 ``MAX_MONTHS_BACK``（18）或为负 → ``ValueError``。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
import zlib
from datetime import datetime, timezone
from urllib.parse import urljoin

SOURCE = "restaurant-rpi"
PAGE_URL = (
    "https://restaurant.org/research-and-media/research/"
    "restaurant-economic-insights/restaurant-performance-index/"
)
# 2026-11-07 实测页内现行报告（版本推进后由页面发现接管，此为兜底）
FALLBACK_PDF_URL = "https://restaurant.org/NRA/media/Research/RPI/2024/RPI-September-2026.pdf"
PROXY_URL = "http://127.0.0.1:7890"
# 历史月报 PDF 规律：目录段恒为 2024（CMS 惯例），文件名用版次月份全名
REPORT_PDF_TEMPLATE = "https://restaurant.org/NRA/media/Research/RPI/2024/RPI-{month}-{year}.pdf"
MAX_MONTHS_BACK = 18  # 回补硬上限：防止误传大数把单次运行变成 hammer

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html, application/pdf, */*",
}
TIMEOUT = 30  # 秒；单 URL 单次尝试（直连+代理兜底共至多两次）

_PDF_LINK_RE = re.compile(r'href="([^"]*/NRA/media/Research/RPI/[^"]*\.pdf[^"]*)"', re.IGNORECASE)

# ---------------------------------------------------------------- PDF 文本定位

_TOKEN_RE = re.compile(
    rb"(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+Tm"  # 1-6: Tm
    rb"|(-?[\d.]+)\s+(-?[\d.]+)\s+T[dD]"  # 7-8: Td/TD 相对定位
    rb"|\[((?:[^\[\]\\]|\\.)*)\]\s*TJ"  # 9: TJ 数组
    rb"|\(((?:\\.|[^\\()])*)\)\s*(?:Tj|')"  # 10: Tj / ' 字面串
    rb"|\bT\*\b",  # 换行（实测表页未用，兜底忽略）
    re.S,
)
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_YEAR_RE = re.compile(r"20\d{2}")
_MONTH_NAMES = (
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
# 表数据行的行标签 → 字段名或分节控制标记；精确匹配（规范化后），非前缀——
# 定义段（Definitions）里的同名词行不含数值格，天然被「无数值格即跳过」过滤掉
_ROW_LABELS = {
    "restaurant performance index": "rpi",
    "current situation index": "current_situation_index",
    "expectations index": "expectations_index",
    "current situation indicators": "_sec:cs",
    "expectations indicators": "_sec:exp",
    "customer traffic": "cs_customer_traffic",
    "labor": "cs_labor",
    "staffing": "exp_staffing",
    "business conditions": "exp_business_conditions",
}
# 分节后歧义标签（Same-Store Sales / Capital Expenditures 在两节各一条）：
# 按（当前分节, 规范化标签）落位到 cs_* / exp_* 字段
_DUAL = {
    "cs": {"same-store sales": "cs_same_store_sales",
           "capital expenditures": "cs_capital_expenditures"},
    "exp": {"same-store sales": "exp_same_store_sales",
            "capital expenditures": "exp_capital_expenditures"},
}
# 表数据列（宽表字段），顺序即 manifest columns 的数据列
FIELDS = (
    "rpi", "current_situation_index",
    "cs_same_store_sales", "cs_customer_traffic", "cs_labor", "cs_capital_expenditures",
    "expectations_index", "exp_same_store_sales", "exp_staffing",
    "exp_capital_expenditures", "exp_business_conditions",
)


def _unescape_pdf(b: bytes) -> str:
    """PDF 字面字符串转义还原（\\( \\) \\\\ 八进制等）。"""
    out = bytearray()
    i = 0
    simple = {0x28: 0x28, 0x29: 0x29, 0x5C: 0x5C, 0x6E: 0x0A, 0x72: 0x0D,
              0x74: 0x09, 0x62: 0x08, 0x66: 0x0C}
    while i < len(b):
        c = b[i]
        if c == 0x5C and i + 1 < len(b):
            n = b[i + 1]
            if n in simple:
                out.append(simple[n])
                i += 2
                continue
            if 0x30 <= n <= 0x37:  # 八进制转义
                j = i + 1
                digits = b""
                while j < len(b) and len(digits) < 3 and 0x30 <= b[j] <= 0x37:
                    digits += bytes([b[j]])
                    j += 1
                out.append(int(digits, 8) & 0xFF)
                i = j
                continue
            out.append(n)
            i += 2
            continue
        out.append(c)
        i += 1
    return out.decode("latin-1")


def _pdf_text_triples(stream: bytes) -> list[tuple[float, float, str]]:
    """从内容流取 (x, y, 文本) 三元组（Tm/Td 定位 + Tj/TJ 取字）。"""
    triples: list[tuple[float, float, str]] = []
    x = y = 0.0
    for m in _TOKEN_RE.finditer(stream):
        if m.group(1) is not None:  # Tm：e=x f=y
            x, y = float(m.group(5)), float(m.group(6))
        elif m.group(7) is not None:  # Td/TD：相对移动
            x, y = x + float(m.group(7)), y + float(m.group(8))
        elif m.group(9) is not None:  # TJ 数组（字距数字忽略）
            txt = "".join(
                _unescape_pdf(sm.group(1))
                for sm in re.finditer(rb"\(((?:\\.|[^\\()])*)\)", m.group(9))
            )
            if txt:
                triples.append((x, y, txt))
        elif m.group(10) is not None:  # Tj / '
            txt = _unescape_pdf(m.group(10))
            if txt:
                triples.append((x, y, txt))
    return triples


def _pdf_streams(data: bytes) -> list[bytes]:
    """解出全部 FlateDecode 内容流（解压失败的跳过）。"""
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        try:
            out.append(zlib.decompress(m.group(1)))
        except zlib.error:
            continue
    return out


def _cluster_rows(triples: list[tuple[float, float, str]]) -> list[list[tuple[float, str]]]:
    """按 y 聚类成行（容差 3pt），行内按 x 升序；行序 = y 降序（页顶在前）。"""
    ys = sorted({t[1] for t in triples}, reverse=True)
    bands: list[tuple[float, list[tuple[float, str]]]] = []
    for yy in ys:
        cells = [(t[0], t[2]) for t in triples if t[1] == yy]
        if bands and bands[-1][0] - yy <= 3.0:
            bands[-1][1].extend(cells)
        else:
            bands.append((yy, cells))
    return [sorted(cells, key=lambda c: c[0]) for _, cells in bands]


def _merge_cells(row: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """行内相邻切块按起点 x 间距（<25pt）合并为单元格。

    实测切块形态：``100.7`` → ``100.``(464) + ``7``(480)；``2026`` → ``20``+``2``+``6``；
    列间距 ≥60pt，25pt 阈值两侧余量充足。
    """
    cells: list[tuple[float, str]] = []
    for x, txt in row:
        if txt.strip() == "":
            continue
        if cells and x - cells[-1][0] < 25.0:
            cells[-1] = (cells[-1][0], cells[-1][1] + txt)
        else:
            cells.append((x, txt))
    return cells


def _norm_label(text: str) -> str:
    """行标签规范化：小写、连字符紧排（Same -Store → same-store）、空白收敛、去尾部冒号。"""
    s = re.sub(r"\s*-\s*", "-", text.strip().lower())
    return re.sub(r"\s+", " ", s).rstrip(":").strip()


def _parse_table_stream(stream: bytes) -> list[dict] | None:
    """在单个内容流里找 RPI 数据表；找到返回 [{month, <field>...}]，否则 None。

    认表标准（实测 Sep-2026 版布局，不写死页码/坐标）：
    - 某行 ≥2 个纯月份名单元格（月份列），其相邻行同 x 处有 20XX 年份格配对成列；
    - 表体同时出齐 rpi / current_situation_index / expectations_index 三条主干序列
      （页 1/2 图表页不满足）。
    """
    rows = [_merge_cells(r) for r in _cluster_rows(_pdf_text_triples(stream))]

    header: tuple[int, int, list[tuple[float, str]]] | None = None
    for i, row in enumerate(rows):
        month_cells = [(x, _MONTHS[t.strip().lower()])
                       for x, t in row if t.strip().lower() in _MONTHS]
        if len(month_cells) < 2:
            continue
        for j in range(max(0, i - 3), min(len(rows), i + 4)):
            if j == i:
                continue
            year_cells = [(x, t.strip()) for x, t in rows[j] if _YEAR_RE.fullmatch(t.strip())]
            if len(year_cells) < 2:
                continue
            cols = []
            for mx, mo in month_cells:
                nearest = min(year_cells, key=lambda yc: abs(yc[0] - mx))
                if abs(nearest[0] - mx) <= 25.0:
                    cols.append((mx, f"{nearest[1]}-{mo:02d}"))
            if len(cols) == len(month_cells):
                header = (i, j, cols)
                break
        if header is not None:
            break
    if header is None:
        return None

    _, year_row_idx, col_list = header
    col_list.sort(key=lambda c: c[0])
    # 表体 = 表头（月份行/年份行取更靠后者）之后的行（y 更小，即页面更靠下）
    body_rows = rows[max(header[0], year_row_idx) + 1:]

    label_limit = col_list[0][0] - 30.0
    series: dict[str, dict[str, float]] = {}
    section: str | None = None
    for row in body_rows:
        label_chunks = [t for x, t in row if x < label_limit]
        if not label_chunks:
            continue
        label = _norm_label(" ".join(label_chunks))
        target = _ROW_LABELS.get(label)
        if target is not None and target.startswith("_sec:"):
            # 分节标记行无数值格，必须先于「无数值格即跳过」处理，否则 section 永远设不上
            section = target.split(":", 1)[1]
            continue
        value_cells = [(x, t) for x, t in row
                       if x >= label_limit and _NUM_RE.fullmatch(t.replace(" ", ""))]
        if not value_cells:
            continue  # 注记行/定义段行无数值格，天然排除
        if target is not None:
            field = target
        else:  # 歧义标签（两节各一条的序列）按当前分节落位
            field = _DUAL.get(section or "", {}).get(label)
            if field is None:
                continue
        values: dict[str, float] = {}
        for x, t in value_cells:
            center = min(col_list, key=lambda c: abs(c[0] - x))
            if abs(center[0] - x) > 30.0:
                continue  # 环比列（带 % 的已被 _NUM_RE 排除，此处兜底）
            values[center[1]] = float(t.replace(" ", ""))
        if values:
            series[field] = values

    # 认表硬门槛：三条主干序列必须同时在
    if not {"rpi", "current_situation_index", "expectations_index"} <= set(series):
        return None

    months_sorted = sorted(m for _, m in col_list)
    return [
        {"month": month, **{f: series.get(f, {}).get(month) for f in FIELDS}}
        for month in months_sorted
    ]


def _parse_pdf(data: bytes) -> list[dict] | None:
    """逐个内容流试解析，返回数据表行；全失败返回 None。"""
    for stream in _pdf_streams(data):
        parsed = _parse_table_stream(stream)
        if parsed:
            return parsed
    return None


def _rows_from_parsed(parsed: list[dict], pdf_url: str, now: str) -> list[dict]:
    """把单版 PDF 的 5 个月窗转成宽表行（report_month = 该版窗口末月）。"""
    report_month = max(r["month"] for r in parsed)
    return [
        {
            "month": rec["month"],
            **{f: rec.get(f) for f in FIELDS},
            "report_month": report_month,
            "source": SOURCE,
            "url": pdf_url,
            "scraped_at": now,
        }
        for rec in sorted(parsed, key=lambda r: r["month"])
    ]


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """按自然月平移（delta 可为负），返回 ``(year, month)``。"""
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _report_pdf_url(year: int, month: int) -> str:
    """版次年月 → 历史月报 PDF URL（目录段恒为 2024，月份用英文全名）。"""
    return REPORT_PDF_TEMPLATE.format(month=_MONTH_NAMES[month], year=year)


def _is_consistent_window(months: list[str], edition: str) -> bool:
    """回补版窗口自洽校验：升序连续无缺月，且末月 = 版次月。

    实测 March-2026 版 PDF 表头把 ``January 2026`` 误印成 ``January 2025``
    （同窗口 Nov-2025…Mar-2026 中出现 2025-01），解析结果非连续。此类版次
    **整版跳过并留痕**，不猜年份、不造数；该月数据仍可由相邻版次窗口覆盖。
    """
    if not months or months[-1] != edition:
        return False
    for prev, cur in zip(months, months[1:]):
        step = _shift_month(int(prev[:4]), int(prev[5:7]), 1)
        if (int(cur[:4]), int(cur[5:7])) != step:
            return False
    return True


def _fetch_once(url: str) -> tuple[bytes | None, str | None]:
    """历史回补专用：只发一次直连请求；失败返回 ``(None, 失败摘要)``，不重试。"""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
            return resp.read(), None
    except Exception as exc:  # noqa: BLE001 - 回补失败只留痕，不判红
        return None, f"{type(exc).__name__}: {exc}"


def _collect(limit: int, months_back: int = 0) -> list[dict]:
    """页面发现当期 PDF → 可选回补历史月报 → 合并去重 → 升序截取。

    当期报告失败抛 ``RuntimeError``（失败即红）；历史回补各期失败只 stderr
    留痕并跳过，不影响当期数据。
    """
    pdf_url = _resolve_pdf_url()
    data = _fetch(pdf_url)
    parsed = _parse_pdf(data)
    if not parsed:
        raise RuntimeError(
            f"restaurant-rpi: PDF 中未解析出 RPI 数据表（{pdf_url}）——布局可能已变，"
            "请人工复核 spiders/restaurant-rpi/README.md 的解析规则"
        )

    now = datetime.now(timezone.utc).isoformat()
    by_month: dict[str, dict] = {}
    # 当期版先入：重叠月份保留当期（最新）版的值
    for row in _rows_from_parsed(parsed, pdf_url, now):
        by_month.setdefault(row["month"], row)

    skipped: list[str] = []
    if months_back > 0:
        anchor = max(r["month"] for r in parsed)
        anchor_year, anchor_month = int(anchor[:4]), int(anchor[5:7])
        for i in range(1, months_back + 1):
            year, month = _shift_month(anchor_year, anchor_month, -i)
            hist_url = _report_pdf_url(year, month)
            hist_data, failure = _fetch_once(hist_url)
            if hist_data is None:
                skipped.append(f"{year}-{month:02d}（{failure}）")
                continue
            hist_parsed = _parse_pdf(hist_data)
            if not hist_parsed:
                skipped.append(f"{year}-{month:02d}（PDF 中未解析出数据表）")
                continue
            edition = f"{year}-{month:02d}"
            hist_months = [r["month"] for r in hist_parsed]
            if not _is_consistent_window(hist_months, edition):
                skipped.append(
                    f"{edition}（窗口不自洽: {','.join(hist_months)}；"
                    "源表年份笔误/布局变化，整版跳过不造数）"
                )
                continue
            # 越新的版次越先到：setdefault 让重叠月份保留新版值
            for row in _rows_from_parsed(hist_parsed, hist_url, now):
                by_month.setdefault(row["month"], row)

    if skipped:
        # 回补缺口留痕（stderr 进运行日志），不重试、不判红、不造数
        print(
            f"{SOURCE}: 历史月报回补跳过（404/失败/无表，不造数）: "
            + ", ".join(skipped),
            file=sys.stderr,
        )

    return [by_month[m] for m in sorted(by_month)][:limit]


def _fetch(url: str) -> bytes:
    """GET 一个 https 资源返回原始字节：先直连，网络级失败经代理重试一次。"""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 https 主机)
            return resp.read()
    except Exception as direct_exc:  # noqa: BLE001 - 直连失败才走代理兜底
        proxy = urllib.request.ProxyHandler({"http": PROXY_URL, "https": PROXY_URL})
        try:
            with urllib.request.build_opener(proxy).open(req, timeout=TIMEOUT) as resp:  # noqa: S310
                return resp.read()
        except Exception as proxy_exc:  # noqa: BLE001
            raise RuntimeError(
                f"restaurant-rpi: 取数失败（{url}）— 直连: "
                f"{type(direct_exc).__name__}: {direct_exc}；代理: "
                f"{type(proxy_exc).__name__}: {proxy_exc}"
            ) from proxy_exc


def _resolve_pdf_url() -> str:
    """从官方页发现现行 RPI 报告 PDF 链接；发现失败落实测固定 URL。"""
    try:
        page = _fetch(PAGE_URL).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - 页面失败不致命，落固定 URL（该 URL 自身失败会红）
        return FALLBACK_PDF_URL
    match = _PDF_LINK_RE.search(page)
    if not match:
        return FALLBACK_PDF_URL
    return urljoin(PAGE_URL, match.group(1).replace("&amp;", "&"))


def run_restaurant_rpi(limit: int = 100, months_back: int = 0) -> list[dict]:
    """取 NRA 餐馆业绩指数（RPI）月度宽表行。

    Args:
        limit: 返回行数上限（按 ``month`` 升序确定性截取）。
        months_back: 有界历史回补期数，缺省 0 = 只取当期报告（最近 5 个月滚动窗，
            Sep-2026 版实测 2026-05…2026-09）。>0 时按期次 URL 规律再取往前
            ``months_back`` 期月报并合并去重（越新的版次优先）。上限
            ``MAX_MONTHS_BACK``（18）；单期 404/失败/解析不出表即跳过并 stderr
            留痕，不重试、不判红。

    Returns:
        list of dict：每月一行，11 条序列各一列（``rpi`` /
        ``current_situation_index`` / ``cs_*`` 4 列 / ``expectations_index`` /
        ``exp_*`` 4 列），缺值 = ``None``（不补 0）；另附 ``report_month``
        （实际供值版次的报告期 YYYY-MM）、``source`` / ``url``（该版 PDF）/
        ``scraped_at``。行按 ``month`` 升序。

    Raises:
        RuntimeError: 当期页面/PDF 失败，或当期 PDF 解析不出数据表（失败即红）。
        ValueError: ``limit`` / ``months_back`` 非法（``months_back`` 超出 0..18）。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be an int, got {limit!r}") from None
    try:
        months_back = int(months_back)
    except (TypeError, ValueError):
        raise ValueError(f"months_back must be an int, got {months_back!r}") from None
    if not 0 <= months_back <= MAX_MONTHS_BACK:
        raise ValueError(
            f"months_back must be within 0..{MAX_MONTHS_BACK}, got {months_back}"
        )
    if limit <= 0:
        return []
    return _collect(limit, months_back)


if __name__ == "__main__":  # 单页冒烟：python3 spiders/restaurant-rpi/spider.py
    print(json.dumps(run_restaurant_rpi(limit=6), ensure_ascii=False, indent=2))
