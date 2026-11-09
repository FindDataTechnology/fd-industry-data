"""spiders/ctha-hotel-report —— 中国饭店协会年度报告（酒店业发展报告 + 集团及品牌发展报告）。

数据表面（**PDF 文本层**，非 OCR；poppler ``pdftotext -layout``）：

- 协会站点：``https://www.chinahotel.org.cn``（``ctha.com.cn`` 是死域名，别按缩写拼）
- 报告载体：``/sfs/cms/<32 位 hex>.pdf``，**哈希命名、无语义**，必须从文章页抓 ``href``
- 两部报告（2026 版，InDesign 产出，``Tagged: yes``，文本层完整）：
  ``hotel2026.pdf`` 32 页 / 19,139,434 B（行业规模·档次·规模·城市级别·省份·城市·连锁化率）；
  ``group2026.pdf`` 53 页 / 12,873,238 B（前 50 集团、外资前 30 品牌、四档品牌规模、品牌价值指数）

两类产出（``row_kind``）：

=========  ========================================  ==================================
row_kind   来源                                       形态
=========  ========================================  ==================================
metric     正文散文里的锚定句                           (table, dimension, metric, value, unit)
ranking    表格页逐行（序号 / 名称 / 总部或集团 / 客房数 / 门店数）  (rank, entity_*, rooms, hotels)
=========  ========================================  ==================================

口坑（侦察 + 建单实测，逐一落实）：

1. **索引面有登录墙（2026-11-08 03:10 实测）**：``/cat/35/pN.html`` 与 ``/art/<id>.html``
   一律 302 → ``/account/login``（裸 UA / 浏览器 UA / iPhone UA / http 全一样），
   但 ``/sfs/cms/*.pdf`` 静态件仍 206 可取。故本单元支持 ``reports`` 参数**直接传 PDF URL**
   （静态件哈希不随版本变），索引发现失败时抛带解释的 ``RuntimeError``，绝不静默空跑。
2. **图形页不猜值**：hotel2026 的 26 个图表页里，柱状/折线无数据标签的页（如 P12 的 31 省排序、
   P08 的十年序列）抽不出序列 —— 只取**同一页上的散文句**，图形页本身在 ``section`` 里留痕，
   值**一个都不猜**。
3. **表格逐行只在单元格不折行时取**：前 50 集团表（P09/P10）与外资前 30 品牌表（P13）
   实测每行一格不缺（30+20 / 30 行，序号 1..N 全覆盖）；豪华/高端/中端/经济型品牌榜
   （P14–P18）名称与集团单元格**跨行折行**（``锦江国际酒`` + ``店``），同一行的列归属不可复原
   → **整表不取**，只取这些页上的**汇总句**（如「前 20 家豪华品牌酒店营业客房数共 287,557 间」），
   并留痕 ``明细不取``。宁缺不错：绝不取半张表。
4. **年度口径 = 报告封面年份减 1**：2026 版报告的数据是「截至 2025 年 12 月 31 日」，
   期次按正文 ``截至(\\d{4})年12月31日`` 落，不按下载时间、不按封面年份。
5. **集团榜 ≠ 品牌榜**：``锦江国际酒店`` 品牌 23,936 间与``上海锦江国际酒店股份有限公司`` 集团
   1,368,057 间是两个量级；两张表各带 ``table``（``group_top50`` / ``brand_foreign_top30``），
   绝不按名字合并。
6. **协会口径 ≠ 星级饭店口径**：37.5 万家酒店 vs 文旅部 6,129 家星级饭店是两个宇宙；
   连锁口径是「同一品牌 3 家（含）以上门店」。任何跨源比较都必须显式写口径。
7. **概要页是摘要，且与明细页有舍入差**（hotel2026 P07 写「63%和91%」，P09 明细写
   「63.18%和91.33%」）→ hotel2026 跳过概要页，只取明细页；group2026 的概要页（P07）
   反而是唯一给全四档精确值的一页（3,163 家 / 854,039 间），故保留。
8. **硬失败**：任一 ``REQUIRED`` 锚定句缺失即 ``UnmappedPhrase`` → 整次运行 ``RuntimeError``
   （上游改版不许静默少指标）。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SOURCE = "ctha-hotel-report"
BASE = "https://www.chinahotel.org.cn"
CATEGORY_URL = BASE + "/cat/35/p{p}.html"      # 行业报告栏目
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/128.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9",
}
TIMEOUT = 120

# 报告标题命中（文章页标题里带书名号，逐字来自上游）
HOTEL_HINT = "中国酒店业发展报告"
GROUP_HINT = "酒店集团及品牌发展报告"
PDF_HREF_RE = re.compile(r'href="([^"]+\.pdf)"', re.I)

# hotel2026 概要页是摘要（有舍入差），跳过；group2026 概要页是唯一给全四档精确值的一页，保留
SKIP_PAGES = {("hotel2026", "中国酒店业发展报告概要")}

LAST_TRACES: list[str] = []

GRADES = ("经济型", "中档", "高档", "豪华")
SIZES = ("15-29间", "30-69间", "70-149间", "150间及以上")
TIERS = ("一线城市", "副省级及省会城市", "其它城市")


class UnmappedPhrase(ValueError):
    """正文锚定句失配 —— 上游改版，必须硬失败。"""


class LoginWall(RuntimeError):
    """索引面被登录墙拦截（302 → /account/login）。"""


# ---------------------------------------------------------------- 锚定句

# (正则, ((锚 index, table, dimension, metric, unit), ...), 归属报告, 是否必需)
# index 指向 capture group；归属报告 = 该锚定句只应出现在这份报告里（必需性按报告判）
ANCHORS: tuple[tuple[str, tuple, str, bool], ...] = (
    # ── 行业总量（hotel2026「关于报告」+「总体发展规模」两页给出同一组精确值）──
    (r"中国住宿业(?:在营业的)?设施总数为([\d,]+)家",
     ((1, "industry_scale", "全国", "lodging_facilities", "家"),), "hotel2026", True),
    (r"客房总规模([\d,]+)间",
     ((1, "industry_scale", "全国", "lodging_rooms", "间"),), "hotel2026", True),
    (r"其中酒店业设施([\d,]+)家，客房总数([\d,]+)间",
     ((1, "industry_scale", "全国", "hotel_facilities", "家"),
      (2, "industry_scale", "全国", "hotel_rooms", "间")), "hotel2026", True),
    (r"其它住宿业设施([\d,]+)家，客房总数([\d,]+)间",
     ((1, "industry_scale", "全国", "other_facilities", "家"),
      (2, "industry_scale", "全国", "other_rooms", "间")), "hotel2026", False),
    (r"平均客房规模约([\d.]+)间",
     ((1, "industry_scale", "全国", "hotel_avg_rooms", "间"),), "hotel2026", False),
    (r"平均客房规模约为([\d.]+)间",
     ((1, "industry_scale", "全国", "other_avg_rooms", "间"),), "hotel2026", False),
    (r"酒店业设施和客房数分别占我国住宿业的([\d.]+)%和([\d.]+)%",
     ((1, "industry_scale", "全国", "hotel_share_facilities", "%"),
      (2, "industry_scale", "全国", "hotel_share_rooms", "%")), "hotel2026", False),
    (r"其它住宿业设施和客房数分别占我国住宿业的([\d.]+)%和([\d.]+)%",
     ((1, "industry_scale", "全国", "other_share_facilities", "%"),
      (2, "industry_scale", "全国", "other_share_rooms", "%")), "hotel2026", False),
    # ── 档次结构（P11 明细页）──
    (r"这四个档次的设施数分别是([\d.]+)万家、([\d.]+)万家、([\d.]+)万家和([\d.]+)万家，"
     r"所占比重分别是([\d.]+)%、([\d.]+)%、([\d.]+)%和([\d.]+)%",
     ((1, "grade_structure", GRADES[0], "facilities", "万家"),
      (2, "grade_structure", GRADES[1], "facilities", "万家"),
      (3, "grade_structure", GRADES[2], "facilities", "万家"),
      (4, "grade_structure", GRADES[3], "facilities", "万家"),
      (5, "grade_structure", GRADES[0], "share_facilities", "%"),
      (6, "grade_structure", GRADES[1], "share_facilities", "%"),
      (7, "grade_structure", GRADES[2], "share_facilities", "%"),
      (8, "grade_structure", GRADES[3], "share_facilities", "%")), "hotel2026", False),
    (r"经济型（二星级及以下）客房数约为([\d.]+)万间，占([\d.]+)%",
     ((1, "grade_structure", GRADES[0], "rooms", "万间"),
      (2, "grade_structure", GRADES[0], "share_rooms", "%")), "hotel2026", False),
    (r"中档（三星级）客房数约为([\d.]+)万间，占([\d.]+)%",
     ((1, "grade_structure", GRADES[1], "rooms", "万间"),
      (2, "grade_structure", GRADES[1], "share_rooms", "%")), "hotel2026", False),
    (r"高档（四星级）客房数约为([\d.]+)万间，占([\d.]+)%",
     ((1, "grade_structure", GRADES[2], "rooms", "万间"),
      (2, "grade_structure", GRADES[2], "share_rooms", "%")), "hotel2026", False),
    (r"豪华（五星级）客房数为([\d.]+)万间，占([\d.]+)%",
     ((1, "grade_structure", GRADES[3], "rooms", "万间"),
      (2, "grade_structure", GRADES[3], "share_rooms", "%")), "hotel2026", False),
    # ── 规模结构（P12）──
    (r"客房规模在15-29间的设施数为([\d.]+)万家，客房规模在30-69间的设施数为([\d.]+)万家，"
     r"客房规模在70-149间的设施数为([\d.]+)万家，客房规模在150间及以上的设施数为([\d.]+)万家",
     ((1, "size_structure", SIZES[0], "facilities", "万家"),
      (2, "size_structure", SIZES[1], "facilities", "万家"),
      (3, "size_structure", SIZES[2], "facilities", "万家"),
      (4, "size_structure", SIZES[3], "facilities", "万家")), "hotel2026", False),
    (r"上述四类规模的设施在总量中所占比重分别为([\d.]+)%、([\d.]+)%、([\d.]+)%和([\d.]+)%",
     ((1, "size_structure", SIZES[0], "share_facilities", "%"),
      (2, "size_structure", SIZES[1], "share_facilities", "%"),
      (3, "size_structure", SIZES[2], "share_facilities", "%"),
      (4, "size_structure", SIZES[3], "share_facilities", "%")), "hotel2026", False),
    (r"中国酒店15-29间占比([\d.]+)%，30-69间的占比([\d.]+)%，"
     r"70-149间占比([\d.]+)%，150间及以上规模的占比([\d.]+)%",
     ((1, "size_structure", SIZES[0], "share_rooms", "%"),
      (2, "size_structure", SIZES[1], "share_rooms", "%"),
      (3, "size_structure", SIZES[2], "share_rooms", "%"),
      (4, "size_structure", SIZES[3], "share_rooms", "%")), "hotel2026", False),
    # ── 城市级别结构（P13）──
    (r"三大区域的设施数分别是([\d.]+)万家、([\d.]+)万家、([\d.]+)万家，"
     r"所占比重分别是([\d.]+)%、([\d.]+)%、([\d.]+)%",
     ((1, "city_tier", TIERS[0], "facilities", "万家"),
      (2, "city_tier", TIERS[1], "facilities", "万家"),
      (3, "city_tier", TIERS[2], "facilities", "万家"),
      (4, "city_tier", TIERS[0], "share_facilities", "%"),
      (5, "city_tier", TIERS[1], "share_facilities", "%"),
      (6, "city_tier", TIERS[2], "share_facilities", "%")), "hotel2026", False),
    (r"一线城市酒店客房数约为([\d.]+)万间，占([\d.]+)%；"
     r"副省级城市及省会城市酒店客房数约为([\d.]+)万间，占([\d.]+)%；"
     r"其它城市酒店客房数约为([\d.]+)万间，占([\d.]+)%",
     ((1, "city_tier", TIERS[0], "rooms", "万间"),
      (2, "city_tier", TIERS[0], "share_rooms", "%"),
      (3, "city_tier", TIERS[1], "rooms", "万间"),
      (4, "city_tier", TIERS[1], "share_rooms", "%"),
      (5, "city_tier", TIERS[2], "rooms", "万间"),
      (6, "city_tier", TIERS[2], "share_rooms", "%")), "hotel2026", False),
    # ── 省份 / 城市极值（P15 / P16 的散文；P14 的 31 省排序是纯图形，不猜）──
    (r"位列第一的广东省酒店业设施数达到了([\d,]+)家",
     ((1, "province", "广东", "facilities", "家"),), "hotel2026", False),
    (r"设施数最少的三个行政区是天津、青海和宁夏，分别是([\d,]+)家、([\d,]+)家和([\d,]+)家",
     ((1, "province", "天津", "facilities", "家"),
      (2, "province", "青海", "facilities", "家"),
      (3, "province", "宁夏", "facilities", "家")), "hotel2026", False),
    (r"位列第一的广东省拥有([\d.]+)万间客房",
     ((1, "province", "广东", "rooms", "万间"),), "hotel2026", False),
    (r"这十个省的酒店客房数都超过了([\d.]+)万间",
     ((1, "province", "前十省", "rooms_floor", "万间"),), "hotel2026", False),
    (r"重庆的设施总数达到了([\d,]+)家",
     ((1, "city", "重庆", "facilities", "家"),), "hotel2026", False),
    (r"这10个城市的客房数总数约([\d.]+)万间，占中国酒店客房总数的([\d.]+)%",
     ((1, "city", "前十城", "rooms", "万间"),
      (2, "city", "前十城", "share_rooms", "%")), "hotel2026", False),
    # ── 连锁与集团规模（group2026 概要页 P07 + 品牌价值指数页 P29）──
    (r"我国连锁酒店家数共([\d.]+)万家，连锁酒店客房总规模约([\d.]+)万间，"
     r"客房同比增长([\d.]+)万间，增幅为([\d.]+)%",
     ((1, "chain", "全国", "chain_hotels", "万家"),
      (2, "chain", "全国", "chain_rooms", "万间"),
      (3, "chain", "全国", "chain_rooms_delta", "万间"),
      (4, "chain", "全国", "chain_rooms_yoy", "%")), "group2026", True),
    (r"全国连锁酒店达([\d.]+)万家，客房规模约([\d.]+)万间，同比增长([\d.]+)%；"
     r"客房连锁化率升至([\d.]+)%，门店连锁化率\s*达([\d.]+)%",
     ((4, "chain", "全国", "chain_rate_rooms", "%"),
      (5, "chain", "全国", "chain_rate_hotels", "%")), "group2026", False),
    (r"我国规模前50家酒店集团营业客房总数约([\d.]+)万间",
     ((1, "group_scale", "前50集团", "rooms", "万间"),), "group2026", False),
    (r"相比\d{4}年度客房增长约([\d.]+)万间，增幅为([\d.]+)%",
     ((1, "group_scale", "前50集团", "rooms_delta", "万间"),
      (2, "group_scale", "前50集团", "rooms_yoy", "%")), "group2026", False),
    (r"这十家酒店集团的客房总数近([\d.]+)万间，相比\d{4}年度规模前10的酒店集团客房约([\d.]+)万间，"
     r"同比增长了([\d.]+)%",
     ((1, "group_scale", "前10集团", "rooms", "万间"),
      (2, "group_scale", "前10集团", "rooms_prior", "万间"),
      (3, "group_scale", "前10集团", "rooms_yoy", "%")), "group2026", False),
    (r"\d{4}年前十家酒店集团客房规模在连锁酒店市场占有率为([\d.]+)%",
     ((1, "group_scale", "前10集团", "share_of_chain", "%"),), "group2026", False),
    (r"豪华品牌酒店([\d,]+)家，客房数([\d,]+)间，客房总数同比增长([\d.]+)%",
     ((1, "brand_tier", GRADES[3], "facilities", "家"),
      (2, "brand_tier", GRADES[3], "rooms", "间"),
      (3, "brand_tier", GRADES[3], "rooms_yoy", "%")), "group2026", False),
    (r"高端品牌酒店([\d,]+)家，客房数([\d,]+)间，客房总数同比增长([\d.]+)%",
     ((1, "brand_tier", GRADES[2], "facilities", "家"),
      (2, "brand_tier", GRADES[2], "rooms", "间"),
      (3, "brand_tier", GRADES[2], "rooms_yoy", "%")), "group2026", False),
    (r"中端品牌酒店([\d,]+)家，客房数([\d,]+)间，客房总数同比增长([\d.]+)%",
     ((1, "brand_tier", GRADES[1], "facilities", "家"),
      (2, "brand_tier", GRADES[1], "rooms", "间"),
      (3, "brand_tier", GRADES[1], "rooms_yoy", "%")), "group2026", False),
    (r"经济型品牌酒店([\d,]+)家，客房数([\d,]+)间，客房总数同比增长([\d.]+)%",
     ((1, "brand_tier", GRADES[0], "facilities", "家"),
      (2, "brand_tier", GRADES[0], "rooms", "间"),
      (3, "brand_tier", GRADES[0], "rooms_yoy", "%")), "group2026", False),
)

# 汇总句（表格折行页只取这一句，明细不取）
ROLLUP_ANCHORS: tuple[tuple[str, str, str, str], ...] = (
    (r"中国境内外资前30家酒店品牌营业客房数共([\d,]+)间，营业门店数共([\d,]+)家",
     "brand_foreign_top30", "rooms_total", "间"),
    (r"中国前20家豪华品牌酒店营业客房数共([\d,]+)间，营业门店数共([\d,]+)家",
     "brand_luxury_top20", "rooms_total", "间"),
)

# 排名表：(标题正则, table, 列名顺序, 所属报告)
RANKING_TABLES: tuple[tuple[str, str, str, str], ...] = (
    (r"中国前\s*(\d+)\s*家酒店集团规模", "group_top50", "group", "group2026"),
    (r"中国境内前\s*(\d+)\s*家外资品牌酒店规模", "brand_foreign_top30", "brand", "group2026"),
)

RANK_ROW_RE = re.compile(
    r"^\s{0,8}(\d{1,3})\s+(\S.*?)\s{2,}(\S.*?)\s{2,}(\d{1,8})\s+(\d{1,7})\s*$")


# ------------------------------------------------------------------ 取数


def _get(url: str, timeout: int = TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 固定 https 主机
        final = resp.geturl()
        if "/account/login" in final:
            raise LoginWall(f"{url} → {final}")
        return resp.read()


def _num(text: str) -> float | None:
    try:
        return float(str(text).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _flat(text: str) -> str:
    """压掉全部空白（换行会把锚定句切断，``门店连锁化率\\n达28.37%`` 实测如此）。"""
    return re.sub(r"[ \t\r\n\u3000\xa0]+", "", text)


def _pages(text: str) -> list[str]:
    """``pdftotext`` 用换页符分隔页 → 逐页文本（下标 0 = 第 1 页）。"""
    return text.split("\f")


def page_of(pages: list[str], needle: str) -> int:
    """锚定句所在页码（1 起）；找不到返回 0。"""
    for idx, page in enumerate(pages, start=1):
        if needle in _flat(page):
            return idx
    return 0


def parse_metrics(pages: list[str], report: str) -> tuple[list[dict], list[str]]:
    """逐页跑锚定句 → metric 行 + 留痕。同一 (table, dimension, metric) 只留首次出现。"""
    rows: list[dict] = []
    traces: list[str] = []
    seen: dict[tuple[str, str, str], dict] = {}
    fatal: list[str] = []
    for pattern, spec, owner, required in ANCHORS:
        if owner != report:
            continue          # 该锚定句属于另一份报告（两份 PDF 各管各的面）
        rx = re.compile(pattern)
        hit_page = 0
        match = None
        for idx, page in enumerate(pages, start=1):
            found = rx.search(_flat(page))
            if found:
                match, hit_page = found, idx
                break
        if match is None:
            if required:
                fatal.append(f"必需锚定句缺失：{pattern[:48]}")
            continue
        for group, table, dimension, metric, unit in spec:
            value = _num(match.group(group))
            if value is None:
                continue
            key = (table, dimension, metric)
            if key in seen:
                if seen[key]["value"] == value:
                    traces.append(f"{key}: 同值复现（P{hit_page} 与 P{seen[key]['page']} 一致）")
                else:
                    traces.append(f"{key}: 口径冲突 P{seen[key]['page']}={seen[key]['value']} "
                                  f"P{hit_page}={value}（前者保留）")
                continue
            row = {
                "report": report, "row_kind": "metric", "table": table,
                "dimension": dimension, "metric": metric,
                "rank": None, "entity_name": "", "entity_group": "", "entity_hq": "",
                "value": value, "unit": unit, "rooms": None, "hotels": None,
                "page": hit_page,
            }
            seen[key] = row
            rows.append(row)
    if fatal:
        raise UnmappedPhrase("; ".join(fatal))
    return rows, traces


def parse_rollups(pages: list[str], report: str) -> list[dict]:
    """折行表格页的汇总句（明细不取）。"""
    rows: list[dict] = []
    for pattern, table, metric, unit in ROLLUP_ANCHORS:
        rx = re.compile(pattern)
        for idx, page in enumerate(pages, start=1):
            m = rx.search(_flat(page))
            if not m:
                continue
            rows.append({
                "report": report, "row_kind": "metric", "table": table,
                "dimension": "全国", "metric": metric,
                "rank": None, "entity_name": "", "entity_group": "", "entity_hq": "",
                "value": _num(m.group(1)), "unit": unit,
                "rooms": _num(m.group(1)), "hotels": _num(m.group(2)) if m.lastindex and m.lastindex > 1 else None,
                "page": idx,
            })
            break
    return rows


def parse_rankings(pages: list[str], report: str) -> tuple[list[dict], list[str]]:
    """序号表逐行；**序号 1..N 不全即整表不取**（绝不取半张表）。"""
    rows: list[dict] = []
    traces: list[str] = []
    for pattern, table, kind, owner in RANKING_TABLES:
        if owner != report:
            continue
        rx = re.compile(pattern)
        total = 0
        span: list[tuple[int, str]] = []
        taken: set[int] = set()
        for idx, page in enumerate(pages, start=1):
            flat = _flat(page)
            hits = [m for m in (RANK_ROW_RE.match(line) for line in page.splitlines()) if m]
            if not hits:
                continue
            ranks_here = {int(m.group(1)) for m in hits}
            title = rx.search(flat)
            if title:
                total = total or int(title.group(1))
                span.append((idx, page))
                taken |= ranks_here
                continue
            # 表跨页：续页没有标题但有同一表头，且序号不与已取行重叠（防串到别张榜）
            if span and "ROOMS" in flat and "HOTELS" in flat and not (ranks_here & taken):
                span.append((idx, page))
                taken |= ranks_here
        if not total:
            traces.append(f"{table}: 未发现表标题")
            continue
        found: dict[int, tuple[str, str, int, int, int]] = {}
        for idx, page in span:
            for line in page.splitlines():
                hit = RANK_ROW_RE.match(line)
                if not hit:
                    continue
                rank = int(hit.group(1))
                if rank in found:
                    continue
                found[rank] = (hit.group(2).strip(), hit.group(3).strip(),
                               int(hit.group(4)), int(hit.group(5)), idx)
        missing = [i for i in range(1, total + 1) if i not in found]
        if missing:
            traces.append(f"{table}: 单元格折行，{len(found)}/{total} 行可取 → 整表不取"
                          f"（缺序号 {missing[:6]}{'…' if len(missing) > 6 else ''}）")
            continue
        for rank in range(1, total + 1):
            name, second, rooms, hotels, page_no = found[rank]
            rows.append({
                "report": report, "row_kind": "ranking", "table": table,
                "dimension": "全国", "metric": "",
                "rank": rank, "entity_name": name,
                "entity_group": second if kind == "brand" else "",
                "entity_hq": second if kind == "group" else "",
                "value": None, "unit": "",
                "rooms": rooms, "hotels": hotels,
                "page": page_no,
            })
        traces.append(f"{table}: {total} 行全取")
    return rows, traces


def parse_report(pages: list[str], report: str) -> tuple[list[dict], list[str]]:
    """一份报告的全部页 → (metric + ranking 行, 留痕)。"""
    kept = [p for p in pages
            if not any(report == name and hint in _flat(p) for name, hint in SKIP_PAGES)]
    if len(kept) != len(pages):
        traces = [f"{report}: 跳过概要页（摘要，与明细页有舍入差）"]
    else:
        traces = []
    metrics, mtrace = parse_metrics(kept, report)
    ranking, rtrace = parse_rankings(kept, report)
    return metrics + parse_rollups(kept, report) + ranking, traces + mtrace + rtrace


def _period(pages: list[str]) -> int | None:
    """期次 = 正文「截至 YYYY 年 12 月 31 日」（封面年份 − 1，不按下载时间）。"""
    for page in pages:
        m = re.search(r"截至(\d{4})年12月31日", _flat(page))
        if m:
            return int(m.group(1))
    return None


# ------------------------------------------------------------ 发现与抓取


def _category_articles(pages: int) -> list[tuple[str, str]]:
    """栏目页 → [(标题, 文章 URL)]。栏目是 SSR 页，文章清单在内嵌 JSON 里。"""
    out: list[tuple[str, str]] = []
    seen: set[int] = set()
    for page in range(1, pages + 1):
        raw = _get(CATEGORY_URL.format(p=page)).decode("utf-8", "replace")
        for m in re.finditer(
                r'\{"id":(\d+),.*?"title":"(.*?)".*?"publishDate":"([^"]+)"', raw, re.S):
            art_id, title = int(m.group(1)), m.group(2)
            title = title.encode().decode("unicode_escape") if "\\u" in title else title
            if art_id in seen or not (HOTEL_HINT in title or GROUP_HINT in title):
                continue
            seen.add(art_id)
            out.append((title, f"{BASE}/art/{art_id}.html"))
    return out


def _pdf_of(article_url: str) -> str:
    """文章页 → PDF 直链（哈希名，无语义）。"""
    raw = _get(article_url).decode("utf-8", "replace")
    for m in PDF_HREF_RE.finditer(raw):
        return urllib.parse.urljoin(article_url, m.group(1))
    raise UnmappedPhrase(f"{article_url} 文章页无 PDF 直链")


def _reports_from_param(reports: str | None) -> list[tuple[str, str]]:
    """``reports="标题=URL,标题=URL"`` 或纯 URL 列表 → [(标题, URL)]。"""
    if not reports:
        return []
    out: list[tuple[str, str]] = []
    for chunk in reports.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" in chunk:
            title, url = chunk.split("=", 1)
            out.append((title.strip(), url.strip()))
        else:
            title = GROUP_HINT if "group" in chunk.lower() else HOTEL_HINT
            out.append((title, chunk))
    return out


def _report_name(title: str) -> str:
    return "group2026" if GROUP_HINT in title else "hotel2026"


# --------------------------------------------------------------------- 入口


def run_ctha_hotel_report(limit: int = 1200, pages: int = 6,
                          reports: str | None = None) -> list[dict]:
    """抓中国饭店协会年度报告 PDF，产出 metric / ranking 长表行。

    Args:
        limit: 最大返回行数。
        pages: 栏目翻页数（1..12，默认 6）。
        reports: 直接指定 PDF（``"标题=URL,标题=URL"`` 或 URL 列表）。索引面被登录墙
            拦截时用它；缺省走栏目发现。

    Raises:
        ValueError: 参数非法。
        RuntimeError: 索引面登录墙且未给 ``reports``，或锚定句失配，或全部失败。
    """
    try:
        limit = int(limit)
        pages = int(pages)
    except (TypeError, ValueError):
        raise ValueError(f"limit/pages must be ints, got {limit!r}/{pages!r}") from None
    if limit <= 0:
        return []
    if not 1 <= pages <= 12:
        raise ValueError(f"pages must be 1..12, got {pages}")

    from fd_industry_data import pdftext

    now = datetime.now(timezone.utc).isoformat()
    traces: list[str] = []
    LAST_TRACES.clear()

    targets = _reports_from_param(reports)
    if targets:
        traces.append(f"reports 参数直给 {len(targets)} 份 PDF（跳过索引发现）")
    else:
        try:
            targets = _category_articles(pages)
        except LoginWall as exc:
            raise RuntimeError(
                "ctha-hotel-report: 索引面被登录墙拦截（302 → /account/login），无法发现 PDF；"
                f"静态 PDF 仍可直连 —— 请用 reports=\"标题=URL\" 直给。实测：{exc}"
            ) from None
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"ctha-hotel-report: 栏目发现失败 {type(exc).__name__}: {exc}") from None
        if not targets:
            raise RuntimeError("ctha-hotel-report: 栏目页未命中任何酒店业报告文章")

    rows: list[dict] = []
    fatal: list[str] = []
    used: set[str] = set()
    for title, url in targets:
        name = _report_name(title)
        if name in used:  # 同一报告多篇文章（转载）只取首篇
            continue
        used.add(name)
        try:
            payload = _get(url)
        except Exception as exc:  # noqa: BLE001
            traces.append(f"{name}: 下载失败 {type(exc).__name__}: {exc}"[:200])
            continue
        if not pdftext.is_pdf(payload):
            traces.append(f"{name}: 载荷非 PDF（{url}）")
            continue
        try:
            raw = pdftext.extract_text(payload, layout=True)
        except pdftext.PdfTextError as exc:
            traces.append(f"{name}: 文本层抽取失败 {exc}"[:200])
            continue
        page_list = _pages(raw)
        period = _period(page_list)
        if period is None:
            traces.append(f"{name}: 未找到「截至 YYYY 年 12 月 31 日」，期次不可定")
            continue
        try:
            doc_rows, dtrace = parse_report(page_list, name)
        except UnmappedPhrase as exc:
            fatal.append(f"{name}: {exc}")
            continue
        for row in doc_rows:
            rows.append({**row, "report_period": period, "pdf_url": url,
                         "source": SOURCE, "scraped_at": now})
        traces.extend(f"{name}: {t}" for t in dtrace)
        traces.append(f"{name}（期次 {period}）: {len(doc_rows)} 行")

    LAST_TRACES.extend(traces)
    if fatal:
        raise RuntimeError("ctha-hotel-report: 锚定句失配，已中止（上游改版）— "
                           + "; ".join(fatal)[:400])
    if not rows:
        raise RuntimeError("ctha-hotel-report: 全部报告解析失败 — " + "; ".join(traces)[:400])
    metrics = [r for r in rows if r["row_kind"] == "metric"]
    ranking = [r for r in rows if r["row_kind"] == "ranking"]
    metrics.sort(key=lambda r: (r["report"], r["table"], r["dimension"], r["metric"]))
    ranking.sort(key=lambda r: (r["report"], r["table"], r["rank"]))
    return (metrics + ranking)[:limit]


if __name__ == "__main__":  # 冒烟：python3 spiders/ctha-hotel-report/spider.py
    print(json.dumps(run_ctha_hotel_report(limit=12), ensure_ascii=False, indent=2, default=str))
