"""酒店节奏判定：逾期与抓取失败必须分流（add-hotel-vertical 6.5）。

引擎是 `fd_industry_data.publication.classify`（与花卉线共用，不新建机制）。
这里钉住两条互斥结论：

* **上游逾期**（我们按点跑到了、但上游没发新期）→ `awaiting_publication`，
  不得当断流出工单；
* **我方断流**（水位之后有失败运行，或水位之后根本没再跑）→ `stream_broken`，
  必须出工单。

这两条若混淆，二选一都错：都当故障=每天重出无效单；都当正常=真断流没人管。
用合成 Run 序列验证，不连库。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.publication import Cadence, Run, classify  # noqa: E402

CADENCE = Cadence(period_days=91, min_samples=3, lag_percentile=0.95,
                  lag_trim=0.0, grace_hours=0.0)
# 基准日取在「水位 2026-09-30 + 91 天 + 滞后 5 天」之后，才可能判逾期；
# 若把基准日放在期望点之前，任何场景都会被判 published（那是正确的，不是漏判）。
NOW = datetime(2027, 2, 1, tzinfo=timezone.utc)


def _learned_runs():
    """三次成功运行，滞后稳定 5 天（运行日 − 数据日），足以学出窗。"""
    return [
        Run("2026-04-05T00:00:00+00:00", "success", watermark="2026-03-31"),
        Run("2026-07-05T00:00:00+00:00", "success", watermark="2026-06-30"),
        Run("2026-10-05T00:00:00+00:00", "success", watermark="2026-09-30"),
    ]


def test_upstream_late_is_awaiting_not_stream_broken():
    """上游逾期：我们又跑了一次、成功，但拿到的还是旧水位 → awaiting_publication。"""
    runs = _learned_runs() + [
        Run("2026-11-05T00:00:00+00:00", "success", watermark="2026-09-30"),
    ]
    state = classify(runs, CADENCE, NOW)
    assert state.state == "awaiting_publication", state.detail
    assert state.needs_attention is False          # 不产工单
    assert state.learned_lag_days is not None      # 窗已学出，不是冷启动


def test_our_extraction_failure_is_stream_broken():
    """我方断流：水位之后连续失败运行 → stream_broken，必须出工单。"""
    runs = _learned_runs() + [
        Run("2026-11-03T00:00:00+00:00", "failed"),
        Run("2026-11-06T00:00:00+00:00", "failed"),
    ]
    state = classify(runs, CADENCE, NOW)
    assert state.state == "stream_broken", state.detail
    assert state.needs_attention is True           # 出工单
    assert "断在抓取侧" in state.detail


def test_not_running_at_all_is_also_stream_broken():
    """水位之后根本没再跑（调度侧没在取）→ 也是断流，不是「上游未发布」。"""
    state = classify(_learned_runs(), CADENCE, NOW)
    assert state.state == "stream_broken", state.detail
    assert "调度侧没在跑" in state.detail


def test_cold_start_never_fires_an_alert():
    """样本不足只报 window_learning —— 冷启动期出「未发布」等于编造 SLA。"""
    runs = _learned_runs()[:2]
    state = classify(runs, CADENCE, NOW)
    assert state.state == "window_learning", state.detail
    assert state.needs_attention is False


def test_never_succeeded_is_broken_from_the_first_day():
    runs = [Run("2026-11-01T00:00:00+00:00", "failed")]
    state = classify(runs, CADENCE, NOW)
    assert state.state == "stream_broken", state.detail
    assert state.needs_attention is True
