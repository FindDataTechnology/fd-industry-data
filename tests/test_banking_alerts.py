"""银行三层预警的契约（add-banking-source-alerting 2.x–5.x）。

钉住四件**会静默出错**的事：

1. **方向**：「不得低于」类监管指标，**满足要求不得告警**。踩过：资本充足率 13.77
   高于红线 10.5 被判成 firing——内核早期拿比较结果直接当告警条件。
2. **降级按规则依赖**：事件源从未排期（health=contract）不得把本来健康的阈值层打成 pending，
   否则永远不告警。
3. **缺数不冒充达标也不冒充恢复**：无观测不进分母，缺数不改状态。
4. **不强归属**：处罚主体匹配不上就标 unattributed，错归属比不归属更坏。
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

spec = importlib.util.spec_from_file_location(
    "banking_alerts", ROOT / "scripts" / "banking_alerts.py")
ba = importlib.util.module_from_spec(spec)
sys.modules["banking_alerts"] = ba      # @dataclass 需要模块已注册，否则 __module__ 解析失败
spec.loader.exec_module(ba)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 8)


@pytest.fixture()
def cfg():
    return {
        "threshold_rules": [
            {"rule_id": "cap", "indicator_zh": "资本充足率", "operator": ">=",
             "value": 10.5, "enabled": True, "depends_on": "cn-report",
             "citation": "某办法", "citation_verified": "document"},
        ],
        "indicator_concepts": {"资本充足率": "financials.capital_adequacy_ratio"},
        "monitored_universe": {"size": 42},
    }


def _series(**by_entity):
    return {("financials.capital_adequacy_ratio", code): {"name": name, "points": pts}
            for code, (name, pts) in by_entity.items()}


# --- 方向 ---------------------------------------------------------------------

def test_satisfied_regulatory_requirement_does_not_alert(cfg):
    """资本充足率 13.77 ≥ 10.5 是**合规**，绝不能告警。"""
    s = _series(**{"600036": ("招商银行", [("2025-12-31", 13.77)])})
    verdicts, states, _ = ba.threshold_verdicts(cfg, s, {}, now=NOW)
    assert verdicts[0]["verdict"] == "quiet"
    assert verdicts[0]["events"] == []


def test_violated_requirement_alerts_once(cfg):
    """跌破红线要告警，且同一报告期只报一次。"""
    s = _series(**{"600036": ("招商银行", [("2025-12-31", 9.1)])})
    v1, states, _ = ba.threshold_verdicts(cfg, s, {}, now=NOW)
    assert v1[0]["verdict"] == "firing" and len(v1[0]["events"]) == 1
    v2, _, _ = ba.threshold_verdicts(cfg, s, states, now=NOW)
    assert v2[0]["verdict"] == "firing" and v2[0]["events"] == []


def test_recovery_after_violation_emits_resolved(cfg):
    bad = _series(**{"600036": ("招商银行", [("2025-12-31", 9.1)])})
    _, states, _ = ba.threshold_verdicts(cfg, bad, {}, now=NOW)
    good = _series(**{"600036": ("招商银行", [("2026-03-31", 12.0)])})
    v, _, _ = ba.threshold_verdicts(cfg, good, states, now=NOW)
    assert v[0]["verdict"] == "resolved" and len(v[0]["events"]) == 1


# --- 降级只按本规则依赖 ---------------------------------------------------------

def test_degradation_is_scoped_to_rule_dependency(cfg):
    """事件源不健康不得把阈值层降级——它依赖的是 cn-report。"""
    s = _series(**{"600036": ("招商银行", [("2025-12-31", 9.1)])})
    v, _, _ = ba.threshold_verdicts(cfg, s, {}, now=NOW,
                                    unhealthy_sources={"eastmoney-disclosure-plan"})
    assert v[0]["verdict"] == "firing", "依赖源健康时仍应正常告警"


def test_unhealthy_dependency_downgrades_to_pending(cfg):
    s = _series(**{"600036": ("招商银行", [("2025-12-31", 9.1)])})
    v, _, _ = ba.threshold_verdicts(cfg, s, {}, now=NOW, unhealthy_sources={"cn-report"})
    assert v[0]["verdict"] == "pending" and v[0]["events"] == [], \
        "源不健康：既不擅自 resolved，也不新发 firing"


# --- 覆盖 ---------------------------------------------------------------------

def test_coverage_declares_partial_scope(cfg):
    s = _series(**{"600036": ("招商银行", [("2025-12-31", 13.0)])})
    _, _, cov = ba.threshold_verdicts(cfg, s, {}, now=NOW)
    assert cov["covered"] == ["600036"]
    block = ba.coverage_block(cfg, cov["covered"], 0)
    assert block["covered"] == 1 and block["universe"] == 42
    assert "not_covered" in block["note"]


def test_no_points_is_not_scored(cfg):
    """空序列既不出判定也不进覆盖，绝不能当「达标」。"""
    s = _series(**{"600036": ("招商银行", [])})
    v, _, cov = ba.threshold_verdicts(cfg, s, {}, now=NOW)
    assert v == []


def test_disabled_rule_is_not_evaluated(cfg):
    cfg["threshold_rules"][0]["enabled"] = False
    cfg["threshold_rules"][0]["blocked_reason"] = "出处待核"
    s = _series(**{"600036": ("招商银行", [("2025-12-31", 9.1)])})
    v, _, _ = ba.threshold_verdicts(cfg, s, {}, now=NOW)
    assert v == []


# --- 披露逾期 -----------------------------------------------------------------

def _disc(code="601398", rtype="年报", fy=2025, due="2026-03-31", published=None):
    return {"stock_code": code, "stock_name": "工商银行", "report_type": rtype,
            "fiscal_year": fy, "first_appoint_date": due, "change_date": None,
            "actual_publish_date": published}


def test_overdue_fires_once_then_silent():
    rows = [_disc()]
    v1, states, counts = ba.disclosure_verdicts(rows, {}, today=TODAY, now=NOW)
    assert counts["overdue"] == 1
    assert v1[0]["verdict"] == "firing" and len(v1[0]["events"]) == 1
    v2, _, _ = ba.disclosure_verdicts(rows, states, today=TODAY, now=NOW)
    assert v2[0]["events"] == [], "同一报告期不得重复原谅"


def test_published_report_does_not_fire():
    rows = [_disc(published="2026-03-28")]
    v, _, counts = ba.disclosure_verdicts(rows, {}, today=TODAY, now=NOW)
    assert counts["overdue"] == 0 and v[0]["verdict"] == "quiet"


def test_late_publication_resolves():
    _, states, _ = ba.disclosure_verdicts([_disc()], {}, today=TODAY, now=NOW)
    v, _, _ = ba.disclosure_verdicts([_disc(published="2026-11-01")], states,
                                     today=TODAY, now=NOW)
    assert v[0]["verdict"] == "resolved" and len(v[0]["events"]) == 1


def test_future_due_date_does_not_fire():
    rows = [_disc(due="2026-12-31")]
    v, _, counts = ba.disclosure_verdicts(rows, {}, today=TODAY, now=NOW)
    assert counts["overdue"] == 0 and v[0]["verdict"] == "quiet"


def test_rescheduled_date_is_used_over_first():
    """改期后应按最新改期日判定，不能用首次预约日提前报警。"""
    row = _disc(due="2026-01-31")
    row["change_date"] = "2026-12-20"
    v, _, counts = ba.disclosure_verdicts([row], {}, today=TODAY, now=NOW)
    assert counts["overdue"] == 0


def test_disclosure_counts_total_includes_skipped():
    rows = [_disc(), {"stock_code": "600036", "report_type": "年报", "fiscal_year": 2025,
                      "first_appoint_date": None, "change_date": None,
                      "actual_publish_date": None}]
    _, _, counts = ba.disclosure_verdicts(rows, {}, today=TODAY, now=NOW)
    assert counts["total"] == 2 and counts["no_due_date"] == 1


# --- 处罚增量 -----------------------------------------------------------------

def _pen(doc_id, party="中国工商银行股份有限公司"):
    return {"doc_id": doc_id, "party": party, "office": "某监管分局",
            "penalty_no": f"处字〔2026〕{doc_id}号", "penalty_content": "罚款"}


def test_penalty_increment_is_idempotent():
    rows = [_pen("d1"), _pen("d2")]
    v1, states, counts = ba.penalty_verdicts(rows, {}, now=NOW, aliases={})
    assert counts["new"] == 2 and len(v1) == 2
    v2, _, counts2 = ba.penalty_verdicts(rows, states, now=NOW, aliases={})
    assert counts2["new"] == 0 and v2 == []


def test_penalty_attribution_matches_alias():
    rows = [_pen("d1")]
    v, _, _ = ba.penalty_verdicts(rows, {}, now=NOW,
                                  aliases={"中国工商银行股份有限公司": "601398"})
    att = v[0]["parties"][0]["attribution"]
    assert att["status"] == "matched" and att["code"] == "601398"


def test_penalty_attribution_matches_normalized_name():
    """处罚原文常带「中国…股份有限公司」或「及相关责任人」，归一名要能对上。"""
    rows = [{"doc_id": "d2", "party": "中国光大银行股份有限公司及相关责任人",
             "office": "局", "penalty_no": "x", "penalty_content": "罚款"}]
    v, _, _ = ba.penalty_verdicts(rows, {}, now=NOW,
                                  aliases={"中国光大银行股份有限公司": "601818"})
    assert v[0]["parties"][0]["attribution"]["code"] == "601818"


def test_multiple_parties_in_one_document_are_all_kept():
    """一份文书含多个主体时，事件按文书去重，但主体一个都不能丢。"""
    rows = [{"doc_id": "d3", "party": "中国工商银行股份有限公司", "office": "局",
             "penalty_no": "x", "penalty_content": "罚款"},
            {"doc_id": "d3", "party": "某某村镇银行", "office": "局",
             "penalty_no": "x", "penalty_content": "罚款"}]
    v, _, counts = ba.penalty_verdicts(rows, {}, now=NOW,
                                       aliases={"中国工商银行股份有限公司": "601398"})
    assert counts["new"] == 1 and counts["documents"] == 1
    assert len(v[0]["parties"]) == 2


def test_penalty_unmatched_is_unattributed_not_forced():
    """匹配不上就标 unattributed 并保留原名，绝不硬塞给某家银行。"""
    v, _, _ = ba.penalty_verdicts([_pen("d9", party="某某村镇银行")], {}, now=NOW, aliases={})
    att = v[0]["parties"][0]["attribution"]
    assert att["status"] == "unattributed" and att["raw"] == "某某村镇银行"


def test_non_bank_institution_is_not_attributed():
    """保险主体/自然人不得归到银行名下（真实数据里确实混着）。"""
    v, _, _ = ba.penalty_verdicts([_pen("d9", party="民生人寿保险股份有限公司及相关责任人")],
                                  {}, now=NOW, aliases={})
    assert v[0]["parties"][0]["attribution"]["reason"] == "not-a-bank"


def test_individual_employee_is_unattributed():
    """个人处罚不得记到银行头上——归一后是「交通银行」，必须被自然人识别拦住。"""
    v, _, _ = ba.penalty_verdicts([_pen("d9", party="原交通银行股份有限公司员工仇征")],
                                  {}, now=NOW, aliases={"中国交通银行股份有限公司": "601328"})
    att = v[0]["parties"][0]["attribution"]
    assert att["status"] == "unattributed" and att["reason"] == "individual"


def test_penalty_empty_party_is_unattributed():
    v, _, _ = ba.penalty_verdicts([_pen("d9", party="")], {}, now=NOW, aliases={})
    assert v[0]["parties"][0]["attribution"]["reason"] == "empty-party"


# --- 通知正文 -----------------------------------------------------------------

def test_unhealthy_source_prefixes_message(cfg):
    health = ba.SourceHealth("eastmoney-disclosure-plan", False, "contract", "never ran")
    msgs = ba.compose_messages(
        [{"rule_id": "r", "indicator_zh": "资本充足率", "name": "招商银行",
          "events": [{"kind": "firing", "key": "2025-12-31"}]}],
        unhealthy=[health])
    assert "信息可能不完整" in msgs[0]["body"]
    assert msgs[0]["subject"].startswith("[银行预警]")


def test_resolved_message_subject_is_distinct():
    msgs = ba.compose_messages(
        [{"rule_id": "r", "indicator_zh": "资本充足率", "name": "招商银行",
          "events": [{"kind": "resolved", "key": "2025-12-31"}]}], unhealthy=[])
    assert msgs[0]["subject"].startswith("[银行预警恢复]")


def test_verdict_without_events_produces_no_message():
    msgs = ba.compose_messages(
        [{"rule_id": "r", "indicator_zh": "x", "name": "y", "events": []}], unhealthy=[])
    assert msgs == []


def test_config_recipients_injected_before_smtp_config_is_built():
    """收件人兜底必须在构造 SmtpConfig **之前**注入。

    踩过：smtp_config_from_env() 缺收件人直接返回 None，事后再改 cfg.recipients
    是徒劳——配置里的收件人在流水线里形同虚设，而通知会被记成 skipped（看起来
    「没配通道」而非「配了没用上」）。
    """
    import inspect
    src = inspect.getsource(ba.main)
    assert 'smtp_env["FD_ALERT_SMTP_TO"]' in src
    assert "smtp_config_from_env(smtp_env)" in src
    assert src.index('smtp_env["FD_ALERT_SMTP_TO"]') < src.index("smtp_config_from_env(smtp_env)")


def test_repo_config_declares_notify_recipients():
    """收件人是配置（可版本化），凭据才是机密——两者不能混在一处。"""
    cfg = ba.load_config()
    rec = (cfg.get("notify") or {}).get("recipients") or []
    assert rec and all("@" in r for r in rec)


# --- 配置纪律 -----------------------------------------------------------------

def _write_cfg(tmp_path, **override):
    base = {
        "threshold_rules": [{"rule_id": "cap", "indicator_zh": "资本充足率",
                             "operator": ">=", "value": 10.5, "enabled": True,
                             "citation": "某办法", "citation_verified": "document"}],
        "indicator_concepts": {"资本充足率": "financials.capital_adequacy_ratio"},
    }
    base.update(override)
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    return p


def test_config_rejects_enabled_rule_without_citation(tmp_path):
    p = _write_cfg(tmp_path)
    d = json.loads(p.read_text(encoding="utf-8"))
    d["threshold_rules"][0].pop("citation")
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="citation"):
        ba.load_config(p)


def test_config_rejects_enabled_unverified_citation(tmp_path):
    p = _write_cfg(tmp_path)
    d = json.loads(p.read_text(encoding="utf-8"))
    d["threshold_rules"][0]["citation_verified"] = False
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="未核"):
        ba.load_config(p)


def test_config_rejects_disabled_rule_without_reason(tmp_path):
    p = _write_cfg(tmp_path)
    d = json.loads(p.read_text(encoding="utf-8"))
    d["threshold_rules"][0]["enabled"] = False
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="blocked_reason"):
        ba.load_config(p)


def test_config_rejects_npl_threshold(tmp_path):
    p = _write_cfg(tmp_path)
    d = json.loads(p.read_text(encoding="utf-8"))
    d["threshold_rules"][0]["indicator_zh"] = "不良率"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="不良率"):
        ba.load_config(p)


def test_repo_config_loads_and_has_no_npl_threshold():
    """仓内真实配置必须能过纪律校验（否则巡检一启动就红）。"""
    cfg = ba.load_config()
    assert all(r.get("depends_on") for r in cfg["threshold_rules"])
    assert "不良率" not in {r["indicator_zh"] for r in cfg["threshold_rules"]}
    for r in cfg["threshold_rules"]:
        if not r["enabled"]:
            assert r.get("unlock"), f"{r['rule_id']} 停用但没写 unlock"


def test_dry_run_reads_state_so_drill_matches_reality():
    """演练必须反映「现在真跑会发生什么」：读状态、不写回。

    早先 dry-run 从空状态起算，于是每次演练都把历史事件重新算成「新增」，
    与真实运行结果不符——演练就此失去意义。
    """
    import inspect
    src = inspect.getsource(ba.main)
    assert "states = load_state(STATE)" in src
    assert "{} if args.dry_run" not in src


def test_uncaught_exception_exits_2_not_1():
    """崩溃必须 exit 2：1 是「有通知发送失败」的预期语义，混用会让流水线假绿。"""
    def boom(argv=None):
        raise RuntimeError("boom")

    orig = ba.main
    ba.main = boom
    try:
        assert ba._guarded_main([]) == 2
    finally:
        ba.main = orig


def test_items_sql_is_one_template():
    """取 crawl_items 只有一份 SQL 模板。

    踩过：驱动路径与 psql 路径各拼一份，前者漏了子查询收尾 `) p`，直到真跑才
    SyntaxError。现在只有一条路径、一个模板，``{src}`` 只出现一次。
    """
    assert ba._ITEMS_SQL.count("{src}") == 1
    assert ba._ITEMS_SQL.strip().endswith(") p")
    assert "json_agg" in ba._ITEMS_SQL
    import inspect
    assert inspect.getsource(ba.fetch_crawl_items).count("_ITEMS_SQL.format(") == 1


def test_readers_use_the_shared_pg_fallback():
    """三处取数都用共享的 ``connect_readonly``，不许再自带 psql 实现。

    本条是**去重约束**：本文件曾自写 _psql/_sql_literal/_have_psycopg2，
    与 fd_industry_data.pg_fallback 重复；现在共享模块是唯一实现
    （psycopg3 → psycopg2 → psql，且权限不足时 SystemExit(3)）。
    """
    import inspect
    for fn in (ba.fetch_ratio_series, ba.fetch_source_runs, ba.fetch_crawl_items):
        src = inspect.getsource(fn)
        assert "connect_readonly(" in src, fn.__name__
    assert not hasattr(ba, "_psql"), "不得保留本地 psql 实现"
    assert not hasattr(ba, "_sql_literal"), "不得保留本地字面量转义"


# --- 健康级 -----------------------------------------------------------------

def test_no_runs_is_scheduled_absent_not_healthy():
    """一次都没跑过是调度面故障，不是「健康」也不是「证据不足」。"""
    h = ba.judge_health("nfra-penalty", [], NOW)
    assert h.healthy is False and h.category == "contract"


def test_healthy_source_with_successes():
    from fd_industry_data.health.triage import Run
    runs = [Run(started_at=(NOW.replace(hour=h)).isoformat(), status="success", rows=10)
            for h in (9, 10, 11)]
    assert ba.judge_health("eastmoney-disclosure-plan", runs, NOW).healthy is True
