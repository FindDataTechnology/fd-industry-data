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
EXPECTED = {"西药": 1446, "中成药": 1335, "谈判药品": 472}
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
    """
    out: dict[str, set] = {}
    for row in rows:
        # 三段各自从 1 编号；谈判段内部还有「西药 / 中成药 / 竞价」三套 1 起编号
        # （凡例 472 = 西药 399 + 竞价 12 + 中成药 61）。所以键必须同时含分部与渠道。
        key = (row.get("subsection"), row.get("pricing_channel"), row["item_no"])
        out.setdefault(row["section"], set()).add(key)
    return {k: len(v) for k, v in out.items()}


def run_nrdl_national_catalog(limit: int = 0) -> list[dict]:
    """下载当年目录 PDF 并解析成全部三段条目行。

    两条硬判据任一不满足即抛（失败即红）：
    1. 各段唯一品种编号数必须等于官方凡例（``EXPECTED``，含谈判段 472）；
    2. **任何字段都不得含金额形态**（价格红线，见 ``_PRICE_RE``）。

    三段走两条解析路径：西药/中成药走 ``-layout`` 文本切栏；谈判药品走 bbox 列坐标，
    其「医保支付标准」列整列丢弃（见 ``parse_negotiated_words``）。
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
    for row in rows:
        row.setdefault("agreement_valid_from", None)
        row.setdefault("agreement_valid_to", None)
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
                      "agreement_valid_from", "agreement_valid_to"):
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
