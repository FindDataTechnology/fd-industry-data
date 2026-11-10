"""SUBMIT 的 turn 串行语义（2026-11-10 实测缺陷回归）。

实测：批量提交时第 2 条起全部 HTTP 409 `TURN_IN_FLIGHT`（每 caller+agent 同时只一个
turn），18 条里 17 条每天重试每天 409、工单永久卡死。这里钉两件事：
1. 被占时**有界等待**后重试同一张单，等到就正常受理；
2. 预算耗尽 → 记 `deferred` 并**本批停手**（继续捶剩余单只会各自 409）。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.health import inspector as ins  # noqa: E402
from fd_industry_data.health import ticket as tm  # noqa: E402

EV = {"window": {"from": "2026-11-01", "to": "2026-11-10"}, "failing_runs": [],
      "last_success": None, "watermark": None}


def _mk(dirpath: pathlib.Path, slugs=("t-a", "t-b")) -> pathlib.Path:
    for slug in slugs:
        doc = tm.new_ticket(source=slug, unit=f"spiders/{slug}/", kind="generate",
                            brief={"source_urls": ["https://e.com"], "expectations": "x"},
                            evidence=EV, verify_commands=["true"])
        (dirpath / f"20261110-{slug}.yaml").write_text(tm.dump_ticket(doc), encoding="utf-8")
    return dirpath


@pytest.fixture
def cfg():
    return type("C", (), {"master_switch": True})()


@pytest.fixture(autouse=True)
def _fast_wait(monkeypatch):
    monkeypatch.setattr(ins, "_SUBMIT_TURN_POLL_S", 0.001)
    monkeypatch.setattr(ins, "_SUBMIT_TURN_BUDGET_S", 0.004)


def test_detects_only_409_turn_in_flight():
    assert ins._is_turn_in_flight(409, '{"code": "TURN_IN_FLIGHT"}') is True
    assert ins._is_turn_in_flight(409, '{"code": "CONFLICT"}') is False
    assert ins._is_turn_in_flight(500, "TURN_IN_FLIGHT") is False


def test_waits_then_accepts_same_ticket(tmp_path, cfg):
    d = _mk(tmp_path)
    calls: list[str] = []

    def poster(url, key, idem, text):
        calls.append(idem)
        if len(calls) == 1:
            return 409, '{"code": "TURN_IN_FLIGHT"}'
        return 200, '{"status": "queued"}'

    res = ins.submit_new_tickets(d, "org/repo", "http://x", "k", cfg, poster=poster)
    assert [r["submitted"] for r in res] == [True, True]
    assert len(calls) == 3  # 第一张 409→重试→200；第二张直接 200


def test_budget_exhausted_defers_and_stops_batch(tmp_path, cfg):
    d = _mk(tmp_path, slugs=("t-a", "t-b", "t-c"))
    calls: list[str] = []

    def poster(*args):
        calls.append(args[2])
        return 409, '{"code": "TURN_IN_FLIGHT"}'

    res = ins.submit_new_tickets(d, "org/repo", "http://x", "k", cfg, poster=poster)
    assert len(res) == 1, "预算耗尽后必须停手，不得继续捶剩余工单"
    assert res[0]["verdict"] == "deferred"
    assert res[0]["submitted"] is False
    assert "停手" in res[0]["verdict_reason"]
    # 延后不算受理：下一轮巡检还会重投这张单
    doc = tm.load_ticket(d / res[0]["ticket"])
    assert ins._submit_recorded(doc) is False


def test_batch_limits_bound_the_run(tmp_path, cfg, monkeypatch):
    """每轮限量 + 批量等待预算：不加界会撞 workflow 45 分钟超时（实测 49m30s 被掐）。"""
    monkeypatch.setattr(ins, "_SUBMIT_MAX_TICKETS_PER_RUN", 2)
    monkeypatch.setattr(ins, "_SUBMIT_BATCH_BUDGET_S", 0.01)
    d = _mk(tmp_path, slugs=("t-a", "t-b", "t-c", "t-d"))
    calls: list[str] = []

    def poster(url, key, idem, text):
        calls.append(idem)
        return 200, '{"status": "queued"}'

    res = ins.submit_new_tickets(d, "org/repo", "http://x", "k", cfg, poster=poster)
    real = [r for r in res if r["ticket"] != "__batch_limit__"]
    assert len(real) == 2, f"每轮最多 2 张，实际 {len(real)}"
    assert any(r["ticket"] == "__batch_limit__" for r in res), "超限需留一条说明条目"
    assert len(calls) == 2


def test_status_sync_is_not_bounded_by_submit_limits(tmp_path, monkeypatch):
    """批量上限只作用于 SUBMIT：STATUS 回读有自己的 max_reads，不受影响。"""
    d = _mk(tmp_path, slugs=("t-a", "t-b", "t-c"))
    monkeypatch.setattr(ins, "_SUBMIT_MAX_TICKETS_PER_RUN", 1)
    read: list[str] = []

    def fetcher(url, key, idem, text):
        read.append(text)
        return 200, '{"state": "working"}'

    res = ins.sync_status(d, "http://x", "k", fetcher=fetcher, max_reads=3)
    assert len(res) == 3, "STATUS 应读满自己的 max_reads，不被 SUBMIT 的 1 张上限约束"


def test_generation_tickets_submitted_before_repairs(tmp_path, cfg, monkeypatch):
    """turn 串行、每轮只能推进两三张：generate 单必须排在 repair 前面。

    不加这条，业务单永远排在每天自动重生成的 repair 单后面（实测 2026-11-10：
    18 张积压，每轮只推进 2 张，业务单排在队尾）。
    """
    monkeypatch.setattr(ins, "_SUBMIT_MAX_TICKETS_PER_RUN", 1)
    monkeypatch.setattr(ins, "_SUBMIT_BATCH_BUDGET_S", 999)
    # 文件名顺序：repair 的单在字典序上更早（a-）但仍应排在 generate 之后
    for name, slug, kind in (
        ("20261101-a-repair.yaml", "aaa", "repair"),
        ("20261109-z-generate.yaml", "zzz", "generate"),
    ):
        doc = tm.new_ticket(source=slug, unit=f"spiders/{slug}/", kind=kind,
                            brief={"source_urls": ["https://e.com"], "expectations": "x"},
                            evidence=EV, verify_commands=["true"])
        (tmp_path / name).write_text(tm.dump_ticket(doc), encoding="utf-8")

    calls: list[str] = []

    def poster(url, key, idem, text):
        calls.append(text)
        return 200, '{"status": "queued"}'

    res = ins.submit_new_tickets(tmp_path, "org/repo", "http://x", "k", cfg, poster=poster)
    real = [r for r in res if r["ticket"] != "__batch_limit__"]
    assert len(real) == 1
    assert real[0]["ticket"] == "20261109-z-generate.yaml", "generate 单必须优先"
