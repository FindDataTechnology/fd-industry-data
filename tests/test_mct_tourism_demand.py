"""mct-tourism-demand 通道契约（add-hotel-vertical 任务 2.3）。

七份 fixture 全是**上游真件**，只做了 gzip（解压后与 MCT 页面字节相同）：

* `mct_20250530_960335.html.gz` —— 《2024年文化和旅游发展统计公报》（57,560 B）
* `mct_20251021_962874.html.gz` —— 2025 年前三季度国内旅游市场数据情况
* `mct_20260126_964367.html.gz` —— 2025 年国内居民出游数据情况
* `mct_202604_t20260429_965662.html.gz` —— 2026 年一季度国内居民出游数据情况
* `mct_202608_t20260807_966808.html.gz` —— 2026 年上半年国内居民出游数据情况
* `mct_202610_t20261026_967476.html.gz` —— 2026 年前三季度国内居民出游数据情况
* `mct_202606_t20260602_966073.html.gz` —— 《2025年文化和旅游发展统计公报》（39,599 B）

这套契约盯的是六条只有在真件上才验得出的铁律：中文散文里取数不串句、累计与单季不混粒度、
公报直报口径不与季报合并、未公布不补 0、量纲不换算、换措辞必须炸。
"""

from __future__ import annotations

import gzip
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "mct_tourism"
sys.path.insert(0, str(ROOT))

# 每个 spider 测试用**唯一模块名**加载：多个测试同进程跑时 `import spider` 会互相顶掉 sys.modules["spider"]
_MODULE = "spider_mct_tourism_demand"
_spec = importlib.util.spec_from_file_location(
    _MODULE, ROOT / "spiders" / "mct-tourism-demand" / "spider.py")
lane = importlib.util.module_from_spec(_spec)
sys.modules[_MODULE] = lane
_spec.loader.exec_module(lane)

B2024 = "mct_20250530_960335.html.gz"
D2025_Q3 = "mct_20251021_962874.html.gz"
D2025 = "mct_20260126_964367.html.gz"
D2026_Q1 = "mct_202604_t20260429_965662.html.gz"
D2026_H1 = "mct_202608_t20260807_966808.html.gz"
D2026_Q3 = "mct_202610_t20261026_967476.html.gz"
B2025 = "mct_202606_t20260602_966073.html.gz"

TITLES = {
    B2024: "2024年文化和旅游发展统计公报",
    D2025_Q3: "2025年前三季度国内旅游市场数据情况",
    D2025: "2025年国内居民出游数据情况",
    D2026_Q1: "2026年一季度国内居民出游数据情况",
    D2026_H1: "2026年上半年国内居民出游数据情况",
    D2026_Q3: "2026年前三季度国内居民出游数据情况",
    B2025: "2025年文化和旅游发展统计公报",
}
PERIODS = {
    B2024: ("2024", "fy", "bulletin"),
    D2025_Q3: ("2025Q1-Q3", "ytd_9m", "dispatch"),
    D2025: ("2025", "fy", "dispatch"),
    D2026_Q1: ("2026Q1", "quarter", "dispatch"),
    D2026_H1: ("2026H1", "ytd_h1", "dispatch"),
    D2026_Q3: ("2026Q1-Q3", "ytd_9m", "dispatch"),
    B2025: ("2025", "fy", "bulletin"),
}


def _text(name: str) -> str:
    with gzip.open(FIXTURES / name, "rb") as handle:
        return lane._flatten(handle.read().decode("utf-8", errors="replace"))


def _parse(name: str) -> list[dict]:
    period, kind, source_kind = PERIODS[name]
    text = _text(name)
    if source_kind == "dispatch":
        return lane.parse_dispatch(text, period, kind, TITLES[name], name)
    return lane.parse_bulletin(text, period, TITLES[name], name)


def _one(rows: list[dict], **want) -> dict:
    hits = [r for r in rows if all(r[k] == v for k, v in want.items())]
    assert len(hits) == 1, f"{want} 命中 {len(hits)} 行，期望恰好 1"
    return hits[0]


# ── 正文归一：style/script 必须先剥 ──────────────────────────────────────
def test_flatten_strips_inline_css_and_whitespace():
    text = _text(D2026_Q3)
    assert "display" not in text[:400].lower()
    assert "margin" not in text
    assert " " not in text and "\n" not in text
    assert "国内居民出游人次53.07亿" in text


# ── 期次：从标题解析，不按发布日 ────────────────────────────────────────
def test_period_parsed_from_title():
    for name, (period, kind, source_kind) in PERIODS.items():
        assert lane._period_of(TITLES[name], source_kind) == (period, kind, int(period[:4])), name
    assert lane._period_of("2025年前三季度国内旅游市场数据情况", "dispatch") == (
        "2025Q1-Q3", "ytd_9m", 2025)
    assert lane._period_of("2026年上半年国内居民出游数据情况", "dispatch") == (
        "2026H1", "ytd_h1", 2026)
    assert lane._period_of("2026年一季度国内居民出游数据情况", "dispatch") == (
        "2026Q1", "quarter", 2026)


# ── 累计期三档 + 同比 + 增量 ────────────────────────────────────────────
def test_latest_dispatch_anchors_match_published_text():
    rows = _parse(D2026_Q3)
    assert len(rows) == 6
    assert {r["period"] for r in rows} == {"2026Q1-Q3"}
    assert {r["period_kind"] for r in rows} == {"ytd_9m"}
    assert {r["scope"] for r in rows} == {"全国", "城镇", "农村"}
    assert _one(rows, indicator="domestic_trips", scope="全国")["value"] == 53.07
    assert _one(rows, indicator="domestic_trips", scope="全国")["yoy_pct"] == 6.2
    assert _one(rows, indicator="domestic_trips", scope="全国")["delta"] == 3.09
    assert _one(rows, indicator="domestic_trips", scope="城镇")["value"] == 40.37
    assert _one(rows, indicator="domestic_trips", scope="农村")["value"] == 12.7
    assert _one(rows, indicator="domestic_spending", scope="全国")["value"] == 4.95
    assert _one(rows, indicator="domestic_spending", scope="全国")["yoy_pct"] == 2.2


def test_delta_is_same_sentence_only_never_cross_indicator():
    """花费的增量绝不许挂到人次行（真件里两句紧邻，是实测抓出的串味缺陷）。"""
    for name in (D2025, D2025_Q3, D2026_H1, D2026_Q1, D2026_Q3):
        rows = _parse(name)
        for row in rows:
            if row["indicator"] == "domestic_trips" and row["delta_unit"]:
                assert row["delta_unit"] in ("亿", "亿人次"), (name, row)
            if row["indicator"] == "domestic_spending" and row["delta_unit"]:
                assert row["delta_unit"] in ("万亿元", "万亿"), (name, row)
    rows = _parse(D2025)
    assert _one(rows, indicator="domestic_trips", scope="农村")["delta"] is None
    assert _one(rows, indicator="domestic_spending", scope="全国")["delta"] == 0.55


def test_bulletin_travel_agency_row_does_not_inherit_next_sentence_yoy():
    """2024 公报：`旅行社64616家。…2024年国内居民出游56.2亿，同比增长14.8%` —— 不许串。"""
    rows = _parse(B2024)
    row = _one(rows, indicator="travel_agencies")
    assert row["value"] == 64616.0
    assert row["yoy_pct"] is None


# ── 粒度分离：累计 vs 单季 ──────────────────────────────────────────────
def test_half_year_release_carries_its_own_quarterly_split():
    rows = _parse(D2026_H1)
    assert len(rows) == 8                       # 6 条 H1 + 2 条分季度
    assert {r["period_kind"] for r in rows} == {"ytd_h1", "quarter"}
    assert _one(rows, indicator="domestic_trips", period="2026H1", scope="全国")["value"] == 34.63
    assert _one(rows, indicator="domestic_trips", period="2026Q1", scope="全国")["value"] == 19.01
    assert _one(rows, indicator="domestic_trips", period="2026Q2", scope="全国")["value"] == 15.62
    # 分季度拆分只给全国档，不许编出城镇/农村的单季数
    assert {r["scope"] for r in rows if r["period_kind"] == "quarter"} == {"全国"}


def test_merge_dedupes_cross_document_quarters_and_traces_agreement():
    rows = _parse(D2026_Q1) + _parse(D2026_H1)
    traces: list[str] = []
    merged = lane._merge(rows, traces)
    q1 = [r for r in merged if r["period"] == "2026Q1"
          and r["indicator"] == "domestic_trips" and r["scope"] == "全国"]
    assert len(q1) == 1, "2026Q1 全国档在两份文档里同值，必须只留一行"
    assert q1[0]["value"] == 19.01
    # Q1 稿独有的城镇/农村两档必须保留（H1 稿的分季度拆分只给全国）
    assert {r["scope"] for r in merged
            if r["period"] == "2026Q1" and r["indicator"] == "domestic_trips"} == {
        "全国", "城镇", "农村"}
    assert any("同值复现" in t for t in traces)


def test_merge_keeps_both_rows_on_conflict_and_traces_it():
    base = dict(period="2026Q1", period_kind="quarter", scope="全国",
                indicator="domestic_trips", unit="亿人次", delta=None,
                delta_unit="", indicator_note="x", report_title="t", doc_url="u",
                source=lane.SOURCE)
    a = dict(base, value=19.01, yoy_pct=6.0, source_kind="dispatch")
    b = dict(base, value=19.99, yoy_pct=9.9, source_kind="bulletin")
    traces: list[str] = []
    merged = lane._merge([a, b], traces)
    assert len(merged) == 2, "异值不许悄悄择一"
    assert any("口径冲突" in t for t in traces)


# ── 公报：需求侧 + 供给侧同年同源 ──────────────────────────────────────
def test_2025_bulletin_supply_and_demand_rows():
    rows = _parse(B2025)
    assert {r["source_kind"] for r in rows} == {"bulletin"}
    assert {r["period"] for r in rows} == {"2025"}
    expect = {
        ("domestic_trips", "全国"): (65.22, "亿人次", 16.2),
        ("domestic_trips", "城镇"): (49.96, "亿人次", 14.3),
        ("domestic_trips", "农村"): (15.26, "亿人次", 22.6),
        ("domestic_spending", "全国"): (6.3, "万亿元", 9.5),
        ("domestic_spending", "城镇"): (5.3, "万亿元", 7.5),
        ("domestic_spending", "农村"): (1.0, "万亿元", 21.4),
        ("inbound_trips", "全国"): (15450.0, "万人次", 17.1),
        ("foreign_inbound_trips", "外国"): (3517.0, "万人次", 30.6),
        ("hkmotw_inbound_trips", "港澳台"): (11932.0, "万人次", 13.7),
        ("outbound_trips", "内地"): (14836.0, "万人次", 20.8),
        ("travel_agencies", "全国"): (70354.0, "家", None),
        ("star_hotels", "全国"): (7586.0, "家", None),
        ("star_hotel_revenue", "全国"): (1448.0, "亿元", None),
        ("star_hotel_adr", "全国"): (360.2, "元/间夜", None),
        ("star_hotel_occ", "全国"): (46.8, "%", None),
        ("a_level_scenic", "全国"): (16994.0, "个", None),
        ("scenic_visits", "全国"): (75.1, "亿人次", None),
        ("scenic_revenue", "全国"): (5544.9, "亿元", None),
    }
    for (indicator, scope), (value, unit, yoy) in expect.items():
        row = _one(rows, indicator=indicator, scope=scope)
        assert row["value"] == value, indicator
        assert row["unit"] == unit, indicator
        assert row["yoy_pct"] == yoy, indicator


def test_2024_bulletin_uses_its_own_wording_variant():
    """2024 公报写「国内居民出游56.2亿人次」（人次在前面无），2025 写「出游人次65.22亿」。"""
    rows = _parse(B2024)
    assert _one(rows, indicator="domestic_trips", scope="全国")["value"] == 56.2
    assert _one(rows, indicator="domestic_trips", scope="城镇")["value"] == 43.7
    assert _one(rows, indicator="domestic_trips", scope="农村")["value"] == 12.5
    assert _one(rows, indicator="domestic_spending", scope="全国")["value"] == 5.8
    assert _one(rows, indicator="inbound_trips")["value"] == 13190.2
    assert _one(rows, indicator="outbound_trips")["value"] == 12279.2
    assert _one(rows, indicator="star_hotels")["value"] == 7716.0
    assert _one(rows, indicator="star_hotel_revenue")["value"] == 1557.3


def test_bulletin_star_hotel_caliber_is_marked_apart_from_quarterly():
    rows = _parse(B2025) + _parse(D2025)
    star_rows = [r for r in rows if r["indicator"] == "star_hotels"]
    assert {r["source_kind"] for r in star_rows} == {"bulletin"}
    # 531 季报口径的行不许冒充公报口径
    assert not any(r["indicator"].startswith("star_hotel") and r["source_kind"] == "dispatch"
                   for r in rows)


# ── 硬失败 ──────────────────────────────────────────────────────────────
def test_missing_anchor_phrase_is_hard_failure():
    with pytest.raises(lane.UnmappedPhrase):
        lane.parse_dispatch("2026年前三季度，国内旅游人次53.07亿。", "2026Q1-Q3",
                            "ytd_9m", "t", "u")


def test_run_raises_when_nothing_found(monkeypatch):
    monkeypatch.setattr(lane, "_get", lambda url, timeout=0: b"<html>no links</html>")
    with pytest.raises(RuntimeError):
        lane.run_mct_tourism_demand(limit=10, pages=1)


def test_invalid_pages_rejected():
    with pytest.raises(ValueError):
        lane.run_mct_tourism_demand(limit=10, pages=0)
    with pytest.raises(ValueError):
        lane.run_mct_tourism_demand(limit=10, pages=13)


# ── 端到端离线：假栏目页 + 真文档 ──────────────────────────────────────
def _column_html() -> str:
    return "".join(
        f'<a href="/zfxxgkml/tjxx/2026/x{i}.html">{TITLES[name]}</a>'
        for i, name in enumerate(PERIODS)
    )


def _fake_get(monkeypatch):
    def _get(url: str, timeout: int = 0) -> bytes:  # noqa: ARG001
        if url.endswith("index_3081.html"):
            return _column_html().encode()
        name = url.rsplit("/", 1)[-1].replace("x", "", 1)
        for idx, fixture in enumerate(PERIODS):
            if name == f"{idx}.html":
                with gzip.open(FIXTURES / fixture, "rb") as handle:
                    return handle.read()
        raise AssertionError(f"未预置的 URL: {url}")

    monkeypatch.setattr(lane, "_get", _get)


def test_full_run_over_seven_real_documents(monkeypatch):
    _fake_get(monkeypatch)
    rows = lane.run_mct_tourism_demand(limit=2000, pages=1)
    assert len(rows) == 60                      # 7 份文档 67 行 − 7 组同值复现
    assert {r["source_kind"] for r in rows} == {"dispatch", "bulletin"}
    assert {r["period"] for r in rows} == {
        "2024", "2025", "2025Q1-Q3", "2026Q1", "2026Q2", "2026H1", "2026Q1-Q3"}
    assert all(r["source"] == lane.SOURCE and r["scraped_at"] for r in rows)
    assert sum("同值复现" in t for t in lane.LAST_TRACES) == 7
    assert not any("口径冲突" in t for t in lane.LAST_TRACES), "真件之间不应有口径冲突"
    # 每个 (期间, 粒度, 口径, 指标) 唯一
    keys = [(r["period"], r["period_kind"], r["scope"], r["indicator"]) for r in rows]
    assert len(keys) == len(set(keys))


def test_period_filter_and_order_invariance(monkeypatch):
    _fake_get(monkeypatch)
    rows = lane.run_mct_tourism_demand(limit=2000, pages=1, periods="2026Q1-Q3,2025")
    assert len(rows) == 24
    assert {r["period"] for r in rows} == {"2025", "2026Q1-Q3"}
    assert [r["period"] for r in rows] == sorted(r["period"] for r in rows)


def test_bulletin_before_min_year_is_skipped(monkeypatch):
    """更早的公报用「国内旅游人数」措辞：整份跳过留痕，不许按新口径硬解析。"""
    html = ('<a href="/zfxxgkml/tjxx/2021/x.html">2021年文化和旅游发展统计公报</a>'
            '<a href="/zfxxgkml/tjxx/2026/y.html">2026年前三季度国内居民出游数据情况</a>')

    def _get(url: str, timeout: int = 0) -> bytes:  # noqa: ARG001
        if url.endswith("index_3081.html"):
            return html.encode()
        if url.endswith("y.html"):
            with gzip.open(FIXTURES / D2026_Q3, "rb") as handle:
                return handle.read()
        raise AssertionError("不应抓取被跳过的旧公报")

    monkeypatch.setattr(lane, "_get", _get)
    rows = lane.run_mct_tourism_demand(limit=100, pages=1)
    assert {r["period"] for r in rows} == {"2026Q1-Q3"}
    assert any("2021 公报口径不同" in t for t in lane.LAST_TRACES)


# ── manifest / golden 契约 ──────────────────────────────────────────────
def test_manifest_declares_every_emitted_column():
    import yaml

    manifest = yaml.safe_load(
        (ROOT / "spiders" / "mct-tourism-demand" / "manifest.yaml").read_text("utf-8"))
    declared = {c["name"] for c in manifest["functions"][0]["columns"]}
    emitted = set(_parse(D2026_Q3)[0])
    assert emitted <= declared
    assert manifest["functions"][0]["command"] == "run_mct_tourism_demand"
    # 2026-11-08 点亮（月探针，覆盖四种累计期发布节奏）；limit 显式声明。
    assert manifest["schedule"] == "42 10 10 * *"
    assert manifest["limit"] == 5000
    assert manifest["data_period"] == "irregular"


def test_golden_uses_only_supported_operators():
    import json

    golden = json.loads(
        (ROOT / "spiders" / "mct-tourism-demand" / "golden"
         / "001-mct-tourism-demand.json").read_text("utf-8"))
    for clause in golden["expect"]["rows_contains"]:
        assert set(clause) == {"field", "equals"}
        assert clause["field"] not in golden["whitelist_fields"]
