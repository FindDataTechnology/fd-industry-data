"""Cadence-aware freshness tests (harden-crawl-delivery D2 / task 1.7)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fd_industry_data.health.freshness import (
    ALERT_AFTER_CYCLES,
    Schedule,
    cycles_since_last_good,
)

CST = timezone(timedelta(hours=8))
UTC = timezone.utc


def _cst(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=CST)


def test_good_run_in_the_current_cycle_is_zero():
    # daily 04:00 CST; last good today 04:00; now 10:00 -> nothing missed
    got = cycles_since_last_good(
        schedule="0 4 * * *", tz="Asia/Shanghai",
        last_good_at=_cst(2026, 11, 9, 4), now=_cst(2026, 11, 9, 10))
    assert got.cycles == 0
    assert not got.alerting


def test_one_missed_cycle_only_leaves_a_trace():
    got = cycles_since_last_good(
        schedule="0 4 * * *", tz="Asia/Shanghai",
        last_good_at=_cst(2026, 11, 8, 4), now=_cst(2026, 11, 9, 10))
    assert got.cycles == 1
    assert not got.alerting  # D2: first miss is trace-only


def test_two_missed_cycles_alert():
    got = cycles_since_last_good(
        schedule="0 4 * * *", tz="Asia/Shanghai",
        last_good_at=_cst(2026, 11, 7, 4), now=_cst(2026, 11, 9, 10))
    assert got.cycles == 2
    assert got.alerting
    assert ALERT_AFTER_CYCLES == 2


def test_monthly_source_is_not_false_alarmed_by_hours():
    # 1st of month 06:00; last good 2026-10-01; now 2026-11-09 -> exactly 1 fire
    got = cycles_since_last_good(
        schedule="0 6 1 * *", tz="Asia/Shanghai",
        last_good_at=_cst(2026, 10, 1, 6), now=_cst(2026, 11, 9, 12))
    assert got.cycles == 1
    assert not got.alerting


def test_first_fire_not_yet_reached_scores_zero():
    # created 2026-11-09 12:11 CST, schedule 11:20 weekdays -> next fire tomorrow
    got = cycles_since_last_good(
        schedule="20 11 * * 1-5", tz="Asia/Shanghai",
        last_good_at=None, created_at=_cst(2026, 11, 9, 12, 11),
        now=_cst(2026, 11, 9, 15, 0))
    assert got.cycles == 0
    assert not got.alerting


def test_never_good_counts_from_creation():
    # created Monday 04:11, now Wednesday 15:00 -> 11:20 Mon, Tue, Wed = 3 fires
    got = cycles_since_last_good(
        schedule="20 11 * * 1-5", tz="Asia/Shanghai",
        last_good_at=None, created_at=_cst(2026, 11, 9, 4, 11),
        now=_cst(2026, 11, 11, 15, 0))
    assert got.cycles == 3
    assert got.alerting


def test_utc_source_uses_its_own_timezone():
    # 23:00 UTC daily; last good 2026-11-07 23:00Z; now 2026-11-09 23:30Z -> 2
    got = cycles_since_last_good(
        schedule="0 23 * * *", tz="UTC",
        last_good_at=datetime(2026, 11, 7, 23, tzinfo=UTC),
        now=datetime(2026, 11, 9, 23, 30, tzinfo=UTC))
    assert got.cycles == 2
    # the same instant judged in Shanghai would be 07:00 local, i.e. no fire
    got_cst = cycles_since_last_good(
        schedule="0 23 * * *", tz="Asia/Shanghai",
        last_good_at=datetime(2026, 11, 7, 23, tzinfo=UTC),
        now=datetime(2026, 11, 9, 23, 30, tzinfo=UTC))
    assert got_cst.cycles == 2  # 23:00 CST on the 8th and 9th


def test_hourly_source_is_not_alarmed_within_a_day():
    """每小时跑的源欠两小时不该判陈旧：2 个周期 = 2h 是空转。

    实测 eia-v2 / nmc-weather 在漏斗里就是这样落进 stale 的。
    """
    from fd_industry_data.health.freshness import MIN_ALERT_WINDOW_HOURS
    got = cycles_since_last_good(
        schedule="0 * * * *", tz="UTC",
        last_good_at=datetime(2026, 11, 9, 10, tzinfo=UTC),
        now=datetime(2026, 11, 9, 13, tzinfo=UTC))  # 3 次触发，仅 3h
    assert MIN_ALERT_WINDOW_HOURS == 24.0
    assert got.cycles == 1
    assert not got.alerting
    assert "trace-only" in (got.note or "")


def test_hourly_source_is_alarmed_after_a_full_day():
    got = cycles_since_last_good(
        schedule="0 * * * *", tz="UTC",
        last_good_at=datetime(2026, 11, 7, 10, tzinfo=UTC),
        now=datetime(2026, 11, 9, 13, tzinfo=UTC))
    assert got.cycles >= 2
    assert got.alerting


def test_lookback_caps_the_walk_and_says_so():
    got = cycles_since_last_good(
        schedule="0 4 * * *", tz="UTC",
        last_good_at=datetime(2020, 1, 1, tzinfo=UTC),
        now=datetime(2026, 11, 9, 12, tzinfo=UTC),
        lookback_days=7)
    assert got.cycles == 7
    assert got.capped


def test_unknown_timezone_degrades_with_a_note():
    got = cycles_since_last_good(
        schedule="0 4 * * *", tz="Mars/Olympus",
        last_good_at=_cst(2026, 11, 8, 4), now=_cst(2026, 11, 9, 12))
    assert got.note and "unknown schedule_tz" in got.note
    assert got.cycles >= 1


def test_unparseable_schedule_is_zero_not_a_crash():
    got = cycles_since_last_good(
        schedule="not a cron", tz="UTC", last_good_at=None,
        now=datetime(2026, 11, 9, tzinfo=UTC))
    assert got.cycles == 0
    assert got.note


def test_vixie_dom_dow_or_rule():
    sched = Schedule.parse("0 4 1 * 1")
    assert sched is not None
    # 2026-12-01 is a Tuesday: dom matches
    assert sched.matches(datetime(2026, 12, 1, 4, 0, tzinfo=UTC))
    # 2026-11-09 is a Monday: dow matches even though dom is 1
    assert sched.matches(datetime(2026, 11, 9, 4, 0, tzinfo=UTC))
    # 2026-11-10 is a Tuesday, neither
    assert not sched.matches(datetime(2026, 11, 10, 4, 0, tzinfo=UTC))
