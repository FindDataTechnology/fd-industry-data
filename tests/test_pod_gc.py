"""pod_gc 的选择逻辑（2026-11-14）。

这组测钉四件会「删错东西」或「静默什么都不删」的事：

1. **只删 Succeeded/Failed**。Running/Pending 一律不碰——一次误判就是杀线上服务。
2. **裸 Pod 永不删**：没有 Job/ReplicaSet owner 的，可能是有人手工起的现场，
   删了不可恢复（同族于「清理只按精确名字，绝不 grep 宽匹配」）。
3. **时间读不出就不删**：一次时间格式变更不该变成「整批删光」。
4. **「跳过」要能看见**：全是太新/无 owner 时，日志必须与「判据写错」可区分。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.pod_gc import main, parse_ts, select_pods  # noqa: PLC2701 — 脚本与测同仓

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def pod(name: str, phase: str, *, owners=None, start: str | None = None) -> dict:
    return {
        "metadata": {
            "name": name,
            "ownerReferences": owners if owners is not None else
            [{"kind": "Job", "name": name.rsplit("-", 2)[0]}],
            "creationTimestamp": start or (NOW - timedelta(hours=3)).isoformat(),
        },
        "status": {"phase": phase, "startTime": start or (NOW - timedelta(hours=3)).isoformat()},
    }


def test_deletes_old_finished_pods_only():
    pods = [pod("a-succeeded", "Succeeded"), pod("b-failed", "Failed"),
            pod("c-running", "Running"), pod("d-pending", "Pending")]
    dead, skipped, unparsed = select_pods(pods, NOW, 1.0)
    assert sorted(dead) == ["a-succeeded", "b-failed"]
    assert skipped == 0 and unparsed == 0


def test_bare_pods_are_never_deleted():
    pods = [pod("bare", "Succeeded", owners=[]),
            pod("owned", "Succeeded")]
    dead, skipped, _ = select_pods(pods, NOW, 1.0)
    assert dead == ["owned"]
    assert skipped == 1, "被跳过的裸 Pod 要计数，否则与「判据写错」无法区分"


def test_replicaset_owned_finished_pods_count_as_debris():
    # legal-auth-broker 那种反复重启留下的死 Pod 就是这一类。
    pods = [pod("rs-pod", "Succeeded", owners=[{"kind": "ReplicaSet", "name": "rs"}])]
    assert select_pods(pods, NOW, 1.0)[0] == ["rs-pod"]


def test_recent_finished_pods_are_kept():
    fresh = (NOW - timedelta(minutes=10)).isoformat()
    pods = [pod("just-finished", "Succeeded", start=fresh)]
    dead, skipped, _ = select_pods(pods, NOW, 1.0)
    assert dead == []
    assert skipped == 1


def test_unparsable_timestamp_is_counted_not_deleted():
    # 直接构造「没有可用时间戳」的 Pod：helper 的 `start or 默认值` 会兜底，
    # 用它测不出这一档（第一次写这条用例就栽在这里）。
    pods = [
        {"metadata": {"name": "weird", "ownerReferences": [{"kind": "Job"}],
                      "creationTimestamp": "not-a-timestamp"},
         "status": {"phase": "Succeeded", "startTime": "not-a-timestamp"}},
        {"metadata": {"name": "empty", "ownerReferences": [{"kind": "Job"}],
                      "creationTimestamp": None},
         "status": {"phase": "Succeeded", "startTime": None}},
    ]
    dead, _, unparsed = select_pods(pods, NOW, 1.0)
    assert dead == [], "时间读不出时删了就是不可逆事故"
    assert unparsed == 2


def test_parse_ts_accepts_kubernetes_shapes():
    assert parse_ts("2026-11-14T09:00:00Z") == datetime(2026, 11, 14, 9, tzinfo=timezone.utc)
    # 带纳秒的真实形状：Python 3.9/3.10 的 fromisoformat 只吃到微秒
    got = parse_ts("2026-11-14T09:00:00.123456789Z")
    assert got is not None and got.microsecond == 123456
    assert parse_ts("2026-11-14T09:00:00+00:00") == datetime(2026, 11, 14, 9, tzinfo=timezone.utc)
    assert parse_ts("garbage") is None
    assert parse_ts(None) is None


def test_main_dry_run_lists_without_deleting(tmp_path):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"items": [pod("old", "Succeeded")]}), encoding="utf-8")
    assert main(["--from-json", str(snap), "--dry-run", "--namespace", "scraw"]) == 0


def test_main_with_empty_snapshot_still_reports(tmp_path):
    # 空列表是合法输入（这一轮确实没有残骸），同样退 0；判据读的是 candidates 字段。
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"items": []}), encoding="utf-8")
    assert main(["--from-json", str(snap), "--dry-run"]) == 0
    assert Path(snap).exists()
