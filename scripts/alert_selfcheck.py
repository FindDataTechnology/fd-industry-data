#!/usr/bin/env python3
"""告警线的自检（openspec: wire-alert-delivery 5.2 / 5.3）。

**要回答的问题不是"某条规则越阈了吗"，而是"这些告警线本身还活着吗"**：

1. **评估器沉默**（5.3）：某条线应当每天跑，但最近一次产物已经是 N 天前——
   它安静地不告警，与"没有越阈"在外部看起来完全一样。
2. **投递链凭据失效**（5.2）：工单链的 SUBMIT 连续鉴权失败（401），
   历史上真的发生过（2026-11-07 起 5 张单全 401），而当时除了
   `last-run.json` 里那几行，没有任何人被告知。

产物：一份 JSON 摘要 + （可选）一封聚合邮件。**只在"沉默/失效"集合发生变化时发信**，
避免每天重复打扰；集合清空时发一封恢复。

用法::

    python3 scripts/alert_selfcheck.py --dry-run      # 只算不发
    python3 scripts/alert_selfcheck.py                # 读 env 里的 SMTP 配置发送
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# 每条线的产物目录名 → 允许的最大沉默天数（日频=1；年频源走 publication 规则，不在此列）
LINES = {
    "agriculture": ("reports/agriculture-alerts", 2),
    "banking": ("reports/banking-alerts", 2),
    "flower": ("reports/flower-alerts", 2),
    "hotel": ("reports/hotel-alerts", 2),
    "restaurant": ("reports/restaurant-alerts", 2),
}

TICKETS_DIR = "reports/health-tickets"
#: `last-run.json` 已于 2026-11-10 移出 git（生成态，每次运行覆写，只会与巡检机器人
#: 自己的提交反复冲突）。**本检查不再依赖它**：SUBMIT 的 http_status 本来就逐条记在
#: 工单的 `lifecycle` 里，那才是原始数据；而工单 YAML 仍留在 git（`SUBMIT <repo> <path>`
#: 协议要用它）。
#:
#: 为什么不能只删掉这检查：它是唯一会发现"整批 401 → 工单链断了"的地方
#: （2026-11-07 那次就是靠它定位的）。
TICKETS_DIR_LEGACY_NOTE = "（last-run.json 已不再跟踪，见 .gitignore）"
#: 一轮 SUBMIT 的时间跨度上界（秒）。巡检每轮把未终态工单一次性投完，实测在 1 分钟内；
#: 取 30 分钟留出重试与限速的余量，避免把相邻两轮混成一批。
SUBMIT_BATCH_WINDOW_SECONDS = 1800
STATE_PATH = "reports/alert-selfcheck/state.json"

DATE_PREFIX = 10  # "YYYY-MM-DD"


_DATE_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _date_dirs(path: Path) -> list[date]:
    """该线产物里出现过的日期——**目录与文件都算**。

    两条线的落物形状不同：餐饮/酒店写日期目录（`<date>/threshold-*.json`），
    农业/花卉写扁平文件（`rules-<date>.json`）。只认目录会让"文件型"的线
    永远被判成"从未报告"——这是个会永久误报的坑（2026-11-08 实测）。
    """
    out: list[date] = []
    if not path.is_dir():
        return out
    for name in path.iterdir():
        for token in _DATE_IN_NAME.findall(name.name[:32]):
            try:
                out.append(date.fromisoformat(token))
            except ValueError:
                continue
            break
    return sorted(out)


def check_silence(repo: Path, today: date, lines: dict | None = None) -> list[dict]:
    """返回沉默的线：有的从未出过产物，有的最后一次已超过容忍窗。"""
    silent: list[dict] = []
    for name, (rel, max_days) in (lines or LINES).items():
        dirs = _date_dirs(repo / rel)
        if not dirs:
            silent.append({"line": name, "kind": "never_reported", "last": None,
                           "days": None, "max_days": max_days})
            continue
        last = dirs[-1]
        days = (today - last).days
        if days > max_days:
            silent.append({"line": name, "kind": "stale", "last": last.isoformat(),
                           "days": days, "max_days": max_days})
    return silent


def _submit_events(repo: Path) -> list[dict]:
    """从工单 YAML 的 lifecycle 里收集所有 SUBMIT 尝试：(at, http_status)。

    数据源选工单而不是巡检摘要：摘要（`last-run.json`）已移出 git，而工单里
    `submitted` / `submit-failed` 事件的 `http_status` 是同一事实的原始记录。
    """
    import yaml

    out: list[dict] = []
    base = repo / TICKETS_DIR
    if not base.is_dir():
        return out
    for p in sorted(base.glob("*.yaml")):
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError):
            continue
        for e in doc.get("lifecycle") or []:
            if e.get("event") not in ("submitted", "submit-failed"):
                continue
            at = _parse_iso(e.get("at"))
            if at is None:
                continue
            out.append({"at": at, "code": e.get("http_status")})
    out.sort(key=lambda x: x["at"])
    return out


def _parse_iso(value: object) -> datetime | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace(" ", "T"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def check_ticket_auth(repo: Path) -> dict:
    """工单链最近一轮的 SUBMIT 是否整批鉴权失败。

    判定对象是**最近一批**投递（时间上相邻的 SUBMIT 尝试），不是全部历史：
    历史上偶发的 401 不该让今天永远报警。
    """
    events = _submit_events(repo)
    if not events:
        return {"checked": True, "at": None, "total": 0, "auth_failed": 0,
                "broken": False,
                "note": "no submit events in tickets " + TICKETS_DIR_LEGACY_NOTE}
    latest = events[-1]["at"]
    batch = [e for e in events
             if (latest - e["at"]).total_seconds() <= SUBMIT_BATCH_WINDOW_SECONDS]
    codes = [e["code"] for e in batch]
    auth_failed = sum(1 for c in codes if c in (401, 403))
    return {
        "checked": True,
        "at": latest.isoformat(),
        "total": len(batch),
        "auth_failed": auth_failed,
        # 整批鉴权失败才算"链断了"；个别单失败属业务问题，不在此报
        "broken": auth_failed > 0 and auth_failed == len(batch),
    }


def evaluate(repo: Path, today: date) -> dict:
    silent = check_silence(repo, today)
    ticket = check_ticket_auth(repo)
    findings = [f"silent:{s['line']}" for s in silent]
    findings += [f"ticket_auth:{s}" for s in _known_ticket_issues(repo)]
    if ticket.get("broken") and "ticket_auth:route_moved" not in findings:
        # 未知的整批鉴权失败（不是已知那条走廊搬迁）仍按新问题报出来。
        findings.append("ticket_auth")
    return {"at": datetime.now(timezone.utc).isoformat(), "silent": silent,
            "ticket": ticket, "findings": sorted(findings)}


# ── 已知状态（不再每天当新问题喊）────────────────────────────────────────
#
# 工单链（health_inspect --submit-new → 萬星 spider-heal）在 2026-11-07 起整批 401。
# 实测根因不是凭据：a2a 路由已从平台摘挂搬到 wanxing 门面，而 spider-heal
# **不在门面的 18 个 public agent 里**（`GET /api/wangxing/v1/agents` 无任何
# `spider`/`heal` 条目），原 slug 打门面直接 404 AGENT_NOT_FOUND。
# 告警用途已由邮件通道完全覆盖，故把它记成「已知、不修复」，避免自检每天喊同一条。
KNOWN_TICKET_ISSUES = {
    "route_moved": (
        "工单链 a2a 路由已搬迁至 wanxing 门面且 spider-heal 未上服务面；"
        "告警用途已由邮件覆盖，代码保留不删（openspec wire-alert-delivery 5.1）"
    ),
}


def _known_ticket_issues(repo: Path) -> list[str]:
    """已归档的工单链问题：仍然检查，但只标记为已知状态。"""
    path = repo / "config" / "alert-selfcheck-known.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    issues = doc.get("ticket_auth") or []
    return [str(x) for x in issues if str(x) in KNOWN_TICKET_ISSUES]


def deliver(report: dict, *, state_path: Path, dry_run: bool,
            smtp_factory=None) -> dict:
    """集合变化才发：新增/变更发告警，清空发恢复。"""
    receipt: dict = {"sent": 0, "failed": 0, "skipped": 0, "reason": None}
    prev_doc = {}
    if state_path.exists():
        try:
            prev_doc = json.loads(state_path.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            prev_doc = {}
    prev = set(prev_doc.get("findings") or [])
    current = set(report["findings"])
    added = sorted(current - prev)
    cleared = sorted(prev - current)

    if not dry_run:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"at": report["at"], "findings": sorted(current)},
                                         ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")

    if not added and not cleared:
        receipt["reason"] = "无变化"
        return receipt
    if dry_run:
        receipt["skipped"] = 1
        receipt["reason"] = "dry-run（不发送）"
        return receipt

    from fd_industry_data.alerts_core import notify  # noqa: PLC0415
    from fd_industry_data.alerts_core.delivery import smtp_from_env  # noqa: PLC0415

    recipients = [x.strip() for x in (os.environ.get("FD_ALERT_SMTP_TO") or "").split(",")
                  if x.strip()]
    smtp = smtp_from_env(recipients=recipients)
    if smtp is None:
        receipt["skipped"] = 1
        receipt["reason"] = "no-smtp-config"
        return receipt

    subject_bits = []
    if added:
        subject_bits.append(f"新增 {len(added)}")
    if cleared:
        subject_bits.append(f"恢复 {len(cleared)}")
    subject = "[告警自检] " + "、".join(subject_bits)
    body_lines = ["告警线自检（评估器沉默 / 投递链鉴权）", ""]
    if added:
        body_lines.append("## 新增")
        for f in added:
            body_lines.append(f"- {f}")
        body_lines.append("")
    if cleared:
        body_lines.append("## 已恢复")
        for f in cleared:
            body_lines.append(f"- {f}")
        body_lines.append("")
    if report["silent"]:
        body_lines.append("## 沉默明细")
        for s in report["silent"]:
            body_lines.append(
                f"- {s['line']}：{s['kind']}"
                + (f"，最后 {s['last']}（{s['days']} 天前，容忍 {s['max_days']} 天）"
                   if s.get("last") else "，从未产出过报告"))
        body_lines.append("")
    if report["ticket"].get("broken"):
        t = report["ticket"]
        known = [f for f in report["findings"] if f.startswith("ticket_auth:")]
        body_lines.append("## 工单链")
        body_lines.append(f"- 整批 SUBMIT 鉴权失败（{t['auth_failed']}/{t['total']}，"
                          f"最近一轮 {t.get('at')}）——工单送不出去，等于没人收到")
        for f in known:
            reason = KNOWN_TICKET_ISSUES.get(f.split(":", 1)[1])
            if reason:
                body_lines.append(f"  - 已知状态（不视为新问题）：{reason}")
    try:
        notify.send_email(smtp, subject, "\n".join(body_lines), smtp_factory=smtp_factory)
        receipt["sent"] = 1
    except Exception as exc:  # noqa: BLE001 — 自检自身的投递失败要可见
        receipt["failed"] = 1
        receipt["reason"] = f"{type(exc).__name__}: {exc}"[:200]
    return receipt


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="告警线自检：评估器沉默 + 投递链鉴权")
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--today", default=None, help="基准日 YYYY-MM-DD（缺省=本机今天）")
    ap.add_argument("--state", default=None, help="状态文件覆盖（测试用隔离目录）")
    ap.add_argument("--dry-run", action="store_true", help="只算不发、不推进状态")
    ap.add_argument("--smtp-factory", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    repo = Path(args.repo)
    today = date.fromisoformat(args.today) if args.today else date.today()
    state_path = Path(args.state) if args.state else repo / STATE_PATH
    outdir = repo / "reports" / "alert-selfcheck"
    outdir.mkdir(parents=True, exist_ok=True)

    report = evaluate(repo, today)
    receipt = deliver(report, state_path=state_path, dry_run=args.dry_run)
    report["notify"] = receipt
    (outdir / "last-check.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({"findings": report["findings"], "silent": report["silent"],
                      "ticket": report["ticket"], "notify": receipt},
                     ensure_ascii=False, indent=2))
    if receipt.get("failed"):
        return 4
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
