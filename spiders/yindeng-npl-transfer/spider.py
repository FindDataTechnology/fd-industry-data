"""银登中心 不良贷款转让信息披露：挂牌公告（zrgg）+ 成交结果公告（zrjggg）。

两个列表都是**静态 CMS 分页**（无 JSON 接口、无登录）：

- ``GET https://www.yindeng.com.cn/ywzq/ywzq_bldkzr/bldkzr_xxpl/bldkzr_xxpl_zrgg/``
  转让公告（挂牌），首页 = ``index.html``，静态页 ``index_1.html`` … ``index_615.html``；
  页脚 ``createPageHTMLApp(616, 0, "index", "html")`` 给出总页数，**每页 10 条**。
  实测 616 页 ≈ 6,155 条（2021 试点起）。
- ``GET .../bldkzr_xxpl_zrjggg/`` 转让结果公告（成交），``index.html`` + ``index_1..302``，
  303 页 ≈ 3,030 条。实测 ``index_302``（最旧静态页）同样带文本层。

**页号方向（反直觉，实测确认）**：``index_K.html`` 是**自旧往新第 K 个静态页**
（``index_1`` = 最旧页，2021-02/03；``index_615``/``index_302`` = 最新静态页），
``index.html`` = 当前最新页（与最高号静态页内容不同，是更近的 10 条）。
静态旧页生成后不再重写（``index_1`` 页脚仍写 535 页而当前总量 616 页），
所以「自旧编号 + 新页追加在高号端」是稳定口径——这也是 ``start_page`` 能钉死
golden 页面的前提。

详情页 ``.../YYYYMM/tYYYYMMDD_<id>.html``：``发布时间：YYYY-MM-DD`` + 附件
``<a href='.../YYYYMM/P0202....pdf' title='转让公告.pdf'>``。PDF 是 ``%PDF-1.4``，
**带文本层**（2023 起几乎全部；2021/2022 部分为纯图片扫描件，见 README）。

PDF 文本层实测样貌（``-layout``，竖排水印「未经许可不得转载」逐字散落）::

    未
    浙商银行股份有限公司关于2026年第3期个人不良贷款
                                 经 许
      （信用卡透支）转让项目不良贷款转让公告      未
                                  转 载
                              不 得
未经交易基准日         20260813       未偿本金总额（元）           547,327,240.18
    资产笔数（笔）     38215          未偿利息总额（元）           170,636,937.70
    借款人户数（户）    37824          未偿本息总额（元）           717,964,177.88
    加权平均逾期天数    1440.03        其他费用（元）             0.00             许
...
未经项目编号                    NPL2025010165
    出让方全称                 内蒙古蒙商消费金融股份有限公司
    受让方全称                 广西联合资产管理股份有限公司
    转让协议签署日期              2025年12月25日

水印剔除规则（``_clean_lines``，README 有实测证据）：水印由 8 个汉字
「未经许可不得转载」构成，版面竖向排布后会退化成**纯水印字符的短 token**
（单字成行 / 字间空格），也可能**粘连**在标签或数值两端（``未经交易基准日``、
``0.00             许``、``未经项目编号``）。因此：

1. 逐行 ``split()``，丢弃「长度 ≤ 4 且每个字符都属于水印字符集」的 token
   （单字行、``经 许`` 这类被空格拆开的竖排字串即在此被清掉）；
2. 清洗后剩余 token 为空的行整行丢弃；
3. 粘连在标签前的残留（``未经项目编号``）不删——按标签做 ``search`` 即可命中
   （正则里也允许标签与值之间再夹最多 6 个水印字符）。

字段抽取（全部按标签正则，不依赖列坐标）：

- 挂牌（zrgg）：``交易基准日`` / ``资产笔数（笔）`` / ``借款人户数（户）`` /
  ``加权平均逾期天数`` / ``未偿本金总额（元）`` / ``未偿利息总额（元）`` /
  ``未偿本息总额（元）`` / ``五级分类情况`` / ``转让方式``。
  挂牌公告**没有**受让方（尚未成交），也没有 ``项目编号``（实测 2021–2026 样本均无）。
- 成交（zrjggg）：``项目编号``（``NPL`` + 10 位）/ ``出让方全称`` / ``受让方全称`` /
  ``转让协议签署日期``。
- 出让方在 zrgg 侧从标题前缀（``…关于…`` 之前的机构全称）取，README 注明来源差异。

**不伪造**：PDF 无文本层（扫描件）时只输出列表元数据（标题/日期/链接），
数值字段留 ``None``，``text_layer=false``，并打一行留痕；不写 0、不猜值。
"""
from __future__ import annotations

import html as _html
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from fd_industry_data import pdftext

BASE = "https://www.yindeng.com.cn"
SECTION = BASE + "/ywzq/ywzq_bldkzr/bldkzr_xxpl"

#: kind → (列表目录名, 中文名)。上游主键 = 目录名，不接受别名。
KINDS: dict[str, tuple[str, str]] = {
    "zrgg": ("bldkzr_xxpl_zrgg", "挂牌公告"),
    "zrjggg": ("bldkzr_xxpl_zrjggg", "成交结果公告"),
}
KIND_ORDER = ("zrgg", "zrjggg")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": SECTION + "/bldkzr_xxpl_zrgg/",
}
SLEEP = 1.0          # 网络礼节：任意两次请求间隔 ≥ 1s
TIMEOUT = 90
PAGE_SIZE = 10       # 实测每页 10 条（616 页 × 10 ≈ 6,155）

#: 竖排水印「未经许可不得转载」——按字拆成集合，用于 token / 行级剔除。
WATERMARK = "未经许可不得转载"
_WM = set(WATERMARK)

#: 输出列（顺序 = manifest columns 顺序）。
COLUMNS = (
    "notice_kind", "announcement_title", "publish_date", "notice_url", "pdf_url",
    "project_no", "transferor", "transferee", "signing_date", "base_date",
    "loan_count", "borrower_count", "overdue_days",
    "unpaid_principal", "unpaid_interest", "unpaid_total",
    "five_category", "transfer_method", "text_layer", "source", "scraped_at",
)

_BLOCK_RE = re.compile(r'<div class="rightListContent list-item">(.*?)</div>', re.S)
_LINK_RE = re.compile(r'class="hrefLink"[^>]*?href=\'([^\']+)\'[^>]*?title=\'([^\']*)\'', re.S)
_DATE_RE = re.compile(r'rightListTime data-time">\s*(\d{4}-\d{2}-\d{2})')
_TOTAL_RE = re.compile(r"createPageHTMLApp\((\d+),\s*(\d+)")
_PUB_RE = re.compile(r"发布时间[：:]\s*(\d{4}-\d{2}-\d{2})")
_PDF_RE = re.compile(r"href='([^']+\.pdf)'", re.I)

#: 判定「PDF 有文本层」的锚标签——命中任一即认为抽得动。
_TEXT_MARKERS = (
    "交易基准日", "资产笔数", "加权平均逾期天数", "未偿本金总额",
    "项目编号", "出让方全称", "受让方全称",
)

_LAST_REQUEST = [0.0]


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _fetch(url: str, *, timeout: int = TIMEOUT, retries: int = 1) -> bytes:
    """带 1s 间隔的 GET；网络错误重试 ``retries`` 次。"""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        wait = SLEEP - (time.monotonic() - _LAST_REQUEST[0])
        if wait > 0:
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(SLEEP)
        finally:
            _LAST_REQUEST[0] = time.monotonic()
    raise last_exc  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# 列表页
# --------------------------------------------------------------------------- #
def _list_url(kind: str, page: int) -> str:
    code = KINDS[kind][0]
    name = "index.html" if page == 0 else f"index_{page}.html"
    return f"{SECTION}/{code}/{name}"


def _page_items(kind: str, page: int) -> tuple[list[dict], int | None]:
    """某静态列表页的条目（href/title/date）+ 页脚总页数。"""
    raw = _fetch(_list_url(kind, page)).decode("utf-8", "replace")
    total_m = _TOTAL_RE.search(raw)
    total = int(total_m.group(1)) if total_m else None
    items: list[dict] = []
    for block in _BLOCK_RE.findall(raw):
        link = _LINK_RE.search(block)
        if not link:
            continue
        date_m = _DATE_RE.search(block)
        items.append({
            "url": link.group(1).strip(),
            "title": _html.unescape(link.group(2)).strip(),
            "date": date_m.group(1) if date_m else None,
        })
    return items, total


# --------------------------------------------------------------------------- #
# PDF 文本清洗与字段抽取
# --------------------------------------------------------------------------- #
def _is_watermark_token(token: str) -> bool:
    """纯水印 token：长度 ≤ 4 且字符全部属于「未经许可不得转载」。"""
    return 0 < len(token) <= 4 and all(ch in _WM for ch in token)


def _clean_lines(text: str) -> list[str]:
    """剔竖排水印：丢纯水印 token，丢清洗后为空的行。"""
    lines: list[str] = []
    for line in text.splitlines():
        tokens = [t for t in line.split() if not _is_watermark_token(t)]
        if not tokens:
            continue
        lines.append(" ".join(tokens))
    return lines


def _has_text_layer(lines: list[str]) -> bool:
    if not lines or len("".join(lines)) < 60:
        return False
    joined = "\n".join(lines)
    return any(marker in joined for marker in _TEXT_MARKERS)


def _label(text: str) -> str:
    """标签 → 正则；全/半角括号互认（上游两种写法都出现过）。"""
    out: list[str] = []
    for ch in text:
        if ch == "（":
            out.append("[（(]")
        elif ch == "）":
            out.append("[）)]")
        else:
            out.append(re.escape(ch))
    return "".join(out)


#: 标签与值之间允许夹最多 6 个水印字符（粘连形态：``项目编号`` 前面常有 ``未经``）。
_GAP = r"\s*(?:[未经许可不得转载]\s*){0,6}"


def _extract(lines: list[str], label: str, value: str) -> str | None:
    pattern = re.compile(_label(label) + _GAP + "(" + value + ")")
    for line in lines:
        matched = pattern.search(line)
        if matched:
            return matched.group(1)
    return None


def _rest_of_line(lines: list[str], label: str, limit: int = 80) -> str | None:
    """取标签之后的整段（五级分类/担保这类自由文本）。"""
    pattern = re.compile(_label(label) + r"\s+(.+)$")
    for line in lines:
        matched = pattern.search(line)
        if not matched:
            continue
        value = re.sub(r"\s+", " ", matched.group(1)).strip().strip(WATERMARK + " ")
        if value:
            return value[:limit]
    return None


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.replace(",", "").replace("，", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(value: str | None) -> int | None:
    number = _to_float(value)
    if number is None:
        return None
    return int(number) if float(number).is_integer() else None


def _iso_date8(value: str | None) -> str | None:
    if value and len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return None


def _iso_cn_date(value: str | None) -> str | None:
    if not value:
        return None
    matched = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", value)
    if matched:
        year, month, day = (int(g) for g in matched.groups())
        return f"{year:04d}-{month:02d}-{day:02d}"
    matched = re.match(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", value)
    if matched:
        year, month, day = (int(g) for g in matched.groups())
        return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def _title_transferor(title: str) -> str | None:
    """标题前缀（``…关于…`` 之前）即出让机构全称——zrgg 侧 PDF 无出让方字段。"""
    head = title.split("关于", 1)[0].strip()
    return head or None


def _fill_fields(row: dict, lines: list[str]) -> None:
    project_no = _extract(lines, "项目编号", r"NPL[0-9A-Za-z]{4,24}")
    transferor = _extract(lines, "出让方全称", r"\S{2,60}")
    transferee = _extract(lines, "受让方全称", r"\S{2,60}")
    signing = _extract(lines, "转让协议签署日期",
                       r"\d{4}年\d{1,2}月\d{1,2}日|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")
    base = _extract(lines, "交易基准日", r"\d{8}")
    method = _extract(lines, "转让方式", r"[：:]\s*([^\s，。;；]{2,20})")

    row["project_no"] = project_no
    if transferor:
        row["transferor"] = transferor.strip(WATERMARK + " ")
    row["transferee"] = transferee.strip(WATERMARK + " ") if transferee else None
    row["signing_date"] = _iso_cn_date(signing)
    row["base_date"] = _iso_date8(base)
    row["loan_count"] = _to_int(_extract(lines, "资产笔数（笔）", r"[\d,]+"))
    row["borrower_count"] = _to_int(_extract(lines, "借款人户数（户）", r"[\d,]+"))
    row["overdue_days"] = _to_float(_extract(lines, "加权平均逾期天数", r"[\d,]+(?:\.\d+)?"))
    row["unpaid_principal"] = _to_float(_extract(lines, "未偿本金总额（元）", r"[\d,]+(?:\.\d+)?"))
    row["unpaid_interest"] = _to_float(_extract(lines, "未偿利息总额（元）", r"[\d,]+(?:\.\d+)?"))
    row["unpaid_total"] = _to_float(_extract(lines, "未偿本息总额（元）", r"[\d,]+(?:\.\d+)?"))
    row["five_category"] = _rest_of_line(lines, "五级分类情况")
    row["transfer_method"] = method.lstrip("：: ") if method else None


# --------------------------------------------------------------------------- #
# 单条公告
# --------------------------------------------------------------------------- #
def _notice_row(kind: str, item: dict, stamp: str) -> dict:
    """列表元数据 → 详情页 → PDF → 文本层字段；任一环节失败保留已有元数据。"""
    row: dict = {name: None for name in COLUMNS}
    row.update({
        "notice_kind": kind,
        "announcement_title": item["title"],
        "publish_date": item["date"],
        "notice_url": item["url"],
        "transferor": _title_transferor(item["title"]),
        "text_layer": False,
        "source": "yindeng-npl-transfer",
        "scraped_at": stamp,
    })

    try:
        detail = _fetch(item["url"]).decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"[yindeng-npl-transfer] {kind} 详情页失败 {item['url'][-32:]}：{exc}", flush=True)
        return row
    publish = _PUB_RE.search(detail)
    if publish:
        row["publish_date"] = publish.group(1)
    pdf_match = _PDF_RE.search(detail)
    if not pdf_match:
        print(f"[yindeng-npl-transfer] {kind} 详情页无 PDF 附件 {item['url'][-32:]}", flush=True)
        return row
    row["pdf_url"] = pdf_match.group(1)

    try:
        payload = _fetch(row["pdf_url"])
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"[yindeng-npl-transfer] {kind} PDF 下载失败 {row['pdf_url'][-32:]}：{exc}", flush=True)
        return row

    if not pdftext.is_pdf(payload):
        print(f"[yindeng-npl-transfer] {kind} 附件非 PDF 魔数 {row['pdf_url'][-32:]}", flush=True)
        return row
    try:
        text = pdftext.extract_text(payload, layout=True)
    except pdftext.PdfTextError as exc:
        print(f"[yindeng-npl-transfer] {kind} PDF 文本抽取失败 {row['pdf_url'][-32:]}：{exc}", flush=True)
        return row

    lines = _clean_lines(text)
    row["text_layer"] = _has_text_layer(lines)
    if row["text_layer"]:
        _fill_fields(row, lines)
    else:
        print(
            f"[yindeng-npl-transfer] {kind} PDF 无文本层（清洗后 {len(''.join(lines))} 字）"
            f"，仅保留列表元数据 {item['url'].rsplit('/', 1)[-1]}",
            flush=True,
        )
    return row


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def _parse_kinds(kinds: str) -> list[str]:
    text = (kinds or "").strip().lower()
    if not text:
        return list(KIND_ORDER)
    requested: list[str] = []
    for token in re.split(r"[,\s]+", text):
        if not token:
            continue
        if token not in KINDS:
            raise ValueError(f"kinds 只接受 {sorted(KINDS)}，收到 {token!r}")
        if token not in requested:
            requested.append(token)
    return requested or list(KIND_ORDER)


def _crawl_kind(kind: str, limit: int, start_page: int) -> list[dict]:
    """单路列表：从 ``start_page`` 起向「更新」方向走，直到 ``limit`` 行。"""
    rows: list[dict] = []
    seen: set[str] = set()
    stamp = _now()

    def take(items: list[dict]) -> int:
        added = 0
        for item in items:
            if len(rows) >= limit:
                break
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            rows.append(_notice_row(kind, item, stamp))
            added += 1
        return added

    if start_page <= 0:
        try:
            items, total = _page_items(kind, 0)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"[yindeng-npl-transfer] {kind} 列表首页不可达：{exc}", flush=True)
            return rows
        take(items)
        page = (total or 1) - 1
        misses = 0
        while len(rows) < limit and page >= 1 and misses < 3:
            try:
                items, _ = _page_items(kind, page)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                print(f"[yindeng-npl-transfer] {kind} 列表页 index_{page} 失败：{exc}", flush=True)
                misses += 1
                page -= 1
                continue
            misses = 0
            take(items)
            page -= 1
        return rows

    # start_page > 0：自旧编号的静态页起，向更新方向（页号递增），最后补 index.html。
    page = start_page
    empty_streak = 0
    while len(rows) < limit and empty_streak < 2:
        try:
            items, _ = _page_items(kind, page)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"[yindeng-npl-transfer] {kind} 列表页 index_{page} 不存在/失败（{exc}）", flush=True)
            break
        if not items:
            empty_streak += 1
        else:
            empty_streak = 0 if take(items) else empty_streak + 1
        page += 1
    if len(rows) < limit:
        try:
            items, _ = _page_items(kind, 0)
            take(items)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"[yindeng-npl-transfer] {kind} 最新页补抓失败：{exc}", flush=True)
    return rows


def run_yindeng_npl_transfer(limit: int = 100, kinds: str = "",
                             start_page: int = 0) -> list[dict]:
    """银登中心不良贷款转让公告（挂牌 zrgg / 成交 zrjggg）逐条明细。

    Args:
        limit: **每路列表**返回行数上限（``kinds`` 为空时两路各取 ``limit``，
            合计最多 ``2×limit``；默认 100 → 至多 200 条 = 约 400 次请求）。
        kinds: 逗号/空格分隔的列表选择，``"zrgg"`` / ``"zrjggg"``；空 = 两路都抓。
        start_page: 起始静态列表页。``0`` = 当前最新页 ``index.html``（调度口径，
            自新往旧翻）；``N>0`` = 静态页 ``index_N.html``（自旧编号第 N 页，
            生成后内容不再变），自该页向更新方向翻至 ``index.html``——用于
            回补历史区间与 golden 定页复算。

    Returns:
        每行 = 一条公告（``notice_kind`` 区分挂牌/成交）。PDF 无文本层时只带
        列表元数据，数值字段为 ``None`` 且 ``text_layer=false``。

    Raises:
        ValueError: ``kinds`` 含未登记的列表名。
        RuntimeError: 两路都抓不到任何公告（首页不可达 / 上游改版）。
    """
    if limit <= 0:
        return []
    selected = _parse_kinds(kinds)
    errors: list[str] = []
    rows: list[dict] = []
    for kind in selected:
        try:
            kind_rows = _crawl_kind(kind, int(limit), max(0, int(start_page)))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            errors.append(f"{kind}: {exc}")
            print(f"[yindeng-npl-transfer] {kind} 整路失败：{exc}", flush=True)
            continue
        print(f"[yindeng-npl-transfer] {kind}（{KINDS[kind][1]}）{len(kind_rows)} 行", flush=True)
        rows.extend(kind_rows)
    if not rows:
        detail = "；".join(errors) or "两路列表均无条目"
        raise RuntimeError(f"yindeng-npl-transfer: 未取到任何公告（{detail}）")
    return rows
