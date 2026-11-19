#!/usr/bin/env python3
"""定时作业失败告警：**作业红了自己要有人知道**（2026-11-15 立）。

**要回答的问题**：`fd-prod` 里那 30 多个 CronJob，今天有没有哪一个是「跑了但失败了」
或者「压根没在跑」？

为什么必须单独立这条线：`sink-freshness`（落点新鲜度）与 `offsite-age-check`（异地归档
年龄）用的都是 **36h 阈值**——那对「归档停了一天」是合适的，但对「昨晚这一轮备份失败了」
是**瞎的**：第二天早上最新归档才 24h 旧，两条闸都报绿。而失败本身只在 Job 对象上留一行
`Failed` condition（`failedJobsHistoryLimit` 保对象、不保 Pod，日志早被清掉），
唯一会读它的是 `scripts/check_cronjob_health.py`——**那支要人想起来才跑**。
「判据只能人跑」在本仓已经栽过两次（node-health-alerts 两轮 DeadlineExceeded、
GHA 每日线三晚被 45 分墙杀），所以这条线把它搬进集群、并接上已有 SMTP 出口。

**它抓六种形态**（前四种是「作业红了」，后两种是「判不出」）：

  · `job-failed`       —— 最新一轮**排期**创建的 Job 带 `Failed` condition（backoff/deadline/准入）；
  · `stall`            —— `lastSuccessfulTime` 超过「2 个周期 + 松弛」，控制器没有成功跑过它；
  · `never-scheduled`  —— 非 suspend、create 超过宽限期但 `lastScheduleTime` 为空；
  · `stuck-active`     —— Job 起跑超过 24h 仍既无 Complete 也无 Failed（多半漏了 activeDeadlineSeconds）；
  · `schedule-unparseable` —— 排期表达式解析不出 ⇒ 这个作业**判不出**，必须点名（不是"通过"）；
  · `blind`            —— 读不到集群 API / 说是 0 个 CronJob ⇒ 整轮判不出，退 4。

**三条判据纪律（决定它会不会变成常年红的闸）**：

  1. **只认「排期轮次」**：CronJob 产出的 Job 名字恒为 `<cronjob>-<epoch分钟>`（实测 211 个
     排期轮次全符合），而 `kubectl create job --from=cronjob` 造的手工作业名字不一样
     ——2026-11-14 就是手工作业（另一会话为复现问题建的，按设计超 deadline 失败）被
     报成「最近一轮失败」，而真正排期的三轮全部 Complete。手工作业只打印事实、不计命中。
  2. **`suspend: true` 只报事实不计数**：合法闲置（`erp-daily-billing` 在等客户 WMS 源）。
  3. **阈值按各自的排期算**，不写一个全局数：`*/15` 的告警线用 36h 会永远绿，日频用它
     又太紧；这里按「2×最大间隔+60min」，周频/月频自动放宽。

**它不是 `check_cronjob_health.py` 的替代品**：那支扫六个集群、判「历史里有没有过失败」；
这支只扫本命名空间、判「**现在**这一轮是不是坏着」，并且带投递。分工写在
`fd-infra-deploy/AGENTS.md` 的对应段落里。

**故意只扫一个命名空间**：in-cluster 的 ServiceAccount 只该有 `fd-prod` 的 `get/list`
（`cronjobs`/`jobs`），多集群 admin kubeconfig 不进集群——那是把手工体检换成一处凭据面。

用法：
    python3 scripts/backup_job_alerts.py --dry-run                      # 只判定不发（打印 JSON）
    python3 scripts/backup_job_alerts.py --run                          # 判定 + 写日志 + 发邮件
    python3 scripts/backup_job_alerts.py --from-json /tmp/snap.json --dry-run   # 离线复核
    python3 scripts/backup_job_alerts.py --run --assume-failed fd-prod/wanxing-backup
        自证能红：把指定 CronJob 当作「最新一轮失败」走真实判据（不必真造一个坏作业）。

退出码：0=判定完成（有无告警都是 0，见下）；1=该发没发出去（死信，strict）；
        2=有送达也有失败；3=投递未配置；4=判不出（读不到 API / 0 个 CronJob）。

**为什么「有告警」不退 1**：这条线自己也在 `fd-prod` 里、也被自己判。若「发现 1 个坏作业」
就让自己的 Job 变红，那么只要有任何备份坏着，下一轮它就会报「backup-job-alerts 最近一轮
失败」——噪声盖住真信号（「看门人自己死了」才该红）。所以：**Job 红 ⇔ 这条线坏了**，
告警条数只写在日志的 `firing=N` 里，恢复判据看 `firing=0`（与另外几条告警线同一口径）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 只 import 镜像 `fd-industry-runner:sha-11ad0c3` **确有的符号**（见
# tests/test_alert_script_image_contract.py 的 IMAGE_EXPORTS 实测快照）。
# 2026-11-14 有人给三条告警脚本加了镜像里没有的 `state_health`，本地全绿、线上三条线
# 全部 ImportError、一封告警都没发出去——这条纪律不是洁癖。
from fd_industry_data.alerts_core import dispatch  # noqa: E402
from fd_industry_data.alerts_core.delivery import (  # noqa: E402
    DeliveryNotConfigured,
    DeliveryReceipt,
    judge,
    resolve_target,
    strict_enabled,
)
# dispatch_any 住在 notify（按声明通道分派到各发送实现）。add-mattermost-
# infrastructure 组 4：此前这里固定调 smtp_from_env，配置里写 channel: webhook
# 会被静默忽略、照样发邮件并判绿。注意本文件**仍 import dispatch**：自证块用它。
from fd_industry_data.alerts_core.notify import dispatch_any  # noqa: E402

EXIT_OK = 0
EXIT_FIRING = 1
EXIT_NOT_CONFIGURED = 3
EXIT_BLIND = 4

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")

#: CronJob 产出的 Job 名字：`<cronjob>-<epoch分钟>`。实测 211/211 排期轮次符合，
#: 19/19 手工作业不符合——所以「名字不像」是识别手工作业的**硬判据**，不是启发式。
SCHEDULED_JOB_RE = re.compile(r"^(?P<cronjob>.+)-(?P<epoch>\d{5,})$")

#: 「最新一轮的 Job 对象不在」时，`lastSuccessfulTime` 落在 tick 之后多久还**可以**算作那一轮
#: 自己的收尾（与 tick 的最长间隔取小）。超出即「说明不了这一轮」——2026-11-16 实历：
#: `sink-freshness` 的 tick 11-14T21:10Z、`lastSuccessfulTime` 是 11-15T15:42Z 的手工成功，
#: 相隔 18.6h。窗口只用于「别把正常收尾误读成手工」，不是阈值判据。
TICK_COMPLETION_PLAUSIBLE_MINUTES = 120.0

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DOWS = {d: i for i, d in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"], 0)}

MACROS = {
    "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0", "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}


# ─────────────────────────── 排期解析（纯函数，可单测） ───────────────────────────

def _num(token: str, names: dict[str, int]) -> int:
    t = token.strip().lower()
    if t in names:
        return names[t]
    if not t.isdigit():
        raise ValueError(f"不是数字也不是已知名字：{token!r}")
    return int(t)


def _expand(field: str, lo: int, hi: int, names: dict[str, int] | None = None) -> set[int]:
    """把一个 cron 字段展开成取值集合。支持 `*` `a-b` `a-b/n` `*/n` `a/n` `a,b`。"""
    names = names or {}
    out: set[int] = set()
    for part in str(field).split(","):
        part = part.strip()
        if not part:
            raise ValueError("空字段片段")
        step = 1
        if "/" in part:
            part, raw_step = part.split("/", 1)
            step = int(raw_step)
            if step <= 0:
                raise ValueError(f"步长为 0：{field!r}")
        if part in ("*", "?"):
            a, b = lo, hi
        elif "-" in part:
            ra, rb = part.split("-", 1)
            a, b = _num(ra, names), _num(rb, names)
        else:
            a = _num(part, names)
            b = hi if step != 1 else a
        if a < lo or b > hi:
            # 越界必须报错，不能"解析成功但永远不触发"：61 分钟的排期会算出一个
            # 看起来正常的日间隔，而它一次都不会跑——那是「判不出」伪装成「健康」。
            raise ValueError(f"取值越界（{lo}..{hi}）：{part!r}")
        if a > b:
            raise ValueError(f"区间倒置：{part!r}")
        for v in range(a, b + 1, step):
            out.add(v)
    return out


def parse_cron(spec: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    """返回 (months, doms, dows, hours, minutes)。0/7 都表示周日。"""
    s = str(spec).strip()
    if s in MACROS:
        s = MACROS[s]
    fields = s.split()
    if len(fields) != 5:
        raise ValueError(f"不是 5 段：{spec!r}")
    mi, ho, dom, mo, dow = fields
    dows = {0 if v == 7 else v for v in _expand(dow, 0, 7, DOWS)}
    return (
        _expand(mo, 1, 12, MONTHS),
        _expand(dom, 1, 31),
        dows,
        _expand(ho, 0, 23),
        _expand(mi, 0, 59),
    )


def firing_offsets(spec: str, start_day: datetime, days: int) -> list[int]:
    """从 `start_day` 起 `days` 天内所有触发时刻（相对 start_day 00:00 的分钟偏移）。

    按「天 → 时 → 分」展开，不做逐分钟扫描：月频表达式要跑 800 天，逐分钟会慢两个数量级。
    Vixie cron 的语义：**日与周同时受限时取并集**（OR），否则取交集。
    """
    months, doms, dows, hours, minutes = parse_cron(spec)
    dom_restricted = doms != set(range(1, 32))
    dow_restricted = dows != set(range(7))
    base = start_day.replace(hour=0, minute=0, second=0, microsecond=0)
    out: list[int] = []
    for d in range(days):
        day = base + timedelta(days=d)
        mo_ok = day.month in months
        if dom_restricted and dow_restricted:
            day_ok = mo_ok and ((day.day in doms) or ((day.weekday() + 1) % 7 in dows))
        else:
            day_ok = mo_ok and (day.day in doms) and ((day.weekday() + 1) % 7 in dows)
        if not day_ok:
            continue
        for h in sorted(hours):
            for m in sorted(minutes):
                out.append(d * 1440 + h * 60 + m)
    return out


def max_gap_minutes(spec: str, now: datetime, *, min_firings: int = 2) -> int:
    """相邻两次触发之间的最大间隔（分钟）。解析不出抛 ValueError。

    只取「相邻触发之间」的间隔，不含首尾不完整段——否则一个月频作业在 70 天窗口里
    会因为「开头 10 天没到点」得到 14400 分钟的假间隔，把阈值抬高一倍。
    """
    months, doms, _, _, _ = parse_cron(spec)
    # 窗口按表达式自身的稀疏度选：年/月频必定带受限的月或日字段。
    if len(months) < 12:
        days = 800
    elif doms != set(range(1, 32)):
        days = 70
    else:
        days = 12
    while True:
        offs = firing_offsets(spec, now, days)
        if len(offs) >= min_firings:
            return max(offs[i + 1] - offs[i] for i in range(len(offs) - 1))
        if days >= 800:
            raise ValueError(f"窗口 800 天内触发不足 {min_firings} 次：{spec!r}")
        days = min(days * 2, 800)


def schedule_gap_minutes(spec: str, now: datetime) -> int:
    """本脚本内部统一入口：解析失败一律抛 ValueError（调用方记 `schedule-unparseable`）。"""
    return max_gap_minutes(spec, now)


# ─────────────────────────── 判定（纯函数，可单测） ───────────────────────────

def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_minutes(value: str | None, now: datetime) -> float | None:
    t = _ts(value)
    return None if t is None else (now - t).total_seconds() / 60.0


def _condition(obj: dict, ctype: str) -> dict | None:
    for c in (obj.get("status") or {}).get("conditions") or []:
        if str(c.get("type")) == ctype and str(c.get("status")) == "True":
            return c
    return None


def _job_reason(job: dict) -> str:
    """失败原因取 condition 的 reason/message；都没有也要给一个，别留空。"""
    cond = _condition(job, "Failed") or {}
    reason = str(cond.get("reason") or "").strip()
    msg = str(cond.get("message") or "").strip().replace("\n", " ")[:160]
    if reason and msg:
        return f"{reason}：{msg}"
    return reason or msg or "Failed condition 但无 reason/message"


def _jobs_by_cronjob(jobs: list[dict]) -> dict[str, list[dict]]:
    """按 ownerReferences 把 Job 归到 CronJob 名下（`create job --from` 也会挂 owner）。"""
    out: dict[str, list[dict]] = {}
    for j in jobs:
        for ref in (j.get("metadata") or {}).get("ownerReferences") or []:
            if str(ref.get("kind")) == "CronJob" and ref.get("name"):
                out.setdefault(str(ref["name"]), []).append(j)
    return out


def _job_when(job: dict) -> datetime | None:
    """Job 的代表时刻：完成优先，跑着的用开始。"""
    st = job.get("status") or {}
    return _ts(st.get("completionTime")) or _ts(st.get("startTime"))


def _tick_object_absent(name: str, owned: list[dict], tick: datetime) -> bool:
    """`<cronjob>-<tick 的 epoch 分钟>` 这个 Job 对象**不在**（容忍 ±1 分钟口径差）。"""
    minute = int(tick.timestamp() // 60)
    have = {str((j.get("metadata") or {}).get("name") or "") for j in owned}
    return not any(f"{name}-{minute + d}" in have for d in (-1, 0, 1))


def evaluate(
    cronjobs: list[dict],
    jobs: list[dict],
    *,
    now: datetime,
    thresholds: dict,
    assume_failed: tuple[str, ...] = (),
    assume_stale: tuple[str, ...] = (),
) -> tuple[list[dict], list[dict], list[dict]]:
    """返回 (findings, facts, rows)。

    findings：要进邮件的（firing）；facts：只打印不计数（suspend / 手工作业 / 有 owner 的裸 Pod 等）；
    rows：每个 CronJob 一行的判定明细（便于事后复盘「它到底看没看」）。
    """
    stall_periods = float(thresholds.get("stall_periods", 2))
    stall_slack = float(thresholds.get("stall_slack_minutes", 60))
    never_grace = float(thresholds.get("never_scheduled_grace_minutes", 60))
    stuck_hours = float(thresholds.get("stuck_active_hours", 24))

    by_cj = _jobs_by_cronjob(jobs)
    findings: list[dict] = []
    facts: list[dict] = []
    rows: list[dict] = []
    assumed_failed = {a.split("/", 1)[-1] for a in assume_failed}
    assumed_stale = {a.split("/", 1)[-1] for a in assume_stale}

    for cj in sorted(cronjobs, key=lambda c: str(c["metadata"]["name"])):
        name = str(cj["metadata"]["name"])
        ns = str(cj["metadata"].get("namespace") or "")
        spec = str((cj.get("spec") or {}).get("schedule") or "")
        suspended = bool((cj.get("spec") or {}).get("suspend"))
        status = cj.get("status") or {}
        row = {"namespace": ns, "name": name, "schedule": spec, "suspended": suspended,
               "verdict": "ok", "notes": []}

        if suspended:
            facts.append({"kind": "suspended", "namespace": ns, "name": name,
                          "detail": "suspend=true（合法闲置按事实报，不计命中）"})
            row["verdict"] = "suspended"
            rows.append(row)
            continue

        try:
            gap = schedule_gap_minutes(spec, now)
        except (ValueError, TypeError) as exc:
            findings.append({
                "kind": "schedule-unparseable", "namespace": ns, "name": name,
                "detail": f"排期 `{spec}` 解析不出（{exc}）⇒ 这个作业判不出，不是通过",
            })
            row["verdict"] = "unparseable"
            rows.append(row)
            continue

        deadline = gap * stall_periods + stall_slack
        last_ok_status = _ts(status.get("lastSuccessfulTime"))
        last_sched = _ts(status.get("lastScheduleTime"))
        created_age = _age_minutes((cj.get("metadata") or {}).get("creationTimestamp"), now)

        # **成功时刻只认排期轮次**（2026-11-15 补，与 `check_cronjob_health.py` 同口径）：
        # `status.lastSuccessfulTime` 会被 `kubectl create job --from=cronjob/X` 的**手工作业**
        # 推进（它带 ownerReference，控制器把它算成自己的成功），于是「手工跑一次」就能把
        # **停滞**判据洗绿 —— 那正是这条线存在时要防的形态（判据只能人跑＝没有判据）。
        sched_ok = manual_ok = None
        for j in by_cj.get(name, []):
            if _condition(j, "Complete") is None:
                continue
            when = (_ts((j.get("status") or {}).get("completionTime"))
                    or _ts((j.get("status") or {}).get("startTime")))
            if when is None:
                continue
            m = SCHEDULED_JOB_RE.match(str((j["metadata"] or {}).get("name") or ""))
            if m and m.group("cronjob") == name:
                if sched_ok is None or when > sched_ok:
                    sched_ok = when
            elif manual_ok is None or when > manual_ok:
                manual_ok = when
        last_ok = sched_ok
        untrusted = None
        # `sched_ok` 缺失时允许状态兜底（`successfulJobsHistoryLimit` 会清掉成功 Job），
        # 但**只有它说得通时**才算：与手工作业的完成时刻逐秒相等，或晚于最新一个 tick
        # （排期轮次都在 tick 开始，比它还晚的「成功」只可能是手工件）。
        if last_ok is None and last_ok_status is not None:
            if manual_ok is not None and abs((last_ok_status - manual_ok).total_seconds()) <= 60:
                untrusted = f"来自手工/复现作业的成功（{manual_ok.isoformat()}），与它逐秒相等"
            elif last_sched is not None and last_ok_status > last_sched:
                # **`lastSuccessfulTime > lastScheduleTime` 本身不是手工的证据**（2026-11-16 订正，
                # 与 `check_cronjob_health.py` 同口径）：前一个是那一轮 tick 的**开始**、后一个是
                # 那一轮 Job 的**完成**，晚于它是常态（`pvc-snap-cheap4` 实测 tick 18:47:00 /
                # 成功 18:50:56）。只有落在「那一轮自己说得通的收尾窗口」之外，才判「说明不了这一轮」。
                # 窗口 = `TICK_COMPLETION_PLAUSIBLE_MINUTES` 与排期间隔取小。
                window = timedelta(minutes=min(TICK_COMPLETION_PLAUSIBLE_MINUTES, max(gap, 1.0)))
                late = last_ok_status - last_sched
                if late <= window:
                    last_ok = last_ok_status
                else:
                    untrusted = (f"晚于最新一个排期 tick（{last_sched.isoformat()}）"
                                 f"{late.total_seconds() / 3600:.1f}h，"
                                 f"超出那一轮的收尾窗口 {window.total_seconds() / 60:.0f}min")
            if untrusted is None:
                last_ok = last_ok_status
        last_ok_age = None if last_ok is None else (now - last_ok).total_seconds() / 60.0
        last_sched_age = _age_minutes(status.get("lastScheduleTime"), now)
        row.update({"gap_minutes": gap, "deadline_minutes": deadline,
                    "last_success_age_minutes": None if last_ok_status is None
                    else round(_age_minutes(status.get("lastSuccessfulTime"), now) or 0.0),
                    "last_scheduled_success_age_minutes": None if last_ok_age is None else round(last_ok_age),
                    "last_schedule_age_minutes": None if last_sched_age is None else round(last_sched_age)})

        # ① 最新一轮**排期**作业失败
        # 判定要**两件同时**：名字形状像 `<cronjob>-<epoch分钟>`，**且**前缀就是本 CronJob。
        # 只判形状不够——手工作业可以叫 `sinkf-verify-11150048`（实测存在），末尾也是数字，
        # 只按正则就会把手工作业当排期轮次，正是 2026-11-14 `fedobs-repro-1` 那次的错法。
        scheduled = [j for j in by_cj.get(name, [])
                     if (lambda m: bool(m) and m.group("cronjob") == name)(
                         SCHEDULED_JOB_RE.match(str((j["metadata"] or {}).get("name") or "")))]
        manual = [j for j in by_cj.get(name, []) if j not in scheduled]
        for j in manual:
            jname = str((j["metadata"] or {}).get("name"))
            failed = _condition(j, "Failed") is not None
            facts.append({"kind": "manual-job", "namespace": ns, "name": name,
                          "detail": f"手工作业 {jname}{'（Failed）' if failed else ''}"
                                    "——不是排期轮次，只报事实不计命中"})
        failed_hit = None
        if scheduled:
            latest = max(scheduled, key=lambda j: _ts((j["metadata"] or {}).get("creationTimestamp"))
                         or datetime.min.replace(tzinfo=timezone.utc))
            latest_name = str((latest["metadata"] or {}).get("name"))
            row["latest_scheduled_job"] = latest_name
            if _condition(latest, "Failed") is not None:
                failed_hit = {"kind": "job-failed", "namespace": ns, "name": name,
                              "detail": f"最近一轮排期作业 {latest_name} 失败：{_job_reason(latest)}"}
            # ② Job 卡 Active
            for j in scheduled:
                jname = str((j["metadata"] or {}).get("name"))
                start_age = _age_minutes((j.get("status") or {}).get("startTime"), now)
                if (start_age is not None and start_age > stuck_hours * 60
                        and _condition(j, "Complete") is None
                        and _condition(j, "Failed") is None):
                    findings.append({
                        "kind": "stuck-active", "namespace": ns, "name": name,
                        "detail": f"作业 {jname} 已跑 {start_age / 60:.1f}h 仍无 Complete/Failed"
                                  "（漏了 activeDeadlineSeconds？）",
                    })
                    row["verdict"] = "stuck-active"

        if name in assumed_failed and failed_hit is None:
            failed_hit = {"kind": "job-failed", "namespace": ns, "name": name,
                          "detail": "（--assume-failed 自证注入）最近一轮排期作业失败：BackoffLimitExceeded"}
        if failed_hit:
            findings.append(failed_hit)
            row["verdict"] = "job-failed"

        # ③ 停滞（从没成功过 / 成功太久以前）与 ④ 从未调度
        # 这两条的区分只在「控制器有没有在跑它」：lastScheduleTime 为空＝没排过；
        # 有排期但从没成功＝跑了但每轮都失败（那是另一条故障，报 stall 而不是 never-scheduled）。
        if untrusted:
            # 判不出就说出声：这一档**不报 stall**（说不清是真的停了还是刚被手工件洗过），
            # 也不许读成「通过」。`job-failed` / `stuck-active` 不受影响，照常按排期轮次判。
            facts.append({"kind": "success-time-untrusted", "namespace": ns, "name": name,
                          "detail": f"lastSuccessfulTime（{last_ok_status.isoformat()}）{untrusted}"
                                    " —— 不拿它清红；停滞判据本轮**判不出**"})
            row["verdict"] = "undecidable" if row.get("verdict") in (None, "ok") else row["verdict"]

            # **但「判不出」不许只活在日志里（2026-11-16 补）**：`compose()` 在没有 finding 时
            # 根本不发信，所以上面那行 `·` 收信的人永远看不到——「判不出」在行级被读成了「没事」，
            # 与这条线自己的纪律（判不出绝不退 0）矛盾。
            #
            # 实历：`fd-prod/sink-freshness` 的 `failedJobsHistoryLimit=5` 被四个手工
            # `create job --from=cronjob` 的 Failed 作业占满（`sink-freshness-verify-qd3/qd4/qd5`、
            # `sink-verify-tcal`），11-14T21:10Z 那轮**排期失败的对象被挤出历史池** ⇒ 本线既看不到
            # `job-failed`、`stall` 又被 untrusted 挡住 ⇒ 整轮安静，而那一轮其实失败了。
            #
            # 所以能**证成**「这一轮是被挤掉的」时就升级成 finding，三个条件同时成立：
            #   ① 最新 tick 的对象不在（它只有一个载体：`<cronjob>-<tick 的 epoch 分钟>`）；
            #   ② 池里有**比该 tick 更新**的对象——否则是「被手工删净」（`pvc-snap-*` 那族演练后
            #      `owned=0`），那时闸对历史本就无话可说，报红只是假红；
            #   ③ 这一轮没被任何排期轮次成功覆盖（被覆盖的旧 tick 不该再提）。
            # 已因 `job-failed` 判过的作业不再叠一条：**一处问题只算一次命中**。
            owned = by_cj.get(name, [])
            newer = [j for j in owned
                     if _job_when(j) is not None and last_sched is not None
                     and _job_when(j) > last_sched]
            covered = (sched_ok is not None and last_sched is not None and sched_ok >= last_sched)
            if (last_sched is not None and _tick_object_absent(name, owned, last_sched)
                    and newer and not covered and row.get("verdict") == "undecidable"):
                tick_min = int(last_sched.timestamp() // 60)
                non_sched = [j for j in newer
                             if not (lambda m: bool(m) and m.group("cronjob") == name)(
                                 SCHEDULED_JOB_RE.match(str((j["metadata"] or {}).get("name") or "")))]
                findings.append({
                    "kind": "unverifiable", "namespace": ns, "name": name,
                    "detail": (f"最新一轮排期（tick {last_sched.isoformat()}，"
                               f"{(now - last_sched).total_seconds() / 3600:.1f}h 前）的 Job 对象不在了"
                               f"（应有 `{name}-{tick_min}`），之后池里还有 {len(newer)} 个更新的对象"
                               f"（非排期 {len(non_sched)} 个）——**这一轮的结果不可核**：既没有成功记录、"
                               "也没有失败证据，别把它读成通过。手工 `create job --from=cronjob` 的作业"
                               "会占 `failedJobsHistoryLimit` 槽位、把排期失败挤掉；"
                               "验证请手写去掉 `ownerReferences` 的独立 Job"),
                })
                row["verdict"] = "unverifiable"
        if last_ok_age is None:
            if last_sched_age is None:
                if created_age is not None and created_age > max(never_grace, gap):
                    findings.append({
                        "kind": "never-scheduled", "namespace": ns, "name": name,
                        "detail": f"建了 {created_age / 60:.1f}h 仍无 lastScheduleTime"
                                  f"（宽限 {max(never_grace, gap) / 60:.1f}h）——控制器没在跑它",
                    })
                    row["verdict"] = "never-scheduled"
            elif last_sched_age > deadline and not untrusted:
                findings.append({
                    "kind": "stall", "namespace": ns, "name": name,
                    "detail": f"调度过（{last_sched_age / 60:.1f}h 前）但从未成功"
                              f"（无任何排期轮次成功记录）；阈值 {deadline / 60:.1f}h",
                })
                row["verdict"] = "stall"
        elif last_ok_age > deadline:
            findings.append({
                "kind": "stall", "namespace": ns, "name": name,
                "detail": f"最后一次成功在 {last_ok_age / 60:.1f}h 前；"
                          f"阈值 {deadline / 60:.1f}h（2×周期 {gap}min + 松弛 {stall_slack:.0f}min）",
            })
            row["verdict"] = "stall"

        if name in assumed_stale and not any(
                f["name"] == name and f["kind"] == "stall" for f in findings):
            findings.append({"kind": "stall", "namespace": ns, "name": name,
                             "detail": "（--assume-stale 自证注入）最后一次成功远超阈值"})
            row["verdict"] = "stall"

        rows.append(row)

    return findings, facts, rows


# ─────────────────────────── 集群读取 ───────────────────────────

class Blind(Exception):
    """读不到集群状态。**判不出不许读成健康**。"""


def fetch_api(namespace: str, *, timeout: float = 20.0) -> tuple[list[dict], list[dict]]:
    token_path = SA_DIR / "token"
    if not token_path.exists():
        raise Blind(f"没有 ServiceAccount token（{token_path}）——不在集群里跑？用 --from-json")
    token = token_path.read_text().strip()
    ca = SA_DIR / "ca.crt"
    ctx = ssl.create_default_context(cafile=str(ca)) if ca.exists() else ssl.create_default_context()

    def get(path: str) -> dict:
        req = urllib.request.Request(
            f"https://kubernetes.default.svc{path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            raise Blind(f"读 {path} 失败：{exc}") from exc

    cj = get(f"/apis/batch/v1/namespaces/{namespace}/cronjobs")
    jb = get(f"/apis/batch/v1/namespaces/{namespace}/jobs")
    return list(cj.get("items") or []), list(jb.get("items") or [])


def load_snapshot(path: str) -> tuple[list[dict], list[dict]]:
    """离线复核：`kubectl get cronjobs,jobs -n fd-prod -o json` 的产物（或 {"cronjobs":…,"jobs":…}）。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "cronjobs" in raw:
        return list(raw["cronjobs"]), list(raw.get("jobs") or [])
    if isinstance(raw, dict) and raw.get("kind") == "List":
        items = list(raw.get("items") or [])
        return ([i for i in items if str(i.get("kind")) == "CronJob"],
                [i for i in items if str(i.get("kind")) == "Job"])
    raise Blind(f"{path} 看不出 cronjobs/jobs 两段结构")


# ─────────────────────────── 消息与投递 ───────────────────────────

def compose(findings: list[dict], *, namespace: str, judged: int, facts: int) -> list[dict]:
    if not findings:
        return []
    names = "、".join(sorted({str(f["name"]) for f in findings}))
    subject = f"[finddata] {namespace} 定时作业告警：{len(findings)} 项（{names}）"
    lines = [
        f"{namespace} 里有 {len(findings)} 项定时作业处于「坏了」的状态（本轮共判 {judged} 个作业；"
        f"另有 {facts} 条事实不计命中：suspend / 手工作业）。",
        "",
    ]
    for f in findings:
        lines.append(f"· [{f['kind']}] {f['namespace']}/{f['name']}")
        lines.append(f"    {f['detail']}")
    lines += [
        "",
        "怎么查：",
        "  kubectl -n <ns> get cronjob <name> -o yaml | grep -A3 lastSuccessfulTime",
        "  kubectl -n <ns> get jobs --sort-by=.metadata.creationTimestamp | grep <name>",
        "  kubectl -n <ns> logs job/<最新排期作业名>    # 若 Pod 还没被清；被清了只能看 Job condition",
        "复现一轮（不动排期）：",
        "  kubectl -n <ns> create job --from=cronjob/<name> manual-$(date +%s)",
        "",
        "阈值口径：2×该作业自己的排期间隔 + 60min（周频/月频自动放宽）；"
        "suspend 与手工作业永不计数。",
    ]
    return [{"rule_id": "backup-job-alerts", "subject": subject, "body": "\n".join(lines)}]


def deliver(messages: list[dict], config: dict) -> tuple[DeliveryReceipt, object]:
    receipt = DeliveryReceipt()
    try:
        target = resolve_target(config, os.environ)
    except (OSError, ValueError, DeliveryNotConfigured) as exc:
        verdict = judge(receipt, firing=len(messages), strict=strict_enabled(os.environ),
                        not_configured_reason=str(exc))
        return receipt, verdict
    # 按声明通道分派。声明 webhook 而未配接收端、或声明 relay 时，
    # dispatch_any 抛 DeliveryNotConfigured —— 与上面那条解析失败同路返判红，
    # 而不是回退到邮件再把结局记成成功。
    try:
        sent = dispatch_any(messages, target, os.environ)
    except DeliveryNotConfigured as exc:
        verdict = judge(receipt, firing=len(messages), strict=strict_enabled(os.environ),
                        not_configured_reason=str(exc))
        return receipt, verdict
    receipt = DeliveryReceipt(
        sent=int(sent.get("sent", 0)), failed=int(sent.get("failed", 0)),
        skipped=1 if sent.get("skipped") else 0,
        reasons=[str(f) for f in sent.get("failures") or []],
    )
    verdict = judge(receipt, firing=len(messages), strict=strict_enabled(os.environ))
    return receipt, verdict


# ─────────────────────────── 自证 ───────────────────────────
#
# 为什么要有：`unverifiable` 这条判据的**全部价值**在于「该红时会红」，而它在正常数据上
# 一律安静 —— 只跑生产数据永远证明不了它承重。这里用内存夹具把红路与三条绿路各钉一遍，
# 红证＝把 `evaluate()` 里的 EV 条件逐个短路，对应臂立刻转 ✗。
#
# 时间基准统一取 `NOW`，夹具里的小时数都是相对它算的，避免「恰好跨过阈值」的漂移。

#: 臂数**自核**：写完一个数字写在旁边迟早会漂（同族教训见巡检 ⑧/⑭）。
SELF_TEST_ARMS = 9

def _self_test() -> int:
    now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
    K = {"stall_periods": 2, "stall_slack_minutes": 60,
         "never_scheduled_grace_minutes": 60, "stuck_active_hours": 24}

    def cj(name, *, schedule="0 9 * * *", last_sched=None, last_ok=None):
        st = {}
        if last_sched is not None:
            st["lastScheduleTime"] = last_sched.isoformat()
        if last_ok is not None:
            st["lastSuccessfulTime"] = last_ok.isoformat()
        return {"metadata": {"name": name, "namespace": "ns",
                             "creationTimestamp": "2026-01-01T00:00:00Z"},
                "spec": {"schedule": schedule, "suspend": False}, "status": st}

    def job(cj_name, jname, *, when, condition=None):
        # 归组是**按 ownerReferences**（`_jobs_by_cronjob`），不是按名字前缀——
        # 夹具里漏了 owner 就等于「池里没有这个作业」，红路会假绿。
        return {"metadata": {"name": jname, "namespace": "ns",
                             "creationTimestamp": when.isoformat(),
                             "ownerReferences": [{"kind": "CronJob", "name": cj_name}]},
                "status": {"completionTime": when.isoformat(),
                           "startTime": when.isoformat(),
                           "conditions": [{"type": condition, "status": "True"}] if condition else []}}

    def tick_job(cj_name, tick, *, condition="Complete"):
        return job(cj_name, f"{cj_name}-{int(tick.timestamp() // 60)}",
                   when=tick + timedelta(minutes=1), condition=condition)

    def run(cronjobs, jobs):
        return evaluate(cronjobs, jobs, now=now, thresholds=K)

    def kinds(items):
        return sorted({f["kind"] for f in items})

    fails = 0
    arms = 0

    def arm(label, ok):
        nonlocal fails, arms
        arms += 1
        print(("  ✓ " if ok else "  ✗ ") + label)
        if not ok:
            fails += 1

    tick = now - timedelta(hours=24)               # 最新一轮排期（日频）
    late = now - timedelta(hours=5)                # 19h 后 ⇒ 超出 2h 收尾窗口
    soon = tick + timedelta(minutes=30)            # 那一轮自己的收尾
    manual = lambda name, when: job("sink", name, when=when, condition="Failed")

    # ① 红路：tick 对象不在 + 池里有更新的手工 Failed + 成功时刻说明不了这一轮
    findings, _, rows = run([cj("sink", last_sched=tick, last_ok=late)],
                            [manual("sink-verify-1", now - timedelta(hours=4)),
                             manual("sink-verify-2", now - timedelta(hours=3))])
    arm("红路：不可核 ⇒ 出 unverifiable 且 verdict=unverifiable",
        kinds(findings) == ["unverifiable"] and rows[0]["verdict"] == "unverifiable")
    arm("红路：详情报出应有的 tick 对象名",
        any(f"sink-{int(tick.timestamp() // 60)}" in f["detail"] for f in findings))

    # ② 绿路：对象被手工删净（owned 为空，`pvc-snap-*` 那族）——闸对历史无话可说
    findings, _, _ = run([cj("pvc-snap-cheap4", last_sched=tick, last_ok=late)], [])
    arm("绿路：owned=0（删净）⇒ 不判 unverifiable", "unverifiable" not in kinds(findings))

    # ③ 绿路：tick 对象在场（哪怕之后还有更新的手工件）⇒ 走 job-failed，不叠 unverifiable
    findings, _, _ = run([cj("sink", last_sched=tick, last_ok=late)],
                         [tick_job("sink", tick, condition="Failed"),
                          manual("sink-verify-1", now - timedelta(minutes=30))])
    arm("绿路：tick 对象在场 ⇒ job-failed 且不判 unverifiable",
        kinds(findings) == ["job-failed"])

    # ④ 绿路：被后一轮排期成功覆盖（tick 对象仍在，但已有更新的排期成功）
    nxt = now - timedelta(minutes=30)
    findings, _, _ = run([cj("sink", last_sched=tick, last_ok=late)],
                         [manual("sink-verify-1", now - timedelta(minutes=45)),
                          tick_job("sink", nxt)])
    arm("绿路：之后有排期成功 ⇒ 不判 unverifiable", "unverifiable" not in kinds(findings))

    # ⑤ 收尾窗口内：tick 之后 30min 的「成功」算那一轮自己的收尾（不是手工证据）
    findings, facts, _ = run([cj("sink", last_sched=tick, last_ok=soon)],
                             [manual("sink-verify-1", now - timedelta(minutes=20))])
    arm("收尾窗口内 ⇒ 不出 success-time-untrusted、不出 unverifiable",
        "success-time-untrusted" not in kinds(facts) and "unverifiable" not in kinds(findings))

    # ⑥ 收尾窗口外 ⇒ 判 untrusted（并因此不可核）
    findings, facts, _ = run([cj("sink", last_sched=tick, last_ok=late)],
                             [manual("sink-verify-1", now - timedelta(minutes=20))])
    arm("收尾窗口外 ⇒ 出 success-time-untrusted", "success-time-untrusted" in kinds(facts))

    # ⑦ 已因 job-failed 判过的作业不叠 unverifiable（一处问题只算一次命中）
    findings, _, _ = run([cj("sink", last_sched=tick, last_ok=late)],
                         [tick_job("sink", tick, condition="Failed")])
    arm("已判 job-failed ⇒ 不叠 unverifiable", kinds(findings) == ["job-failed"])

    # ⑧ 正常作业（tick 对象在场且 Complete、成功时刻=那一轮收尾）零 finding
    findings, _, rows = run([cj("fine", last_sched=tick, last_ok=soon)],
                            [tick_job("fine", tick)])
    arm("正常作业 ⇒ 零 finding、verdict=ok",
        findings == [] and rows[0]["verdict"] == "ok")

    if arms != SELF_TEST_ARMS:   # 加了臂没改常量（或反过来）时，先报这个
        print(f"  ✗ 臂数漂了：实跑 {arms} 臂、常量写 {SELF_TEST_ARMS}")
        fails += 1
    print(f"自证：{arms} 臂 / 失败 {fails}")
    return 0 if fails == 0 else 1


# ─────────────────────────── 入口 ───────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="定时作业失败告警（判不出退 4，绝不退 0）")
    ap.add_argument("--namespace", default="fd-prod", help="要判的命名空间（默认 fd-prod）")
    ap.add_argument("--config", default=str(ROOT / "config" / "backup-job-alerts.json"))
    ap.add_argument("--from-json", default=None, help="离线快照（cronjobs+jobs）")
    ap.add_argument("--run", action="store_true", help="发送告警（默认只判定）")
    ap.add_argument("--dry-run", action="store_true", help="显式只判定（与默认相同，写清楚用）")
    ap.add_argument("--assume-failed", action="append", default=[], help="把该 CronJob 当作失败（自证）")
    ap.add_argument("--assume-stale", action="append", default=[], help="把该 CronJob 当作停滞（自证）")
    ap.add_argument("--now", default=None, help="覆盖当前时刻（ISO，测试用）")
    ap.add_argument("--self-test", action="store_true",
                    help="离线自证判据能红（不碰集群、不发信）")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    now = _ts(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        print(json.dumps({"error": f"--now 解析不出：{args.now}"}, ensure_ascii=False))
        return EXIT_BLIND

    cfg: dict = {}
    try:
        cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(json.dumps({"warn": f"读不到配置 {args.config}：{exc}（投递会记 not_configured）"},
                         ensure_ascii=False), file=sys.stderr)
    thresholds = cfg.get("thresholds") or {}

    blind_reason = None
    try:
        cronjobs, jobs = (load_snapshot(args.from_json) if args.from_json
                          else fetch_api(args.namespace))
    except Blind as exc:
        cronjobs, jobs, blind_reason = [], [], str(exc)

    if blind_reason is None and not cronjobs:
        blind_reason = (f"{args.namespace} 里读到 0 个 CronJob —— 这不是「都健康」，"
                        "是 RBAC/命名空间读错了（0 个校验对象＝判不出）")

    findings, facts, rows = (evaluate(cronjobs, jobs, now=now, thresholds=thresholds,
                                      assume_failed=tuple(args.assume_failed),
                                      assume_stale=tuple(args.assume_stale))
                             if blind_reason is None else ([], [], []))
    if blind_reason is not None:
        findings = [{"kind": "blind", "namespace": args.namespace, "name": "(判不出)",
                     "detail": blind_reason}]

    messages = compose(findings, namespace=args.namespace, judged=len(cronjobs), facts=len(facts))
    receipt, verdict = DeliveryReceipt(), None
    if args.run:
        receipt, verdict = deliver(messages, cfg)

    for f in findings:
        print(f"✗ [{f['kind']}] {f['namespace']}/{f['name']}: {f['detail']}")
    for f in facts:
        print(f"· [{f['kind']}] {f['namespace']}/{f['name']}: {f['detail']}")
    for r in rows:
        print("  " + json.dumps(r, ensure_ascii=False))

    summary = {
        "namespace": args.namespace,
        "source": args.from_json or "in-cluster",
        "judged": len(cronjobs),
        "jobs_seen": len(jobs),
        "firing": len(findings),
        "facts": len(facts),
        "blind": blind_reason,
        "messages": len(messages),
        "delivery": receipt.as_dict() if args.run else None,
        "verdict": verdict.as_dict() if verdict is not None else None,
        "kinds": sorted({str(f["kind"]) for f in findings}),
    }
    print(json.dumps(summary, ensure_ascii=False))

    if verdict is not None:
        # 有告警但已送达 ⇒ judge 给 ok(0)：Job 红只表示「这条线坏了」。
        return verdict.exit_code
    if blind_reason is not None:
        return EXIT_BLIND
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
