"""NRA Restaurant Performance Index (RPI) spider.

Source: National Restaurant Association — Restaurant Performance Index
    https://restaurant.org/research-and-media/research/restaurant-performance-index/

The RPI is published as a monthly PDF (3 pages). Page 3 carries the
"Restaurant Performance Index and Its Components" table: the composite RPI,
the Current Situation Index and the Expectations Index, each with the last
~5 months of values plus a month-over-month percent change.

Text extraction strategy:
  1. Platform shared layer ``fd_industry_data.pdftext`` when available
     (pdftotext binary in the mirror) — same contract as cncotton /
     cba-wealth-index units: ``extract_text(payload, layout=True)``.
  2. A self-contained stdlib-only fallback extractor (below) for
     environments where the shared layer is absent. It handles FlateDecode
     content streams, WinAnsi simple fonts, Type0/Identity-H fonts with
     ToUnicode CMaps, and reconstructs layout lines by clustering text runs
     on the Tm baseline and ordering by x. No third-party dependency, no OCR.

No OCR is used anywhere: an image-only PDF yields an empty text layer and
the run fails loudly (never guessed). No anti-circumvention logic: a 403 /
bot wall surfaces as an error for the platform to handle.

Contract: ``run_nra_rpi(limit) -> list[dict]`` — newest period first.
"""
from __future__ import annotations

import datetime as _dt
import re
import urllib.error
import urllib.request
import zlib

try:  # 平台共享层优先（镜像环境）
    from fd_industry_data import pdftext  # type: ignore
except Exception:  # pragma: no cover - 本地/沙箱无共享层
    pdftext = None  # type: ignore[assignment]

# 落地页候选链：工单原路径已 404（2026-11-11 实测，页面迁移），逐个回退。
# 两个现行路径为同一页面（同 PDF 链接），任一可用即止。
_LANDING_CANDIDATES = (
    "https://restaurant.org/research-and-media/research/restaurant-performance-index/",
    "https://restaurant.org/research-and-media/research/restaurant-economic-insights/restaurant-performance-index/",
    "https://restaurant.org/research-and-media/research/economists-notebook/restaurant-performance-index/",
)
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 fd-industry-data/1.0"
)
_TIMEOUT = 60

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

# 复合指数行标签（去空格后匹配）。指标行（Same-Store Sales / Labor 等）不采集。
_INDEX_KEYS = (
    ("restaurantperformanceindex", "RPI"),
    ("currentsituationindex", "current_situation"),
    ("expectationsindex", "expectations"),
)

# 每月一行校验：RPI 与两分量均值之差超过 0.15 即视为解析错位（四舍五入容差）。
_RPI_MEAN_TOL = 0.15


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429) or 500 <= exc.code < 600:
                last_exc = exc
                continue  # 一次退避重试；不绕过任何封锁
            raise
        except (urllib.error.URLError, TimeoutError) as exc:
            last_exc = exc
            continue
    raise RuntimeError(f"download failed after retry: {url}: {last_exc}") from last_exc


def _find_pdf_url(html: str) -> str:
    links = re.findall(
        r'href=["\']([^"\']*?RPI-([A-Za-z]+)-(\d{4})\.pdf)["\']', html, re.I
    )
    if not links:
        raise RuntimeError(
            "no RPI-<Month>-<YYYY>.pdf link found on landing page (page layout may have changed)"
        )
    def key(m):
        month = _MONTHS.get(m[1].lower(), 0)
        return (int(m[2]), month)
    best = max(links, key=key)
    url = best[0]
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = "https://restaurant.org" + url
    elif not url.startswith("http"):
        url = "https://restaurant.org/research-and-media/research/restaurant-performance-index/" + url
    return url


# ---------------------------------------------------------------------------
# 内嵌 stdlib PDF 文本回退件（platform pdftext 不可用时使用；无第三方依赖）
# ---------------------------------------------------------------------------

_WINANSI_SPECIAL = {
    0x80: "\u20ac", 0x82: "\u201a", 0x83: "\u0192", 0x84: "\u201e",
    0x85: "\u2026", 0x86: "\u2020", 0x87: "\u2021", 0x88: "\u02c6",
    0x89: "\u2030", 0x8A: "\u0160", 0x8B: "\u2039", 0x8C: "\u0152",
    0x8E: "\u017d", 0x91: "\u2018", 0x92: "\u2019", 0x93: "\u201c",
    0x94: "\u201d", 0x95: "\u2022", 0x96: "\u2013", 0x97: "\u2014",
    0x98: "\u02dc", 0x99: "\u2122", 0x9A: "\u0161", 0x9B: "\u203a",
    0x9C: "\u0153", 0x9E: "\u017e", 0x9F: "\u0178",
}
_DELIMS = b"()<>[]{}%/ \t\r\n\x00"
_CHAR_W = 4.6  # 近似平均字符宽（pt），仅用于 -layout 列还原


class _PdfError(Exception):
    pass


def _pdf_winansi(raw: bytes) -> str:
    return "".join(_WINANSI_SPECIAL.get(b, chr(b)) for b in raw)


def _pdf_objects(data: bytes) -> dict:
    objs: dict = {}
    for m in re.finditer(rb"(?<![0-9])(\d+)\s+0\s+obj\b", data):
        num = int(m.group(1))
        end = data.find(b"endobj", m.end())
        if end >= 0:
            objs[num] = data[m.start():end]
    return objs


def _pdf_inflate(obj_body: bytes) -> bytes:
    i = obj_body.find(b"stream")
    if i < 0:
        raise _PdfError("object has no stream")
    j = i + len(b"stream")
    if obj_body[j:j + 2] == b"\r\n":
        j += 2
    elif obj_body[j:j + 1] in (b"\n", b"\r"):
        j += 1
    k = obj_body.find(b"endstream", j)
    if k < 0:
        raise _PdfError("stream not terminated")
    try:
        return zlib.decompress(obj_body[j:k])
    except Exception as exc:
        raise _PdfError(f"stream inflate failed: {exc}") from exc


def _pdf_parse_tounicode(text: bytes) -> dict:
    cmap: dict = {}

    def hex2str(h: bytes) -> str:
        s = h.decode()
        return "".join(chr(int(s[i:i + 4], 16)) for i in range(0, len(s) - len(s) % 4, 4))

    for m in re.finditer(rb"beginbfchar(.*?)endbfchar", text, re.S):
        for hm in re.finditer(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", m.group(1)):
            cmap[int(hm.group(1), 16)] = hex2str(hm.group(2))
    for m in re.finditer(rb"beginbfrange(.*?)endbfrange", text, re.S):
        for rm in re.finditer(
            rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]+>|\[[^\]]*\])",
            m.group(1), re.S,
        ):
            lo, hi = int(rm.group(1), 16), int(rm.group(2), 16)
            tgt = rm.group(3)
            if tgt.startswith(b"["):
                dsts = re.findall(rb"<([0-9A-Fa-f]+)>", tgt)
                for idx, src in enumerate(range(lo, hi + 1)):
                    if idx < len(dsts):
                        cmap[src] = hex2str(dsts[idx])
            else:
                base = int(tgt[1:-1], 16)
                for off in range(hi - lo + 1):
                    cmap[lo + off] = chr(base + off)
    return cmap


def _pdf_collect_fonts(objs: dict) -> dict:
    fonts: dict = {}
    for body in objs.values():
        flat = body.replace(b" ", b"").replace(b"\r", b"").replace(b"\n", b"")
        if b"/Type/Font" not in flat:
            continue
        name_m = re.search(rb"/Name\s*/(\S+)", body)
        if not name_m:
            continue
        info: dict = {"type0": False, "cmap": None}
        if b"/Subtype/Type0" in flat:
            info["type0"] = True
            tu_m = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", body)
            if tu_m and int(tu_m.group(1)) in objs:
                info["cmap"] = _pdf_parse_tounicode(_pdf_inflate(objs[int(tu_m.group(1))]))
        fonts[name_m.group(1).decode()] = info
    return fonts


def _pdf_decode(raw: bytes, font: dict) -> str:
    if font.get("type0"):
        cmap = font.get("cmap") or {}
        out = []
        for k in range(0, len(raw) - len(raw) % 2, 2):
            cid = (raw[k] << 8) | raw[k + 1]
            out.append(cmap.get(cid, ""))
        return "".join(out)
    return _pdf_winansi(raw)


_NUM_RE = re.compile(rb"^-?\d*\.?\d+$")


def _pdf_tokenize(data: bytes):
    i, n = 0, len(data)
    while i < n:
        c = data[i:i + 1]
        if c in b" \t\r\n\x00\f":
            i += 1
            continue
        if c == b"%":
            j = data.find(b"\n", i)
            i = n if j < 0 else j + 1
            continue
        if c == b"(":
            depth, j, out = 1, i + 1, bytearray()
            while j < n:
                ch = data[j:j + 1]
                if ch == b"\\":
                    nxt = data[j + 1:j + 2]
                    esc = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b",
                           b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\"}
                    if nxt in esc:
                        out += esc[nxt]
                        j += 2
                    elif nxt.isdigit():
                        om = re.match(rb"[0-7]{1,3}", data[j + 1:j + 4])
                        out.append(int(om.group(0), 8) & 0xFF)
                        j += 1 + len(om.group(0))
                    elif nxt == b"\n":
                        j += 2
                    else:
                        j += 1
                    continue
                if ch == b"(":
                    depth += 1
                elif ch == b")":
                    depth -= 1
                    if depth == 0:
                        break
                out += ch
                j += 1
            if depth != 0:
                raise _PdfError("unterminated literal string")
            yield ("str", bytes(out))
            i = j + 1
            continue
        if c == b"<":
            if data[i:i + 2] == b"<<":
                depth, j = 1, i + 2
                while j < n and depth:
                    if data[j:j + 2] == b"<<":
                        depth += 1
                        j += 2
                    elif data[j:j + 2] == b">>":
                        depth -= 1
                        j += 2
                    else:
                        j += 1
                i = j
                continue
            j = data.find(b">", i)
            if j < 0:
                raise _PdfError("unterminated hex string")
            hx = re.sub(rb"\s", b"", data[i + 1:j])
            try:
                yield ("str", bytes.fromhex(hx.decode()))
            except ValueError:
                yield ("str", b"")
            i = j + 1
            continue
        if c == b"/":
            j = i + 1
            while j < n and data[j:j + 1] not in _DELIMS:
                j += 1
            yield ("name", data[i + 1:j].decode("latin-1"))
            i = j
            continue
        if c == b"[":
            yield ("arr_open", None)
            i += 1
            continue
        if c == b"]":
            yield ("arr_close", None)
            i += 1
            continue
        j = i
        while j < n and data[j:j + 1] not in _DELIMS:
            j += 1
        word = data[i:j]
        if _NUM_RE.match(word):
            yield ("num", float(word))
        else:
            yield ("op", word.decode("latin-1"))
        i = j


def _pdf_run_content(content: bytes, fonts: dict):
    lines: dict = {}
    cur_font: dict = {"type0": False, "cmap": None}
    x = y = lead = 0.0
    stack: list = []
    arr: list | None = None

    def flush_tj(elements, array_mode: bool):
        nonlocal x
        seg_x = x
        produced = []
        for kind, val in elements:
            if kind == "str":
                txt = _pdf_decode(val, cur_font)
                if txt:
                    produced.append((seg_x, txt))
                    seg_x += len(txt) * _CHAR_W
            elif array_mode and kind == "num":
                if val <= -110:
                    seg_x += 2.4
        x = seg_x
        if produced:
            key = int(round(y / 2.0))
            lines.setdefault(key, []).extend(produced)

    for kind, val in _pdf_tokenize(content):
        if kind == "arr_open":
            arr = []
            continue
        if kind == "arr_close":
            if arr is not None:
                stack = arr
                arr = None
            continue
        if kind in ("num", "str", "name"):
            (arr if arr is not None else stack).append((kind, val))
            continue
        op = val
        if arr is not None:
            arr = None
            stack = []
            continue
        if op == "BT":
            x = y = 0.0
            stack = []
        elif op == "Tf":
            names = [v for k, v in stack if k == "name"]
            if names:
                cur_font = fonts.get(names[-1], cur_font)
            stack = []
        elif op == "Tm" and len(stack) >= 6:
            x, y = stack[-2][1], stack[-1][1]
            stack = []
        elif op in ("Td", "TD") and len(stack) >= 2:
            x += stack[-2][1]
            y += stack[-1][1]
            if op == "TD":
                lead = stack[-1][1]
            stack = []
        elif op == "TL" and stack:
            lead = stack[-1][1]
            stack = []
        elif op == "T*":
            y -= lead
            stack = []
        elif op == "TJ":
            flush_tj(stack, True)
            stack = []
        elif op in ("Tj", "'", '"'):
            flush_tj(stack, False)
            stack = []
            if op in ("'", '"'):
                y -= lead
        elif op in ("ET", "q", "Q", "cm", "gs", "BDC", "EMC", "BI"):
            stack = []
        elif len(stack) > 10:
            stack = stack[-10:]
    return [(-k * 2.0, v) for k, v in lines.items()]


def _pdf_extract_text(payload: bytes, layout: bool = True) -> str:
    if payload[:5] != b"%PDF-":
        raise _PdfError("not a PDF payload")
    objs = _pdf_objects(payload)
    if not objs:
        raise _PdfError("no objects found")
    fonts = _pdf_collect_fonts(objs)
    order: list = []
    for body in objs.values():
        if b"/Type/Pages" in body.replace(b" ", b""):
            kids = re.search(rb"/Kids\s*\[([^\]]*)\]", body)
            if kids:
                order.extend(int(n) for n in re.findall(rb"(\d+)\s+0\s+R", kids.group(1)))
    paged: dict = {}
    for num, body in objs.items():
        flat = body.replace(b" ", b"").replace(b"\r", b"").replace(b"\n", b"")
        if b"/Type/Page/" in flat and b"/Contents" in flat:
            paged[num] = body
    if not paged:
        raise _PdfError("no pages found")
    pages = [paged.pop(num) for num in order if num in paged]
    pages.extend(paged.values())
    lines: list = []
    for page_body in pages:
        cm = re.search(rb"/Contents\s+(\d+)\s+0\s+R", page_body)
        if not cm or int(cm.group(1)) not in objs:
            raise _PdfError("page content missing")
        lines.extend(_pdf_run_content(_pdf_inflate(objs[int(cm.group(1))]), fonts))
    lines.sort(key=lambda item: -item[0])
    out: list = []
    for _y, runs in lines:
        runs.sort(key=lambda r: r[0])
        if not layout:
            out.append("".join(t for _x, t in runs).strip())
            continue
        buf: list = []
        prev_end: float | None = None
        for x, text in runs:
            if prev_end is not None:
                gap = x - prev_end
                if gap > 2.6:
                    buf.append(" " * max(1, min(int(gap / _CHAR_W), 48)))
                elif gap > 0.6:
                    buf.append(" ")
            buf.append(text)
            prev_end = x + len(text) * _CHAR_W
        out.append("".join(buf).rstrip())
    return "\n".join(ln for ln in out if ln.strip())


def _extract_layout(payload: bytes) -> str:
    """平台 pdftext 优先，失败/缺失时回退内嵌实现。两者契约一致。"""
    if pdftext is not None:
        try:
            if getattr(pdftext, "available", lambda: True)() and pdftext.is_pdf(payload):
                return pdftext.extract_text(payload, layout=True)
        except pdftext.PdfTextError:
            pass  # 平台层解析失败 → 回退，不静默伪造
    return _pdf_extract_text(payload, layout=True)


# ---------------------------------------------------------------------------
# 表格解析：定位三个复合指数行 + 月份表头，输出 (period, index, value)
# ---------------------------------------------------------------------------

_VALUE_RE = re.compile(r"-?\d{2,3}\.\d(?=%?)")  # 整数部分 2-3 位；排除 0.1% 型变动列
_MONTH_WORD = (
    r"January|February|March|April|May|June|July|August|September|October|November|December"
)


def _parse_rows(text: str, source_url: str) -> list[dict]:
    lines = text.splitlines()

    # 月份表头行：≥5 个月份词，按出现顺序即 5 列（旧→新）
    month_cols: list[str] = []
    for ln in lines:
        found = re.findall(rf"\b({_MONTH_WORD})\b", ln)
        if len(found) >= 5:
            month_cols = found
            break
    if not month_cols:
        raise RuntimeError("month header row (5+ month names) not found in PDF text")

    # 指数行：label 去空格后匹配复合指数名，且含 ≥5 个数值 token
    series: dict[str, list[float]] = {}
    for ln in lines:
        flat = ln.replace(" ", "").lower()
        for key, name in _INDEX_KEYS:
            if key not in flat:
                continue
            vals = [float(v) for v in _VALUE_RE.findall(ln)]
            if len(vals) >= 5 and name not in series:
                series[name] = vals[:5]
            break
    missing = {"RPI", "current_situation", "expectations"} - set(series)
    if missing:
        raise RuntimeError(f"component index rows missing from PDF text: {sorted(missing)}")

    # 期次：月份序列的最后一列即报告期；年份从 RPI 文本/页脚取多数
    last_m = _MONTHS[month_cols[-1].lower()]
    year: int | None = None
    fm = re.search(rf"\|\s*({_MONTH_WORD})\s+(\d{{4}})", text)
    if fm and _MONTHS[fm.group(1).lower()] == last_m:
        year = int(fm.group(2))
    if year is None:
        years = re.findall(r"\b(20\d{2})\b", text)
        year = int(max(set(years), key=years.count)) if years else None
    if year is None:
        raise RuntimeError("report year not determinable from PDF text")

    periods: list[str] = []
    m, y = last_m, year
    for _ in month_cols:
        periods.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    periods.reverse()  # 旧→新，与 vals 对齐

    scraped = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows: list[dict] = []
    rpi, csi, emi = series["RPI"], series["current_situation"], series["expectations"]
    dropped: list[str] = []
    for i, period in enumerate(periods):
        # 交叉验证（防列错位）：RPI ≈ (CSI+EMI)/2
        if abs(rpi[i] - (csi[i] + emi[i]) / 2.0) > _RPI_MEAN_TOL:
            dropped.append(period)
            continue
        for name, val in (
            ("RPI", rpi[i]),
            ("current_situation", csi[i]),
            ("expectations", emi[i]),
        ):
            rows.append({
                "period": period,
                "index": name,
                "value": val,
                "unit": "index_level",
                "source_url": source_url,
                "scraped_at": scraped,
            })
    if not rows:
        raise RuntimeError(
            "all parsed months failed RPI=(CSI+EMI)/2 cross-validation — table layout likely changed"
        )
    if dropped:
        print(f"[nra-rpi] dropped months failing cross-validation: {dropped}", flush=True)
    rows.sort(key=lambda r: (r["period"], r["index"]), reverse=True)
    return rows


def run_nra_rpi(limit=None):
    """抓取 NRA Restaurant Performance Index 月度 PDF，返回复合指数长表。

    limit: 最多返回的行数（新期次在前）；None/0 → 全部。
    """
    html = None
    last_err: Exception | None = None
    for landing in _LANDING_CANDIDATES:
        try:
            html = _http_get(landing).decode("utf-8", "replace")
            break
        except Exception as exc:  # 404=页面迁移，试下一个候选
            last_err = exc
    if html is None:
        raise RuntimeError(f"all landing-page candidates failed: {last_err}")
    pdf_url = _find_pdf_url(html)
    payload = _http_get(pdf_url)
    if not payload.startswith(b"%PDF-"):
        raise RuntimeError(f"fetched payload is not a PDF: {pdf_url}")
    text = _extract_layout(payload)
    if not text.strip():
        raise RuntimeError(
            "PDF has no extractable text layer (image-only?); OCR is not used — refusing to guess"
        )
    rows = _parse_rows(text, pdf_url)
    if limit:
        rows = rows[: int(limit)]
    return rows
