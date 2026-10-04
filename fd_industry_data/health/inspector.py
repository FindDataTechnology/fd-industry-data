"""巡检器：遥测（快照文件 / 中央库 psql）→ 分诊 → 工单落仓 → SUBMIT/STATUS 接线。

设计要点：
- 只读遥测；出单是唯一副作用；SUBMIT/STATUS 仅在显式开启时发生。
- 纯 stdlib；中央库读取经 `psql` 子进程（DSN 走环境，避免引入驱动依赖）。
- poster/fetcher 可注入，全部网络调用可离线测试。
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import ticket as ticket_mod
from . import triage
from .config import HealthConfig, frequency_hours

STATE_WORDS = ("queued", "working", "pr-open", "done", "manual")


# ── 遥测载入 ──────────────────────────────────────────────────────────────

def load_runs_from_snapshot(path: str | Path) -> list[dict]:
    """快照 JSON：[{source,status,started_at,rows,error,http_status}]。"""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, list):
        raise ValueError(f"snapshot must be a JSON list: {path}")
    return doc


def load_runs_from_psql(config: HealthConfig, dsn: str, binary: str = "psql") -> list[dict]:
    """经 psql 子进程读中央库；TSV 输出解析（-A -F '\\t'）。"""
    if not dsn:
        raise ValueError("FD_CENTRAL_PG_DSN is required for --from-pg")
    proc = subprocess.run(
        [binary, dsn, "-At", "-F", "\t", "-c", config.telemetry_sql],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed ({proc.returncode}): {proc.stderr.strip()[:400]}")
    out: list[dict] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        cols += [""] * (6 - len(cols))
        source, status, started_at, rows, error, http_status = cols[:6]
        out.append({
            "source": source.strip(),
            "status": status.strip(),
            "started_at": started_at.strip(),
            "rows": int(rows) if rows.strip().lstrip("-").isdigit() else None,
            "error": error.strip(),
            "http_status": int(http_status) if http_status.strip().isdigit() else None,
        })
    return out


def group_runs(records: list[dict]) -> dict[str, list[triage.Run]]:
    grouped: dict[str, list[triage.Run]] = {}
    for r in records:
        run = triage.Run(
            started_at=str(r.get("started_at", "")),
            status=str(r.get("status", "failed")),
            rows=r.get("rows"),
            error=str(r.get("error") or ""),
            http_status=r.get("http_status"),
        )
        grouped.setdefault(str(r.get("source", "")), []).append(run)
    for runs in grouped.values():
        runs.sort(key=lambda x: x.started_at)
    return grouped


# ── 周期推断（manifest.frequency） ─────────────────────────────────────────

def manifest_period_hours(repo: Path, source: str, config: HealthConfig) -> float | None:
    if source in config.expected_period_overrides:
        return float(config.expected_period_overrides[source])
    manifest = repo / "spiders" / source / "manifest.yaml"
    if not manifest.exists():
        return None
    try:
        doc = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None
    hours = [
        h for h in (frequency_hours(f.get("frequency")) for f in doc.get("functions") or [])
        if h is not None
    ]
    return min(hours) if hours else None


# ── 出单规划 ──────────────────────────────────────────────────────────────

@dataclass
class Emit:
    source: str
    unit: str
    verdict: triage.Verdict
    runs: list[triage.Run]


@dataclass
class Plan:
    emit: list[Emit]
    deferred: list[dict]          # {source, category, reason}
    healthy: int
    skipped_unknown_period: list[str]


def _last_success(runs: list[triage.Run]) -> dict | None:
    for r in reversed(runs):
        if r.status == "success":
            return {"started_at": r.started_at, "rows": r.rows}
    return None


def build_evidence(runs: list[triage.Run], verdict: triage.Verdict) -> dict:
    failing = [r for r in runs if r.status != "success"]
    return {
        "window": {"from": runs[0].started_at if runs else "", "to": runs[-1].started_at if runs else ""},
        "failing_runs": [
            {"started_at": r.started_at, "status": r.status, "rows": r.rows,
             "error_summary": r.error[:300], "http_status": r.http_status}
            for r in failing[-10:]
        ],
        "last_success": _last_success(runs),
        "notes": verdict.rule_trace,
    }


def plan_tickets(runs_by_source: dict[str, list[triage.Run]], repo: Path, config: HealthConfig,
                 now: datetime) -> Plan:
    emit, deferred, healthy, unknown = [], [], 0, []
    for source in sorted(runs_by_source):
        if source in config.excluded_sources:
            continue
        runs = runs_by_source[source]
        period = manifest_period_hours(repo, source, config)
        if period is None:
            unknown.append(source)
            continue
        verdict = triage.classify(source, f"spiders/{source}/", period, runs, now, config.thresholds)
        if verdict.category is None and not verdict.needs_human:
            healthy += 1
            continue
        emit.append(Emit(source=source, unit=f"spiders/{source}/", verdict=verdict, runs=runs))
    return Plan(emit=emit, deferred=deferred, healthy=healthy, skipped_unknown_period=unknown)


def _golden_paths(repo: Path, unit: str) -> list[str]:
    gdir = repo / unit / "golden"
    return sorted(f"{unit}golden/{p.name}" for p in gdir.glob("*.json")) if gdir.is_dir() else []


def todays_emitted(outdir: Path, day: str) -> int:
    return len(list(outdir.glob(f"{day}-*.yaml"))) if outdir.is_dir() else 0


def nonterminal_sources(outdir: Path, within_days: int = 7) -> set[str]:
    """近 within_days 天内已存在未终态工单（terminal 为 None 或 queued）的源。"""
    found: set[str] = set()
    if not outdir.is_dir():
        return found
    cutoff = datetime.now(timezone.utc) - timedelta(days=within_days)
    for p in outdir.glob("*.yaml"):
        try:
            day = datetime.strptime(p.name[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
            doc = ticket_mod.load_ticket(p)
        except (ValueError, Exception):
            continue
        if day < cutoff:
            continue
        if doc.get("terminal") in (None, "queued"):
            found.add(str(doc.get("source", "")))
    return found


def write_plan(plan: Plan, repo: Path, outdir: Path, config: HealthConfig, now: datetime) -> dict:
    """落单：去重（非终态存量）+ 日限额。返回统计。"""
    outdir.mkdir(parents=True, exist_ok=True)
    day = now.strftime("%Y%m%d")
    already_today = todays_emitted(outdir, day)
    remaining = max(0, config.max_daily_tickets - already_today)
    existing = nonterminal_sources(outdir)
    written: list[str] = []
    for item in plan.emit:
        if item.source in existing:
            plan.deferred.append({"source": item.source, "reason": "nonterminal ticket exists"})
            continue
        if remaining <= 0:
            plan.deferred.append({"source": item.source, "reason": "daily cap reached"})
            continue
        doc = ticket_mod.new_ticket(
            source=item.source,
            unit=item.unit,
            evidence=build_evidence(item.runs, item.verdict),
            verify_commands=[],
            category=item.verdict.category,
            needs_human=item.verdict.needs_human,
            suspected_anti_bot=item.verdict.suspected_anti_bot,
            suggested_action=item.verdict.suggested_action,
            golden_paths=_golden_paths(repo, item.unit),
            created_at=now.isoformat(),
        )
        short_id = doc["ticket_id"].split("-")[-1]
        fname = ticket_mod.ticket_filename(day, item.source, short_id)
        doc["ticket_id"] = fname[:-5]
        doc["verify"]["commands"] = [f"python3 scripts/health_verify.py --ticket reports/health-tickets/{fname}"]
        (outdir / fname).write_text(ticket_mod.dump_ticket(doc), encoding="utf-8")
        written.append(fname)
        remaining -= 1
    return {"written": written, "deferred": plan.deferred, "healthy": plan.healthy,
            "skipped_unknown_period": plan.skipped_unknown_period}


# ── 接线：SUBMIT / STATUS（poster/fetcher 可注入） ─────────────────────────

def _default_poster(url: str, key: str, idem: str, body: str, timeout: int = 900) -> tuple[int, str]:
    """a2a 消息发送：JSON-RPC message/send 封装（文本指令在 parts[0].text）。

    首投可能触发整回合（实测 110s+），SUBMIT 超时放宽到 15 分钟；超时按未完成
    处理：下次运行重投，agent 侧 inbox 去重保证安全（只回执不动手）。
    """
    import uuid
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "kind": "message",
                "messageId": idem,
                "parts": [{"kind": "text", "text": body}],
            }
        },
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Idempotency-Key": idem,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(8000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:  # noqa: BLE001
        return e.code, e.read(8000).decode("utf-8", "replace")


def extract_message_text(text: str) -> str:
    """从 a2a JSON-RPC 回执中提取文本（result.parts[].text）；非 JSON 原样返回。"""
    try:
        doc = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    if isinstance(doc, dict):
        if doc.get("error"):
            return json.dumps(doc["error"], ensure_ascii=False)
        result = doc.get("result")
        if isinstance(result, dict):
            for part in result.get("parts") or []:
                if isinstance(part, dict) and "text" in part:
                    return str(part["text"])
    return text


def _looks_failed(status: int, raw_text: str, message: str) -> bool:
    """判定 SUBMIT 回执是否失败：HTTP 非 2xx / JSON-RPC error 信封 / 回执开头的明确拒绝。

    仅看回执开头（前 60 字符）与错误信封——回执正文里引述历史错误不应判失败。
    """
    if status >= 400:
        return True
    try:
        doc = json.loads(raw_text)
        if isinstance(doc, dict) and doc.get("error"):
            return True
    except (json.JSONDecodeError, TypeError):
        pass
    head = message.strip()[:60].lower()
    return head.startswith(("invalid request", "unauthorized", "not allowed", "拒绝", "未通过"))


def _submit_recorded(doc: dict) -> bool:
    """是否已有成功的 SUBMIT 记录（失败回执允许重试）。"""
    for e in doc.get("lifecycle") or []:
        if e.get("event") != "submitted":
            continue
        resp = str(e.get("response", "")).lower()
        if e.get("ok") is False or "invalid request" in resp or "error" in resp or "unauthorized" in resp:
            continue
        return True
    return False


def submit_new_tickets(outdir: Path, repo_slug: str, url: str, key: str, config: HealthConfig,
                       poster=None, dry_run: bool = False) -> list[dict]:
    """对尚无 submitted 事件的工单执行 SUBMIT；总闸关闭时不提交（纯巡检）。"""
    poster = poster or _default_poster
    results: list[dict] = []
    if not outdir.is_dir():
        return results
    for p in sorted(outdir.glob("*.yaml")):
        doc = ticket_mod.load_ticket(p)
        if doc.get("terminal") is not None:
            continue  # 已终态工单不重投
        if _submit_recorded(doc):
            continue
        rel = f"reports/health-tickets/{p.name}"
        entry = {"ticket": p.name, "submitted": False, "reason": ""}
        if not config.master_switch:
            entry["reason"] = "master switch off (inspect-only)"
            ticket_mod.append_event(doc, "submit-skipped", reason=entry["reason"])
        elif dry_run:
            entry["reason"] = "dry-run"
        else:
            # 门面语义：同 Idempotency-Key 重放首答（含错误首答）——曾因首投撞上 agent
            # 不可用而缓存 -32032、此后重投永远拿到旧错误。故键按次递增；工单级去重
            # 由 agent 侧 inbox（按路径）兜底，重投安全。
            prior = sum(
                1 for e in doc.get("lifecycle") or []
                if e.get("event") in ("submitted", "submit-failed")
            )
            idem = f"{p.name[:-5]}-try{prior + 1}"
            entry["attempt"] = prior + 1
            try:
                status, text = poster(url, key, idem, f"SUBMIT {repo_slug} {rel}")
                message = extract_message_text(text)
                failed = _looks_failed(status, text, message)
                entry.update({"submitted": not failed, "http_status": status, "response": message[:500]})
                ticket_mod.append_event(doc, "submitted", http_status=status, ok=not failed,
                                        response=message[:300])
            except Exception as e:  # noqa: BLE001 — 网络失败留痕不炸
                entry.update({"reason": f"submit failed: {type(e).__name__}: {e}"[:300]})
                ticket_mod.append_event(doc, "submit-failed", error=str(e)[:300])
        p.write_text(ticket_mod.dump_ticket(doc), encoding="utf-8")
        results.append(entry)
    return results


def parse_status(text: str) -> dict:
    """容错解析 STATUS 回执：优先 JSON，其次关键词扫描。"""
    state, note, pr_url = None, "", ""
    try:
        doc = json.loads(text)
        if isinstance(doc, dict):
            state = str(doc.get("state") or doc.get("status") or "") or None
            note = str(doc.get("note") or "")
            pr_url = str(doc.get("prUrl") or doc.get("pr_url") or doc.get("url") or "")
    except (json.JSONDecodeError, TypeError):
        pass
    if state is None:
        m = re.search(r"\b(queued|working|pr-open|done|manual)\b", text)
        state = m.group(1) if m else None
    if not pr_url:
        m = re.search(r"https?://\S+", text)
        pr_url = m.group(0) if m else ""
    return {"state": state, "note": note[:300], "pr_url": pr_url}


TERMINAL_MAP = {"done": "fixed-pending-human", "manual": "manual"}


def sync_status(outdir: Path, url: str, key: str, fetcher=None) -> list[dict]:
    """回读未终态工单的 STATUS，映射进生命周期与终态枚举。"""
    fetcher = fetcher or (lambda u, k, idem, body: _default_poster(u, k, idem, body, timeout=120))
    results: list[dict] = []
    if not outdir.is_dir():
        return results
    for p in sorted(outdir.glob("*.yaml")):
        doc = ticket_mod.load_ticket(p)
        if doc.get("terminal") is not None:
            continue
        try:
            status, text = fetcher(url, key, f"status-{p.name[:-5]}", f"STATUS reports/health-tickets/{p.name}")
        except Exception as e:  # noqa: BLE001 — 回读失败留痕不炸
            ticket_mod.append_event(doc, "status-failed", error=str(e)[:300])
            p.write_text(ticket_mod.dump_ticket(doc), encoding="utf-8")
            results.append({"ticket": p.name, "state": None, "error": str(e)[:200]})
            continue
        parsed = parse_status(extract_message_text(text))
        ticket_mod.append_event(doc, "status", http_status=status, state=parsed["state"],
                                note=parsed["note"], pr_url=parsed["pr_url"])
        if parsed["state"] in TERMINAL_MAP:
            ticket_mod.set_terminal(doc, TERMINAL_MAP[parsed["state"]], note=parsed["note"])
        p.write_text(ticket_mod.dump_ticket(doc), encoding="utf-8")
        results.append({"ticket": p.name, "http_status": status, **parsed})
    return results