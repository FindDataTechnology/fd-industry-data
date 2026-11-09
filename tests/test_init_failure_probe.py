"""Init-failure probe tests (harden-crawl-delivery 8.2).

The only genuinely error-prone part is splitting ``fd-industry-<src>-<jobid>``
back into a source: slugs themselves contain hyphens (``education-podcast``,
``dounan-flower-price``), so a naive ``split('-')[0]`` silently reports the
wrong source. That is what these tests pin down, plus the dedupe rules that keep
a 5-minute probe from flooding ``crawl_runs``.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("probe", REPO / "scripts" / "init_failure_probe.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


probe = _load()

SOURCES = ["education-podcast", "dounan-flower-price", "nifdc-batchcert",
           "portwatch-imf", "eia-v2", "lhb-daily", "nmc-weather"]
ISSUED = "2026-11-09T15:50:00Z"


def _failure(job: str, exit_code: int = 128, pod: str = "p", created: str = ISSUED):
    return {"pod": pod, "job": job, "created": created,
            "exit_code": exit_code, "reason": "Error"}


def test_hyphenated_slug_is_split_correctly():
    assert probe.source_from_job("fd-industry-education-podcast-29904017", SOURCES) == "education-podcast"
    assert probe.source_from_job("fd-industry-dounan-flower-price-29904001", SOURCES) == "dounan-flower-price"


def test_plain_slug():
    assert probe.source_from_job("fd-industry-eia-v2-29903990", SOURCES) == "eia-v2"


def test_platform_components_are_not_sources():
    """dispatcher 不是 spiders 下的源，不该被当成一个数据源报到。"""
    assert probe.source_from_job("fd-industry-dispatcher-29904140", SOURCES) is None


def test_unrelated_job_is_ignored():
    assert probe.source_from_job("some-other-job", SOURCES) is None


def test_one_record_per_source_and_job():
    """同一 Job 的两次重试只留一条；同一源的两个 Job 各留一条。"""
    failures = [
        _failure("fd-industry-eia-v2-100", pod="a"),
        _failure("fd-industry-eia-v2-100", pod="b"),   # 同 Job 重试
        _failure("fd-industry-eia-v2-200", pod="c", created="2026-11-09T16:10:00Z"),
    ]
    rows = probe.plan(failures, SOURCES, {})
    assert len(rows) == 2
    assert [r["job"] for r in rows] == ["fd-industry-eia-v2-100", "fd-industry-eia-v2-200"]


def test_already_reported_source_is_skipped():
    job_start = datetime.fromisoformat(ISSUED.replace("Z", "+00:00"))
    recorded = {"eia-v2": [job_start + timedelta(minutes=3)]}
    rows = probe.plan([_failure("fd-industry-eia-v2-100")], SOURCES, recorded)
    assert rows == []


def test_a_record_outside_the_window_does_not_suppress():
    job_start = datetime.fromisoformat(ISSUED.replace("Z", "+00:00"))
    recorded = {"eia-v2": [job_start - timedelta(hours=2)]}
    rows = probe.plan([_failure("fd-industry-eia-v2-100")], SOURCES, recorded)
    assert len(rows) == 1


def test_error_head_is_one_line_and_names_the_cause():
    rows = probe.plan([_failure("fd-industry-eia-v2-100", exit_code=128)], SOURCES, {})
    head = rows[0]["error_head"]
    assert "\n" not in head
    assert "exit=128" in head and "credential" in head


def test_exit_code_classes():
    oom = probe.plan([_failure("fd-industry-eia-v2-1", exit_code=137)], SOURCES, {})
    other = probe.plan([_failure("fd-industry-eia-v2-1", exit_code=1)], SOURCES, {})
    assert oom[0]["cause"] == "oom-or-kill"
    assert other[0]["cause"] == "unknown"


def test_rows_are_ordered_by_job_start():
    failures = [
        _failure("fd-industry-eia-v2-2", created="2026-11-09T16:30:00Z"),
        _failure("fd-industry-eia-v2-1", created="2026-11-09T15:50:00Z"),
    ]
    rows = probe.plan(failures, SOURCES, {})
    assert [r["job"] for r in rows] == ["fd-industry-eia-v2-1", "fd-industry-eia-v2-2"]
