"""中国银行业协会 中国银行业理财市场指数（月度）。

站点与端点（2026-11-08 实读）：

1. 栏目页 ``https://www.china-cba.net/Index/lists/catid/279.html``
   （理财市场指数报告）→ 4 个年度聚合详情页 ``/Index/show/catid/280/id/<id>.html``：
   2026 / 2025 / 2024 / 2022-2023。
2. 年度聚合详情页正文里是一串附件直链，形如::

       /Uploads/ueditor/file/20261012/6acc86244d7a6.pdf
       /Uploads/ueditor/file/20250519/682b42f9e8b56.docx

   **批次目录陷阱**：``/file/<YYYYMMDD>/`` 是**该批附件的上传日期**，不是报告期。
   实测 2026 页把 1–9 月共 9 期一次性放在 ``20261012/``（9 月单独在 ``20261019/``）；
   2025 页的 1–4 月在 ``20250519/``、5–6 月在 ``20250724/``……同一年跨多个批次目录，
   而同一批次目录又装多期。报告期只能从**锚文本**
   （``中国银行业理财市场指数报告（2026年9月）``）解析，绝不能从目录或文件名推断。
   另外 2022-2023 页把 ``2023年1月`` 列了两次（两次上传文件 sha256 相同），
   2024 页的 ``2024年12月`` 反而在 2025 年批次目录下——按报告期去重是必须的。

3. 2024-01 起附件为 **PDF**（``%PDF-`` 魔数，带完整文本层，``pdftotext -layout``
   可直接抽出指数表与正文），走共享层 ``fd_industry_data.pdftext``。
   2022-02…2023-12 为 **docx**（``PK`` 魔数），用 ``zipfile`` 读
   ``word/document.xml``，``xml.etree`` 按段落取 ``w:t``。

口径断层（实测，不是解析缺陷）：2022-02…2023-12 是普益「精选 60」旧模板
（现金管理产品周度七日年化 + 六个月/1 年投资周期**价格指数**），2024-01 起
改为「现金管理类/固收类产品**收益指数**」。两者基期与样本不同，故输出用
``methodology`` 列区分，绝不把 104.91 与 111.93 当成同一序列。

入口参数 ``period`` 用于把 golden 钉在历史月份（月更数据不能做长期锚）。
"""
from __future__ import annotations

import html as html_mod
import io
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone

from fd_industry_data import pdftext

BASE = "https://www.china-cba.net"
LIST_URL = BASE + "/Index/lists/catid/279.html"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": LIST_URL,
}
SOURCE = "cba-wealth-index"
SLEEP = 1.0  # 网络礼节：任意两次请求间隔 >= 1s

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_ANCHOR_RE = re.compile(r'<a\b[^>]*\bhref="([^"]*)"[^>]*>(.*?)</a>', re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_DETAIL_RE = re.compile(r"/Index/show/catid/280/id/(\d+)\.html")
_FILE_RE = re.compile(r"\.(pdf|docx)(?:$|\?)", re.I)
_PERIOD_RE = re.compile(r"^(\d{4})-(\d{2})$")
_PERIOD_TEXT_RE = re.compile(r"[（(]\s*(20\d{2})\s*年\s*(\d{1,2})\s*月\s*[）)]")
_BATCH_RE = re.compile(r"/file/(\d{8})/")
_NUM = r"([0-9]+(?:\.[0-9]+)?)"
_DIR = r"(上升|下降|下滑|降低|增长|上涨|回落|持平)"
_SIGN = {
    "上升": 1.0,
    "增长": 1.0,
    "上涨": 1.0,
    "下降": -1.0,
    "下滑": -1.0,
    "降低": -1.0,
    "回落": -1.0,
    "持平": 0.0,
}

NEW = "fixed_income_return_index"
OLD = "selected60_price_index"

SERIES_NAMES = {
    "cash_management": "全国银行理财市场现金管理类产品收益指数",
    "fixed_income": "全国银行理财市场固收类产品收益指数",
    "fixed_income_short": "全国银行理财市场短期固收类产品收益指数",
    "fixed_income_medium": "全国银行理财市场中期固收类产品收益指数",
    "fixed_income_long": "全国银行理财市场长期固收类产品收益指数",
    "selected60_cash": "全国精选60款现金管理产品当周七日年化收益率",
    "selected60_price_6m": (
        "全国精选60款非现金管理类中低风险六个月投资周期产品综合价格指数"
    ),
    "selected60_price_1y": (
        "全国精选60款非现金管理类中低风险1年投资周期产品综合价格指数"
    ),
}

TERM_SERIES = {
    "短期": "fixed_income_short",
    "中期": "fixed_income_medium",
    "长期": "fixed_income_long",
}

_last_request = 0.0


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _throttle() -> None:
    global _last_request
    wait = SLEEP - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _get(url: str, *, timeout: int = 90, attempts: int = 2) -> bytes:
    """GET 字节；每次请求前 throttle，瞬时错误重试一次。"""
    last: Exception | None = None
    for _ in range(attempts):
        _throttle()
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
            print(f"[{SOURCE}] GET 失败 {url}: {exc}", flush=True)
    raise RuntimeError(f"[{SOURCE}] GET failed: {url}: {last}")


def _get_text(url: str) -> str:
    return _get(url).decode("utf-8", "replace")


def _anchors(page: str):
    for match in _ANCHOR_RE.finditer(page):
        href = html_mod.unescape(match.group(1)).strip()
        text = html_mod.unescape(_TAG_RE.sub("", match.group(2)))
        text = re.sub(r"\s+", " ", text).strip()
        yield href, text


@dataclass(frozen=True)
class _Attachment:
    period: str
    url: str
    ext: str
    batch: str
    label: str


def _annual_pages() -> list[tuple[int, int, str]]:
    """栏目页 → [(min_year, max_year, detail_url)]，按最大年份倒序。"""
    page = _get_text(LIST_URL)
    found: dict[str, tuple[int, int]] = {}
    for href, text in _anchors(page):
        if not _DETAIL_RE.search(href):
            continue
        years = [int(y) for y in re.findall(r"(20\d{2})", text)]
        if not years:
            continue
        url = urllib.parse.urljoin(BASE, href)
        found[url] = (min(years), max(years))
    pages = [(lo, hi, url) for url, (lo, hi) in found.items()]
    pages.sort(key=lambda item: (-item[1], -item[0]))
    return pages


def _attachments(url: str) -> list[_Attachment]:
    page = _get_text(url)
    out: list[_Attachment] = []
    for href, text in _anchors(page):
        file_match = _FILE_RE.search(href)
        period_match = _PERIOD_TEXT_RE.search(text)
        if not file_match or not period_match:
            continue
        year, month = int(period_match.group(1)), int(period_match.group(2))
        if not 1 <= month <= 12:
            continue
        batch = _BATCH_RE.search(href)
        out.append(
            _Attachment(
                period=f"{year:04d}-{month:02d}",
                url=urllib.parse.urljoin(BASE, href),
                ext=file_match.group(1).lower(),
                batch=batch.group(1) if batch else "",
                label=text,
            )
        )
    return out


def _catalog(pages: list[tuple[int, int, str]], min_year: int, max_year: int) -> dict[str, _Attachment]:
    """年度页 → {period: 附件}，同期多次上传取批次日期最新的一份。"""
    catalog: dict[str, _Attachment] = {}
    for lo, hi, url in pages:
        if hi < min_year or lo > max_year:
            continue
        try:
            atts = _attachments(url)
        except RuntimeError as exc:
            print(f"[{SOURCE}] 年度页不可用 {url}: {exc}", flush=True)
            continue
        for att in atts:
            prev = catalog.get(att.period)
            if prev is None or att.batch > prev.batch:
                catalog[att.period] = att
    return catalog


def _previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _shift_months(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) - delta
    return index // 12, index % 12 + 1


def _walk(start: str, months: int, catalog: dict[str, _Attachment]) -> list[str]:
    year, month = int(start[:4]), int(start[5:7])
    out: list[str] = []
    guard = 0
    limit = months * 2 + 24
    while len(out) < months and guard < limit and year >= 2021:
        period = f"{year:04d}-{month:02d}"
        if period in catalog:
            out.append(period)
        year, month = _previous_month(year, month)
        guard += 1
    return out


def _document_text(payload: bytes) -> tuple[str, str]:
    """按魔数判型并抽文本，返回 (format, text)。"""
    if payload.startswith(b"%PDF"):
        return "pdf", pdftext.extract_text(payload, layout=True)
    if payload.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if "word/document.xml" not in archive.namelist():
                raise ValueError("zip 载荷缺少 word/document.xml")
            root = ET.fromstring(archive.read("word/document.xml"))
        body = root.find(W_NS + "body")
        node = body if body is not None else root
        lines: list[str] = []
        for para in node.iter(W_NS + "p"):
            text = "".join(t.text or "" for t in para.iter(W_NS + "t"))
            if text.strip():
                lines.append(text)
        return "docx", "\n".join(lines)
    raise ValueError(f"未知附件魔数：{payload[:8]!r}")


def _title_period(text: str) -> str:
    match = _PERIOD_TEXT_RE.search(text)
    if not match:
        return ""
    month = int(match.group(2))
    if not 1 <= month <= 12:
        return ""
    return f"{int(match.group(1)):04d}-{month:02d}"


def _norm(text: str) -> str:
    """去掉全部空白：pdftotext 在数字与汉字间插空格（``2026 年 9 月``、
    ``109.31 点``），正文正则统一在无空白串上匹配。"""
    return re.sub(r"[\s\u3000]+", "", text)


def _signed(direction: str, number: str) -> float:
    return round(_SIGN.get(direction, 1.0) * float(number), 10)


def _window(norm: str, end: int, size: int = 220) -> str:
    """从 end 起取一段，遇到图表/分期限/数据来源截断，防止串到下一节。"""
    chunk = norm[end : end + size]
    cut = len(chunk)
    for stop in ("图表", "数据来源", "分期限来看", "附录"):
        pos = chunk.find(stop)
        if pos != -1:
            cut = min(cut, pos)
    return chunk[:cut]


def _add(rows: list[dict], ctx: dict, series: str, metric: str,
         value: float | None, unit: str) -> None:
    if value is None:
        return
    rows.append(
        {
            "period": ctx["period"],
            "methodology": ctx["methodology"],
            "series": series,
            "series_name": SERIES_NAMES[series],
            "metric": metric,
            "value": value,
            "unit": unit,
            "file_format": ctx["file_format"],
            "source": SOURCE,
            "scraped_at": ctx["scraped_at"],
        }
    )


def _mom(segment: str, prefix: str) -> tuple[float | None, str | None]:
    """环比变化：返回 (带符号值, 单位)。``点`` 与 ``%`` 均可能出现在原文。"""
    match = re.search(prefix + _DIR + _NUM + r"(点|%)?", segment)
    if not match:
        return None, None
    return _signed(match.group(1), match.group(2)), (match.group(3) or "点")


def _pct(segment: str) -> float | None:
    """月度百分比变化：优先取「涨幅/降幅」，其次「增长/降低/下降/上升」措辞。"""
    for label in (r"(?:环比)?(?:涨幅|降幅)", r"(?:环比)?(?:增长|降低|下降|上升)"):
        match = re.search(label + _NUM + "%", segment)
        if match:
            raw = match.group(0)
            direction = -1.0 if ("降" in raw or "下降" in raw) else 1.0
            return round(direction * float(match.group(1)), 10)
    return None


def _base(segment: str, unit_hint: str = "点") -> tuple[float | None, str | None]:
    match = re.search(r"较基期(?:月|月均值|周|周均值)?" + _DIR + _NUM + r"(点|%)?", segment)
    if not match:
        return None, None
    return _signed(match.group(1), match.group(2)), (match.group(3) or unit_hint)


def _base_pct(segment: str) -> float | None:
    match = re.search(
        r"较基期(?:月|月均值|周|周均值)?[^。；图分]{0,80}?(增长|下降|降低|上升)" + _NUM + "%",
        segment,
    )
    if not match:
        return None
    return round(_signed(match.group(1), match.group(2)), 10)


def _cash_yield(norm: str) -> float | None:
    match = re.search(
        r"(?:平均近7日|平均近七日|平均七日|月均七日|当周七日)年化收益率为+" + _NUM + "%",
        norm,
    )
    return float(match.group(1)) if match else None


def _parse_index_block(rows: list[dict], ctx: dict, segment: str, series: str) -> None:
    change, unit = _mom(segment, r"(?:较上月)?环比")
    if change is not None and unit == "点":
        _add(rows, ctx, series, "mom_change_point", change, "点")
    # unit == "%" 时不落 mom_change_point：上游个别期把「环比上升 0.27%」写成
    # 百分比（2024-01 长期固收），把它当点数会污染序列；同句的「环比涨幅」
    # 才是可比的月度涨幅，由下面的 mom_change_pct 承接（README 已留痕）。
    _add(rows, ctx, series, "mom_change_pct", _pct(segment), "%")
    base, base_unit = _base(segment)
    _add(rows, ctx, series, "base_change_point", base, base_unit or "点")
    _add(rows, ctx, series, "base_change_pct", _base_pct(segment), "%")


def _parse_returns(rows: list[dict], ctx: dict, norm: str, term: str, series: str) -> None:
    """区间收益：全文里「三个月」带完整主语，「过去六个月」常省略主语，
    因此先定位三个月，再在同一句的后半段找六个月。"""
    prefix = "全国银行理财市场" + ("" if term == "固定收益类" else term) + "固收类产品"
    three = re.search(prefix + r"过去三个月投资收益为" + _NUM + "%", norm)
    if not three:
        return
    _add(rows, ctx, series, "return_3m_pct", float(three.group(1)), "%")
    window = norm[three.end(): three.end() + 120]
    six = re.search(r"过去六个月投资收益(?:为|达)" + _NUM + "%", window)
    if not six:
        six = re.search(prefix + r"过去六个月投资收益(?:为|达)" + _NUM + "%", norm)
    _add(rows, ctx, series, "return_6m_pct", float(six.group(1)) if six else None, "%")


def _parse_new(norm: str, ctx: dict) -> list[dict]:
    """2024-01 起模板：现金管理类 / 固收类（含短中长期）收益指数。"""
    rows: list[dict] = []

    match = re.search(r"现金管理类产品收益指数为" + _NUM + r"(?:点)?", norm)
    if match:
        segment = _window(norm, match.end())
        _add(rows, ctx, "cash_management", "index_point", float(match.group(1)), "点")
        _parse_index_block(rows, ctx, segment, "cash_management")
        _add(rows, ctx, "cash_management", "seven_day_annualized_pct", _cash_yield(norm), "%")

    match = re.search(r"全国银行理财市场固收类产品收益指数为" + _NUM + r"(?:点)?", norm)
    if match:
        segment = _window(norm, match.end(), 260)
        _add(rows, ctx, "fixed_income", "index_point", float(match.group(1)), "点")
        _parse_index_block(rows, ctx, segment, "fixed_income")
        _parse_returns(rows, ctx, norm, "固定收益类", "fixed_income")

    for term, series in TERM_SERIES.items():
        match = re.search(term + r"固收类产品收益指数为" + _NUM + r"(?:点)?", norm)
        if not match:
            continue
        end = len(norm)
        for stop in ("；", "。", "图表"):
            pos = norm.find(stop, match.end())
            if pos != -1:
                end = min(end, pos)
        _add(rows, ctx, series, "index_point", float(match.group(1)), "点")
        _parse_index_block(rows, ctx, norm[match.end():end], series)
        _parse_returns(rows, ctx, norm, term, series)

    return rows


def _parse_old(norm: str, ctx: dict) -> list[dict]:
    """2022-02…2023-12 旧模板：精选 60 现金管理周度收益 + 六个月/1 年价格指数。"""
    rows: list[dict] = []

    cash = re.search(
        r"现金管理产品(?:当周七日年化收益率平均值|七日年化收益率周均值)(?:为|达到?)"
        + _NUM + "%",
        norm,
    )
    if cash:
        _add(rows, ctx, "selected60_cash", "seven_day_annualized_pct",
             float(cash.group(1)), "%")
        segment = _window(norm, cash.end(), 160)
        mom_bp = re.search(r"环比" + _DIR + _NUM + "BP", segment)
        if mom_bp:
            _add(rows, ctx, "selected60_cash", "mom_change_bp",
                 _signed(mom_bp.group(1), mom_bp.group(2)), "BP")
        bp = re.search(
            r"较基期(?:周|周均值)?[^。；]{0,40}?" + _DIR + _NUM + "BP", segment
        )
        if bp:
            _add(rows, ctx, "selected60_cash", "base_change_bp",
                 _signed(bp.group(1), bp.group(2)), "BP")

    for needle, series in (
        (r"非现金管理类中低风险六个月投资周期产品综合价格指数", "selected60_price_6m"),
        (r"非现金管理类中低风险1年投资周期产品综合价格指数", "selected60_price_1y"),
    ):
        match = re.search(needle + r"为" + _NUM + r"点", norm)
        if not match:
            continue
        end = len(norm)
        for stop in ("。", "图表"):
            pos = norm.find(stop, match.end())
            if pos != -1:
                end = min(end, pos)
        segment = norm[match.end():end]
        _add(rows, ctx, series, "index_point", float(match.group(1)), "点")
        _parse_index_block(rows, ctx, segment, series)

    return rows


def _parse_document(text: str, ctx: dict) -> list[dict]:
    norm = _norm(text)
    rows = _parse_new(norm, ctx)
    if not any(row["metric"] == "index_point" for row in rows):
        rows = _parse_old(norm, ctx)
        if rows:
            ctx["methodology"] = OLD
            for row in rows:
                row["methodology"] = OLD
    return rows


def _period_bounds(start: str, months: int) -> tuple[int, int]:
    year, month = int(start[:4]), int(start[5:7])
    lo_year, _ = _shift_months(year, month, max(months - 1, 0))
    return lo_year, year


def run_cba_wealth_index(limit: int = 60, months: int = 1, period: str = "") -> list[dict]:
    """中国银行业理财市场指数长表（每（期, 口径, 序列, 指标）一行）。

    Args:
        limit: 返回行数上限（按期倒序后截断；新模板每期约 32 行）。
        months: 从起点期往回取几期已发布报告（``1`` = 只取一期）。
        period: 起点期 ``YYYY-MM``；空 = 取站点上最新一期（调度口径）。
            指定历史月份用于 golden 定月复算与历史回补（2022-02 起）。

    Raises:
        ValueError: ``period`` 不是 ``YYYY-MM`` 或月份越界。
        RuntimeError: 年度页/附件目录为空，或所有期都解析不出指数值。
    """
    if limit <= 0 or months <= 0:
        return []
    stamp = _now()
    if period:
        matched = _PERIOD_RE.match(period.strip())
        if not matched:
            raise ValueError(f"period 须为 YYYY-MM，收到 {period!r}")
        month = int(matched.group(2))
        if not 1 <= month <= 12:
            raise ValueError(f"period 月份越界：{period!r}")
        start = f"{int(matched.group(1)):04d}-{month:02d}"
        min_year, max_year = _period_bounds(start, int(months))
    else:
        start = ""
        min_year, max_year = 0, 9999

    pages = _annual_pages()
    if not pages:
        raise RuntimeError(f"{SOURCE}: 栏目页未解析到年度聚合页")

    catalog = _catalog(pages, min_year, max_year)
    if not catalog:
        raise RuntimeError(f"{SOURCE}: 年度聚合页未解析到任何附件")

    if not start:
        start = max(catalog)
    if start not in catalog:
        raise RuntimeError(
            f"{SOURCE}: {start} 不在已发布期列表中（最新 {max(catalog)}，"
            f"最早 {min(catalog)}）"
        )
    periods = _walk(start, int(months), catalog)
    print(
        f"[{SOURCE}] 期数 {len(periods)}：{periods[0] if periods else '-'} … "
        f"{periods[-1] if periods else '-'}（目录 {len(catalog)} 期）",
        flush=True,
    )

    rows: list[dict] = []
    for item in periods:
        attack = catalog[item]
        try:
            payload = _get(attack.url)
            file_format, text = _document_text(payload)
        except (RuntimeError, ValueError, zipfile.BadZipFile, ET.ParseError,
                pdftext.PdfTextError) as exc:
            print(f"[{SOURCE}] {item} 附件不可用（{attack.url}）：{exc}", flush=True)
            continue
        title_period = _title_period(text)
        if title_period and title_period != item:
            print(
                f"[{SOURCE}] {item} 期号交叉校验不一致：锚文本 {item} / 文档标题 "
                f"{title_period}（按锚文本入库）",
                flush=True,
            )
        ctx = {
            "period": item,
            "methodology": NEW,
            "file_format": file_format,
            "scraped_at": stamp,
        }
        parsed = _parse_document(text, ctx)
        if not parsed:
            print(f"[{SOURCE}] {item} 未解析出指数值（{file_format}，{len(text)} 字符）",
                  flush=True)
            continue
        rows.extend(parsed)
        print(
            f"[{SOURCE}] {item} {file_format} {ctx['methodology']}：{len(parsed)} 行",
            flush=True,
        )

    if not rows:
        raise RuntimeError(f"{SOURCE}: 全部期均未解析出指数值")
    return rows[: int(limit)]
