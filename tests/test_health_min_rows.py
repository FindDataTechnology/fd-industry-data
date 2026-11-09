"""「成功但查出 0 行」必须可见（add-flower-vertical 6.6）。

实证：`flower-auction` 有 **4 次 success / rows_written 合计 0**，而健康级此前
**完全无信号**——零行成功只在错误文本命中结构异常时才被看见。静默通过比报错更危险：
板上会一直是空的，而巡检全绿。

三条纪律：

1. **未声明即不生效**（零回归）：0 行对某些源合法（节假日无新增、幂等 upsert）。
2. **声明后必须先于健康短路**：否则「跑成功、时间新鲜、只是没写出来」仍判健康。
3. **只认 success 运行**：失败运行本来就走网络/结构规则，不该被这条抢走。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from fd_industry_data.health import inspector, triage

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
DAY = 24.0


def _run(hours_ago: float, status: str = "success", rows: int | None = 100,
         limit: int | None = None) -> triage.Run:
    return triage.Run(started_at=(NOW - timedelta(hours=hours_ago)).isoformat(),
                      status=status, rows=rows, limit=limit)


def test_undeclared_source_is_untouched():
    """未声明 min_rows_per_run → 0 行成功照样判健康（零回归）。"""
    v = triage.classify("src", "spiders/src/", DAY, [_run(2, rows=0)], NOW)
    assert v.category is None and not v.needs_human


def test_declared_floor_turns_a_fresh_zero_row_success_into_a_ticket():
    v = triage.classify("src", "spiders/src/", DAY, [_run(2, rows=0)], NOW,
                        min_rows_per_run=50)
    assert v.category == "structure"
    assert not v.needs_human
    assert "rule1c" in v.rule_trace and "min_rows_per_run=50" in v.rule_trace
    assert "表头" in v.suggested_action


def test_thin_run_above_threshold_is_healthy():
    v = triage.classify("src", "spiders/src/", DAY, [_run(2, rows=200)], NOW,
                        min_rows_per_run=50)
    assert v.category is None


def test_old_thin_run_does_not_keep_a_healthy_source_ticketed():
    """一次陈年低产不得让源永久挂单——判据是最近一次成功，不是窗口里任意一次。

    实证（2026-11-09）：给 38 个 A 桶源声明下限后按真实 crawl_runs 复算，有 10 个
    （gdacs/open-meteo/cisa/… ）历史上各存在一次低于下限的成功，而它们最近一次都正常。
    旧实现扫全窗口，这 10 个会在上线当天各出一张工单，且直到那条陈年 run 滑出回看窗
    之前天天重出。规格 source-output-floor 要的是「成功但 0 行」和「上游少给几条不得误报」。
    """
    runs = [_run(60 * 24, rows=2), _run(3, rows=100)]  # 两个月前一次 2 行，最近正常
    v = triage.classify("src", "spiders/src/", DAY, runs, NOW, min_rows_per_run=20)
    assert v.category is None, v.rule_trace


def test_latest_thin_run_still_tickets_even_if_older_runs_were_fine():
    """反向：最近一次才是坏的，必须出单——否则改宽了就变成永远不出单。"""
    runs = [_run(60 * 24, rows=100), _run(2, rows=5)]
    v = triage.classify("src", "spiders/src/", DAY, runs, NOW, min_rows_per_run=20)
    assert v.category == "structure"
    assert "rule1c" in v.rule_trace
    assert "rows=5" in v.rule_trace


def test_ticket_clears_after_the_next_good_run():
    """出单不是终局：下一次正常即恢复健康，工单可自然消解。"""
    bad = [_run(60 * 24, rows=100), _run(2, rows=5)]
    assert triage.classify("src", "spiders/src/", DAY, bad, NOW,
                           min_rows_per_run=20).category == "structure"
    healed = bad + [_run(1, rows=100)]
    assert triage.classify("src", "spiders/src/", DAY, healed, NOW,
                           min_rows_per_run=20).category is None


def test_historical_lows_are_reported_as_context_not_verdict():
    """陈年低位要出现在 trace 里（便于判断"一向如此"还是"这次突然掉"），但不参与判定。"""
    runs = [_run(60 * 24, rows=1), _run(30 * 24, rows=1), _run(1, rows=3)]
    v = triage.classify("src", "spiders/src/", DAY, runs, NOW, min_rows_per_run=20)
    assert v.category == "structure"
    assert "另有 2 次低于下限" in v.rule_trace


def test_failed_runs_keep_their_own_category():
    """失败运行不该被本规则抢走：仍然是网络/结构那几条规则判。"""
    v = triage.classify("src", "spiders/src/", DAY,
                        [_run(2, status="failed", rows=None) for _ in range(3)], NOW,
                        min_rows_per_run=50)
    assert v.category != "structure" or "rule1c" not in v.rule_trace


def test_manifest_floor_is_read_only_when_declared(tmp_path: Path):
    unit = tmp_path / "spiders" / "src"
    unit.mkdir(parents=True)
    (unit / "manifest.yaml").write_text(yaml.safe_dump({"name": "src", "schedule": "0 1 * * *"}),
                                        encoding="utf-8")
    assert inspector.min_rows_floor(tmp_path, "src") is None

    (unit / "manifest.yaml").write_text(
        yaml.safe_dump({"name": "src", "schedule": "0 1 * * *", "min_rows_per_run": 50}),
        encoding="utf-8")
    assert inspector.min_rows_floor(tmp_path, "src") == 50


@pytest.mark.parametrize("bad", [0, -1, True, "50", 1.5])
def test_malformed_floor_is_ignored_not_guessed(tmp_path: Path, bad):
    unit = tmp_path / "spiders" / "src"
    unit.mkdir(parents=True)
    (unit / "manifest.yaml").write_text(
        yaml.safe_dump({"name": "src", "schedule": "0 1 * * *", "min_rows_per_run": bad}),
        encoding="utf-8")
    assert inspector.min_rows_floor(tmp_path, "src") is None


def test_run_truncated_by_a_smaller_limit_is_not_judged_as_low_output():
    """上限 < 下限的运行物理上写不满，不能拿它判低产。

    实证（2026-11-06）：48 个源各跑一次、每次恰好 20 行（全部 `image_tag=unknown`，
    未走 k8s 调度）。若不看 limit，3 个周频源（boxofficemojo/cisa/gb-standards）的
    最近一次就会永久判"产出不足"，直到下一次正常调度。
    """
    runs = [_run(60 * 24, rows=100, limit=100), _run(2, rows=20, limit=20)]
    v = triage.classify("src", "spiders/src/", DAY, runs, NOW, min_rows_per_run=25)
    assert v.category is None, v.rule_trace


def test_truncated_run_does_not_mask_a_real_low_output():
    """跳过被截断的运行，不等于放弃判定：要退回到上一次可比的运行。"""
    runs = [_run(60 * 24, rows=5, limit=100), _run(2, rows=20, limit=20)]
    v = triage.classify("src", "spiders/src/", DAY, runs, NOW, min_rows_per_run=25)
    assert v.category == "structure"
    assert "最近一次可比成功 rows=5" in v.rule_trace
    assert "已跳过 1 次上限低于下限的运行" in v.rule_trace


def test_unrecorded_limit_is_judged_as_before():
    """历史 run 没有 limit = "不知道"，不是"不可比"：照旧判，零回归。"""
    v = triage.classify("src", "spiders/src/", DAY, [_run(2, rows=5)], NOW,
                        min_rows_per_run=25)
    assert v.category == "structure"


def test_all_runs_truncated_falls_back_instead_of_silently_passing():
    """全被上限压住时不能就此判健康——退回原判据，保持既有行为。"""
    runs = [_run(2, rows=20, limit=20), _run(1, rows=20, limit=20)]
    v = triage.classify("src", "spiders/src/", DAY, runs, NOW, min_rows_per_run=25)
    assert v.category == "structure"


def test_partial_counts_as_reached_for_liveness():
    """`partial` = 爬取成功但 accept 条件未满足：数据不完整，不是没跑。

    把 partial 当没成功，会让一个每天正常在跑的源被判"极久未成功"→ rule2 需人工。
    实测（2026-11-09）：三次 partial、最近一次 2h 前 → 旧逻辑 needs_human=True，
    同数据改成 success 就健康。
    """
    runs = [_run(50, status="partial"), _run(26, status="partial"), _run(2, status="partial")]
    v = triage.classify("src", "spiders/src/", DAY, runs, NOW)
    assert v.category is None and not v.needs_human, v.rule_trace


def test_partial_with_zero_rows_still_trips_the_floor():
    """活性算"跑到了"，不等于产出合格：partial + 0 行 + 下限 5 仍然出单。"""
    v = triage.classify("src", "spiders/src/", DAY, [_run(2, status="partial", rows=0)], NOW,
                        min_rows_per_run=5)
    assert v.category == "structure"
    assert "rule1c" in v.rule_trace


def test_partial_truncated_by_limit_is_also_skipped():
    """被上限压住的 partial 同样不可比：两种状态共用同一套可比性判据。"""
    runs = [_run(60 * 24, status="success", rows=100, limit=100),
            _run(2, status="partial", rows=20, limit=20)]
    v = triage.classify("src", "spiders/src/", DAY, runs, NOW, min_rows_per_run=25)
    assert v.category is None, v.rule_trace
