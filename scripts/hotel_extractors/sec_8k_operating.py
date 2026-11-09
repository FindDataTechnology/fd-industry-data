#!/usr/bin/env python3
"""美股酒店 8-K exhibit 99.1 营运指标通道（add-hotel-vertical 任务 4.3）。

SOURCE = ``sec-edgar-8k-operating``（本波新建的定位车道；抽取在本模块，
逐家 mapping 表在这里，不在 spider 里）。

## 上游事实（实测 2026-11-08，recon §3）

美股 14 家酒店宇宙里，真正用 **8-K Item 2.02** 发布季度业绩、且新闻稿内含
可解析营运指标表的只有 5 家：

| ticker | exhibit 文件名 | 指标表形态 |
|---|---|---|
| MAR 万豪 | ``mar-2026q3xex99earningsrel.htm`` | Comparable Company-Operated / Systemwide × (US & Canada / International+Worldwide)，品牌行 + 合计行 |
| HLT 希尔顿 | ``q32026earningsrelease.htm`` | System-wide / Region / Brand / Segment 四段 |
| WH 温德姆 | ``exhibit9912026930.htm`` | ``HISTORICAL REVPAR`` 表：Global / U.S. / International 逐季水平值 |
| H 凯悦 | ``q32026earningsrelease.htm`` | ``(in constant $)`` 表：System-wide / 分区 / 自有租赁 |
| CHH 精选 | ``exhibit991earningspressrel.htm`` | ``CURRENCY-NEUTRAL SYSTEM-WIDE`` 表：Total U.S. / International / Total System |

其余 9 家逐家不可得，理由各不相同（见 ``AVAILABILITY``）：5 家是 6-K／20-F
的外国私人发行人（根本不用 8-K），3 家是 OTA（无酒店营运指标口径），1 家
（HGV）是分时度假、新闻稿全文 RevPAR 命中 0。

**任务书原文「至少 4 家（MAR/HLT/WH/HTHT）」的 HTHT 一项经实测证伪**：
HTHT（华住）在 EDGAR 只报 6-K（271 份，8-K 为 0），本通道按 8-K 定位，
故由实测可解析的 H 与 CHH 补位，交付 5 家。证伪事实写入任务账与口径表。

## 解析纪律（四条硬约束）

1. **不按列序猜**：一行数据先取「非空单元格里的槽序列」，必须是 **3 指标 ×
   (量值, 同比) = 6 槽**，否则整行丢弃留 note。同比位可以是破折号（实测 H 自有
   酒店出租率同比「—」，量值三个都在）——按数字个数数会误判成 5 个并丢真数据，
   所以按槽走：单位格跳过，破折号占空槽，量值缺失才算形状不符。
2. **指标归属由恒等式裁决**：RevPAR ＝ ADR × 出租率 是酒店业的定义式，
   用它遍历 6 种归属，只有**唯一**解才接受；0 解=「恒等式不符」，多解=
   「列归属歧义」，两种都丢弃并留证。表头文字顺序作为独立旁证，与恒等式
   不一致时同样丢弃（防止把 Net Package / 分部口径的行混进同名列）。
3. **只写当季（3M）块**：六/九/十二个月累计块、``Year Ended``、``TTM Ended``
   一律先判掉并清表头 —— 五家的 FY（Q4）业绩稿把两套列并进同一张表、季度块
   后面紧跟行名一模一样的累计块，只认「three months ended」会让年度值顶掉季度值
   （实测 HLT FY 114.39 压掉 Q4 110.89）。上半年的稿同理（Six Months Ended）。
   同一份 exhibit 内 (序列, 指标, 期间) 出现两个不同值时留痕并以先出现者取用。
4. **旧报告基准不混写**：温德姆 FY 稿除本期 historical 表外还附一张
   「PRE Q2 2025 REPORTING BASIS」对照表（同结构、同 2025 行，Global Q1 一个
   38.44、一个 36.13，旧表 Q2–Q4 全 n/a）。按「已披露季度数最多」取本期基准，
   旧基准整块不写。

## 口径（逐家不同，随行携带，见 docs/hotel-kpi-caliber.md）

* MAR = constant-$、Comparable（可比口径，非全量）
* HLT = system-wide（全量），未声明 constant-$，按披露原文记 as-reported
* WH  = Total System，水平值逐季（同一份公告会给当年已过季度，取最新申报值）
* H   = constant-$（另有 reported-$ 的 all-inclusive 表，本批不注册以免同名列混口径）
* CHH = currency-neutral、system-wide

空白 / ``n/a`` / ``—`` 一律**未披露**，不写 0，不插值，不跨公司补齐。
"""
from __future__ import annotations

import gzip
import hashlib
import re
import sys
import urllib.request
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from hotel_extractors._common import (  # noqa: E402
    dedup_latest,
    ensure_unit_concepts,
    insert_rows,
    unit_items,
)

SOURCE = "sec-edgar-8k-operating"
EVIDENCE = "openspec/changes/add-hotel-vertical/reports/4.0-wave3-recon.md"

#: exhibit HTML 缓存（与 hkex_operating 同法：sha1(url) 落盘，重跑不再回源）
DOC_CACHE = Path(__file__).resolve().parents[1] / "data" / "sec-8k-hotel-cache"
USER_AGENT = "HotelVertical research@finddatatech.cloud"

# ── availability matrix（实测，逐家理由不同，不要「统一」）─────────────────
_NOT_8K = ("外国私人发行人（20-F/6-K 申报人），EDGAR 无 8-K；"
           "营运数据在 6-K 业绩新闻稿里，本车道按 8-K 定位故不覆盖")
AVAILABILITY: tuple[dict, ...] = (
    {
        "entity_code": "MAR",
        "entity_name": "万豪国际",
        "status": "parsed",
        "carrier": "8-K Item 2.02 + exhibit 99.1（*ex99earningsrel.htm）",
        "reason": "Comparable Company-Operated / Systemwide × (US & Canada / International) 表，"
                  "合计行含 US & Canada - All / International - All / Worldwide",
        "evidence": EVIDENCE,
        "recovery": "无需恢复（按季自动定位）",
    },
    {
        "entity_code": "HLT",
        "entity_name": "希尔顿全球",
        "status": "parsed",
        "carrier": "8-K Item 2.02 + exhibit 99.1（q3YYYYearningsrelease.htm）",
        "reason": "System-wide / Region / Brand / Segment 四段表，列序为 出租率·ADR·RevPAR",
        "evidence": EVIDENCE,
        "recovery": "无需恢复",
    },
    {
        "entity_code": "WH",
        "entity_name": "温德姆酒店及度假村",
        "status": "parsed",
        "carrier": "8-K Item 2.02 + exhibit 99.1（exhibit991YYYYMMDD.htm）",
        "reason": "HISTORICAL REVPAR 表给 Global / U.S. / International 逐季水平值"
                  "（同一公告含当年已过各季，不只当季）",
        "evidence": EVIDENCE,
        "recovery": "无需恢复",
    },
    {
        "entity_code": "H",
        "entity_name": "凯悦酒店",
        "status": "parsed",
        "carrier": "8-K Item 2.02 + exhibit 99.1（q3YYYYearningsrelease.htm）",
        "reason": "(in constant $) 表含 System-wide / 六大区 / Owned and leased；"
                  "同一份 8-K 还带一份 7.01 演示稿（选件时必须排除）",
        "evidence": EVIDENCE,
        "recovery": "无需恢复",
    },
    {
        "entity_code": "CHH",
        "entity_name": "精选国际酒店",
        "status": "parsed",
        "carrier": "8-K Item 2.02 + exhibit 99.1（exhibit991earningspressrel.htm）",
        "reason": "CURRENCY-NEUTRAL SYSTEM-WIDE 表，列序为 ADR·出租率·RevPAR，"
                  "出租率同比以 bps 计",
        "evidence": EVIDENCE,
        "recovery": "无需恢复",
    },
    {
        "entity_code": "IHG",
        "entity_name": "洲际酒店集团",
        "status": "not-an-8k-filer",
        "carrier": "6-K（883 份；8-K 为 0）",
        "reason": _NOT_8K,
        "evidence": EVIDENCE,
        "recovery": "后续单立 6-K 业绩新闻稿抽取单元（与本模块共用逐家 mapping 纪律）",
    },
    {
        "entity_code": "HTHT",
        "entity_name": "华住集团（ADR）",
        "status": "not-an-8k-filer",
        "carrier": "6-K（271 份；8-K 为 0）",
        "reason": _NOT_8K + "；任务书原列 HTHT 为 8-K 抽取对象，实测证伪："
                  "EDGAR 实测 271 份 6-K、0 份 8-K（2026-11-08）",
        "evidence": EVIDENCE,
        "recovery": "同 IHG：另立 6-K 通道；香港主板的经营数据可走 hkex-disclosure（01179）",
    },
    {
        "entity_code": "ATAT",
        "entity_name": "亚朵",
        "status": "not-an-8k-filer",
        "carrier": "6-K（45 份；8-K 为 0）",
        "reason": _NOT_8K,
        "evidence": EVIDENCE,
        "recovery": "同 IHG",
    },
    {
        "entity_code": "GHG",
        "entity_name": "格林酒店",
        "status": "not-an-8k-filer",
        "carrier": "6-K（52 份；8-K 为 0）",
        "reason": _NOT_8K,
        "evidence": EVIDENCE,
        "recovery": "同 IHG",
    },
    {
        "entity_code": "TCOM",
        "entity_name": "携程集团",
        "status": "not-an-8k-filer",
        "carrier": "6-K（265 份；8-K 为 0）",
        "reason": _NOT_8K,
        "evidence": EVIDENCE,
        "recovery": "同 IHG",
    },
    {
        "entity_code": "HGV",
        "entity_name": "希尔顿分时度假",
        "status": "no-revpar-disclosure",
        "carrier": "8-K Item 2.02 + exhibit 99.1（hgvq3YYYYearningsreleaseex.htm）",
        "reason": "分时度假运营商：实测该 exhibit 全文 890,755 字节里 RevPAR 命中 0、"
                  "occupancy 命中 0（披露合同销售/到访/VOI，非酒店营运三指标）；"
                  "且该份 8-K 正文无任何文件链接（选件 status=no-exhibit-link）",
        "evidence": EVIDENCE,
        "recovery": "若需要分时度假口径（contract sales / tours / VOI），另立指标族，不复用 RevPAR",
    },
    {
        "entity_code": "BKNG",
        "entity_name": "Booking Holdings",
        "status": "ota-no-hotel-kpi",
        "carrier": "8-K Item 2.02（有 49 份）但无酒店营运指标",
        "reason": "OTA 分销平台：新闻稿披露间夜/预订额，不披露 RevPAR/出租率；"
                  "与酒店运营商不同业态，按宇宙口径只作对照不做合并聚合",
        "evidence": EVIDENCE,
        "recovery": "改注册 OTA 指标族（room nights / gross bookings）后另立通道",
    },
    {
        "entity_code": "EXPE",
        "entity_name": "Expedia 集团",
        "status": "ota-no-hotel-kpi",
        "carrier": "8-K Item 2.02（有 39 份）但无酒店营运指标",
        "reason": "同 BKNG",
        "evidence": EVIDENCE,
        "recovery": "同 BKNG",
    },
    {
        "entity_code": "ABNB",
        "entity_name": "爱彼迎",
        "status": "ota-no-hotel-kpi",
        "carrier": "8-K Item 2.02（有 14 份）但无酒店营运指标",
        "reason": "同 BKNG（民宿分销平台，GBV/间夜口径）",
        "evidence": EVIDENCE,
        "recovery": "同 BKNG",
    },
)

# ── 指标与序列 ────────────────────────────────────────────────────────────
METRICS = {
    "revpar": ("RevPAR（每间可出租客房收入）", "RevPAR", "USD", "value"),
    "adr": ("ADR（平均每日房价）", "average daily rate", "USD", "value"),
    "occupancy": ("出租率", "occupancy rate", "%", "ratio"),
}

#: 序列 -> (中文名, 英文名, scope, dimension)
SERIES = {
    "systemwide_worldwide": ("全系统·全球", "system-wide, worldwide", "systemwide", "worldwide"),
    "systemwide_us": ("全系统·美国", "system-wide, U.S.", "systemwide", "us"),
    "systemwide_us_canada": ("全系统·美国与加拿大", "system-wide, U.S. & Canada",
                             "systemwide", "us_canada"),
    "systemwide_international": ("全系统·国际", "system-wide, international",
                                 "systemwide", "international"),
    "systemwide_americas_ex_us": ("全系统·美洲（除美国）",
                                  "system-wide, Americas excluding U.S.",
                                  "systemwide", "americas_ex_us"),
    "systemwide_europe": ("全系统·欧洲", "system-wide, Europe", "systemwide", "europe"),
    "systemwide_mea": ("全系统·中东与非洲", "system-wide, Middle East & Africa",
                       "systemwide", "mea"),
    "systemwide_apac": ("全系统·亚太", "system-wide, Asia Pacific", "systemwide", "apac"),
    "systemwide_greater_china": ("全系统·大中华", "system-wide, Greater China",
                                 "systemwide", "greater_china"),
    "company_operated_worldwide": ("公司自营·全球", "company-operated, worldwide",
                                  "company_operated", "worldwide"),
    "company_operated_us_canada": ("公司自营·美国与加拿大",
                                   "company-operated, U.S. & Canada",
                                   "company_operated", "us_canada"),
    "company_operated_international": ("公司自营·国际", "company-operated, international",
                                       "company_operated", "international"),
    "owned_leased_worldwide": ("自有及租赁·全球", "owned and leased, worldwide",
                               "owned_leased", "worldwide"),
}

CONCEPTS: list[tuple] = []
for _metric, (_zh, _en, _unit, _measure) in METRICS.items():
    for _series, (_szh, _sen, _scope, _dim) in SERIES.items():
        CONCEPTS.append((
            f"hotel.us.{_metric}.{_series}.quarterly",
            f"美股酒店 {_zh} {_szh}（季度）",
            f"US hotel {_en}, {_sen} (quarterly)",
            "行业经营",
            _unit,
            _measure,
            "quarter",
            "stock",
            SOURCE,
        ))
CONCEPT_BY_KEY = {
    (c[0].split(".")[2], c[0].split(".")[3]): c[0] for c in CONCEPTS
}

# ── 逐家 caliber（随行携带；4.4 口径表的机器可读源）─────────────────────────
CALIBER = {
    "MAR": ("万豪：constant-$ 可比口径（Comparable，非全量库存）；"
            "RevPAR＝ADR×出租率；跨 Company-Operated 与 Systemwide 两个口径分列，不混写"),
    "HLT": ("希尔顿：system-wide 全量口径，按披露原文为 as-reported（该表未声明 constant $）；"
            "RevPAR＝ADR×出租率；出租率同比以百分点计"),
    "WH": ("温德姆：Total System 口径；RevPAR 为逐季水平值（该表不披露 ADR/出租率，"
           "故无法用恒等式自校，仅结构校验）；同一公告会给当年已过各季，取最新申报值"),
    "H": ("凯悦：constant-$ 口径；RevPAR＝ADR×出租率；"
          "同一公告另有 reported $ 的 all-inclusive（Net Package）表，本批不注册以免同名列混口径"),
    "CHH": ("精选国际：currency-neutral system-wide 口径；RevPAR＝ADR×出租率；"
            "出租率同比以 bps 计"),
}

# ── 数字与行工具 ──────────────────────────────────────────────────────────
_DASH = {"—", "–", "-", "n/a", "N/A", "na", ""}
_NUMERIC = re.compile(r"^-?\d[\d,]*(?:\.\d+)?$")
_YEAR = re.compile(r"^20\d{2}$")
_TAG_STRIP = re.compile(r"<[^>]+>")
_FOOTNOTE = re.compile(r"\s*(?:\(\s*[0-9a-z]{1,2}\s*\)|\d{1,2})$")
_WS = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _WS.sub(" ", (text or "").replace("\u00a0", " ")).strip()


def _num(text: str) -> str | None:
    """单元格 -> 规范数字字符串；非数值（含空白/n-a/破折号）-> None。"""
    token = _clean(text)
    if token in _DASH:
        return None
    negative = token.startswith("(") and token.endswith(")")
    if negative:
        token = token[1:-1].strip()
    for prefix in ("$", "US$", "HK$"):
        if token.startswith(prefix):
            token = token[len(prefix):].strip()
    # 「3.6 % pts.」这类复合单位要反复剥（实测单元格多是分开的，但合并写法也接受）
    while True:
        cleaned = token
        for suffix in ("%", "pts.", "pts", "bps", "x"):
            if cleaned.lower().endswith(suffix):
                cleaned = cleaned[: -len(suffix)].strip()
                break
        if cleaned == token:
            break
        token = cleaned
    token = token.replace(",", "").strip()
    if not _NUMERIC.match(token):
        return None
    return f"-{token}" if negative else token


def _label_key(text: str) -> str:
    """行标签归一化：去脚注数字、压空白、小写。"""
    cleaned = _clean(text).lower()
    return _FOOTNOTE.sub("", cleaned).strip()


def _table_rows(html_text: str):
    """按文档顺序产出 ``(table_index, 非空单元格文本列表)``。

    带上 table_index 是为了让调用方在**换表时重置上下文**：同一份新闻稿里
    「Total U.S.」「International」这类行名会在营运指标表、房价表、系统规模表
    里重复出现，只有「表头 + 期间行 + 数据行」在同一张表内才算一条可解析记录。
    只保留行内非空单元格（空单元格不携带信息）。

    只用 lxml（镜像内经 scrapling 预装，仓内 7 个 spider 已有直接 import 先例）
    —— 手工正则拆表格会在嵌套 <table> 上出错。
    """
    from lxml import html as lxml_html

    doc = lxml_html.fromstring(html_text)
    index_of: dict[int, int] = {}
    for index, table in enumerate(doc.iter("table")):
        index_of[id(table)] = index
    for tr in doc.iter("tr"):
        owner = tr.getparent()
        while owner is not None and owner.tag != "table":
            owner = owner.getparent()
        table_index = index_of.get(id(owner), -1) if owner is not None else -1
        cells = []
        for cell in tr:
            if cell.tag not in ("td", "th"):
                continue
            text = _clean(cell.text_content())
            if text:
                cells.append(text)
        if cells:
            yield table_index, cells


# ── 指标归属：恒等式裁决 ──────────────────────────────────────────────────
_IDENTITY_TOLERANCE = 0.02      # 2%：ADR×出租率 与 RevPAR 的舍入差远小于此
#: 6 种归属 = 3 个数值位置分配给 (RevPAR, ADR, 出租率) 的全部排列
_PERMUTATIONS = (
    (0, 1, 2), (0, 2, 1), (1, 0, 2),
    (1, 2, 0), (2, 0, 1), (2, 1, 0),
)


def _assign_metrics(
    pairs: list[tuple[str, str]],
    header_order: list[str] | None = None,
) -> tuple[dict | None, str]:
    """把 3 个 (值, 同比) 对判给 RevPAR / ADR / 出租率。

    裁决依据是定义式 RevPAR ＝ ADR × 出租率（出租率按百分数）。只有**唯一**解
    才接受：0 解=口径不符，多解=列归属歧义，两者都丢弃并留证 —— 这正是
    「不按列序猜」的机器化实现。

    实测有真的会撞车的行：HLT 亚太 出租率 71.9 与 RevPAR 71.78 几乎同值，
    恒等式同时接受「RevPAR=71.78／出租率=71.9」与「RevPAR=71.9／出租率=71.78」。
    这种情形用表头列序做第二条独立证据裁决（表头顺序必须落在解集里），
    仍然对不上就丢弃 —— 两条证据都不支持时不猜。
    """
    if len(pairs) != 3:
        return None, f"数值对不是 3 个（{len(pairs)}）"
    values = []
    for value, _change in pairs:
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            return None, "数值不可转浮点"
    solved = []
    for revpar_i, adr_i, occ_i in _PERMUTATIONS:
        revpar, adr, occ = values[revpar_i], values[adr_i], values[occ_i]
        if not (0 < occ <= 100) or adr <= 0 or revpar <= 0:
            continue
        expected = adr * occ / 100.0
        if abs(revpar - expected) / max(revpar, 1e-9) <= _IDENTITY_TOLERANCE:
            solved.append((revpar_i, adr_i, occ_i))
    if not solved:
        return None, "RevPAR≠ADR×出租率（恒等式不符）"

    def _as_assignment(triple: tuple[int, int, int]) -> dict:
        return {
            "revpar": pairs[triple[0]],
            "adr": pairs[triple[1]],
            "occupancy": pairs[triple[2]],
        }

    if len(solved) == 1:
        return _as_assignment(solved[0]), "identity"
    if header_order is not None and len(header_order) == 3 \
            and set(header_order) == {"revpar", "adr", "occupancy"}:
        implied = (
            header_order.index("revpar"),
            header_order.index("adr"),
            header_order.index("occupancy"),
        )
        if implied in solved:
            return _as_assignment(implied), "identity+header"
    return None, f"列归属歧义（{len(solved)} 组解满足恒等式且表头无法裁决）"


def _header_order(cells: list[str]) -> list[str] | None:
    """表头行 -> 指标列顺序（作为恒等式的独立旁证）。"""
    order = []
    for cell in cells:
        low = _clean(cell).lower()
        if "revpar" in low:
            order.append("revpar")
        elif "occupancy" in low or "出租率" in low:
            order.append("occupancy")
        elif "average daily rate" in low or low == "adr":
            order.append("adr")
    return order or None


def _pair_numbers(nums: list[str]) -> list[tuple[str, str]]:
    return [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]


#: 只有单位、没有量值的单元格（表格里把「$」「%」「% pts」拆成了独立格）
_UNIT_ONLY = re.compile(r"^(?:\$|us\$|%|pts\.?|bps|x|\s)+$", re.I)
#: 一个营运三列行应有的槽数：3 指标 × (量值, 同比)
_ROW_SLOTS = 6


def _slot_pairs(cells: list[str]) -> tuple[str, list[tuple[str | None, str | None]] | None]:
    """数据行 -> (标签, [(量值, 同比) × 3])；槽数不是 6 就返回 None。

    为什么不用「数字个数」判形状：同比位可以是破折号（实测 H 自有酒店
    ``Owned and leased hotels (b)`` 的出租率同比就是「—」，量值 246.55 /
    71.1 / 346.84 都是真披露）。按数字个数数会得到 5 个数字，把整行连同三个
    真实指标一起丢弃。这里按**槽**走：单位格跳过，破折号占一个空槽，
    量值缺失才算形状不符。
    """
    label = ""
    slots: list[str | None] = []
    for cell in cells:
        token = _clean(cell)
        if not token or _UNIT_ONLY.match(token):
            continue
        number = _num(token)
        if number is None:
            if not slots and not label:
                label = token
            elif token in _DASH:
                slots.append(None)      # 同比缺口：占槽，不猜值
            continue
        slots.append(number)
    if len(slots) != _ROW_SLOTS:
        return label, None
    pairs = [(slots[i], slots[i + 1]) for i in range(0, _ROW_SLOTS, 2)]
    if any(value is None for value, _change in pairs):
        return label, None              # 量值缺失 = 这行不是完整的三指标行
    return label, pairs


def _data_numbers(cells: list[str]) -> tuple[str, list[str]]:
    """数据行 -> (标签, 数字序列)。标签是第一个非数字单元格。"""
    label = ""
    nums: list[str] = []
    for cell in cells:
        number = _num(cell)
        if number is None:
            if not nums and not label:
                label = cell
            continue
        nums.append(number)
    return label, nums


# ── 逐家解析器 ────────────────────────────────────────────────────────────
#: MAR：section 标题 -> scope；数据行标签 -> 序列
_MAR_SECTION = re.compile(
    r"comparable\s+(company-operated|systemwide)\s+(us\s*&\s*canada|international)",
    re.I,
)
_MAR_ROWS = {
    "us & canada - all": "systemwide_us_canada",
    "international - all": "systemwide_international",
    "worldwide": "systemwide_worldwide",
}

_HLT_ROWS = {
    "system-wide": "systemwide_worldwide",
    "u.s.": "systemwide_us",
    "americas (excluding u.s.)": "systemwide_americas_ex_us",
    "europe": "systemwide_europe",
    "middle east & africa": "systemwide_mea",
    "asia pacific": "systemwide_apac",
}

_H_ROWS = {
    "system-wide hotels": "systemwide_worldwide",
    "united states": "systemwide_us",
    "americas (excluding u.s.)": "systemwide_americas_ex_us",
    "greater china": "systemwide_greater_china",
    "asia pacific (excluding greater china)": "systemwide_apac",
    "europe": "systemwide_europe",
    "middle east & africa": "systemwide_mea",
    "owned and leased hotels": "owned_leased_worldwide",
}

_CHH_ROWS = {
    "total system": "systemwide_worldwide",
    "total u.s.": "systemwide_us",
    "international": "systemwide_international",
}

_THREE_MONTHS = re.compile(r"three months ended", re.I)
#: 累计期块。实测每家 FY（Q4）业绩稿都把「Three Months Ended」与「Year Ended /
#: Twelve Months Ended」两套列并进同一张表，季度块后面紧跟累计块且行名一模一样
#: （H：「Three Months Ended June 30,」→ 数据行 →「Six Months Ended June 30,」→
#: 同样行名）。只认 3M 会让累计值当成当季写进去（HLT FY 114.39 冒充 Q4 110.89）。
#: 所以累计期一律显式判掉，宁可整块不写，也不把年度值挂到季度上。
_CUMULATIVE = re.compile(
    r"\b(?:six|nine|twelve)\s+months\s+ended|\byear\s+ended|\bttm\s+ended", re.I
)
_NINE_MONTHS = re.compile(r"nine months ended", re.I)
_REPORTED = re.compile(r"in reported", re.I)
_CONSTANT = re.compile(r"in constant", re.I)


def _parse_flat_table(
    html_text: str,
    issuer: str,
    rows_map: dict[str, str],
    period_end: str,
    notes: list[str],
    section_scope: dict[str, str] | None = None,
    require_constant: bool = False,
    table_tag: str = "",
) -> list[dict]:
    """MAR / HLT / H / CHH 共用：3M 块 + 6 数字行 + 恒等式裁决。

    ``section_scope`` 给 MAR 用（section 标题决定 scope，数据行只决定 dimension）。

    上下文随**换表**重置（``mode`` / ``currency`` / ``header_order``）：同一份
    新闻稿里「Total U.S.」「International」这类行名在营运指标表、房价表、系统
    规模表里重复出现，只有「表头与数据行同表」才算一条可解析记录。这样
    CHH 的 royalty rate 表（行名同为 Total U.S.、数字 4 个）不会被当成营运行，
    也不会把 9M 块的数字写进当季。
    """
    out: list[dict] = []
    mode = None                  # "3m" / "9m"
    currency = None              # constant / reported
    header_order: list[str] | None = None
    scope = "systemwide"
    table_now = None
    dropped = {"shape": 0, "identity": 0, "header": 0}
    parsed_labels: set[str] = set()
    kept = 0

    for table_index, cells in _table_rows(html_text):
        if table_index != table_now:
            table_now = table_index
            mode = None
            currency = None
            header_order = None
            if section_scope is None:
                scope = "systemwide"

        joined = " ".join(cells)

        if section_scope is not None:
            match = _MAR_SECTION.search(joined)
            if match:
                scope = ("company_operated" if match.group(1).lower().startswith("company")
                         else "systemwide")
                header_order = None
                continue

        # 期间行与口径行可能**同时**是表头行（实测 H：「(in constant $) RevPAR
        # Occupancy ADR」一行三职：口径 + 表头）。所以先认表头，再按需补记
        # 期间/口径，不能在这些行上 continue 掉表头。
        header = _header_order(cells)
        fresh_period = False
        # 累计期先判：一行的期间标签可能同时写着「Three Months Ended … Six Months
        # Ended …」，先判 3M 会让整块累计行按当季解析（见 _CUMULATIVE 注释）。
        if _CUMULATIVE.search(joined):
            mode = "cumulative"
            fresh_period = True
        elif _THREE_MONTHS.search(joined):
            mode = "3m"
            fresh_period = True
        elif _NINE_MONTHS.search(joined):
            mode = "cumulative"
            fresh_period = True
        if _CONSTANT.search(joined):
            currency = "constant"
        elif _REPORTED.search(joined):
            currency = "reported"

        if header is not None:
            header_order = header
            continue
        if fresh_period or _CONSTANT.search(joined) or _REPORTED.search(joined):
            if fresh_period:
                header_order = None      # 新期间块：表头必须重新出现才算数
            continue

        if mode != "3m":
            continue
        if header_order is None:
            # 同表内表头之前 / 无表头的表（规模表、费率表）一律不解析
            continue
        if require_constant and currency != "constant":
            continue

        label, pairs = _slot_pairs(cells)
        key = _label_key(label)
        target = rows_map.get(key)
        if target is None:
            continue
        if pairs is None:
            dropped["shape"] += 1
            if key not in parsed_labels:
                notes.append(f"{SOURCE}: {issuer} 「{label}」不是 3 指标 × (量值, 同比) "
                             "的 6 槽行——整行丢弃（同名列出现在别的表里也不是营运三列）")
            continue
        assignment, why = _assign_metrics(pairs, header_order)
        if assignment is None:
            dropped["identity"] += 1
            notes.append(f"{SOURCE}: {issuer} 「{label}」列归属不可信：{why}——整行丢弃")
            continue

        by_metric = {metric: assignment[metric][0] for metric in assignment}

        # 顺序旁证：把解出的三元组按「原始数值位置」排出来，与表头顺序比对。
        # 恒等式与表头是两条独立证据；两者矛盾说明这一行的列不是表面那三列
        # （例如同名列在另一张口径表里），宁可丢弃也不写错序列。
        positions = {metric: _pair_index(pairs, assignment[metric])
                     for metric in assignment}
        solved_order = tuple(
            metric for metric, _ in sorted(positions.items(), key=lambda kv: kv[1])
        )
        if header_order is not None and len(header_order) == 3:
            declared_order = tuple(header_order)
            if set(declared_order) == {"revpar", "adr", "occupancy"} and \
                    declared_order != solved_order:
                dropped["header"] += 1
                notes.append(f"{SOURCE}: {issuer} 「{label}」表头顺序 {declared_order} 与"
                             f"恒等式解出的顺序 {solved_order} 不一致——整行丢弃")
                continue
        parsed_labels.add(key)

        series = target
        if section_scope is not None:
            # MAR：行标签决定 dimension，section 决定 scope
            dimension = SERIES[series][3]
            series = f"{scope}_{dimension}"
            if series not in SERIES:
                continue
        for metric, value in by_metric.items():
            if not _series_supports(series, metric):
                continue
            out.append(_record(issuer, metric, series, period_end, value, table_tag))
        kept += 1

    if kept == 0:
        notes.append(f"{SOURCE}: {issuer} 当季块未解出任何行（{table_tag}）——"
                     f"丢弃统计 {dropped}")
    return out


def _pair_index(pairs: list[tuple[str, str]], pair: tuple[str, str]) -> int:
    """某 (值, 同比) 对在行内的序号（用于顺序旁证）。"""
    for index, candidate in enumerate(pairs):
        if candidate is pair:
            return index
    return 99


def _series_supports(series: str, metric: str) -> bool:
    """WH 只披露 RevPAR；其余序列三指标齐全。"""
    if series in {"systemwide_worldwide", "systemwide_us", "systemwide_international"}:
        return True
    return metric in METRICS


def _record(issuer: str, metric: str, series: str, date: str, value: str,
            table_tag: str) -> dict:
    return {
        "issuer": issuer,
        "metric": metric,
        "series": series,
        "date": date,
        "value": value,
        "unit": METRICS[metric][2],
        "caliber": f"{CALIBER[issuer]}｜表：{table_tag}" if table_tag else CALIBER[issuer],
    }


def parse_mar(html_text: str, period_end: str, notes: list[str]) -> list[dict]:
    return _parse_flat_table(
        html_text, "MAR", _MAR_ROWS, period_end, notes,
        section_scope={}, table_tag="Comparable Company-Operated/Systemwide",
    )


def parse_hlt(html_text: str, period_end: str, notes: list[str]) -> list[dict]:
    return _parse_flat_table(
        html_text, "HLT", _HLT_ROWS, period_end, notes,
        table_tag="System-wide / Region",
    )


def parse_h(html_text: str, period_end: str, notes: list[str]) -> list[dict]:
    return _parse_flat_table(
        html_text, "H", _H_ROWS, period_end, notes,
        require_constant=True, table_tag="(in constant $) System-wide / Region",
    )


def parse_chh(html_text: str, period_end: str, notes: list[str]) -> list[dict]:
    return _parse_flat_table(
        html_text, "CHH", _CHH_ROWS, period_end, notes,
        table_tag="Currency-neutral system-wide",
    )


_WH_LABEL = re.compile(
    r"^(global|u\.s\.|international)\s+revpar$", re.I,
)
_WH_ROW = {"global": "systemwide_worldwide",
           "u.s.": "systemwide_us",
           "international": "systemwide_international"}
#: 温德姆 historical 表的季度表头行 —— 一出现即视为新的一块
#: （「First Quarter Second Quarter Third Quarter Fourth Quarter Full- Year」）。
#: FY 稿实测有两块：本期基准（Global Q1 38.44，四季齐全）与
#: 「PRE Q2 2025 REPORTING BASIS」对照块（Global Q1 36.13，Q2–Q4 全 n/a）。
_WH_QUARTER_HEAD = re.compile(
    r"first\s+quarter.*second\s+quarter.*third\s+quarter.*fourth\s+quarter.*full[-\s]{0,2}year",
    re.I,
)


def _quarter_end(year: int, quarter: int) -> str:
    return f"{year}-{quarter * 3:02d}-{'31' if quarter in (1, 4) else '30'}"


_MONTHS = {name: index for index, name in enumerate(
    ("january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"), start=1)}
_THREE_MONTHS_DATE = re.compile(
    r"three\s+months\s+ended\s+([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})", re.I
)
#: 年份不在同一行时的退路：实测 H 的表头是「Three Months Ended September 30,」
#: 紧随其后是「(in constant $) RevPAR Occupancy ADR」，年份写在下一行
#: 「2026 vs. 2025」。允许中间夹 ≤60 个非数字字符再取最近年份。
_THREE_MONTHS_DATE_LOOSE = re.compile(
    r"three\s+months\s+ended\s+([A-Z][a-z]+)\s+(\d{1,2}),?[^0-9]{0,60}?(20\d{2})", re.I
)


def period_end_from_html(html_text: str) -> str | None:
    """从 exhibit 表头取财季末（``Three Months Ended September 30, 2026``）。

    比载荷载荷里的 ``period_end`` 更硬：解析用的表和数据在同一个文档里，
    表头自己写的期间才是这批数字的期间。
    """
    from lxml import html as lxml_html

    try:
        plain = _WS.sub(" ", lxml_html.fromstring(html_text).text_content())
    except Exception:  # noqa: BLE001 — 畸形 HTML 退回正则直扫原文
        plain = _WS.sub(" ", _TAG_STRIP.sub(" ", html_text or ""))
    match = _THREE_MONTHS_DATE.search(plain) or _THREE_MONTHS_DATE_LOOSE.search(plain)
    if not match:
        return None
    month = _MONTHS.get(match.group(1).lower())
    if month is None:
        return None
    return f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(2)):02d}"


def parse_wh(html_text: str, period_end: str, notes: list[str]) -> list[dict]:
    """温德姆 ``HISTORICAL REVPAR`` 表：逐季水平值（不是同比、不是累计）。

    结构：指标行（Global / U.S. / International RevPAR）后跟 ``2026`` / ``2025``
    两行，每行 5 个值槽 = Q1..Q4 + Full Year（``n/a`` 是未披露）。只取当年、
    只取 Q1..Q4；Full Year 槽与上年整行都不写。

    一份稿里会出现多张同结构的 historical 表（实测 FY2025 稿同时有
    「REPORTING BASIS AS OF Q4 2025」与「PRE Q2 2025 REPORTING BASIS」两张，
    同一 2025 行给出 38.44 / 36.13 两个 Global Q1）。表名与基准标注都落在表格
    之外的段落里，所以按**块**处理：每遇到一行「First Quarter … Full-Year」表
    头即开新块，同一序列取「当年已披露季度最多」的那块（旧基准对照块实测只有
    Q1 有值，其余 n/a），被丢弃的块留痕。
    """
    blocks: list[dict[str, list[str | None]]] = []
    block: dict[str, list[str | None]] | None = None
    in_block = False
    current: str | None = None
    year_wanted = period_end[:4]
    table_now = None
    for table_index, cells in _table_rows(html_text):
        if table_index != table_now:
            table_now = table_index
            # 表名（「HISTORICAL REVPAR, ROYALTY RATE AND ROOMS」）实测落在表外的
            # 段落里，所以 in_block 不随换表重置；只有「当前指标行」按表清空。
            current = None
        joined = " ".join(cells)
        if "historical revpar" in joined.lower():
            in_block = True
            current = None
            continue
        if not in_block:
            continue
        if _WH_QUARTER_HEAD.match(joined):
            block = {}
            blocks.append(block)
            current = None
            continue
        label_match = _WH_LABEL.match(_clean(cells[0]))
        if label_match:
            current = _WH_ROW[label_match.group(1).lower()]
            continue
        if block is None or current is None or not _YEAR.match(_clean(cells[0])):
            continue
        if _clean(cells[0]) != year_wanted:
            continue                      # 上年整行只作对照，不写
        slots = [c for c in cells[1:] if _clean(c) != "$"]
        if len(slots) < 4:
            continue
        values = [_num(slot) for slot in slots[:4]]
        block[current] = values           # 同一块内同名序列后出现的整行取后者
        current = None

    out: list[dict] = []
    for series in sorted({k for block in blocks for k in block}):
        candidates = [block[series] for block in blocks if series in block]
        best = max(candidates, key=lambda vals: sum(v is not None for v in vals))
        if len(candidates) > 1:
            notes.append(
                f"{SOURCE}: WH 「{series}」同期有 {len(candidates)} 张 historical 表，"
                f"按已披露季度数取用最完整的一张（{sum(v is not None for v in best)} 季），"
                "旧基准对照块整块不写"
            )
        for quarter, value in enumerate(best, start=1):
            if value is None:
                continue                  # n/a / 空白 = 未披露，绝不补 0
            out.append(_record("WH", "revpar", series,
                               _quarter_end(int(year_wanted), quarter),
                               value, "Historical RevPAR"))
    if not out:
        notes.append(f"{SOURCE}: WH 未解出任何季度 RevPAR——按不可得留痕")
    return out


PARSERS = {
    "MAR": parse_mar,
    "HLT": parse_hlt,
    "WH": parse_wh,
    "H": parse_h,
    "CHH": parse_chh,
}


def availability(code: str) -> dict:
    for record in AVAILABILITY:
        if record["entity_code"] == code:
            return record
    return {
        "entity_code": code,
        "entity_name": "",
        "status": "not-in-us-hotel-universe",
        "carrier": "",
        "reason": f"{code} 不在本通道的美股酒店宇宙（14 家）之内",
        "evidence": EVIDENCE,
        "recovery": "先登记宇宙（任务 3.6）再接入",
    }


# ── exhibit 抓取 ──────────────────────────────────────────────────────────
def fetch_exhibit(url: str, notes: list, cache_dir: Path = DOC_CACHE) -> str | None:
    """取 exhibit HTML（gzip + 90s 超时 + sha1 磁盘缓存）。

    实测坑：python urllib 不带 ``Accept-Encoding: gzip`` 在 1.27 MB 的 MAR
    exhibit 上会卡死（90 秒只收 128 KB）；带上 gzip 即秒回。失败留 note，
    绝不静默当 0 行。
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".html")
    if cache.exists():
        return cache.read_text(encoding="utf-8", errors="replace")
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip",
        "Accept": "text/html,*/*",
    })
    try:
        with urllib.request.urlopen(request, timeout=90) as response:  # noqa: S310
            raw = response.read()
            if (response.headers.get("Content-Encoding") or "").lower() == "gzip":
                raw = gzip.decompress(raw)
    except Exception as exc:  # noqa: BLE001
        notes.append(f"{SOURCE}: exhibit 抓取失败 {url} — {type(exc).__name__}: {exc}")
        return None
    text = raw.decode("utf-8", errors="replace")
    cache.write_text(text, encoding="utf-8")
    return text


# ── 抽取入口 ──────────────────────────────────────────────────────────────
def extract(cur, notes):
    """读库内 ``sec-edgar-8k-operating`` 载荷 -> 解析 -> 观测行。"""
    payloads = unit_items(cur, SOURCE)
    if not payloads:
        notes.append(f"{SOURCE}: 库内无载荷——先跑 sec-edgar-8k-operating 车道，"
                     "否则本通道无从提取")
        return []

    usable = [p for p in payloads
              if str(p.get("status")) == "ok" and p.get("exhibit_url")]
    skipped = len([p for p in payloads if p not in usable])
    if skipped:
        notes.append(f"{SOURCE}: {skipped} 条载荷 exhibit 未定位（status≠ok）——"
                     "按 availability 呈现不可得，不猜文件名")

    entity_ids: dict[str, int] = {}
    for code in sorted({str(p.get("ticker")) for p in usable}):
        cur.execute(
            "SELECT id FROM entities WHERE entity_type='stock' AND code=%s", (code,)
        )
        row = cur.fetchone()
        if row is None:
            notes.append(f"{SOURCE}: 实体 {code} 未注册（任务 3.6）——跳过，不造数")
            continue
        entity_ids[code] = row[0]

    # 同一条序列在同一期可能被后续公告重述：按申报日升序遍历，后到的覆盖先到的
    ordered = sorted(
        [p for p in usable if str(p.get("ticker")) in entity_ids],
        key=lambda p: str(p.get("filed") or ""),
    )
    latest: dict[tuple, dict] = {}
    for payload in ordered:
        ticker = str(payload.get("ticker"))
        parser = PARSERS.get(ticker)
        if parser is None:
            notes.append(f"{SOURCE}: {ticker} 无逐家 mapping——按不可得呈现（不按列序猜）")
            continue
        period_end = str(payload.get("period_end") or "")
        text = fetch_exhibit(str(payload["exhibit_url"]), notes)
        if text is None:
            continue
        from_html = period_end_from_html(text)
        if from_html is not None and from_html != period_end:
            if period_end:
                notes.append(f"{SOURCE}: {ticker} {payload.get('accession')} 载荷期间 "
                             f"{period_end} 与 exhibit 表头 {from_html} 不一致——以 exhibit 为准")
            period_end = from_html
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", period_end):
            notes.append(f"{SOURCE}: {ticker} {payload.get('accession')} 取不到财季末——跳过")
            continue
        # 同一份 exhibit 内，(序列, 指标, 期间) 只许出一条：出现第二个不同值
        # 说明解析器把两个块（当季 vs 累计、现基准 vs 旧基准）当成了同一期。
        # 这时保留先出现的并留痕，交给测试当红灯——绝不静默后值覆盖前值
        # （HLT FY 114.39 压掉 Q4 110.89 就是这么发生的）。
        in_doc: dict[tuple[str, str, str], dict] = {}
        conflicts = 0
        for record in parser(text, period_end, notes):
            key = (record["metric"], record["series"], record["date"])
            prior = in_doc.get(key)
            if prior is not None:
                if prior["value"] != record["value"]:
                    conflicts += 1
                    notes.append(
                        f"{SOURCE}: {ticker} {payload.get('accession')} 同一份 exhibit 内 "
                        f"{record['series']}/{record['metric']}@{record['date']} 出现两个值 "
                        f"{prior['value']} 与 {record['value']}——保留先出现的，"
                        "疑为累计/旧基准块混入"
                    )
                continue
            in_doc[key] = record
        if conflicts:
            notes.append(f"{SOURCE}: {ticker} {payload.get('accession')} 文档内冲突 "
                         f"{conflicts} 条（已按先出现者取用，逐条见上）")
        for record in in_doc.values():
            latest[(record["issuer"], record["metric"], record["series"], record["date"])] = record

    rows: list[tuple] = []
    for record in sorted(latest.values(),
                         key=lambda r: (r["issuer"], r["series"], r["date"])):
        code = CONCEPT_BY_KEY.get((record["metric"], record["series"]))
        if code is None:
            notes.append(f"{SOURCE}: 序列 {record['metric']}/{record['series']} 未注册概念——"
                         "丢弃（不写无名序列）")
            continue
        rows.append((code, "stock", entity_ids[record["issuer"]],
                     record["date"], "quarter", record["value"]))

    by_issuer: dict[str, set[str]] = {}
    for record in latest.values():
        by_issuer.setdefault(record["issuer"], set()).add(record["date"])
    notes.append(
        f"{SOURCE}: 解析出 {len(rows)} 条季度观测，覆盖 "
        + "、".join(f"{code} {len(dates)} 期" for code, dates in sorted(by_issuer.items()))
        + f"；口径随行携带（{len({r['caliber'] for r in latest.values()})} 种）"
    )
    return rows


def main(argv=None) -> int:
    import argparse
    import json
    import os

    parser = argparse.ArgumentParser(description="美股酒店 8-K 营运指标通道（默认 dry-run）")
    parser.add_argument("--dsn", default=os.environ.get("FD_OPEN_DATA_DSN"))
    parser.add_argument("--write", action="store_true", help="写入 semantic_observations")
    parser.add_argument("--availability", action="store_true", help="只打印可得性矩阵")
    parser.add_argument("--html", help="直接解析一份本地 exhibit HTML（离线自检）")
    parser.add_argument("--issuer", default="MAR", help="配 --html 使用的逐家 mapping")
    parser.add_argument("--period-end", default="2026-09-30")
    args = parser.parse_args(argv)

    if args.availability:
        print(json.dumps(list(AVAILABILITY), ensure_ascii=False, indent=2))
        return 0

    if args.html:
        text = Path(args.html).read_text(encoding="utf-8", errors="replace")
        notes: list[str] = []
        records = PARSERS[args.issuer](text, args.period_end, notes)
        print(json.dumps(records, ensure_ascii=False, indent=2))
        for note in notes:
            print("NOTE:", note, file=sys.stderr)
        return 0

    if not args.dsn:
        raise SystemExit("FATAL: $FD_OPEN_DATA_DSN 未设置（source ~/.config/finddata/dashboards.env）")

    import psycopg2

    notes = []
    conn = psycopg2.connect(args.dsn)
    try:
        with conn.cursor() as cur:
            rows = extract(cur, notes)
            for note in notes:
                print("NOTE:", note)
            if args.write:
                ids = ensure_unit_concepts(cur, SOURCE, CONCEPTS, {r[0] for r in rows})
                print("inserted =", insert_rows(cur, SOURCE, ids, rows))
        if args.write:
            conn.commit()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
