"""spiders/nrdl-national-catalog —— 国家医保药品目录（年度版）条目取数。

来源形态：**官方 PDF 附件**（不是网页表格）。国家医保局每年以「关于印发《药品目录》…
（YYYY年）的通知」发布，附件 1 即目录全文，带文本层，可用共享
``fd_industry_data.pdftext`` 抽，无需 OCR。

    通知页    http://www.nhsa.gov.cn/art/2025/12/7/art_104_18970.html
    附件 1    /module/download/downfile.jsp?classid=0&filename=<md5>.pdf

为什么走 PDF 而不是页面/API（2026-11-11 实测，见 SOURCE-FEASIBILITY.md）：
NHSA 首页与文章页 200 可读，但目录条目级并没有网页表格或公开 API，唯一权威载体
就是这份 PDF；同期的 NMPA/CDE/ChiCTR 数据面则被 WAF 主动拦截（412/202/405）。
PDF 带文本层且结构规整，属于「可自动化」而非「人工导出」。

口径坑位（实测，逐一落实）：

- **凡例自带核对基准**：PDF 第 1 页凡例写明「西药部分 1446 个，中成药部分 1335 个
  （含民族药 95 个），协议期内谈判药品部分 472 个，共计 3253 个」。本单元把这三个数
  当**硬 golden**：解析出的唯一品种编号数必须与之相等，少一个就红。
- **★ 行是剂型变体，不是新品种**：``★(18) 兰索拉唑  注射剂`` 表示与编号 18 同品种的
  另一剂型。计数时只数唯一编号，入库时每行保留（剂型是独立事实）。
- **三个部分各自从 1 开始编号**，所以编号必须与 ``section`` 联合构成地址；单看编号会串。
- **中药饮片部分（892 个）按笔画排序、无编号无甲乙类**，与前三部分结构不同，本单元只
  输出其品种名并标 ``section=饮片``，不参与 3253 的计数口径。
- **备注（限定支付范围）会折行**：``限：1.反流性食管炎；2.与适当的抗生素联用…`` 在
  版面上跨多行，抽取时需把「非条目行」并入上一行的 note。
- 非价格面：本单元**只取**分类/剂型/甲乙类/限定范围，绝不取支付标准金额（用户裁决）。

工单：``openspec/changes/establish-pharma-domain-stack``（task 2.3）
"""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from fd_industry_data import pdftext

SOURCE = "nrdl-national-catalog"
NOTICE_URL = "http://www.nhsa.gov.cn/art/2025/12/7/art_104_18970.html"
CATALOG_YEAR = 2025
TIMEOUT = 60

# 凡例宣告的条目数 —— 解析结果的唯一硬判据（见模块文档）
EXPECTED = {"西药": 1446, "中成药": 1335}
# 首版**只解析**这两段：它们版面干净（甲乙类/编号/名称/剂型/备注）。
# 谈判药品段与饮片段显式排除，理由见 run_ 的返回说明与 SOURCE-FEASIBILITY.md：
#   - 谈判药品段内嵌「支付标准金额」列（如 ``4.52元(24µg/粒)``）与协议有效期列，
#     且药名跨 3 行折行；按文本切栏会把金额误当剂型——那是用户裁决的红线字段。
#     要正确接它必须做列坐标解析，留到后续 change，宁缺不脏。
#   - 饮片段按笔画排序、无编号无甲乙类，与前三部分不同构，且含「不予支付」排除名单。
PARSED_SECTIONS = frozenset({"西药", "中成药"})

SECTIONS = (
    ("西药", "二"),
    ("中成药", "三"),
    ("谈判药品", "四"),
    ("饮片", "五"),
)

# 目录行：``二、西药部分.....................8``
TOC_RE = re.compile(r"^\s*(?:二|三|四|五)、\s*(\S.*?)[.．\s]{3,}(\d+)\s*$")
_PAGE_FOOTER_RE = re.compile(r"^\s*第\s*(\d+)\s*页\s*$")
# 条目行：(甲乙类) (★(n) | n) 余下（名称/剂型/备注，用 2+ 空格分栏）。
# 实测两种版面塌陷：``乙1074 复方阿替卡因`` 无空格；``乙    240`` 名称被挤到下一行。
# 因此甲乙类与编号之间不强制空格，且名称允许为空（由紧随行回填）。
ENTRY_RE = re.compile(r"^\s*([甲乙])\s*(★\(\d+\)|\d+)\s*(.*\S)?\s*$")
_ATC_RE = re.compile(r"^\s*([A-Z]{2}\d{2}[A-Z]{0,2})\s+(\S.*)$")
_COLSPLIT_RE = re.compile(r"\s{2,}")
# 备注以「限」开头或以句号收尾；剂型则是「片剂/注射剂/…」这类短语
_NOTE_HINT = re.compile(r"^(限|支付|用于|仅限)")
_DOSAGE_END_RE = re.compile(r"(剂|片|胶囊|丸|散|膏|贴|栓|滴|液|乳|酊|气雾|喷雾|胶浆|洗剂|纱条|膜)$")
# 价格红线（用户裁决「价格/财报数据停爬」）：任何字段出现金额形态即视为越界，
# 单元直接失败而不是把金额当文字落库。匹配 ``元``/``¥``/``￥`` 与带小数的金额。
_PRICE_RE = re.compile(r"(\d+(\.\d+)?\s*元|¥|￥|支付标准|零售价|中标价)")



def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "fd-industry-data/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (固定 http 主机)
        return resp.read()


def find_attachment(notice_html: str) -> str:
    """从通知页取出附件 1（药品目录）的下载地址。

    通知页有两个附件（1 药品目录、2 商保创新药目录），附件列表顺序即编号顺序，
    取第 1 个；找不到即抛（不猜别的链接）。
    """
    links = re.findall(
        r'href="(/module/download/downfile\.jsp\?classid=0&(?:amp;)?filename=[0-9a-f]{32}\.pdf)"',
        notice_html,
    )
    if not links:
        raise RuntimeError("nrdl: 通知页未找到 PDF 附件链接")
    return "http://www.nhsa.gov.cn" + links[0].replace("&amp;", "&")


def _split_fields(rest: str) -> tuple[str, str | None, str | None]:
    """把「名称  剂型  备注」按 2+ 空格切栏，缺栏返回 None。

    版面里三列不一定都出现（无剂型、无备注都常见），因此按内容判定：
    以「限/支付/用于/仅限」开头或含句号的，是备注；其余当作剂型。
    """
    parts = [p.strip() for p in _COLSPLIT_RE.split(rest) if p.strip()]
    if not parts:
        return rest.strip(), None, None
    name = parts[0]
    dosage = note = None
    for part in parts[1:]:
        if note is None and (_NOTE_HINT.match(part) or "。" in part or "；" in part):
            note = part
        elif dosage is None:
            dosage = part
        elif note is None:
            note = part
        else:
            note = f"{note}{part}"
    return name, dosage, note


def parse_catalog_text(text: str, year: int = CATALOG_YEAR) -> list[dict]:
    """把 ``pdftotext -layout`` 的目录全文解析成条目行。

    分段靠**目录页区间**：正文里没有「二、西药部分」这类标题（只有开头目录有），
    因此先读目录得到各部分的起始页，再按正文里的「第 N 页」页脚推进当前页。
    实测页脚标在**页尾**，所以见到「第 N 页」后，其后内容属于第 N+1 页——这一页差
    正是首版解析每段少 8/16/80 条的原因（每段首页的条目被算进了上一段）。

    返回每行一条（★ 剂型变体各占一行）；``section`` 区分三个有编号部分与饮片。
    """
    lines = text.split("\n")

    # 1) 读目录：(部分名, 起始页)
    starts: list[tuple[int, str]] = []
    label_by_numeral = {numeral: label for label, numeral in SECTIONS}
    for line in lines[:120]:  # 目录在前 2 页内
        m = TOC_RE.match(line)
        if not m:
            continue
        numeral = line.strip()[0]
        label = label_by_numeral.get(numeral)
        if label:
            starts.append((int(m.group(2)), label))
    if not starts:
        raise RuntimeError("nrdl: 未在 PDF 开头找到目录页区间，无法切分部分")

    starts.sort()

    def section_at(page: int) -> str | None:
        current = None
        for start, label in starts:
            if page >= start:
                current = label
            else:
                break
        return current

    rows: list[dict] = []
    category: str | None = None
    page = 1
    section: str | None = None
    scraped = datetime.now(timezone.utc).isoformat()
    pending: dict | None = None  # 名称为空、等下一行回填的条目
    for raw in lines:
        line = raw.rstrip()
        footer = _PAGE_FOOTER_RE.match(line)
        if footer:
            # 页脚在页尾：其后的内容属于下一页
            page = int(footer.group(1)) + 1
            section = section_at(page)
            pending = None
            continue
        if not line.strip():
            continue
        m = ENTRY_RE.match(line)
        if m and section in PARSED_SECTIONS:
            ab_class, marker, rest = m.group(1), m.group(2), (m.group(3) or "").strip()
            if marker.startswith("★"):
                item_no = int(marker[2:-1])
                is_variant = True
            else:
                item_no = int(marker)
                is_variant = False
            drug_name, dosage, note = _split_fields(rest) if rest else ("", None, None)
            row = {
                "catalog_year": year,
                "section": section,
                "category": category,
                "class_ab": ab_class,
                "item_no": item_no,
                "is_variant": is_variant,
                "drug_name": drug_name or None,
                "dosage_form": dosage,
                "payment_scope": note,
                "source": SOURCE,
                "scraped_at": scraped,
            }
            rows.append(row)
            pending = row if not drug_name else None
            continue
        atc = _ATC_RE.match(line)
        if atc and section in PARSED_SECTIONS:
            category = f"{atc.group(1)} {atc.group(2).strip()}"
            pending = None
            continue
        if section not in PARSED_SECTIONS or not rows:
            continue
        cont = line.strip()
        if pending is not None:
            # 编号单独成行：紧随行承载名称（可能还带剂型）
            name, dosage, note = _split_fields(cont)
            pending["drug_name"] = name or None
            if dosage:
                pending["dosage_form"] = dosage
            if note:
                pending["payment_scope"] = note
            pending = None
            continue
        prev = rows[-1]
        if prev["section"] != section:
            continue
        # 折行：备注未收尾 → 并回上一条；否则若是剂型短语 → 补剂型
        if prev["payment_scope"] and not prev["payment_scope"].endswith("。"):
            prev["payment_scope"] = f"{prev['payment_scope']}{cont}"
        elif prev["dosage_form"] is None and _DOSAGE_END_RE.search(cont) and len(cont) <= 12:
            prev["dosage_form"] = cont
    return rows



def counts(rows: list[dict]) -> dict[str, int]:
    """按部分统计**唯一品种编号**数（★ 变体不重复计数）。"""
    out: dict[str, int] = {}
    for row in rows:
        out.setdefault(row["section"], set()).add(row["item_no"])  # type: ignore[arg-type]
    return {k: len(v) for k, v in out.items()}


def run_nrdl_national_catalog(limit: int = 0) -> list[dict]:
    """下载当年目录 PDF 并解析成条目行。

    两条硬判据任一不满足即抛（失败即红）：
    1. 各段唯一品种编号数必须等于官方凡例（``EXPECTED``）；
    2. **任何字段都不得含金额形态**（价格红线，见 ``_PRICE_RE``）。
    """
    if not pdftext.available():
        raise pdftext.PdfTextError("nrdl: 镜像缺 pdftotext（poppler-utils），无法抽取文本层")
    html = _fetch(NOTICE_URL).decode("utf-8", "replace")
    pdf_url = find_attachment(html)
    payload = _fetch(pdf_url)
    if not pdftext.is_pdf(payload):
        raise RuntimeError(f"nrdl: 附件不是 PDF（{pdf_url}）")
    text = pdftext.extract_text(payload)
    rows = parse_catalog_text(text)
    got = counts(rows)
    drift = {k: (v, got.get(k, 0)) for k, v in EXPECTED.items() if got.get(k, 0) != v}
    if drift:
        raise RuntimeError(
            "nrdl: 条目数与官方凡例不符（部分是 (官方, 解析)）: "
            + ", ".join(f"{k}={a}/{b}" for k, (a, b) in drift.items())
        )
    for row in rows:
        for field in ("drug_name", "dosage_form", "payment_scope", "category"):
            value = row.get(field)
            if isinstance(value, str) and _PRICE_RE.search(value):
                raise RuntimeError(
                    f"nrdl: 价格越界 —— {field} 含金额形态（section={row['section']}, "
                    f"item_no={row['item_no']}）：{value[:60]}"
                )
    return rows[:limit] if limit and limit > 0 else rows


if __name__ == "__main__":  # 冒烟：python3 spiders/nrdl-national-catalog/spider.py
    import json

    out = run_nrdl_national_catalog()
    print(json.dumps({"rows": len(out), "counts": counts(out)}, ensure_ascii=False, indent=2))
