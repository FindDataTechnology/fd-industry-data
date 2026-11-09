"""Watermark tests (harden-crawl-delivery 3.2).

Four fixture states must each be distinguishable — that is the whole point of
the task: 推进 / 停滞 / 不可得 / 未知. Collapsing any two of them is how a
"we cannot tell" becomes a "healthy" in the dashboard.
"""
from __future__ import annotations

from datetime import date

from fd_industry_data.health.watermark import (
    ALERT_AFTER_PERIODS,
    judge,
    max_period,
    normalise,
    periods_behind,
)

TODAY = date(2026, 11, 10)


def test_normalise_shapes_seen_on_the_fleet():
    assert normalise("2026-10").canonical == "2026-10"
    assert normalise("2026-10").granularity == "month"
    assert normalise("2026-11-09").granularity == "day"
    assert normalise("2026").granularity == "year"
    # 实测 nbs-stats 的 10 日区间：取末端（这一行覆盖到哪天）
    assert normalise("2026-10-21~2026-10-31").canonical == "2026-10-31"
    assert normalise("2026-W44").granularity == "week"


def test_normalise_rejects_garbage():
    assert normalise("") is None
    assert normalise(None) is None
    assert normalise("见附录") is None
    assert normalise("2026-13") is None


def test_ordering_across_granularities():
    # 年值代表整年结束，用于比较时排在同年月份之后
    assert normalise("2026").sort_key > normalise("2026-10").sort_key
    assert normalise("2026-11").sort_key > normalise("2026-10-31").sort_key


def test_state_unavailable_when_field_not_declared():
    wm = max_period([{"period": "2026-10"}], None)
    assert wm["state"] == "unavailable"
    assert judge(wm, "month", TODAY)["state"] == "unavailable"


def test_state_unavailable_when_declared_field_absent():
    wm = max_period([{"other": 1}, {"other": 2}], "period")
    assert wm["state"] == "unavailable"


def test_state_unknown_when_values_do_not_parse():
    wm = max_period([{"period": "详见附件"}], "period")
    assert wm["state"] == "unknown"
    assert judge(wm, "month", TODAY)["state"] == "unknown"


def test_state_advance_when_current():
    wm = max_period([{"period": "2026-10"}, {"period": "2026-09"}], "period")
    out = judge(wm, "month", TODAY)
    assert wm["state"] == "ok" and wm["period"].canonical == "2026-10"
    assert out["state"] == "advance"


def test_state_stall_after_two_periods():
    wm = max_period([{"period": "2026-07"}], "period")
    out = judge(wm, "month", TODAY)  # 7 月底到 11 月 10 日 ≈ 3.6 个月
    assert out["state"] == "stall"
    assert out["periods_behind"] >= ALERT_AFTER_PERIODS


def test_irregular_has_no_cadence_so_it_is_unavailable_not_healthy():
    wm = max_period([{"period": "2024-01"}], "period")
    out = judge(wm, "irregular", TODAY)
    assert out["state"] == "unavailable"
    assert out["periods_behind"] is None
    assert periods_behind(wm["period"], "irregular", TODAY) is None


def test_future_period_is_not_negative():
    wm = max_period([{"period": "2026-12-31"}], "period")
    assert judge(wm, "day", TODAY)["periods_behind"] == 0.0


def test_coarser_watermark_cannot_judge_staleness():
    """年值 + 月频 = 永远「落后 0」的假健康。

    实测 amis-market：`year` 字段只有年份，data_period=month；年值代表 12-31，
    在月频下恒在未来，若不守粒度就会一直报 advance。
    """
    wm = max_period([{"year": "2024"}, {"year": "2026"}], "year")
    out = judge(wm, "month", TODAY)
    assert out["state"] == "unavailable"
    assert "coarser" in out["reason"]


def test_one_period_behind_is_only_a_trace():
    """9 月数据在 11 月 = 只欠一期（10 月）→ 未达 2 期阈值，判 advance。"""
    wm = max_period([{"month": "2026-09"}], "month")
    out = judge(wm, "month", TODAY)
    assert out["state"] == "advance"
    assert 1.0 <= out["periods_behind"] < 2.0


def test_same_granularity_still_judges():
    wm = max_period([{"month": "2026-07"}], "month")
    assert judge(wm, "month", TODAY)["state"] == "stall"


def test_finer_watermark_is_fine_under_coarser_cadence():
    """日粒度水位 + 月频周期：可比（日水位比月周期细）。"""
    wm = max_period([{"d": "2026-11-09"}], "d")
    out = judge(wm, "month", TODAY)
    assert out["state"] == "advance"


def test_dotted_field_path_is_not_silently_treated_as_flat():
    """只支持顶层键；路径写错应落到 unavailable（取不到），不猜。"""
    wm = max_period([{"raw": {"period": "2026-10"}}], "raw.period")
    assert wm["state"] == "unavailable"
