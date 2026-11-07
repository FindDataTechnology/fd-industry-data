"""花卉线预警引擎的契约（add-flower-vertical 6.2–6.5）。

钉六条**会静默出错**的默认值：

1. **数据不足 ≠ 没越阈**：期次不够时判 ``insufficient_data``，绝不能返回 ``quiet``
   —— 后者会被状态机读成「恢复正常」，于是一次缺数就发出一条假的恢复通知。
2. **薄桶剔除**：斗南偶有整周只发 1 天，1 个样本的「周均值」比 7 个样本的均值能造出
   −33% 的假跌幅（2026-09-07 / 2026-10-05 实测）。
3. **上期为 0 时环比无定义**：不是 0% 变化，也不是「跌 100%」。
4. **分母缺失/为 0 的那一期不产生比值**：营收为 0 时利润率无定义，不能当 0 利润率。
5. **期次时效闸**：HYFM 改用含代收税口径后营收序列停更，拿 14 个月前的期次算当期
   利润率「算得出来」但回答的是另一个问题——必须跳过并报原因。
6. **边缘触发 + 冷却 + 恢复**：连续越阈只发一条告警，恢复发一条，冷却期内的再次越阈
   不打扰；冷却期满后再次越阈算新事件（episode 递增）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("flower_alerts", ROOT / "scripts" / "flower_alerts.py")
fa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fa)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


class Cur:
    """按 SQL 形状分流的假游标：series / entity_id / crawl_runs / crawl_items。"""

    def __init__(self, *, series=None, entity_ids=None, runs=None, items=None):
        self.series = series or []          # list[(date, value)]
        self.entity_ids = entity_ids or {}
        self.runs = runs or []              # list[(started_at, status, rows)]
        self.items = items or []            # list[payload dict]
        self._rows = []

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if "FROM semantic_observations" in s:
            self._rows = list(self.series)
        elif "FROM entities WHERE code" in s:
            code = params[0]
            self._rows = [(self.entity_ids[code],)] if code in self.entity_ids else []
        elif "FROM crawl_runs" in s:
            self._rows = list(self.runs)
        elif "FROM crawl_items" in s:
            self._rows = [(p,) for p in self.items]
        else:
            raise AssertionError(f"未预期的 SQL: {s[:80]}")

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


# ── 状态机（6.4） ─────────────────────────────────────────────────────────

def test_edge_trigger_emits_one_alert_then_suppresses_while_crossed():
    state: dict = {}
    action, state = fa.apply_state({}, "crossed", now=NOW, cooldown_hours=24,
                                   value=-15.0, period="2026-11-02")
    assert action == "alert" and state["state"] == "firing" and state["episode"] == 1
    # 连续两轮仍越阈：抑制，不重复打扰
    for _ in range(2):
        action, state = fa.apply_state(state, "crossed", now=NOW, cooldown_hours=24,
                                       value=-18.0, period="2026-11-02")
        assert action == "suppress"
        assert state["episode"] == 1
    assert state["last_value"] == -18.0


def test_recovery_emits_once_and_enters_cooldown():
    _, state = fa.apply_state({}, "crossed", now=NOW, cooldown_hours=24)
    action, state = fa.apply_state(state, "quiet", now=NOW, cooldown_hours=24, value=-2.0)
    assert action == "recover" and state["state"] == "cooldown"
    assert state["cooldown_until"]
    # 已在阈值内再跑：idle，不发第二条恢复
    action, state = fa.apply_state(state, "quiet", now=NOW, cooldown_hours=24, value=-1.0)
    assert action == "idle"


def test_rebound_inside_cooldown_is_suppressed_then_counts_after_it():
    _, state = fa.apply_state({}, "crossed", now=NOW, cooldown_hours=24)
    _, state = fa.apply_state(state, "quiet", now=NOW, cooldown_hours=24)
    # 冷却期内再次越阈 → 抑制（防抖动）
    action, state = fa.apply_state(state, "crossed", now=NOW, cooldown_hours=24)
    assert action == "suppress" and state["episode"] == 1
    # 冷却期满后再次越阈 → 新事件
    from datetime import timedelta
    later = NOW + timedelta(hours=25)
    _, back = fa.apply_state(state, "quiet", now=later, cooldown_hours=24)
    action, back = fa.apply_state(back, "crossed", now=later + timedelta(hours=1),
                                  cooldown_hours=24)
    assert action == "alert" and back["episode"] == 2


def test_insufficient_data_never_flips_the_state():
    """数据不足既不能开也不能关一个告警——否则一次缺数会发出假恢复。"""
    _, state = fa.apply_state({}, "crossed", now=NOW, cooldown_hours=24)
    action, after = fa.apply_state(state, "insufficient_data", now=NOW, cooldown_hours=24)
    assert action == "suppress"
    assert after == state, "状态必须原样保留"


# ── 规则：change（6.3） ───────────────────────────────────────────────────

def _daily(start: str, days: int, value: float):
    from datetime import timedelta
    d0 = date.fromisoformat(start)
    return [((d0 + timedelta(days=i)).isoformat(), value + i * 0.0) for i in range(days)]


def test_change_rule_skips_thin_buckets_and_reports_them():
    pts = []
    pts += _daily("2026-10-05", 7, 1.00)     # 满周
    pts += _daily("2026-10-12", 7, 0.90)     # 满周（−10%）
    pts += _daily("2026-10-19", 1, 0.10)     # 薄桶：只有 1 天，若计入会造出 −89%
    pts += _daily("2026-10-26", 7, 0.88)
    rule = {"name": "t", "kind": "change", "concept": "c", "source_used": "s",
            "entity_code": "E", "entity_type": "commodity", "aggregate": "week_mean",
            "comparator": "lt", "threshold": -12.0, "min_history_periods": 2,
            "min_bucket_samples": 3, "unit": "percent"}
    notes: list[str] = []
    out = fa.eval_change_rule(Cur(series=pts, entity_ids={"E": 1}), rule, notes)
    assert out["verdict"] in ("quiet", "crossed")
    assert "2026-10-19" not in json.dumps(out)
    assert any("剔除薄桶" in n and "2026-10-19" in n for n in notes)


def test_change_rule_short_history_is_insufficient_not_quiet():
    pts = _daily("2026-11-02", 7, 1.0)       # 只有 1 个整周
    rule = {"name": "t", "kind": "change", "concept": "c", "source_used": "s",
            "entity_code": "E", "entity_type": "commodity", "aggregate": "week_mean",
            "comparator": "lt", "threshold": -12.0, "min_history_periods": 4,
            "min_bucket_samples": 1}
    out = fa.eval_change_rule(Cur(series=pts, entity_ids={"E": 1}), rule, [])
    assert out["verdict"] == "insufficient_data"
    assert "min_history_periods" in out["detail"]


def test_change_rule_zero_baseline_is_undefined_not_a_hundred_percent_drop():
    pts = _daily("2026-10-05", 7, 0.0) + _daily("2026-10-12", 7, 1.0)
    rule = {"name": "t", "kind": "change", "concept": "c", "source_used": "s",
            "entity_code": "E", "entity_type": "commodity", "aggregate": "week_mean",
            "comparator": "gt", "threshold": 0.0, "min_history_periods": 2,
            "min_bucket_samples": 1}
    out = fa.eval_change_rule(Cur(series=pts, entity_ids={"E": 1}), rule, [])
    assert out["verdict"] == "insufficient_data"
    assert "0" in out["detail"]


# ── 规则：ratio（6.3） ───────────────────────────────────────────────────

def _ratio_rule(**over):
    rule = {"name": "r", "kind": "ratio",
            "numerator": {"concept": "financials.operating_profit"},
            "denominator": {"concept": "financials.revenue"},
            "entities": [{"code": "AAA", "entity_type": "stock", "source_used": "s"}],
            "comparator": "lt", "threshold": -5.0, "min_history_periods": 1,
            "max_period_age_days": 200, "unit": "percent"}
    rule.update(over)
    return rule


def test_ratio_skips_periods_without_denominator():
    """营收缺失的那期不产生利润率（不是 0%）。"""
    pts = [("2026-06-30", -10.0), ("2026-09-30", 30.0)]   # 分子
    # 分母只有 2026-09-30
    cur = Cur(series=None, entity_ids={"AAA": 1})
    seq = iter([pts, [("2026-09-30", 100.0)]])
    cur.execute  # noqa: B018  (保持可读性)

    class TwoShot(Cur):
        def execute(self, sql, params=None):
            if "FROM semantic_observations" in " ".join(sql.split()):
                self._rows = next(seq)
            else:
                super().execute(sql, params)

    out = fa.eval_ratio_rule(TwoShot(entity_ids={"AAA": 1}), _ratio_rule(),
                             [], asof=date(2026, 10, 15))
    assert out["verdict"] == "quiet"          # 30/100 = +30%，不越阈
    assert out["per_entity"][0]["period"] == "2026-09-30"
    assert out["per_entity"][0]["value"] == 30.0


def test_ratio_staleness_gate_names_why_it_skipped():
    """HYFM 形态：营收序列停更，不得用旧期次冒充当期。"""
    seq = iter([[("2025-09-30", -10.0)], [("2025-09-30", 100.0)]])
    cur = Cur(entity_ids={"AAA": 1})

    real_execute = cur.execute

    def execute(sql, params=None):
        if "FROM semantic_observations" in " ".join(sql.split()):
            cur._rows = next(seq)
        else:
            real_execute(sql, params)

    cur.execute = execute
    out = fa.eval_ratio_rule(cur, _ratio_rule(), [], asof=date(2026, 11, 8))
    assert out["verdict"] == "insufficient_data"
    assert out["skipped"][0]["entity"] == "AAA"
    assert "401 天" in out["skipped"][0]["reason"] or "400 天" in out["skipped"][0]["reason"] \
        or "max_period_age_days" in out["skipped"][0]["reason"]


def test_ratio_reports_scale_so_a_micro_cap_is_recognisable():
    seq = iter([[("2026-06-30", -3_177_000.0)], [("2026-06-30", 3_550_000.0)]])
    cur = Cur(entity_ids={"AAA": 1})
    real_execute = cur.execute

    def execute(sql, params=None):
        if "FROM semantic_observations" in " ".join(sql.split()):
            cur._rows = next(seq)
        else:
            real_execute(sql, params)

    cur.execute = execute
    out = fa.eval_ratio_rule(cur, _ratio_rule(), [], asof=date(2026, 11, 8))
    assert out["verdict"] == "crossed"
    assert out["entity"] == "AAA"
    assert out["per_entity"][0]["denominator"] == 3_550_000.0
    assert round(out["value"], 2) == -89.49


# ── 事件台账（6.5） ──────────────────────────────────────────────────────

def test_event_ledger_is_idempotent_by_ticker_form_filed(tmp_path):
    rows = [{"key": "FLWS|10-Q|2026-11-05", "ticker": "FLWS"},
            {"key": "SMG|10-K|2026-11-06", "ticker": "SMG"}]
    path = tmp_path / "events.jsonl"
    new, existed = fa.append_ledger(path, rows)
    assert len(new) == 2 and existed == 0
    new2, existed2 = fa.append_ledger(path, rows)
    assert new2 == [] and existed2 == 2
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_event_ledger_dedupes_within_the_same_batch(tmp_path):
    """同一批里出现同键行也只写一次。

    2026-11-08 实测：台账文件 56 行 / 28 个键——只比对文件、不比对批内，
    重复行被两条都写进去（``crawl_items`` 逐次运行追加，同一申报拿到两行）。
    """
    path = tmp_path / "events.jsonl"
    row = {"key": "EDBL|8-K|2026-08-11|acc-1", "ticker": "EDBL"}
    new, existed = fa.append_ledger(path, [row, dict(row), dict(row)])
    assert len(new) == 1 and existed == 2
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_scan_events_filters_window_and_builds_stable_keys():
    cfg = {"events": {"enabled": True, "source": "sec-edgar-facts",
                      "tickers": ["FLWS"], "forms": ["10-Q", "8-K"], "window_days": 30,
                      "level": "info"}}
    items = [{"ticker": "FLWS", "form": "10-Q", "filed": "2026-11-05"},
             {"ticker": "FLWS", "form": "8-K", "filed": "2026-01-02"}]   # 窗外
    out = fa.scan_events(Cur(items=items), cfg, date(2026, 11, 8))
    assert [r["key"] for r in out] == ["FLWS|10-Q|2026-11-05"]
    assert out[0]["level"] == "info"


def test_same_day_same_form_items_stay_separate_by_accession():
    """同一天同一家公司可能发两份 8-K，只用 (ticker, form, filed) 会把它们并成一条。"""
    cfg = {"events": {"enabled": True, "source": "sec-edgar-facts",
                      "tickers": ["FLWS"], "forms": ["8-K"], "window_days": 30}}
    items = [{"ticker": "FLWS", "form": "8-K", "filed": "2026-11-05",
              "accession": "0001-26-000001"},
             {"ticker": "FLWS", "form": "8-K", "filed": "2026-11-05",
              "accession": "0001-26-000002"}]
    out = fa.scan_events(Cur(items=items), cfg, date(2026, 11, 8))
    assert len({r["key"] for r in out}) == 2


def test_repeated_items_from_multiple_runs_count_once():
    """``crawl_items`` 逐次运行追加：同一份申报会被后续运行再写一行。

    2026-11-08 走查实测：90 天窗内 56 行其实只有 28 个申报（每份 2 行）。
    台账按 key 幂等不会重记，但**返回条数**必须先按业务键收敛，否则对外报数虚高。
    """
    cfg = {"events": {"enabled": True, "source": "sec-edgar-facts",
                      "tickers": ["EDBL"], "forms": ["8-K"], "window_days": 90}}
    one = {"ticker": "EDBL", "form": "8-K", "filed": "2026-08-11",
           "accession": "0001477932-26-004877"}
    out = fa.scan_events(Cur(items=[one, dict(one), dict(one)]), cfg, date(2026, 11, 8))
    assert len(out) == 1, f"同一份申报被重复计成 {len(out)} 条"


def test_events_disabled_yields_nothing():
    assert fa.scan_events(Cur(items=[]), {"events": {"enabled": False}}, date(2026, 11, 8)) == []


# ── 开单归属（6.5 escalate） ─────────────────────────────────────────────

def test_ratio_rule_ticket_attributes_to_the_crossing_entity_source():
    """ratio 规则没有顶层 source_used——源挂在 entities[] 上。

    2026-11-08 演练实录：直接取 ``rule["source_used"]`` 抛 KeyError，浮点告警开不出单。
    """
    rule = _ratio_rule()
    rule["entities"] = [{"code": "AAA", "entity_type": "stock", "source_used": "sec-edgar-facts"},
                        {"code": "BBB", "entity_type": "stock", "source_used": "akshare"}]
    result = {"value": -89.49, "unit": "percent", "period": "2026-06-30", "entity": "BBB",
              "basis": {"period": "2026-06-30"}, "crossed_entities": ["BBB"]}
    doc = fa._ticket_for("r", rule, result, {}, NOW)
    assert doc["source"] == "akshare", "必须归到越线那家自己的源，而不是随便挑一个"
    assert doc["unit"] == "spiders/akshare/"
    assert fa.ticket_mod.validate_ticket(doc) == []
    assert "BBB" in doc["evidence"]["notes"]
    assert "越线实体" in doc["evidence"]["notes"]


def test_change_rule_ticket_uses_its_own_source():
    rule = {"name": "c", "kind": "change", "source_used": "dounan-flower-price",
            "description": "d", "comparator": "lt", "threshold": -12.0}
    result = {"value": 4.37, "unit": "percent", "period": "2026-11-02",
              "basis": {"period": "2026-11-02"}}
    doc = fa._ticket_for("c", rule, result, {}, NOW)
    assert doc["source"] == "dounan-flower-price"
    assert doc["unit"] == "spiders/dounan-flower-price/"
    assert fa.ticket_mod.validate_ticket(doc) == []


# ── 年频序列（3.3 Eurostat 主面积） ──────────────────────────────────────

def test_year_aggregate_makes_adjacent_change_a_yoy():
    """年频序列上「相邻两期变化」必须就是同比：year_mean 按年分桶，不是按月。"""
    pts = [("2019-01-01", 80.0), ("2020-01-01", 78.0),
           ("2021-01-01", 72.0), ("2022-01-01", 71.46)]
    rule = {"name": "eu", "kind": "change", "concept": "area.flower.all.planted",
            "source_used": "eurostat-api", "entity_code": "EU27_2020",
            "entity_type": "country", "aggregate": "year_mean",
            "comparator": "lt", "threshold": 0.0, "min_history_periods": 2,
            "min_bucket_samples": 1, "unit": "percent"}
    out = fa.eval_change_rule(Cur(series=pts, entity_ids={"EU27_2020": 1}), rule, [])
    assert out["period"] == "2022"
    assert out["baseline"]["period"] == "2021"
    assert round(out["value"], 4) == round((71.46 - 72.0) / 72.0 * 100.0, 4)
    assert out["verdict"] == "crossed"


def test_unknown_aggregate_still_raises():
    """新增 year_* 不能变成「什么都收」——拼错的聚合仍然报错，避免静默错分桶。"""
    with pytest.raises(ValueError):
        fa._period_key("2024-01-01", "week_median")


# ── 报告路径（演练/外部定时器会把 state 放到仓外） ─────────────────────

def test_display_path_handles_paths_outside_the_repo(tmp_path):
    """仓外 state 路径不许把整个 --rules 弄崩（只影响展示）。

    实测：把 defaults.state 指到 /tmp 时，`state_path.relative_to(REPO)` 直接
    抛 ValueError —— 报告写不出来、状态也没保存，等于整轮白跑。
    """
    inside = fa.REPO / "reports" / "x.json"
    assert fa._display_path(inside) == "reports/x.json"
    outside = tmp_path / "state.json"
    assert fa._display_path(outside) == str(outside.resolve())


# ── CLI 编排（cmd_rules 整条线：报告 + 状态 + 开单） ─────────────────────

class RoutingCur:
    """按 (concept, source, entity) 路由的假游标——cmd_rules 是整条编排，不能只给一条序列。"""

    def __init__(self, series_by_concept, entity_ids):
        self.series = series_by_concept      # {concept: [(date, value)]}
        self.entity_ids = entity_ids
        self._rows = []
        self.params = None

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.params = params
        if "FROM semantic_observations" in s:
            concept = params[0]
            self._rows = list(self.series.get(concept, []))
        elif "FROM entities WHERE code" in s:
            code = params[0]
            self._rows = [(self.entity_ids[code],)] if code in self.entity_ids else []
        else:
            raise AssertionError(f"未预期 SQL: {s[:60]}")

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def _cli_config(tmp_path, *, threshold: float, outdir_name: str = "out") -> dict:
    """一份可跑通的极小配置：1 条 change 规则，状态与报告都落在 tmp_path 下。"""
    return {
        "version": 1,
        "defaults": {
            "outdir": outdir_name,
            "state": str(tmp_path / "state" / "alert-state.json"),
            "events_ledger": str(tmp_path / "state" / "events.jsonl"),
            "cooldown_hours": 24,
        },
        "publication": {},
        "rules": [{
            "name": "cli_rule", "enabled": True, "kind": "change",
            "description": "CLI 编排测试", "concept": "c.test", "source_used": "src-test",
            "entity_code": "E", "entity_type": "commodity", "aggregate": "week_mean",
            "comparator": "lt", "threshold": threshold, "min_history_periods": 2,
            "min_bucket_samples": 1, "unit": "percent",
        }],
        "events": {"enabled": False},
    }


def _weekly_points():
    pts = []
    for week, value in (("2026-10-05", 1.00), ("2026-10-12", 0.80)):
        for i in range(7):
            from datetime import date, timedelta
            d = date.fromisoformat(week) + timedelta(days=i)
            pts.append((d.isoformat(), value))
    return pts


def test_cmd_rules_writes_report_state_and_ticket_end_to_end(tmp_path):
    """整条编排：越阈 → 报告 + 状态(firing) + 工单；再跑一次 → suppress 且不重复开单。"""
    outdir = tmp_path / "out"
    outdir.mkdir()
    cfg = _cli_config(tmp_path, threshold=0.0)      # −20% < 0 → 越阈
    cur = RoutingCur({"c.test": _weekly_points()}, {"E": 1})
    now = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)

    code = fa.cmd_rules(cur, cfg, outdir, now, dry_run=False, do_escalate=True)
    assert code == 1, "有越阈时退出码应为 1"

    report = json.loads((outdir / f"rules-{now.date().isoformat()}.json").read_text())
    assert report["rules"][0]["action"] == "alert"
    state = json.loads((tmp_path / "state" / "alert-state.json").read_text())
    assert state["rules"]["cli_rule"]["state"] == "firing"
    tickets = sorted((outdir / "tickets").glob("*.yaml"))
    assert len(tickets) == 1

    # 第二轮：仍越阈 → suppress，不开第二张单
    code2 = fa.cmd_rules(cur, cfg, outdir, now, dry_run=False, do_escalate=True)
    assert code2 == 0, "持续越阈不应再触发告警"
    assert len(sorted((outdir / "tickets").glob("*.yaml"))) == 1


def test_cmd_rules_dry_run_writes_no_state(tmp_path):
    outdir = tmp_path / "out"
    outdir.mkdir()
    cfg = _cli_config(tmp_path, threshold=0.0)
    now = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
    fa.cmd_rules(RoutingCur({"c.test": _weekly_points()}, {"E": 1}), cfg, outdir,
                 now, dry_run=True, do_escalate=False)
    assert not (tmp_path / "state" / "alert-state.json").exists()
    assert (outdir / f"rules-{now.date().isoformat()}.json").exists(), "报告照写"
