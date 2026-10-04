"""工单 schema：自愈闭环的持久化工单（YAML）。

字段形态（``new_ticket`` 产出的完整工单）::

    ticket_id: str            # 文件名去 .yaml，形如 20261003-<source>-<short_id>
    source: str               # 数据源 id（如 fred-data）
    unit: str                 # spider 单元路径（如 "spiders/fred-data/"）
    kind: str                 # "repair"（缺省）| "generate"（source-generation-flow）
    created_at: str           # UTC ISO8601
    category: str | None      # CATEGORIES 之一或 None（生成单必须为 None；证据不足转人工时允许）
    needs_human: bool         # 证据不足/无法自动判定时转人工
    suspected_anti_bot: bool  # 仅 category == "contract" 允许为 True
    suggested_action: str
    brief: dict | None        # 仅生成单：{source_urls: [...], expectations: str, cadence?: str, notes?: str}
    evidence:
      window: {from: ..., to: ...}
      failing_runs: [...]
      last_success: {...} | None
      notes: str
    verify:
      commands: [...]         # 验证链命令声明
    golden: [...]             # golden 路径
    lifecycle: [{at, event, ...}]
    terminal: str | None      # TERMINALS 之一或 None

校验规则（``validate_ticket``）：
- 必填：ticket_id / source / unit / created_at / evidence / verify / lifecycle；
- unit 必须形如 ``spiders/<slug>/``，slug 匹配 ``^[a-z0-9][a-z0-9-]*$``
  （生成单允许指向尚不存在的 slug；是否存在由验证链判定）；
- ``kind`` 缺省视为 ``repair``，必须属于 KINDS；
- repair：needs_human=False 时 category 必填（不猜类别也不许空分类）；
  needs_human=True 时 category 允许 None（证据不足转人工）；
- generate：category 必须为空（生成不属故障分诊五类）；brief 必填且含
  非空 source_urls 列表与 expectations 字符串；
- suspected_anti_bot 仅当 category == "contract" 才可为 True；
- terminal 为 None 或 TERMINALS 之一。

生命周期只追加不篡改：``append_event`` / ``set_terminal`` 仅向 lifecycle 末尾
追加事件（含 ``at`` = UTC ISO8601），既有事件原样保留。
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

CATEGORIES = ("network", "structure", "contract", "source-dead", "fallback")
KINDS = ("repair", "generate")
TERMINALS = (
    "queued",
    "fixed-pending-human",
    "sample-update-pending-human",
    "manual",
    "retire-suggested",
    # 已关闭（闭环结束）：仅由人工路径（合并/裁决）置入；自动路径不得自行置入
    "closed",
)

_REQUIRED = ("ticket_id", "source", "unit", "created_at", "evidence", "verify", "lifecycle")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_UNIT_RE = re.compile(r"^spiders/([^/]+)/$")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_id(source: str, unit: str, created_at: str) -> str:
    digest = hashlib.sha1(f"{source}|{unit}|{created_at}".encode("utf-8")).hexdigest()
    return digest[:8]


def ticket_filename(day: str, source: str, short_id: str) -> str:
    """工单文件名：``<day>-<source>-<short_id>.yaml``（day=YYYYMMDD）。"""
    return f"{day}-{source}-{short_id}.yaml"


def _normalize_evidence(evidence: dict) -> dict:
    src = evidence or {}
    window = src.get("window")
    if isinstance(window, dict):
        window = {"from": window.get("from"), "to": window.get("to")}
    else:
        window = {"from": None, "to": None}
    failing_runs = src.get("failing_runs") or []
    if not isinstance(failing_runs, list):
        failing_runs = [failing_runs]
    return {
        "window": window,
        "failing_runs": list(failing_runs),
        "last_success": src.get("last_success"),
        "notes": src.get("notes", "") or "",
    }


def new_ticket(
    source: str,
    unit: str,
    *,
    evidence: dict,
    verify_commands: list[str],
    kind: str = "repair",
    category: str | None = None,
    needs_human: bool = False,
    suspected_anti_bot: bool = False,
    suggested_action: str = "",
    brief: dict | None = None,
    golden_paths: list[str] | None = None,
    created_at: str | None = None,
) -> dict:
    """构造完整工单 dict；created_at 缺省为当前 UTC ISO8601。

    ticket_id 按 ``ticket_filename(day(source, created_at), source, short_id)``
    的文件名去 ``.yaml`` 生成，保证与落盘文件名一致。
    """
    created = created_at or _utc_now_iso()
    day = str(created)[:10].replace("-", "")
    filename = ticket_filename(day, source, _short_id(source, unit, created))
    ticket_id = filename[: -len(".yaml")]
    doc = {
        "ticket_id": ticket_id,
        "source": source,
        "unit": unit,
        "kind": kind,
        "created_at": created,
        "category": category,
        "needs_human": bool(needs_human),
        "suspected_anti_bot": bool(suspected_anti_bot),
        "suggested_action": suggested_action,
        "evidence": _normalize_evidence(evidence),
        "verify": {"commands": list(verify_commands or [])},
        "golden": list(golden_paths or []),
        "lifecycle": [{"at": created, "event": "created"}],
        "terminal": None,
    }
    if brief is not None:
        doc["brief"] = brief
    return doc


def validate_ticket(doc: dict) -> list[str]:
    """校验工单，返回错误信息列表；空列表 = 合法。"""
    if not isinstance(doc, dict):
        return ["工单必须是 dict"]
    errors: list[str] = []

    for field in _REQUIRED:
        if field not in doc:
            errors.append(f"缺少必填字段 {field}")
        elif doc[field] is None or doc[field] == "":
            errors.append(f"缺少必填字段 {field}（不能为空）")

    for field in ("ticket_id", "source", "unit", "created_at"):
        value = doc.get(field)
        if value is not None and not isinstance(value, str):
            errors.append(f"{field} 必须是字符串")

    unit = doc.get("unit")
    if isinstance(unit, str):
        m = _UNIT_RE.match(unit)
        if not m:
            errors.append(f"unit 必须形如 spiders/<slug>/，实际: {unit!r}")
        elif not _SLUG_RE.match(m.group(1)):
            errors.append(f"unit slug 非法: {m.group(1)!r}（须匹配 ^[a-z0-9][a-z0-9-]*$）")

    kind = doc.get("kind", "repair")
    if kind not in KINDS:
        errors.append(f"kind 非法: {kind!r}，必须是 {KINDS} 之一")

    evidence = doc.get("evidence")
    if evidence is not None and not isinstance(evidence, dict):
        errors.append("evidence 必须是 dict")

    verify = doc.get("verify")
    if verify is not None:
        if not isinstance(verify, dict):
            errors.append("verify 必须是 dict")
        elif not isinstance(verify.get("commands"), list):
            errors.append("verify.commands 必须是列表")

    lifecycle = doc.get("lifecycle")
    if lifecycle is not None:
        if not isinstance(lifecycle, list):
            errors.append("lifecycle 必须是列表")
        else:
            for index, event in enumerate(lifecycle):
                if not isinstance(event, dict) or not event.get("event"):
                    errors.append(f"lifecycle[{index}] 必须包含 event 字段")

    needs_human = doc.get("needs_human", False)
    category = doc.get("category")
    if not isinstance(needs_human, bool):
        errors.append("needs_human 必须是 bool")
    if category is not None and category not in CATEGORIES:
        errors.append(
            f"category 非法: {category!r}，必须是 {CATEGORIES} 之一或 None"
        )

    if kind == "generate":
        if category is not None:
            errors.append("kind=generate 时 category 必须为空（生成不属故障分诊五类）")
        brief = doc.get("brief")
        if not isinstance(brief, dict):
            errors.append("kind=generate 时 brief 必填且为 dict")
        else:
            urls = brief.get("source_urls")
            if not (isinstance(urls, list) and urls
                    and all(isinstance(u, str) and u.strip() for u in urls)):
                errors.append("brief.source_urls 必填且为非空字符串列表")
            expectations = brief.get("expectations")
            if not (isinstance(expectations, str) and expectations.strip()):
                errors.append("brief.expectations 必填（期望产出描述）")
    else:
        if needs_human is False and category is None:
            errors.append("needs_human=False 时 category 不能为空（不得空分类/猜测分类）")

    suspected = doc.get("suspected_anti_bot", False)
    if not isinstance(suspected, bool):
        errors.append("suspected_anti_bot 必须是 bool")
    elif suspected and category != "contract":
        errors.append(
            "suspected_anti_bot=True 仅允许 category=='contract'（反爬签名属契约层）"
        )

    terminal = doc.get("terminal")
    if terminal is not None and terminal not in TERMINALS:
        errors.append(f"terminal 非法: {terminal!r}，必须是 {TERMINALS} 之一或 None")

    action = doc.get("suggested_action")
    if action is not None and not isinstance(action, str):
        errors.append("suggested_action 必须是字符串")

    golden = doc.get("golden")
    if golden is not None and not isinstance(golden, list):
        errors.append("golden 必须是列表")

    return errors


def load_ticket(path) -> dict:
    """读取 YAML 工单。"""
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def dump_ticket(doc: dict) -> str:
    """序列化为 YAML 文本（中文不转义，字段顺序保持）。"""
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


def append_event(doc: dict, event: str, **fields) -> None:
    """向 lifecycle 末尾追加事件（含 at=UTC ISO8601）；既有事件不动。"""
    events = doc.setdefault("lifecycle", [])
    if not isinstance(events, list):
        raise TypeError("lifecycle 必须是列表")
    payload = {"at": _utc_now_iso(), "event": event}
    payload.update(fields)
    events.append(payload)


def set_terminal(doc: dict, terminal: str, note: str = "") -> None:
    """设置工单终态并追加生命周期事件；terminal 必须在 TERMINALS 内。"""
    if terminal not in TERMINALS:
        raise ValueError(
            f"terminal 非法: {terminal!r}，必须是 {TERMINALS} 之一"
        )
    previous = doc.get("terminal")
    doc["terminal"] = terminal
    append_event(doc, "terminal", terminal=terminal, previous=previous, note=note)