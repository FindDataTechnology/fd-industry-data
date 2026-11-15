"""pod_gc 的选择逻辑（2026-11-14 立，2026-11-15 加封存档）。

这组测钉五件会「删错东西」或「静默什么都不删」的事：

1. **只删 Succeeded/Failed**。Running/Pending 一律不碰——一次误判就是杀线上服务。
2. **裸 Pod 永不删**：没有 Job/ReplicaSet owner 的，可能是有人手工起的现场，
   删了不可恢复（同族于「清理只按精确名字，绝不 grep 宽匹配」）。
3. **时间读不出就不删**：一次时间格式变更不该变成「整批删光」。
4. **「跳过」要能看见**：全是太新/无 owner 时，日志必须与「判据写错」可区分。
5. **失败 Job 的 Pod 封存**：删了它，失败原因（只存在于最后一个 Pod 的日志里）
   就永久消失——2026-11-15 实历，`sink-freshness` 21:10Z 那轮真红就是这么丢的。
   三向都要证：失败 Job ⇒ 留；成功 Job ⇒ 照删；封存到期 ⇒ 照删。
"""
from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.pod_gc import (  # noqa: PLC2701 — 脚本与测同仓
    failed_job_names, main, parse_ts, select_pods, unavailable_note)

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def pod(name: str, phase: str, *, owners=None, start: str | None = None,
        exit_code: int | None = None) -> dict:
    owner_refs = owners if owners is not None else [{"kind": "Job", "name": name.rsplit("-", 2)[0]}]
    item = {
        "metadata": {
            "name": name,
            "ownerReferences": owner_refs,
            "creationTimestamp": start or (NOW - timedelta(hours=3)).isoformat(),
        },
        "status": {"phase": phase, "startTime": start or (NOW - timedelta(hours=3)).isoformat()},
    }
    if exit_code is not None:
        item["status"]["containerStatuses"] = [
            {"name": "c", "state": {"terminated": {"exitCode": exit_code}}}]
    return item


def job(name: str, failed: bool) -> dict:
    return {"metadata": {"name": name},
            "status": {"conditions": [{"type": "Failed" if failed else "Complete",
                                       "status": "True"}]}}


def test_deletes_old_finished_pods_only():
    pods = [pod("a-succeeded", "Succeeded"), pod("b-failed", "Failed"),
            pod("c-running", "Running"), pod("d-pending", "Pending")]
    dead, skipped, unparsed, held = select_pods(pods, NOW, 1.0, failed_jobs=set())
    assert sorted(dead) == ["a-succeeded", "b-failed"]
    assert skipped == 0 and unparsed == 0 and held == 0


def test_bare_pods_are_never_deleted():
    pods = [pod("bare", "Succeeded", owners=[]),
            pod("owned", "Succeeded")]
    dead, skipped, _, _ = select_pods(pods, NOW, 1.0, failed_jobs=set())
    assert dead == ["owned"]
    assert skipped == 1, "被跳过的裸 Pod 要计数，否则与「判据写错」无法区分"


def test_replicaset_owned_finished_pods_count_as_debris():
    # legal-auth-broker 那种反复重启留下的死 Pod 就是这一类。
    pods = [pod("rs-pod", "Succeeded", owners=[{"kind": "ReplicaSet", "name": "rs"}])]
    assert select_pods(pods, NOW, 1.0, failed_jobs=set())[0] == ["rs-pod"]


def test_recent_finished_pods_are_kept():
    fresh = (NOW - timedelta(minutes=10)).isoformat()
    pods = [pod("just-finished", "Succeeded", start=fresh)]
    dead, skipped, _, _ = select_pods(pods, NOW, 1.0, failed_jobs=set())
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
    dead, _, unparsed, _ = select_pods(pods, NOW, 1.0, failed_jobs=set())
    assert dead == [], "时间读不出时删了就是不可逆事故"
    assert unparsed == 2


def test_failed_job_pods_are_held_for_forensics():
    # 2026-11-15 实历的形态：Job 带 Failed=True，Pod 三小时前就结束了。
    # 删掉它，失败原因（只在最后一个 Pod 的日志里）就再也读不到。
    pods = [pod("sink-freshness-abcd-1", "Failed", owners=[{"kind": "Job", "name": "sink-freshness-abcd"}])]
    dead, skipped, _, held = select_pods(pods, NOW, 1.0, failed_jobs={"sink-freshness-abcd"})
    assert dead == [], "失败 Job 的 Pod 是唯一证据，不许当残骸删"
    assert held == 1, "封存数要单独打出来，否则与「没有失败作业」无法区分"
    assert skipped == 0


def test_succeeded_job_pods_still_deleted_even_if_pod_phase_failed():
    # 重试后成功的 Job：失败的尝试 Pod 是残骸，不是证据（这个 Job 已经红了又绿）。
    pods = [pod("retried-abcd-1", "Failed", owners=[{"kind": "Job", "name": "retried-abcd"}])]
    dead, _, _, held = select_pods(pods, NOW, 1.0, failed_jobs=set())
    assert dead == ["retried-abcd-1"]
    assert held == 0


def test_forensic_hold_is_capped_per_cronjob():
    """设计内常红（`image-gc` 每 15 分钟按 82% 门判失败）不能把 Pod 攒成照片墙：
    每个 CronJob 只留最新 N 个，多出来的照删。2026-11-15 自查实历。"""
    # 年龄必须都 ≥ 1h（否则先被 min_age 跳过，这条就测不到限额）
    pods = [pod(f"image-gc-vm-0-4-ubuntu-2991180{n}-aaaa", "Failed",
                owners=[{"kind": "Job", "name": f"image-gc-vm-0-4-ubuntu-2991180{n}"}],
                start=(NOW - timedelta(minutes=61 + n)).isoformat())
            for n in range(4)]
    dead, _, _, held = select_pods(
        pods, NOW, 1.0,
        failed_jobs={f"image-gc-vm-0-4-ubuntu-2991180{n}" for n in range(4)},
        forensic_max=2)
    assert held == 2, "只留最新 2 个"
    # `start = NOW - (61+n)` ⇒ n 越大越老；留最新两个（n=0,1）⇒ 删 n=2,3
    assert sorted(dead) == ["image-gc-vm-0-4-ubuntu-29911802-aaaa",
                            "image-gc-vm-0-4-ubuntu-29911803-aaaa"], "更早的两个照删"


def test_forensic_cap_is_per_cronjob_not_global():
    """限额按 CronJob 前缀各算各的：不能跨作业串味（否则第二个作业永远留不下证据）。"""
    pods = [pod("a-29911800-1", "Failed", owners=[{"kind": "Job", "name": "a-29911800"}]),
            pod("a-29911801-1", "Failed", owners=[{"kind": "Job", "name": "a-29911801"}],
                start=(NOW - timedelta(hours=2)).isoformat()),
            pod("b-29911800-1", "Failed", owners=[{"kind": "Job", "name": "b-29911800"}])]
    dead, _, _, held = select_pods(pods, NOW, 1.0,
                                   failed_jobs={"a-29911800", "a-29911801", "b-29911800"},
                                   forensic_max=1)
    assert held == 2, "a 留最新一个、b 留一个"
    assert dead == ["a-29911800-1"], "a 里更早那条（默认 3h 前）照删"


def test_forensic_hold_expires():
    old = (NOW - timedelta(hours=80)).isoformat()
    pods = [pod("stuck-abcd-1", "Failed", owners=[{"kind": "Job", "name": "stuck-abcd"}], start=old)]
    dead, _, _, held = select_pods(pods, NOW, 1.0, failed_jobs={"stuck-abcd"}, forensic_h=72.0)
    assert dead == ["stuck-abcd-1"], "封存不是永久保留：到期照删，否则照片墙会一直长"
    assert held == 0


def test_without_job_table_falls_back_to_exit_code():
    # 取不到 Job 表时不许「什么都不留」：退回 Pod 自己的退出码。
    pods = [pod("gate-abcd-1", "Failed", exit_code=1),
            pod("ok-abcd-1", "Succeeded", exit_code=0),
            pod("unknown-abcd-1", "Failed")]
    dead, _, _, held = select_pods(pods, NOW, 1.0, failed_jobs=None)
    assert dead == ["ok-abcd-1", "unknown-abcd-1"]
    assert held == 1, "退出码非 0 的留作证据；读不到退出码的按原规则删（清理是默认动作）"


def test_failed_job_names_reads_condition_not_count():
    # 只认 condition Failed=True：重试成功但 status.failed>0 的 Job 不在里面。
    items = [job("perm", True), job("retried", False),
             {"metadata": {"name": "counted"},
              "status": {"failed": 3, "conditions": [{"type": "Complete", "status": "True"}]}}]
    assert failed_job_names(items) == {"perm"}


def test_parse_ts_accepts_kubernetes_shapes():
    assert parse_ts("2026-11-14T09:00:00Z") == datetime(2026, 11, 14, 9, tzinfo=timezone.utc)
    # 带纳秒的真实形状：Python 3.9/3.10 的 fromisoformat 只吃到微秒
    got = parse_ts("2026-11-14T09:00:00.123456789Z")
    assert got is not None and got.microsecond == 123456
    assert parse_ts("2026-11-14T09:00:00+00:00") == datetime(2026, 11, 14, 9, tzinfo=timezone.utc)
    assert parse_ts("garbage") is None
    assert parse_ts(None) is None


def test_main_dry_run_lists_without_deleting(tmp_path, capsys):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"items": [pod("old", "Succeeded")]}), encoding="utf-8")
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps({"items": [job("old", True)]}), encoding="utf-8")
    assert main(["--from-json", str(snap), "--jobs-from-json", str(jobs),
                 "--dry-run", "--namespace", "scraw"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["held_for_forensics"] == 1 and out["deleted"] == 0
    assert out["jobs_map"] == "fixture"


def test_main_without_jobs_fixture_says_so(tmp_path, capsys):
    # 离线没给 Job 快照 ⇒ jobs_map 必须是 unavailable（不是静默当地图是空的）。
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"items": [pod("x", "Succeeded", exit_code=0)]}), encoding="utf-8")
    assert main(["--from-json", str(snap), "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["jobs_map"] == "unavailable"


def test_main_with_empty_snapshot_still_reports(tmp_path):
    # 空列表是合法输入（这一轮确实没有残骸），同样退 0；判据读的是 candidates 字段。
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"items": []}), encoding="utf-8")
    assert main(["--from-json", str(snap), "--dry-run"]) == 0
    assert Path(snap).exists()


def test_unavailable_note_carries_http_status():
    # 2026-11-15 实历：Role 补 `batch/jobs` 的提交与）Job 起跑只差 34 秒，
    # 日志里只有 `HTTPError` 四个字 ⇒ 分不出「还在等 Argo 同步」与「压根没授上」。
    err = urllib.error.HTTPError("http://x/apis/batch/v1/namespaces/fd-prod/jobs",
                                403, "Forbidden", None, None)
    assert unavailable_note(err) == "unavailable: HTTPError:403"
    # 404 是另一件事（路径/版本不对 ⇒ 改代码），不许与 403 同形
    assert unavailable_note(urllib.error.HTTPError("u", 404, "nope", None, None)) == \
        "unavailable: HTTPError:404"
    # 没有状态码的异常保持原样，不补一个假的 0
    assert unavailable_note(TimeoutError("timed out")) == "unavailable: TimeoutError"
    assert unavailable_note(ConnectionResetError("boom")) == "unavailable: ConnectionResetError"


def test_main_surfaces_job_table_status_code(tmp_path, monkeypatch, capsys):
    # 端到端：取不到 Job 表时，输出里的 jobs_map 必须带上 HTTP 状态码。
    import scripts.pod_gc as pg

    (tmp_path / "token").write_text("dummy-token\n", encoding="utf-8")
    (tmp_path / "ca.crt").write_text("", encoding="utf-8")
    monkeypatch.setattr(pg, "SA_DIR", str(tmp_path))
    monkeypatch.setattr(pg.ssl, "create_default_context", lambda **_kw: None)

    def fake_api(path, *_args, **_kwargs):
        if path.endswith("/jobs"):
            raise urllib.error.HTTPError(path, 403, "Forbidden", None, None)
        return {"items": [pod("old", "Succeeded")]}

    monkeypatch.setattr(pg, "_api_call", fake_api)
    assert main(["--namespace", "fd-prod", "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["jobs_map"] == "unavailable: HTTPError:403"
    assert out["held_for_forensics"] == 0
    assert out["deleted"] == 1, "dry-run 只计数不删；取不到 Job 表不该让整轮不清理"
