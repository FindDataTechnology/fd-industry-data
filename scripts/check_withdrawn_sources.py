#!/usr/bin/env python3
"""暂撤声明的三个源：核「窗口之后那次跑真的成了没有」。

为什么要单独有个步骤
--------------------
`boxofficemojo` / `cisa` / `gb-standards` 的 `min_rows_per_run` 已在 2026-11-10 暂撤，
恢复的前提是**在自己的排期上真跑成功过**（不是"日期到了"）。但巡检面看不见这件事：

`gb-standards` 的 manifest 写 `data_period: month`（容忍窗 840h = 35 天），实际却是每周跑。
一次周跑失败既不会出 rule1c 单（没有"行数不足的成功运行"可判），也不会报超期
（离 35 天还远）——巡检照样全绿。所以必须显式核 k8s Job，不能等巡检报。

判据（**取严的一侧**）
----------------------
窗口之后**新建的最新 Job** 必须 `Complete 1/1`。四种非通过态分开报，因为处置完全不同：

    pending     窗口还没到 —— 不能判，**不算红**（提前报红等于天天误报）
    no_new_job  窗口过了却没有新 Job —— 调度没触发（CronJob 被 suspend、控制器没扫到）
    failed      Job 在但 Failed —— 跑过没成（boxofficemojo 2026-11-10 就是这种）
    incomplete  Job 在但没到 1/1 也没 Failed —— 还在跑，或卡在拉镜像/调度的中间态

**不看 Pod**（它有 TTL，会被清掉——boxofficemojo 那个失败 Job 的日志就是这么丢的），
也**不看 `lastScheduleTime`**（它只证明"触发过"，不证明跑成）。

用法
----
    python3 scripts/check_withdrawn_sources.py                       # 读 live 集群
    python3 scripts/check_withdrawn_sources.py --json                # 机器可读
    python3 scripts/check_withdrawn_sources.py --from-json /tmp/jobs.json   # 离线/测试

退出码：0 = 全部 Complete 或仍在等窗口；1 = 至少一个源标红。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))

#: 恢复窗口 = 该源下一次排期。窗口之后新建的 Job 才算数——窗口之前那次跑
#: （哪怕成功）证明不了"上游恢复了"，它跑的还是撤声明之前的状态。
WINDOWS: dict[str, datetime] = {
    "gb-standards": datetime(2026, 11, 12, 2, 25, tzinfo=CST),    # 周四 02:25
    "cisa":         datetime(2026, 11, 15, 1, 29, tzinfo=CST),    # 5/15/25 01:29
    "boxofficemojo": datetime(2026, 11, 17, 3, 14, tzinfo=CST),   # 周二 03:14
}

RED = {"no_new_job", "failed", "incomplete"}


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def job_state(job: dict) -> str:
    """单个 Job 的三态：complete / failed / incomplete。"""
    st = job.get("status") or {}
    for c in st.get("conditions") or []:
        if c.get("type") == "Failed" and str(c.get("status")) == "True":
            return "failed"
    if int(st.get("succeeded") or 0) >= 1:
        return "complete"
    return "incomplete"


def jobs_from_kubectl(context: str, namespace: str) -> list[dict]:
    out = subprocess.run(
        ["kubectl", f"--context={context}", "get", "jobs", "-n", namespace, "-o", "json"],
        capture_output=True, text=True, timeout=120,
    )
    if out.returncode != 0:
        raise RuntimeError(f"kubectl 失败：{out.stderr.strip()[:300]}")
    return json.loads(out.stdout).get("items") or []


def classify(items: list[dict], windows: dict[str, datetime], now: datetime) -> list[dict]:
    """每个源给一条判定。窗口未到 → pending（不判红）。"""
    rows: list[dict] = []
    for source, window in sorted(windows.items()):
        jobs = []
        for it in items:
            md = it.get("metadata") or {}
            labels = md.get("labels") or {}
            if labels.get("fd-industry/source") != source:
                continue
            created = _ts(md.get("creationTimestamp"))
            if created is None:
                continue
            jobs.append({"name": md.get("name"), "created": created,
                         "state": job_state(it)})
        jobs.sort(key=lambda j: j["created"])
        latest = jobs[-1] if jobs else None
        in_window = [j for j in jobs if j["created"] >= window]

        if now < window:
            verdict, why = "pending", f"窗口 {window.astimezone(CST):%m-%d %H:%M} CST 未到"
        elif not in_window:
            verdict = "no_new_job"
            why = f"窗口后无新 Job（最新是 {latest['created'].astimezone(CST):%m-%d %H:%M} {latest['state']}）" \
                if latest else "该源一个 Job 都没有"
        else:
            verdict = in_window[-1]["state"]
            why = f"窗口后最新 Job {in_window[-1]['name']} 为 {verdict}"

        rows.append({
            "source": source,
            "window": window.astimezone(CST).isoformat(),
            "verdict": verdict,
            "why": why,
            "latest_job": None if latest is None else {
                "name": latest["name"],
                "created": latest["created"].astimezone(CST).isoformat(),
                "state": latest["state"],
            },
            "red": verdict in RED,
        })
    return rows


def render(rows: list[dict]) -> str:
    lines = [f"{'source':16} {'判定':12} {'窗口(CST)':16} 说明",
             "-" * 96]
    for r in rows:
        mark = "✗" if r["red"] else ("…" if r["verdict"] == "pending" else "✓")
        lines.append(f"{r['source']:16} {mark}{r['verdict']:11} "
                     f"{r['window'][5:16].replace('T', ' '):16} {r['why']}")
    n_red = sum(1 for r in rows if r["red"])
    n_pend = sum(1 for r in rows if r["verdict"] == "pending")
    lines += ["", f"红 {n_red} 个，待窗口 {n_pend} 个。" +
              ("窗口未到不能判，不算红。" if n_pend else "")]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-json", help="离线：kubectl get jobs -o json 的产物")
    ap.add_argument("--context", default="tencent")
    ap.add_argument("--namespace", default="fd-industry")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--now", help="覆盖当前时间（ISO8601，测试用）")
    args = ap.parse_args(argv)

    now = _ts(args.now) if args.now else datetime.now(timezone.utc)
    if args.from_json:
        items = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        items = items.get("items") if isinstance(items, dict) else items
    else:
        try:
            items = jobs_from_kubectl(args.context, args.namespace)
        except (RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            print(f"读 Job 失败：{exc}", file=sys.stderr)
            return 2

    rows = classify(items or [], WINDOWS, now)
    if args.json:
        print(json.dumps({"now": now.isoformat(), "rows": rows}, ensure_ascii=False, indent=2))
    else:
        print(render(rows))
    return 1 if any(r["red"] for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
