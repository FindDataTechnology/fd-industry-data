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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import ticket as ticket_mod
from . import triage
from .config import (
    VALID_DATA_PERIODS,
    HealthConfig,
    data_period_tolerance_hours,
    frequency_hours,
)

STATE_WORDS = ("queued", "working", "pr-open", "done", "manual")

# SUBMIT 回执语义词表（fix-health-ticket-landing-race）
# 去重回执 = 投递成功（agent 已受理，不重试）；拒收回执 = 投递失败（须重试）。
# 判定按「结论首段」而非全文——回执正文常引述历史错误，全文匹配会误判。
DEDUP_WORDS = ("已见过", "已入队", "重复提交", "已处理")
REFUSAL_WORDS = (
    "拒绝入队", "未入队", "不可读", "不认领", "拒绝并转人工",
    "拒绝", "未通过", "无法读取", "不存在",
)


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
        # 第 7 列为可选 watermark（数据水位/最新数据期）。telemetry_sql 默认只取
        # 6 列（中央库 crawl_runs 无数据期列）；运维扩展 SQL 后自动并入工单证据。
        cols += [""] * (7 - len(cols))
        source, status, started_at, rows, error, http_status = cols[:6]
        out.append({
            "source": source.strip(),
            "status": status.strip(),
            "started_at": started_at.strip(),
            "rows": int(rows) if rows.strip().lstrip("-").isdigit() else None,
            "error": error.strip(),
            "http_status": int(http_status) if http_status.strip().isdigit() else None,
            "watermark": cols[6].strip() or None,
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
            watermark=(str(r["watermark"]) if r.get("watermark") else None),
        )
        grouped.setdefault(str(r.get("source", "")), []).append(run)
    for runs in grouped.values():
        runs.sort(key=lambda x: x.started_at)
    return grouped


# ── 周期推断（manifest.data_period 优先，functions.frequency 兜底） ─────────

def manifest_data_period(repo: Path, source: str) -> tuple[str | None, bool]:
    """读 manifest 的 data_period 声明与 parked 注记，返回 (data_period, parked)。

    - manifest 缺失/不可解析 → (None, False)。
    - data_period 须为 day/month/year/irregular（validate_manifests.py 把关枚举）；
      非法值按未声明处理（回退 legacy 推断，不让脏值炸巡检）。
    - parked:true = 裁决 park 注记（add-fleet-lifecycle-adjudication D3），源被
      有意挂起，豁免超期判定。
    """
    manifest = repo / "spiders" / source / "manifest.yaml"
    if not manifest.exists():
        return None, False
    try:
        doc = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None, False
    period = doc.get("data_period")
    if isinstance(period, str) and period.strip().lower() in VALID_DATA_PERIODS:
        period = period.strip().lower()
    else:
        period = None
    return period, doc.get("parked") is True


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


# 豁免超期的期望周期（parked / data_period=irregular）：freshness/静默规则恒不
# 触发（age <= inf 恒真、age > max(3*inf,48h) 恒假），只剩连续失败判定——阈值
# 沿用 thresholds.consecutive_failures（add-fleet-lifecycle-adjudication 1.3）。
NO_OVERDUE_HOURS = float("inf")


def expected_period_hours(repo: Path, source: str, config: HealthConfig) -> float | None:
    """判「超期未产出」的期望周期（小时）；None = 周期未知（跳过判定）。

    优先级（add-fleet-lifecycle-adjudication 1.3）：

    1. manifest ``parked: true`` 或 ``data_period: irregular`` → inf：豁免超期，
       只判连续失败（连续失败阈值沿用现有逻辑）。
    2. config.expected_period_overrides（运维显式覆盖，小时）——先于 data_period，
       保持既有运维通道语义不变（向后兼容）。
    3. manifest ``data_period: day/month/year`` → 容忍窗 / freshness_multiplier。
       triage 的健康边界 = period * freshness_multiplier，把 period 取成
       容忍窗/multiplier 恰使边界等于容忍窗（day→3d、month→35d、year→370d）：
       超窗即非健康必出单（静默走规则 1b/2，带失败走规则 3~9）。
    4. legacy：manifest functions.frequency 推断（manifest_period_hours）。
    """
    period, parked = manifest_data_period(repo, source)
    if parked or period == "irregular":
        return NO_OVERDUE_HOURS
    if source in config.expected_period_overrides:
        return float(config.expected_period_overrides[source])
    tolerance = data_period_tolerance_hours(period)  # period 非 None 时必命中
    if tolerance is not None:
        return tolerance / config.thresholds.freshness_multiplier
    return manifest_period_hours(repo, source, config)


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
    # 在册（manifest 有 schedule 且未 parked）但遥测窗内零运行的源。
    # 这些源以前**完全不可见**（巡检面 = 遥测里出现过的源），年频源一过 30 天
    # 遥测窗就静默消失——add-flower-vertical 6.1 实测确认的缺口。
    absent: list[dict] = field(default_factory=list)   # {source, reason, data_period}


def _last_success(runs: list[triage.Run]) -> dict | None:
    for r in reversed(runs):
        if r.status == "success":
            return {"started_at": r.started_at, "rows": r.rows}
    return None


def _watermark(runs: list[triage.Run], last_success: dict | None) -> dict:
    """数据水位（最新数据期）证据。

    口径：优先取遥测里最新一个非空 ``watermark``（按时间升序取末个）。中央库
    ``crawl_runs`` 目前无数据期列，故常态是 unknown——**如实标注口径来源，不编造**；
    5.4 覆盖度基线的 staleness 口径（最新数据期）可在遥测 SQL 增加第 7 列后并入。
    """
    latest = None
    for run in runs:
        if run.watermark:
            latest = str(run.watermark)
    if latest:
        return {"latest": latest, "basis": "run telemetry watermark"}
    basis = (
        "unknown（遥测未提供数据期；最近成功时间见 evidence.last_success）"
        if last_success
        else "unknown（遥测未提供数据期；无成功运行记录）"
    )
    return {"latest": None, "basis": basis}


def build_evidence(runs: list[triage.Run], verdict: triage.Verdict) -> dict:
    failing = [r for r in runs if r.status != "success"]
    last_success = _last_success(runs)
    return {
        "window": {"from": runs[0].started_at if runs else "", "to": runs[-1].started_at if runs else ""},
        "failing_runs": [
            {"started_at": r.started_at, "status": r.status, "rows": r.rows,
             "error_summary": r.error[:300], "http_status": r.http_status}
            for r in failing[-10:]
        ],
        "last_success": last_success,
        "watermark": _watermark(runs, last_success),
        "notes": verdict.rule_trace,
    }


def min_rows_floor(repo: Path, source: str) -> int | None:
    """源 manifest 里**显式声明**的「一次成功运行至少应写入多少行」。

    返回 None = 未声明 = 该规则不生效（零回归：0 行成功对某些源是合法的）。
    声明字段：``min_rows_per_run: N``（整数，>0）。

    为什么要显式声明而不是代码里给个默认值：0 行的含义因源而异——节假日无新增、
    幂等 upsert 无改动都可能是正常的；只有源自己知道「我一次至少该写出几行」。
    实证：`flower-auction` 有 4 次 success / rows_written 合计 0，健康级此前无信号。
    """
    path = Path(repo) / "spiders" / source / "manifest.yaml"
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return None
    raw = doc.get("min_rows_per_run")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return None
    return int(raw)


def scheduled_sources(repo: Path) -> dict[str, dict]:
    """在册源：``spiders/*/manifest.yaml`` 里有 ``schedule`` 且未 ``parked``。

    返回 ``{source: {"data_period": ..., "schedule": ...}}``。这是「应该跑」的集合，
    与遥测里「真的跑过」的集合对账，才能发现「排期在册却整窗没跑」的源。
    """
    out: dict[str, dict] = {}
    for manifest in sorted((repo / "spiders").glob("*/manifest.yaml")):
        try:
            doc = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError):
            continue
        if not str(doc.get("schedule") or "").strip():
            continue
        if doc.get("parked") is True:
            continue
        period = doc.get("data_period")
        period = (str(period).strip().lower()
                  if isinstance(period, str) and str(period).strip().lower() in VALID_DATA_PERIODS
                  else None)
        out[manifest.parent.name] = {"data_period": period,
                                     "schedule": " ".join(str(doc["schedule"]).split())}
    return out


def reconcile_absent(runs_by_source: dict, repo: Path, config: HealthConfig) -> list[dict]:
    """在册但遥测窗内零运行的源，逐条给出**为什么**（不静默、不混为一谈）。

    两种原因处置完全不同：

    - ``period-longer-than-window``：期别容忍窗比遥测回看窗还长（典型 ``year``
      → 370 天 vs 30 天）。窗内**本就不该有运行**，据此出单是把口径不足伪造成
      故障；只报告，任何策略下都不出单。
    - ``no-run-in-window``：期别短于回看窗却没有一条运行 → 调度面故障
      （CronJob 没渲染 / 站点不认领该源）。按 ``absent_source_policy`` 决定是否出单。
    """
    absent: list[dict] = []
    for source, meta in sorted(scheduled_sources(repo).items()):
        if source in runs_by_source or source in config.excluded_sources:
            continue
        period = meta.get("data_period")
        tolerance = data_period_tolerance_hours(period)
        if tolerance is not None and tolerance > config.lookback_hours:
            reason = "period-longer-than-window"
        elif period is None:
            reason = "period-undeclared"
        else:
            reason = "no-run-in-window"
        absent.append({"source": source, "reason": reason, "data_period": period})
    return absent


def plan_tickets(runs_by_source: dict[str, list[triage.Run]], repo: Path, config: HealthConfig,
                 now: datetime) -> Plan:
    emit, deferred, healthy, unknown = [], [], 0, []
    absent = reconcile_absent(runs_by_source, repo, config)
    # 遥测里没有的源补成空列表再走同一条分诊链，避免「巡检面 = 遥测里出现过的源」
    # 这个隐性口径（year 频源一过回看窗就静默消失，add-flower-vertical 6.1）。
    ticketable = {a["source"] for a in absent
                  if a["reason"] == "no-run-in-window"
                  and config.absent_source_policy == "ticket"}
    merged = dict(runs_by_source)
    # 只有 ticket 策略下的「该跑却没跑」才进分诊链。report 策略只登记不判——
    # 否则空 runs 会落规则 2「证据不足」needs_human=True，把「分析口径」变成
    # 每天一张重出的人工单（正是 rule1b 注释里记的那 15 张的成因）。
    for entry in absent:
        if entry["source"] in ticketable:
            merged.setdefault(entry["source"], [])
    for source in sorted(merged):
        if source in config.excluded_sources:
            continue
        runs = merged[source]
        period = expected_period_hours(repo, source, config)
        if period is None:
            unknown.append(source)
            continue
        verdict = triage.classify(source, f"spiders/{source}/", period, runs, now, config.thresholds,
                                  scheduled_absent=source in ticketable,
                                  min_rows_per_run=min_rows_floor(repo, source))
        if verdict.category is None and not verdict.needs_human:
            healthy += 1
            continue
        emit.append(Emit(source=source, unit=f"spiders/{source}/", verdict=verdict, runs=runs))
    return Plan(emit=emit, deferred=deferred, healthy=healthy,
                skipped_unknown_period=unknown, absent=absent)


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
    from . import adjudication
    adjudication.apply_freeze(plan, repo)  # 升级源冻结新单派发（在途不受影响）
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
            kind="repair",  # 巡检器只产修复单（生成单由人工/工具创建，source-generation-flow）
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
            "skipped_unknown_period": plan.skipped_unknown_period,
            "absent_sources": plan.absent}


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


def _part_texts(result: dict) -> list[str]:
    """取 result.parts 里所有文本片段（按序）。"""
    parts = result.get("parts") if isinstance(result, dict) else None
    out: list[str] = []
    for part in parts or []:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            out.append(part["text"])
    return out


def _is_thinking(text: str) -> bool:
    """是否为思考片段（非结论）。

    萬星 runner 会把模型的 `<thinking>` 块作为独立 part 前置（2026-10-11 实测：
    STATUS 回执 parts[0] = `<thinking>Let me look at /data...</thinking>`，
    结论在其后的 part）。只取 parts[0] 会拿到思考过程，状态词解析必然为空——
    这是 141/151 次 STATUS 回读拿不到 state 的根因。
    """
    raw = text.strip()
    if not raw:
        return True
    if raw.startswith("<thinking>") or raw.startswith("<think>"):
        return True
    # 整段被思考标签包裹（可能带前后空白或内层标签）
    return bool(re.match(r"^<(thinking|think)>[\s\S]*</\1>\s*$", raw))


def extract_message_text(text: str) -> str:
    """从 a2a JSON-RPC 回执中提取结论文本；非 JSON 原样返回。

    取**最后一个非思考**文本片段（而非 parts[0]）：结论在后、思考在前。
    """
    try:
        doc = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    if isinstance(doc, dict):
        if doc.get("error"):
            return json.dumps(doc["error"], ensure_ascii=False)
        result = doc.get("result")
        if isinstance(result, dict):
            parts = [p for p in _part_texts(result) if not _is_thinking(p)]
            if parts:
                return parts[-1]
            # 全是思考片段：至少回一段原文，别把整个 JSON 信封当结论
            allparts = _part_texts(result)
            if allparts:
                return allparts[-1]
    return text


def _conclusion_segment(message: str) -> str:
    """取回执的结论首段（第一个 。/；/;/\\n 之前），小写去空白。

    回执正文常引述历史错误（如「说明：某次 submit 曾回 Invalid Request」），
    全文匹配会把这类引述误判为失败——只取结论句。
    """
    seg = re.split(r"[。；;\n]", message.strip(), maxsplit=1)[0]
    return seg.lower().strip()


def _looks_failed(status: int, raw_text: str, message: str) -> bool:
    """判定 SUBMIT 回执是否失败：HTTP 非 2xx / JSON-RPC error 信封 / 结论首段的拒收语义。

    拒收语义（2026-10-11 实测烧单后补）：agent 读不到工单时回复「拒绝入队」
    「未入队」「不认领」等——**这类回执必须判失败**才会在下一轮重试。此前它们
    被判成功并记 `ok: true`，工单被永久跳过（inmetals/metal-com/flower-auction
    各有实例）。去重回执（已见过/已入队）仍判成功，不误伤既有幂等语义。
    """
    if status >= 400:
        return True
    try:
        doc = json.loads(raw_text)
        if isinstance(doc, dict) and doc.get("error"):
            return True
    except (json.JSONDecodeError, TypeError):
        pass
    head = _conclusion_segment(message)
    if any(k in head for k in DEDUP_WORDS):
        return False                      # 去重回执 = 已受理，非失败
    if any(k in head for k in REFUSAL_WORDS):
        return True
    return head.startswith(("invalid request", "unauthorized", "not allowed", "未通过"))


def _submit_recorded(doc: dict) -> bool:
    """是否已有成功的 SUBMIT 记录（失败回执允许重试）。

    存量的 `ok: true` 记录也会按现行拒收语义重判（fix-health-ticket-landing-race）：
    历史上有拒收回执被误记为成功，若只信 `ok` 字段，这些单永远不会被重投。
    """
    for e in doc.get("lifecycle") or []:
        if e.get("event") != "submitted":
            continue
        resp = str(e.get("response", ""))
        if e.get("ok") is False:
            continue
        if _looks_failed(200, resp, resp):
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
        m = re.search(r"\b(queued|working|pr-open|done|manual|merged)\b", text)
        state = m.group(1) if m else None
    if not pr_url:
        m = re.search(r"https?://\S+", text)
        pr_url = m.group(0) if m else ""
    return {"state": state, "note": note[:300], "pr_url": pr_url}


# agent 状态 → 我方终态映射：done=修复完成待人工；manual=转人工；merged=人工已合并（闭环结束→closed）
TERMINAL_MAP = {"done": "fixed-pending-human", "manual": "manual", "merged": "closed"}


# STATUS 回读超时（秒）：实测 agent 回合 20~150s+，曾见 180s 超时；120s 偏紧。
STATUS_READ_TIMEOUT = 300


def _status_idem_key(doc: dict, name: str) -> str:
    """STATUS 幂等键：按工单回读次数递增（每轮唯一，强制真回合取当前状态）。

    门面按 (caller, agent, key) 重放首答，窗口 24h。固定键有两层代价：
    ①一次坏答（空文本/思考答，解析不出 state）会被重放满窗口——2026-10-12
    实证三张 10-11 单被 09:31 的空答重放到次日窗口过期才收敛；
    ②好答重放让状态跃迁滞后（pr-open/done 要等窗口过期才可见）。
    STATUS 是读：每次都要当前状态，故键随生命周期递增（SUBMIT 侧同为递增）。
    lifecycle 只追加，故序号严格递增、永不碰撞。
    """
    prior = sum(
        1 for e in doc.get("lifecycle") or []
        if e.get("event") in ("status", "status-failed")
    )
    return f"status-{name}-r{prior + 1}"


# 单轮 STATUS 回读上限：唯一键让每次回读都走真回合（20~150s+），积压时必须
# 限流，否则顶穿工作流预算（2026-10-12 实证：3 新单 + 3 存量单跑满 19m54s）。
# 取最久未读的 N 张先读；其余下一轮继续（顺序由 _last_read_at 决定，不会饿死）。
STATUS_MAX_PER_RUN = 8


def _last_read_at(doc: dict) -> str:
    """该单最后一次 STATUS 回读时间（ISO）；从未读过返回空串（排最前）。"""
    for e in reversed(doc.get("lifecycle") or []):
        if e.get("event") in ("status", "status-failed"):
            return str(e.get("at") or "")
    return ""


def sync_status(outdir: Path, url: str, key: str, fetcher=None,
                max_reads: int = STATUS_MAX_PER_RUN) -> list[dict]:
    """回读未终态工单的 STATUS，映射进生命周期与终态枚举。

    单轮最多读 max_reads 张，优先最久未读的（含从未读过的）。
    """
    fetcher = fetcher or (
        lambda u, k, idem, body: _default_poster(u, k, idem, body, timeout=STATUS_READ_TIMEOUT)
    )
    results: list[dict] = []
    if not outdir.is_dir():
        return results
    pending: list[tuple[str, Path, dict]] = []
    for p in sorted(outdir.glob("*.yaml")):
        doc = ticket_mod.load_ticket(p)
        if doc.get("terminal") is not None:
            continue
        pending.append((_last_read_at(doc), p, doc))
    pending.sort(key=lambda t: (t[0], str(t[1])))  # 最久未读优先（空串=从未读）
    for _at, p, doc in pending[:max_reads]:
        try:
            status, text = fetcher(url, key, _status_idem_key(doc, p.name[:-5]),
                                   f"STATUS reports/health-tickets/{p.name}")
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