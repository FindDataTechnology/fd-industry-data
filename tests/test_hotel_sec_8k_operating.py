"""美股酒店 8-K exhibit 99.1 通道契约（add-hotel-vertical 任务 4.3）。

六份 fixture 全是**上游真件**，未改写：

* ``sec_{mar,hlt,wh,h,chh}_2026q3.htm.gz`` —— 2026Q3 业绩新闻稿 exhibit 99.1
  的原始 HTML（gzip 只为入库体积，内容是原样字节解压结果）。
  MAR 1,273,792 字节 / HLT 621,354 / WH 659,112 / H 791,879 / CHH 577,355。
* ``sec_{mar,hlt,wh,h,chh}_8k_primary.htm`` —— 对应 8-K 正文（exhibit 清单所在）。

这份数据面为什么值得单独建车道：``companyfacts``（sec-edgar-facts）只返回
**无维度**事实，分地区 / 分品牌 RevPAR 只在新闻稿的表格里。边界注记随单元
文档保留：exhibit 99.1 是业绩新闻稿，不是财报正文。

五家五套表格形态（列序、行名、期间块都不同），所以解析按逐家 mapping，
不按列序猜；指标归属由「RevPAR ＝ ADR × 出租率」恒等式裁决。
"""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "spiders" / "sec-edgar-8k-operating"))

import hotel_extractors.sec_8k_operating as mod  # noqa: E402
import spider as lane  # noqa: E402

PERIOD_END = "2026-09-30"


def _html(name: str) -> str:
    with gzip.open(FIXTURES / name, "rb") as handle:
        return handle.read().decode("utf-8", errors="replace")


def _parse(ticker: str, notes: list | None = None) -> list[dict]:
    return mod.PARSERS[ticker](
        _html(f"sec_{ticker.lower()}_2026q3.htm.gz"), PERIOD_END, notes if notes is not None else []
    )


def _by(records: list[dict], metric: str) -> dict[str, str]:
    return {r["series"]: r["value"] for r in records if r["metric"] == metric}


# ── MAR：3M 而非 9M，两个口径不混写 ──────────────────────────────────────
def test_mar_yields_six_series_three_metrics():
    records = _parse("MAR")
    assert len(records) == 18
    assert {r["series"] for r in records} == {
        "company_operated_worldwide", "company_operated_us_canada",
        "company_operated_international",
        "systemwide_worldwide", "systemwide_us_canada", "systemwide_international",
    }
    assert {r["date"] for r in records} == {PERIOD_END}


@pytest.mark.parametrize(
    "series,revpar,occupancy,adr",
    [
        ("systemwide_worldwide", "137.91", "72.5", "190.33"),
        ("systemwide_us_canada", "146.29", "73.9", "197.93"),
        ("systemwide_international", "121.74", "69.7", "174.76"),
        ("company_operated_worldwide", "155.19", "70.7", "219.57"),
        ("company_operated_us_canada", "200.53", "72.2", "277.92"),
        ("company_operated_international", "125.34", "69.7", "179.80"),
    ],
)
def test_mar_values_match_the_release(series, revpar, occupancy, adr):
    records = _parse("MAR")
    assert _by(records, "revpar")[series] == revpar
    assert _by(records, "occupancy")[series] == occupancy
    assert _by(records, "adr")[series] == adr


def test_mar_headline_triple_is_reproduced_by_the_table():
    """新闻稿头条写 worldwide +6.5% / US & Canada +7.4% / international +4.4%，
    这三句必须能在表里找到对应的三行水平值 —— 否则就是选错了口径行。"""
    records = _parse("MAR")
    revpar = _by(records, "revpar")
    assert revpar["systemwide_worldwide"] == "137.91"
    assert revpar["systemwide_us_canada"] == "146.29"
    assert revpar["systemwide_international"] == "121.74"
    # 三行必须互不相同且量级递增关系与「全球介于两者之间」一致
    assert revpar["systemwide_international"] < revpar["systemwide_worldwide"] \
        < revpar["systemwide_us_canada"]


def test_mar_nine_month_block_is_not_written_as_a_quarter():
    """9M 块同表同名列（如 systemwide US&Canada All = 141.86）若被当成当季写入，
    季度序列就会出现两个量级。这里锁死「只写 3M」。"""
    records = _parse("MAR")
    values = {r["value"] for r in records if r["metric"] == "revpar"}
    assert "141.86" not in values          # 9M Systemwide US & Canada - All
    assert "204.30" not in values          # 9M Company-Operated US & Canada - All
    assert "124.22" not in values          # 9M Company-Operated International - All


def test_mar_tampered_row_is_dropped_not_written():
    """把一个值改坏让恒等式不成立：该行必须整行丢弃并留证，兄弟行不受牵连。"""
    text = _html("sec_mar_2026q3.htm.gz").replace("146.29", "196.29", 1)
    notes: list[str] = []
    records = mod.parse_mar(text, PERIOD_END, notes)
    revpar = _by(records, "revpar")
    assert "systemwide_us_canada" not in revpar
    assert revpar["systemwide_worldwide"] == "137.91"     # 同表其他行照常
    assert [n for n in notes if "列归属不可信" in n]


# ── HLT：列序是 出租率·ADR·RevPAR，且亚太行恒等式双解 ────────────────────
def test_hlt_yields_six_region_series():
    records = _parse("HLT")
    assert len(records) == 18
    assert {r["series"] for r in records} == {
        "systemwide_worldwide", "systemwide_us", "systemwide_americas_ex_us",
        "systemwide_europe", "systemwide_mea", "systemwide_apac",
    }


def test_hlt_column_order_is_resolved_not_assumed():
    """HLT 列序是 出租率·ADR·RevPAR（与 MAR 相反）。若按 MAR 的列序读，
    出租率会变成 124.70。"""
    records = _parse("HLT")
    assert _by(records, "revpar")["systemwide_worldwide"] == "124.70"
    assert _by(records, "occupancy")["systemwide_worldwide"] == "75.8"
    assert _by(records, "adr")["systemwide_worldwide"] == "164.57"


def test_hlt_apac_row_resolved_by_header_tiebreak():
    """亚太行 出租率 71.9 与 RevPAR 71.78 几乎同值，恒等式同时接受两种归属，
    必须由表头列序裁决 —— 这一行是本通道唯一真的会撞车的行。"""
    notes: list[str] = []
    records = mod.parse_hlt(_html("sec_hlt_2026q3.htm.gz"), PERIOD_END, notes)
    assert _by(records, "revpar")["systemwide_apac"] == "71.78"
    assert _by(records, "occupancy")["systemwide_apac"] == "71.9"
    assert _by(records, "adr")["systemwide_apac"] == "99.87"
    assert not [n for n in notes if "列归属" in n]


def test_hlt_segment_rows_are_not_part_of_the_region_series():
    """Management and franchise / Ownership 是 Segment 口径，不落 region 序列。"""
    records = _parse("HLT")
    assert not [r for r in records if r["value"] == "123.97"]   # M&F RevPAR
    assert all(r["series"].startswith("systemwide_") for r in records)


# ── WH：逐季水平值，当年三个季度一次给全；n/a 不补 ────────────────────────
def test_wh_yields_three_quarters_of_levels():
    records = _parse("WH")
    assert len(records) == 9
    assert {r["date"] for r in records} == {"2026-03-31", "2026-06-30", "2026-09-30"}
    assert all(r["metric"] == "revpar" for r in records)


def test_wh_values_match_the_historical_table():
    records = _parse("WH")
    revpar = {(r["series"], r["date"]): r["value"] for r in records}
    assert revpar[("systemwide_worldwide", "2026-09-30")] == "51.13"
    assert revpar[("systemwide_us", "2026-09-30")] == "58.35"
    assert revpar[("systemwide_international", "2026-09-30")] == "41.79"
    assert revpar[("systemwide_worldwide", "2026-03-31")] == "38.53"


def test_wh_prior_year_rows_and_q4_are_not_written():
    """表里同时给 2025 全年与 2026 Q4 的 n/a；上年只作对照，空白就是未披露。"""
    records = _parse("WH")
    assert all(r["date"].startswith("2026") for r in records)
    assert not [r for r in records if r["date"] == "2026-12-31"]
    # 2025 Q3 global 50.05 不得顶替 2026 Q3 的 51.13
    assert "50.05" not in {r["value"] for r in records}


def test_wh_caliber_says_the_identity_check_does_not_apply():
    records = _parse("WH")
    assert all("无法用恒等式自校" in r["caliber"] for r in records)


# ── H：constant-$ 表；reported-$ 的 all-inclusive 表不注册 ─────────────────
def test_h_yields_eight_series_including_owned_leased():
    records = _parse("H")
    assert len(records) == 24
    assert {r["series"] for r in records} == {
        "systemwide_worldwide", "systemwide_us", "systemwide_americas_ex_us",
        "systemwide_greater_china", "systemwide_apac", "systemwide_europe",
        "systemwide_mea", "owned_leased_worldwide",
    }


def test_h_only_the_constant_dollar_block_is_read():
    """同一 exhibit 里 reported-$ 的 Net Package 表 VerPAR 187.19/226.95；
    混读会把 all-inclusive 口径写进 system-wide 序列。"""
    records = _parse("H")
    values = {r["value"] for r in records if r["metric"] == "revpar"}
    assert "187.19" not in values            # all-inclusive system-wide, reported $
    assert "226.95" not in values            # all-inclusive Europe, reported $
    assert _by(records, "revpar")["systemwide_worldwide"] == "154.80"


def test_h_nine_month_block_is_not_written():
    records = _parse("H")
    values = {r["value"] for r in records if r["metric"] == "revpar"}
    assert "151.90" not in values            # 9M System-wide hotels
    assert "220.46" not in values            # 9M Owned and leased


# ── CHH：currency-neutral，出租率同比以 bps 计 ─────────────────────────────
def test_chh_yields_three_series():
    records = _parse("CHH")
    assert len(records) == 9
    assert {r["series"] for r in records} == {
        "systemwide_worldwide", "systemwide_us", "systemwide_international",
    }
    assert _by(records, "revpar")["systemwide_worldwide"] == "67.41"


def test_chh_royalty_rate_table_is_not_mistaken_for_operating_rows():
    """同一张表下半部分有 U.S. Average Royalty Rate（行名同为 Total U.S.，
    数字 4 个）。它必须被跳过，不能写成 RevPAR。"""
    notes: list[str] = []
    records = mod.parse_chh(_html("sec_chh_2026q3.htm.gz"), PERIOD_END, notes)
    values = {r["value"] for r in records}
    assert "5.23" not in values and "5.15" not in values
    assert _by(records, "occupancy")["systemwide_us"] == "61.9"


def test_chh_footnoted_labels_are_recognised():
    assert mod._label_key("Total System (5)") == "total system"
    assert mod._label_key("International (5)") == "international"
    assert mod._label_key("Total U.S.") == "total u.s."
    # 真括号内容不是脚注，不能被削掉
    assert mod._label_key("Americas (excluding U.S.)") == "americas (excluding u.s.)"


# ── 数字与恒等式工具 ──────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1,234.5", "1234.5"),
        ("$ 225.97", "225.97"),
        ("10.7 %", "10.7"),
        ("3.6 % pts.", "3.6"),
        ("160 bps", "160"),
        ("(5.0)", "-5.0"),
        ("(10)", "-10"),
        ("—", None),
        ("n/a", None),
        ("", None),
        ("JW Marriott", None),
    ],
)
def test_number_parsing(raw, expected):
    assert mod._num(raw) == expected


def test_identity_alone_decides_when_unambiguous():
    assignment, why = mod._assign_metrics([("225.97", "10.7"), ("71.0", "3.6"), ("318.24", "5.1")])
    assert why == "identity"
    assert assignment["revpar"][0] == "225.97"
    assert assignment["occupancy"][0] == "71.0"
    assert assignment["adr"][0] == "318.24"


def test_identity_conflict_is_dropped():
    assignment, why = mod._assign_metrics([("225.97", "10.7"), ("99.0", "3.6"), ("318.24", "5.1")])
    assert assignment is None
    assert "恒等式不符" in why


def test_ambiguous_identity_needs_a_header_and_still_refuses_otherwise():
    # 71.9 / 71.78 几乎同值 → 恒等式两种解都成立
    pairs = [("71.9", "-0.7"), ("99.87", "0.5"), ("71.78", "-0.5")]
    assignment, why = mod._assign_metrics(pairs)
    assert assignment is None and "歧义" in why
    assignment, why = mod._assign_metrics(
        pairs, ["occupancy", "adr", "revpar"]
    )
    assert why == "identity+header"
    assert assignment["revpar"][0] == "71.78"
    assert assignment["occupancy"][0] == "71.9"


# ── exhibit 选件（spider 侧，拿真 8-K 正文跑）──────────────────────────────
@pytest.mark.parametrize(
    "ticker,expected",
    [
        ("mar", "mar-2026q3xex99earningsrel.htm"),
        ("hlt", "q32026earningsrelease.htm"),
        ("wh", "exhibit9912026930.htm"),
        ("h", "q32026earningsrelease.htm"),
        ("chh", "exhibit991earningspressrel.htm"),
    ],
)
def test_exhibit_is_located_in_the_real_8k_body(ticker, expected):
    body = (FIXTURES / f"sec_{ticker}_8k_primary.htm").read_text(
        encoding="utf-8", errors="replace"
    )
    candidates, evidence = lane.pick_exhibit(lane.exhibits(body))
    assert candidates is not None, evidence
    assert candidates["name"] == expected


def test_hyatt_supplemental_slide_deck_is_excluded():
    """H 的 8-K 同时带了 7.01 的演示稿；选件必须排除它，只留业绩新闻稿。"""
    body = (FIXTURES / "sec_h_8k_primary.htm").read_text(encoding="utf-8", errors="replace")
    names = {c["name"] for c in lane.exhibits(body)}
    assert "finalq32026supplementald.htm" in names        # 候选里确实有两份
    picked, _ = lane.pick_exhibit(lane.exhibits(body))
    assert picked["name"] == "q32026earningsrelease.htm"


def test_no_link_body_is_reported_as_no_exhibit_link():
    picked, why = lane.pick_exhibit(lane.exhibits("<html><body><p>No exhibits.</p></body></html>"))
    assert picked is None and why == "no-candidates"


def test_ambiguous_candidates_are_refused():
    body = (
        '<html><body><table><tr><td>Exhibit 99.1</td>'
        '<td><a href="a-earnings.htm">Press release</a></td></tr>'
        '<tr><td>Exhibit 99.2</td>'
        '<td><a href="b-earnings.htm">Press release</a></td></tr></table></body></html>'
    )
    picked, why = lane.pick_exhibit(lane.exhibits(body))
    assert picked is None and why.startswith("ambiguous")


def test_absolute_urls_and_schema_files_are_not_candidates():
    body = (
        '<html><body><a href="doc.xsd">schema</a>'
        '<a href="https://example.com/x.htm">press release</a>'
        '<a href="real.htm">Exhibit 99.1 Press release</a></body></html>'
    )
    names = [c["name"] for c in lane.exhibits(body)]
    assert names == ["real.htm"]


# ── 车队侧：宇宙与申报筛选 ────────────────────────────────────────────────
def test_default_universe_comes_from_the_hotel_truth_source():
    assert lane._default_tickers() == ("MAR", "HLT", "WH", "H", "CHH")


def test_default_universe_fails_loudly_when_the_registry_lacks_the_lane(tmp_path, monkeypatch):
    empty = tmp_path / "hotel-universe.yaml"
    empty.write_text("companies: []\n", encoding="utf-8")
    monkeypatch.setenv(lane.HOTEL_UNIVERSE_ENV, str(empty))
    lane._default_tickers.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="没有 region=US"):
            lane._default_tickers()
    finally:
        lane._default_tickers.cache_clear()


def test_earnings_filings_filter_keeps_only_item_202():
    submissions = {"filings": {"recent": {
        "form": ["8-K", "8-K", "10-Q", "8-K"],
        "items": ["2.02,9.01", "5.02", "", "7.01,9.01"],
        "accessionNumber": ["a-1", "a-2", "a-3", "a-4"],
        "filingDate": ["2026-10-29", "2026-10-01", "2026-09-30", "2026-08-01"],
        "reportDate": ["2026-09-30", "2026-09-01", "2026-09-30", "2026-07-30"],
        "primaryDocument": ["p1.htm", "p2.htm", "p3.htm", "p4.htm"],
    }}}
    picked = lane._earnings_filings(submissions, "MAR", 4)
    assert [f["accession"] for f in picked] == ["a-1"]
    assert picked[0]["event_date"] == "2026-09-30"   # 事件面字段，非财季末


def test_earnings_filings_respects_the_lookback_window():
    submissions = {"filings": {"recent": {
        "form": ["8-K"] * 3,
        "items": ["2.02"] * 3,
        "accessionNumber": ["a-1", "a-2", "a-3"],
        "filingDate": ["2026-10-29", "2026-07-29", "2026-04-29"],
        "reportDate": ["2026-09-30", "2026-06-30", "2026-03-31"],
        "primaryDocument": ["p1.htm", "p2.htm", "p3.htm"],
    }}}
    assert len(lane._earnings_filings(submissions, "MAR", 2)) == 2


def test_ragged_submission_arrays_are_a_hard_failure():
    submissions = {"filings": {"recent": {
        "form": ["8-K"], "items": ["2.02"], "accessionNumber": ["a-1"],
        "filingDate": ["2026-10-29"], "reportDate": ["2026-09-30"],
        "primaryDocument": [],
    }}}
    with pytest.raises(RuntimeError, match="长度不齐"):
        lane._earnings_filings(submissions, "MAR", 4)


def test_pick_method_is_recorded_in_the_row_contract():
    assert lane.pick_exhibit([])[1] == "no-candidates"
    body = in_release = (
        '<html><body><table><tr><td>Exhibit 99.1</td>'
        '<td><a href="x.htm">Press release dated October 29, 2026</a></td>'
        "</tr></table></body></html>"
    )
    picked, why = lane.pick_exhibit(lane.exhibits(in_release))
    assert picked["name"] == "x.htm" and why == "explicit-exhibit-99.1"


# ── 可得性矩阵与概念表 ────────────────────────────────────────────────────
def test_availability_covers_all_fourteen_us_hotels():
    codes = {r["entity_code"] for r in mod.AVAILABILITY}
    assert codes == {
        "MAR", "HLT", "WH", "H", "CHH", "IHG", "HTHT", "ATAT", "GHG", "TCOM",
        "HGV", "BKNG", "EXPE", "ABNB",
    }
    assert len(mod.AVAILABILITY) == 14


def test_availability_states_are_the_measured_ones():
    by = {r["entity_code"]: r for r in mod.AVAILABILITY}
    for code in ("MAR", "HLT", "WH", "H", "CHH"):
        assert by[code]["status"] == "parsed"
    for code in ("IHG", "HTHT", "ATAT", "GHG", "TCOM"):
        assert by[code]["status"] == "not-an-8k-filer"
        assert "6-K" in by[code]["carrier"]
    assert by["HGV"]["status"] == "no-revpar-disclosure"
    assert "RevPAR 命中 0" in by["HGV"]["reason"]
    for code in ("BKNG", "EXPE", "ABNB"):
        assert by[code]["status"] == "ota-no-hotel-kpi"


def test_availability_never_silently_empty():
    for record in mod.AVAILABILITY:
        assert record["reason"] and record["evidence"] and record["recovery"]
    unknown = mod.availability("MCD")
    assert unknown["status"] == "not-in-us-hotel-universe"
    assert unknown["reason"]


def test_htht_falsification_is_recorded():
    """任务书把 HTHT 列为 8-K 抽取对象；实测它只报 6-K。证伪必须写在矩阵里。"""
    record = mod.availability("HTHT")
    assert record["status"] == "not-an-8k-filer"
    assert "实测证伪" in record["reason"]


def test_concepts_cover_every_registered_series():
    assert len(mod.SERIES) == 13
    assert len(mod.CONCEPTS) == 3 * 13
    codes = [c[0] for c in mod.CONCEPTS]
    assert len(set(codes)) == len(codes)
    for concept in mod.CONCEPTS:
        assert len(concept) == 9
        assert concept[0].startswith("hotel.us.")
        assert concept[0].endswith(".quarterly")
        assert concept[6] == "quarter" and concept[7] == "stock"
        assert concept[8] == mod.SOURCE


def test_units_follow_the_metric():
    assert mod.METRICS["revpar"][2] == "USD"
    assert mod.METRICS["adr"][2] == "USD"
    assert mod.METRICS["occupancy"][2] == "%"


def test_caliber_is_carried_on_every_record_and_differs_per_issuer():
    calibers = {}
    for ticker in ("MAR", "HLT", "WH", "H", "CHH"):
        records = _parse(ticker)
        assert records, ticker
        assert all(r["caliber"] for r in records)
        assert all(r["caliber"].startswith(mod.CALIBER[ticker]) or
                   r["caliber"].startswith(mod.CALIBER[ticker].split("；")[0])
                   for r in records)
        calibers[ticker] = records[0]["caliber"]
    assert len(set(calibers.values())) == 5      # 五家五套口径，不许混


# ── 财季末取自公告自身，不取 submissions.reportDate ────────────────────────
@pytest.mark.parametrize("ticker", ["mar", "hlt", "wh", "h", "chh"])
def test_period_end_comes_from_the_exhibit_header(ticker):
    """实测坑：MAR 2026Q3 的 8-K 其 submissions.reportDate = 2026-11-02（申报日），
    不是财季末。期间必须从公告自身取。"""
    text = _html(f"sec_{ticker}_2026q3.htm.gz")
    assert mod.period_end_from_html(text) == "2026-09-30"


def test_period_end_from_the_8k_body_for_every_issuer():
    for ticker in ("mar", "hlt", "wh", "h", "chh"):
        body = (FIXTURES / f"sec_{ticker}_8k_primary.htm").read_text(
            encoding="utf-8", errors="replace"
        )
        assert lane.period_end_from_text(body) == "2026-09-30", ticker


def test_period_end_is_none_when_the_document_does_not_state_it():
    assert mod.period_end_from_html("<html><body><p>no period here</p></body></html>") is None
    assert lane.period_end_from_text("no dates at all") is None


# ── 累计期块绝不冒充当季（2026-11-08 实测回归）─────────────────────────────
# 五家的 FY（Q4）业绩稿都把「Three Months Ended」与「Year Ended / Twelve Months
# Ended」两套列放进同一张表，季度块之后紧跟着行名一模一样的累计块：
#   H  2025Q2 稿：Three Months Ended June 30, → 数据行 → Six Months Ended June 30,
#   HLT 2025Q4 稿：Three Months Ended Year Ended（同一表头行里两个期间）
# 旧实现只认「three months ended」，累计块按当季写入，同一 (序列, 日期) 出现
# 两个值，后写者覆盖前写者 —— HLT FY RevPAR 114.39 就那么顶掉了 Q4 的 110.89。
# 下面每份 fixture 都是上游真件，逐家钉住「季度值在、累计值不在」。
_CUMULATIVE_CASES = [
    # (ticker, fixture, period_end, 保留的季度值, 必须缺席的累计值, series, metric)
    ("mar", "sec_mar_2025q4.htm.gz", "2025-12-31",
     "182.43", "185.78", "company_operated_us_canada", "revpar"),
    ("hlt", "sec_hlt_2025q4.htm.gz", "2025-12-31",
     "110.89", "114.39", "systemwide_worldwide", "revpar"),
    ("h", "sec_h_2025q4.htm.gz", "2025-12-31",
     "146.01", "144.63", "systemwide_worldwide", "revpar"),
    ("h", "sec_h_2025q2.htm.gz", "2025-06-30",
     "158.70", "150.96", "systemwide_worldwide", "revpar"),
    ("chh", "sec_chh_2026q2.htm.gz", "2026-06-30",
     "58.92", "52.08", "systemwide_us", "revpar"),
]


@pytest.mark.parametrize(
    "ticker,fixture,period_end,kept,cumulative,series,metric", _CUMULATIVE_CASES
)
def test_cumulative_block_never_written_as_the_quarter(
    ticker, fixture, period_end, kept, cumulative, series, metric
):
    notes: list[str] = []
    records = mod.PARSERS[ticker.upper()](_html(fixture), period_end, notes)
    values = [r["value"] for r in records
              if (r["series"], r["metric"], r["date"]) == (series, metric, period_end)]
    assert values == [kept]
    assert cumulative not in {r["value"] for r in records}
    # 累计块被识别掉了，不该再留下「同期两个值」的冲突痕迹
    assert not any("文档内冲突" in n for n in notes)


@pytest.mark.parametrize("ticker,fixture,period_end", [
    ("MAR", "sec_mar_2025q4.htm.gz", "2025-12-31"),
    ("HLT", "sec_hlt_2025q4.htm.gz", "2025-12-31"),
    ("H", "sec_h_2025q4.htm.gz", "2025-12-31"),
    ("H", "sec_h_2025q2.htm.gz", "2025-06-30"),
    ("CHH", "sec_chh_2026q2.htm.gz", "2026-06-30"),
])
def test_no_duplicate_key_within_one_document(ticker, fixture, period_end):
    """同一份 exhibit 内 (序列, 指标, 期间) 必须唯一 —— 冲突意味着两个期间块
    被当成了同一期，这是 4.3 最危险的一类错（会静默改写季度值）。"""
    records = mod.PARSERS[ticker](_html(fixture), period_end, [])
    keys = [(r["series"], r["metric"], r["date"]) for r in records]
    assert len(keys) == len(set(keys)), [k for k in keys if keys.count(k) > 1]


# ── 温德姆同稿两套「报告基准」：只收本期基准 ───────────────────────────────
def test_wh_takes_current_reporting_basis_not_the_pre_q2_2025_control():
    """FY2025 稿同时有「REPORTING BASIS AS OF Q4 2025」与「PRE Q2 2025 REPORTING
    BASIS」两张同结构 historical 表：Global 2025 Q1 一个 38.44、一个 36.13，
    旧基准表 Q2–Q4 全是 n/a。只收最完整的一张，旧基准整块不写。"""
    notes: list[str] = []
    records = mod.parse_wh(_html("sec_wh_2025q4.htm.gz"), "2025-12-31", notes)
    assert {(r["series"], r["date"]): r["value"] for r in records} == {
        ("systemwide_worldwide", "2025-03-31"): "38.44",
        ("systemwide_worldwide", "2025-06-30"): "47.55",
        ("systemwide_worldwide", "2025-09-30"): "50.05",
        ("systemwide_worldwide", "2025-12-31"): "40.36",
        ("systemwide_us", "2025-03-31"): "42.37",
        ("systemwide_us", "2025-06-30"): "53.32",
        ("systemwide_us", "2025-09-30"): "55.07",
        ("systemwide_us", "2025-12-31"): "42.91",
        ("systemwide_international", "2025-03-31"): "32.81",
        ("systemwide_international", "2025-06-30"): "39.45",
        ("systemwide_international", "2025-09-30"): "43.11",
        ("systemwide_international", "2025-12-31"): "36.96",
    }
    assert "36.13" not in {r["value"] for r in records}
    assert any("historical 表" in n and "最完整" in n for n in notes)


# ── 同比位是破折号的行不是「形状不符」──────────────────────────────────────
def test_row_with_dash_in_change_slot_still_parses():
    """实测 H 自有酒店：``[..., '246.55', '7.2', '%', '71.1', '%', '—', '% pts',
    '$', '346.84', '7.2', '%']`` —— 出租率同比是「—」，量值三个都在。
    按数字个数数会得 5 个数字并整行丢弃，把三个真披露的指标一起丢掉。"""
    cells = ["Owned and leased hotels (b)", "$", "246.55", "7.2", "%",
             "71.1", "%", "—", "% pts", "$", "346.84", "7.2", "%"]
    label, pairs = mod._slot_pairs(cells)
    assert label == "Owned and leased hotels (b)"
    assert pairs == [("246.55", "7.2"), ("71.1", None), ("346.84", "7.2")]

    records = mod.parse_h(_html("sec_h_2025q4.htm.gz"), "2025-12-31", [])
    owned = {(r["metric"], r["date"]): r["value"]
             for r in records if r["series"] == "owned_leased_worldwide"}
    assert len(owned) == 3
    assert owned[("revpar", "2025-12-31")] == "246.55"
    assert owned[("occupancy", "2025-12-31")] == "71.1"
    assert owned[("adr", "2025-12-31")] == "346.84"


def test_row_missing_a_value_is_still_rejected():
    """缺口容错只容同比位：量值缺失说明这行根本不是营运三列，必须丢。"""
    cells = ["Total system", "$", "—", "1.0", "%", "60.0", "%", "0.5", "% pts",
             "$", "98.28", "1.0", "%"]
    label, pairs = mod._slot_pairs(cells)
    assert label == "Total system"
    assert pairs is None


def test_slot_pairs_rejects_rows_from_other_tables():
    """规模表 / 费率表的同名列（4 个数字）不能被当成营运三列。"""
    label, pairs = mod._slot_pairs(["Owned and leased", "423", "264", "1,375", "1,174"])
    assert label == "Owned and leased"
    assert pairs is None
