"""投递通道体检：逐张工单的 error 判定**不足以**让人发现通道整个断了。

2026-11-13~11-14 实测：自愈投递每次都被平台拒（`-32032 fetch failed`、
`-32003 Connection error`；11-14 全量运行实测为 **HTTP 502**、7 张单 2 分 05 秒全灭），
而摘要里只是七张单各一条 `error`——读起来像七张单各自的问题，
通道死了三天没有任何东西会红（最后一次被受理是 2026-11-11T00:08）。

这里钉两件事：
1. 判据只认「每一次尝试都是服务侧错误」才算 outage，别把一张内容有问题的单判成通道死；
2. outage 必须让这一轮**变红**（CLI 退 3），否则又回到「沉默与没有事件长得一样」。
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.health import inspector as ins  # noqa: E402
from scripts import health_inspect as cli  # noqa: E402


def _row(ticket: str, *, verdict: str, ok: bool = False, http_status=502, why: str = "") -> dict:
    return {"ticket": ticket, "submitted": ok, "http_status": http_status,
            "verdict": verdict, "verdict_reason": why or verdict}


class TestLaneVerdict:
    def test_all_service_errors_is_outage(self):
        rows = [_row(f"t{i}.yaml", verdict="error", why="HTTP 502") for i in range(7)]
        lane = ins.delivery_lane_health(rows)
        assert lane["status"] == "outage"
        assert lane["attempted"] == 7 and lane["service_errors"] == 7
        assert lane["accepted"] == 0
        assert lane["reasons"] == ["HTTP 502 (HTTP 502)"], "同一原因只留一条，别让七行 502 冲爆摘要"

    def test_one_accepted_is_only_degraded(self):
        rows = [_row("a.yaml", verdict="error"),
                _row("b.yaml", verdict="accepted", ok=True, http_status=200)]
        assert ins.delivery_lane_health(rows)["status"] == "degraded"

    def test_refusal_is_a_ticket_problem_not_a_lane_outage(self):
        """`refused` = 工单内容/路径问题（如「路径在仓中不存」），通道本身是通的。"""
        rows = [_row("a.yaml", verdict="refused", http_status=200, why="hollow receipt")]
        assert ins.delivery_lane_health(rows)["status"] == "ok"

    def test_no_attempts_is_idle_not_outage(self):
        """没东西可投（干跑/总闸关/全已投）不能报通道坏——那是「没有事件」，不是「事件是坏的」。"""
        assert ins.delivery_lane_health([])["status"] == "idle"
        assert ins.delivery_lane_health(None)["status"] == "idle"
        assert ins.delivery_lane_health(
            [{"ticket": "a.yaml", "submitted": False, "reason": "dry-run"}]
        )["status"] == "idle", "dry-run 条目没有 http_status，不算尝试"

    def test_batch_limit_marker_is_not_an_attempt(self):
        rows = [_row("a.yaml", verdict="error"),
                {"ticket": "__batch_limit__", "submitted": False, "verdict": "deferred"}]
        lane = ins.delivery_lane_health(rows)
        assert lane["attempted"] == 1, "上限标记不是一次投递"

    def test_reasons_are_deduped_and_capped(self):
        rows = [_row("a.yaml", verdict="error", why="HTTP 502"),
                _row("b.yaml", verdict="error", why="HTTP 502"),
                _row("c.yaml", verdict="error", why="-32032 fetch failed"),
                _row("d.yaml", verdict="error", why="-32003 Connection error")]
        lane = ins.delivery_lane_health(rows)
        assert lane["reasons"] == [
            "HTTP 502 (HTTP 502)",
            "-32032 fetch failed (HTTP 502)",
            "-32003 Connection error (HTTP 502)",
        ], "去重后三条，且带上 HTTP 码——否则「fetch failed」看不出是通道还是工单"
        assert lane["status"] == "outage"


class TestCliExitCode:
    """CLI 侧：outage 必须退 3（让 workflow 变红），干跑永远退 0。"""

    def _run(self, tmp_path: pathlib.Path, monkeypatch, rows, *, dry_run=False) -> int:
        snapshot = tmp_path / "runs.json"
        # 快照是**裸列表**（load_runs_from_snapshot 只认 list）；空列表 = 本轮不出单，
        # 正好让被测的是通道体检而不是分诊。
        snapshot.write_text(json.dumps([]), encoding="utf-8")
        outdir = tmp_path / "tickets"
        outdir.mkdir()
        monkeypatch.setenv("WANXING_SPIDER_HEAL_KEY", "dummy-not-a-credential")
        monkeypatch.setattr(ins, "submit_new_tickets", lambda *a, **k: rows)
        monkeypatch.setattr(ins, "sync_status", lambda *a, **k: [])
        argv = ["health_inspect.py", "--snapshot", str(snapshot), "--outdir", str(outdir),
                "--repo", str(tmp_path), "--deliver", "--submit-new"]
        if dry_run:
            argv.append("--dry-run")
        monkeypatch.setattr(sys, "argv", argv)
        return cli.main()

    def test_outage_makes_the_run_red(self, tmp_path, monkeypatch):
        rows = [_row(f"t{i}.yaml", verdict="error", why="HTTP 502") for i in range(3)]
        assert self._run(tmp_path, monkeypatch, rows) == 3, "通道断了这一轮必须红"

    def test_one_accepted_keeps_the_run_green(self, tmp_path, monkeypatch):
        rows = [_row("a.yaml", verdict="error"),
                _row("b.yaml", verdict="duplicate", ok=True, http_status=200)]
        assert self._run(tmp_path, monkeypatch, rows) == 0

    def test_dry_run_never_fails_on_lane(self, tmp_path, monkeypatch):
        rows = [_row(f"t{i}.yaml", verdict="error") for i in range(3)]
        assert self._run(tmp_path, monkeypatch, rows, dry_run=True) == 0, \
            "干跑不投递，不该因为通道体检把校准轮判红"
