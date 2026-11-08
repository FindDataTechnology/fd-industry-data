#!/usr/bin/env python3
"""农业线三层预警的执行面（add-agriculture-industry-sources W6 / 任务 9.2–9.5）。

分工（不重复健康级的事）：

| 级别 | 在哪判 | 本脚本的职责 |
|---|---|---|
| 健康级（该源活着吗） | `fd_industry_data/health/` + `scripts/health_inspect.py` | 只调用既有判据（`inspector.expected_period_hours` + `triage.classify`），不重写 |
| 阈值级（读数越线） | 本脚本 `eval_*` | 全部阈值读 `config/agriculture-alerts.json`，代码内无硬编码阈值 |
| 事件级（披露/公告） | 本脚本 `scan_event_rule` | 四类逐条给状态；无样本显式声明，不静默缺席 |

三条纪律（每条都对应一次会伪造数据的默认值）：

1. **不插值、不补 0、不沿用旧值**：缺期就是缺，报告里 `gaps` 明写；样本不足
   `min_history_periods` 时判 `insufficient_data`，**不当作「没越阈」**。
2. **边缘触发 + 冷却**：越阈只在进入越阈状态的那一刻通知一次，持续期抑制；
   回到区间发一次恢复；状态落 `state/agriculture-alert-state.json` 跨轮存活。
   `--dry-run` 不写状态、不写报告、不发通知（只算不落盘）。
3. **不可得的规则要留名**：`enabled: false` + `blocked_reason`，报告逐条列出
   「为什么不判」；事件级四类无样本时写 `no_sample_reason` + 解锁条件。

告警诚实性（9.4）：某规则的源不健康（序列最新期距基准日超过容忍窗，或既有健康
巡检判该源非健康）时，该规则**抑制**并标注 `suppressed: source-unhealthy` +
「信息可能不完整」；绝不用旧值冒充当前态。抑制项在报告里单列一节，与「未越阈」
严格区分。

通知（9.5）：邮件走 stdlib `smtplib`（零新依赖），env 为
`FD_ALERT_SMTP_HOST/PORT/USER/PASS/FROM/TO` + TLS 开关 `FD_ALERT_SMTP_TLS`
（`starttls` 缺省 / `ssl` / `none`）。未配置或发送失败写入 `notify_errors`，
不阻塞其它通知。触发与恢复各发一次（状态机保证），发送结果落报告与事件台账。
`--escalate` 把需处置项（越阈 / 因源不健康被抑制）写成既有 health ticket schema
的工单，可被既有 SUBMIT 消费。

用法::

    source ~/.config/finddata/dashboards.env
    export NO_PROXY='*'
    python3 scripts/agriculture_alerts.py --health            # 农业单元覆盖表
    python3 scripts/agriculture_alerts.py --rules --dry-run   # 阈值级（含边缘/冷却）
    python3 scripts/agriculture_alerts.py --events --dry-run  # 事件级四类状态
    python3 scripts/agriculture_alerts.py --rules --escalate  # 越阈/抑制项写成工单
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import math
import os
import re
import smtplib
import sys
from datetime import date, datetime, time, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import yaml  # noqa: E402

from fd_industry_data.health import config as health_config  # noqa: E402
from fd_industry_data.health import inspector, ticket as ticket_mod, triage  # noqa: E402

DEFAULT_CONFIG = REPO / "config" / "agriculture-alerts.json"


# ── 基础设施 ──────────────────────────────────────────────────────────────

def load_config(path: Path) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or "rules" not in doc:
        raise SystemExit(f"FATAL 配置不合法（缺 rules）：{path}")
    return doc


def connect(dsn: str | None = None):
    """只读连接：psycopg3 → psycopg2 → **psql 子进程**（CI runner 无驱动）。

    第三段回落是必须的：self-hosted runner 上没有 psycopg/psycopg2（2026-11-08 CI
    实测 `ModuleNotFoundError`），只实现前两段等于这条线永远连不上库。
    环境不满足（缺 DSN / 缺 SELECT 权限）一律退出 3，与「有规则越阈=1」区分开。
    """
    from fd_industry_data.pg_fallback import connect_readonly  # noqa: PLC0415

    return connect_readonly(dsn)


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _now(asof: str | None) -> datetime:
    if asof:
        text = str(asof).strip()
        if "T" in text or " " in text:
            parsed = datetime.fromisoformat(text)
        else:
            parsed = datetime.combine(date.fromisoformat(text), time.min)
        return parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


# ── 期次与序列 ────────────────────────────────────────────────────────────

def parse_period_date(raw: str) -> date | None:
    """把 ``YYYY`` / ``YYYY-MM`` / ``YYYY-MM-DD`` / ``YYYYQn`` 解析成日期。

    解析失败返回 None（**不猜**）；调用方应把该行计入 rejected，不静默丢。
    """
    text = str(raw or "").strip()
    if not text:
        return None
    m = re.match(r"^(\d{4})-?Q([1-4])$", text, re.IGNORECASE)
    if m:
        year, quarter = int(m.group(1)), int(m.group(2))
        month = quarter * 3
        return date(year, month, calendar.monthrange(year, month)[1])
    if re.match(r"^\d{4}$", text):
        return date(int(text), 12, 31)
    if re.match(r"^\d{4}-\d{2}$", text):
        year, month = (int(part) for part in text.split("-"))
        return date(year, month, calendar.monthrange(year, month)[1])
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def period_key(day: date, periodicity: str) -> str:
    if periodicity == "quarter":
        return f"{day.year}Q{(day.month - 1) // 3 + 1}"
    if periodicity == "month":
        return f"{day.year}-{day.month:02d}"
    if periodicity == "year":
        return str(day.year)
    if periodicity == "week":
        iso = day.isocalendar()
        return f"{iso.year}W{iso.week:02d}"
    return day.isoformat()


def period_ordinal(day: date, periodicity: str) -> int:
    if periodicity == "quarter":
        return day.year * 4 + (day.month - 1) // 3
    if periodicity == "month":
        return day.year * 12 + day.month - 1
    if periodicity == "year":
        return day.year
    if periodicity == "week":
        return day.toordinal() // 7
    return day.toordinal()


def _enumerate_period_keys(start: date, end: date, periodicity: str):
    if periodicity == "quarter":
        for ordinal in range(period_ordinal(start, periodicity),
                             period_ordinal(end, periodicity) + 1):
            year, quarter = divmod(ordinal, 4)
            yield f"{year}Q{quarter + 1}"
        return
    if periodicity == "month":
        for ordinal in range(period_ordinal(start, periodicity),
                             period_ordinal(end, periodicity) + 1):
            year, month = divmod(ordinal, 12)
            yield f"{year}-{month + 1:02d}"
        return
    if periodicity == "year":
        for year in range(start.year, end.year + 1):
            yield str(year)
        return
    if periodicity == "week":
        for ordinal in range(period_ordinal(start, periodicity),
                             period_ordinal(end, periodicity) + 1):
            day = date.fromordinal(ordinal * 7)
            iso = day.isocalendar()
            yield f"{iso.year}W{iso.week:02d}"
        return
    for ordinal in range(start.toordinal(), end.toordinal() + 1):
        yield date.fromordinal(ordinal).isoformat()


def detect_gaps(points: list[tuple[str, float]], periodicity: str,
                gap_tolerance_days: int | None = None) -> dict:
    """缺期/长间断如实登记；不插值、不补 0、不沿用旧值。

    - 季/月/年：枚举首末之间的全部期次，缺的逐条列出。
    - 日频：周末/节假日是源侧不发布，只列超过 ``gap_tolerance_days`` 的间断，
      不把每个周末都当作「缺口」。
    """
    dates = [d for d in (parse_period_date(raw) for raw, _ in points) if d is not None]
    if len(dates) < 2:
        return {"missing": [], "count": 0,
                "note": "期次不足两期，无法判断缺期（不是「无缺口」）"}
    observed = {period_key(day, periodicity) for day in dates}
    if periodicity == "day" and gap_tolerance_days is not None:
        long_gaps = []
        for before, after in zip(dates, dates[1:]):
            delta = (after - before).days
            if delta > int(gap_tolerance_days):
                long_gaps.append({"after": before.isoformat(),
                                  "before": after.isoformat(), "days": delta})
        return {"missing": [], "long_gaps": long_gaps, "count": len(long_gaps),
                "max_gap": (max(long_gaps, key=lambda g: g["days"]) if long_gaps else None),
                "tolerance_days": int(gap_tolerance_days),
                "note": "交易日序列：周末/节假日为源侧不发布；只列超过容忍天的间断（非缺口）"}
    expected = list(_enumerate_period_keys(min(dates), max(dates), periodicity))
    missing = [key for key in expected if key not in observed]
    return {"missing": missing, "count": len(missing),
            "note": "缺期即缺，不插值、不补 0、不沿用旧值"}


def series(cur, concept: str, source_used: str) -> tuple[list[tuple[str, float]], list[dict]]:
    """一条序列的 (date, value) 升序 + 非数值拒绝清单（不静默丢）。"""
    cur.execute(
        """
        SELECT o.date, o.value FROM semantic_observations o
        JOIN concepts c ON c.id = o.concept_id
        WHERE c.code = %s AND o.source_used = %s
        ORDER BY o.date, o.fetched_at
        """,
        (concept, source_used),
    )
    deduped: dict[str, float] = {}
    rejected: list[dict] = []
    for raw_date, raw_value in cur.fetchall():
        key = str(raw_date)[:10] if raw_date is not None else ""
        try:
            deduped[key] = float(str(raw_value).replace(",", ""))
        except (TypeError, ValueError):
            rejected.append({"date": key, "raw_value": str(raw_value)})
    return sorted(deduped.items()), rejected


def source_latest(cur, source_used: str) -> tuple[date | None, int, int]:
    """该 source_used 在观测面的最新数据期 + 行数 + 不可解析期次数。"""
    cur.execute("SELECT date FROM semantic_observations WHERE source_used = %s", (source_used,))
    latest: date | None = None
    count = 0
    unparsed = 0
    for (raw,) in cur.fetchall():
        count += 1
        parsed = parse_period_date(raw)
        if parsed is None:
            unparsed += 1
            continue
        if latest is None or parsed > latest:
            latest = parsed
    return latest, count, unparsed


# ── manifest / 遥测 / 健康级（只调用既有判据） ─────────────────────────────

def manifest_path(source: str) -> Path:
    return REPO / "spiders" / source / "manifest.yaml"


def manifest_doc(source: str) -> dict:
    path = manifest_path(source)
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return {}


def _unlit_reason(source: str) -> str:
    """从 manifest 的注释里取「为什么不写 schedule」；取不到就给统一措辞。

    manifest 的 YAML 注释在 safe_load 后会丢，所以读原文。这不是猜：
    manifest 自己写了原因（如「不写 schedule：先静默合入」）。
    """
    path = manifest_path(source)
    if not path.exists():
        return "manifest 缺失（无法判断点亮状态）"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "manifest 不可读"
    for line in lines:
        text = line.strip()
        if not text.startswith("#"):
            continue
        body = text.lstrip("#").strip()
        if "不写 schedule" not in body and not body.lower().startswith("schedule: 不写"):
            continue
        body = body.replace("不写 schedule", "").replace("schedule: 不写", "")
        body = body.lstrip("：:—- ").strip()
        if body:
            return body
    return "manifest 未声明 schedule（未点亮；不判停更）"


def telemetry_runs(cur, source: str, since: datetime) -> list[triage.Run]:
    cur.execute(
        """
        SELECT status, started_at, rows_written, COALESCE(error_head, '')
        FROM crawl_runs WHERE source = %s AND started_at >= %s
        ORDER BY started_at
        """,
        (source, since),
    )
    out: list[triage.Run] = []
    for status, started_at, rows, error in cur.fetchall():
        out.append(triage.Run(
            started_at=started_at.isoformat() if hasattr(started_at, "isoformat") else str(started_at),
            status=str(status or "failed"),
            rows=int(rows) if rows is not None else None,
            error=str(error or ""),
            http_status=None,
            watermark=None,
        ))
    return out


def health_snapshot(cur, sources: list[str], now: datetime, lookback_days: int) -> dict[str, dict]:
    """对在册排期源跑既有健康级判据；未点亮的源**不判停更**（明确返回 unlit）。"""
    cfg = health_config.load_config(None)
    since = now - timedelta(days=int(lookback_days))
    out: dict[str, dict] = {}
    for source in sources:
        doc = manifest_doc(source)
        schedule = " ".join(str(doc.get("schedule") or "").split())
        parked = doc.get("parked") is True
        if not schedule or parked:
            out[source] = {
                "status": "unlit",
                "reason": ("manifest parked: true（有意挂起，不判停更）" if parked
                           else _unlit_reason(source)),
                "trace": "",
                "runs": 0,
                "last_run": None,
                "last_success": None,
            }
            continue
        period = inspector.expected_period_hours(REPO, source, cfg)
        if period is None:
            out[source] = {
                "status": "unknown-period",
                "reason": "manifest 未声明可推断周期（健康级跳过，不猜）",
                "trace": "",
                "runs": 0,
                "last_run": None,
                "last_success": None,
            }
            continue
        runs = telemetry_runs(cur, source, since)
        verdict = triage.classify(
            source, f"spiders/{source}/", period, runs, now, cfg.thresholds,
            scheduled_absent=not runs,
        )
        status = ("healthy" if verdict.category is None and not verdict.needs_human
                  else (verdict.category or "needs-human"))
        last_run = runs[-1].started_at if runs else None
        last_success = next((r.started_at for r in reversed(runs) if r.status == "success"), None)
        out[source] = {
            "status": status,
            "category": verdict.category,
            "needs_human": verdict.needs_human,
            "suggested_action": verdict.suggested_action,
            "trace": verdict.rule_trace,
            "runs": len(runs),
            "last_run": last_run,
            "last_success": last_success,
        }
    return out


# ── 阈值级评估器（所有阈值读 config，代码零硬编码） ────────────────────────

def eval_band(points: list[tuple[str, float]], rule: dict) -> dict:
    """区间判据：``[low, high]``，含端点算区间内；越界仅指 <low 或 >high。"""
    min_history = int(rule.get("min_history_periods", 1))
    if len(points) < min_history:
        return {"verdict": "insufficient_data",
                "detail": f"该序列 {len(points)} 期 < min_history_periods={min_history}，不判（不是「没越阈」）"}
    raw_date, raw_value = points[-1]
    day = parse_period_date(raw_date)
    if day is None:
        return {"verdict": "insufficient_data", "detail": f"最新期 {raw_date!r} 无法解析为日期"}
    thresholds = rule["thresholds"]
    low, high = float(thresholds["low"]), float(thresholds["high"])
    inclusive = bool(thresholds.get("inclusive", True))
    value = float(raw_value)

    def outside(candidate: float) -> bool:
        return (candidate < low or candidate > high) if inclusive \
            else (candidate <= low or candidate >= high)

    crossed = outside(value)
    side = "below" if value < low else ("above" if value > high else None)
    history = []
    for raw_day, raw_v in points:
        parsed = parse_period_date(raw_day)
        try:
            candidate = float(raw_v)
        except (TypeError, ValueError):
            continue
        if parsed is not None and outside(candidate):
            history.append({"period": period_key(parsed, rule["periodicity"]),
                            "date": raw_day, "value": candidate})
    return {
        "verdict": "crossed" if crossed else "quiet",
        "value": value,
        "unit": rule.get("unit"),
        "period": period_key(day, rule["periodicity"]),
        "period_date": raw_date,
        "threshold": {"low": low, "high": high, "inclusive": inclusive},
        "side": side,
        "history_out_of_band": history,
        "basis": {
            "latest_period": period_key(day, rule["periodicity"]),
            "latest_date": raw_date,
            "low": low, "high": high, "inclusive": inclusive,
            "relation": ("inside" if not crossed else f"outside-{side}"),
        },
        "gaps": detect_gaps(points, rule["periodicity"]),
    }


def eval_rolling_percentile(points: list[tuple[str, float]], rule: dict) -> dict:
    """当前值在滚动窗（不含当日）里的分位；阈值是配置分位，不是元/吨绝对值。"""
    if len(points) < 2:
        return {"verdict": "insufficient_data", "detail": "序列不足两期，无法构造滚动窗"}
    raw_date, raw_value = points[-1]
    day = parse_period_date(raw_date)
    if day is None:
        return {"verdict": "insufficient_data", "detail": f"最新期 {raw_date!r} 无法解析为日期"}
    window_days = int(rule["window_days"])
    percentile = float(rule["percentile"])
    min_history = int(rule.get("min_history_periods", 1))
    window_start = day - timedelta(days=window_days)
    window: list[tuple[date, float]] = []
    for raw_day, raw_v in points[:-1]:
        parsed = parse_period_date(raw_day)
        if parsed is None or not (window_start <= parsed < day):
            continue
        try:
            window.append((parsed, float(raw_v)))
        except (TypeError, ValueError):
            continue
    if len(window) < min_history:
        return {"verdict": "insufficient_data",
                "detail": f"滚动 {window_days} 天窗口仅 {len(window)} 点 < min_history_periods="
                          f"{min_history}，不判（不是「没越阈」）",
                "basis": {"window_days": window_days, "window_samples": len(window)}}
    values = [v for _, v in window]
    value = float(raw_value)
    rank_pct = 100.0 * sum(1 for v in values if v < value) / len(values)
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile / 100.0 * len(ordered)) - 1))
    threshold_value = ordered[index]
    crossed = rank_pct >= percentile
    return {
        "verdict": "crossed" if crossed else "quiet",
        "value": value,
        "unit": rule.get("unit"),
        "period": period_key(day, rule["periodicity"]),
        "period_date": raw_date,
        "threshold": {"percentile": percentile, "value_at_percentile": threshold_value},
        "percentile_rank": round(rank_pct, 2),
        "basis": {
            "latest_period": period_key(day, rule["periodicity"]),
            "latest_date": raw_date,
            "window_days": window_days,
            "window_from": window[0][0].isoformat(),
            "window_to": window[-1][0].isoformat(),
            "window_samples": len(window),
            "percentile": percentile,
            "value_at_percentile": threshold_value,
            "percentile_rank": round(rank_pct, 2),
        },
        "gaps": detect_gaps(points, rule["periodicity"],
                            rule.get("gap_tolerance_days")),
    }


EVALUATORS = {
    "band": eval_band,
    "rolling_percentile": eval_rolling_percentile,
}


# ── 边缘触发 + 冷却（按周期，季频源按季） ──────────────────────────────────

def apply_state(prev: dict, verdict: str, *, now: datetime, cooldown_periods: int,
                period_ordinal_value: int, value: float | None = None,
                period: str | None = None) -> tuple[str, dict]:
    """把本轮读数喂进状态机，返回 (action, new_state)。

    ``action``：``alert``（进入越阈）/ ``recover``（回到阈值内）/ ``suppress``
    （持续越阈或冷却期内的重复越阈，不打扰）/ ``idle``（一直正常）。

    冷却窗按**周期**计（`cooldown_periods`）：季频判据恢复后 1 个季度内即使再次
    越阈也不再通知——季报数据抖动不该变成连环告警。`--dry-run` 不写状态。
    """
    state = dict(prev or {})
    current = state.get("state", "quiet")
    cooldown_until = state.get("cooldown_until_period")
    in_cooldown = (cooldown_until is not None
                   and period_ordinal_value <= int(cooldown_until))

    if verdict == "crossed":
        if current == "firing":
            state["last_value"] = value
            state["period_ordinal"] = period_ordinal_value
            return "suppress", state
        if in_cooldown:
            state["last_value"] = value
            state["period_ordinal"] = period_ordinal_value
            return "suppress", state
        state.update({
            "state": "firing",
            "since": now.isoformat(),
            "last_notified_at": now.isoformat(),
            "episode": int(state.get("episode", 0)) + 1,
            "last_value": value,
            "period": period,
            "period_ordinal": period_ordinal_value,
        })
        return "alert", state

    if verdict == "quiet":
        if current == "firing":
            until = period_ordinal_value + max(0, int(cooldown_periods))
            state.update({
                "state": "cooldown",
                "recovered_at": now.isoformat(),
                "last_notified_at": now.isoformat(),
                "last_value": value,
                "period": period,
                "period_ordinal": period_ordinal_value,
                "cooldown_until_period": until,
            })
            return "recover", state
        if current == "cooldown" and not in_cooldown:
            state["state"] = "quiet"
            state.pop("cooldown_until_period", None)
        state.setdefault("state", "quiet")
        state["last_value"] = value
        state["period"] = period
        state["period_ordinal"] = period_ordinal_value
        return "idle", state

    # insufficient_data 等：不改状态（数据不足既不能开也不能关一个告警）
    return "suppress", state


# ── 告警诚实性：源不健康则抑制 ────────────────────────────────────────────

def check_source_guard(rule: dict, points: list[tuple[str, float]],
                       source_latest_date: date | None, source_health: dict | None,
                       asof: date) -> dict:
    guard = rule.get("source_guard") or {}
    if guard.get("enabled", True) is False:
        return {"suppressed": False, "reasons": [], "basis": {"enabled": False}}
    reasons: list[str] = []
    basis: dict = {}

    if points:
        latest_raw = points[-1][0]
        parsed = parse_period_date(latest_raw)
        if parsed is not None:
            age = (asof - parsed).days
            max_age = guard.get("max_period_age_days")
            basis["series_latest"] = latest_raw
            basis["series_age_days"] = age
            basis["max_period_age_days"] = max_age
            if max_age is not None and age > float(max_age):
                reasons.append(
                    f"序列最新期 {latest_raw} 距基准日 {age} 天 > max_period_age_days={max_age}")

    if source_latest_date is not None:
        age = (asof - source_latest_date).days
        max_age = guard.get("max_source_age_days", guard.get("max_period_age_days"))
        basis["source_latest"] = source_latest_date.isoformat()
        basis["source_age_days"] = age
        basis["max_source_age_days"] = max_age
        if max_age is not None and age > float(max_age):
            reasons.append(
                f"源最新数据期 {source_latest_date.isoformat()} 距基准日 {age} 天 > "
                f"max_source_age_days={max_age}")

    if guard.get("check_telemetry", True):
        basis["telemetry_health"] = (source_health or {}).get("status")
        if source_health is None:
            basis["telemetry_note"] = "该源不在在册排期面，未取得健康巡检结论（不据此判非健康）"
        elif source_health.get("status") != "healthy":
            reasons.append(
                f"健康巡检判该源非健康：{source_health.get('status')}"
                f"；{source_health.get('trace') or source_health.get('reason') or ''}")

    return {"suppressed": bool(reasons), "reasons": reasons, "basis": basis}


# ── 通知（stdlib smtplib） ────────────────────────────────────────────────

def _smtp_config(timeout_default: float = 15.0) -> dict:
    tls = os.environ.get("FD_ALERT_SMTP_TLS", "starttls").strip().lower()
    if tls not in ("starttls", "ssl", "none"):
        tls = "starttls"
    recipients = [part.strip() for part in re.split(r"[,;]", os.environ.get("FD_ALERT_SMTP_TO", ""))
                  if part.strip()]
    return {
        "host": os.environ.get("FD_ALERT_SMTP_HOST", "").strip(),
        "port": int(os.environ.get("FD_ALERT_SMTP_PORT", "25") or 25),
        "user": os.environ.get("FD_ALERT_SMTP_USER", "").strip(),
        "password": os.environ.get("FD_ALERT_SMTP_PASS", ""),
        "from": (os.environ.get("FD_ALERT_SMTP_FROM", "").strip()
                 or os.environ.get("FD_ALERT_SMTP_USER", "").strip()
                 or "alerts@localhost"),
        "to": recipients,
        "tls": tls,
        "timeout": float(os.environ.get("FD_ALERT_SMTP_TIMEOUT", "") or timeout_default),
    }


def send_email(subject: str, body: str, timeout_default: float = 15.0) -> dict:
    """发一封邮件；**任何失败都返回记录，不抛异常**（不阻塞同批其它通知）。"""
    try:
        cfg = _smtp_config(timeout_default)
    except Exception as exc:  # noqa: BLE001 — 端口/超时等配置错误也必须留痕
        return {"channel": "email", "status": "error",
                "error": f"SMTP 配置错误：{type(exc).__name__}: {exc}"[:400]}
    if not cfg["host"]:
        return {"channel": "email", "status": "error",
                "error": "SMTP 未配置：FD_ALERT_SMTP_HOST 为空（已留记录，不阻塞其它通知）"}
    if not cfg["to"]:
        return {"channel": "email", "status": "error",
                "error": "SMTP 未配置：FD_ALERT_SMTP_TO 为空（已留记录，不阻塞其它通知）"}
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = cfg["from"]
    message["To"] = ", ".join(cfg["to"])
    message.set_content(body)
    server = None
    try:
        if cfg["tls"] == "ssl":
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=cfg["timeout"])
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=cfg["timeout"])
            if cfg["tls"] == "starttls":
                server.starttls()
        if cfg["user"]:
            server.login(cfg["user"], cfg["password"])
        server.send_message(message)
        return {"channel": "email", "status": "sent",
                "host": cfg["host"], "port": cfg["port"],
                "to": cfg["to"], "tls": cfg["tls"]}
    except Exception as exc:  # noqa: BLE001 — 发送失败必须留痕，不能炸整批
        return {"channel": "email", "status": "error",
                "error": f"{type(exc).__name__}: {exc}"[:400],
                "host": cfg["host"], "port": cfg["port"],
                "to": cfg["to"], "tls": cfg["tls"]}
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:  # noqa: BLE001
                pass


def build_notification(rule: dict, result: dict, action: str, now: datetime,
                       prev_state: dict) -> dict:
    label = "触发" if action == "alert" else "恢复"
    subject = f"[农业预警] {label} {rule['name']} {result.get('period')}"
    lines = [
        f"农业预警（阈值级）—— {label}",
        "",
        f"规则：{rule['name']}",
        f"说明：{rule.get('description', '')}",
        f"动作：{action}",
        f"读数：{result.get('value')} {result.get('unit', '')}",
        f"期次：{result.get('period')}（数据期 {result.get('period_date', '')}）",
        f"阈值/口径：{json.dumps(result.get('threshold'), ensure_ascii=False)}",
        f"判据出处：{rule.get('citation', '')}",
        f"版本：{rule.get('citation_version', '')}",
        f"源：{rule.get('source_used')}（健康级判据：healthy）",
        f"基准日：{now.date().isoformat()}",
        f"信息可能不完整：否（源健康；本轮读数新鲜）",
    ]
    if result.get("basis"):
        lines.append(f"计算依据：{json.dumps(result['basis'], ensure_ascii=False)}")
    if prev_state.get("since"):
        lines.append(f"上一轮 firing 起始：{prev_state['since']}")
    lines += ["", "本引擎不自动改数据；请核对读数与口径。"]
    return {
        "rule": rule["name"],
        "action": action,
        "subject": subject,
        "body": "\n".join(lines),
        "source": rule.get("source_used"),
        "period": result.get("period"),
        "value": result.get("value"),
        "unit": result.get("unit"),
    }


def dispatch_notifications(items: list[dict], dry_run: bool,
                            timeout_default: float = 15.0) -> tuple[list[dict], list[dict]]:
    """逐条发送；单条失败只记 `notify_errors`，继续下一条。"""
    notifications: list[dict] = []
    errors: list[dict] = []
    for item in items:
        if dry_run:
            result = {"channel": "email", "status": "dry-run",
                      "note": "dry-run 不发邮件"}
        else:
            try:
                result = send_email(item["subject"], item["body"], timeout_default)
            except Exception as exc:  # noqa: BLE001 — 单条失败绝不阻塞同批
                result = {"channel": "email", "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"[:400]}
        record = {
            "rule": item["rule"],
            "action": item["action"],
            "source": item.get("source"),
            "period": item.get("period"),
            "value": item.get("value"),
            "unit": item.get("unit"),
            "results": [result],
        }
        notifications.append(record)
        if result.get("status") == "error":
            errors.append({
                "rule": item["rule"],
                "action": item["action"],
                "channel": result.get("channel", "email"),
                "error": result.get("error", "unknown"),
            })
    return notifications, errors


# ── 事件级（9.3） ─────────────────────────────────────────────────────────

def scan_event_rule(cur, rule: dict, asof: date) -> list[dict]:
    """按配置的 jsonb 匹配条件扫 `crawl_items`（与看板事件图同源）。"""
    match = rule.get("payload_match") or {}
    if not match:
        return []
    cur.execute(
        "SELECT payload FROM crawl_items WHERE payload @> %s::jsonb ORDER BY idx",
        (json.dumps(match, ensure_ascii=False),),
    )
    date_field = rule.get("date_field", "date")
    window_days = int(rule.get("window_days", 90))
    since = asof - timedelta(days=window_days)
    out: list[dict] = []
    for (payload,) in cur.fetchall():
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                continue
        if not isinstance(payload, dict):
            continue
        raw_day = str(payload.get(date_field) or "")[:10]
        parsed = parse_period_date(raw_day)
        if parsed is None or parsed < since:
            continue
        template = rule.get("key_template")
        if template:
            try:
                key = template.format(**{k: payload.get(k) for k in payload})
            except (KeyError, IndexError, ValueError):
                key = ""
        else:
            key = ""
        if not key:
            key = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        out.append({"key": key, "rule": rule["name"], "label": rule.get("label"),
                    "source": match.get("source"), "date": raw_day,
                    "payload": payload, "level": rule.get("level", "info")})
    return out


def append_ledger(path: Path, rows: list[dict]) -> tuple[list[dict], int]:
    """幂等落账：返回 (新增行, 已存在条数)。"""
    seen: set[str] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                seen.add(json.loads(line)["key"])
            except (json.JSONDecodeError, KeyError):
                continue
    new = [row for row in rows if row["key"] not in seen]
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for row in new:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return new, len(rows) - len(new)


def append_alert_events(path: Path, events: list[dict]) -> None:
    if not events:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


# ── 工单升级（沿既有 health ticket schema） ───────────────────────────────

def _ticket_for(item: dict, now: datetime) -> dict:
    rule = item["rule"]
    result = item["result"]
    kind = item.get("kind", "alert")
    source = rule["source_used"]
    period = str(result.get("period") or "")
    if kind == "suppressed":
        reasons = "；".join((result.get("source_guard") or {}).get("reasons") or [])
        notes = (f"级别=阈值级-抑制；源不健康/数据过期，信息可能不完整；"
                 f"原因={reasons}；未用旧值冒充当前态；规则={rule['name']}；"
                 f"出处={rule.get('citation', '')}（{rule.get('citation_version', '')}）")
        action = ("先按健康级修复源（python3 scripts/health_inspect.py --from-pg --emit），"
                  "源恢复健康后再复核阈值读数；本引擎不自动改数据")
        error_summary = f"阈值告警抑制 {rule['name']}：{reasons}"
    else:
        notes = (f"级别=阈值级；依据={rule.get('description', rule['name'])}；"
                 f"读数={result.get('value')} {result.get('unit', '')}；"
                 f"期={period}；阈值={json.dumps(result.get('threshold'), ensure_ascii=False)}；"
                 f"出处={rule.get('citation', '')}（{rule.get('citation_version', '')}）；"
                 f"归因=数据侧越线，非抓取故障")
        action = (f"核对 {rule['name']} 的读数与口径（{rule.get('description', '')}）；"
                  "阈值级告警不自动改数据")
        error_summary = (f"阈值告警 {rule['name']}：读数 {result.get('value')} "
                         f"{result.get('unit', '')} 越阈（期 {period}）")
    evidence = {
        "window": {"from": period, "to": period},
        "failing_runs": [{
            "started_at": now.isoformat(),
            "status": "failed",
            "rows": None,
            "error_summary": error_summary,
            "http_status": None,
        }],
        "last_success": None,
        "watermark": {"latest": period, "basis": "告警读数所在期"},
        "notes": notes,
    }
    return ticket_mod.new_ticket(
        source=source,
        unit=f"spiders/{source}/",
        kind="repair",
        category="contract",
        evidence=evidence,
        verify_commands=[
            f"python3 scripts/agriculture_alerts.py --rules --asof {now.date().isoformat()}",
            "python3 scripts/health_inspect.py --from-pg --emit",
        ],
        suggested_action=action,
        brief={"rule": rule["name"], "level": "threshold", "config": str(DEFAULT_CONFIG)},
        created_at=now.isoformat(),
    )


def escalate(items: list[dict], outdir: Path, now: datetime, dry_run: bool) -> list[str]:
    """把越阈/抑制项写成工单文件（沿用 health ticket schema，可被既有 SUBMIT 消费）。"""
    tickets_dir = outdir / "tickets"
    written: list[str] = []
    for item in items:
        doc = _ticket_for(item, now)
        errors = ticket_mod.validate_ticket(doc)
        if errors:
            raise SystemExit(f"FATAL 工单不合法（{item['name']}）：{errors}")
        day = doc["created_at"][:10].replace("-", "")
        short = hashlib.sha1(
            f"{doc['source']}|{item['name']}|{doc['created_at']}".encode("utf-8")
        ).hexdigest()[:8]
        filename = ticket_mod.ticket_filename(day, doc["source"], short)
        doc["ticket_id"] = filename[:-5]
        if not dry_run:
            tickets_dir.mkdir(parents=True, exist_ok=True)
            (tickets_dir / filename).write_text(ticket_mod.dump_ticket(doc), encoding="utf-8")
        written.append(filename)
    return written


# ── 命令：健康级覆盖表 ────────────────────────────────────────────────────

def _health_row(cur, source: str, now: datetime, lookback_days: int, health: dict) -> dict:
    doc = manifest_doc(source)
    schedule = " ".join(str(doc.get("schedule") or "").split())
    parked = doc.get("parked") is True
    data_period = doc.get("data_period")
    label = doc.get("label") or source
    authority = doc.get("authority")
    latest, obs_count, unparsed = source_latest(cur, source)
    runs = telemetry_runs(cur, source, now - timedelta(days=int(lookback_days)))
    last_success = next((r.started_at for r in reversed(runs) if r.status == "success"), None)
    registered = bool(schedule) and not parked

    if not registered:
        status = "未点亮"
        reason = ("manifest parked: true（有意挂起，不判停更）" if parked
                  else _unlit_reason(source))
        note = "未点亮的源不判停更（无 schedule ≠ 停更），避免误报"
        health_entry = None
    else:
        status = "已点亮" if runs else "已点亮（遥测静默）"
        reason = ""
        note = ("遥测窗内零运行：健康级会按「排期在册但整窗没跑」分诊；本命令只报不掩盖"
                if not runs else "")
        health_entry = health.get(source)
        if obs_count == 0:
            note = (note + "；" if note else "") + "观测面无该 source_used 序列（排期在册 ≠ 已落数）"

    return {
        "source": source,
        "label": label,
        "authority": authority,
        "data_period": data_period,
        "schedule": schedule,
        "parked": parked,
        "registered": registered,
        "lit": registered and bool(runs),
        "telemetry_silent": registered and not runs,
        "status": status,
        "unlit_reason": reason,
        "note": note,
        "telemetry": {
            "runs": len(runs),
            "last_run": runs[-1].started_at if runs else None,
            "last_success": last_success,
        },
        "observations": {
            "rows": obs_count,
            "latest": latest.isoformat() if latest else None,
            "age_days": (now.date() - latest).days if latest else None,
            "unparsed_periods": unparsed,
        },
        "health": health_entry,
    }


def cmd_health(cur, cfg: dict, outdir: Path, now: datetime, dry_run: bool) -> int:
    defaults = cfg.get("defaults") or {}
    lookback = int(defaults.get("telemetry_lookback_days", 30))
    units = list(cfg.get("units") or [])
    extra = [source for source in (cfg.get("rule_source_units") or []) if source not in units]
    health = health_snapshot(cur, units + extra, now, lookback)
    rows = [_health_row(cur, source, now, lookback, health) for source in units]
    extra_rows = [_health_row(cur, source, now, lookback, health) for source in extra]

    print(f"== 农业单元覆盖（基准日 {now.date().isoformat()}，遥测回看 {lookback}d） ==")
    print(f"{'source':<26} {'data_period':<12} {'schedule':<14} {'状态':<20} "
          f"{'遥测':<22} 观测面")
    for row in rows:
        telemetry = (f"{row['telemetry']['runs']} runs; last success "
                     f"{row['telemetry']['last_success'] or '—'}")
        observations = (f"{row['observations']['rows']} rows; latest "
                        f"{row['observations']['latest'] or '—'}")
        print(f"{row['source']:<26} {str(row['data_period'] or '—'):<12} "
              f"{(row['schedule'] or '(none)'):<14} {row['status']:<20} "
              f"{telemetry:<22} {observations}")
        if row["health"]:
            print(f"{'':<26} 健康级：{row['health'].get('status')}  {row['health'].get('trace', '')}")
        if row["note"]:
            print(f"{'':<26} 说明：{row['note']}")

    unlit = [row for row in rows if not row["registered"]]
    registered = [row for row in rows if row["registered"]]
    lit = [row for row in registered if row["lit"]]
    silent = [row for row in registered if row["telemetry_silent"]]
    if unlit:
        print(f"\n-- 未点亮原因（{len(unlit)}）--")
        for row in unlit:
            print(f"- {row['source']}: {row['unlit_reason']}")
    print("\n说明：未点亮的源不判停更（无 schedule ≠ 停更），避免把「未点亮」误报成「停更」。")
    print(f"== 汇总 == 在册排期源 {len(registered)} / 已点亮 {len(lit)} / "
          f"未点亮 {len(unlit)} / 遥测静默 {len(silent)}")
    if silent:
        print("遥测静默：" + ", ".join(row["source"] for row in silent))

    if extra_rows:
        print("\n-- 规则源依赖（不在农业单元清单内，但阈值规则读取） --")
        for row in extra_rows:
            print(f"{row['source']}: data_period={row['data_period']} schedule="
                  f"{row['schedule'] or '(none)'} 状态={row['status']} "
                  f"遥测={row['telemetry']['runs']} runs "
                  f"观测={row['observations']['rows']} rows latest="
                  f"{row['observations']['latest'] or '—'}")
            if row["health"]:
                print(f"    健康级：{row['health'].get('status')}  "
                      f"{row['health'].get('trace', '')}")

    if not dry_run:
        _write_json(outdir / f"health-{now.date().isoformat()}.json", {
            "at": now.isoformat(),
            "base_date": now.date().isoformat(),
            "lookback_days": lookback,
            "units": rows,
            "rule_source_units": extra_rows,
            "summary": {
                "registered": len(registered),
                "lit": len(lit),
                "unlit": len(unlit),
                "telemetry_silent": len(silent),
            },
            "notes": [
                "未点亮的源不判停更（无 schedule ≠ 停更）",
                "在册排期 ≠ 已落数：观测面无序列的源单独标注",
            ],
        })
    return 0


# ── 命令：阈值级 ──────────────────────────────────────────────────────────

def _print_rule_header(item: dict) -> None:
    rule = item["rule"]
    print(f"    出处：{rule.get('citation', '')}（{rule.get('citation_version', '')}）")


def cmd_rules(cur, cfg: dict, outdir: Path, now: datetime, dry_run: bool,
              do_escalate: bool, state_path: Path, ledger_path: Path) -> int:
    defaults = cfg.get("defaults") or {}
    state = _read_json(state_path, {"rules": {}})
    rule_sources = sorted({
        rule.get("source_used") for rule in cfg["rules"]
        if rule.get("enabled", True) and rule.get("source_used")
    })
    health = health_snapshot(cur, rule_sources, now,
                             int(defaults.get("telemetry_lookback_days", 30)))

    judged: list[dict] = []
    insufficient: list[dict] = []
    suppressed: list[dict] = []
    disabled: list[dict] = []
    notifications: list[dict] = []
    transitions: list[dict] = []
    escalate_items: list[dict] = []
    exit_code = 0

    print(f"== 阈值级（基准日 {now.date().isoformat()}；冷却按周期） ==")
    for rule in cfg["rules"]:
        name = rule["name"]
        if not rule.get("enabled", True):
            item = {"name": name, "verdict": "disabled",
                    "blocked_reason": rule.get("blocked_reason", "未声明原因"),
                    "unlock": rule.get("unlock", ""),
                    "citation": rule.get("citation"),
                    "citation_version": rule.get("citation_version")}
            disabled.append(item)
            print(f"[DISABLED] {name}: 不判 —— {item['blocked_reason']}")
            if item["unlock"]:
                print(f"           解锁：{item['unlock']}")
            continue

        notes: list[str] = []
        evaluator = EVALUATORS.get(rule["kind"])
        if evaluator is None:
            raise SystemExit(f"FATAL 未知 kind={rule['kind']!r}（规则 {name}）")
        points, rejected = series(cur, rule["concept"], rule["source_used"])
        if rejected:
            notes.append(f"{len(rejected)} 个非数值读数被排除（不当作 0）")
        # 最新一期如果是非数值，不能拿上一期的数值冒充当前态（纪律 1：不沿用旧值）。
        latest_numeric = parse_period_date(points[-1][0]) if points else None
        latest_rejected = max(
            (parse_period_date(row.get("date")) for row in rejected
             if parse_period_date(row.get("date")) is not None),
            default=None,
        )
        if latest_rejected is not None and (
                latest_numeric is None or latest_rejected > latest_numeric):
            detail = (f"最新期 {latest_rejected.isoformat()} 为非数值读数；"
                      f"不能用上一期 {points[-1][0] if points else '（无）'} 冒充当前态，不判"
                      f"（不是「没越阈」）")
            insufficient.append({"name": name, "rule": rule,
                                 "verdict": "insufficient_data",
                                 "detail": detail, "notes": notes})
            print(f"[INSUFFICIENT] {name}: {detail}")
            continue
        if not points:
            item = {"name": name, "rule": rule, "verdict": "insufficient_data",
                    "detail": "该序列零观测（不判，不是「没越阈」）", "notes": notes}
            insufficient.append(item)
            print(f"[INSUFFICIENT] {name}: {item['detail']}")
            continue

        result = evaluator(points, rule)
        result["notes"] = notes
        if result["verdict"] == "insufficient_data":
            insufficient.append({"name": name, "rule": rule, **result})
            print(f"[INSUFFICIENT] {name}: {result.get('detail')}")
            continue

        latest_source_date, obs_count, unparsed = source_latest(cur, rule["source_used"])
        guard = check_source_guard(rule, points, latest_source_date,
                                   health.get(rule["source_used"]), now.date())
        guard["observation_rows"] = obs_count
        guard["unparsed_periods"] = unparsed
        result["source_guard"] = guard

        if guard["suppressed"]:
            item = {
                "name": name, "rule": rule, **result,
                "verdict": "suppressed",
                "suppressed": "source-unhealthy",
                "warning": "信息可能不完整",
            }
            suppressed.append(item)
            escalate_items.append({"name": name, "rule": rule, "result": result,
                                   "kind": "suppressed"})
            print(f"[SUPPRESSED] {name}: source-unhealthy —— 信息可能不完整")
            print(f"    原因：{'；'.join(guard['reasons'])}")
            print(f"    读数仅存档、不作为当前态：{result.get('value')} "
                  f"{result.get('unit', '')}（期 {result.get('period')}）")
            _print_rule_header(item)
            continue

        parsed_period = parse_period_date(result["period_date"])
        ordinal = period_ordinal(parsed_period, rule["periodicity"])
        prev_state = dict((state.get("rules") or {}).get(name, {}))
        action, new_state = apply_state(
            prev_state, result["verdict"], now=now,
            cooldown_periods=int(rule.get("cooldown_periods", 1)),
            period_ordinal_value=ordinal, value=result.get("value"),
            period=result.get("period"),
        )
        state.setdefault("rules", {})[name] = new_state
        result["action"] = action
        judged.append({"name": name, "rule": rule, **result})

        marker = {"alert": "[ALERT]", "recover": "[RECOVER]",
                  "suppress": "[SUPPRESS]", "idle": "[OK]"}[action]
        verdict_text = "越阈" if result["verdict"] == "crossed" else "未越阈"
        print(f"{marker} {name}: {verdict_text} 读数={result.get('value')} "
              f"{result.get('unit', '')} 期={result.get('period')} 动作={action}")
        if result.get("history_out_of_band"):
            history = ", ".join(
                f"{row['period']}={row['value']}" for row in result["history_out_of_band"])
            print(f"    历史越界（仅历史；本次只判最新季，不因此误报）：{history}")
        gaps = result.get("gaps") or {}
        if gaps.get("missing"):
            print(f"    缺口：{', '.join(gaps['missing'])}"
                  f"（不插值、不补 0、不沿用旧值）")
        if gaps.get("long_gaps"):
            spans = gaps["long_gaps"]
            largest = gaps.get("max_gap") or max(spans, key=lambda g: g["days"])
            recent = ", ".join(f"{g['after']}→{g['before']}({g['days']}d)"
                               for g in spans[-3:])
            print(f"    长间断（>{gaps.get('tolerance_days')}d）：{len(spans)} 处；"
                  f"最大 {largest['days']}d（{largest['after']}→{largest['before']}）；"
                  f"最近：{recent}（周末/节假日不发布，非缺口）")
        if result.get("basis"):
            print(f"    依据：{json.dumps(result['basis'], ensure_ascii=False)}")
        _print_rule_header({"rule": rule})
        for note in notes:
            print(f"    {note}")

        if action in ("alert", "recover"):
            notifications.append(build_notification(rule, result, action, now, prev_state))
            transitions.append({
                "at": now.isoformat(),
                "rule": name,
                "event": "firing" if action == "alert" else "resolved",
                "level": "threshold",
                "source": rule.get("source_used"),
                "period": result.get("period"),
                "value": result.get("value"),
                "unit": result.get("unit"),
                "citation": rule.get("citation"),
                "citation_version": rule.get("citation_version"),
            })
        if action == "alert":
            exit_code = 1
            escalate_items.append({"name": name, "rule": rule, "result": result,
                                   "kind": "alert"})

    notification_results, notify_errors = dispatch_notifications(
        notifications, dry_run, float(defaults.get("notify_timeout_seconds", 15)))

    print("\n-- 判了：越阈 --")
    crossed_items = [item for item in judged if item["verdict"] == "crossed"]
    if crossed_items:
        for item in crossed_items:
            print(f"- {item['name']}: {item['value']} {item.get('unit', '')} "
                  f"期={item['period']} 动作={item['action']}")
    else:
        print("（无）")
    print("-- 判了：未越阈 --")
    quiet_items = [item for item in judged if item["verdict"] == "quiet"]
    if quiet_items:
        for item in quiet_items:
            print(f"- {item['name']}: {item['value']} {item.get('unit', '')} "
                  f"期={item['period']} 动作={item['action']}")
    else:
        print("（无）")
    print("-- 数据不足（不当作「没越阈」）--")
    if insufficient:
        for item in insufficient:
            print(f"- {item['name']}: {item.get('detail')}")
    else:
        print("（无）")
    print("-- 抑制（源不健康 / 数据过期；信息可能不完整）--")
    if suppressed:
        for item in suppressed:
            print(f"- {item['name']}: suppressed={item['suppressed']} "
                  f"原因={'；'.join(item['source_guard']['reasons'])}")
            print(f"  读数仅存档、不作为当前态：{item.get('value')} "
                  f"{item.get('unit', '')}（期 {item.get('period')}）")
    else:
        print("（无）")
    print("-- 未启用（逐条为什么不判）--")
    if disabled:
        for item in disabled:
            print(f"- {item['name']}: {item['blocked_reason']}")
            if item.get("unlock"):
                print(f"  解锁：{item['unlock']}")
    else:
        print("（无）")

    print("\n== 通知（email）==")
    if notification_results:
        for item in notification_results:
            result = item["results"][0]
            if result.get("status") == "sent":
                detail = "sent"
            elif result.get("status") == "dry-run":
                detail = "dry-run（未发送）"
            else:
                detail = f"error: {result.get('error')}"
            print(f"- {item['action']} {item['rule']}: {detail}")
    else:
        print("（本轮无触发/恢复，无需通知）")
    print("== 通知失败（notify_errors）==")
    if notify_errors:
        for error in notify_errors:
            print(f"- {error['action']} {error['rule']} [{error['channel']}]: {error['error']}")
        print("说明：单条失败已留记录，未阻塞同批其它通知。")
    else:
        print("（无）")

    print(f"\n状态：{state_path}（{'dry-run 不写；' if dry_run else ''}跨轮存活）")

    if do_escalate and escalate_items:
        files = escalate(escalate_items, outdir, now, dry_run)
        if dry_run:
            print(f"工单（dry-run 不落盘）：将写 {len(files)} 张 → {outdir / 'tickets'}")
            for filename in files:
                print(f"  - {filename}")
        else:
            print(f"工单：{len(files)} 张 → {outdir / 'tickets'}")

    if not dry_run:
        _write_json(state_path, state)
        append_alert_events(ledger_path, transitions)
        _write_json(outdir / f"rules-{now.date().isoformat()}.json", {
            "at": now.isoformat(),
            "base_date": now.date().isoformat(),
            "config": str(DEFAULT_CONFIG),
            "state_path": str(state_path),
            "alert_events_ledger": str(ledger_path),
            "summary": {
                "judged": len(judged),
                "crossed": len(crossed_items),
                "quiet": len(quiet_items),
                "insufficient_data": len(insufficient),
                "suppressed": len(suppressed),
                "disabled": len(disabled),
            },
            "judged": judged,
            "insufficient_data": insufficient,
            "suppressed": suppressed,
            "disabled": disabled,
            "notifications": notification_results,
            "notify_errors": notify_errors,
            "transitions": transitions,
        })
    return exit_code


# ── 命令：事件级 ──────────────────────────────────────────────────────────

def cmd_events(cur, cfg: dict, outdir: Path, now: datetime, dry_run: bool) -> int:
    event_cfg = cfg.get("events") or {}
    rules = list(event_cfg.get("rules") or [])
    defaults = cfg.get("defaults") or {}
    ledger = REPO / defaults.get("events_ledger",
                                 "reports/agriculture-alerts/state/events.jsonl")
    rows: list[dict] = []
    scanned_total = 0
    new_total = 0

    print(f"== 事件级（基准日 {now.date().isoformat()}；四类逐条给状态） ==")
    for rule in rules:
        label = rule.get("label") or rule["name"]
        if not rule.get("enabled", False):
            item = {
                "name": rule["name"],
                "label": label,
                "verdict": "no_sample",
                "status_text": "暂无样本",
                "no_sample_reason": rule.get("no_sample_reason", "未声明原因"),
                "unlock": rule.get("unlock", ""),
            }
            rows.append(item)
            print(f"[NO-SAMPLE] {label}: 暂无样本 —— {item['no_sample_reason']}")
            if item["unlock"]:
                print(f"            解锁：{item['unlock']}")
            continue
        if not rule.get("payload_match"):
            item = {"name": rule["name"], "label": label, "verdict": "misconfigured",
                    "detail": "enabled=true 但未声明 payload_match（配置错误，不静默跳过）"}
            rows.append(item)
            print(f"[MISCONFIGURED] {label}: {item['detail']}")
            continue
        found = scan_event_rule(cur, rule, now.date())
        scanned_total += len(found)
        new, existed = append_ledger(ledger, found) if not dry_run else (found, 0)
        new_total += len(new)
        item = {
            "name": rule["name"], "label": label, "verdict": "scanned",
            "scanned": len(found), "new": len(new), "already_logged": existed,
        }
        rows.append(item)
        print(f"[SCANNED] {label}: 扫到 {len(found)} 条，新增 {len(new)} 条"
              f"（已入账 {existed} 条）")

    no_sample = sum(1 for row in rows if row["verdict"] == "no_sample")
    print(f"\n== 事件级汇总 == 四类 {len(rules)}；在册 {len(rules) - no_sample}；"
          f"真实样本 {scanned_total} 条；暂无样本 {no_sample} 类")

    if not dry_run:
        _write_json(outdir / f"events-{now.date().isoformat()}.json", {
            "at": now.isoformat(),
            "base_date": now.date().isoformat(),
            "scanned": scanned_total,
            "new": new_total,
            "rules": rows,
        })
    return 0


# ── CLI ───────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="农业线三层预警（健康级覆盖 / 阈值级 / 事件级）")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--asof", default=None,
                    help="演练用：把「现在」钉到某一天；缺省读 FD_ALERT_BASE_DATE")
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--state", default=None, help="状态文件路径覆盖（测试用隔离目录）")
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--health", action="store_true", help="农业单元覆盖表（健康级）")
    ap.add_argument("--rules", action="store_true", help="阈值级（含边缘触发/冷却/抑制）")
    ap.add_argument("--events", action="store_true", help="事件级四类状态")
    ap.add_argument("--escalate", action="store_true",
                    help="越阈/抑制项写成工单（health ticket schema）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只算不落盘：不写状态、不写报告、不发通知")
    args = ap.parse_args(argv)

    cfg = load_config(Path(args.config))
    defaults = cfg.get("defaults") or {}
    asof = args.asof or os.environ.get("FD_ALERT_BASE_DATE")
    now = _now(asof)
    outdir = Path(args.outdir) if args.outdir else REPO / defaults.get(
        "outdir", "reports/agriculture-alerts")
    if args.state:
        state_path = Path(args.state)
        # 隔离测试时事件台账跟随 state 目录，避免把演练事件写进仓库状态面。
        ledger_path = state_path.parent / "agriculture-alert-events.jsonl"
    else:
        state_path = REPO / defaults.get(
            "state", "state/agriculture-alert-state.json")
        ledger_path = REPO / defaults.get(
            "alert_events_ledger", "state/agriculture-alert-events.jsonl")

    if not (args.health or args.rules or args.events):
        ap.error("至少给一个动作：--health / --rules / --events")

    conn = connect(args.dsn)
    code = 0
    try:
        cur = conn.cursor()
        if args.health:
            code = max(code, cmd_health(cur, cfg, outdir, now, args.dry_run))
        if args.rules:
            code = max(code, cmd_rules(cur, cfg, outdir, now, args.dry_run,
                                       args.escalate, state_path, ledger_path))
        if args.events:
            code = max(code, cmd_events(cur, cfg, outdir, now, args.dry_run))
    finally:
        conn.close()
    return code


if __name__ == "__main__":
    # 退出码语义：0=全部未越阈 / 1=有规则越阈（预期语义，不是故障）/ 2=脚本自身崩溃。
    # 必须先捕异常再映射成 2：Python 未捕获异常的默认退出码也是 1，会和「越阈」撞车，
    # 于是崩溃被当成正常越阈、工作流报绿（花卉线 2026-11-08 踩过同一个坑）。
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 — 崩溃统一映射为终止码，栈照打
        import traceback

        traceback.print_exc()
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        # 只读账号缺 semantic_observations 的 SELECT 是「环境不满足」（3=warning，需 DBA 授权），
        # 与脚本自身崩溃（2=红）分开：混在一起会让授权缺失看起来像代码坏了。
        if "permission denied" in str(exc).lower():
            raise SystemExit(3)
        raise SystemExit(2)
