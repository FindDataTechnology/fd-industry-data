#!/usr/bin/env python3
"""花卉线四级预警的执行面（add-flower-vertical 6.2–6.5）。

分工（不重复健康级的事）：

| 级别 | 在哪判 | 本脚本的职责 |
|---|---|---|
| 健康级（该源活着吗） | `fd_industry_data/health/` + `scripts/health_inspect.py` | 不碰 |
| 发布节奏级（上游发了没） | `fd_industry_data/publication.py` | 取数、喂模型、落报告 |
| 阈值级（读数越线） | 本脚本 `eval_*` | 全部阈值读 config，代码内无硬编码 |
| 事件级（披露/公告） | 本脚本 `scan_events` | 幂等台账 + 沿既有工单路径升级 |

三条纪律（每条都对应一次会伪造数据的默认值）：

1. **不插值、不补 0**：缺月/缺日就是缺，报告里 `gaps` 明写；序列不足
   `min_history_periods` 时该规则判 `insufficient_data`，**不当作「没越阈」**。
2. **边缘触发 + 冷却**：越阈只在**进入越阈状态的那一刻**通知一次，持续期抑制；
   回到阈值内发一次恢复，之后 `cooldown_hours` 内的再次越阈不再重复打扰（防抖动）。
   状态落在 `state/alert-state.json`，跨轮存活——这正是餐饮引擎缺的那一块。
3. **不可得的规则要留名**：车道未接入的规则写 `enabled: false` + `blocked_reason`，
   报告里逐条列出「为什么不判」，而不是悄悄少一条规则。

用法::

    source ~/.config/finddata/dashboards.env
    python3 scripts/flower_alerts.py --publication        # 发布节奏体检
    python3 scripts/flower_alerts.py --rules              # 阈值级（含边缘/冷却状态机）
    python3 scripts/flower_alerts.py --events             # 事件级（幂等台账）
    python3 scripts/flower_alerts.py --rules --escalate   # 越阈项写成修复工单
    python3 scripts/flower_alerts.py --rules --dry-run    # 只算不写状态（演练用）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess  # noqa: S404 - psql 兜底通道
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data import publication as pub  # noqa: E402
from fd_industry_data.alerts_core import notify as alert_notify  # noqa: E402
from fd_industry_data.health import ticket as ticket_mod  # noqa: E402

DEFAULT_CONFIG = REPO / "config" / "flower-alerts.json"

CATALOG_TICKERS_SQL = """
    SELECT e.code, e.id FROM entities e WHERE e.entity_type = 'stock' AND e.code = ANY(%s)
"""


# ── 基础设施 ──────────────────────────────────────────────────────────────

def load_config(path: Path) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or "rules" not in doc:
        raise _fatal(f"FATAL 配置不合法（缺 rules）：{path}")
    return doc


EXIT_ENV_UNMET = 3


def can_read_observations(cur) -> bool:
    """当前账号能否读 ``semantic_observations``。

    为什么要探测：定时任务复用的是健康管道那个**只读账号**（`fd_health_ro`），它只被
    授权读 `crawl_runs`；阈值级与水位判定都读观测表，直接跑会
    ``permission denied for table semantic_observations``（2026-11-08 CI 实测）。

    这种情况**既不能假绿也不能每天判红**：判红会让整条流水线长期飘红失去信号，
    假绿则掩盖真问题。所以显式返回「环境不满足」(exit 3)，由调用方决定如何呈现。
    """
    try:
        cur.execute("SELECT 1 FROM semantic_observations LIMIT 1")
        cur.fetchall()
        return True
    except Exception:  # noqa: BLE001 - 权限/表缺失都按「不可读」处理
        return False


def _fatal(message: str) -> "NoReturn":  # noqa: F821
    """致命配置/契约错误：写 stderr 后以 **2** 退出。

    为什么不用 ``raise SystemExit("消息")``：Python 对字符串参数取退出码 **1**，
    与「有规则越阈」的 1 撞车——定时任务会把配置错误读成「有告警」并报成功
    （2026-11-08 CI 假绿就栽在这条语义上）。约定：0 未越阈 / 1 有越阈 / 2 自身失败。
    """
    print(message, file=sys.stderr)
    raise SystemExit(2)


def _sql_literal(value) -> str:
    """Python 值 → SQL 字面量（psql 模式用；参数全是常量或库内代码，仍严格转义）。"""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, datetime):
        return "'" + value.isoformat() + "'"
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        if not items:
            return "ARRAY[]::text[]"
        return "ARRAY[" + ", ".join(_sql_literal(v) for v in items) + "]"
    return "'" + str(value).replace("'", "''") + "'"


class _PsqlCursor:
    """经 ``psql`` 子进程执行查询的游标（纯 stdlib，无驱动依赖）。

    为什么需要：定时任务跑在 self-hosted runner 上，那里**没有 psycopg/psycopg2**
    （2026-11-08 实测 `ModuleNotFoundError`）。健康巡检早就为同一原因走 psql 子进程
    （`health/inspector.py`：「纯 stdlib；中央库读取经 psql 子进程，避免引入驱动依赖」），
    本类沿用同一纪律。参数仍以 ``%s`` 书写，这里内联成安全字面量。
    """

    def __init__(self, dsn: str, binary: str = "psql") -> None:
        self.dsn, self.binary, self._rows = dsn, binary, []

    def execute(self, sql: str, params=None) -> None:
        params = tuple(params or ())
        parts = " ".join(sql.split()).split("%s")
        if len(parts) - 1 != len(params):
            raise RuntimeError(
                f"SQL 占位符 {len(parts) - 1} 个、参数 {len(params)} 个，不匹配")
        query = parts[0]
        for value, tail in zip(params, parts[1:]):
            query += _sql_literal(value) + tail
        proc = subprocess.run(
            [self.binary, self.dsn, "-A", "-F", "\t", "-t", "-q",
             "-v", "ON_ERROR_STOP=1", "-c", query],
            capture_output=True, text=True, timeout=180,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"psql failed ({proc.returncode}): "
                               f"{proc.stderr.strip()[:400]}")
        self._rows = [tuple(line.split("\t"))
                      for line in proc.stdout.splitlines() if line]

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _PsqlConn:
    """无驱动环境下 ``connect()`` 返回的连接壳（只提供 cursor/close）。"""

    def __init__(self, dsn: str) -> None:
        self._cursor = _PsqlCursor(dsn)

    def cursor(self):
        return self._cursor

    def close(self) -> None:
        pass


def connect(dsn: str | None = None):
    """只读连接：psycopg3 → psycopg2 → **psql 子进程**（无驱动环境，如 CI runner）。"""
    # 三处回落：显式 --dsn → 本地 FD_OPEN_DATA_DSN → 健康管道既有只读账号 FD_CENTRAL_PG_DSN
    value = (dsn or os.environ.get("FD_OPEN_DATA_DSN", "")
             or os.environ.get("FD_CENTRAL_PG_DSN", ""))
    if not value:
        raise _fatal(
            "ERROR: 需要 FD_OPEN_DATA_DSN 或 FD_CENTRAL_PG_DSN（先 "
            "`source ~/.config/finddata/dashboards.env`）")
    if os.environ.get("FD_FLOWER_ALERTS_FORCE_PSQL") == "1":
        return _PsqlConn(value)          # 测试钩子：强制走 psql 路径
    try:
        import psycopg  # noqa: PLC0415
        conn = psycopg.connect(value, connect_timeout=40)
        conn.read_only = True
        return conn
    except ImportError:
        pass
    try:
        import psycopg2  # noqa: PLC0415
        return psycopg2.connect(value, connect_timeout=40)
    except ImportError:
        return _PsqlConn(value)


def series(cur, concept: str, source_used: str,
           entity_id: int | None = None) -> tuple[list[tuple[str, float]], list[dict]]:
    """一条序列的 (date, value) 升序 + 非数值拒绝清单（不静默丢）。"""
    cur.execute(
        """
        SELECT o.date, o.value FROM semantic_observations o
        JOIN concepts c ON c.id = o.concept_id
        WHERE c.code = %s AND o.source_used = %s
          AND (%s::int IS NULL OR o.entity_id = %s::int)
        ORDER BY o.date, o.fetched_at
        """,
        (concept, source_used, entity_id, entity_id),
    )
    deduped: dict[str, float] = {}
    rejected: list[dict] = []
    for raw_date, raw_value in cur.fetchall():
        d = str(raw_date)[:10] if raw_date is not None else ""
        try:
            deduped[d] = float(str(raw_value).replace(",", ""))
        except (TypeError, ValueError):
            rejected.append({"date": d, "raw_value": str(raw_value)})
    return sorted(deduped.items()), rejected


def entity_id(cur, code: str, entity_type: str) -> int | None:
    cur.execute(
        "SELECT id FROM entities WHERE code = %s AND entity_type = %s ORDER BY id LIMIT 1",
        (code, entity_type),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


def _iso(value) -> str:
    """时间列归一成 ISO 字符串：驱动给 datetime，psql 给 "2026-11-07 17:50:10+00"。"""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    text = str(value).strip()
    return text.replace(" ", "T", 1) if text else text


def _display_path(path: Path) -> str:
    """报告里展示的路径：仓内给相对路径，仓外给绝对路径。

    原来直接 ``path.relative_to(REPO)``，于是把 state 指到仓外（演练目录放 /tmp、
    或外部定时器指定别处）时**整个 --rules 直接崩**——报告写不出来、状态也保存不了。
    展示用路径不该有这种硬要求。
    """
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(REPO.resolve()))
    except ValueError:
        return str(resolved)


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


def _today(asof: str | None) -> date:
    return date.fromisoformat(asof) if asof else datetime.now(timezone.utc).date()


def _now(asof: str | None) -> datetime:
    if asof:
        return datetime.fromisoformat(asof).replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _crossed(comparator: str, value: float, threshold: float) -> bool:
    table = {
        "lt": lambda v, t: v < t,
        "lte": lambda v, t: v <= t,
        "gt": lambda v, t: v > t,
        "gte": lambda v, t: v >= t,
        "abs_gte": lambda v, t: abs(v) >= t,
    }
    if comparator not in table:
        raise ValueError(f"未知 comparator: {comparator!r}（合法值 {sorted(table)}）")
    return table[comparator](value, threshold)


# ── 规则 1：kind=change（聚合后两期之间的百分比变化） ──────────────────────

def _week_key(day: str) -> str:
    d = date.fromisoformat(day)
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat()


def _period_key(day: str, aggregate: str) -> str:
    if aggregate in ("week_mean", "week_sum"):
        return _week_key(day)
    if aggregate in ("month_mean", "month_sum"):
        return day[:7]
    if aggregate in ("year_mean", "year_sum"):
        # 年频序列（Eurostat 面积、AIPH 截面）本身就是一年一个点：直接取年，
        # 聚合等于恒等，但「相邻两期变化」由此变成**同比**——这正是年频要看的口径。
        return day[:4]
    raise ValueError(f"未知 aggregate: {aggregate!r}")


def eval_change_rule(cur, rule: dict, notes: list[str]) -> dict:
    """把日/周频序列按 ``aggregate`` 聚合，再算相邻两期的百分比变化。

    为什么必须聚合：斗南是**日频现货价**，「环比」在日粒度上是噪声；规则要说的是
    「这一周比上一周跌了多少」，所以聚合口径写进 config（``aggregate``），不由代码猜。
    """
    eid = entity_id(cur, rule["entity_code"], rule.get("entity_type", "commodity"))
    if eid is None:
        return {"verdict": "insufficient_data",
                "detail": f"实体 {rule['entity_code']} 未注册（先跑 register 脚本）"}
    points, rejected = series(cur, rule["concept"], rule["source_used"], eid)
    if rejected:
        notes.append(f"{rule['name']}: {len(rejected)} 个非数值读数被排除（不当作 0）")
    if not points:
        return {"verdict": "insufficient_data", "detail": "该序列零观测"}

    buckets: dict[str, list[float]] = {}
    for day, value in points:
        buckets.setdefault(_period_key(day, rule["aggregate"]), []).append(value)
    # 薄桶必须剔除：斗南偶有整周只发 1 天，用「1 个样本的周均值」去比「7 个样本的
    # 周均值」会凭空造出 −33% 这种跌幅（2026-10-05 那周实测 n=1，跌幅 −33.33%，
    # 而前后周都在 ±6% 内）。样本下限写进 config，代码里不设默认数字。
    min_bucket = int(rule.get("min_bucket_samples", 1))
    thin = {k: len(v) for k, v in buckets.items() if len(v) < min_bucket}
    if thin:
        buckets = {k: v for k, v in buckets.items() if len(v) >= min_bucket}
        notes.append(f"{rule['name']}: 剔除薄桶 {sorted(thin)}"
                     f"（样本数 < min_bucket_samples={min_bucket}：单日样本冒充周均值会造出假跌幅）")
    keys = sorted(buckets)
    if len(keys) < int(rule.get("min_history_periods", 2)):
        return {"verdict": "insufficient_data",
                "detail": f"聚合后仅 {len(keys)} 期 < min_history_periods"
                          f"={rule.get('min_history_periods', 2)}，不判（不是「没越阈」）"}
    is_sum = rule["aggregate"].endswith("_sum")
    agg = {k: (sum(v) if is_sum else sum(v) / len(v)) for k, v in buckets.items()}
    last_key, prev_key = keys[-1], keys[-2]
    prev = agg[prev_key]
    if prev == 0:
        return {"verdict": "insufficient_data", "detail": f"上期 {prev_key} 聚合值为 0，环比无定义"}
    change_pct = (agg[last_key] - prev) / prev * 100.0
    crossed = _crossed(rule["comparator"], change_pct, float(rule["threshold"]))
    return {
        "verdict": "crossed" if crossed else "quiet",
        "value": round(change_pct, 4),
        "unit": rule.get("unit", "percent"),
        "period": last_key,
        "basis": {"period": last_key, "aggregate": rule["aggregate"],
                  "value": round(agg[last_key], 6), "samples": len(buckets[last_key])},
        "baseline": {"period": prev_key, "value": round(prev, 6),
                     "samples": len(buckets[prev_key])},
        "threshold": rule["threshold"],
        "comparator": rule["comparator"],
    }


# ── 规则 2：kind=ratio（两条序列之比） ────────────────────────────────────

def eval_ratio_rule(cur, rule: dict, notes: list[str], asof: date | None = None) -> dict:
    """按同一 ``date`` 对齐两条序列求比值（分子/分母），逐实体判定。

    三处口径陷阱，每处都有对应的防线：

    1. **分母为 0 或缺失的那一期不产生比值**（不是 0 利润率）——只在两侧同日均有的
       期次上算。营收为 0 时利润率无定义，不是「利润率为 0」。
    2. **期次时效闸**（``max_period_age_days``）：某实体最近一个有效期次太旧时不判。
       实证必要：HYFM 自 2026 起改用 ``RevenueFromContractWithCustomerIncludingAssessedTax``
       申报营收，抽取端**刻意不并入**（含代收税，并入等于凭空造营收跳升），于是它的
       营收序列止于 2025-09-30 而营业利润走到 2026-06-30。不加闸就会拿 14 个月前的
       期次去算当期利润率——数字「算得出来」，但回答的是另一个问题。
    3. **币种不加总、不换算**：比值无量纲可比，绝对额（``denominator``）只作规模披露，
       跨实体只用来判断「这个读数来自多大规模的公司」，不做任何跨币种汇总。
    """
    results: list[dict] = []
    skipped: list[dict] = []
    max_age = rule.get("max_period_age_days")
    for target in rule["entities"]:
        eid = entity_id(cur, target["code"], target.get("entity_type", "stock"))
        if eid is None:
            notes.append(f"{rule['name']}: 实体 {target['code']} 未注册，跳过")
            skipped.append({"entity": target["code"], "reason": "实体未注册"})
            continue
        num, _ = series(cur, rule["numerator"]["concept"], target["source_used"], eid)
        den, _ = series(cur, rule["denominator"]["concept"], target["source_used"], eid)
        den_map = dict(den)
        rows = [(d, v / den_map[d] * 100.0) for d, v in num
                if den_map.get(d) not in (None, 0)]
        if len(rows) < int(rule.get("min_history_periods", 1)):
            notes.append(f"{rule['name']}: {target['code']} 有效期次 {len(rows)} 不足，跳过")
            skipped.append({"entity": target["code"],
                            "reason": f"两侧同日都有的期次仅 {len(rows)} 期"})
            continue
        last_date, last_value = rows[-1]
        if max_age and asof is not None:
            age = (asof - date.fromisoformat(last_date)).days
            if age > int(max_age):
                reason = (f"最近有效期次 {last_date} 距今 {age} 天 > max_period_age_days="
                          f"{max_age}（分母序列未跟进：{rule['denominator']['concept']}）")
                skipped.append({"entity": target["code"], "reason": reason,
                                "last_valid_period": last_date})
                continue
        crossed = _crossed(rule["comparator"], last_value, float(rule["threshold"]))
        results.append({
            "entity": target["code"], "entity_id": eid,
            "value": round(last_value, 4), "period": last_date,
            "crossed": crossed,
            "denominator": den_map.get(last_date),
            "basis": {"period": last_date,
                      "numerator": dict(num).get(last_date),
                      "denominator": den_map.get(last_date)},
        })
    if not results:
        return {"verdict": "insufficient_data",
                "detail": f"无实体可判（跳过 {len(skipped)} 家，原因逐条见 skipped）",
                "skipped": skipped}
    crossed = [r for r in results if r["crossed"]]
    worst = min(results, key=lambda r: r["value"]) if rule["comparator"] in ("lt", "lte") \
        else max(results, key=lambda r: r["value"])
    return {
        "verdict": "crossed" if crossed else "quiet",
        "value": worst["value"],
        "unit": rule.get("unit", "percent"),
        "period": worst["period"],
        "entity": worst["entity"],
        "basis": worst["basis"],
        "threshold": rule["threshold"],
        "comparator": rule["comparator"],
        "per_entity": results,
        "skipped": skipped,
        "crossed_entities": [r["entity"] for r in crossed],
    }


EVALUATORS = {"change": eval_change_rule, "ratio": eval_ratio_rule}


# ── 边缘触发 + 冷却（6.4） ────────────────────────────────────────────────

def apply_state(prev: dict, verdict: str, *, now: datetime, cooldown_hours: float,
                value: float | None = None, period: str | None = None) -> tuple[str, dict]:
    """把本轮读数喂进状态机，返回 (action, new_state)。

    ``action``：``alert``（进入越阈）/ ``recover``（回到阈值内）/ ``suppress``（持续期
    或冷却期内的重复越阈，不打扰）/ ``idle``（一直正常）。

    冷却的语义是**恢复之后的防抖窗**：恢复通知发出后 ``cooldown_hours`` 内即使再次
    越阈也不再通知（上游数据抖动不该变成连环告警）；冷却期满再越阈才算新事件。
    """
    state = dict(prev or {})
    current = state.get("state", "quiet")
    cooldown_until = state.get("cooldown_until")
    in_cooldown = bool(cooldown_until) and now < datetime.fromisoformat(cooldown_until)

    if verdict == "crossed":
        if current == "firing":
            state["last_value"] = value
            return "suppress", state
        if in_cooldown:
            state["last_value"] = value
            return "suppress", state
        state.update({
            "state": "firing",
            "since": now.isoformat(),
            "last_notified_at": now.isoformat(),
            "episode": int(state.get("episode", 0)) + 1,
            "last_value": value,
            "period": period,
        })
        return "alert", state

    if verdict == "quiet":
        if current == "firing":
            until = (now + timedelta(hours=cooldown_hours)).isoformat()
            state.update({
                "state": "cooldown",
                "recovered_at": now.isoformat(),
                "last_notified_at": now.isoformat(),
                "last_value": value,
                "period": period,
                "cooldown_until": until,
            })
            return "recover", state
        if current == "cooldown" and not in_cooldown:
            state["state"] = "quiet"
            state.pop("cooldown_until", None)
        state["last_value"] = value
        state["period"] = period
        return "idle", state

    # insufficient_data 等：不改状态（数据不足既不能开也不能关一个告警）
    return "suppress", state


# ── 事件级（6.5） ─────────────────────────────────────────────────────────

def scan_events(cur, cfg: dict, asof: date) -> list[dict]:
    """披露日历：花卉宇宙的 SEC 申报事件（一条申报 = 一个事件）。

    取数口径与看板事件图**同一来源**：``crawl_items`` 的 payload（``kind='event'``）。
    注意 ``crawl_items`` 只有 ``(run_id, idx, payload)`` 三列——``source``/``kind``/
    ``ticker`` 全在 jsonb 里，按列过滤会直接报「column does not exist」（踩过）。

    幂等键 = ``ticker|form|filed|accession``：同一天同一家公司可能发两份 8-K，
    只用 (ticker, form, filed) 会把它们并成一条，少记事件。
    """
    ev = cfg.get("events") or {}
    if not ev.get("enabled", False):
        return []
    tickers = list(ev.get("tickers") or [])
    forms = list(ev.get("forms") or [])
    if not tickers or not forms:
        return []
    since = asof - timedelta(days=int(ev.get("window_days", 90)))
    cur.execute(
        """
        SELECT payload FROM crawl_items
        WHERE payload->>'source' = %s AND payload->>'kind' = 'event'
          AND payload->>'ticker' = ANY(%s) AND payload->>'form' = ANY(%s)
        ORDER BY payload->>'filed'
        """,
        (ev["source"], tickers, forms),
    )
    out: list[dict] = []
    seen: set[str] = set()
    for (payload,) in cur.fetchall():
        if isinstance(payload, str):
            payload = json.loads(payload)
        filed = str(payload.get("filed") or "")[:10]
        if not filed or date.fromisoformat(filed) < since:
            continue
        accession = str(payload.get("accession") or "")
        # 尾部只在真有 accession 时拼——否则键会带一个空段（``…|2026-11-05|``），
        # 与不含 accession 的历史台账行对不上，重跑会把同一事件再记一遍。
        parts = [str(payload.get("ticker")), str(payload.get("form")), filed]
        if accession:
            parts.append(accession)
        key = "|".join(parts)
        # ``crawl_items`` 是**逐次运行追加**的：同一条申报会被后续运行再写一行，
        # 所以「表里几行」≠「几次申报」。不去重就会把 28 个申报报成 56 条
        # （2026-11-08 端到端走查实测）。台账按 key 幂等，但**打印与返回的条数**
        # 必须先按业务键收敛，否则对外报的数字直接虚高一倍。
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "key": key,
            "kind": "disclosure",
            "source": ev["source"],
            "ticker": payload.get("ticker"),
            "form": payload.get("form"),
            "filed": filed,
            "accession": accession or None,
            "report_date": payload.get("report_date"),
            "level": ev.get("level", "info"),
            "basis": {"form": payload.get("form"),
                      "reason": ev.get("level_reason", "定期报告/重大事项申报")},
        })
    return out


def append_ledger(path: Path, rows: list[dict]) -> tuple[list[dict], int]:
    """幂等落账：返回 (新增行, 已存在条数)。

    去重必须同时覆盖**文件里已有的键**与**本批次内部的键**：只比对文件时，
    同一批里出现两个同键行会两条都写进去（2026-11-08 实测台账 56 行 / 28 键）。
    """
    seen: set[str] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                seen.add(json.loads(line)["key"])
            except (json.JSONDecodeError, KeyError):
                continue
    new: list[dict] = []
    for row in rows:
        if row["key"] in seen:
            continue
        seen.add(row["key"])          # 批内去重
        new.append(row)
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for row in new:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return new, len(rows) - len(new)


# ── 发布节奏（6.2） ───────────────────────────────────────────────────────

def publication_report(cur, cfg: dict, now: datetime) -> list[dict]:
    """按 config 里声明的发布节奏，逐源跑一次 ``publication.classify``。"""
    out: list[dict] = []
    for source, spec in sorted((cfg.get("publication") or {}).items()):
        cadence = pub.Cadence(**spec["cadence"])
        cur.execute(
            """
            SELECT started_at, status, COALESCE(rows_written, 0)
            FROM crawl_runs WHERE source = %s AND started_at >= %s
            ORDER BY started_at
            """,
            (source, now - timedelta(days=int(spec.get("lookback_days", 120)))),
        )
        runs = [pub.Run(started_at=_iso(r[0]), status=str(r[1]), rows=int(r[2]))
                for r in cur.fetchall()]
        # 水位来自观测面（数据日），不来自遥测——遥测没有数据期列。
        wm_concept = spec.get("watermark_concept")
        watermark = None
        if wm_concept:
            points, _ = series(cur, wm_concept, spec.get("watermark_source_used", source))
            watermark = points[-1][0] if points else None
        if watermark:
            # 把当前水位挂到最近一次成功运行上（真正的「这次看到哪一期」由抓取期决定，
            # 离线只有最新值可用——如实写进 basis，不假装逐次都有）。
            for i in range(len(runs) - 1, -1, -1):
                if runs[i].status == "success":
                    runs[i] = pub.Run(started_at=runs[i].started_at, status="success",
                                      rows=runs[i].rows, watermark=watermark)
                    break
        state = pub.classify(runs, cadence, now)
        out.append({
            "source": source, "state": state.state, "detail": state.detail,
            "watermark": state.watermark, "expected_next_by": state.expected_next_by,
            "learned_lag_days": state.learned_lag_days, "samples": state.samples,
            "needs_attention": state.needs_attention,
            "watermark_basis": (f"{wm_concept}@{spec.get('watermark_source_used', source)}"
                                " 最新数据日（观测面），非逐次运行带回"),
        })
    return out


# ── 升级（沿既有工单路径） ────────────────────────────────────────────────

def _rule_source(rule: dict, result: dict) -> str:
    """规则真正读过的那个源。

    ``change`` 规则顶层带 ``source_used``；``ratio`` 规则的源挂在 ``entities[]`` 上
    （同一个比值可以分子分母来自不同源），所以按**越线的实体**回查它的 ``source_used``——
    直接取顶层键会 KeyError（2026-11-08 演练实录），也取不到「这家公司到底读的哪条源」。
    """
    if rule.get("source_used"):
        return rule["source_used"]
    entities = rule.get("entities") or []
    for target in entities:
        if target.get("code") == result.get("entity") and target.get("source_used"):
            return target["source_used"]
    for target in entities:                      # 越线实体没注册时退到首个已声明源
        if target.get("source_used"):
            return target["source_used"]
    raise _fatal(f"FATAL 规则 {rule.get('name')} 未声明任何 source_used，工单无从归属")


def _ticket_for(name: str, rule: dict, result: dict, cfg: dict, now: datetime) -> dict:
    source = _rule_source(rule, result)
    basis = result.get("basis") or {}
    crossed_entities = result.get("crossed_entities") or []
    evidence = {
        "window": {"from": str(basis.get("period") or ""), "to": str(basis.get("period") or "")},
        "failing_runs": [{
            "started_at": now.isoformat(),
            "status": "failed",
            "rows": None,
            "error_summary": (
                f"阈值告警 {name}：读数 {result.get('value')} {result.get('unit')} "
                f"{rule['comparator']} {rule['threshold']}（期 {basis.get('period')}）"
            ),
            "http_status": None,
        }],
        "last_success": None,
        "watermark": {"latest": str(basis.get("period") or ""), "basis": "告警读数所在期"},
        "notes": (f"级别=阈值级；依据={rule.get('description', name)}；"
                  f"读数={result.get('value')} {result.get('unit')}"
                  + (f"（{result['entity']}）" if result.get("entity") else "")
                  + f"；阈值={rule['comparator']} {rule['threshold']}；"
                  + (f"越线实体={crossed_entities}；" if crossed_entities else "")
                  + f"归因={'数据侧越线，非抓取故障'}"),
    }
    return ticket_mod.new_ticket(
        source=source,
        unit=f"spiders/{source}/",
        kind="repair",
        category="contract",
        evidence=evidence,
        verify_commands=[f"python3 scripts/flower_alerts.py --rules --asof {now.date().isoformat()}"],
        suggested_action=(f"核对 {name} 的读数与口径（{rule.get('description', '')}）；"
                          "阈值级告警不自动改数据"),
        brief={"rule": name, "level": "threshold", "config": str(DEFAULT_CONFIG)},
        created_at=now.isoformat(),
    )


def escalate(items: list[dict], cfg: dict, outdir: Path, now: datetime,
             dry_run: bool) -> list[str]:
    """把需要处置的越阈/事件写成工单文件（沿用 health ticket schema，可被既有 SUBMIT 消费）。"""
    tdir = outdir / "tickets"
    written: list[str] = []
    for item in items:
        if not item.get("needs_ticket"):
            continue
        doc = _ticket_for(item["name"], item["rule"], item["result"], cfg, now)
        errors = ticket_mod.validate_ticket(doc)
        if errors:
            raise _fatal(f"FATAL 工单不合法（{item['name']}）：{errors}")
        day = doc["created_at"][:10].replace("-", "")
        fname = ticket_mod.ticket_filename(day, doc["source"], doc["ticket_id"].split("-")[-1])
        doc["ticket_id"] = fname[:-5]
        if not dry_run:
            tdir.mkdir(parents=True, exist_ok=True)
            (tdir / fname).write_text(ticket_mod.dump_ticket(doc), encoding="utf-8")
        written.append(fname)
    return written


# ── 命令 ──────────────────────────────────────────────────────────────────

def cmd_publication(cur, cfg: dict, outdir: Path, now: datetime, dry_run: bool) -> int:
    rows = publication_report(cur, cfg, now)
    _write_json(outdir / f"publication-{now.date().isoformat()}.json",
                {"at": now.isoformat(), "sources": rows})
    for r in rows:
        flag = "⚠️ " if r["needs_attention"] else ""
        lag = r["learned_lag_days"]
        print(f"{flag}{r['source']}: {r['state']}  水位={r['watermark']}  "
              f"滞后={f'{lag:.1f}d' if lag is not None else '未学出'}  样本={r['samples']}")
        print(f"    {r['detail']}")
    return 1 if any(r["needs_attention"] for r in rows) else 0


def _notify_targets(cfg: dict) -> list[str]:
    """收件人：env 覆盖优先，否则读配置的 delivery.to（可版本化、非机密）。"""
    env_to = [x.strip() for x in (os.environ.get("FD_ALERT_SMTP_TO") or "").split(",")
              if x.strip()]
    if env_to:
        return env_to
    delivery = cfg.get("delivery") or {}
    return [str(x).strip() for x in (delivery.get("to") or []) if str(x).strip()]


def deliver_notifications(items: list[dict], cfg: dict, *, dry_run: bool,
                          now: datetime, smtp_factory=None) -> dict:
    """把边缘触发的 alert/recover 发出去，返回三态回执。

    三条纪律（每条都对应一种"看起来发了其实没发"）：

    1. **无通道 = skipped(带原因)**，不是成功。凭证缺失时报告里必须能看到原因。
    2. **单条失败不阻塞同批**，但失败条数与原因要落进回执（调用方据此置 exit 4）。
    3. **dry-run 不发**，回执里明确 `reason=dry-run`。
    """
    receipt: dict = {"sent": 0, "failed": 0, "skipped": 0, "messages": []}
    if not items:
        return receipt
    if dry_run:
        receipt["skipped"] = len(items)
        receipt["reason"] = "dry-run（不发送）"
        return receipt

    cfg_smtp = alert_notify.smtp_config_from_env()
    if cfg_smtp is None:
        receipt["skipped"] = len(items)
        receipt["reason"] = ("no-smtp-config：缺 FD_ALERT_SMTP_HOST/FROM/TO；"
                             "落仓成功不等于有人收到")
        print(f"通知：跳过 {len(items)} 条 —— {receipt['reason']}")
        return receipt

    recipients = _notify_targets(cfg) or cfg_smtp.recipients
    cfg_smtp.recipients = recipients
    for item in items:
        res = item["result"]
        rule = item.get("rule") or {}
        kind = "越阈" if item["action"] == "alert" else "恢复"
        subject = f"[花卉线] {kind}：{item['name']}"
        body = "\n".join([
            f"规则：{item['name']}",
            f"状态：{kind}",
            f"读数：{res.get('value')} {res.get('unit', '')}".rstrip(),
            f"期次：{res.get('period')}",
            f"实体：{res.get('entity') or '-'}",
            f"引用：{rule.get('citation') or '-'}（{rule.get('citation_version') or '-'}）",
            "",
            f"判定时间：{now.isoformat()}",
            "本邮件由 fd-industry-data 花卉线预警在边缘触发时自动发出；持续期不重复。",
        ])
        try:
            alert_notify.send_email(cfg_smtp, subject, body, smtp_factory=smtp_factory)
            receipt["sent"] += 1
            receipt["messages"].append({"rule": item["name"], "status": "sent"})
        except Exception as exc:  # noqa: BLE001 — 单条失败不阻塞同批
            receipt["failed"] += 1
            receipt["messages"].append({"rule": item["name"], "status": "failed",
                                        "error": f"{type(exc).__name__}: {exc}"[:200]})
    print(f"通知：sent={receipt['sent']} failed={receipt['failed']} → {recipients}")
    return receipt


def cmd_rules(cur, cfg: dict, outdir: Path, now: datetime, dry_run: bool,
              do_escalate: bool) -> int:
    defaults = cfg.get("defaults") or {}
    cooldown = float(defaults.get("cooldown_hours", 24))
    state_path = REPO / defaults.get("state", "reports/flower-alerts/state/alert-state.json")
    state = _read_json(state_path, {"rules": {}})
    reports, escalate_items, notify_items = [], [], []
    exit_code = 0
    for rule in cfg["rules"]:
        name = rule["name"]
        if not rule.get("enabled", True):
            reports.append({"name": name, "verdict": "disabled",
                            "blocked_reason": rule.get("blocked_reason", "未声明原因"),
                            "unblock": rule.get("unblock", "")})
            print(f"⊘ {name}: 未启用 —— {rule.get('blocked_reason', '')}")
            continue
        notes: list[str] = []
        evaluator = EVALUATORS.get(rule["kind"])
        if evaluator is None:
            raise _fatal(f"FATAL 未知 kind={rule['kind']!r}（规则 {name}）")
        result = (evaluator(cur, rule, notes, asof=now.date())
                  if rule["kind"] == "ratio" else evaluator(cur, rule, notes))
        prev = (state.get("rules") or {}).get(name, {})
        if result["verdict"] in ("crossed", "quiet"):
            action, new_state = apply_state(
                prev, result["verdict"], now=now, cooldown_hours=cooldown,
                value=result.get("value"), period=result.get("period"))
            state.setdefault("rules", {})[name] = new_state
            result["action"] = action
        else:
            result["action"] = "suppress"
            action = "suppress"
        reports.append({"name": name, "rule": rule, **result, "notes": notes})
        icon = {"alert": "🔴", "recover": "🟢", "suppress": "…", "idle": "·"}[action]
        who = f" [{result['entity']}]" if result.get("entity") else ""
        print(f"{icon} {name}: {result['verdict']} 读数={result.get('value')} "
              f"{result.get('unit','')} 期={result.get('period')}{who}")
        for row in result.get("per_entity") or []:
            scale = row.get("denominator")
            scale_txt = f" 分母={scale:,.0f}" if isinstance(scale, (int, float)) else ""
            mark = " ✗" if row["crossed"] else ""
            print(f"    {row['entity']}: {row['value']:+.2f}%  期={row['period']}{scale_txt}{mark}")
        for row in result.get("skipped") or []:
            print(f"    ⊘ {row['entity']}: {row['reason']}")
        if result["verdict"] == "insufficient_data":
            print(f"    {result['detail']}")
        for n in notes:
            print(f"    {n}")
        if action == "alert":
            exit_code = max(exit_code, 1)
            escalate_items.append({"name": name, "rule": rule, "result": result,
                                   "needs_ticket": True})
        if action in ("alert", "recover"):
            notify_items.append({"name": name, "action": action, "result": result,
                                 "rule": rule})
    if not dry_run:
        _write_json(state_path, state)

    # 投递：边缘触发的 alert / recover 各报一次（状态机保证不重复）。
    # 这里之前什么都没有——"只落仓不推送"，等于规则越阈只有写报告的人知道。
    # 已知取舍（刻意写在这里，免得后来人以为没想过）：
    # 状态机在上面已经前进了，若本轮投递失败，下一轮不会为同一次越阈重发——
    # 「不重复打扰」优先于「保证送达」。补偿是 exit 4 判红 + 报告 notify 段记录失败原因，
    # 人可以重跑。若将来要求"未送达自动重发"，需要把状态推进改成按条成功才推进
    # （见 openspec change wire-alert-delivery 的 3.5）。
    receipt = deliver_notifications(notify_items, cfg, dry_run=dry_run, now=now)
    if receipt.get("failed"):
        # 4 = 有通知发送失败。不与 1（有规则越阈，预期语义）混用：
        # 混在一起会让"邮件发不出去"看起来像"正常的越阈"。
        exit_code = max(exit_code, 4)

    _write_json(outdir / f"rules-{now.date().isoformat()}.json",
                {"at": now.isoformat(), "cooldown_hours": cooldown,
                 "state_path": _display_path(state_path), "rules": reports,
                 "notify": receipt})
    if do_escalate and escalate_items:
        files = escalate(escalate_items, cfg, outdir, now, dry_run)
        print(f"工单：{len(files)} 张 → {outdir / 'tickets'}")
    return exit_code


def cmd_events(cur, cfg: dict, outdir: Path, now: datetime, dry_run: bool) -> int:
    defaults = cfg.get("defaults") or {}
    ledger = REPO / defaults.get("events_ledger", "reports/flower-alerts/state/events.jsonl")
    rows = scan_events(cur, cfg, now.date())
    if dry_run:
        print(f"events（dry-run）：扫到 {len(rows)} 条，不落账")
        for r in rows[:10]:
            print(f"  {r['filed']} {r['ticker']} {r['form']}")
        return 0
    new, existed = append_ledger(ledger, rows)
    _write_json(outdir / f"events-{now.date().isoformat()}.json",
                {"at": now.isoformat(), "scanned": len(rows), "new": len(new),
                 "already_logged": existed, "new_events": new})
    print(f"events：扫到 {len(rows)} 条，新增 {len(new)} 条（已入账 {existed} 条）→ {ledger}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="花卉线四级预警（发布节奏/阈值/事件）")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--asof", default=None, help="演练用：把「现在」钉到某一天（YYYY-MM-DD）")
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--publication", action="store_true", help="发布节奏级体检")
    ap.add_argument("--rules", action="store_true", help="阈值级（含边缘触发/冷却）")
    ap.add_argument("--events", action="store_true", help="事件级（幂等台账）")
    ap.add_argument("--escalate", action="store_true", help="越阈项写成工单（health ticket schema）")
    ap.add_argument("--dry-run", action="store_true", help="只算不写状态（演练）")
    args = ap.parse_args(argv)

    cfg = load_config(Path(args.config))
    now = _now(args.asof)
    outdir = Path(args.outdir) if args.outdir else REPO / (cfg.get("defaults") or {}).get(
        "outdir", "reports/flower-alerts")
    if not (args.publication or args.rules or args.events):
        ap.error("至少给一个动作：--publication / --rules / --events")

    conn = connect(args.dsn)
    code = 0
    try:
        cur = conn.cursor()
        if not can_read_observations(cur):
            # 只读账号能读 crawl_runs 但不能读观测表：阈值级/事件级/水位全都做不了。
            # 明确报「环境不满足」而不是崩溃或空跑通过。
            print(
                "ENV: 当前数据库账号无 `semantic_observations` 读权限——"
                "阈值级、事件级与发布水位判定均无法执行。\n"
                "     需要 DBA 授予：GRANT SELECT ON public.semantic_observations "
                "TO <该只读账号>（台账 F17b）", file=sys.stderr)
            return EXIT_ENV_UNMET
        if args.publication:
            code = max(code, cmd_publication(cur, cfg, outdir, now, args.dry_run))
        if args.rules:
            code = max(code, cmd_rules(cur, cfg, outdir, now, args.dry_run, args.escalate))
        if args.events:
            code = max(code, cmd_events(cur, cfg, outdir, now, args.dry_run))
    finally:
        conn.close()
    return code


if __name__ == "__main__":
    # 退出码语义（定时任务按它判成败）：0=全部未越阈 / 1=有规则越阈（预期）/ 2=脚本自身崩溃
    # 为何必须区分：CI 曾把 ModuleNotFoundError 的退出码 1 当成「有越阈」，job 报 success 而
    # 脚本根本没跑（假绿，2026-11-08 实测）。
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - 兜底：任何未预期异常都必须判红
        import traceback
        traceback.print_exc()
        raise SystemExit(2)
