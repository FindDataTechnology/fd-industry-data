"""裁决通道：同源 manual 终态工单达到阈值即升级人工裁决（fleet-lifecycle-adjudication）。

背景：巡检闭环里 ``manual`` 是合法终态（agent 判定需人工），但慢性病源
（flower-auction/inmetals/metal-com 实证）每周重复出 manual 工单永不收口。
本模块把「重复 manual」从静默积压变成显式升级：

- 统计同源 manual 终态工单数，第 2 张（``ESCALATION_THRESHOLD``）触发升级：
  创建卷宗 ``output/adjudication/cases/<src>.md``（全部工单史、最近一次成功
  运行时间、失败根因分类、repair/park/retire 三选项依据）；
- 升级状态记录 ``output/adjudication/queue.json``（机读队列）；
- 升级后该源**不再派发新工单**：``apply_freeze`` 在工单生成入口
  （inspector.write_plan）过滤 frozen 源的 emit 项并记入 deferred；
  在途工单（已落盘的非终态单）不受影响；
- 裁决（repair/park/retire）经 ``record_decision`` 落台账
  ``output/adjudication/ledger.json``（append-only entries）。

幂等：``scan_escalations`` 可重复执行——卷宗已存在则更新不重建；队列既有
条目不重复创建、``escalated_at`` 不漂移。``output/`` 不入 git，工单语料在
git 里，扫描每次可从工单全量确定性重derive；裁决的持久效果落在仓库本体
（park/retire 改 manifest、retire 归档移动），ledger 只在本工作区累积。

纯 stdlib + yaml；无网络、无随机。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import ticket as ticket_mod

# 升级阈值：同源 manual 终态工单数达到该值即升级（第 2 张触发）
ESCALATION_THRESHOLD = 2

# 仓库内落点（相对仓库根；output/ 在 .gitignore，属工作区派生物）
ADJUDICATION_DIR = "output/adjudication"
CASES_SUBDIR = "cases"
QUEUE_FILENAME = "queue.json"
LEDGER_FILENAME = "ledger.json"

# 队列条目状态
STATUS_ESCALATED = "escalated"    # 待裁决：冻结新工单派发
STATUS_REPAIRED = "repaired"      # 已裁决 repair：解冻，关闭队列项
STATUS_PARKED = "parked"          # 已裁决 park：摘 schedule，保留代码
STATUS_RETIRED = "retired"        # 已裁决 retire：摘 schedule，单元归档

# 冻结态：处于这些状态的源不派发新工单；仅 repair 解冻
FROZEN_STATUSES = (STATUS_ESCALATED, STATUS_PARKED, STATUS_RETIRED)

DECISIONS = ("repair", "park", "retire")
_DECISION_STATUS = {
    "repair": STATUS_REPAIRED,
    "park": STATUS_PARKED,
    "retire": STATUS_RETIRED,
}

FREEZE_DEFER_REASON = "adjudication freeze (manual escalation)"

ARCHIVE_ROOT = "archive"
QUEUE_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 路径 ──────────────────────────────────────────────────────────────────

def adjudication_dir(repo) -> Path:
    return Path(repo) / ADJUDICATION_DIR


def cases_dir(repo) -> Path:
    return adjudication_dir(repo) / CASES_SUBDIR


def case_path(repo, source: str) -> Path:
    return cases_dir(repo) / f"{source}.md"


def queue_path(repo) -> Path:
    return adjudication_dir(repo) / QUEUE_FILENAME


def ledger_path(repo) -> Path:
    return adjudication_dir(repo) / LEDGER_FILENAME


# ── 队列 / 台账读写 ────────────────────────────────────────────────────────

def load_queue(repo) -> dict:
    """读裁决队列；缺失时返回空队列（version/threshold 兜底）。"""
    p = queue_path(repo)
    if not p.is_file():
        return {"version": QUEUE_VERSION, "updated_at": None,
                "threshold": ESCALATION_THRESHOLD, "entries": []}
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc.setdefault("version", QUEUE_VERSION)
    doc.setdefault("entries", [])
    return doc


def save_queue(repo, queue: dict) -> Path:
    p = queue_path(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    return p


def load_ledger(repo) -> dict:
    p = ledger_path(repo)
    if not p.is_file():
        return {"version": QUEUE_VERSION, "entries": []}
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc.setdefault("entries", [])
    return doc


def append_ledger_entry(repo, entry: dict) -> dict:
    """台账只追加：既有 entries 原样保留，新条目append到末尾。"""
    ledger = load_ledger(repo)
    ledger["entries"].append(entry)
    p = ledger_path(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    return ledger


# ── 工单语料统计 ───────────────────────────────────────────────────────────

def _parse_ts(value) -> datetime | None:
    """宽容解析工单里的时间串（遥测串形如 '2026-10-02 07:05:10.901+00'）。"""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if text.endswith("+00"):  # psql timestamptz 短时区
        text = text + ":00"
    text = text.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _rule_trace(doc: dict) -> str:
    """根因线索：evidence.notes 的 ruleN:xxx 首词；无则空串。"""
    notes = str(((doc.get("evidence") or {}).get("notes")) or "")
    first = notes.split()[0] if notes.split() else ""
    return first if first.startswith("rule") else ""


def _category_label(doc: dict) -> str:
    if doc.get("category"):
        return str(doc["category"])
    if doc.get("needs_human"):
        return "needs_human(未分类)"
    return "unspecified"


def _last_success_of(doc: dict) -> str | None:
    ls = (doc.get("evidence") or {}).get("last_success")
    if isinstance(ls, dict) and ls.get("started_at"):
        return str(ls["started_at"])
    return None


def collect_source_tickets(tickets_dir, source: str | None = None) -> dict[str, list[dict]]:
    """扫描工单目录，按源归组（created_at 升序）。

    每张工单摘要字段：ticket_id / created_at / terminal / category /
    needs_human / suggested_action / rule / last_success_at。
    损坏文件跳过并计数（返回值不带计数，容错优先）。
    """
    tickets_dir = Path(tickets_dir)
    grouped: dict[str, list[dict]] = {}
    if not tickets_dir.is_dir():
        return grouped
    for path in sorted(tickets_dir.glob("*.yaml")):
        try:
            doc = ticket_mod.load_ticket(path)
        except Exception:  # noqa: BLE001 — 单文件损坏不炸全量扫描
            continue
        if not isinstance(doc, dict):
            continue
        src = str(doc.get("source", ""))
        if source is not None and src != source:
            continue
        grouped.setdefault(src, []).append({
            "ticket_id": str(doc.get("ticket_id", path.stem)),
            "created_at": str(doc.get("created_at", "")),
            "terminal": doc.get("terminal"),
            "category": _category_label(doc),
            "needs_human": bool(doc.get("needs_human")),
            "suggested_action": str(doc.get("suggested_action", "") or ""),
            "rule": _rule_trace(doc),
            "last_success_at": _last_success_of(doc),
        })
    for tickets in grouped.values():
        tickets.sort(key=lambda t: t["created_at"])
    return grouped


def manual_counts(tickets_dir) -> dict[str, int]:
    """同源 manual 终态工单计数。"""
    return {
        src: sum(1 for t in tickets if t["terminal"] == "manual")
        for src, tickets in collect_source_tickets(tickets_dir).items()
    }


# ── 卷宗 ──────────────────────────────────────────────────────────────────

def _latest_last_success(tickets: list[dict]) -> str | None:
    """全部工单里可得的最近一次成功运行时间（不可得返回 None → unknown）。"""
    best: tuple[datetime, str] | None = None
    for t in tickets:
        raw = t.get("last_success_at")
        parsed = _parse_ts(raw)
        if parsed is not None and (best is None or parsed > best[0]):
            best = (parsed, str(raw))
    return best[1] if best else None


def manifest_schedule(repo, source: str) -> str | None:
    """manifest.yaml 顶层 schedule（无 manifest / 无 schedule 返回 None）。"""
    m = Path(repo) / "spiders" / source / "manifest.yaml"
    if not m.is_file():
        return None
    try:
        doc = yaml.safe_load(m.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None
    sched = doc.get("schedule")
    return str(sched) if sched else None


def _options_rationale(facts: dict) -> str:
    """repair/park/retire 三选项依据摘要（结合该源事实）。"""
    sched = facts["schedule"]
    last_ok = facts["last_success_at"]
    top_rules = "、".join(f"{k} x{n}" for k, n in facts["rules"].most_common(3)) or "无 rule 线索"
    sched_text = f"manifest schedule={sched!r}" if sched else "manifest 无 schedule（不在滚动调度面）"
    ok_text = f"最近一次成功运行 {last_ok}" if last_ok else "工单里无成功运行记录（unknown）"
    return f"""### repair（解冻，恢复自愈派发，关闭队列项）

适合根因可修（network/source-revision/structure/contract）或调度面仍在、修复有抓手时。
本源事实：{sched_text}；{ok_text}；根因线索：{top_rules}。
repair 后该源立即解冻（``FROZEN_STATUSES`` 之外），新工单恢复派发；
建议同步走人工路径把存量 manual 工单收口为 closed，避免重复升级。

### park（保留代码，摘除调度，停止滚动出单）

适合反复转人工、根因不明（needs_human 居多）或上游间歇不可用、价值待评估的源。
执行后 manifest 摘除 schedule 并标记 ``parked: true``；单元目录原地保留，
随时可 repair 复产（重新排 schedule）。

### retire（退役，摘除调度并将单元目录归档）

适合长期无成功记录（source-dead 形态）或确认无业务价值的源。
执行后 manifest 摘除 schedule，并打印 ``git mv`` 命令清单把
``spiders/{facts['source']}/`` 归档到 ``{ARCHIVE_ROOT}/adjudication-<date>/``；
移动由主代理/人工统一执行，本通道不自动移动。"""


def render_case_markdown(facts: dict, status: str, now: str) -> str:
    """渲染卷宗 markdown（确定性：同输入同输出）。"""
    src = facts["source"]
    lines: list[str] = []
    lines.append(f"# 裁决卷宗：{src}")
    lines.append("")
    lines.append(f"- 队列状态: {status}")
    lines.append(f"- 升级规则: 同源 manual 终态工单 >= {facts['threshold']} 张"
                 f"（当前 manual {facts['manual_count']} 张 / 全部工单 {facts['total']} 张）")
    lines.append(f"- 生成/更新时间(UTC): {now}")
    if facts["last_success_at"]:
        lines.append(f"- 最近一次成功运行: {facts['last_success_at']}（来源: 工单 evidence.last_success）")
    else:
        lines.append("- 最近一次成功运行: unknown（工单语料无成功记录）")
    sched = facts["schedule"]
    lines.append(f"- 调度面: manifest schedule={'有 ' + repr(sched) if sched else '无（unknown/未排程）'}")
    if facts.get("parked"):
        lines.append("- manifest 已标记 parked: true")
    lines.append("")
    lines.append(f"## 工单史（全部 {facts['total']} 张）")
    lines.append("")
    if facts["tickets"]:
        lines.append("| 工单 | 创建(UTC) | 终态 | 分类 | 根因线索 |")
        lines.append("|---|---|---|---|---|")
        for t in facts["tickets"]:
            lines.append(
                f"| {t['ticket_id']} | {t['created_at']} | {t['terminal'] or '(在途)'} "
                f"| {t['category']} | {t['rule'] or '-'} |"
            )
    else:
        lines.append("（无工单）")
    lines.append("")
    lines.append("## 失败根因分类")
    lines.append("")
    if facts["rules"]:
        for rule, n in facts["rules"].most_common():
            lines.append(f"- {rule} x{n}")
    else:
        lines.append("- 无 rule 级线索（工单 evidence.notes 未带 ruleN 前缀）")
    if facts["categories"]:
        cats = "、".join(f"{k} x{n}" for k, n in facts["categories"].most_common())
        lines.append(f"- 分类分布: {cats}")
    lines.append("")
    lines.append("## 裁决选项依据")
    lines.append("")
    lines.append(_options_rationale(facts))
    lines.append("")
    return "\n".join(lines)


def build_case_facts(tickets_dir, repo, source: str,
                     threshold: int = ESCALATION_THRESHOLD) -> dict:
    """汇总卷宗事实：工单史、manual 数、最近成功、根因分布、调度面。"""
    grouped = collect_source_tickets(tickets_dir, source=source)
    tickets = grouped.get(source, [])
    from collections import Counter
    rules = Counter(t["rule"] for t in tickets if t["rule"])
    categories = Counter(t["category"] for t in tickets)
    doc = yaml.safe_load(
        (Path(repo) / "spiders" / source / "manifest.yaml").read_text(encoding="utf-8")
    ) if (Path(repo) / "spiders" / source / "manifest.yaml").is_file() else {}
    return {
        "source": source,
        "threshold": threshold,
        "tickets": tickets,
        "total": len(tickets),
        "manual_count": sum(1 for t in tickets if t["terminal"] == "manual"),
        "last_success_at": _latest_last_success(tickets),
        "rules": rules,
        "categories": categories,
        "schedule": manifest_schedule(repo, source),
        "parked": bool((doc or {}).get("parked")),
    }


def write_case_file(repo, facts: dict, status: str, now: str) -> Path:
    p = case_path(repo, facts["source"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_case_markdown(facts, status, now), encoding="utf-8")
    return p


# ── 升级扫描（幂等） ───────────────────────────────────────────────────────

def _entry_facts_snapshot(facts: dict) -> dict:
    return {
        "manual_tickets": facts["manual_count"],
        "total_tickets": facts["total"],
        "manual_ticket_ids": [t["ticket_id"] for t in facts["tickets"]
                              if t["terminal"] == "manual"],
        "last_success_at": facts["last_success_at"],
        "root_causes": {k: v for k, v in facts["rules"].most_common()},
        "categories": {k: v for k, v in facts["categories"].most_common()},
        "schedule": facts["schedule"],
    }


def scan_escalations(tickets_dir, repo, now: str | None = None,
                     threshold: int = ESCALATION_THRESHOLD) -> dict:
    """扫描 manual 工单并维护升级队列与卷宗（幂等，可重复执行）。

    - 首次达到阈值的源：新建队列条目（status=escalated）+ 卷宗 → 计入
      ``escalated_now``；
    - 已在队列（含已裁决）的源：刷新条目计数与卷宗内容，``escalated_at`` /
      裁决字段不动 → 计入 ``already``（其中已裁决的单列 ``decided``）；
    - 未达阈值的源不进队列；已达阈值的已裁决源**不重复升级**（repair 后请
      走人工路径收口存量 manual 工单，否则下一工作区重新扫描会再升级）。
    """
    now = now or _utc_now_iso()
    repo = Path(repo)
    counts = manual_counts(tickets_dir)
    candidates = sorted(s for s, n in counts.items() if n >= threshold)

    queue = load_queue(repo)
    by_source = {e["source"]: e for e in queue["entries"]}
    escalated_now: list[str] = []
    already: list[str] = []
    decided: list[str] = []

    for src in candidates:
        facts = build_case_facts(tickets_dir, repo, src, threshold=threshold)
        entry = by_source.get(src)
        if entry is None:
            entry = {
                "source": src,
                "status": STATUS_ESCALATED,
                "escalated_at": now,
                "threshold": threshold,
                "case_file": f"{ADJUDICATION_DIR}/{CASES_SUBDIR}/{src}.md",
                "decided_at": None,
                "decision": None,
                "note": None,
            }
            queue["entries"].append(entry)
            by_source[src] = entry
            escalated_now.append(src)
        else:
            already.append(src)
            if entry.get("status") != STATUS_ESCALATED:
                decided.append(src)
        entry.update(_entry_facts_snapshot(facts))
        write_case_file(repo, facts, entry["status"], now)

    queue["entries"].sort(key=lambda e: e["source"])
    queue["updated_at"] = now
    queue["threshold"] = threshold
    save_queue(repo, queue)
    return {
        "escalated_now": escalated_now,
        "already": already,
        "decided": decided,
        "queue_size": len(queue["entries"]),
        "threshold": threshold,
        "queue_path": str(queue_path(repo)),
    }


# ── 冻结（工单生成入口过滤） ───────────────────────────────────────────────

def frozen_sources(repo) -> set[str]:
    """处于冻结态的源集合（escalated/parked/retired；repair 后解冻）。"""
    queue = load_queue(repo)
    return {
        e["source"] for e in queue["entries"]
        if e.get("status") in FROZEN_STATUSES
    }


def is_frozen(source: str, repo) -> bool:
    return source in frozen_sources(repo)


def apply_freeze(plan, repo) -> list[str]:
    """在工单生成入口过滤冻结源的 emit 项（原位修改 plan）。

    只挡**新工单**：frozen 源的 emit 项移入 ``plan.deferred``（reason=
    ``FREEZE_DEFER_REASON``）；在途工单（已落盘的非终态单）不受影响——
    本函数不触碰工单目录。``plan`` 鸭子类型：需 ``.emit``（元素含
    ``.source``）与 ``.deferred``（list）。返回被挡下的源名列表。
    """
    frozen = frozen_sources(repo)
    if not frozen:
        return []
    blocked: list[str] = []
    kept = []
    for item in plan.emit:
        source = getattr(item, "source", None)
        if source in frozen:
            blocked.append(source)
            # 顺延条目必须带判词（category/rule）——与 write_plan 里日限额/去重那几条
            # 同一口径。2026-11-11 实测发现这条路径漏了：观察脚本的指纹 ③
            # 「deferred 带 category/rule」因此长期是 ✗（1 条），把"新代码在跑"的
            # 自检信号搞成常红。verdict 用 getattr 取，保持本函数对 plan 的鸭子类型
            # 契约（单测会传只有 source 的假 item）。
            verdict = getattr(item, "verdict", None)
            rule_trace = (getattr(verdict, "rule_trace", "") or "") if verdict else ""
            plan.deferred.append({
                "source": source,
                "category": getattr(verdict, "category", None) if verdict else None,
                "rule": (rule_trace.split(":")[0] or None) if rule_trace else None,
                "reason": FREEZE_DEFER_REASON,
            })
        else:
            kept.append(item)
    plan.emit[:] = kept
    return blocked


# ── 裁决执行 ───────────────────────────────────────────────────────────────

def _write_manifest(doc: dict, mpath: Path) -> None:
    mpath.write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def park_manifest(repo, source: str) -> list[str]:
    """park 的 manifest 动作：schedule 置空（摘除键）+ parked: true。"""
    mpath = Path(repo) / "spiders" / source / "manifest.yaml"
    if not mpath.is_file():
        return [f"manifest not found, skip schedule strip: {mpath}"]
    doc = yaml.safe_load(mpath.read_text(encoding="utf-8")) or {}
    actions = []
    if doc.get("schedule"):
        doc.pop("schedule", None)
        actions.append(f"manifest {mpath}: schedule removed (置空)")
    doc["parked"] = True
    actions.append(f"manifest {mpath}: parked=true set")
    _write_manifest(doc, mpath)
    return actions


def retire_manifest(repo, source: str, date: str) -> tuple[list[str], list[str]]:
    """retire 的 manifest 动作：摘除 schedule；返回 (actions, git mv 命令清单)。

    只打印命令，不执行移动（移动由主代理/人工统一做）。
    """
    mpath = Path(repo) / "spiders" / source / "manifest.yaml"
    actions: list[str] = []
    if mpath.is_file():
        doc = yaml.safe_load(mpath.read_text(encoding="utf-8")) or {}
        if doc.get("schedule"):
            doc.pop("schedule", None)
            actions.append(f"manifest {mpath}: schedule removed (摘 schedule)")
            _write_manifest(doc, mpath)
    else:
        actions.append(f"manifest not found, skip schedule strip: {mpath}")
    target_dir = f"{ARCHIVE_ROOT}/adjudication-{date}"
    commands = [
        f"mkdir -p {target_dir}",
        f"git mv spiders/{source} {target_dir}/{source}",
    ]
    actions.append(f"git mv commands printed for spiders/{source}/ -> {target_dir}/ (not executed)")
    return actions, commands


def record_decision(repo, source: str, decision: str, note: str = "",
                    now: str | None = None,
                    actor: str = "scripts/adjudicate.py") -> dict:
    """执行裁决：更新队列条目、做仓库动作、append 台账。返回 {entry, actions, commands}。

    - repair：解冻（条目 status=repaired），不动 manifest；
    - park：manifest schedule 置空 + parked: true；
    - retire：manifest 摘 schedule，打印 git mv 命令清单（不执行移动）。
    """
    if decision not in DECISIONS:
        raise ValueError(f"decision 非法: {decision!r}，必须是 {DECISIONS} 之一")
    now = now or _utc_now_iso()
    repo = Path(repo)
    queue = load_queue(repo)
    entry = next((e for e in queue["entries"] if e.get("source") == source), None)
    if entry is None:
        known = ", ".join(sorted(e.get("source", "") for e in queue["entries"])) or "(empty)"
        raise ValueError(f"source 不在裁决队列: {source!r}（队列: {known}）")
    if entry.get("status") != STATUS_ESCALATED:
        raise ValueError(
            f"source 已裁决: {source!r} status={entry.get('status')!r}（队列条目不可重复裁决）"
        )

    actions: list[str] = []
    commands: list[str] = []
    if decision == "park":
        actions.extend(park_manifest(repo, source))
    elif decision == "retire":
        date = now[:10].replace("-", "")
        ret_actions, commands = retire_manifest(repo, source, date)
        actions.extend(ret_actions)

    entry["status"] = _DECISION_STATUS[decision]
    entry["decision"] = decision
    entry["decided_at"] = now
    entry["note"] = note or None
    unfreeze = "" if decision == "repair" else "（维持冻结：不在调度面即不出新工单）"
    if decision == "repair":
        actions.append(f"queue entry closed (status={STATUS_REPAIRED}); ticket dispatch unfrozen for {source}")
    else:
        actions.append(f"queue entry status={_DECISION_STATUS[decision]}{unfreeze}")
    queue["updated_at"] = now
    save_queue(repo, queue)

    ledger_entry = {
        "at": now,
        "source": source,
        "decision": decision,
        "note": note or None,
        "actions": actions,
        "actor": actor,
    }
    append_ledger_entry(repo, ledger_entry)
    return {"entry": entry, "actions": actions, "commands": commands}


# ── 摘要（GHA job summary / 日志） ─────────────────────────────────────────

def render_summary_markdown(repo) -> str:
    """裁决队列摘要（队列条目数 + 各状态源名），供 job summary 或日志。"""
    queue = load_queue(Path(repo))
    entries = queue["entries"]
    pending = [e["source"] for e in entries if e.get("status") == STATUS_ESCALATED]
    decided = [e for e in entries if e.get("status") != STATUS_ESCALATED]
    lines = [
        "## 裁决队列摘要（fleet-lifecycle-adjudication）",
        "",
        f"- 队列条目数: {len(entries)}（升级阈值: 同源 manual 终态工单 >= {queue.get('threshold', ESCALATION_THRESHOLD)}）",
    ]
    if pending:
        lines.append(f"- 待裁决（冻结派发中）: {len(pending)} — {', '.join(pending)}")
    else:
        lines.append("- 待裁决: 0")
    if decided:
        parts = [f"{e['source']}({e.get('decision')})" for e in decided]
        lines.append(f"- 已裁决: {len(decided)} — {', '.join(parts)}")
    lines.append("")
    return "\n".join(lines)
