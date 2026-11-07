"""mct-star-hotel 通道契约（add-hotel-vertical 任务 2.3/2.4）。

六份 fixture 全是**上游真件**，只做了 gzip（内容字节与 MCT 附件逐字节相同）：

* ``P020241226577213362130.xlsx.gz`` —— 2024Q3 附件1 全国星级饭店经营情况统计表（分省 13 列）
* ``P020241226577213581254.xlsx.gz`` —— 2024Q3 附件2 同上（按星级分，16 列，合并表头）
* ``P020241226577213779637.xlsx.gz`` —— 2024Q3 附件3 全国50个重点旅游城市…统计表（50 城 13 列）
* ``P020241226577213978535.xlsx.gz`` —— 2024Q3 附件4 同上（按星级分，16 列）
* ``2024Q1_P020240530616094639089.xlsx.gz`` / ``…856823.xlsx.gz`` —— 2024Q1 附件 1/2，
  内页其实是「统计情况完成表」，且两份逐单元格相同（坑 2 的实物证据）

为什么不把这几条断言写成「跑一次网络就有」：本单元的铁律是**不把审核完成率当经营指标**、
**不把 50 城小计当全国**、**未披露不补 0**，这三条只有在真件上才验得出来。
"""

from __future__ import annotations

import gzip
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "mct_star_hotel"
sys.path.insert(0, str(ROOT))

# 每个 spider 测试用**唯一模块名**加载：多个测试同进程跑时 `import spider` 会互相顶掉 sys.modules["spider"]
_MODULE = "spider_mct_star_hotel"
_spec = importlib.util.spec_from_file_location(
    _MODULE, ROOT / "spiders" / "mct-star-hotel" / "spider.py")
lane = importlib.util.module_from_spec(_spec)
sys.modules[_MODULE] = lane
_spec.loader.exec_module(lane)

from fd_industry_data.tabular import read_grid  # noqa: E402

PERIOD = "2024Q3"
REGION = "P020241226577213362130.xlsx.gz"
STAR = "P020241226577213581254.xlsx.gz"
CITY = "P020241226577213779637.xlsx.gz"
CITY_STAR = "P020241226577213978535.xlsx.gz"
Q1_A = "2024Q1_P020240530616094639089.xlsx.gz"
Q1_B = "2024Q1_P020240530616094856823.xlsx.gz"

ALL = (REGION, STAR, CITY, CITY_STAR, Q1_A, Q1_B)


def _grid(name: str) -> list[list[str]]:
    with gzip.open(FIXTURES / name, "rb") as handle:
        return read_grid(handle.read())


def _parse(name: str, table: str | None = None) -> list[dict]:
    grid = _grid(name)
    kind = table or lane._classify(grid)
    return lane.PARSERS[kind](grid, PERIOD, kind, lane._first_text(grid), "doc://x", name)


def _one(rows: list[dict], **want) -> dict:
    hits = [r for r in rows if all(r[k] == v for k, v in want.items())]
    assert len(hits) == 1, f"{want} 命中 {len(hits)} 行，期望恰好 1"
    return hits[0]


# ── 表类型判定：只看内页标题 ──────────────────────────────────────────────
def test_classify_by_inner_title_covers_four_tables():
    assert lane._classify(_grid(REGION)) == "region"
    assert lane._classify(_grid(STAR)) == "star"
    assert lane._classify(_grid(CITY)) == "city"
    assert lane._classify(_grid(CITY_STAR)) == "city_star"


def test_2024q1_attachments_are_audit_completion_tables():
    """2024Q1 附件 1/2 名不符实：内页都是「统计情况完成表」，且两份逐格相同。"""
    a, b = _grid(Q1_A), _grid(Q1_B)
    assert lane._classify(a) == "audit-completion"
    assert lane._classify(b) == "audit-completion"
    assert a == b, "2024Q1 附件 1/2 实测逐单元格完全相同"
    assert "完成表" in lane._first_text(a)
    # 完成表的列（总数/完成数/退回重填/停业/合格率）一列都不该被经营解析器认领：
    # 连表头都找不到 → UnmappedColumn，而不是被悄悄当成分省表少列入库
    with pytest.raises(lane.UnmappedColumn):
        lane._header_row(a)


# ── 分省表：全国锚 + 33 个地区 ────────────────────────────────────────────
def test_region_national_anchor_matches_published_2024q3():
    rows = _parse(REGION)
    assert len(rows) == 396                      # 33 地区 × 12 个非空指标列
    assert len({r["area"] for r in rows}) == 33  # 全国 + 31 省 + 新疆兵团
    assert {r["area_kind"] for r in rows if r["area"] == "全国"} == {"全国"}
    assert all(r["table"] == "region" and r["star"] == "" for r in rows)
    for indicator, value, unit in (
        ("count", 6129.0, "家"),
        ("revenue", 390.01, "亿元"),
        ("revenue_share_fnb", 35.76, "%"),
        ("revenue_share_room", 47.33, "%"),
        ("adr", 375.1, "元/间夜"),
        ("occupancy", 52.96, "%"),
        ("revpar", 198.66, "元/间夜"),
        ("revenue_per_room", 37124.31, "元/间"),
    ):
        row = _one(rows, area="全国", indicator=indicator)
        assert row["value"] == pytest.approx(value, abs=1e-6), indicator
        assert row["unit"] == unit


def test_region_indicator_slugs_cover_all_thirteen_columns():
    """13 列 = 地区 + 12 指标；同比列必须与量值列分开（否则同比冒充量值）。"""
    assert set(lane.COLUMN_MAP.values()) == {
        (slug, unit) for slug, unit in {v for v in lane.COLUMN_MAP.values()}
    }
    slugs = {slug for slug, _unit in lane.COLUMN_MAP.values()}
    assert len(slugs) == 12
    for base in ("adr", "occupancy", "revpar", "revenue_per_room"):
        assert base in slugs and f"{base}_yoy" in slugs
    rows = _parse(REGION)
    assert {r["indicator"] for r in rows} == slugs


def test_region_province_rows_keep_verbatim_labels():
    rows = _parse(REGION)
    assert _one(rows, area="北京", indicator="count")["value"] == 273.0
    assert _one(rows, area="上海", indicator="adr")["value"] == 721.41
    assert _one(rows, area="广东", indicator="revpar")["value"] == 219.22


# ── 50 城表：全国行 = 50 城小计，绝不与全国混算 ───────────────────────────
def test_city_national_row_is_fifty_city_subtotal():
    rows = _parse(CITY)
    assert len(rows) == 612
    assert len({r["area"] for r in rows}) == 51
    row = _one(rows, area="全国", indicator="count")
    assert row["area_kind"] == "50城合计", "50 城表的「全国」是 50 城小计"
    assert row["value"] == 2209.0            # vs 全国 6129 家
    assert _one(rows, area="全国", indicator="revpar")["value"] == 283.2
    assert _one(rows, area="全国", indicator="adr")["value"] == 474.22
    # 城市行的 area_kind 是「城市」，不是「省」
    assert _one(rows, area="北京", indicator="count")["area_kind"] == "城市"


def test_city_and_region_do_not_share_area_kind_vocabulary():
    region = _parse(REGION)
    city = _parse(CITY)
    assert {r["area_kind"] for r in region} == {"全国", "省"}
    assert {r["area_kind"] for r in city} == {"50城合计", "城市"}


# ── 按星级表：合并表头前向继承 + 未披露整行不写 ──────────────────────────
def test_star_header_forward_fills_tier_prefix():
    """档位前缀只在每组首格；第 2/3 列（平均房价/出租率）必须继承上一档。"""
    header = _grid(STAR)[lane._header_row(_grid(STAR))[0]]
    assert lane._squash(header[2]) == "平均房价(元/间夜)"   # 真件里不带「一星饭店」前缀
    mapping = lane._map_star_columns(header)
    assert [(star, slug) for _idx, star, slug, _u, _l in mapping] == [
        (str(tier), slug)
        for tier in (1, 2, 3, 4, 5)
        for slug in ("count", "adr", "occupancy")
    ]
    assert len(mapping) == 15


def test_star_national_tiers_exact_values():
    rows = _parse(STAR)
    assert {r["indicator"] for r in rows} == {"count", "adr", "occupancy"}
    assert all(r["star"] in {"1", "2", "3", "4", "5"} for r in rows)
    assert all(r["area_kind"] == "全国" for r in rows if r["area"] == "全国")
    assert _one(rows, area="全国", star="3", indicator="count")["value"] == 2811.0
    assert _one(rows, area="全国", star="3", indicator="adr")["value"] == 230.22
    assert _one(rows, area="全国", star="3", indicator="occupancy")["value"] == 47.9
    assert _one(rows, area="全国", star="5", indicator="adr")["value"] == 599.72
    assert sum(
        _one(rows, area="全国", star=t, indicator="count")["value"]
        for t in "12345"
    ) == 6129.0, "五档家数之和应等于分省表全国总数"


def test_undisclosed_cells_are_dropped_not_zero_filled():
    """星级表 101 个全角「－」＝ 不写行；33×15 − 101 = 394。"""
    grid = _grid(STAR)
    dashes = sum(
        1 for raw in grid for cell in raw if lane._squash(cell) in ("－",)
    )
    rows = _parse(STAR)
    assert dashes == 101
    assert len(rows) == 33 * 15 - dashes == 394
    assert all(isinstance(r["value"], float) for r in rows)
    assert not any(r["value"] == 0 for r in rows), "未见真实的 0 值，出现即补 0 的痕迹"


def test_city_star_shape_and_dash_budget():
    grid = _grid(CITY_STAR)
    dashes = sum(1 for raw in grid for cell in raw if lane._squash(cell) == "－")
    rows = _parse(CITY_STAR)
    assert dashes == 247
    assert len(rows) == 51 * 15 - dashes == 518
    assert {r["area_kind"] for r in rows if r["area"] == "全国"} == {"50城合计"}
    assert _one(rows, area="全国", star="3", indicator="count")["value"] == 760.0


# ── 硬失败：上游改表必须炸 ──────────────────────────────────────────────
def test_unknown_region_column_is_hard_failure():
    grid = _grid(REGION)
    start = lane._header_row(grid)[0]
    broken = [list(row) for row in grid]
    broken[start][6] = "平均出租率口径调整（%）"
    with pytest.raises(lane.UnmappedColumn):
        lane.parse_region([list(r) for r in broken], PERIOD, "region", "t", "u", "x")


def test_unknown_star_column_is_hard_failure():
    grid = _grid(STAR)
    start = lane._header_row(grid)[0]
    broken = [list(row) for row in grid]
    broken[start][10] = "四星饭店\n平均净房价(元/间夜)"
    with pytest.raises(lane.UnmappedColumn):
        lane.parse_star([list(r) for r in broken], PERIOD, "star", "t", "u", "x")


def test_missing_star_prefix_without_inheritance_context_is_hard_failure():
    """档位前缀与已知列名都不认的裸表头 → 硬失败，不是静默丢列。"""
    header = ["地区", "神秘指标", "平均房价(元/间夜)"]
    with pytest.raises(lane.UnmappedColumn):
        lane._map_star_columns(header)


def test_header_with_no_known_column_is_hard_failure():
    with pytest.raises(lane.UnmappedColumn):
        lane._header_row([["地区", "甲", "乙"], ["北京", "1", "2"]])


# ── 链接归一：urljoin 且 base 不补尾斜杠（坑 8） ─────────────────────────
def test_relative_detail_link_joins_to_absolute_url():
    assert lane._absolute(
        "../../tjxx/202412/t20241226_957396.html",
        "https://zwgk.mct.gov.cn/zfxxgkml/447/465/index_3081.html",
    ) == "https://zwgk.mct.gov.cn/zfxxgkml/tjxx/202412/t20241226_957396.html"
    assert lane._page_url(0).endswith("index_3081.html")
    assert lane._page_url(2).endswith("index_3081_2.html")


def test_period_parsed_from_title_not_publish_date():
    assert lane._period_of("2024年第三季度全国星级旅游饭店统计调查报告") == "2024Q3"
    assert lane._period_of("2023年第四季度全国星级旅游饭店统计调查报告") == "2023Q4"
    assert lane._period_of("2023年度全国星级旅游饭店统计调查报告") is None


# ── 端到端离线：假 fetch，真附件 ────────────────────────────────────────
COLUMN_HTML = """
<div class="list"><ul>
  <li><a href="../../tjxx/202412/t20241226_957396.html">
      2024年第三季度全国星级旅游饭店统计调查报告</a></li>
  <li><a href="../../tjxx/202409/t20240930_999999.html">
      2024年第二季度全国星级旅游饭店统计调查报告</a></li>
  <li><a href="../../tjxx/202403/t20240301_888888.html">文化和旅游部召开新闻发布会</a></li>
</ul></div>
"""


def _detail_html(names: list[str]) -> str:
    links = "".join(
        f'<div class="nyb_fj">'
        f'<a href="/zfxxgkml/447/465/202412/{name.removesuffix(".gz")}">{name}</a></div>'
        for name in names
    )
    return f"<html><body><p>2024年第三季度全国星级旅游饭店统计调查报告</p>{links}</body></html>"


def _payload_for(names: list[str], payload: dict[str, bytes]) -> None:
    """附件 URL 是 ``.xlsx``（真件名带 .gz 只为入库体积），故按去掉 .gz 的名字注册。"""
    for name in names:
        with gzip.open(FIXTURES / name, "rb") as handle:
            payload[name.removesuffix(".gz")] = handle.read()


def _fake_get(mapping: dict[str, bytes]):
    def _get(url: str, timeout: int = 0) -> bytes:  # noqa: ARG001
        for key, payload in mapping.items():
            if url.endswith(key):
                return payload
        raise AssertionError(f"未预置的 URL: {url}")

    return _get


def test_run_end_to_end_offline_on_real_attachments(monkeypatch):
    payload = {"index_3081.html": COLUMN_HTML.encode()}
    payload["t20241226_957396.html"] = _detail_html(
        [REGION, STAR, CITY, CITY_STAR]).encode()
    payload["t20240930_999999.html"] = _detail_html([REGION]).encode()
    _payload_for([REGION, STAR, CITY, CITY_STAR], payload)

    monkeypatch.setattr(lane, "_get", _fake_get(payload))
    rows = lane.run_mct_star_hotel(limit=5000, pages=1)

    assert len(rows) == 396 + 394 + 612 + 518 + 396
    assert {r["period"] for r in rows} == {"2024Q3", "2024Q2"}
    assert {r["table"] for r in rows} == {"region", "star", "city", "city_star"}
    assert all(r["source"] == "mct-star-hotel" and r["scraped_at"] for r in rows)
    assert any("解析表" in trace for trace in lane.LAST_TRACES)


def test_run_skips_audit_completion_and_fails_loudly_if_nothing_left(monkeypatch):
    """只挂「完成表」的一期 → 一行经营数据都不许产出（宁愿红）。"""
    payload = {"index_3081.html": COLUMN_HTML.encode()}
    payload["t20241226_957396.html"] = _detail_html([Q1_A, Q1_B]).encode()
    payload["t20240930_999999.html"] = _detail_html([Q1_A]).encode()
    _payload_for([Q1_A, Q1_B], payload)

    monkeypatch.setattr(lane, "_get", _fake_get(payload))
    with pytest.raises(RuntimeError):
        lane.run_mct_star_hotel(limit=5000, pages=1)
    assert any("audit-completion" in trace for trace in lane.LAST_TRACES)


def test_run_raises_when_column_page_has_no_report(monkeypatch):
    html = '<div class="list"><a href="/x.html">文化和旅游部新闻发布会</a></div>'
    monkeypatch.setattr(lane, "_get", _fake_get({"index_3081.html": html.encode()}))
    with pytest.raises(RuntimeError):
        lane.run_mct_star_hotel(limit=10, pages=1)


def test_period_filter_keeps_requested_quarter_only(monkeypatch):
    payload = {"index_3081.html": COLUMN_HTML.encode()}
    payload["t20241226_957396.html"] = _detail_html([REGION]).encode()
    payload["t20240930_999999.html"] = _detail_html([CITY]).encode()
    _payload_for([REGION, CITY], payload)

    monkeypatch.setattr(lane, "_get", _fake_get(payload))
    rows = lane.run_mct_star_hotel(limit=5000, pages=1, periods="2024q3")
    assert {r["period"] for r in rows} == {"2024Q3"}
    assert {r["table"] for r in rows} == {"region"}


def test_invalid_pages_is_rejected():
    with pytest.raises(ValueError):
        lane.run_mct_star_hotel(limit=10, pages=0)
    with pytest.raises(ValueError):
        lane.run_mct_star_hotel(limit=10, pages=13)


# ── manifest / golden 契约 ────────────────────────────────────────────────
def test_manifest_declares_every_column_the_parser_emits():
    import yaml

    manifest = yaml.safe_load(
        (ROOT / "spiders" / "mct-star-hotel" / "manifest.yaml").read_text("utf-8"))
    declared = {c["name"] for c in manifest["functions"][0]["columns"]}
    emitted = set(_parse(REGION)[0])
    assert emitted <= declared
    assert manifest["functions"][0]["command"] == "run_mct_star_hotel"
    # 2026-11-08 用户授权点亮：月探针用于断更/复更检测（系列最新期 2024Q3）。
    # 点亮后每轮必须取全量快照，故 limit 必须显式声明（chart 默认 100 会截断）。
    assert manifest["schedule"] == "42 11 20 * *"
    assert manifest["limit"] == 20000
    assert manifest["data_period"] == "irregular"


def test_golden_only_uses_supported_expect_shape():
    import json

    golden = json.loads(
        (ROOT / "spiders" / "mct-star-hotel" / "golden" / "001-mct-star-hotel.json")
        .read_text("utf-8"))
    for clause in golden["expect"]["rows_contains"]:
        assert set(clause) == {"field", "equals"}, "golden 只支持 {field, equals}"
        assert clause["field"] not in golden["whitelist_fields"]
    assert golden["params"]["periods"] == PERIOD
