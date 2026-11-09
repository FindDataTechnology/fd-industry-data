"""Cost instrumentation tests (harden-crawl-delivery 5.1).

The structure/cost decision needs numbers the table never had: `crawl_runs`
only records the runner's wall time, so a per-source pod's clone and startup
cost is invisible. These keys are recorded when known and **absent** when not —
a 0-second clone is a claim, and a wrong claim is worse than "not measured".
"""
from __future__ import annotations

import time
from pathlib import Path

from fd_industry_data.runner_cli import _process_metrics


def test_runner_time_and_memory_are_measured():
    out = _process_metrics({"started": time.time() - 2.0})
    assert out["runner_seconds"] >= 1.9
    assert out["peak_rss_mb"] > 0


def test_timings_file_is_read(tmp_path, monkeypatch):
    (tmp_path / ".timings").write_text("pod_start_seconds=3.5\nclone_seconds=12.25\n")
    monkeypatch.setenv("FD_CONTENT_DIR", str(tmp_path / "spiders"))
    out = _process_metrics()
    assert out["pod_start_seconds"] == 3.5
    assert out["clone_seconds"] == 12.25


def test_absent_timings_are_absent_not_zero(tmp_path, monkeypatch):
    """没有计时文件时不能写 0 —— 0 是一个断言，『没测』不是。"""
    monkeypatch.setenv("FD_CONTENT_DIR", str(tmp_path / "spiders"))
    out = _process_metrics()
    assert "clone_seconds" not in out
    assert "pod_start_seconds" not in out


def test_this_pods_file_wins_over_inherited_env(tmp_path, monkeypatch):
    """`.timings` 是本 pod 自己的 initContainer 写的，比可能继承来的环境变量可信。"""
    (tmp_path / ".timings").write_text("clone_seconds=99\n")
    monkeypatch.setenv("FD_CONTENT_DIR", str(tmp_path / "spiders"))
    monkeypatch.setenv("FD_CLONE_SECONDS", "5.5")
    assert _process_metrics()["clone_seconds"] == 99.0


def test_garbage_values_are_ignored(tmp_path, monkeypatch):
    (tmp_path / ".timings").write_text("clone_seconds=not-a-number\n")
    monkeypatch.setenv("FD_CONTENT_DIR", str(tmp_path / "spiders"))
    monkeypatch.setenv("FD_POD_START_SECONDS", "abc")
    out = _process_metrics()
    assert "clone_seconds" not in out and "pod_start_seconds" not in out


def test_unknown_keys_in_timings_are_ignored(tmp_path, monkeypatch):
    (tmp_path / ".timings").write_text("something_else=1\nclone_seconds=2\n")
    monkeypatch.setenv("FD_CONTENT_DIR", str(tmp_path / "spiders"))
    out = _process_metrics()
    assert "something_else" not in out
    assert out["clone_seconds"] == 2.0
