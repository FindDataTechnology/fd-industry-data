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
EXPECTED = {"西药": 1446, "中成药": 1335, "谈判药品": 472, "中药饮片": 892}
# 解析路径分两种版面：
#   - 西药 / 中成药：版面干净（甲乙类/编号/名称/剂型/备注），走 ``-layout`` 文本切栏；
#   - 谈判药品：六列混排且内嵌「医保支付标准」金额列，走 **bbox 列坐标**（见
#     change add-nrdl-negotiated-entries）。金额列整列丢弃，任何金额词落在其他列即失败。
#   - 饮片段：按笔画排序、无编号无甲乙类，含「不予支付」排除名单，本单元不解析。
TEXT_SECTIONS = frozenset({"西药", "中成药"})
PARSED_SECTIONS = TEXT_SECTIONS | {"谈判药品"}

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
        if m and section in TEXT_SECTIONS:
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
                "coverage_restriction": note,
                "source": SOURCE,
                "scraped_at": scraped,
            }
            rows.append(row)
            pending = row if not drug_name else None
            continue
        atc = _ATC_RE.match(line)
        if atc and section in TEXT_SECTIONS:
            category = f"{atc.group(1)} {atc.group(2).strip()}"
            pending = None
            continue
        if section not in TEXT_SECTIONS or not rows:
            continue
        cont = line.strip()
        if pending is not None:
            # 编号单独成行：紧随行承载名称（可能还带剂型）
            name, dosage, note = _split_fields(cont)
            pending["drug_name"] = name or None
            if dosage:
                pending["dosage_form"] = dosage
            if note:
                pending["coverage_restriction"] = note
            pending = None
            continue
        prev = rows[-1]
        if prev["section"] != section:
            continue
        # 折行：备注未收尾 → 并回上一条；否则若是剂型短语 → 补剂型
        if prev["coverage_restriction"] and not prev["coverage_restriction"].endswith("。"):
            prev["coverage_restriction"] = f"{prev['coverage_restriction']}{cont}"
        elif prev["dosage_form"] is None and _DOSAGE_END_RE.search(cont) and len(cont) <= 12:
            prev["dosage_form"] = cont
    _demote_dosage_only_names(rows)
    return rows


# 纯剂型短语：药名只由这些词构成时，说明版面的名称被挤到下一行、而这一格装的是剂型。
_DOSAGE_ONLY_RE = re.compile(
    r"^(?:外用|口服|注射|吸入|鼻用|眼用|耳用|直肠|阴道|舌下|含服|喷雾|滴眼|滴耳|滴鼻)?"
    r"(?:冻干制剂|冻干粉针剂|粉针剂|注射剂|注射液|片剂|肠溶片|分散片|咀嚼片|泡腾片|"
    r"胶囊剂|肠溶胶囊|软胶囊|颗粒剂|散剂|口服溶液剂|口服混悬剂|混悬凝胶剂|凝胶剂|"
    r"乳膏剂|软膏剂|贴剂|栓剂|滴剂|滴眼剂|滴耳剂|喷雾剂|气雾剂|吸入剂|胶浆剂|"
    r"外用液体剂|口服液体剂|口服常释剂型|口服散剂|缓释片|控释片|贴膏剂|洗剂|"
    r"搽剂|涂剂|酊剂|露剂|煎膏剂|糖浆剂|胶剂|茶剂|锭剂|棒剂|海绵剂|植入剂|"
    r"乳剂|混悬剂|溶液剂|无菌粉针剂)$"
)


def _demote_dosage_only_names(rows: list[dict]) -> None:
    """把"药名其实是被挤到名称列的剂型"纠正回来。

    实测（2026-11-11）：西药/中成药段里名称跨行的条目，``_split_fields`` 会把剂型
    当成药名（例如 503 号的四行药名分别是「外用冻干制剂」「凝胶剂」「滴眼剂」）。
    后果不只是显示错误——药名为剂的条目**地址会互相撞上**（同段同编号、剂型为空），
    事实台账无法唯一定位。实测因此有 102 组冲突。

    规则：药名是纯剂型短语时降级为剂型，药名清空（后续由续行回填真实药名）。
    """
    for row in rows:
        name = (row.get("drug_name") or "").strip()
        if not name or not _DOSAGE_ONLY_RE.match(name):
            continue
        if not row.get("dosage_form"):
            row["dosage_form"] = name
        row["drug_name"] = None


# 已知无法从文本层区分的编号（2025 年版实测）：这几个条目的药名在版面上**跨在编号行
# 两侧**（编号行那一格的名称列只有剂型），文本层重组不可靠，故药名留 null（诚实未知，
# 而不是把剂型当药名填进去）。它们与 ★ 变体同编号同剂型，地址会撞——这是分辨率上限，
# 不是 bug。白名单的作用是**只容忍这 3 个**：一旦冲突变多（改版/解析退化）立即失败。
KNOWN_DUPLICATE_ITEMS = frozenset({954, 960, 962})


def _check_address_uniqueness(rows: list[dict]) -> None:
    """同一（段, 编号, 药名, 剂型）不得出现多行，除非该编号在已知白名单里。

    为什么需要：事实台账按稳定地址定位，两行共用地址会让它们互相覆盖且无人报警。
    """
    seen: dict[tuple, int] = {}
    for row in rows:
        key = (row["section"], row.get("subsection"), row.get("pricing_channel"),
               row["item_no"], row.get("drug_name"), row.get("dosage_form"))
        seen[key] = seen.get(key, 0) + 1
    bad = {
        k: v for k, v in seen.items()
        if v > 1 and not (k[0] == "西药" and k[3] in KNOWN_DUPLICATE_ITEMS)
    }
    if bad:
        sample = "; ".join(f"{k} ×{v}" for k, v in list(bad.items())[:5])
        raise RuntimeError(f"nrdl: 地址冲突（{len(bad)} 组）: {sample}")


def counts(rows: list[dict]) -> dict[str, int]:
    """按部分统计**唯一品种编号**数（★ 变体不重复计数）。

    谈判药品段必须带 ``subsection`` 一起做键：该段含「竞价药品部分」子段，且子段
    编号从 1 重启，只按 item_no 去重会把两段撞成一段（实测 472 行只剩 399 个编号）。

    饮片段的「不得纳入基金支付范围」黑名单行没有品种编号（``payment_scope='excluded'``），
    它们不是目录条目，MUST 不参与本计数——否则会多出一个 ``item_no=None`` 的伪编号。
    """
    out: dict[str, set] = {}
    for row in rows:
        if row.get("payment_scope") == "excluded":
            continue
        # 三段各自从 1 编号；谈判段内部还有「西药 / 中成药 / 竞价」三套 1 起编号
        # （凡例 472 = 西药 399 + 竞价 12 + 中成药 61）。所以键必须同时含分部与渠道。
        key = (row.get("subsection"), row.get("pricing_channel"), row["item_no"])
        out.setdefault(row["section"], set()).add(key)
    return {k: len(v) for k, v in out.items()}


def run_nrdl_national_catalog(limit: int = 0) -> list[dict]:
    """下载当年目录 PDF 并解析成全部四段条目行。

    两条硬判据任一不满足即抛（失败即红）：
    1. 各段唯一品种编号数必须等于官方凡例（``EXPECTED``，含谈判段 472、饮片 892）；
    2. **任何字段都不得含金额形态**（价格红线，见 ``_PRICE_RE``）。

    四段走三条解析路径：西药/中成药走 ``-layout`` 文本切栏；谈判药品走 bbox 列坐标，
    其「医保支付标准」列整列丢弃（见 ``parse_negotiated_words``）；中药饮片走 bbox
    双栏切半（见 ``parse_herbal_slice``），其「(二) 不得纳入基金支付范围」黑名单
    另走文本解析（见 ``parse_excluded_slice``）——黑名单不是目录条目，用
    ``payment_scope='excluded'`` 区分。
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
    words = pdftext.extract_words(
        payload, first_page=NEGOTIATED_PAGE_FIRST, last_page=NEGOTIATED_PAGE_LAST
    )
    rows.extend(parse_negotiated_words(words))
    herbal_words = pdftext.extract_words(
        payload, first_page=HERBAL_PAGE_FIRST, last_page=HERBAL_PAGE_LAST
    )
    rows.extend(parse_herbal_slice(herbal_words))
    pages = text.split("\f")
    # PDF 页号 - 1 = 列表下标；(二) 黑名单在饮片正文后一页（PDF 202）。
    excluded_pdf_page = HERBAL_PAGE_LAST + 1
    if excluded_pdf_page - 1 >= len(pages):
        raise RuntimeError(
            f"nrdl: PDF 页数不足（{len(pages)}），取不到饮片黑名单页 {excluded_pdf_page}"
        )
    rows.extend(parse_excluded_slice(pages[excluded_pdf_page - 1]))
    for row in rows:
        row.setdefault("agreement_valid_from", None)
        row.setdefault("agreement_valid_to", None)
        row.setdefault("payable_standalone", None)
        row.setdefault("payment_scope", "payable")
        row.setdefault("kind", "drug")
        row.setdefault("aliases", None)
    got = counts(rows)
    drift = {k: (v, got.get(k, 0)) for k, v in EXPECTED.items() if got.get(k, 0) != v}
    if drift:
        raise RuntimeError(
            "nrdl: 条目数与官方凡例不符（部分是 (官方, 解析)）: "
            + ", ".join(f"{k}={a}/{b}" for k, (a, b) in drift.items())
        )
    _check_address_uniqueness(rows)
    for row in rows:
        for field in ("drug_name", "dosage_form", "coverage_restriction", "category",
                      "agreement_valid_from", "agreement_valid_to", "aliases"):
            value = row.get(field)
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, str) and _PRICE_RE.search(item):
                    raise RuntimeError(
                        f"nrdl: 价格越界 —— {field} 含金额形态（section={row['section']}, "
                        f"item_no={row['item_no']}）：{item[:60]}"
                    )
    return rows[:limit] if limit and limit > 0 else rows


if __name__ == "__main__":  # 冒烟：python3 spiders/nrdl-national-catalog/spider.py
    import json

    out = run_nrdl_national_catalog()
    print(json.dumps({"rows": len(out), "counts": counts(out)}, ensure_ascii=False, indent=2))


# ─────────────────────────────────────────────────────────────────────────────
# 谈判药品段：bbox 列坐标解析（change add-nrdl-negotiated-entries）
#
# 为什么不能走文本序：该段六列（分类|编号|名称|医保支付标准|备注|有效期）里，
# 药名跨 3 行（``甘精胰岛素利司那肽注射液`` + ``(Ⅰ)``）、支付标准可能只有一个
# ``*``、金额 ``52.60元(10mg/瓶)`` 与剂型写法 ``(10mg/瓶)`` 极像。文本序归列必然
# 把金额串进名称或备注——那是用户裁决的价格红线。
#
# 列边界来自**实测**（2026-11-11，第 125–190 页 5609 个词 x0 分桶，每 20pt）：
#   280–300 / 320–380 / 460–520 / 660–700 四处空隙，取中点为界。
# **不要用表头的 x**：表头是居中的、数据是左对齐的，两者不同（实测 名称 表头 x=309
# 而数据 x=263，直接用会把药名判成编号列）。
#
# 实测校验：全段含金额形态的词 207 个，100% 落在 (360, 490) 区间，其他四列零命中。
# 该事实被写成运行时断言（_assert_amounts_only_in_price_column），不是一次性人工核对。
# ─────────────────────────────────────────────────────────────────────────────
# 实测（2026-11-11，逐页扫）：PDF 第 125 页仍属中成药（其末条编号 1319 起）；
# 谈判段正文自第 126 页「协议期内谈判药品部分」标题起；第 189–191 页是**竞价药品部分**
# （凡例的「谈判药品部分（含竞价药品）」把它算进 472）；第 192 页起为中药饮片。
# 注意 PDF 页号与页脚「第 N 页」差 1（页脚标在页尾），不要照抄目录页码。
NEGOTIATED_PAGE_FIRST = 126
NEGOTIATED_PAGE_LAST = 191
NEGOTIATED_CUTS = (290.0, 360.0, 490.0, 680.0)
_COL_CLASS_NO_NAME = 0
_COL_PRICE = 1
_COL_NOTE = 2
_COL_VALID = 3


def _column_of(x: float) -> int:
    """按 xMin 归列：0=分类/编号/名称，1=支付标准，2=备注，3=协议有效期。"""
    if x < NEGOTIATED_CUTS[0]:
        return _COL_CLASS_NO_NAME
    if x < NEGOTIATED_CUTS[1]:
        return _COL_CLASS_NO_NAME
    if x < NEGOTIATED_CUTS[2]:
        return _COL_PRICE
    if x < NEGOTIATED_CUTS[3]:
        return _COL_NOTE
    return _COL_VALID


def _assert_amounts_only_in_price_column(
    words: list[tuple[int, float, float, float, float, str]],
) -> None:
    """任何金额形态的词，其归属列必须是支付标准列；否则解析失败。

    这道闸防的是**版面漂移**：边界常量还在，但官方改了栏宽，金额挪进了备注列。
    那时若继续跑，金额就会以「备注」的身份入库——正是要被拦住的情形。
    """
    strays = [
        (x, word, _column_of(x))
        for _pg, x, _y, _x1, _y1, word in words
        if _AMOUNT_TOKEN_RE.search(word) and _column_of(x) != _COL_PRICE
    ]
    if strays:
        sample = ", ".join(f"{w!r}@x={x:.0f}" for x, w, _c in strays[:5])
        raise RuntimeError(
            f"nrdl: 金额形态的词落在非支付标准列（共 {len(strays)} 个）: {sample}"
            " —— 版面可能已改版，拒绝继续以免金额入库"
        )


def parse_negotiated_words(
    words: list[tuple[int, float, float, float, float, str]], year: int = CATALOG_YEAR
) -> list[dict]:
    """把谈判段的词坐标解析成条目行（金额列整列丢弃）。

    行内字段：``section`` 恒为 ``谈判药品``；``dosage_form`` 恒为 None（该段无独立剂型列，
    剂型写在名称里）；协议有效期拆成两列。金额列在归并后**直接丢弃**，从不进入 row。

    **绑定策略（踩过坑）**：不能用"按 y 排序后依次累积"。实测版心里，一条目的协议
    有效期文字块（``2026年1月1日至`` / ``2027年12月31日``）的 y 中心与条目行相差
    6–12pt，可能排到条目行**之前**；按顺序累积会把有效期挂到上一条，导致 472 条里
    有效期全为 null。改为**就近绑定**：每个非条目行按 ``|Δy|`` 绑到同页最近的条目行；
    页首出现在首条之前的续行，绑到上一页的最后一条。
    """
    _assert_amounts_only_in_price_column(words)

    # 按 (页, y) 聚行：y 每页重新起算（只用 y 会把不同页的同一高度并成一行）
    rows: list[tuple[int, float, dict[int, list[str]]]] = []
    grouped: dict[tuple[int, int], list[tuple[float, str]]] = {}
    for page, x, y, _x1, _y1, word in words:
        grouped.setdefault((page, int(round(y / 3.0) * 3)), []).append((x, word))
    for (page, ybucket), items in sorted(grouped.items()):
        cells: dict[int, list[str]] = {}
        for x, word in sorted(items):
            cells.setdefault(_column_of(x), []).append(word)
        rows.append((page, float(ybucket), cells))

    scraped = datetime.now(timezone.utc).isoformat()
    entries: list[dict] = []          # 输出顺序 = 条目行出现顺序
    # 表头行（列名）与子段标题：既不是条目也不是数据，必须跳过，否则「备注」两个字
    # 会被当成第一条的限定支付范围（实测首版就出现了 "备注限：1.预防重症…"）。
    _HEADER_TOKENS = ("药品分类", "编号", "药品名称", "医保支付标准", "备注", "协议有效期")
    anchors: list[tuple[int, float]] = []  # 与 entries 一一对应的 (页, y)
    category: str | None = None
    # 段内分部（凡例的 472 = 西药 411 + 中成药 61；实测拆开是 399 + 61 + 12）：
    #   「（一）西药」页 126 起（399 条）、「(二)中成药」页 181 起（61 条），各自从 1 编号；
    #   「竞价药品部分」页 189 起（12 条）从 1 重新编号，且**归属中成药分部**
    #   （我起初按"竞价属西药"推断过，被实测 399+12=411 的表面吻合误导；真实分布是
    #   西药 399 / 中成药 61 / 竞价 12 属中成药，合计 472）。
    #   所以 part 由标题决定、竞价只改 pricing_channel，part 沿用当前值。
    # 不这样分部的后果：472 行只剩 399 个唯一编号（三套 1 起编号互相覆盖），
    # 计数判据直接失效。
    part = "西药"
    channel = "谈判"
    for page, y, cells in rows:
        left = " ".join(cells.get(_COL_CLASS_NO_NAME, []))
        # 子段标题按**整行文本**判定：实测「竞价药品部分」在 x≈350，正好落进支付标准列
        # 区间，只看左列会看不见它，导致竞价子段 73 条被算成谈判段的编号重复。
        row_text = " ".join(w for ws in cells.values() for w in ws)
        mk = re.search(r"[（(]\s*[一二三四]\s*[)）]\s*(西药|中成药)", row_text)
        if mk:
            part = mk.group(1)
        if "竞价药品部分" in row_text:
            channel = "竞价"
        if not ENTRY_RE.match(left):
            # 分类行与子段标题在这里也要更新，否则条目里的 category 恒为 None
            if any(tok in left for tok in _HEADER_TOKENS):
                if left.startswith("（"):
                    category = left
                continue
            if left and not re.match(r"^[甲乙]", left) and not re.match(r"^[（(]", left):
                category = left
            continue
        m = ENTRY_RE.match(left)
        ab_class, marker, rest = m.group(1), m.group(2), (m.group(3) or "").strip()
        item_no = int(marker[2:-1]) if marker.startswith("★") else int(marker)
        name, _dosage, note_inline = _split_fields(rest) if rest else ("", None, None)
        entries.append({
            "catalog_year": year,
            "section": "谈判药品",
            "subsection": part,
            "pricing_channel": channel,
            "category": category,
            "class_ab": ab_class,
            "item_no": item_no,
            "is_variant": marker.startswith("★"),
            "drug_name": name or None,
            "dosage_form": None,
            "coverage_restriction": note_inline or None,
            "agreement_valid_from": None,
            "agreement_valid_to": None,
            "source": SOURCE,
            "scraped_at": scraped,
        })
        anchors.append((page, y))

    def nearest_entry(page: int, y: float) -> int | None:
        """同页最近的条目下标；同页无条目（页首续行）→ 上一页最后一条。"""
        best, best_d = None, None
        for i, (epage, ey) in enumerate(anchors):
            if epage != page:
                continue
            d = abs(ey - y)
            if best_d is None or d < best_d:
                best, best_d = i, d
        if best is not None:
            return best
        prior = [i for i, (epage, _y) in enumerate(anchors) if epage < page]
        return prior[-1] if prior else None

    valid_buf: dict[int, str] = {}
    for page, y, cells in rows:
        left = " ".join(cells.get(_COL_CLASS_NO_NAME, []))
        note = "".join(cells.get(_COL_NOTE, []))
        valid = " ".join(cells.get(_COL_VALID, []))
        # 支付标准列 cells[_COL_PRICE] 被**故意不读**——整列丢弃。
        if ENTRY_RE.match(left):
            continue
        # 表头/子段标题：跳过且不当分类
        if any(tok in left for tok in _HEADER_TOKENS) or left.startswith("（一）") or left.startswith("（二）"):
            category = left if left.startswith("（") else category
            continue
        # 分类行（ATC 代码 + 中文分类名）：只更新 category，不绑定到条目
        if left and not re.match(r"^[甲乙]", left) and not re.match(r"^[（(]", left):
            if not note and not valid:
                category = left
                continue
        idx = nearest_entry(page, y)
        if idx is None:
            continue
        entry = entries[idx]
        if left and not re.match(r"^[A-Z]{2}\d", left):
            if re.match(r"^[（(]", left):
                entry["drug_name"] = f"{entry['drug_name'] or ''}{left}"
            elif not entry["drug_name"]:
                entry["drug_name"] = left
        if note:
            cur = entry["coverage_restriction"]
            entry["coverage_restriction"] = f"{cur}{note}" if cur else note
        if valid:
            valid_buf[idx] = valid_buf.get(idx, "") + valid

    for idx, buf in valid_buf.items():
        vfrom, vto = _parse_validity(buf)
        if vfrom:
            entries[idx]["agreement_valid_from"] = vfrom
            entries[idx]["agreement_valid_to"] = vto
    return entries


_VALIDITY_RE = re.compile(
    r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*至\s*"
    r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)
_AMOUNT_TOKEN_RE = re.compile(r"\d+(\.\d+)?\s*元|^[¥￥]$")


def _parse_validity(text: str) -> tuple[str | None, str | None]:
    """``2026年1月1日至2027年12月31日`` → (``2026-01-01``, ``2027-12-31``)。

    解析不出就返回 (None, None)——**不填占位日期**：占位会让"未知"看起来像"已测量"。
    """
    m = _VALIDITY_RE.search(text or "")
    if not m:
        return None, None
    y1, m1, d1, y2, m2, d2 = m.groups()
    return f"{int(y1):04d}-{int(m1):02d}-{int(d1):02d}", f"{int(y2):04d}-{int(m2):02d}-{int(d2):02d}"


# ─────────────────────────────────────────────────────────────────────────────
# 中药饮片段（change add-nrdl-herbal-slice）
#
# 与前三部分都不同构：**左右双栏并列**（每页两组 序号/名称/备注）、**无甲乙类**、
# 备注以 ``□`` 标记为主。文本层会把同一条水平线上的两栏拼成一行，所以必须按 x 切半。
#
# 列边界来自实测（2026-11-11，PDF 192–201 页全量 token 归类）：
#   左半  序号 80–90 / 名称 108–235 / 备注 228–236
#   右半  序号 314–320 / 名称 345–450 / 备注 448–460
# 判据是「全段未归类 token = 0」，且最长的名称（天然冰片(右旋龙脑) 等）也未越进备注列。
# **不要用表头 x**：表头居中、数据左对齐，两者不同（与谈判段同一个坑）。
#
# 页号：PDF 页号 = 页脚「第 N 页」+ 1。饮片段正文在 PDF 192–201，(二) 黑名单在 202。
HERBAL_PAGE_FIRST = 192
HERBAL_PAGE_LAST = 201
HERBAL_CUT = 300.0          # 左右半栏分界（实测左半最右 236、右半最左 314，取中点偏左）
HERBAL_EXPECTED = 892
# 黑名单段的顶层药名数（实测 2026-11-11，另有 1 条类目行）。
HERBAL_EXCLUDED_DRUGS = 31
# 名称/备注列带（实测，见上）：超出任一带即失败，让版面漂移立刻可见。
_HERBAL_NAME_BAND = {0: (100.0, 226.0), 1: (340.0, 446.0)}
_HERBAL_NOTE_BAND = {0: (226.0, 245.0), 1: (446.0, 470.0)}


def _herbal_band(half: int, x: float) -> str | None:
    """把半栏内的 token 归到 name / note 带；都不在则返回 None（由调用方失败）。"""
    lo, hi = _HERBAL_NAME_BAND[half]
    if lo <= x < hi:
        return "name"
    lo, hi = _HERBAL_NOTE_BAND[half]
    if lo <= x <= hi:
        return "note"
    return None
# 备注行与条目行的最大允许距离（pt）。实测本行 6.1–6.3、最近的非本行 ≥19；
# 超过这条线说明版面漂移，必须失败而不是猜着绑。
HERBAL_NOTE_MAX_DIST = 8.0
_BOX = "□"


def _herbal_note_text(raw: str) -> tuple[bool, str | None]:
    """把备注列原文拆成 (单独使用不予支付, 附加条件文本)。

    凡例原文：标注「□」的指单独使用时统筹基金不予支付，且全部由这些饮片组成的处方
    也不予支付。所以 ``□`` 是语义标记，不是备注文本。实测另有 ``□；限临床危重患者抢救``
    这种「标记 + 附加条件」形态（红参 / 红参片 两条）。
    """
    if not raw:
        return False, None
    marked = _BOX in raw
    text = raw.replace(_BOX, "").lstrip("；;，,、 ").strip()
    return marked, (text or None)


def parse_herbal_slice(words, year: int = CATALOG_YEAR) -> list[dict]:
    """解析「（一）基金予以支付的中药饮片」段（双栏，序号 1..892 连续）。

    硬判据（任一不满足即抛）：
    1. 唯一序号数 == ``HERBAL_EXPECTED``，且序号集合恰好覆盖 ``1..N``（缺号/重号分列）；
    2. 名称互不重复——重名只可能是切栏把两条并成一条。

    备注折行：**逐行按最近的条目行绑定**，并设最大距离护栏。实测红参/红参片的备注
    两行分别距本行 6.1/6.3pt、距相邻行 19.0pt，按「上一行」绑定会错挂给前一条。
    """
    by_line: dict[tuple, list[tuple[float, str]]] = {}
    for page, x0, y0, _x1, _y1, word in words:
        by_line.setdefault((page, round(y0, 1)), []).append((round(x0, 1), word))

    entries: list[dict] = []                       # 待绑定备注的条目
    notes: list[tuple[int, float, int, str]] = []  # (page, y, half, text)
    unclassified: list[tuple[int, float, float, str]] = []
    for key in sorted(by_line):
        page, y = key
        tokens = sorted(by_line[key])
        joined = "".join(w for _, w in tokens)
        if not joined or "序号" in joined or "中药饮片部分" in joined or "（一）" in joined:
            continue
        if re.match(r"^第\d+页$", joined):          # 页脚
            continue
        if "不得纳入基金支付范围" in joined:          # (二) 黑名单页，另行解析
            continue
        for half, part in enumerate(
            ([(x, w) for x, w in tokens if x < HERBAL_CUT],
             [(x, w) for x, w in tokens if x >= HERBAL_CUT])
        ):
            if not part:
                continue
            # 同一条水平线上，备注列（``□`` 或折行备注的首行）与名称列是**分开的两格**：
            # 只按「数字开头 → 其余全是名称」会把 ``□`` 并进药名（实测会把 114 条标记
            # 全吞成名字的一部分，而且名称唯一性检查还能通过）。
            seq_tok = part[0][1]
            if not seq_tok.isdigit():
                note_text = "".join(w for _, w in part)
                notes.append((page, y, half, note_text))
                continue
            name_toks, note_toks = [], []
            for x, w in part[1:]:
                band = _herbal_band(half, x)
                if band == "name":
                    name_toks.append(w)
                elif band == "note":
                    note_toks.append(w)
                else:
                    unclassified.append((page, y, x, w))
            entries.append({
                "page": page, "y": y, "half": half,
                "item_no": int(seq_tok),
                "drug_name": "".join(name_toks).strip(),
                "_notes": ["".join(note_toks)] if note_toks else [],
            })
    if unclassified:
        sample = "; ".join(f"p{p} y={y} x={x} {w!r}" for p, y, x, w in unclassified[:5])
        raise RuntimeError(
            f"nrdl: 饮片段有 {len(unclassified)} 个 token 落在已知列带之外"
            f"（列边界需按实测重定）：{sample}"
        )

    if not entries:
        raise RuntimeError("nrdl: 饮片段未解析到任何条目（版面或页范围变了）")

    # 备注逐行绑定到同页同半栏最近的条目行
    by_half: dict[tuple[int, int], list[dict]] = {}
    for entry in entries:
        by_half.setdefault((entry["page"], entry["half"]), []).append(entry)
    unbound = 0
    for page, y, half, text in notes:
        candidates = by_half.get((page, half)) or []
        if not candidates:
            unbound += 1
            continue
        best = min(candidates, key=lambda e: abs(e["y"] - y))
        dist = abs(best["y"] - y)
        if dist > HERBAL_NOTE_MAX_DIST:
            raise RuntimeError(
                f"nrdl: 饮片备注无法绑定（page={page} y={y} 最近条目 {dist:.1f}pt > "
                f"{HERBAL_NOTE_MAX_DIST}）：{text[:40]}"
            )
        best["_notes"].append(text)
    if unbound:
        raise RuntimeError(f"nrdl: 饮片段有 {unbound} 行备注找不到同页条目（版面漂移）")

    # 序号连续性与名称唯一性
    nums = sorted(e["item_no"] for e in entries)
    dup_nums = sorted({n for n in nums if nums.count(n) > 1})
    missing = [n for n in range(1, HERBAL_EXPECTED + 1) if n not in set(nums)]
    extra = [n for n in set(nums) if n > HERBAL_EXPECTED]
    if dup_nums or missing or extra or len(set(nums)) != HERBAL_EXPECTED:
        raise RuntimeError(
            f"nrdl: 饮片序号异常（唯一 {len(set(nums))} ≠ {HERBAL_EXPECTED}）"
            f" 缺号={missing[:10]}{'…' if len(missing) > 10 else ''}"
            f" 重号={dup_nums[:10]} 越界={extra[:10]}"
        )
    names = [e["drug_name"] for e in entries]
    dup_names = sorted({n for n in names if names.count(n) > 1})
    if dup_names:
        raise RuntimeError(f"nrdl: 饮片名称重复（切栏可能把两条并成一条）：{dup_names[:5]}")

    # 附加条件的内容判据：饮片段的备注是「限定条件」，官方写法以「限」起（实测仅 2 条：
    # 「限临床危重患者抢救」）。这条不只为了格式一致——折行备注**少拼一行**时剩下的
    # 「重患者抢救」仍然是非空文本，数量和序号都没变，只有内容判据能发现它。
    for entry in entries:
        _, note = _herbal_note_text("".join(entry["_notes"]))
        if note and not note.startswith("限"):
            raise RuntimeError(
                f"nrdl: 饮片备注不像限定条件（item_no={entry['item_no']}）：{note[:40]}"
                f" —— 折行可能只拼了一半"
            )

    scraped = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    for entry in sorted(entries, key=lambda e: e["item_no"]):
        marked, note = _herbal_note_text("".join(entry["_notes"]))
        rows.append({
            "catalog_year": year,
            "section": "中药饮片",
            "subsection": None,
            "pricing_channel": None,
            "category": None,
            "class_ab": None,
            "item_no": entry["item_no"],
            "is_variant": False,
            "drug_name": entry["drug_name"] or None,
            "dosage_form": None,
            "coverage_restriction": note,
            "payable_standalone": not marked,
            "payment_scope": "payable",
            "kind": "drug",
            "aliases": None,
            "source": SOURCE,
            "scraped_at": scraped,
        })
    return rows


def parse_excluded_slice(page_text: str, year: int = CATALOG_YEAR) -> list[dict]:
    """解析「(二) 不得纳入基金支付范围的中药饮片」——黑名单，非目录条目。

    三个必须处理的形态（实测）：
    1. **断行把词劈开**：``龟`` + ``鹿二仙胶`` → ``龟鹿二仙胶``，所以先按行拼接；
    2. **括号内也有顿号**：``穿山甲(醋山甲、炮山甲)``，括号内不切分，别名归并到父项；
    3. **末行是类目不是药名**：``各种动物脏器(鸡内金除外)和胎、鞭、尾、筋、骨``
       MUST 作为 ``kind='category'`` 单列，不得拆成 5 个药名。

    实测输出：31 个 ``kind='drug'`` + 1 个 ``kind='category'``。
    """
    body = page_text
    marker = "不得纳入基金支付范围的中药饮片"
    if marker in body:
        body = body.split(marker, 1)[1]
    body = re.split(r"注[:：]", body, maxsplit=1)[0]
    body = re.sub(r"第\s*\d+\s*页", "", body)
    joined = "".join(line.strip() for line in body.splitlines() if line.strip())
    if not joined:
        raise RuntimeError("nrdl: 饮片黑名单段为空（版面或页范围变了）")

    category = None
    cat_match = re.search(r"各种动物脏器.*$", joined)
    if cat_match:
        category = cat_match.group(0)
        joined = joined[: cat_match.start()]
    joined = joined.rstrip("、。； ")

    items: list[str] = []
    buf, depth = "", 0
    for ch in joined:
        if ch in "(（":
            depth += 1
        elif ch in ")）":
            depth = max(0, depth - 1)
        if ch == "、" and depth == 0:
            items.append(buf)
            buf = ""
        else:
            buf += ch
    if buf:
        items.append(buf)

    scraped = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    for raw in items:
        name = raw.strip()
        if not name:
            continue
        base, _, inner = name.partition("(")
        if not inner:
            base, _, inner = name.partition("（")
        aliases = [a.strip() for a in re.split(r"[、,，]", inner.strip(")）")) if a.strip()] if inner else []
        rows.append({
            "catalog_year": year,
            "section": "中药饮片",
            "subsection": None,
            "pricing_channel": None,
            "category": None,
            "class_ab": None,
            "item_no": None,
            "is_variant": False,
            "drug_name": base.strip() or name,
            "dosage_form": None,
            "coverage_restriction": None,
            # 黑名单条目谈不上"单独使用是否不予支付"（它们根本不纳入支付），
            # 该字段留 None 而不是 False，避免与 892 条的 False（= 有 □ 标记）混为一谈。
            "payable_standalone": None,
            "payment_scope": "excluded",
            "kind": "drug",
            "aliases": aliases or None,
            "source": SOURCE,
            "scraped_at": scraped,
        })
    if category:
        rows.append({
            "catalog_year": year,
            "section": "中药饮片",
            "subsection": None,
            "pricing_channel": None,
            # 类目行没有药名；排除类目的原文放 category（本就是"分类"语义），
            # 用 kind='category' 与具体药名区分，不新造列。
            "category": category,
            "class_ab": None,
            "item_no": None,
            "is_variant": False,
            "drug_name": None,
            "dosage_form": None,
            "coverage_restriction": None,
            "payable_standalone": None,
            "payment_scope": "excluded",
            "kind": "category",
            "aliases": None,
            "source": SOURCE,
            "scraped_at": scraped,
        })
    if not rows:
        raise RuntimeError("nrdl: 饮片黑名单段解析出 0 条")
    drugs = [r for r in rows if r["kind"] == "drug"]
    if len(drugs) != HERBAL_EXCLUDED_DRUGS:
        raise RuntimeError(
            f"nrdl: 饮片黑名单条目数与实测不符（期望 {HERBAL_EXCLUDED_DRUGS}，"
            f"解析 {len(drugs)}）：段名或版面可能变了"
        )
    # 括号别名的实测锚：这两条的别名是「多个别名用顿号分隔」，能同时切出来才说明
    # 「括号内不切分」与「别名归并到父项」两条都成立。golden 里断不了列表值
    # （rows_contains 是字符串化比较），所以断言放在这里。
    by_name = {r["drug_name"]: r for r in drugs}
    for name, expected in (("穿山甲", ["醋山甲", "炮山甲"]),
                           ("鹿茸", ["鹿茸粉", "鹿茸片"])):
        got = (by_name.get(name) or {}).get("aliases")
        if got != expected:
            raise RuntimeError(
                f"nrdl: 饮片黑名单别名解析异常（{name}: 期望 {expected}，实际 {got}）"
            )
    return rows
