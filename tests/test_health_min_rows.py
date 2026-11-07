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


def _run(hours_ago: float, status: str = "success", rows: int | None = 100) -> triage.Run:
    return triage.Run(started_at=(NOW - timedelta(hours=hours_ago)).isoformat(),
                      status=status, rows=rows)


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
