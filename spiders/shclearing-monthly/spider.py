"""上海清算所 债券业务月度统计报表（表四 期末托管 / 表五 投资者持有结构）。

端点（2026-11-08 从 ``/cpyyw/ywsj/tjyb/`` 页面 JS 实读）：

1. ``POST https://www.shclearing.com.cn/shchapp/web/sdocClient/getMaxDate``
   空 body → JSON 字符串 ``"2026-09-30"``（最新已发布月的月末日）。
2. ``POST https://www.shclearing.com.cn/shchapp/web/sdocClient/monthListPage``
   参数 ``title= & operTime=<YYYY-MM> & channelId=ff80808140a43ba80140a45b17320007
   & limit=10 & start=0`` → ``{"flag":"0","data":[...]}``，一期 7 条：
   汇总 xlsx（``上海清算所债券业务月度统计报表（YYYY年M月）.xlsx``）+
   表一…表五 PDF + 报表项目说明 PDF。
3. ``GET  https://www.shclearing.com.cn/shchapp/web/sattaClient/download/<attachmentId>``
   → 附件字节。**Content-Type 一律撒谎为 ``application/x-msdownload``**，必须按
   魔数判型：PDF ``%PDF-``、xlsx ``PK\\x03\\x04``。

三重门（实测）：

- 无 JSESSIONID（不先 GET 栏目页）**或**无 ``Referer`` → 302
  ``http://www.shclearing.com.cn/shchapp/pages/common/error.jsp``（空 body）；
- 先 GET ``/cpyyw/ywsj/tjyb/`` 建会话、每次请求带
  ``Referer: https://www.shclearing.com.cn/cpyyw/ywsj/tjyb/`` → 200 且附件可用；
- 附件响应头 ``Content-Disposition`` 带中文文件名，但类型仍不可信，故
  ``pdftext.is_pdf`` / ``tabular.detect`` 双魔数校验，校验失败即报错（绝不把
  error.jsp 的 HTML 当 PDF 入库）。

表结构（xlsx 与 PDF 同源同值）：

``表四 债券期末托管情况``  首列行标签，指标列 = 本月末 / 本月净增 / 本年净增，单位亿元。
``表五 主要券种投资者持有结构``  首列投资者行标签，列 = 超短期融资券 / 非公开定向
债务融资工具 / 短期融资券 / 中期票据 / 其他公司信用类债券 / 金融债券 / 同业存单 /
熊猫债 / 资产支持证券 / 标准化票据，单位亿元；``合计`` 行是各券种合计。

解析口径：同一期的汇总 xlsx（同一 sattaClient 三重门）含 ``表四``/``表五`` 工作表，
是原生行列结构，走共享层 ``fd_industry_data.tabular``；两份 PDF 照常下载并通过
``fd_industry_data.pdftext.is_pdf`` + ``extract_text(payload, layout=True)``
抽取文本做数值抽查（PDF 是多行表头 + 变宽空档，见 README「失败的尝试」）。
xlsx 缺失时才回退到 PDF 文本的「按空档切列」行级解析。
"""
from __future__ import annotations

import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

from fd_industry_data import pdftext, tabular

BASE = "https://www.shclearing.com.cn"
PAGE_URL = BASE + "/cpyyw/ywsj/tjyb/"
APP_ROOT = BASE + "/shchapp/web/"
MAXDATE_URL = APP_ROOT + "sdocClient/getMaxDate"
LIST_URL = APP_ROOT + "sdocClient/monthListPage"
DOWNLOAD_ROOT = APP_ROOT + "sattaClient/download/"
CHANNEL_ID = "ff80808140a43ba80140a45b17320007"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": PAGE_URL,
}
SLEEP = 1.2  # 网络礼节：请求间隔 ≥1s

TABLES: dict[str, tuple[str, str]] = {
    "04": ("表四 债券期末托管情况", "bond_type"),
    "05": ("表五 主要券种投资者持有结构", "investor_type"),
}
#: 表五列顺序（与上游 2014-12 起的固定券种顺序一致；xlsx 表头会校验）。
METRICS_05 = [
    "超短期融资券",
    "非公开定向债务融资工具",
    "短期融资券",
    "中期票据",
    "其他公司信用类债券",
    "金融债券",
    "同业存单",
    "熊猫债",
    "资产支持证券",
    "标准化票据",
]
METRICS_04 = ["本月末", "本月净增", "本年净增"]
UNIT = "亿元"

_SKIP_LABEL_PREFIX = ("注", "说明", "数据来源", "资料来源")
_DATA_ROW = re.compile(r"^(合计|[0-9]+[.、])")
_PERIOD_RE = re.compile(r"^(\d{4})-?(\d{2})$")
_GAP = re.compile(r"\s{2,}")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _opener() -> urllib.request.OpenerDirector:
    """带 cookie 罐的 opener：先 GET 栏目页拿到 JSESSIONID（三重门第一道）。"""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = list(HEADERS.items())
    with opener.open(PAGE_URL, timeout=60) as resp:
        resp.read()
    return opener


def _post(opener: urllib.request.OpenerDirector, url: str, data: dict[str, str],
          *, timeout: int = 90) -> bytes:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=HEADERS)
    with opener.open(req, timeout=timeout) as resp:
        return resp.read()


def _get(opener: urllib.request.OpenerDirector, url: str,
         *, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with opener.open(req, timeout=timeout) as resp:
        return resp.read()


def _max_month(opener: urllib.request.OpenerDirector) -> tuple[int, int]:
    """最新已发布月的 (年, 月)；空响应视为上游异常。"""
    raw = _post(opener, MAXDATE_URL, {})
    text = raw.decode("utf-8", "replace").strip().strip('"')
    matched = re.match(r"^(\d{4})-(\d{2})", text)
    if not matched:
        raise RuntimeError(f"getMaxDate 非 YYYY-MM 响应：{text[:80]!r}")
    return int(matched.group(1)), int(matched.group(2))


def _catalog(opener: urllib.request.OpenerDirector, year: int, month: int) -> list[dict]:
    """某年月报目录；未发布返回 ``[]``。"""
    raw = _post(opener, LIST_URL, {
        "title": "",
        "operTime": f"{year:04d}-{month:02d}",
        "channelId": CHANNEL_ID,
        "limit": "20",
        "start": "0",
    })
    text = raw.decode("utf-8", "replace").strip()
    if not text.startswith("{"):
        raise ValueError(f"monthListPage 非 JSON（{text[:80]!r}）")
    doc = json.loads(text)
    if str(doc.get("flag")) != "0":
        raise ValueError(f"monthListPage flag={doc.get('flag')!r}")
    data = doc.get("data") or []
    return [item for item in data if isinstance(item, dict)]


def _abs_url(rel: str) -> str:
    if rel.startswith("http://") or rel.startswith("https://"):
        return rel
    return APP_ROOT + rel.lstrip("/")


def _pick(items: list[dict]) -> dict[str, dict]:
    """从目录里挑出汇总 xlsx、表四、表五；缺任一表即报错。"""
    chosen: dict[str, dict] = {}
    for item in items:
        title = str(item.get("title") or "").strip()
        if title.startswith("表四"):
            chosen["04"] = item
        elif title.startswith("表五"):
            chosen["05"] = item
        elif title.startswith("上海清算所债券业务月度统计报表"):
            chosen["xlsx"] = item
    missing = [k for k in ("04", "05") if k not in chosen]
    if missing:
        raise RuntimeError(f"月报目录缺表 {missing}（共 {len(items)} 条）")
    return chosen


def _num(cell) -> float | None:
    text = str(cell if cell is not None else "").strip().replace(",", "").replace("\u00a0", "")
    if not text or text in {"-", "--", "—", "---", "…"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _clean_label(cell) -> str:
    text = re.sub(r"[\s\u3000]+", " ", str(cell if cell is not None else ""))
    return text.strip()


def _period_of(grid: list[list], fallback: str) -> str:
    for row in grid[:4]:
        for cell in row:
            text = str(cell if cell is not None else "").strip()
            if text.startswith("统计月份"):
                stamp = re.split(r"[:：]", text)[-1].strip()
                digits = "".join(ch for ch in stamp if ch.isdigit())
                if len(digits) >= 6:
                    return f"{digits[:4]}-{digits[4:6]}"
    return fallback


def _emit(table_code: str, period: str, label: str, metric: str,
          value: float | None, stamp: str) -> dict | None:
    if value is None or not label or not metric:
        return None
    return {
        "period": period,
        "table_code": table_code,
        "table_name": TABLES[table_code][0],
        "row_dimension": TABLES[table_code][1],
        "row_label": label,
        "metric": metric,
        "value": value,
        "unit": UNIT,
        "source": "shclearing-monthly",
        "scraped_at": stamp,
    }


def _data_start(grid: list[list]) -> int:
    for idx, row in enumerate(grid):
        first = _clean_label(row[1] if len(row) > 1 else "")
        if first and _DATA_ROW.match(first):
            return idx
    raise ValueError("未找到数据起始行")


def _table04(grid: list[list], period: str, stamp: str) -> list[dict]:
    start = _data_start(grid)
    header = grid[start - 1] if start else []
    metrics = [_clean_label(c) for c in header[2:]]
    metrics = [m for m in metrics if m]
    if not metrics:
        raise ValueError("表四未找到指标列")
    rows: list[dict] = []
    for raw in grid[start:]:
        label = _clean_label(raw[1] if len(raw) > 1 else "")
        if not label or label.startswith(_SKIP_LABEL_PREFIX):
            continue
        for j, metric in enumerate(metrics, start=2):
            row = _emit("04", period, label, metric, _num(raw[j] if j < len(raw) else None), stamp)
            if row:
                rows.append(row)
    return rows


def _table05_metrics(grid: list[list], start: int) -> list[str]:
    """两行表头（券种大类 + 券种子类）合并为列标签，前向填充大类。"""
    group_row = grid[start - 2] if start >= 2 else []
    sub_row = grid[start - 1] if start else []
    width = max(len(group_row), len(sub_row))
    labels: list[str] = []
    for j in range(2, width):
        group = _clean_label(group_row[j]) if j < len(group_row) else ""
        sub = _clean_label(sub_row[j]) if j < len(sub_row) else ""
        if not group and not sub:
            labels.append("")
        elif not sub:
            labels.append(group)
        elif not group or group in sub:
            labels.append(sub)
        else:
            labels.append(f"{group}·{sub}")
    return labels


def _table05(grid: list[list], period: str, stamp: str) -> list[dict]:
    start = _data_start(grid)
    metrics = _table05_metrics(grid, start)
    if not metrics or not any(metrics):
        raise ValueError("表五未找到指标列")
    rows: list[dict] = []
    for raw in grid[start:]:
        label = _clean_label(raw[1] if len(raw) > 1 else "")
        if not label or label.startswith(_SKIP_LABEL_PREFIX):
            continue
        for j, metric in enumerate(metrics, start=2):
            row = _emit("05", period, label, metric, _num(raw[j] if j < len(raw) else None), stamp)
            if row:
                rows.append(row)
    return rows


def _sheet_name(payload: bytes, code: str) -> str:
    names = tabular.sheets(payload)
    for name in names:
        if name.strip().startswith({"04": "表四", "05": "表五"}[code]):
            return name
    raise ValueError(f"xlsx 缺表{code}工作表（有 {names}）")


def _xlsx_rows(payload: bytes, period: str, stamp: str) -> list[dict]:
    if tabular.detect(payload) is not tabular.Format.XLSX:
        raise ValueError("汇总附件不是 xlsx（魔数不符）")
    grids = {code: tabular.read_grid(payload, sheet=_sheet_name(payload, code))
             for code in ("04", "05")}
    rows: list[dict] = []
    realised = _period_of(grids["04"], period)
    rows.extend(_table04(grids["04"], realised, stamp))
    rows.extend(_table05(grids["05"], realised, stamp))
    return rows


def _pdf_text(payload: bytes, code: str) -> str:
    """PDF 魔数校验 + 文本层抽取（三重门第三道的类型校验）。"""
    if not pdftext.is_pdf(payload):
        raise ValueError(f"表{code} 附件不是 PDF（魔数 %PDF- 缺失）")
    text = pdftext.extract_text(payload, layout=True)
    if not text.strip():
        raise ValueError(f"表{code} PDF 无文本层")
    return text


def _pdf_gap_rows(text: str, metrics: list[str], code: str,
                  period: str, stamp: str) -> list[dict]:
    """PDF 兜底：``-layout`` 文本按 2+ 空格切列，行级 (row_label/metric/value)。

    只在同期 xlsx 缺失时使用。表五表头跨多行且空档不等宽，无法稳定回推列名，
    故列序取上游固定券种顺序 ``METRICS_05``；值个数不符即留痕跳过。
    """
    rows: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        head = re.match(r"^\s*(合计|[0-9]+[.、][^\s]*)", line)
        if not head:
            continue
        parts = _GAP.split(line.strip(), maxsplit=1)
        if len(parts) != 2:
            continue
        label = _clean_label(parts[0])
        if not _DATA_ROW.match(label):
            continue
        values = [p.strip() for p in _GAP.split(parts[1].strip()) if p.strip()]
        if len(values) != len(metrics):
            print(f"[shclearing-monthly] 表{code} PDF 行 {label!r} 值 {len(values)} 个"
                  f"（期望 {len(metrics)}），跳过", flush=True)
            continue
        for metric, token in zip(metrics, values):
            row = _emit(code, period, label, metric, _num(token), stamp)
            if row:
                rows.append(row)
    return rows


def _pdf_crosscheck(payloads: dict[str, bytes], rows: list[dict]) -> None:
    """PDF 文本与 xlsx 行值抽查：锚点值必须能在 PDF 文本中找到。"""
    anchors = {
        "04": ("2.2 商业银行金融债", "本月末"),
        "05": ("2.存款类金融机构", "金融债券"),
    }
    for code, payload in payloads.items():
        try:
            text = _pdf_text(payload, code)
        except (ValueError, pdftext.PdfTextError) as exc:
            print(f"[shclearing-monthly] 表{code} PDF 校验失败：{exc}", flush=True)
            continue
        label, metric = anchors[code]
        hit = [r for r in rows if r["table_code"] == code
               and r["row_label"] == label and r["metric"] == metric]
        if not hit:
            continue
        needle = f"{hit[0]['value']:,.2f}"
        if needle not in text:
            print(f"[shclearing-monthly] 表{code} PDF 与 xlsx 不一致："
                  f"{label}/{metric}={needle} 未出现在 PDF 文本", flush=True)


def _download(opener: urllib.request.OpenerDirector, item: dict, kind: str) -> bytes:
    rel = item.get("downloadPdfUrl") if kind == "pdf" else item.get("downloadExcelUrl")
    if not rel:
        raise ValueError(f"{item.get('title')!r} 无 {kind} 附件链接")
    payload = _get(opener, _abs_url(str(rel)))
    if not payload:
        raise ValueError(f"{item.get('title')!r} 附件为空")
    return payload


def _previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _period_arg(period: str) -> tuple[int, int] | None:
    if not period:
        return None
    matched = _PERIOD_RE.match(period.strip())
    if not matched:
        raise ValueError(f"period 须为 YYYY-MM 或 YYYYMM，收到 {period!r}")
    year, month = int(matched.group(1)), int(matched.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"period 月份越界：{period!r}")
    return year, month


def run_shclearing_monthly(limit: int = 800, months: int = 1,
                           period: str = "") -> list[dict]:
    """上清所月报长表（每（期, 表, 行, 指标）一行），金额单位亿元。

    Args:
        limit: 返回行数上限（按期倒序截断）。
        months: 从起点期往回取几期已发布月报（``1`` = 只取一期）。
        period: 起点期 ``YYYY-MM`` / ``YYYYMM``；空 = ``getMaxDate`` 给的最新
            已发布月（调度口径）。给定历史月用于回补 2014-12 起各期与 golden
            定月复算。

    Raises:
        RuntimeError: 探测窗内没有含表四/表五的已发布月报。
        ValueError: ``period`` 非法或附件魔数/结构不符。
    """
    if limit <= 0 or months <= 0:
        return []
    stamp = _now()
    start = _period_arg(period)
    opener = _opener()
    year, month = start if start else _max_month(opener)
    time.sleep(SLEEP)

    rows: list[dict] = []
    attempts = 0
    max_attempts = int(months) + 4
    got = 0
    while got < int(months) and attempts < max_attempts:
        attempts += 1
        if year < 2014:
            break
        label = f"{year:04d}-{month:02d}"
        try:
            items = _catalog(opener, year, month)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            print(f"[shclearing-monthly] {label} 目录失败：{exc}", flush=True)
            items = []
        time.sleep(SLEEP)

        chosen = {}
        if items:
            try:
                chosen = _pick(items)
            except RuntimeError as exc:
                print(f"[shclearing-monthly] {label} {exc}", flush=True)
        if chosen:
            pdfs: dict[str, bytes] = {}
            for code in ("04", "05"):
                try:
                    pdfs[code] = _download(opener, chosen[code], "pdf")
                    time.sleep(SLEEP)
                except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                    print(f"[shclearing-monthly] {label} 表{code} PDF 下载失败：{exc}",
                          flush=True)
            period_rows: list[dict] = []
            if "xlsx" in chosen:
                try:
                    payload = _download(opener, chosen["xlsx"], "xlsx")
                    time.sleep(SLEEP)
                    period_rows = _xlsx_rows(payload, label, stamp)
                except (urllib.error.URLError, TimeoutError, ValueError,
                        tabular.TabularError) as exc:
                    print(f"[shclearing-monthly] {label} xlsx 解析失败：{exc}", flush=True)
            if not period_rows:
                print(f"[shclearing-monthly] {label} xlsx 不可用，回退 PDF 文本解析",
                      flush=True)
                for code, metrics in (("04", METRICS_04), ("05", METRICS_05)):
                    if code not in pdfs:
                        continue
                    try:
                        period_rows.extend(_pdf_gap_rows(
                            _pdf_text(pdfs[code], code), metrics, code, label, stamp))
                    except (ValueError, pdftext.PdfTextError) as exc:
                        print(f"[shclearing-monthly] {label} 表{code} PDF 解析失败：{exc}",
                              flush=True)
            if period_rows:
                _pdf_crosscheck(pdfs, period_rows)
                rows.extend(period_rows)
                got += 1
                print(f"[shclearing-monthly] {label} {len(period_rows)} 行", flush=True)
        else:
            print(f"[shclearing-monthly] {label} 未发布/无表四表五", flush=True)
        year, month = _previous_month(year, month)

    if not rows:
        raise RuntimeError("shclearing-monthly: 探测窗内无已发布月报")
    return rows[: int(limit)]
