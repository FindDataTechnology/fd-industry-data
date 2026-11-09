#!/usr/bin/env python3
"""餐饮垂直线波4 · 三级预警（阈值级 / 事件级 / escalate 与值守档案）。

任务出处：``openspec/changes/add-restaurant-vertical/tasks.md`` 5.2 / 5.3 / 5.4。
健康级（5.1）不在这里：它由既有巡检器 ``scripts/health_inspect.py`` 承担，本脚本
只负责「健康级工单之后」的两级与升级路径。

只读纪律
--------
* 数据库 **SELECT only**：``semantic_observations`` / ``concepts`` /
  ``crawl_runs`` / ``crawl_items`` 只读；不写 semantic_observations / concepts /
  registry_entries。连接以 ``set_session(readonly=True)`` 打开。
* 不插值、不补零：缺期进 ``gap`` 列表并归因，绝不生成没有上游发布的点。
* 驱动：``fd-industry-data/.venv/bin/python``（psycopg2）。``dashboards/data_plane.py``
  的 ``Plane.series`` 用 psycopg3，本机 venv 无该驱动，故这里按同一取数契约
  （``source_used`` 钉死 + ``concepts.code`` 连接 + ``ORDER BY date``）用 psycopg2
  实现等价查询，不引入第二套口径。

子命令
------
``--rules``      评估 ``config/restaurant-alerts.json`` 的阈值规则，把每条规则的
                 证据对象写到 ``reports/restaurant-alerts/<asof>/threshold-<rule>.json``。
``--events``     扫财报日历 / 盈警 / 抽检不合格事件，按磁盘台账去重（首次上报）。
``--duty-log``   生成/刷新值守档案 ``reports/restaurant-alerts/<asof>/duty-log.md``
                 （provenance + 缺测归因 + 审计回指）。
``--escalate``   把未终态健康工单推进到实际投递前的最后一步。复用
                 ``fd_industry_data.health.inspector.submit_new_tickets``（与
                 ``scripts/health_inspect.py --deliver --submit-new`` 同一实现），
                 不重写投递逻辑；缺 ``WANXING_SPIDER_HEAL_KEY`` 时如实记 blocked。

用法::

    .venv/bin/python scripts/restaurant_alerts.py --rules --asof 2026-11-08 \
        --replay-from 2019-01
    .venv/bin/python scripts/restaurant_alerts.py --events
    .venv/bin/python scripts/restaurant_alerts.py --duty-log --tickets-dir <dir>
    .venv/bin/python scripts/restaurant_alerts.py --escalate --tickets-dir <dir>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

DEFAULT_CONFIG = REPO / "config" / "restaurant-alerts.json"

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

VERDICTS = ("n/a", "fired", "quiet")

# 缺测归因三分类（tasks 5.4 要求）
GAP_KINDS = ("never_held", "gap_in_series", "not_applicable")


# ── 小工具 ────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> date:
    """本机本地日期（项目以 CST 值守；与任务书日期一致）。"""
    return datetime.now().astimezone().date()


def _norm_date(raw: str) -> str:
    """观测 date 文本 → 规则用的规范形（月度 YYYY-MM / 日度 YYYY-MM-DD）。"""
    text = str(raw).strip()
    if DAY_RE.match(text):
        return text[:10]
    if MONTH_RE.match(text):
        return text[:7]
    return text[:10] if len(text) >= 10 else text


def _as_date(text: str) -> date | None:
    text = _norm_date(text)
    try:
        if DAY_RE.match(text):
            return date.fromisoformat(text)
        if MONTH_RE.match(text):
            return date(int(text[:4]), int(text[5:7]), 1)
        if re.match(r"^\d{4}$", text):
            return date(int(text), 1, 1)
    except ValueError:
        return None
    return None


def _fmt(d: date, cadence: str) -> str:
    return d.strftime("%Y-%m") if cadence == "monthly" else d.strftime("%Y-%m-%d")


def _add_months(d: date, months: int) -> date:
    total = d.year * 12 + (d.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def _round(value: float, ndigits: int = 6) -> float:
    return round(float(value), ndigits)


def _sha256_short(path: Path, n: int = 16) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:n]
    except OSError:
        return ""


def _json_default(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(f"not serializable: {type(obj)!r}")


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ── 取数（只读；data_plane.Plane.series 的 psycopg2 等价实现） ─────────────

def _rule_label(cfg: dict) -> str:
    """邮件标题里的线名（餐饮 / 酒店共用本脚本，靠配置里的 label 区分）。"""
    return str(cfg.get("label") or cfg.get("duty_log_title") or "餐饮线")


def _notify_targets(cfg: dict) -> list[str]:
    env_to = [x.strip() for x in (os.environ.get("FD_ALERT_SMTP_TO") or "").split(",")
              if x.strip()]
    if env_to:
        return env_to
    delivery = cfg.get("delivery") or {}
    return [str(x).strip() for x in (delivery.get("to") or []) if str(x).strip()]


def collect_rule_states(runs: list[dict]) -> dict[str, dict]:
    """把 ``--rules`` 的结果压成「每个 (规则, 概念) 的三态」：fired / quiet / unknown。

    为什么要三态而不是"fired 与其余"：缺数（``n/a``：观测不足、真实缺测、结构性缺期）
    **不是恢复**。若把 n/a 当成"不再越阈"，一次源断更就会发出一封假恢复——
    这与 `alerts_core` 的第一条纪律「缺数 ≠ 恢复」直接冲突。
    所以 unknown 只表示"本轮判不了"，差分时既不发告警也不发恢复，且状态原样保留。
    """
    out: dict[str, dict] = {}
    rank = {"unknown": 0, "quiet": 1, "stale": 1, "fired": 2}
    for run in runs or []:
        for ev in run.get("evidence") or []:
            verdict = str(ev.get("verdict") or "n/a")
            state = {"fired": "fired", "quiet": "quiet"}.get(verdict, "unknown")
            key = f"{run['rule']}|{ev.get('concept')}"
            prev = out.get(key)
            # 「窗口内曾越阈」但**最新一期没有越阈** = 历史越阈：不进 fired。
            # 否则 96 个月的回放窗会让两年前的旧越阈永远处于告警态、永不恢复。
            if state == "fired" and ev.get("current_fired") is False:
                state = "stale"
            if prev is None or rank.get(state, 0) > rank.get(prev["state"], 0):
                out[key] = {
                    "key": key,
                    "rule": run["rule"],
                    "concept": ev.get("concept"),
                    "state": state,
                    "verdict": verdict,
                    "latest_period": ev.get("latest_period"),
                    "current_fired": ev.get("current_fired"),
                    "trigger_date": ev.get("trigger_date"),
                    "trigger_value": ev.get("trigger_value"),
                    "threshold": ev.get("threshold"),
                    "comparator": ev.get("comparator"),
                    "reason": ev.get("reason"),
                }
    return out


def collect_fired(runs: list[dict]) -> list[dict]:
    """只取当前 fired 的点（保留给调用方做摘要；投递请用 ``collect_rule_states``）。"""
    return [v for v in collect_rule_states(runs).values() if v["state"] == "fired"]


def deliver_transitions(states: dict[str, dict], cfg: dict, *, dry_run: bool, now,
                        state_path: Path, smtp_factory=None) -> dict:
    """只在**状态发生变化**时发信：进入 fired 发告警，回到 quiet 发恢复。

    为什么需要边界判定：本脚本没有花卉那样的冷却状态机，判定是"每次跑都重算"。
    直接每次发信 = 同一个越阈每天骚扰一遍。

    **缺数不算恢复**（与 `alerts_core` 第一条纪律一致）：``unknown``（n/a：观测不足 /
    真实缺测 / 结构性缺期）既不发告警也不发恢复，而且**状态原样保留**——
    否则一次源断更就会发出一封假恢复，把"看不到"说成"好了"。
    """
    receipt: dict = {"sent": 0, "failed": 0, "skipped": 0, "alerts": [], "recovers": [],
                     "unknown": [], "messages": []}
    prev_doc = _read_json(state_path) or {}
    # 兼容旧形状（只有 fired 字典）与现形状（rules 字典，含三态）。
    if isinstance(prev_doc.get("rules"), dict):
        prev = {k: v for k, v in prev_doc["rules"].items()}
    else:
        prev = {k: {**v, "state": "fired"} for k, v in (prev_doc.get("fired") or {}).items()}

    alerts, recovers, unknowns = [], [], []
    next_state = dict(prev)
    for key, item in states.items():
        prev_state = (prev.get(key) or {}).get("state")
        if item["state"] == "fired":
            next_state[key] = item
            if prev_state != "fired":
                alerts.append(item)
        elif item["state"] in ("quiet", "stale"):
            # stale = 窗口内曾经越阈、但最新一期没有 → 与 quiet 同样算"已回落"。
            next_state[key] = item
            if prev_state == "fired":
                recovers.append(item)
        else:  # unknown：缺数，不判定、不改写、不通知
            unknowns.append(key)
            # next_state 保持 prev 里的条目（若本来没有就不写）

    if not dry_run:
        _write_json(state_path, {"at": _now_iso(), "rules": next_state})
    receipt["alerts"] = [a["key"] for a in alerts]
    receipt["recovers"] = [r["key"] for r in recovers]
    receipt["unknown"] = unknowns
    receipt["stale"] = sorted(k for k, v in states.items() if v["state"] == "stale")
    # 覆盖度信号：一条线"配好了"不等于"能响"。全部规则都判不了（n/a）时，
    # 这条线每天都会安静地跑绿——那是最危险的假信号，必须显性化（退出码 6）。
    total = len(states)
    evaluable = sum(1 for v in states.values() if v["state"] in ("fired", "quiet"))
    receipt["coverage"] = {
        "rules": total,
        "evaluable": evaluable,
        "unknown": len(unknowns),
        "mute": total > 0 and evaluable == 0,
    }

    pending = [("alert", a) for a in alerts] + [("recover", r) for r in recovers]
    if not pending:
        return receipt
    if dry_run:
        receipt["skipped"] = len(pending)
        receipt["reason"] = "dry-run（不发送）"
        return receipt

    from fd_industry_data.alerts_core import notify as alert_notify  # noqa: PLC0415
    from fd_industry_data.alerts_core.delivery import smtp_from_env  # noqa: PLC0415

    # 收件人来自配置（版本化），因此不能用"要求 env 带 TO"的那个构造函数。
    recipients = _notify_targets(cfg)
    smtp = smtp_from_env(recipients=recipients)
    if smtp is None:
        receipt["skipped"] = len(pending)
        receipt["reason"] = ("no-smtp-config：缺 FD_ALERT_SMTP_HOST/FROM/TO；"
                             "落仓成功不等于有人收到")
        print(f"通知：跳过 {len(pending)} 条 —— {receipt['reason']}")
        return receipt

    smtp.recipients = recipients
    line = _rule_label(cfg)
    for kind, item in pending:
        zh = "越阈" if kind == "alert" else "恢复"
        subject = f"[{line}] {zh}：{item['rule']} / {item.get('concept')}"
        body = "\n".join([
            f"线路：{line}",
            f"规则：{item['rule']}",
            f"状态：{zh}",
            f"概念：{item.get('concept')}",
            f"触发期：{item.get('trigger_date')}",
            f"读数：{item.get('trigger_value')}（阈值 {item.get('comparator')} {item.get('threshold')}）",
            "",
            f"判定时间：{now.isoformat() if hasattr(now, 'isoformat') else now}",
            "本邮件只在越阈集合发生变化时发出；持续越阈不重复通知。",
        ])
        try:
            alert_notify.send_email(smtp, subject, body, smtp_factory=smtp_factory)
            receipt["sent"] += 1
            receipt["messages"].append({"key": item["key"], "kind": kind, "status": "sent"})
        except Exception as exc:  # noqa: BLE001 — 单条失败不阻塞同批
            receipt["failed"] += 1
            receipt["messages"].append({"key": item["key"], "kind": kind,
                                        "status": "failed",
                                        "error": f"{type(exc).__name__}: {exc}"[:200]})
    print(f"通知：sent={receipt['sent']} failed={receipt['failed']} → {recipients}")
    return receipt


def connect(dsn: str | None = None):
    """只读连接：psycopg3 → psycopg2 → psql 子进程（CI runner 无驱动）。

    第三段回落是必须的：self-hosted runner 上没有 psycopg/psycopg2
    （2026-11-08 农业线在同一条通道上实测 ModuleNotFoundError）。
    环境不满足（缺 DSN / 缺 SELECT）一律退出 3，与「有规则越阈」区分开。
    """
    from fd_industry_data.pg_fallback import connect_readonly  # noqa: PLC0415

    return connect_readonly(dsn)


def fetch_series(cur, concept: str, source_used: str,
                 entity_id: int | None = None) -> tuple[list[tuple[str, float]], list[dict]]:
    """一条序列的 (date, value) 升序；``source_used`` 钉死。

    返回 (points, rejected)：value 非数值的行不参与计算，逐条进 rejected
    （不静默丢弃）。
    """
    sql = """
        SELECT o.date, o.value
        FROM semantic_observations o
        JOIN concepts c ON c.id = o.concept_id
        WHERE c.code = %s AND o.source_used = %s
          AND (%s::int IS NULL OR o.entity_id = %s::int)
        ORDER BY o.date, o.fetched_at
    """
    cur.execute(sql, (concept, source_used, entity_id, entity_id))
    points: list[tuple[str, float]] = []
    rejected: list[dict] = []
    for raw_date, raw_value in cur.fetchall():
        try:
            value = float(str(raw_value).replace(",", ""))
        except (TypeError, ValueError):
            rejected.append({"date": _norm_date(raw_date), "raw_value": str(raw_value)})
            continue
        points.append((_norm_date(raw_date), value))
    # 同 date 多次抓取：保留最后一次（已按 fetched_at 升序）
    deduped: dict[str, float] = {}
    for d, v in points:
        deduped[d] = v
    return sorted(deduped.items()), rejected


# ── 缺测核对（显式 gap，不插值） ───────────────────────────────────────────

def _buckets(start: date, end: date, cadence: str) -> list[str]:
    out: list[str] = []
    cursor = start
    if cadence == "monthly":
        while cursor <= end:
            out.append(_fmt(cursor, "monthly"))
            cursor = _add_months(cursor, 1)
        return out
    step = timedelta(days=7) if cadence == "weekly" else timedelta(days=1)
    while cursor <= end:
        out.append(_fmt(cursor, cadence))
        cursor += step
    return out


def gap_report(observed_dates: list[str], cadence: str, window_from: date,
               window_to: date, structural_months: list[str] | None = None,
               gap_note: str = "") -> list[dict]:
    """窗口内「期望有、实际没有」的桶，逐条给归因（never_held 在 caller 记）。

    * 结构性缺期（NBS 1/2 月）→ ``not_applicable``；
    * 交易日序列的周末 → ``not_applicable``；
    * 其余 → ``gap_in_series``（真实缺测，不插值）。
    """
    have = set(observed_dates)
    structural = set(structural_months or [])
    gaps: list[dict] = []
    for bucket in _buckets(window_from, window_to, cadence):
        if bucket in have:
            continue
        entry: dict = {"date": bucket}
        if cadence == "monthly" and bucket[5:7] in structural:
            entry.update({
                "attribution": "not_applicable",
                "reason": gap_note or "上游按合并月份发布，该月不分月披露",
            })
        elif cadence == "daily" and _as_date(bucket) is not None and _as_date(bucket).weekday() >= 5:
            entry.update({
                "attribution": "not_applicable",
                "reason": "周末；交易日序列源不发布" + (f"（{gap_note}）" if gap_note else ""),
            })
        else:
            entry.update({
                "attribution": "gap_in_series",
                "reason": "窗口内该期无观测（真实缺测，未插值）"
                          + (f"；可能为交易所休市，配置未提供节假日日历（{gap_note}）" if gap_note else ""),
            })
        gaps.append(entry)
    return gaps


def _never_held(replay_from: date, first_observed: date | None, cadence: str) -> dict | None:
    """replay 起点早于序列首次观测 → 该区间从未有过数据（never_held）。"""
    if first_observed is None or replay_from >= first_observed:
        return None
    return {
        "from": _fmt(replay_from, cadence),
        "to": _fmt(first_observed - timedelta(days=1), cadence),
        "attribution": "never_held",
        "reason": "replay 窗口起点早于该序列首次观测；上游从未提供该区间数据（不补造）",
    }


# ── 判定原语 ──────────────────────────────────────────────────────────────

def _crossed(comparator: str, value: float, threshold: float) -> bool:
    if comparator == "lt":
        return value < threshold
    if comparator == "lte":
        return value <= threshold
    if comparator == "gt":
        return value > threshold
    if comparator == "gte":
        return value >= threshold
    if comparator == "abs_gte":
        return abs(value) >= threshold
    raise ValueError(f"未知 comparator: {comparator!r}")


def _distance(comparator: str, values: list[float], threshold: float) -> float:
    """quiet 状态下最近点距阈值多远（恒 >= 0）。"""
    if not values:
        return 0.0
    if comparator == "lt":
        return min(values) - threshold
    if comparator == "lte":
        return min(values) - threshold
    if comparator == "gt":
        return threshold - max(values)
    if comparator == "gte":
        return threshold - max(values)
    if comparator == "abs_gte":
        return threshold - max(abs(v) for v in values)
    raise ValueError(f"未知 comparator: {comparator!r}")


def _window_bounds(points: list[tuple[str, float]], replay_from: date, asof: date,
                   cadence: str) -> tuple[date, date, str, str] | None:
    """窗口 = [max(replay_from, 首观测), min(asof, 末观测)]；空窗口返回 None。"""
    if not points:
        return None
    first = max(replay_from, _as_date(points[0][0]))
    last = min(asof, _as_date(points[-1][0]))
    if first > last:
        return None
    return first, last, _fmt(first, cadence), _fmt(last, cadence)


# ── 规则 1：单序列阈值 ─────────────────────────────────────────────────────

def eval_threshold_rule(rule: dict, points: list[tuple[str, float]], rejected: list[dict],
                        replay_from: date, asof: date) -> dict:
    cadence = rule.get("periodicity", "monthly")
    min_history = int(rule.get("min_history", 1))
    threshold = float(rule["threshold"])
    comparator = rule["comparator"]
    structural = (rule.get("structural_gaps") or {}).get("months") or []
    gap_note = (rule.get("structural_gaps") or {}).get("reason", "")

    evidence: dict = {
        "rule": rule["name"],
        "concept": rule["concept"],
        "source_used": rule["source_used"],
        "entity_type": rule.get("entity_type"),
        "entity_id": rule.get("entity_id"),
        "window": [None, None],
        "observed": [],
        "trigger_date": None,
        "trigger_value": None,
        "threshold": threshold,
        "comparator": comparator,
        "verdict": "n/a",
        "unit": rule.get("unit", ""),
        "gaps": [],
        "gap_counts": {kind: 0 for kind in GAP_KINDS},
        "never_held": None,
        "closest": None,
        "evaluated_points": 0,
        "provenance": (
            f"semantic_observations; concept={rule['concept']};"
            f" source_used={rule['source_used']}; entity_id={rule.get('entity_id')}"
        ),
        "rejected_values": rejected,
    }
    if rejected:
        evidence["gap_counts"]["gap_in_series"] += len(rejected)

    if len(points) < min_history:
        evidence["reason"] = (
            f"insufficient_history: 观测 {len(points)} 期 < min_history={min_history}（判 n/a，不编阈值）"
        )
        if points:
            evidence["window"] = [_norm_date(points[0][0]), _norm_date(points[-1][0])]
            evidence["observed"] = [{"date": d, "value": _round(v)} for d, v in points]
        return evidence

    bounds = _window_bounds(points, replay_from, asof, cadence)
    if bounds is None:
        evidence["reason"] = "窗口内无观测（replay_from/asof 与序列不重叠）"
        return evidence
    win_from, win_to, win_from_s, win_to_s = bounds
    in_window = [(d, v) for d, v in points if win_from_s <= d <= win_to_s]
    evidence["window"] = [win_from_s, win_to_s]
    evidence["observed"] = [{"date": d, "value": _round(v)} for d, v in in_window]
    evidence["evaluated_points"] = len(in_window)
    evidence["never_held"] = _never_held(replay_from, _as_date(points[0][0]), cadence)
    evidence["gaps"] = gap_report(
        [d for d, _ in in_window], cadence, win_from, win_to,
        structural_months=structural, gap_note=gap_note,
    )
    for gap in evidence["gaps"]:
        evidence["gap_counts"][gap["attribution"]] += 1

    if not in_window:
        evidence["reason"] = "窗口内无观测（全部缺测）"
        return evidence

    triggers = [(d, v) for d, v in in_window if _crossed(comparator, v, threshold)]
    # 最新一期是否越阈——"窗口内曾经越阈"不等于"现在越阈"。
    # replay 窗可以长达 96 个月，只看"窗口内存在越阈点"会把两年前的旧越阈当成当期告警。
    evidence["latest_period"] = in_window[-1][0]
    evidence["current_fired"] = bool(triggers) and triggers[-1][0] == in_window[-1][0]
    if triggers:
        trigger_date, trigger_value = triggers[-1]
        evidence.update({
            "verdict": "fired",
            "trigger_date": trigger_date,
            "trigger_value": _round(trigger_value),
            "trigger_count": len(triggers),
            "triggers": [
                {"date": d, "value": _round(v)} for d, v in triggers[-20:]
            ],
        })
    else:
        values = [v for _, v in in_window]
        closest = min(
            ((d, v) for d, v in in_window),
            key=lambda item: abs(item[1] - threshold),
        )
        evidence.update({
            "verdict": "quiet",
            "closest": {
                "date": closest[0],
                "value": _round(closest[1]),
                "distance": _round(_distance(comparator, values, threshold)),
                "unit": rule.get("unit", ""),
            },
            "trigger_count": 0,
            "triggers": [],
        })
    return evidence


# ── 规则 2：同比剪刀差 ─────────────────────────────────────────────────────

def eval_yoy_diff_rule(rule: dict, comps: dict[str, list[tuple[str, float]]],
                       replays: dict[str, date], asof: date,
                       replay_from: date) -> dict:
    cadence = rule.get("periodicity", "monthly")
    lag = int(rule.get("yoy_lag_months", 12))
    threshold = float(rule["threshold"])
    comparator = rule["comparator"]
    components = rule.get("components") or []
    names = [c["key"] for c in components]

    levels: dict[str, dict[str, float]] = {}
    for comp in components:
        levels[comp["key"]] = dict(comps.get(comp["key"], []))
    rejected = replays.get("__rejected__", [])  # type: ignore[assignment]

    # 月度同级：只有双方都存在、且双方上年同月都存在才计算（不插值）
    diffs: list[dict] = []
    skipped: list[dict] = []
    for month in sorted(set(levels[names[0]]) if names else []):
        level = {name: levels[name].get(month) for name in names}
        if any(v is None for v in level.values()):
            skipped.append({
                "date": month,
                "attribution": "gap_in_series",
                "reason": "组成序列该月水平缺失（真实缺测，不插值）",
            })
            continue
        prior = _fmt(_add_months(_as_date(month), -lag), "monthly")
        base = {name: levels[name].get(prior) for name in names}
        if any(v is None for v in base.values()):
            skipped.append({
                "date": month,
                "attribution": "gap_in_series",
                "reason": f"上年同月 {prior} 水平缺失，无法算同比（不插值）",
            })
            continue
        yoy = {name: (level[name] / base[name] - 1.0) * 100.0 for name in names}
        if len(names) < 2:  # pragma: no cover - 配置约束
            continue
        diffs.append({
            "date": month,
            "value": yoy[names[0]] - yoy[names[1]],
            "yoy_away": yoy[names[0]],
            "yoy_home": yoy[names[1]],
            "level": level,
            "base": base,
            "base_month": prior,
        })

    evidence: dict = {
        "rule": rule["name"],
        "concept": rule["concept"],
        "source_used": rule["source_used"],
        "entity_type": "country",
        "entity_id": rule["components"][0].get("entity_id"),
        "window": [None, None],
        "observed": [],
        "trigger_date": None,
        "trigger_value": None,
        "threshold": threshold,
        "comparator": comparator,
        "verdict": "n/a",
        "unit": rule.get("unit", ""),
        "gaps": [],
        "gap_counts": {kind: 0 for kind in GAP_KINDS},
        "never_held": None,
        "closest": None,
        "evaluated_points": 0,
        "components": [
            {
                "key": c["key"],
                "concept": c["concept"],
                "source_used": c["source_used"],
                "entity_id": c.get("entity_id"),
                "observations": len(comps.get(c["key"], [])),
            }
            for c in components
        ],
        "yoy_skipped": skipped,
        "rejected_values": rejected,
        "provenance": (
            "semantic_observations; yoy(food_away)-yoy(food_at_home);"
            f" yoy_lag_months={lag}; source_used={rule['source_used']}"
        ),
    }

    if not diffs:
        evidence["reason"] = "无可计算同比的月份（组成序列不足或上年同月缺失）"
        return evidence

    first = _as_date(diffs[0]["date"])
    last = _as_date(diffs[-1]["date"])
    win_from = max(replay_from, first)
    win_to = min(asof, last)
    if win_from > win_to:
        evidence["reason"] = "窗口内无可计算同比月份"
        return evidence
    win_from_s, win_to_s = _fmt(win_from, "monthly"), _fmt(win_to, "monthly")
    in_window = [row for row in diffs if win_from_s <= row["date"] <= win_to_s]
    # 缺测核对：以月度桶为期望，diff 未覆盖的月份必须显式出现
    gaps = gap_report(
        [row["date"] for row in in_window], "monthly", win_from, win_to,
    )
    skipped_map = {row["date"]: row for row in skipped}
    for gap in gaps:
        row = skipped_map.get(gap["date"])
        if row:
            gap.update(row)
    evidence.update({
        "window": [win_from_s, win_to_s],
        "observed": [
            {
                "date": row["date"],
                "value": _round(row["value"]),
                "yoy_away": _round(row["yoy_away"]),
                "yoy_home": _round(row["yoy_home"]),
            }
            for row in in_window
        ],
        "evaluated_points": len(in_window),
        "never_held": _never_held(replay_from, first, "monthly"),
        "gaps": gaps,
    })
    for gap in gaps:
        evidence["gap_counts"][gap["attribution"]] += 1
    evidence["gap_counts"]["gap_in_series"] += len(rejected)

    if not in_window:
        evidence["reason"] = "窗口内无可计算同比月份"
        return evidence

    triggers = [row for row in in_window if _crossed(comparator, row["value"], threshold)]
    evidence["latest_period"] = in_window[-1]["date"]
    evidence["current_fired"] = bool(triggers) and triggers[-1]["date"] == in_window[-1]["date"]
    if triggers:
        last_trig = triggers[-1]
        evidence.update({
            "verdict": "fired",
            "trigger_date": last_trig["date"],
            "trigger_value": _round(last_trig["value"]),
            "trigger_count": len(triggers),
            "trigger_detail": {
                "yoy_away": _round(last_trig["yoy_away"]),
                "yoy_home": _round(last_trig["yoy_home"]),
                "levels": {k: _round(v) for k, v in last_trig["level"].items()},
                "base_month": last_trig["base_month"],
                "base_levels": {k: _round(v) for k, v in last_trig["base"].items()},
            },
            "triggers": [
                {"date": row["date"], "value": _round(row["value"])}
                for row in triggers[-20:]
            ],
        })
    else:
        values = [row["value"] for row in in_window]
        closest = min(in_window, key=lambda row: abs(row["value"] - threshold))
        evidence.update({
            "verdict": "quiet",
            "closest": {
                "date": closest["date"],
                "value": _round(closest["value"]),
                "distance": _round(_distance(comparator, values, threshold)),
                "unit": rule.get("unit", ""),
                "yoy_away": _round(closest["yoy_away"]),
                "yoy_home": _round(closest["yoy_home"]),
            },
            "trigger_count": 0,
            "triggers": [],
        })
    return evidence


# ── 规则 3：滚动 z-score 尖峰 ──────────────────────────────────────────────

def eval_zscore_series(rule: dict, series: dict, points: list[tuple[str, float]],
                       rejected: list[dict], replay_from: date, asof: date) -> dict:
    cadence = series.get("periodicity", "daily")
    z_window = int(series.get("z_window", 30))
    min_history = int(series.get("min_history", rule.get("min_history", 24)))
    threshold = float(rule["threshold"])
    comparator = rule["comparator"]

    evidence: dict = {
        "rule": rule["name"],
        "concept": series["concept"],
        "source_used": series["source_used"],
        "entity_type": series.get("entity_type"),
        "entity_id": series.get("entity_id"),
        "window": [None, None],
        "observed": [],
        "trigger_date": None,
        "trigger_value": None,
        "threshold": threshold,
        "comparator": comparator,
        "verdict": "n/a",
        "unit": rule.get("unit", ""),
        "gaps": [],
        "gap_counts": {kind: 0 for kind in GAP_KINDS},
        "never_held": None,
        "closest": None,
        "evaluated_points": 0,
        "z_window": z_window,
        "z_basis": "滚动 z-score：前 z_window 个观测的均值/总体标准差，不含当前点",
        "gap_note": series.get("gap_note", ""),
        "provenance": (
            f"semantic_observations; concept={series['concept']};"
            f" source_used={series['source_used']}; z_window={z_window}"
        ),
        "rejected_values": rejected,
    }
    if len(points) < min_history:
        evidence["reason"] = (
            f"insufficient_history: 观测 {len(points)} 期 < min_history={min_history}"
            f"（判 n/a，不用不足样本算 z-score）"
        )
        if points:
            evidence["window"] = [_norm_date(points[0][0]), _norm_date(points[-1][0])]
            evidence["observed"] = [{"date": d, "value": _round(v)} for d, v in points]
        return evidence

    bounds = _window_bounds(points, replay_from, asof, cadence)
    if bounds is None:
        evidence["reason"] = "窗口内无观测（replay_from/asof 与序列不重叠）"
        return evidence
    win_from, win_to, win_from_s, win_to_s = bounds
    in_window = [(d, v) for d, v in points if win_from_s <= d <= win_to_s]
    evidence["window"] = [win_from_s, win_to_s]
    evidence["observed"] = [{"date": d, "value": _round(v)} for d, v in in_window]
    evidence["never_held"] = _never_held(replay_from, _as_date(points[0][0]), cadence)
    evidence["gaps"] = gap_report(
        [d for d, _ in points], cadence, win_from, win_to,
        gap_note=series.get("gap_note", ""),
    )
    for gap in evidence["gaps"]:
        evidence["gap_counts"][gap["attribution"]] += 1
    evidence["gap_counts"]["gap_in_series"] += len(rejected)

    zs: list[dict] = []
    for index in range(z_window, len(points)):
        window_values = [v for _, v in points[index - z_window:index]]
        if len(window_values) < z_window:
            continue
        mean = statistics.fmean(window_values)
        stdev = statistics.pstdev(window_values)
        if stdev == 0:
            continue
        z = (points[index][1] - mean) / stdev
        zs.append({
            "date": points[index][0],
            "value": points[index][1],
            "z": z,
            "mean": mean,
            "stdev": stdev,
        })
    in_win_zs = [row for row in zs if win_from_s <= row["date"] <= win_to_s]
    evidence["evaluated_points"] = len(in_win_zs)
    if not in_win_zs:
        evidence["reason"] = f"窗口内可用 z 点不足（需 ≥{z_window + 1} 期历史）"
        return evidence

    triggers = [row for row in in_win_zs if comparator == "abs_gte" and abs(row["z"]) >= threshold]
    evidence["latest_period"] = in_win_zs[-1]["date"]
    evidence["current_fired"] = bool(triggers) and triggers[-1]["date"] == in_win_zs[-1]["date"]
    if triggers:
        last_trig = triggers[-1]
        evidence.update({
            "verdict": "fired",
            "trigger_date": last_trig["date"],
            "trigger_value": _round(last_trig["z"]),
            "trigger_detail": {
                "raw_value": _round(last_trig["value"]),
                "rolling_mean": _round(last_trig["mean"]),
                "rolling_stdev": _round(last_trig["stdev"]),
                "z_window": z_window,
            },
            "trigger_count": len(triggers),
            "triggers": [
                {"date": row["date"], "z": _round(row["z"]), "value": _round(row["value"])}
                for row in triggers[-20:]
            ],
        })
    else:
        closest = max(in_win_zs, key=lambda row: abs(row["z"]))
        evidence.update({
            "verdict": "quiet",
            "closest": {
                "date": closest["date"],
                "value": _round(closest["z"]),
                "distance": _round(threshold - abs(closest["z"])),
                "unit": rule.get("unit", ""),
                "raw_value": _round(closest["value"]),
            },
            "trigger_count": 0,
            "triggers": [],
        })
    return evidence


# ── 事件级（5.3） ──────────────────────────────────────────────────────────

def _dedupe_latest(items: list[dict], key_name: str) -> dict:
    """同一自然键保留 scraped_at 最新的 payload。"""
    seen: dict[str, dict] = {}
    for payload in items:
        key = payload.get(key_name)
        if not key:
            continue
        prev = seen.get(key)
        if prev is None or str(payload.get("scraped_at", "")) > str(prev.get("scraped_at", "")):
            seen[key] = payload
    return seen


def _fetch_unit_payloads(cur, source: str) -> list[dict]:
    """抓取该源的 crawl_items.payload。

    **两条连接路径的类型不同**：psycopg 把 ``jsonb`` 解成 dict，而 psql 子进程只返回
    文本（见 ``fd_industry_data/pg_fallback.py`` 的语义差异说明）。CI runner 只能走
    psql，于是这里统一补一次解码——否则事件级会以
    ``AttributeError: 'str' object has no attribute 'get'`` 崩掉（2026-11-08 CI 实测）。
    """
    sql = """
        SELECT ci.payload
        FROM crawl_items ci
        JOIN crawl_runs r ON r.id = ci.run_id
        WHERE r.source = %s AND r.status = 'success'
    """
    cur.execute(sql, (source,))
    out: list[dict] = []
    for row in cur.fetchall():
        value = row[0]
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                continue
        if isinstance(value, dict):
            out.append(value)
    return out


def _title_hash(title: str, n: int = 8) -> str:
    return hashlib.sha1(str(title).encode("utf-8")).hexdigest()[:n]


def scan_events(cur, cfg: dict, asof: date) -> list[dict]:
    """扫三类事件；返回自然键去重后的事件条目（未与台账比对）。"""
    events_cfg = cfg.get("events") or {}
    window_days = int((cfg.get("defaults") or {}).get("events_window_days", 730))
    cutoff = (asof - timedelta(days=window_days)).isoformat()

    out: list[dict] = []

    # 1) 财报日历 + 盈警（hkex-disclosure）
    hkex_results = set(events_cfg.get("hkex_results_types") or ["results"])
    hkex_warnings = set(events_cfg.get("hkex_profit_warning_types")
                        or ["profit-warning", "profit-alert"])
    hkex_items = _dedupe_latest(_fetch_unit_payloads(cur, "hkex-disclosure"), "doc_url")
    for payload in hkex_items.values():
        ann_type = str(payload.get("ann_type") or "")
        event_date = str(payload.get("release_date") or "")
        if not event_date or event_date < cutoff:
            continue
        if ann_type in hkex_results:
            kind = "financial_calendar"
        elif ann_type in hkex_warnings:
            kind = "profit_warning"
        else:
            continue
        stock_code = str(payload.get("stock_code") or "")
        out.append({
            "key": f"hkex:{stock_code}:{event_date}:{ann_type}:{_title_hash(payload.get('title_zh') or payload.get('title') or '')}",
            "kind": kind,
            "source": "hkex-disclosure",
            "entity": stock_code,
            "entity_name": payload.get("stock_name"),
            "date": event_date,
            "ann_type": ann_type,
            "title": payload.get("title_zh") or payload.get("title"),
            "doc_url": payload.get("doc_url"),
        })

    # 2) 财报日历（sec-edgar-facts 的 10-Q/10-K event 行）
    sec_forms = set(events_cfg.get("financial_forms_sec") or ["10-Q", "10-K"])
    sec_items = _dedupe_latest(_fetch_unit_payloads(cur, "sec-edgar-facts"), "accession")
    for payload in sec_items.values():
        if payload.get("kind") != "event":
            continue
        form = str(payload.get("form") or "")
        if form not in sec_forms:
            continue
        event_date = str(payload.get("filed") or "")
        if not event_date or event_date < cutoff:
            continue
        out.append({
            "key": f"sec:{payload.get('ticker')}:{payload.get('accession')}",
            "kind": "financial_calendar",
            "source": "sec-edgar-facts",
            "entity": payload.get("ticker"),
            "entity_name": payload.get("ticker"),
            "date": event_date,
            "form": form,
            "title": f"{payload.get('ticker')} {form}",
            "report_date": payload.get("report_date"),
            "accession": payload.get("accession"),
            "primary_doc_url": payload.get("primary_doc_url"),
        })

    # 3) 抽检不合格 & 餐饮相关（samr-food-inspection）
    samr_category = events_cfg.get("samr_result_category", "unqualified")
    samr_items = _dedupe_latest(_fetch_unit_payloads(cur, "samr-food-inspection"), "url")
    for payload in samr_items.values():
        if payload.get("result_category") != samr_category:
            continue
        if payload.get("restaurant_related") is not True:
            continue
        event_date = str(payload.get("notice_date") or "")
        if not event_date or event_date < cutoff:
            continue
        url = str(payload.get("url") or "")
        out.append({
            "key": f"samr:{url}",
            "kind": "samr_unqualified_restaurant",
            "source": "samr-food-inspection",
            "entity": "SAMR",
            "entity_name": "市场监管总局",
            "date": event_date,
            "result_category": payload.get("result_category"),
            "restaurant_related": True,
            "title": payload.get("title"),
            "url": url,
            "body_digest_head": str(payload.get("body_digest") or "")[:160],
        })

    out.sort(key=lambda e: (e["kind"], e["date"], e["key"]))
    return out


def _load_ledger(path: Path) -> dict[str, dict]:
    seen: dict[str, dict] = {}
    if not path.exists():
        return seen
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("key"):
            seen[row["key"]] = row
    return seen


def _append_ledger(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")


def cmd_events(cur, cfg: dict, asof: date, outdir: Path, dry_run: bool) -> dict:
    defaults = cfg.get("defaults") or {}
    ledger_path = REPO / defaults.get("ledger", "reports/restaurant-alerts/state/events.jsonl")
    events = scan_events(cur, cfg, asof)
    seen = _load_ledger(ledger_path)

    new = [event for event in events if event["key"] not in seen]
    already = [event for event in events if event["key"] in seen]

    by_kind: dict[str, int] = {}
    new_by_kind: dict[str, int] = {}
    for event in events:
        by_kind[event["kind"]] = by_kind.get(event["kind"], 0) + 1
    for event in new:
        new_by_kind[event["kind"]] = new_by_kind.get(event["kind"], 0) + 1

    now_iso = _now_iso()
    ledger_rows = [
        {
            "key": event["key"],
            "kind": event["kind"],
            "source": event["source"],
            "date": event["date"],
            "reported_at": now_iso,
            "ref": {
                k: event.get(k)
                for k in ("title", "doc_url", "url", "primary_doc_url", "accession",
                          "ann_type", "form", "entity", "entity_name")
                if event.get(k) is not None
            },
        }
        for event in new
    ]
    if ledger_rows and not dry_run:
        _append_ledger(ledger_path, ledger_rows)

    payload = {
        "at": now_iso,
        "asof": asof.isoformat(),
        "dry_run": dry_run,
        "ledger": str(ledger_path.relative_to(REPO)) if ledger_path.is_relative_to(REPO) else str(ledger_path),
        "scanned_total": len(events),
        "scanned_by_kind": by_kind,
        "ledger_seen_before": len(seen),
        "new": len(new),
        "new_by_kind": new_by_kind,
        "skipped_seen": len(already),
        "first_reports": [
            {
                "key": event["key"],
                "kind": event["kind"],
                "date": event["date"],
                "title": event.get("title"),
                "doc_url": event.get("doc_url"),
                "url": event.get("url"),
                "primary_doc_url": event.get("primary_doc_url"),
                "accession": event.get("accession"),
            }
            for event in new
        ],
        "already_reported_keys": [event["key"] for event in already],
    }
    if not dry_run:
        _write_json(outdir / asof.isoformat() / "events.json", payload)
    return payload


# ── 健康级工单读取（5.4 档案与 escalate 的对象） ────────────────────────────

def _load_tickets(tickets_dir: Path, include_terminal: bool = False) -> list[dict]:
    from fd_industry_data.health import ticket as ticket_mod  # noqa: PLC0415

    rows: list[dict] = []
    if not tickets_dir.is_dir():
        return rows
    for path in sorted(tickets_dir.glob("*.yaml")):
        doc = ticket_mod.load_ticket(path)
        if not include_terminal and doc.get("terminal") is not None:
            continue
        rows.append({"path": path, "doc": doc, "sha256": _sha256_short(path)})
    return rows


def _ticket_reference(tickets_dir: Path, repo: Path) -> str:
    try:
        return str(tickets_dir.resolve().relative_to(repo))
    except ValueError:
        return str(tickets_dir)


# ── escalate（5.4） ───────────────────────────────────────────────────────

def cmd_escalate(args, cfg: dict, dry_run: bool) -> dict:
    from fd_industry_data.health import inspector  # noqa: PLC0415
    from fd_industry_data.health.config import load_config  # noqa: PLC0415

    tickets_dir = Path(args.tickets_dir or
                       REPO / "reports/restaurant-alerts" / _today().isoformat() / "health-drill/tickets")
    tickets = _load_tickets(tickets_dir)
    key = os.environ.get("WANXING_SPIDER_HEAL_KEY", "")
    # 门面主机 = wanxing.finddatatech.cloud（unify-wanxing-a2a-face 4.x 后，
    # platform.finddatatech.cloud 上的 /api/wanxing 挂载已摘除；打到旧主机只会被平台
    # auth 闸拦成 401 "Authentication required"，与密钥无关——2026-11-09 实测）
    url = os.environ.get(
        "WANXING_SPIDER_HEAL_URL",
        "https://wanxing.finddatatech.cloud/api/wanxing/v1/a2a/"
        "packs-kkcie7nlrhluo4likpnn0w-spider-heal",
    )
    repo_slug = os.environ.get("FD_HEALTH_REPO_SLUG", "FindDataTechnology/fd-industry-data")
    health_config = load_config(args.health_config) if args.health_config else load_config(None)
    record: dict = {
        "at": _now_iso(),
        "tickets_dir": str(tickets_dir),
        "tickets": [t["doc"].get("ticket_id") for t in tickets],
        "key_present": bool(key),
        "url": url,
        "master_switch": health_config.master_switch,
        "dry_run": dry_run,
    }
    if not tickets:
        record.update({"status": "noop", "reason": "目录内无未终态工单"})
    elif dry_run:
        record.update({
            "status": "dry-run",
            "reason": "停在投递前：未调用萬星 spider-heal（--dry-run）",
        })
    elif not key:
        record.update({
            "status": "blocked",
            "reason": "WANXING_SPIDER_HEAL_KEY 未设置——已走到实际投递前的最后一步，未发出 SUBMIT",
            "next_command": (
                f"WANXING_SPIDER_HEAL_KEY=*** .venv/bin/python scripts/restaurant_alerts.py "
                f"--escalate --tickets-dir {tickets_dir}"
            ),
            "two_phase_note": (
                "工单须先落入远端可见的仓库（git push 落仓门）再投递；"
                "同轮写盘+投递会被 agent 以「文件不存在」拒收（docs/health-loop.md）。"
            ),
        })
    else:
        results = inspector.submit_new_tickets(
            tickets_dir, repo_slug, url, key, health_config, dry_run=False,
        )
        ok = sum(1 for row in results if row.get("submitted"))
        record.update({
            "status": "delivered" if ok == len(results) else "partial",
            "results": results,
        })
    ledger_path = REPO / (cfg.get("defaults") or {}).get(
        "escalations", "reports/restaurant-alerts/state/escalations.jsonl"
    )
    if not dry_run:
        _append_ledger(ledger_path, [record])
    return record


# ── 值守档案（5.4） ────────────────────────────────────────────────────────

def _fmt_value(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def render_duty_log(*, asof: date, cfg: dict, outdir: Path, tickets_dir: Path | None,
                    tickets: list[dict], rules_runs: list[dict], events_payload: dict | None,
                    escalations: list[dict], snapshots: list[Path]) -> str:
    lines: list[str] = []
    add = lines.append
    # 线名跟着配置走，不硬编码：这条评估器被餐饮/花卉/酒店三条线共用，
    # 写死「餐饮监测舰」会让酒店线出的值守档案自我误标（2026-11-08 实测：
    # 酒店档标题仍是餐饮 —— 6.6 当时只改了那一份报告正文，没改代码）。
    add(f"# {cfg.get('duty_log_title') or '餐饮监测舰'} · 值守档案 {asof.isoformat()}")
    add("")
    add(f"- 生成时间（UTC）: `{_now_iso()}`")
    add(f"- 生成命令: `{os.environ.get('FD_DUTY_LOG_CMD', 'python3 scripts/restaurant_alerts.py --duty-log')}`")
    add(f"- 时区: 报告日期按本机本地日（CST）")
    add("")

    # 1. 摘要
    add("## 1. 结论摘要")
    add("")
    add("| 层 | 结论 | 证据（可回指） |")
    add("|---|---|---|")
    health_txt = (f"{len(tickets)} 张未终态工单"
                  if tickets else "无未终态工单")
    health_ref = (", ".join(f"`{t['doc'].get('ticket_id')}`" for t in tickets)
                  if tickets else "-")
    add(f"| 健康级（inspector） | {health_txt} | {health_ref} |")
    if rules_runs:
        fired = [e for run in rules_runs for e in run.get("evidence", [])
                 if e.get("verdict") == "fired"]
        na = [e for run in rules_runs for e in run.get("evidence", [])
              if e.get("verdict") == "n/a"]
        quiet = [e for run in rules_runs for e in run.get("evidence", [])
                 if e.get("verdict") == "quiet"]
        add(f"| 阈值级 | fired={len(fired)} / quiet={len(quiet)} / n/a={len(na)} | "
            + ", ".join(f"`threshold-{r['rule']}.json`" for r in rules_runs) + " |")
    else:
        add("| 阈值级 | 未生成（本次未跑 --rules） | - |")
    if events_payload is not None:
        add(f"| 事件级 | 首报 {events_payload['new']} / 已见 {events_payload['skipped_seen']} / "
            f"扫描 {events_payload['scanned_total']} | `events.json`, `state/events.jsonl` |")
    else:
        add("| 事件级 | 未生成（本次未跑 --events） | - |")
    if escalations:
        last = escalations[-1]
        add(f"| escalate | {last.get('status')} | `state/escalations.jsonl` |")
    else:
        add("| escalate | 未执行（本次未跑 --escalate） | - |")
    add("")

    # 2. 健康级工单 + 原始遥测
    add("## 2. 健康级工单（来源：scripts/health_inspect.py）")
    add("")
    if not tickets:
        add("本次目录内无未终态工单。")
        add("")
    for index, row in enumerate(tickets, start=1):
        doc = row["doc"]
        add(f"### 2.{index} `{doc.get('ticket_id')}` · source=`{doc.get('source')}`")
        add("")
        add(f"- 工单文件: `{row['path']}`（sha256:{row['sha256']}）")
        add(f"- unit: `{doc.get('unit')}` · kind=`{doc.get('kind')}` · "
            f"category=`{doc.get('category')}` · needs_human={doc.get('needs_human')} · "
            f"terminal={doc.get('terminal')}")
        evidence = doc.get("evidence") or {}
        window = evidence.get("window") or {}
        add(f"- evidence.window: {window.get('from')} → {window.get('to')}")
        add(f"- last_success: `{json.dumps(evidence.get('last_success'), ensure_ascii=False)}`")
        watermark = evidence.get("watermark") or {}
        add(f"- watermark: latest={watermark.get('latest')} · basis={watermark.get('basis')}")
        add(f"- 分诊轨迹: {evidence.get('notes')}")
        add("")
        add("原始遥测行（工单 evidence.failing_runs，逐行回指快照）：")
        add("")
        add("| started_at | status | rows | http_status | error_summary |")
        add("|---|---|---|---|---|")
        for run in evidence.get("failing_runs") or []:
            err = str(run.get("error_summary") or "").replace("|", "\\|")
            add(f"| {run.get('started_at')} | {run.get('status')} | {run.get('rows')} | "
                f"{run.get('http_status')} | {err[:180]} |")
        add("")
        lifecycle = doc.get("lifecycle") or []
        add("生命周期事件（append-only）: " + " → ".join(
            f"{e.get('event')}@{e.get('at')}" for e in lifecycle))
        add("")

    # 3. 阈值级 provenance
    add("## 3. 阈值级证据（provenance）")
    add("")
    if not rules_runs:
        add("本次未生成阈值证据。")
        add("")
    for run in rules_runs:
        add(f"### 规则 `{run['rule']}`（asof={run['asof']}, replay_from={run['replay_from']}）")
        add("")
        add(f"- 证据文件: `{run['path']}`")
        add("")
        add("| concept | verdict | 触发日 | 触发值 | 阈值 | 比较符 | window |")
        add("|---|---|---|---|---|---|---|")
        for ev in run.get("evidence", []):
            add(f"| `{ev['concept']}` | **{ev['verdict']}** | {ev.get('trigger_date') or '-'} | "
                f"{_fmt_value(ev.get('trigger_value'))} | {_fmt_value(ev.get('threshold'))} | "
                f"{ev.get('comparator')} | {ev['window'][0]} → {ev['window'][1]} |")
        add("")
        for ev in run.get("evidence", []):
            add(f"- `{ev['concept']}` · source_used=`{ev.get('source_used')}` · "
                f"观测 {ev.get('evaluated_points')} 期 · verdict={ev.get('verdict')}"
                + (f" · {ev['reason']}" if ev.get("reason") else ""))
            if ev.get("verdict") == "quiet" and ev.get("closest"):
                c = ev["closest"]
                add(f"  - 未触发；最近点 {c['date']} = {_fmt_value(c['value'])}，"
                    f"距阈值 {_fmt_value(c['distance'])} {c.get('unit', '')}")
            if ev.get("verdict") == "fired" and ev.get("trigger_detail"):
                add(f"  - 触发明细: `{json.dumps(ev['trigger_detail'], ensure_ascii=False)}`")
        add("")

    # 4. 事件级
    add("## 4. 事件级（财报日历 / 盈警 / 抽检不合格）")
    add("")
    if events_payload is None:
        add("本次未生成事件扫描。")
        add("")
    else:
        add(f"- 扫描总数: {events_payload['scanned_total']} · "
            f"分类: `{json.dumps(events_payload['scanned_by_kind'], ensure_ascii=False)}`")
        add(f"- 台账已有: {events_payload['ledger_seen_before']} · "
            f"本次首报: **{events_payload['new']}** · 已见跳过: {events_payload['skipped_seen']}")
        add("")
        add("首报事件（每条可回指原始公告）：")
        add("")
        add("| kind | date | 自然键 | title | 原件 |")
        add("|---|---|---|---|---|")
        for event in events_payload.get("first_reports", [])[:40]:
            ref = event.get("doc_url") or event.get("primary_doc_url") or event.get("url") or "-"
            title = str(event.get("title") or "").replace("|", "\\|")[:60]
            key = str(event.get("key") or "").replace("|", "\\|")
            add(f"| {event['kind']} | {event['date']} | `{key}` | {title} | {ref} |")
        if len(events_payload.get("first_reports", [])) > 40:
            add(f"| … | | | 其余 {len(events_payload['first_reports']) - 40} 条见 events.json | |")
        add("")

    # 5. 缺测归因
    add("## 5. 缺测归因（never_held / gap_in_series / not_applicable）")
    add("")
    add("口径：只统计阈值证据文件里的缺测条目；不插值、不补零。")
    add("")
    add("| rule | concept | never_held | gap_in_series | not_applicable | 说明 |")
    add("|---|---|---|---|---|---|")
    any_gap = False
    for run in rules_runs:
        for ev in run.get("evidence", []):
            counts = ev.get("gap_counts") or {}
            if not any(counts.get(k, 0) for k in GAP_KINDS):
                continue
            any_gap = True
            never = ev.get("never_held")
            note = ""
            if never:
                note = f"never_held 区间 {never['from']}..{never['to']}（replay 早于首观测）"
            add(f"| {ev['rule']} | `{ev['concept']}` | {counts.get('never_held', 0)} | "
                f"{counts.get('gap_in_series', 0)} | {counts.get('not_applicable', 0)} | {note} |")
    if not any_gap:
        add("| - | - | 0 | 0 | 0 | 本次证据无缺测条目 |")
    add("")
    add("明细（前 60 条，完整列表在各阈值证据文件的 `gaps` 字段）：")
    add("")
    detail_rows = 0
    for run in rules_runs:
        for ev in run.get("evidence", []):
            for gap in ev.get("gaps") or []:
                if detail_rows >= 60:
                    break
                add(f"- `{ev['concept']}` {gap['date']} → **{gap['attribution']}** · {gap['reason']}")
                detail_rows += 1
    if detail_rows == 0:
        add("- （无）")
    add("")

    # 6. escalate
    add("## 6. Escalate 路径")
    add("")
    if not escalations:
        add("本次未执行 escalate。")
        add("")
    for record in escalations:
        add(f"- at={record['at']} · status=**{record.get('status')}** · "
            f"tickets={record.get('tickets')} · key_present={record.get('key_present')}")
        if record.get("reason"):
            add(f"  - reason: {record['reason']}")
        if record.get("next_command"):
            add(f"  - 下一步命令: `{record['next_command']}`")
        if record.get("two_phase_note"):
            add(f"  - 两段式约束: {record['two_phase_note']}")
        if record.get("results"):
            add(f"  - 投递结果: `{json.dumps(record['results'], ensure_ascii=False)[:400]}`")
    add("")

    # 7. 审计回指
    add("## 7. 审计回指（文件 → 哈希）")
    add("")
    add("| 文件 | sha256[:16] |")
    add("|---|---|")
    referenced: list[Path] = []
    for row in tickets:
        referenced.append(row["path"])
    for snapshot in snapshots:
        referenced.append(snapshot)
    for run in rules_runs:
        referenced.append(REPO / run["path"])
    referenced.append(REPO / (cfg.get("defaults") or {}).get(
        "ledger", "reports/restaurant-alerts/state/events.jsonl"))
    for path in referenced:
        if path.exists():
            add(f"| `{path}` | {_sha256_short(path)} |")
    add("")
    add("回指链：本档案 §2 工单文件 → 工单 `evidence.failing_runs` 行 → 快照 JSON 原始行"
        "（source/status/started_at/error）；§3 证据文件 → `semantic_observations`"
        "（concept + source_used + date）→ 上游源单元。")
    add("")
    return "\n".join(lines)


def cmd_duty_log(cfg: dict, asof: date, outdir: Path, tickets_dir: Path | None,
                 snapshot_paths: list[Path]) -> Path:
    day_dir = outdir / asof.isoformat()
    tickets = _load_tickets(tickets_dir) if tickets_dir else []
    rules_runs: list[dict] = []
    for path in sorted(day_dir.glob("threshold-*.json")):
        doc = _read_json(path)
        if doc:
            rules_runs.append({
                "rule": doc.get("rule"),
                "asof": doc.get("asof"),
                "replay_from": doc.get("replay_from"),
                "path": str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path),
                "evidence": doc.get("evidence", []),
            })
    events_payload = _read_json(day_dir / "events.json")

    escalations: list[dict] = []
    esc_path = REPO / (cfg.get("defaults") or {}).get(
        "escalations", "reports/restaurant-alerts/state/escalations.jsonl"
    )
    if esc_path.exists():
        for line in esc_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if tickets_dir and str(tickets_dir) not in str(row.get("tickets_dir", "")):
                continue
            escalations.append(row)

    text = render_duty_log(
        asof=asof, cfg=cfg, outdir=outdir, tickets_dir=tickets_dir, tickets=tickets,
        rules_runs=rules_runs, events_payload=events_payload,
        escalations=escalations, snapshots=snapshot_paths,
    )
    path = day_dir / "duty-log.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ── CLI ───────────────────────────────────────────────────────────────────

def _replay_from(asof: date, rules: dict, override: str | None) -> date:
    if override:
        parsed = _as_date(override)
        if parsed is None:
            raise SystemExit(f"ERROR: --replay-from 无法解析: {override!r}")
        return parsed
    months = int(rules.get("replay_months", 36))
    return _add_months(asof.replace(day=1), -months)


def cmd_rules(cur, cfg: dict, asof: date, replay_override: str | None,
              outdir: Path, dry_run: bool) -> list[dict]:
    runs: list[dict] = []
    for rule in cfg.get("rules") or []:
        if not rule.get("enabled", True):
            continue
        replay_from = _replay_from(asof, rule, replay_override or (cfg.get("defaults") or {}).get("replay_from"))
        kind = rule.get("kind")
        evidence: list[dict] = []
        if kind == "threshold":
            points, rejected = fetch_series(
                cur, rule["concept"], rule["source_used"], rule.get("entity_id"))
            evidence.append(eval_threshold_rule(rule, points, rejected, replay_from, asof))
        elif kind == "yoy_diff":
            comps: dict[str, list[tuple[str, float]]] = {}
            all_rejected: list[dict] = []
            for comp in rule.get("components") or []:
                points, rejected = fetch_series(
                    cur, comp["concept"], comp["source_used"], comp.get("entity_id"))
                comps[comp["key"]] = points
                for row in rejected:
                    row = dict(row)
                    row["component"] = comp["key"]
                    all_rejected.append(row)
            evidence.append(eval_yoy_diff_rule(
                rule, comps, {"__rejected__": all_rejected}, asof, replay_from))
        elif kind == "rolling_zscore":
            for series in rule.get("series") or []:
                points, rejected = fetch_series(
                    cur, series["concept"], series["source_used"], series.get("entity_id"))
                evidence.append(eval_zscore_series(
                    rule, series, points, rejected, replay_from, asof))
        else:
            raise SystemExit(f"ERROR: 未知规则 kind={kind!r}（rule={rule.get('name')}）")

        doc = {
            "rule": rule["name"],
            "description": rule.get("description", ""),
            "asof": asof.isoformat(),
            "replay_from": replay_from.isoformat(),
            "generated_at": _now_iso(),
            "config": str(DEFAULT_CONFIG.relative_to(REPO)),
            "evidence": evidence,
        }
        rel = Path("reports/restaurant-alerts") / asof.isoformat() / f"threshold-{rule['name']}.json"
        # outdir 可能被 --outdir 覆盖
        target_dir = outdir / asof.isoformat()
        target = target_dir / f"threshold-{rule['name']}.json"
        if not dry_run:
            _write_json(target, doc)
            try:
                rel = target.relative_to(REPO)
            except ValueError:
                rel = target
        runs.append({**doc, "path": str(rel)})
    return runs


def main() -> int:
    ap = argparse.ArgumentParser(
        description="餐饮波4 阈值级/事件级预警与值守档案（只读 DB）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--asof", default=None, help="评估基准日 YYYY-MM-DD（缺省=本机今天）")
    ap.add_argument("--replay-from", default=None, help="历史回放起点 YYYY-MM / YYYY-MM-DD（覆盖规则 replay_months）")
    ap.add_argument("--outdir", default=None, help="报告根目录（缺省 reports/restaurant-alerts）")
    ap.add_argument("--rules", action="store_true", help="评估阈值规则并写证据")
    ap.add_argument("--events", action="store_true", help="扫描事件级（幂等台账）")
    ap.add_argument("--duty-log", action="store_true", help="生成值守档案 duty-log.md")
    ap.add_argument("--escalate", action="store_true", help="升级未终态健康工单（实际投递前停住）")
    ap.add_argument("--tickets-dir", default=None, help="健康工单目录（--duty-log/--escalate）")
    ap.add_argument("--snapshot", action="append", default=[], help="遥测快照路径（写进档案回指表，可多次）")
    ap.add_argument("--health-config", default=None, help="health 配置 JSON（escalate 的总闸来源）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写文件/不发请求")
    ap.add_argument("--dsn", default=None, help="覆盖 FD_OPEN_DATA_DSN（仍只读）")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    asof = _as_date(args.asof) if args.asof else _today()
    if asof is None:
        raise SystemExit(f"ERROR: --asof 无法解析: {args.asof!r}")
    outdir = Path(args.outdir) if args.outdir else REPO / (cfg.get("defaults") or {}).get(
        "outdir", "reports/restaurant-alerts")
    outdir = Path(outdir)
    if not outdir.is_absolute():
        outdir = REPO / outdir
    tickets_dir = Path(args.tickets_dir).resolve() if args.tickets_dir else None
    snapshots = [Path(p).resolve() for p in args.snapshot]

    if not any([args.rules, args.events, args.duty_log, args.escalate]):
        ap.error("至少给一个动作：--rules / --events / --duty-log / --escalate")

    result: dict = {"at": _now_iso(), "asof": asof.isoformat(), "dry_run": args.dry_run}

    rule_states: dict[str, dict] = {}
    if args.rules or args.events:
        conn = connect(args.dsn)
        try:
            cur = conn.cursor()
            if args.rules:
                runs = cmd_rules(cur, cfg, asof, args.replay_from, outdir, args.dry_run)
                result["rules"] = [
                    {
                        "rule": run["rule"],
                        "path": run["path"],
                        "verdicts": [ev["verdict"] for ev in run["evidence"]],
                        "fired": [
                            {
                                "concept": ev["concept"],
                                "trigger_date": ev.get("trigger_date"),
                                "trigger_value": ev.get("trigger_value"),
                                "threshold": ev.get("threshold"),
                            }
                            for ev in run["evidence"] if ev["verdict"] == "fired"
                        ],
                    }
                    for run in runs
                ]
                rule_states = collect_rule_states(runs)
            if args.events:
                result["events"] = cmd_events(cur, cfg, asof, outdir, args.dry_run)
        finally:
            conn.close()

    # 投递：只在越阈集合变化时发（新增=告警，消失=恢复），避免每天重复骚扰。
    if args.rules:
        state_path = outdir / "state" / "last-fired.json"
        result["notify"] = deliver_transitions(rule_states, cfg, dry_run=args.dry_run,
                                               now=asof, state_path=state_path)

    if args.escalate:
        result["escalate"] = cmd_escalate(args, cfg, args.dry_run)

    if args.duty_log:
        path = cmd_duty_log(cfg, asof, outdir, tickets_dir, snapshots)
        result["duty_log"] = str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path)

    print(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default))
    if result.get("notify", {}).get("failed"):
        # 4 = 有告警但邮件没送出去：不能让"有人该被通知却没收到"看起来像正常。
        return 4
    if (result.get("notify", {}).get("coverage") or {}).get("mute"):
        # 6 = 覆盖度不足：所有规则都判 n/a，这条线结构上不可能告警。
        # 不判红（是数据面欠账，不是代码坏），但必须显性化，不许每天安静跑绿。
        return 6
    if result.get("escalate", {}).get("status") == "blocked":
        # 5 = 被总闸拦停（原为 2；2 已让给"脚本崩溃"，两者不能共用一个码）。
        return 5
    return 0


if __name__ == "__main__":
    # 退出码：0=正常 / 1=escalate 被总闸挡下 / 2=脚本崩溃 / 3=环境不满足 / 4=告警投递失败。
    # 未捕获异常的默认退出码也是 1，会和「被总闸挡下」撞车——崩溃看起来像正常拦停
    # （农业线 2026-11-08 踩过同一类假绿），所以这里显式映射成 2。
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 — 崩溃统一映射，栈照打
        import traceback

        traceback.print_exc()
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        if "permission denied" in str(exc).lower():
            raise SystemExit(3)
        raise SystemExit(2)
