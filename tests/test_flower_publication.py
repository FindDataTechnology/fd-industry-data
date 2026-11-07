"""发布节奏级 SLA 的口径契约（add-flower-vertical 6.2）。

钉四条**会把两类事件混成一件**的默认值：

1. **冷启动不猜**：带水位的成功样本不足时返回 ``window_learning``，绝不出「未发布」
   告警——否则上线第一天就在编造 SLA。
2. **「我方断流」优先于「上游未发布」**：期望时点已过时，先看水位之后有没有失败运行；
   有就是 ``stream_broken``。反过来判会把「出口挂了」说成「上游没发」。
3. **跑了但没新一期 = 上游未发布**：最近成功运行在水位之后且无失败 → 抓取侧正常，
   等下一轮即可，不该出单。
4. **滞后用 p95 不用均值**：上游偶发延迟不该把整条源的期望窗拉长（一次 10 天延迟
   就能让均值永久偏大，之后真断流也判不出来）。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fd_industry_data import publication as pub  # noqa: E402

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
DAILY = pub.Cadence(period_days=1.0, min_samples=5, grace_hours=6.0)


def _run(day: str, status: str = "success", wm: str | None = None, hour: int = 0) -> pub.Run:
    return pub.Run(started_at=f"{day}T{hour:02d}:15:00Z", status=status, rows=10, watermark=wm)


def _daily_ok(days: int, start_day: int = 1, lag: int = 2, month: int = 10) -> list[pub.Run]:
    """造 days 天连续成功：运行日 D 拿到数据日 D-lag。"""
    from datetime import date, timedelta
    out = []
    for i in range(days):
        d = date(2026, month, start_day) + timedelta(days=i)
        wm = d - timedelta(days=lag)
        out.append(_run(d.isoformat(), "success", wm.isoformat()))
    return out


def test_cold_start_is_explicitly_window_learning():
    runs = _daily_ok(3)          # < min_samples=5
    state = pub.classify(runs, DAILY, NOW)
    assert state.state == "window_learning"
    assert "窗口学习中" in state.detail
    assert state.samples == 3


def test_no_runs_at_all_is_unknown_not_a_false_alarm():
    state = pub.classify([], DAILY, NOW)
    assert state.state == "unknown"
    assert not state.needs_attention


def test_never_succeeded_is_stream_broken():
    runs = [_run("2026-11-06", "failed"), _run("2026-11-07", "failed")]
    state = pub.classify(runs, DAILY, NOW)
    assert state.state == "stream_broken"
    assert state.needs_attention


def test_learned_window_covers_a_normal_gap():
    """正常节奏（昨天运行只拿到前两天的数据）不该报任何异常。"""
    runs = _daily_ok(8, start_day=1, lag=2, month=11)   # 11-01..11-08，水位到 11-06
    state = pub.classify(runs, DAILY, NOW)              # NOW = 11-08 12:00Z
    assert state.state == "published", state.detail
    assert state.learned_lag_days == 2.0


def test_failed_runs_after_watermark_mean_our_stream_broke():
    """我们自己在水位之后跑挂了 → 断流（不是「上游没发」）。"""
    runs = _daily_ok(8, start_day=1, lag=2, month=11)    # 水位 11-06
    runs.append(_run("2026-11-09", "failed"))
    runs.append(_run("2026-11-10", "failed"))
    state = pub.classify(runs, DAILY, datetime(2026, 11, 11, tzinfo=timezone.utc))
    assert state.state == "stream_broken"
    assert "断在抓取侧" in state.detail


def test_successful_runs_without_new_period_mean_upstream_did_not_publish():
    """我在跑、也成功，但上游连着几天没出新一期 → 源未发布，不是断流。"""
    runs = _daily_ok(8, start_day=1, lag=2, month=11)   # 11-01..11-08，水位 11-06
    # 11-09 / 11-10 都跑了且成功，但水位仍是 11-06（上游没发新版：按滞后 2 天，
    # 11-09 那次本应看到 11-07、11-10 本应看到 11-08）
    runs.append(_run("2026-11-09", "success", "2026-11-06"))
    runs.append(_run("2026-11-10", "success", "2026-11-06"))
    state = pub.classify(runs, DAILY, datetime(2026, 11, 11, tzinfo=timezone.utc))
    assert state.state == "awaiting_publication"
    assert not state.needs_attention, "源未发布不该升级成故障"
    assert "上游未发布" in state.detail


def test_two_events_are_distinguishable_in_one_drill():
    """同一个时间点、同一水位，只差「水位之后有没有成功跑到」→ 两种结论。"""
    now = datetime(2026, 11, 11, tzinfo=timezone.utc)
    base = _daily_ok(8, start_day=1, lag=2, month=11)
    quiet = base + [_run("2026-11-09", "success", "2026-11-06"),
                    _run("2026-11-10", "success", "2026-11-06")]
    broken = base + [_run("2026-11-09", "failed"), _run("2026-11-10", "failed")]
    a = pub.classify(quiet, DAILY, now)
    b = pub.classify(broken, DAILY, now)
    assert a.state == "awaiting_publication" and b.state == "stream_broken"
    assert a.watermark == b.watermark, "水位相同，结论不同——差别只在抓取侧是否失败"


def test_not_running_at_all_is_stream_broken():
    """水位之后完全没跑（调度侧停了）→ 断流，不能算「上游没发」。"""
    runs = _daily_ok(8, start_day=1, lag=2, month=11)
    state = pub.classify(runs, DAILY, datetime(2026, 11, 20, tzinfo=timezone.utc))
    assert state.state == "stream_broken"
    assert "调度侧" in state.detail


def test_lag_uses_p95_so_one_slow_publication_does_not_widen_the_window():
    lags_runs = [_run("2026-10-01", "success", "2026-09-29"),   # lag 2
                 _run("2026-10-02", "success", "2026-09-30"),   # lag 2
                 _run("2026-10-03", "success", "2026-10-01"),   # lag 2
                 _run("2026-10-04", "success", "2026-10-02"),   # lag 2
                 _run("2026-10-05", "success", "2026-10-03"),   # lag 2
                 _run("2026-10-06", "success", "2026-09-26")]   # lag 10（异常）
    lag, samples = pub.learn_lag_days(lags_runs, DAILY)
    assert samples == 6
    assert lag is not None and lag < 4.0, f"p95 被单次异常拉长到 {lag}"
