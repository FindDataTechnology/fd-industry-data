"""spiders/mct-star-hotel —— 文化和旅游部「全国星级旅游饭店统计调查报告」季度取数。

数据表面（公开静态页 + xlsx 直链，免鉴权；2026-11-08 实测直连 200）：

- 栏目：``GET https://zwgk.mct.gov.cn/zfxxgkml/447/465/index_3081.html``
  （统计信息；**少一层 ``447`` 直接 404**）。分页是静态文件
  ``index_3081.html`` → ``index_3081_1.html`` → ``_2`` …（共 40 页）。
- 详情页：正文是**内嵌 PNG 长图**（2024Q1 单张 1335×6568 / 609 KB），**无 PDF、无文本层**，
  故正文口径说明不入库（只留 ``doc_url`` 可回溯）。
- 附件：每期 **4 张 xlsx**，文件名是纯流水号（``P020241226577213362130.xlsx``），
  无语义，必须从详情页 ``.nyb_fj`` 块的 ``href`` 抓，**不拼接猜测**。

四张表（2024Q3 真件实测）：

===========  =========================  ===================================
附件          工作表标题（内页首行）          维度
===========  =========================  ===================================
1            全国星级饭店经营情况统计表        分省（全国 + 31 省 + 新疆兵团 = 33）
2            同上「（按星级分）」            分省 × 星级（5 档 × 数量/ADR/OCC）
3            全国50个重点旅游城市…统计表      50 城市（51 行，含「全国」= 50 城小计）
4            同上「（按星级分）」            50 城市 × 星级
===========  =========================  ===================================

口坑（侦察实测，逐一落实）：

1. **系列已停更**：最新一期是 2024Q3（2024-12-26）。2024Q4 / 2025Q1–Q3 / 2026Q1–Q3
   精确检索 ``totalHits=0``；栏目 2024-12-26 之后 12 条无星级饭店条目。故本单元
   只能产出 2023Q4–2024Q3 四期，「当期更新」不成立 —— 断更检测交给发布节奏预警。
2. **附件名不符实**：2024Q1 的附件 1/2 声称是「经营情况统计表」与「（按星级分）」，
   实际内页都是「全国各地星级饭店**统计情况完成表**」（``星级饭店总数/完成数/退回重填/
   停业饭店数/合格率``，合计 7947/6295/13/861/90.21），且两份逐单元格完全相同。
   → **不按附件名、不按附件序判定表类型，一律读内页标题**（``_classify``）；
   命中「完成表」即整份跳过并留痕，绝不把审核完成率当经营指标入库。
3. **列名映射按标题匹配、不按列序**（任务 2.4）：表头可能跨两行且单元格内带换行
   （``一星饭店\\n数量``），统一压掉空白后按标题查表；**任何一列映射不中即硬失败**
   （``UnmappedColumn``，整次运行 ``RuntimeError``）——上游改表必须炸，不能静默少列。
4. **50 城表的「全国」行是 50 城小计**（2024Q3：2209 家 vs 全国 6129 家），
   故行内带 ``area_kind``（``全国`` / ``50城合计`` / ``省`` / ``城市``），
   抽取器据此分开落库，绝不混算。
5. **空值是全角「－」**（U+FF0D），不是 ``-``、不是空、不等于 0。解析为 ``None``
   后**整行不入库**（不补 0、不插值）。
6. **同比列与量值列同表**：``平均房价同比（%）`` 与 ``平均房价(元/间夜)`` 是两列，
   指标 slug 必须区分（``adr`` vs ``adr_yoy``），否则同比值会冒充量值。
7. 年度 ``.xls`` 单位不同（``千元/间·年`` vs 季度 ``元/间·季``，ADR 写「元」而非
   「元/间夜」）—— 本单元**只取季度 xlsx**，年度附件整份跳过并留痕，不做单位换算。
8. 附件是流水号，同一期四张表的 ``href`` 顺序**不可依赖**；只按内页标题分派。

工单：``kind=generate``；``data_period: irregular``（季频但词表无 quarter，
且该系列实际已断更 —— 用 ``month`` 会在两季之间假超期）。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SOURCE = "mct-star-hotel"
BASE = "https://zwgk.mct.gov.cn"
COLUMN_URL = BASE + "/zfxxgkml/447/465/index_3081.html"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
TIMEOUT = 30

#: 详情页标题必须是星级饭店统计调查报告；顺带排掉「旅行社」「出游数据」等同栏目条目。
TITLE_HINT = "星级旅游饭店统计调查报告"
TITLE_RE = re.compile(r"(\d{4})\s*年\s*第\s*([一二三四])\s*季度")
QUARTER_NUM = {"一": 1, "二": 2, "三": 3, "四": 4}

#: 详情页附件块：站方把附件放在 ``class="nyb_fj"`` 的块里，链接都是流水号文件名。
FJ_HREF_RE = re.compile(r'href="([^"]+\.xlsx?)"', re.I)

#: 内页标题 → 表类型。**按标题判定，不按附件序、不按文件名**（口坑 2）。
#: 命中 ``完成表`` 的表是「审核完成率」表，不是经营指标，整份跳过。
CLASSIFY_RULES = (
    ("完成表", "audit-completion"),        # 必须先判：它同时含「星级饭店」字样
    ("按星级分", "star"),                  # 再判星级（附件 2/4 共用此词）
    ("50个重点旅游城市", "city"),           # 50 城汇总表
    ("经营情况统计表", "region"),           # 分省经营情况表（兜底）
)

#: 列名映射（标题归一后精确匹配）——任务 2.4 的落点。
#: 键 = 压掉全部空白后的表头原文；值 = (indicator slug, unit)。
COLUMN_MAP = {
    "数量（家）": ("count", "家"),
    "营业收入（亿元）": ("revenue", "亿元"),
    "餐饮收入比重（%）": ("revenue_share_fnb", "%"),
    "客房收入比重（%）": ("revenue_share_room", "%"),
    "平均房价(元/间夜)": ("adr", "元/间夜"),
    "平均出租率（%）": ("occupancy", "%"),
    "每间可供出租客房收入（元/间夜）": ("revpar", "元/间夜"),
    "每间客房平摊营业收入（元/间）": ("revenue_per_room", "元/间"),
    "平均房价同比（%）": ("adr_yoy", "%"),
    "平均出租率同比（%）": ("occupancy_yoy", "%"),
    "每间可供出租客房收入同比（%）": ("revpar_yoy", "%"),
    "每间客房平摊营业收入同比（%）": ("revenue_per_room_yoy", "%"),
}
#: 星级表：``一星饭店数量`` 这类「档位 + 指标」拼接表头 → 拆成 (star, 指标 slug)。
STAR_PREFIX = (("一星饭店", "1"), ("二星饭店", "2"), ("三星饭店", "3"),
               ("四星饭店", "4"), ("五星饭店", "5"))
STAR_COLUMNS = {
    "数量": ("count", "家"),
    "平均房价(元/间夜)": ("adr", "元/间夜"),
    "平均出租率（%）": ("occupancy", "%"),
}

#: 行标签里的「全国」在不同表里含义不同（口坑 4）。
FULL_COUNTRY = "全国"
CITY_AGGREGATE = "50城合计"

#: 空值：全角连字符、半角连字符、空、各种 n/a（口坑 5）。
EMPTY_MARKS = {"", "-", "－", "—", "–", "n/a", "N/A", "NA", "/", "…", "..."}


#: 上一次运行的过程留痕（跳过的表、失败的附件、命中的表类型）。
#: 不是返回值的一部分（产出契约只有 14 列），但**可审计**：单表失败不静默，
#: 调用方/测试可以读它确认「丢的是哪一张、为什么丢」。
LAST_TRACES: list[str] = []


class UnmappedColumn(ValueError):
    """表头出现未知列 —— 上游改表，必须硬失败（任务 2.4）。"""


def _squash(text: str) -> str:
    """压掉全部空白（含单元格内换行、全角空格）——表头匹配与行标签归一都用它。"""
    return re.sub(r"\s+", "", (text or "").replace("\xa0", ""))


def _get(url: str, timeout: int = TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 固定 https 主机
        return resp.read()


def _absolute(href: str, base: str = BASE) -> str:
    """相对链接 → 绝对。**必须 urljoin，且 base 不能补尾斜杠**。

    栏目页里的详情链接实测是 ``../../tjxx/202412/t20241226_957396.html``（相对
    ``/zfxxgkml/447/465/index_3081.html``）。补尾斜杠会把 base 当目录，``../../``
    就少退一层 → 全部 404；手工拼 ``BASE + href`` 会得到非法主机名 → 全部 400。
    """
    return urllib.parse.urljoin(base, href)


def _page_url(page: int) -> str:
    """分页静态文件名：第 1 页 ``index_3081.html``，第 2 页 ``index_3081_1.html``。"""
    return COLUMN_URL if page == 0 else COLUMN_URL.replace(
        "index_3081.html", f"index_3081_{page}.html")


def _num(text: str) -> float | None:
    """单元格 → 数值；空值标记与不可解析都返回 None（不补 0）。"""
    raw = _squash(text).replace(",", "").replace("，", "")
    if raw in EMPTY_MARKS:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


# ------------------------------------------------------------------ 页面走栏


def _detail_links(html: str, page_url: str = COLUMN_URL) -> list[tuple[str, str]]:
    """栏目页 → [(标题, 详情页 URL)]，只留星级饭店统计调查报告。"""
    out: list[tuple[str, str]] = []
    for m in re.finditer(r'<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
        href, inner = m.group(1), m.group(2)
        title = re.sub(r"<[^>]+>|\s+", " ", inner).strip()
        if TITLE_HINT not in title:
            continue
        out.append((title, _absolute(href, page_url)))
    return out


def _period_of(title: str) -> str | None:
    """标题 → ``YYYYQN``；不是季度稿返回 None。"""
    m = TITLE_RE.search(title)
    if not m:
        return None
    return f"{m.group(1)}Q{QUARTER_NUM[m.group(2)]}"


def _attachments(detail_html: str, doc_url: str = COLUMN_URL) -> list[str]:
    """详情页附件直链（去重保序）。含 .xls（年度件），由调用方按扩展名过滤。"""
    seen: list[str] = []
    for m in re.finditer(r'class="nyb_fj"(.*?)(?=<div\s+class=|</body>)', detail_html, re.S | re.I):
        for h in FJ_HREF_RE.finditer(m.group(1)):
            url = _absolute(h.group(1), doc_url)
            if url not in seen:
                seen.append(url)
    if seen:  # 附件块结构变了也兜底：退化为全页 xlsx 链接
        return seen
    for h in FJ_HREF_RE.finditer(detail_html):
        url = _absolute(h.group(1), doc_url)
        if url not in seen:
            seen.append(url)
    return seen


# ---------------------------------------------------------------- 网格解析


def _first_text(grid: list[list[str]]) -> str:
    """前 3 行全部非空单元拼起来当标题。

    为什么不是「第一个非空单元」：附件内页第 1 行常常只有 ``附件1`` 这种序号，
    真标题在第 2 行（``2024年第一季度全国各地星级饭店统计情况完成表``）。只取首个
    非空单元会让 2024Q1 的「完成表」认不出来（口坑 2 就靠这句判定）。
    """
    parts: list[str] = []
    for row in grid[:3]:
        for cell in row:
            text = _squash(cell)
            if text:
                parts.append(text)
    return "".join(parts)


def _classify(grid: list[list[str]]) -> str:
    """按内页标题判定表类型（口坑 2：不按附件名/附件序）。"""
    title = _first_text(grid)
    if "按星级分" in title and "50个重点旅游城市" in title:
        return "city_star"
    if "按星级分" in title:
        return "star"
    if "50个重点旅游城市" in title:
        return "city"
    for needle, kind in CLASSIFY_RULES:
        if needle in title:
            return kind
    return "unknown"


def _header_row(grid: list[list[str]]) -> tuple[int, list[str]]:
    """找表头行：命中 ``COLUMN_MAP`` 键最多的那一行（跨两行表头取下半行）。"""
    best = (-1, 0)
    for i, row in enumerate(grid[:8]):
        hits = sum(1 for cell in row if _squash(cell) in COLUMN_MAP)
        if hits > best[1]:
            best = (i, hits)
    if best[1] == 0:
        raise UnmappedColumn(f"表头未命中任何已知列名（前 3 行：{grid[:3]}）")
    return best[0], grid[best[0]]


def _map_region_columns(header: list[str]) -> list[tuple[int, str, str, str]]:
    """13 列表头 → [(列号, 指标 slug, 单位, 原文)]；遇未知列即抛 ``UnmappedColumn``。"""
    mapping: list[tuple[int, str, str, str]] = []
    for idx, cell in enumerate(header):
        key = _squash(cell)
        if not key or key in ("指标/地区", "指标", "地区", "指标地区"):
            continue
        if key not in COLUMN_MAP:
            raise UnmappedColumn(f"未知列名 {cell!r}（第 {idx + 1} 列）——上游改表")
        slug, unit = COLUMN_MAP[key]
        mapping.append((idx, slug, unit, key))
    if not mapping:
        raise UnmappedColumn("13 列表头解析为空")
    return mapping


def _map_star_columns(header: list[str]) -> list[tuple[int, str, str, str, str]]:
    """16 列表头 → [(列号, star, 指标 slug, 单位, 原文)]；未知列即硬失败。

    **档位是合并表头**：原表只在一档的第一列写 ``一星饭店``，同档后两列（平均房价 /
    平均出租率）是裸列名，靠视觉合并继承档位。所以档位必须**前向继承**——
    实测 2024Q3 附件 2 第 3 列原文就是 ``平均房价\\n(元/间夜)``，不带前缀。
    """
    mapping: list[tuple[int, str, str, str, str]] = []
    current_star: str | None = None
    for idx, cell in enumerate(header):
        key = _squash(cell)
        if not key or key in ("地区", "指标/地区"):
            continue
        for prefix, tier in STAR_PREFIX:
            if key.startswith(prefix):
                current_star = tier
                key = key[len(prefix):]
                break
        if not key:  # 只写了档位名、没有指标名的合并单元
            continue
        if current_star is None:
            raise UnmappedColumn(f"星级表第 {idx + 1} 列 {cell!r} 之前没有任何档位前缀")
        if key not in STAR_COLUMNS:
            raise UnmappedColumn(f"未知星级表列名 {cell!r}（第 {idx + 1} 列）——上游改表")
        slug, unit = STAR_COLUMNS[key]
        mapping.append((idx, current_star, slug, unit, key))
    if not mapping:
        raise UnmappedColumn("16 列表头解析为空")
    return mapping


def _area_kind(table: str, area: str) -> str:
    """行标签在「哪张表里」决定语义（口坑 4）：50 城表的「全国」是 50 城合计。"""
    if area != FULL_COUNTRY:
        return "省" if table == "region" else ("城市" if table == "city" else "星级")
    return CITY_AGGREGATE if table == "city" else FULL_COUNTRY


def parse_region(grid, period, table, report_title, doc_url, xlsx_url) -> list[dict]:
    """分省 / 50 城（同构 13 列）→ 长表行。"""
    start, header = _header_row(grid)
    mapping = _map_region_columns(header)
    rows: list[dict] = []
    for raw in grid[start + 1:]:
        area = _squash(raw[0]) if raw else ""
        if not area or area in ("指标/地区", "地区"):
            continue
        for idx, slug, unit, label in mapping:
            value = _num(raw[idx]) if idx < len(raw) else None
            if value is None:  # 未披露 / 全角「－」：不写行（不补 0、不插值）
                continue
            rows.append({
                "period": period, "table": table, "area": area,
                "star": "", "indicator": slug, "indicator_label": label,
                "value": value, "unit": unit,
                "area_kind": _area_kind(table, area),
                "report_title": report_title, "doc_url": doc_url,
                "xlsx_url": xlsx_url, "source": SOURCE,
            })
    return rows


def parse_star(grid, period, table, report_title, doc_url, xlsx_url) -> list[dict]:
    """按星级（16 列）→ 长表行；``area`` 保留原行标签（省 / 城市），``star`` 落档位。"""
    start, header = _header_row(grid)
    mapping = _map_star_columns(header)
    rows: list[dict] = []
    for raw in grid[start + 1:]:
        area = _squash(raw[0]) if raw else ""
        if not area or area in ("地区", "指标/地区"):
            continue
        for idx, star, slug, unit, label in mapping:
            value = _num(raw[idx]) if idx < len(raw) else None
            if value is None:
                continue
            rows.append({
                "period": period, "table": table, "area": area,
                "star": star, "indicator": slug, "indicator_label": label,
                "value": value, "unit": unit,
                "area_kind": _area_kind("region" if table == "star" else "city", area),
                "report_title": report_title, "doc_url": doc_url,
                "xlsx_url": xlsx_url, "source": SOURCE,
            })
    return rows


PARSERS = {"region": parse_region, "city": parse_region,
           "star": parse_star, "city_star": parse_star}


# --------------------------------------------------------------------- 入口


def run_mct_star_hotel(limit: int = 5000, pages: int = 3,
                       periods: str | None = None) -> list[dict]:
    """抓 2023Q4–2024Q3 四期星级饭店季报，产出 (期间, 表, 地区/星级, 指标) 长表行。

    Args:
        limit: 最大返回行数（默认 5000，四期全量约 4 期 × 4 表 × 数百行）。
        pages: 栏目页翻页数（默认 3；第 1 页仅到 2024-09-02，2023Q4 需第 2 页）。
        periods: 逗号分隔的期次过滤（如 ``"2024Q3,2024Q2"``）；缺省取全部命中。

    Raises:
        ValueError: 参数非法。
        RuntimeError: 全部期次取数失败，或出现 ``UnmappedColumn``（硬失败，上游改表）。
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
    want = {p.strip().upper() for p in periods.split(",")} if periods else None

    now = datetime.now(timezone.utc).isoformat()
    traces: list[str] = []
    LAST_TRACES.clear()
    rows: list[dict] = []
    fatal: list[str] = []

    details: list[tuple[str, str]] = []
    for page in range(pages):
        try:
            page_url = _page_url(page)
            details.extend(_detail_links(
                _get(page_url).decode("utf-8", "replace"), page_url))
        except Exception as exc:  # noqa: BLE001 单页失败只留痕
            traces.append(f"page {page}: {type(exc).__name__}: {exc}"[:200])
    # 同期可能被栏目重复挂载：按期次去重，保留先出现的
    seen_periods: set[str] = set()
    picked: list[tuple[str, str]] = []
    for title, url in details:
        period = _period_of(title)
        if not period or (want and period not in want) or period in seen_periods:
            continue
        seen_periods.add(period)
        picked.append((period, url))
    if not picked:
        raise RuntimeError("mct-star-hotel: 栏目页未命中任何星级饭店季度报告 — "
                           + "; ".join(traces)[:300])

    for period, doc_url in sorted(picked, reverse=True):
        try:
            detail = _get(doc_url).decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            traces.append(f"{period}: detail {type(exc).__name__}: {exc}"[:200])
            continue
        urls = [u for u in _attachments(detail, doc_url) if u.lower().endswith(".xlsx")]
        if not urls:
            traces.append(f"{period}: 详情页无 xlsx 附件")
            continue
        hit_tables: set[str] = set()
        for xlsx_url in urls:
            try:
                from fd_industry_data.tabular import read_grid

                grid = read_grid(_get(xlsx_url))
                kind = _classify(grid)
                if kind in ("audit-completion", "unknown"):
                    traces.append(f"{period}: 跳过 {kind} 表（{_first_text(grid)[:40]}）")
                    continue
                parser = PARSERS[kind]
                title = _first_text(grid)
                for row in parser(grid, period, kind, title, doc_url, xlsx_url):
                    rows.append({**row, "scraped_at": now})
                hit_tables.add(kind)
            except UnmappedColumn as exc:
                # 硬失败：上游改表不允许静默少列（任务 2.4）
                fatal.append(f"{period} {xlsx_url}: {exc}")
            except Exception as exc:  # noqa: BLE001 单表失败只跳过并留痕
                traces.append(f"{period} {xlsx_url.rsplit('/', 1)[-1]}: "
                              f"{type(exc).__name__}: {exc}"[:200])
        traces.append(f"{period}: 解析表 {sorted(hit_tables) or '无'}")

    LAST_TRACES.extend(traces)
    if fatal:
        raise RuntimeError("mct-star-hotel: 表头出现未知列，已中止（上游改表）— "
                           + "; ".join(fatal)[:400])
    if not rows:
        LAST_TRACES.extend(traces)
        raise RuntimeError("mct-star-hotel: 全部期次取数失败 — " + "; ".join(traces)[:400])
    return rows[:limit]


if __name__ == "__main__":  # 冒烟：python3 spiders/mct-star-hotel/spider.py
    print(json.dumps(run_mct_star_hotel(limit=6), ensure_ascii=False, indent=2))
