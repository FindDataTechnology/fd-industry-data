"""定时作业失败告警的判据（2026-11-15）。

每一条都成对：红路证明判据会响，绿路证明它不会把正常形态报成故障。
`--assume-*` 是「不必真造一个坏作业」的自证入口，这里也各钉一条。
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import backup_job_alerts as B  # noqa: E402

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
THRESHOLDS = {"stall_periods": 2, "stall_slack_minutes": 60,
              "never_scheduled_grace_minutes": 60, "stuck_active_hours": 24}


def iso(minutes_ago: float | None) -> str | None:
    if minutes_ago is None:
        return None
    return (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def cronjob(name="wanxing-backup", *, schedule="0 3 * * *", suspend=False,
            last_success=None, last_schedule=None, created=None, namespace="fd-prod"):
    return {
        "metadata": {"name": name, "namespace": namespace,
                     "creationTimestamp": iso(created if created is not None else 60 * 24 * 30)},
        "spec": {"schedule": schedule, "suspend": suspend},
        "status": {"lastSuccessfulTime": iso(last_success), "lastScheduleTime": iso(last_schedule)},
    }


def job(cronjob_name="wanxing-backup", name=None, *, failed=False, complete=False,
        created=60, started=None, completed=None, reason="BackoffLimitExceeded"):
    name = name or f"{cronjob_name}-28991234"
    status: dict = {}
    conds = []
    if failed:
        conds.append({"type": "Failed", "status": "True", "reason": reason,
                      "message": f"{reason}: Job has reached the specified backoff limit"})
    if complete:
        conds.append({"type": "Complete", "status": "True"})
    if conds:
        status["conditions"] = conds
    if started is not None:
        status["startTime"] = iso(started)
    if completed is not None:
        status["completionTime"] = iso(completed)
    return {
        "metadata": {"name": name, "namespace": "fd-prod", "creationTimestamp": iso(created),
                     "ownerReferences": [{"kind": "CronJob", "name": cronjob_name}]},
        "status": status,
    }


def run(cronjobs, jobs, **kw):
    return B.evaluate(cronjobs, jobs, now=NOW, thresholds=kw.pop("thresholds", THRESHOLDS), **kw)


# ── 排期解析 ────────────────────────────────────────────────────────────────

def test_parse_cron_fields():
    assert B.parse_cron("*/15 * * * *")[4] == {0, 15, 30, 45}
    assert B.parse_cron("17,47 * * * *")[4] == {17, 47}
    assert B.parse_cron("0 3 * * *")[3] == {3}
    assert B.parse_cron("23 5 1 * *")[1] == {1}
    # 0 与 7 都表示周日
    assert B.parse_cron("10 5 * * 0")[2] == {0}
    assert B.parse_cron("10 5 * * 7")[2] == {0}
    assert B.parse_cron("@daily") == B.parse_cron("0 0 * * *")


def test_max_gap_by_cadence():
    assert B.max_gap_minutes("*/15 * * * *", NOW) == 15
    assert B.max_gap_minutes("17,47 * * * *", NOW) == 30
    assert B.max_gap_minutes("0 3 * * *", NOW) == 1440
    assert B.max_gap_minutes("10 5 * * 0", NOW) == 7 * 1440
    monthly = B.max_gap_minutes("23 5 1 * *", NOW)
    assert 28 * 1440 <= monthly <= 31 * 1440, monthly


@pytest.mark.parametrize("bad", ["every day", "0 3 * *", "61 * * * *", "*/0 * * * *", "0 3 * * FOO"])
def test_bad_schedule_raises(bad):
    with pytest.raises(ValueError):
        B.max_gap_minutes(bad, NOW)


def test_unparseable_schedule_is_a_finding_not_a_pass():
    findings, facts, rows = run([cronjob(schedule="every day", last_success=10)], [])
    assert [f["kind"] for f in findings] == ["schedule-unparseable"]
    assert rows[0]["verdict"] == "unparseable"


# ── 六种形态：红路 ──────────────────────────────────────────────────────────

def test_failed_latest_scheduled_job_is_flagged():
    findings, _, _ = run(
        [cronjob(last_success=60 * 25, last_schedule=60 * 24)],
        [job(name="wanxing-backup-28991200", failed=True, created=60 * 24)],
    )
    assert len(findings) == 1
    assert findings[0]["kind"] == "job-failed"
    assert "BackoffLimitExceeded" in findings[0]["detail"]


def test_stall_when_last_success_too_old():
    # 日频阈值 = 2*1440+60 = 2940min；成功在 4000min 前
    findings, _, _ = run([cronjob(last_success=4000, last_schedule=1440)], [])
    assert [f["kind"] for f in findings] == ["stall"]


def test_never_scheduled_beyond_one_period():
    """日频的宽限是「一个完整周期」（至少 60min）：建了 10h 的日频作业还不该报。"""
    findings, _, _ = run([cronjob(last_success=None, last_schedule=None, created=600)], [])
    assert findings == []
    findings, _, _ = run([cronjob(last_success=None, last_schedule=None, created=1500)], [])
    assert [f["kind"] for f in findings] == ["never-scheduled"]


def test_scheduled_but_never_succeeded_is_stall_not_never_scheduled():
    findings, _, _ = run([cronjob(last_success=None, last_schedule=4000, created=60 * 30)], [])
    assert [f["kind"] for f in findings] == ["stall"]
    assert "从未成功" in findings[0]["detail"]


def test_stuck_active_job():
    findings, _, _ = run(
        [cronjob(last_success=60, last_schedule=60)],
        [job(name="wanxing-backup-28991200", created=60 * 30, started=60 * 30)],
    )
    assert [f["kind"] for f in findings] == ["stuck-active"]


def test_assume_failed_and_assume_stale_inject():
    cjs = [cronjob(name="a-backup", last_success=10, last_schedule=10),
           cronjob(name="b-backup", last_success=10, last_schedule=10)]
    findings, _, _ = run(cjs, [], assume_failed=("fd-prod/a-backup",))
    assert [f["name"] for f in findings] == ["a-backup"]
    findings, _, _ = run(cjs, [], assume_stale=("fd-prod/b-backup",))
    assert [f["name"] for f in findings] == ["b-backup"]


# ── 六种形态：绿路（判据不能把正常形态报成故障） ────────────────────────────

def test_healthy_cronjob_has_no_findings():
    findings, facts, rows = run(
        [cronjob(last_success=120, last_schedule=60)],
        [job(complete=True, created=120)],
    )
    assert findings == [] and facts == []
    assert rows[0]["verdict"] == "ok"


def test_suspended_is_fact_not_finding():
    findings, facts, _ = run([cronjob(suspend=True, last_success=60 * 24 * 90)], [])
    assert findings == []
    assert [f["kind"] for f in facts] == ["suspended"]


def test_manual_failed_job_is_fact_not_finding():
    """`create job --from` 造的手工作业名字不含 epoch 分钟 ⇒ 不是排期轮次。"""
    findings, facts, _ = run(
        [cronjob(last_success=60, last_schedule=60)],
        [job(name="fedobs-repro-1", failed=True, created=60),
         job(complete=True, created=120)],
    )
    assert findings == []
    assert [f["kind"] for f in facts] == ["manual-job"]
    assert "Failed" in facts[0]["detail"]


def test_manual_job_with_trailing_digits_is_still_manual():
    """`sinkf-verify-11150048`（2026-11-15 在 fd-prod 实测存在）：末尾是数字，
    只按 `<x>-<数字>` 的形状判会把它当排期轮次 ⇒ 手工失败会被报成「最近一轮失败」。"""
    findings, facts, _ = run(
        [cronjob("sink-freshness", last_success=60, last_schedule=60)],
        [job("sink-freshness", name="sinkf-verify-11150048", failed=True, created=60)],
    )
    assert findings == []
    assert [f["kind"] for f in facts] == ["manual-job"]


# ── 成功时刻只认排期轮次（2026-11-15 补：手工件会把 stall 洗绿）──────────────

def test_manual_success_does_not_clear_a_stall():
    """真形态（fd-prod `sink-freshness` 2026-11-15）：排期最后一轮失败、另一个会话建
    `--from=cronjob` 的验证作业成功 ⇒ `lastSuccessfulTime` 被推到 12min 前。
    不设守卫的话，`stall` 会被这次手工成功清成绿——正是「判据只能人跑」的反面。"""
    findings, facts, rows = run(
        [cronjob("sink-freshness", last_success=12, last_schedule=296)],
        [job("sink-freshness", name="sink-freshness-verify-r9", complete=True,
             created=12, completed=12),
         job("sink-freshness", name="sink-freshness-29911510", failed=True, created=296)],
    )
    assert [f["kind"] for f in findings] == ["job-failed"]  # 排期失败照常报
    assert "success-time-untrusted" in [f["kind"] for f in facts]
    assert rows[0]["verdict"] == "job-failed"


def test_manual_success_alone_makes_stall_undecidable_not_green():
    """只剩「手工成功」一条记录（排期成功 Job 已被 GC）⇒ 停滞**判不出**，不许读成通过。"""
    findings, facts, rows = run(
        [cronjob(last_success=12, last_schedule=4000)],
        [job(name="fedobs-repro-1", complete=True, created=12, completed=12)],
    )
    assert findings == [], "判不出就不许报 stall（会变成天天假红），也不许报绿"
    assert [f["kind"] for f in facts] == ["manual-job", "success-time-untrusted"]
    assert rows[0]["verdict"] == "undecidable"


def test_manual_success_newer_than_tick_is_untrusted_even_without_the_job():
    """手工作业跑完把 Job 删了（只剩状态）：晚于最新 tick ⇒ 同样不可信。"""
    findings, facts, rows = run([cronjob(last_success=12, last_schedule=4000)], [])
    assert findings == []
    assert [f["kind"] for f in facts] == ["success-time-untrusted"]
    assert "晚于最新一个排期 tick" in facts[0]["detail"]
    assert rows[0]["verdict"] == "undecidable"


def test_scheduled_success_still_clears_the_stall():
    """绿路（成对）：排期轮次自己成功过 ⇒ 照旧判绿，且报的是排期成功龄期。"""
    findings, facts, rows = run(
        [cronjob(last_success=2000, last_schedule=60)],
        [job(name="wanxing-backup-29911240", complete=True, created=30, completed=25)],
    )
    assert findings == []
    assert [f["kind"] for f in facts] == []
    assert rows[0]["verdict"] == "ok"
    assert rows[0]["last_scheduled_success_age_minutes"] == 25


def test_status_fallback_still_used_when_it_makes_sense():
    """绿路（成对）：成功 Job 已被 `successfulJobsHistoryLimit` 清掉、状态早于最新 tick
    ⇒ 仍可兜底，别把合法形态判成「判不出」。"""
    findings, facts, rows = run([cronjob(last_success=1500, last_schedule=60)], [])
    assert findings == []
    assert [f["kind"] for f in facts] == []
    assert rows[0]["verdict"] == "ok"
    """历史失败但之后成功过：修好的故障不该长期占着红。"""
    findings, _, _ = run(
        [cronjob(last_success=60, last_schedule=60)],
        [job(name="wanxing-backup-28990000", failed=True, created=60 * 24),
         job(name="wanxing-backup-28991200", complete=True, created=60)],
    )
    assert findings == []


def test_never_scheduled_within_grace():
    findings, _, _ = run([cronjob(last_success=None, last_schedule=None, created=10)], [])
    assert findings == []


def test_no_recent_success_on_brand_new_job_is_grace():
    findings, _, _ = run([cronjob(last_success=None, last_schedule=None, created=30)], [])
    assert findings == []


# ── 消息与入口 ──────────────────────────────────────────────────────────────

def test_compose_empty_when_no_findings():
    assert B.compose([], namespace="fd-prod", judged=3, facts=0) == []


def test_compose_names_every_finding():
    findings = [
        {"kind": "job-failed", "namespace": "fd-prod", "name": "wanxing-backup", "detail": "x"},
        {"kind": "stall", "namespace": "fd-prod", "name": "fd-info-backup", "detail": "y"},
    ]
    (msg,) = B.compose(findings, namespace="fd-prod", judged=30, facts=4)
    assert "wanxing-backup" in msg["subject"] and "fd-info-backup" in msg["subject"]
    assert "job-failed" in msg["body"] and "stall" in msg["body"]
    assert "create job --from" in msg["body"]


def test_main_green_snapshot_exits_zero(tmp_path, capsys):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"cronjobs": [cronjob(last_success=60, last_schedule=30)],
                                "jobs": [job(complete=True, created=60)]}), encoding="utf-8")
    rc = B.main(["--from-json", str(snap), "--dry-run", "--now", NOW.isoformat()])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 0 and out["firing"] == 0 and out["judged"] == 1


def test_main_zero_cronjobs_is_blind_not_healthy(tmp_path, capsys):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"cronjobs": [], "jobs": []}), encoding="utf-8")
    rc = B.main(["--from-json", str(snap), "--dry-run", "--now", NOW.isoformat()])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 4, "0 个校验对象＝判不出，绝不许退 0"
    assert out["blind"] and out["kinds"] == ["blind"]


def test_main_findings_still_exit_zero_so_red_means_watcher_broke(tmp_path, capsys):
    """有告警但没带 --run ⇒ 退 0。

    Job 红只表示「这条线自己坏了」（死信/未配置/判不出）。若「发现坏作业」也让 Job 变红，
    它下一轮就会报「backup-job-alerts 最近一轮失败」，噪声盖住真信号。
    """
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({
        "cronjobs": [cronjob(last_success=60 * 24 * 3, last_schedule=60 * 24 * 3)],
        "jobs": [job(failed=True, created=60 * 24 * 3)],
    }), encoding="utf-8")
    rc = B.main(["--from-json", str(snap), "--dry-run", "--now", NOW.isoformat()])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 0 and out["firing"] >= 1


def test_main_run_without_smtp_is_not_configured(tmp_path, capsys, monkeypatch):
    for k in ("FD_ALERT_SMTP_HOST", "FD_ALERT_SMTP_FROM", "FD_ALERT_SMTP_TO"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FD_ALERT_DELIVERY_STRICT", "1")
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"cronjobs": [cronjob(last_success=None, last_schedule=None,
                                                     created=600)], "jobs": []}),
                    encoding="utf-8")
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"delivery": {"channel": "smtp", "to": ["a@b.c"]}}), encoding="utf-8")
    rc = B.main(["--from-json", str(snap), "--run", "--config", str(cfg),
                 "--now", NOW.isoformat()])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 3 and out["verdict"]["code"] == "not_configured"
