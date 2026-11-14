"""巡检器：遥测→分诊→出单→（注入式）SUBMIT/STATUS 接线。全离线。"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.health import identity, inspector  # noqa: E402
from fd_industry_data.health.config import DEFAULTS  # noqa: E402
from fd_industry_data.health.ticket import (  # noqa: E402
    dump_ticket,
    load_ticket,
    new_ticket,
)

NOW = datetime.now(timezone.utc)


def _mk_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "spiders").mkdir(parents=True)
    for slug, freq in (("alpha", "daily"), ("beta", "daily"),
                       ("gamma", "daily"), ("good", "daily"), ("noext", "on_demand")):
        d = repo / "spiders" / slug
        d.mkdir()
        (d / "manifest.yaml").write_text(yaml.safe_dump({
            "version": "1", "name": slug,
            "functions": [{"command": f"run_{slug}", "frequency": freq}],
        }))
    return repo


def _runs(source, *, fails=0, error="", http=None, last_success_hours=1.0, watermark=None):
    recs = []
    if last_success_hours is not None:
        recs.append({"source": source, "status": "success",
                     "started_at": (NOW - timedelta(hours=last_success_hours)).isoformat(),
                     "rows": 10, "error": "", "http_status": 200,
                     "watermark": watermark})
    for i in range(fails):
        recs.append({"source": source, "status": "failed",
                     "started_at": (NOW - timedelta(minutes=30 - i)).isoformat(),
                     "rows": 0, "error": error, "http_status": http})
    return recs


def _snapshot(tmp_path, records):
    p = tmp_path / "runs.json"
    import json
    p.write_text(json.dumps(records))
    return p


def _mk_ticket(outdir, source, terminal=None, category=None, evidence_signature=None):
    doc = new_ticket(source=source, unit=f"spiders/{source}/", evidence={},
                     verify_commands=["echo"], category=category,
                     evidence_signature=evidence_signature)
    if terminal:
        doc["terminal"] = terminal
    fname = doc["ticket_id"] + ".yaml"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / fname).write_text(dump_ticket(doc))
    return outdir / fname


# ── 遥测载入与分组 ────────────────────────────────────────────────────────

def test_group_runs_sorts_by_started_at(tmp_path):
    records = _runs("alpha", fails=2, error="x") + _runs("beta")
    grouped = inspector.group_runs(records)
    assert set(grouped) == {"alpha", "beta"}
    times = [r.started_at for r in grouped["alpha"]]
    assert times == sorted(times)


def test_psql_parse(monkeypatch):
    class P:
        returncode = 0
        stderr = ""
        stdout = ("alpha\tsuccess\t2026-10-01T00:00:00Z\t10\t\t200\n"
                  "alpha\tfailed\t2026-10-02T00:00:00Z\t0\tconnection timed out\t\n")
    monkeypatch.setattr(inspector.subprocess, "run", lambda *a, **k: P())
    rows = inspector.load_runs_from_psql(DEFAULTS, "postgres://x")
    assert rows[0]["source"] == "alpha" and rows[0]["rows"] == 10 and rows[0]["http_status"] == 200
    assert rows[1]["error"] == "connection timed out" and rows[1]["http_status"] is None


def test_manifest_period_hours(tmp_path):
    repo = _mk_repo(tmp_path)
    assert inspector.manifest_period_hours(repo, "alpha", DEFAULTS) == 24.0
    assert inspector.manifest_period_hours(repo, "noext", DEFAULTS) is None
    cfg = replace(DEFAULTS, expected_period_overrides={"alpha": 12.0})
    assert inspector.manifest_period_hours(repo, "alpha", cfg) == 12.0


# ── data_period 判超期（add-fleet-lifecycle-adjudication 1.3） ──────────────

def _mk_dp_repo(tmp_path, **specs):
    """specs: slug -> manifest 增量字段（data_period/parked/functions.frequency）。"""
    repo = tmp_path / "repo"
    (repo / "spiders").mkdir(parents=True)
    for slug, extra in specs.items():
        d = repo / "spiders" / slug
        d.mkdir()
        doc = {"version": "1", "name": slug}
        freq = extra.pop("frequency", "daily")
        doc["functions"] = [{"command": f"run_{slug}", "frequency": freq}]
        doc.update(extra)
        (d / "manifest.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True))
    return repo


def test_manifest_data_period_reads_declaration_and_parked(tmp_path):
    repo = _mk_dp_repo(tmp_path,
                       plain={"data_period": "month"},
                       parked_src={"data_period": "day", "parked": True},
                       legacy={},
                       dirty={"data_period": "weekly"})   # 非法值按未声明处理
    assert inspector.manifest_data_period(repo, "plain") == ("month", False)
    assert inspector.manifest_data_period(repo, "parked_src") == ("day", True)
    assert inspector.manifest_data_period(repo, "legacy") == (None, False)
    assert inspector.manifest_data_period(repo, "dirty") == (None, False)
    assert inspector.manifest_data_period(repo, "absent") == (None, False)


def test_expected_period_tolerance_mapping(tmp_path):
    """容忍窗映射：day→3d、month→35d、year→370d（除以 freshness_multiplier
    使 triage 的健康边界恰为容忍窗）；irregular 与 parked → inf。"""
    repo = _mk_dp_repo(tmp_path,
                       day_src={"data_period": "day"},
                       month_src={"data_period": "month"},
                       year_src={"data_period": "year"},
                       irr={"data_period": "irregular"},
                       parked_src={"data_period": "month", "parked": True},
                       legacy={})
    m = DEFAULTS.thresholds.freshness_multiplier
    assert inspector.expected_period_hours(repo, "day_src", DEFAULTS) == 72.0 / m
    assert inspector.expected_period_hours(repo, "month_src", DEFAULTS) == 840.0 / m
    assert inspector.expected_period_hours(repo, "year_src", DEFAULTS) == 8880.0 / m
    assert inspector.expected_period_hours(repo, "irr", DEFAULTS) == float("inf")
    assert inspector.expected_period_hours(repo, "parked_src", DEFAULTS) == float("inf")
    # 缺 data_period 回退 legacy：functions.frequency=daily → 24h
    assert inspector.expected_period_hours(repo, "legacy", DEFAULTS) == 24.0


def test_expected_period_legacy_and_override_precedence(tmp_path):
    """运维覆盖先于 data_period（既有通道语义不变）；无覆盖走容忍窗。"""
    repo = _mk_dp_repo(tmp_path, alpha={"data_period": "month"})
    cfg = replace(DEFAULTS, expected_period_overrides={"alpha": 12.0})
    assert inspector.expected_period_hours(repo, "alpha", cfg) == 12.0
    m = DEFAULTS.thresholds.freshness_multiplier
    assert inspector.expected_period_hours(repo, "alpha", DEFAULTS) == 840.0 / m


def test_plan_day_source_overdue_flagged(tmp_path):
    """day 源超容忍窗（>3d 无成功）必出单，不再健康短路。"""
    repo = _mk_dp_repo(tmp_path, late={"data_period": "day"})
    records = _runs("late", last_success_hours=24 * 4)   # 4 天前成功，超 3 天窗
    plan = inspector.plan_tickets(inspector.group_runs(records), repo, DEFAULTS, NOW)
    assert plan.emit and plan.emit[0].source == "late"


def test_plan_month_source_within_tolerance_healthy(tmp_path):
    """month 源 30 天前有成功 → 窗内健康（旧口径按 frequency=720h*2.5 也健康，
    但缺 frequency 的源此前会被 skip——现在必须能判定）。"""
    repo = _mk_dp_repo(tmp_path, stat={"data_period": "month", "frequency": "on_demand"})
    records = _runs("stat", last_success_hours=24 * 30)  # 30 天 < 35 天窗
    plan = inspector.plan_tickets(inspector.group_runs(records), repo, DEFAULTS, NOW)
    assert plan.healthy == 1 and plan.emit == []
    assert plan.skipped_unknown_period == []


def test_plan_irregular_never_overdue_only_consecutive_failures(tmp_path):
    """irregular：不判超期（久无成功但无失败=健康）；连续失败达阈值才出单。"""
    repo = _mk_dp_repo(tmp_path, feed={"data_period": "irregular", "frequency": "on_demand"})
    old = _runs("feed", last_success_hours=24 * 400)      # 400 天无失败 → 健康
    plan = inspector.plan_tickets(inspector.group_runs(old), repo, DEFAULTS, NOW)
    assert plan.healthy == 1 and plan.emit == []

    failing = _runs("feed", fails=3, error="connection timed out",
                    last_success_hours=24 * 400)          # 连续失败 3 次 → 出单
    plan2 = inspector.plan_tickets(inspector.group_runs(failing), repo, DEFAULTS, NOW)
    assert plan2.emit and plan2.emit[0].source == "feed"

    few = _runs("feed", fails=2, error="connection timed out",
                last_success_hours=24 * 400)              # 未达阈值 → 健康（只判连续失败）
    plan3 = inspector.plan_tickets(inspector.group_runs(few), repo, DEFAULTS, NOW)
    assert plan3.healthy == 1 and plan3.emit == []


def test_plan_parked_exempt_from_overdue(tmp_path):
    """parked:true 豁免超期：超窗但无失败 → 健康（不再次骚扰已挂起源）。"""
    repo = _mk_dp_repo(tmp_path, resting={"data_period": "day", "parked": True})
    records = _runs("resting", last_success_hours=24 * 30)  # 远超 day 容忍窗
    plan = inspector.plan_tickets(inspector.group_runs(records), repo, DEFAULTS, NOW)
    assert plan.healthy == 1 and plan.emit == []


def test_plan_missing_data_period_falls_back_to_legacy(tmp_path):
    """缺 data_period 回退 legacy：functions.frequency 可推断则照旧判定，
    不可推断仍进 skipped_unknown_period（向后兼容）。"""
    repo = _mk_dp_repo(tmp_path, hasfreq={}, nofreq={"frequency": "on_demand"})
    records = (
        _runs("hasfreq", fails=3, error="connection timed out")
        + _runs("nofreq", fails=1, error="x")
    )
    plan = inspector.plan_tickets(inspector.group_runs(records), repo, DEFAULTS, NOW)
    assert plan.emit and plan.emit[0].source == "hasfreq"
    assert plan.skipped_unknown_period == ["nofreq"]


def test_plan_data_period_clears_skipped_unknown_period(tmp_path):
    """此前因周期未知被跳过的源（bls：on_demand + schedule）声明 data_period
    后必须被判定——22 个 skipped_unknown_period 清零的机制保证。"""
    repo = _mk_dp_repo(tmp_path, bls={"data_period": "day", "frequency": "on_demand"})
    records = _runs("bls", last_success_hours=24 * 10)   # 10 天 > 3 天窗 → 超期出单
    plan = inspector.plan_tickets(inspector.group_runs(records), repo, DEFAULTS, NOW)
    assert plan.skipped_unknown_period == []
    assert plan.emit and plan.emit[0].source == "bls"


def test_psql_parse_with_optional_watermark_column(monkeypatch):
    """telemetry_sql 扩展第 7 列（数据水位）时自动并入；缺列保持 None（向后兼容）。"""
    class P:
        returncode = 0
        stderr = ""
        stdout = (
            "alpha\tsuccess\t2026-10-01T00:00:00Z\t10\t\t200\t2026-09-30\n"
            "alpha\tfailed\t2026-10-02T00:00:00Z\t0\tselector not found\t200\t\n"
        )
    monkeypatch.setattr(inspector.subprocess, "run", lambda *a, **k: P())
    rows = inspector.load_runs_from_psql(DEFAULTS, "postgres://x")
    assert rows[0]["watermark"] == "2026-09-30"
    assert rows[1]["watermark"] is None


def test_plan_classifies_source_revision(tmp_path):
    """HTTP 200 但结构变异（golden drift/字段缺失/选择器失效）-> source-revision。"""
    repo = _mk_repo(tmp_path)
    records = _runs("alpha", fails=3, error="golden drift: missing field 'close'",
                    http=200, last_success_hours=100)
    plan = inspector.plan_tickets(inspector.group_runs(records), repo, DEFAULTS, NOW)
    assert [e.verdict.category for e in plan.emit] == ["source-revision"]
    assert "源改版" in plan.emit[0].verdict.suggested_action


def test_plan_network_sample_stays_network(tmp_path):
    """网络故障样例（超时/5xx）不得被误分到 source-revision。"""
    repo = _mk_repo(tmp_path)
    records = (
        _runs("alpha", fails=3, error="connection timed out", last_success_hours=100)
        + _runs("beta", fails=3, error="bad gateway", http=502, last_success_hours=100)
    )
    plan = inspector.plan_tickets(inspector.group_runs(records), repo, DEFAULTS, NOW)
    by_source = {e.source: e.verdict.category for e in plan.emit}
    assert by_source == {"alpha": "network", "beta": "network"}


# ── 规划：3 个故障源各归其类，健康源与无周期源跳过 ────────────────────────

def test_plan_emits_three_categories(tmp_path):
    repo = _mk_repo(tmp_path)
    records = (
        _runs("alpha", fails=3, error="connection timed out")            # network
        + _runs("beta", fails=3, error="403 forbidden cloudflare", http=403)  # contract+anti-bot
        + _runs("gamma", fails=3, error="404 not found domain gone", last_success_hours=720)  # source-dead
        + _runs("good")                                                   # healthy
        + _runs("noext", fails=3, error="x")                              # unknown period
    )
    runs_by_source = inspector.group_runs(records)
    plan = inspector.plan_tickets(runs_by_source, repo, DEFAULTS, NOW)
    by_source = {e.source: e.verdict for e in plan.emit}
    assert set(by_source) == {"alpha", "beta", "gamma"}
    assert by_source["alpha"].category == "network"
    assert by_source["beta"].category == "contract" and by_source["beta"].suspected_anti_bot
    assert by_source["gamma"].category == "source-dead"
    assert plan.healthy == 1
    assert plan.skipped_unknown_period == ["noext"]


# ── 落单：零写入语义、日限额、去重 ────────────────────────────────────────

def test_write_plan_emits_three_with_evidence(tmp_path):
    repo = _mk_repo(tmp_path)
    outdir = tmp_path / "tickets"
    records = (
        _runs("alpha", fails=3, error="connection timed out")
        + _runs("beta", fails=3, error="403 forbidden cloudflare", http=403)
        + _runs("gamma", fails=3, error="404 not found domain gone", last_success_hours=720)
    )
    plan = inspector.plan_tickets(inspector.group_runs(records), repo, DEFAULTS, NOW)
    stats = inspector.write_plan(plan, repo, outdir, DEFAULTS, NOW)
    assert len(stats["written"]) == 3
    files = sorted(outdir.glob("*.yaml"))
    assert len(files) == 3
    doc = load_ticket(files[0])
    assert doc["evidence"]["failing_runs"], "工单必须带失败证据"
    assert doc["verify"]["commands"] and doc["verify"]["commands"][0].startswith("python3 scripts/health_verify.py")


def test_write_plan_ticket_carries_last_success_and_watermark(tmp_path):
    """工单证据含最近成功时间与水位（水位来自遥测第 7 列；缺省如实标 unknown）。"""
    repo = _mk_repo(tmp_path)
    outdir = tmp_path / "tickets"
    records = _runs("alpha", fails=3, error="schema mismatch: missing column",
                    http=200, last_success_hours=100, watermark="2026-09-30")
    plan = inspector.plan_tickets(inspector.group_runs(records), repo, DEFAULTS, NOW)
    inspector.write_plan(plan, repo, outdir, DEFAULTS, NOW)
    doc = load_ticket(sorted(outdir.glob("*.yaml"))[0])
    assert doc["category"] == "source-revision"
    assert doc["evidence"]["last_success"]["started_at"]
    assert doc["evidence"]["watermark"] == {
        "latest": "2026-09-30", "basis": "run telemetry watermark",
    }


def test_write_plan_watermark_unknown_is_honest(tmp_path):
    """遥测不提供数据期时不得编造：latest=None + basis 说明 unknown。"""
    repo = _mk_repo(tmp_path)
    outdir = tmp_path / "tickets"
    records = _runs("alpha", fails=3, error="connection timed out", last_success_hours=100)
    plan = inspector.plan_tickets(inspector.group_runs(records), repo, DEFAULTS, NOW)
    inspector.write_plan(plan, repo, outdir, DEFAULTS, NOW)
    doc = load_ticket(sorted(outdir.glob("*.yaml"))[0])
    assert doc["evidence"]["last_success"]["started_at"]
    assert doc["evidence"]["watermark"]["latest"] is None
    assert "unknown" in doc["evidence"]["watermark"]["basis"]


def test_write_plan_daily_cap(tmp_path):
    repo = _mk_repo(tmp_path)
    outdir = tmp_path / "tickets"
    cfg = replace(DEFAULTS, max_daily_tickets=2)
    records = (
        _runs("alpha", fails=3, error="connection timed out")
        + _runs("beta", fails=3, error="403 forbidden cloudflare", http=403)
        + _runs("gamma", fails=3, error="404 not found domain gone", last_success_hours=720)
    )
    plan = inspector.plan_tickets(inspector.group_runs(records), repo, cfg, NOW)
    stats = inspector.write_plan(plan, repo, outdir, cfg, NOW)
    assert len(stats["written"]) == 2
    assert any(d["reason"] == "daily cap reached" for d in stats["deferred"])


def test_write_plan_dedupes_nonterminal(tmp_path):
    repo = _mk_repo(tmp_path)
    outdir = tmp_path / "tickets"
    _mk_ticket(outdir, "alpha")
    records = _runs("alpha", fails=3, error="connection timed out")
    plan = inspector.plan_tickets(inspector.group_runs(records), repo, DEFAULTS, NOW)
    stats = inspector.write_plan(plan, repo, outdir, DEFAULTS, NOW)
    assert stats["written"] == []
    # 去重键已从「按源」改为「源 + 类别 + 证据签名」（harden-crawl-delivery 2.7）；
    # 老单没有签名时仍按源退回，因此 reason 是 duplicate of open ticket。
    assert any(d["source"] == "alpha" and "duplicate" in d["reason"] for d in stats["deferred"])


def test_write_plan_allows_new_failure_shape_for_same_source(tmp_path):
    """同源不同故障形状 = 两件事：修好网络层后暴露的结构层不能被旧单吞掉。"""
    repo = _mk_repo(tmp_path)
    outdir = tmp_path / "tickets"
    _mk_ticket(outdir, "alpha", category="network", evidence_signature="old-sig")
    records = _runs("alpha", fails=3, error="403 forbidden cloudflare", http=403)
    plan = inspector.plan_tickets(inspector.group_runs(records), repo, DEFAULTS, NOW)
    stats = inspector.write_plan(plan, repo, outdir, DEFAULTS, NOW)
    assert len(stats["written"]) == 1


def test_write_plan_suppresses_identical_signature(tmp_path):
    repo = _mk_repo(tmp_path)
    outdir = tmp_path / "tickets"
    records = _runs("alpha", fails=3, error="connection timed out")
    plan = inspector.plan_tickets(inspector.group_runs(records), repo, DEFAULTS, NOW)
    sig = identity.signature(plan.emit[0].runs[-1].error)
    _mk_ticket(outdir, "alpha", category=plan.emit[0].verdict.category,
               evidence_signature=sig)
    stats = inspector.write_plan(plan, repo, outdir, DEFAULTS, NOW)
    assert stats["written"] == []
    assert any("duplicate" in d["reason"] for d in stats["deferred"])


def test_nonterminal_sources_terminal_excluded(tmp_path):
    outdir = tmp_path / "tickets"
    _mk_ticket(outdir, "alpha")
    _mk_ticket(outdir, "beta", terminal="fixed-pending-human")
    found = inspector.nonterminal_sources(outdir)
    assert found == {"alpha"}


# ── SUBMIT / STATUS（注入式） ─────────────────────────────────────────────

def test_submit_respects_master_switch(tmp_path):
    outdir = tmp_path / "tickets"
    p = _mk_ticket(outdir, "alpha")
    calls = []
    cfg = replace(DEFAULTS, master_switch=False)
    res = inspector.submit_new_tickets(outdir, "Org/repo", "https://x", "k", cfg,
                                       poster=lambda *a: calls.append(a) or (200, "queued"))
    assert calls == []
    assert res[0]["reason"] == "master switch off (inspect-only)"
    events = [e["event"] for e in load_ticket(p)["lifecycle"]]
    assert "submit-skipped" in events


def test_submit_calls_once_with_idempotency(tmp_path):
    outdir = tmp_path / "tickets"
    p = _mk_ticket(outdir, "alpha")
    calls = []

    def poster(url, key, idem, body):
        calls.append((url, key, idem, body))
        return 200, '{"state":"queued"}'

    res1 = inspector.submit_new_tickets(outdir, "Org/repo", "https://x", "k", DEFAULTS, poster=poster)
    assert res1[0]["submitted"] is True
    assert calls[0][2] == f"{p.name[:-5]}-try1"               # 幂等键按次递增
    # 正文首行仍是原协议（兼容有仓的部署），其后附 SUBMIT-INLINE 全文——
    # 现役 agent 工作区不含该仓，只给路径它读不到（2026-11-10 实录，hollow-ok）。
    assert calls[0][3].startswith(f"SUBMIT Org/repo reports/health-tickets/{p.name}")
    assert "SUBMIT-INLINE" in calls[0][3] and "kind:" in calls[0][3]
    res2 = inspector.submit_new_tickets(outdir, "Org/repo", "https://x", "k", DEFAULTS, poster=poster)
    assert res2 == [] and len(calls) == 1                   # 已提交不重复


def test_submit_failure_recorded_not_raised(tmp_path):
    outdir = tmp_path / "tickets"
    p = _mk_ticket(outdir, "alpha")

    def poster(*a):
        raise OSError("boom")

    res = inspector.submit_new_tickets(outdir, "Org/repo", "https://x", "k", DEFAULTS, poster=poster)
    assert "submit failed" in res[0]["reason"]
    assert "submit-failed" in [e["event"] for e in load_ticket(p)["lifecycle"]]


def test_sync_status_mapping(tmp_path):
    outdir = tmp_path / "tickets"
    p_done = _mk_ticket(outdir, "alpha")
    p_work = _mk_ticket(outdir, "beta")

    def fetcher(url, key, idem, body):
        if "beta" in body:
            return 200, "agent is working on it"
        return 200, '{"state":"done","note":"pr merged","prUrl":"https://github.com/o/r/pull/9"}'

    inspector.sync_status(outdir, "https://x", "k", fetcher=fetcher)
    d_done = load_ticket(p_done)
    assert d_done["terminal"] == "fixed-pending-human"
    ev = [e for e in d_done["lifecycle"] if e["event"] == "status"][-1]
    assert ev["state"] == "done" and ev["pr_url"].endswith("/pull/9")
    d_work = load_ticket(p_work)
    assert d_work["terminal"] is None
    assert [e for e in d_work["lifecycle"] if e["event"] == "status"][-1]["state"] == "working"


def test_parse_status_fallback_text():
    parsed = inspector.parse_status("STATUS for ... state=manual: ticket unreadable")
    assert parsed["state"] == "manual"


def test_merged_maps_to_closed_terminal(tmp_path):
    from fd_industry_data.health.ticket import load_ticket, validate_ticket

    outdir = tmp_path / "tickets"
    p = _mk_ticket(outdir, "alpha")

    def fetcher(url, key, idem, body):
        return 200, "PR #1 已由人工 merge，state 更新为 merged；本单已闭环"

    inspector.sync_status(outdir, "https://x", "k", fetcher=fetcher)
    doc = load_ticket(p)
    assert doc["terminal"] == "closed"                      # merged → closed（人工闭环）
    errs = validate_ticket(doc)
    assert not any("terminal" in e for e in errs), errs     # closed 是合法终态
    ev = [e for e in doc["lifecycle"] if e["event"] == "status"][-1]
    assert ev["state"] == "merged"

    # closed 工单不再被同步/重投
    assert inspector.sync_status(outdir, "https://x", "k", fetcher=fetcher) == []


def test_submit_skips_terminal_and_quoted_error(tmp_path):
    # 已终态工单不重投
    outdir = tmp_path / "tickets"
    _mk_ticket(outdir, "alpha", terminal="fixed-pending-human")
    calls = []
    res = inspector.submit_new_tickets(outdir, "Org/repo", "https://x", "k", DEFAULTS,
                                       poster=lambda *a: calls.append(a) or (200, "queued"))
    assert res == [] and calls == []

    # 回执正文引述历史错误不判失败（仅开头/信封判失败）
    _mk_ticket(outdir, "beta")
    res2 = inspector.submit_new_tickets(
        outdir, "Org/repo", "https://x", "k", DEFAULTS,
        poster=lambda *a: (200, '{"result":{"parts":[{"text":"已入队并当轮处理完毕；说明：历史一次 submit 曾回 Invalid Request"}]}}'),
    )
    assert res2 and res2[0]["submitted"] is True


def test_extract_message_text_and_resubmit_after_error(tmp_path):
    # a2a JSON-RPC 回执 → 文本提取
    env = '{"jsonrpc":"2.0","id":1,"result":{"parts":[{"kind":"text","text":"**QUEUE — 2 单，均终态"}]}}'
    assert "QUEUE" in inspector.extract_message_text(env)
    assert "Invalid Request" in inspector.extract_message_text('{"error":{"message":"Invalid Request"}}')

    # 失败回执（Invalid Request）允许重试，成功后才记 submitted
    outdir = tmp_path / "tickets"
    p = _mk_ticket(outdir, "alpha")
    bodies = []

    def poster(url, key, idem, body):
        bodies.append(body)
        if len(bodies) == 1:
            return 200, '{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"Invalid Request"}}'
        return 200, '{"jsonrpc":"2.0","id":2,"result":{"parts":[{"kind":"text","text":"已入队"}]}}'

    res1 = inspector.submit_new_tickets(outdir, "Org/repo", "https://x", "k", DEFAULTS, poster=poster)
    assert res1[0]["submitted"] is False
    res2 = inspector.submit_new_tickets(outdir, "Org/repo", "https://x", "k", DEFAULTS, poster=poster)
    assert res2 and res2[0]["submitted"] is True       # 失败后重试成功
    assert res2[0]["attempt"] == 2                     # 幂等键按次递增（避门面重放错误首答）
    res3 = inspector.submit_new_tickets(outdir, "Org/repo", "https://x", "k", DEFAULTS, poster=poster)
    assert res3 == [] and len(bodies) == 2             # 成功后退化为不重发


# ── 拒收语义：落仓竞态烧单的根因（fix-health-ticket-landing-race） ────────

def test_refusal_reply_is_failure_and_retried(tmp_path):
    """agent 读不到工单时回「拒绝入队 —— 工单不可读」：必须判失败并允许重投。

    2026-10-06 / 10-11 实测：这类回执被判成功记 ok:true，工单被永久跳过。
    """
    outdir = tmp_path / "tickets"
    p = _mk_ticket(outdir, "inmetals")
    calls = []

    def poster(url, key, idem, body):
        calls.append(idem)
        return 200, ('{"jsonrpc":"2.0","id":1,"result":{"parts":[{"kind":"text","text":'
                     '"**结论：拒绝入队 —— 工单不可读，终态「转人工」（manual）**；'
                     '未动手、未开 PR。\\n\\n依据：contents 404、tree 1768 项无此路径"}]}}')

    res = inspector.submit_new_tickets(outdir, "Org/repo", "https://x", "k", DEFAULTS, poster=poster)
    assert res[0]["submitted"] is False, "拒收回执必须判为投递失败"
    doc = load_ticket(p)
    assert [e for e in doc["lifecycle"] if e["event"] == "submitted"][-1]["ok"] is False
    # 失败允许重投（否则这张单永远烧掉）
    assert inspector.submit_new_tickets(outdir, "Org/repo", "https://x", "k", DEFAULTS,
                                        poster=poster) != []


def test_dedup_reply_still_success(tmp_path):
    """去重回执（已见过/已入队）仍判成功——不误伤既有幂等语义。"""
    for reply in ("**已见过该单，状态维持 manual，未重复动手**",
                  "**结论：已入队（新单，队列第 1 位）→ 本回合终态 manual。**"):
        outdir = tmp_path / f"t{abs(hash(reply)) % 9999}"
        _mk_ticket(outdir, "alpha")
        res = inspector.submit_new_tickets(
            outdir, "Org/repo", "https://x", "k", DEFAULTS,
            poster=lambda *a, r=reply: (200, '{"result":{"parts":[{"text":"%s"}]}}' % r))
        assert res[0]["submitted"] is True, reply


def test_quoted_historical_error_not_failure(tmp_path):
    """结论句里引述历史错误不判失败（只看结论首段）。"""
    outdir = tmp_path / "tickets"
    _mk_ticket(outdir, "beta")
    reply = "已入队并当轮处理完毕；说明：历史一次 submit 曾回 Invalid Request"
    res = inspector.submit_new_tickets(
        outdir, "Org/repo", "https://x", "k", DEFAULTS,
        poster=lambda *a: (200, '{"result":{"parts":[{"text":"%s"}]}}' % reply))
    assert res[0]["submitted"] is True


def test_stale_ok_refusal_is_rejudged(tmp_path):
    """存量 ok:true 的拒收记录按现行语义重判为失败 → 重新投递。

    历史工单里有被误记为成功的拒收回执，若只信 ok 字段则永不重试。
    """
    outdir = tmp_path / "tickets"
    p = _mk_ticket(outdir, "metal-com")
    doc = load_ticket(p)
    doc["lifecycle"].append({"at": "2026-10-11T08:30:37+00:00", "event": "submitted",
                            "http_status": 200, "ok": True,
                            "response": "**结论：拒绝入队 → 终态 manual（转人工）**"})
    (outdir / p.name).write_text(dump_ticket(doc))
    calls = []
    res = inspector.submit_new_tickets(outdir, "Org/repo", "https://x", "k", DEFAULTS,
                                       poster=lambda *a: calls.append(a) or (200, "已入队"))
    assert res and calls, "存量误记的 ok:true 拒收必须重投"

# ── 两段式 CLI：落单与投递分离（fix-health-ticket-landing-race） ───────────

def test_cli_emit_then_deliver_separates_phases(tmp_path, monkeypatch):
    """--emit 不投递、--deliver 不落单；两者互斥。

    根因：同轮既写盘又投递 → agent 读到「文件不存在」拒收（10-11 实测烧单）。
    """
    import subprocess, sys, json as _json
    repo = _mk_repo(tmp_path)
    (repo / "scripts").mkdir(exist_ok=True)
    script = REPO / "scripts" / "health_inspect.py"
    snap = _snapshot(tmp_path, _runs("alpha", fails=3, error="connection timed out"))

    import os
    env = dict(os.environ, WANXING_SPIDER_HEAL_KEY="test-key",
               WANXING_SPIDER_HEAL_URL="http://127.0.0.1:1/none")

    def run(*flags):
        proc = subprocess.run(
            [sys.executable, str(script), "--snapshot", str(snap), "--repo", str(repo),
             "--outdir", str(tmp_path / "tk"), "--submit-new", *flags],
            capture_output=True, text=True, cwd=str(REPO), env=env)
        return proc, (_json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {})

    # 段一：落单（--submit-new 也被 do_deliver 拦住，不产生网络调用）
    p1, s1 = run("--emit")
    assert p1.returncode == 0, p1.stderr
    assert s1["written"], "段一必须落单"
    assert "submit" not in s1, "段一不得投递"

    # 段二：投递（不落单）
    p2, s2 = run("--deliver")
    assert p2.returncode == 0, p2.stderr
    assert s2["written"] == [], "段二不得再出单"
    assert "submit" in s2, "段二必须投递"

    # 互斥
    p3, _ = run("--emit", "--deliver")
    assert p3.returncode != 0


# ── STATUS 回读：思考片段不得冒充结论（萬星 runner 前置 <thinking>） ────────

def test_extract_skips_thinking_part():
    """回执 parts[0]=<thinking> 时，结论应取其后实质文本。

    2026-10-11 实测：STATUS 回执首段是思考块，只取 parts[0] 会解析到思考
    过程 → 141/151 次回读 state=None，工单永不终态、每轮重复投递。
    """
    env = {"jsonrpc": "2.0", "id": 1, "result": {"parts": [
        {"kind": "text", "text": "<thinking>Let me look at /data and read the skills.</thinking>"},
        {"kind": "text", "text": "**STATUS** 工单 X 状态：manual（终态，转人工）"},
    ]}}
    text = inspector.extract_message_text(json.dumps(env))
    assert "thinking" not in text, "思考片段不得被当作结论"
    assert inspector.parse_status(text)["state"] == "manual"


def test_extract_multiple_thinking_then_conclusion():
    env = {"result": {"parts": [
        {"kind": "text", "text": "<thinking>a</thinking>"},
        {"kind": "text", "text": "<thinking>b</thinking>"},
        {"kind": "text", "text": "已入队并当轮处理完毕"},
    ]}}
    assert inspector.extract_message_text(json.dumps(env)) == "已入队并当轮处理完毕"


def test_extract_single_part_unchanged():
    """旧形态（无 thinking）行为不变。"""
    env = {"result": {"parts": [{"kind": "text", "text": "已入队"}]}}
    assert inspector.extract_message_text(json.dumps(env)) == "已入队"


def test_extract_all_thinking_returns_text_not_envelope():
    """全是思考片段时回一段原文，不把整个 JSON 信封当结论。"""
    env = {"result": {"parts": [{"kind": "text", "text": "<thinking>only</thinking>"}]}}
    assert inspector.extract_message_text(json.dumps(env)) == "<thinking>only</thinking>"


def test_status_maps_terminal_after_thinking(tmp_path):
    """端到端：thinking 前置的 STATUS 回执仍能落终态（此前会 null，永不终态）。"""
    outdir = tmp_path / "tickets"
    p = _mk_ticket(outdir, "alpha")
    env = {"result": {"parts": [
        {"kind": "text", "text": "<thinking>checking inbox</thinking>"},
        {"kind": "text", "text": "状态：manual（终态，转人工），未开 PR"},
    ]}}

    def fetcher(url, key, idem, body):
        return 200, json.dumps(env)

    inspector.sync_status(outdir, "https://x", "k", fetcher=fetcher)
    doc = load_ticket(p)
    assert doc["terminal"] == "manual", "思考前置不得阻断终态落库"


def test_status_idem_key_unique_per_call(tmp_path):
    """STATUS 键每轮唯一：门面不会用旧答重放（固定键会把坏答/旧答重放满 24h）。"""
    outdir = tmp_path / "tickets"
    _mk_ticket(outdir, "alpha")
    seen = []

    def fetcher(url, key, idem, body):
        seen.append(idem)
        return 200, "状态：working"

    inspector.sync_status(outdir, "https://x", "k", fetcher=fetcher)
    inspector.sync_status(outdir, "https://x", "k", fetcher=fetcher)
    assert len(seen) == 2
    assert seen[0].startswith("status-") and seen[1].startswith("status-")
    assert seen[0].endswith("-r1") and seen[1].endswith("-r2"), seen


def test_status_idem_key_increments_with_lifecycle():
    """键随生命周期递增：只追加的 lifecycle 保证序号严格递增、永不碰撞。"""
    doc = {"lifecycle": [{"event": "created"}, {"event": "submitted"},
                         {"event": "status", "state": None},
                         {"event": "status-failed", "error": "timeout"}]}
    assert inspector._status_idem_key(doc, "t1") == "status-t1-r3"


def test_status_read_timeout_relaxed():
    """回读超时放宽到 300s（实测回合 20~150s+，曾见 180s 超时）。"""
    assert inspector.STATUS_READ_TIMEOUT == 300


def test_status_budget_cap_prefers_stalest(tmp_path):
    """单轮回读上限：超限时只读最久未读的 N 张，且不饿死（下一轮读其余）。"""
    outdir = tmp_path / "tickets"
    names = ["a", "b", "c"]
    paths = [_mk_ticket(outdir, n) for n in names]
    # 手工给 b 塞一条较新的 status 事件 → 它应排最后
    d = load_ticket(paths[1])
    inspector.ticket_mod.append_event(d, "status", http_status=200, state="working")
    paths[1].write_text(dump_ticket(d))

    seen = []

    def stem(body):
        # body = "STATUS reports/health-tickets/<stem>.yaml" → 取 source 短名
        name = body.rsplit("/", 1)[-1].removesuffix(".yaml")
        return name.rsplit("-", 1)[0].rsplit("-", 1)[-1]

    def fetcher(url, key, idem, body):
        seen.append(stem(body))
        return 200, "状态：working"

    inspector.sync_status(outdir, "https://x", "k", fetcher=fetcher, max_reads=2)
    assert len(seen) == 2, seen
    assert "b" not in seen, f"较新读过的不该优先：{seen}"

    # 第二轮：b 变成最久未读之一，不再饿死
    seen.clear()
    inspector.sync_status(outdir, "https://x", "k", fetcher=fetcher, max_reads=2)
    assert "b" in seen, seen


class TestUnregisteredSources:
    """遥测里有运行、但本仓没有 manifest 的源必须单列，不能混进 skipped_unknown_period。

    实证（2026-11-09）：`config/health-scope.json` 手工排除了 17 个跨仓特殊 Job，
    但新源冒出来时既没 manifest 也没进排除表，就被静默跳过。
    而且残余名单会漂——90 天口径是 flk-law-backfill/polygon，30 天口径变成
    example-stock/yfinance——说明"静默跳过"不是稳定状态，得让它在摘要里可见。
    """

    def test_source_without_manifest_is_reported_as_unregistered(self, tmp_path):
        from fd_industry_data.health import inspector, triage
        from fd_industry_data.health.config import HealthConfig
        repo = tmp_path
        (repo / "spiders").mkdir()
        runs = {"ghost-source": [triage.Run(started_at="2026-11-08T00:00:00+00:00",
                                           status="success", rows=10)]}
        plan = inspector.plan_tickets(runs, repo, HealthConfig(), NOW)
        assert plan.unregistered == ["ghost-source"]
        assert plan.skipped_unknown_period == []

    def test_excluded_source_is_not_reported_at_all(self, tmp_path):
        from fd_industry_data.health import inspector, triage
        from fd_industry_data.health.config import HealthConfig
        repo = tmp_path
        (repo / "spiders").mkdir()
        runs = {"law-job": [triage.Run(started_at="2026-11-08T00:00:00+00:00",
                                       status="success", rows=10)]}
        plan = inspector.plan_tickets(runs, repo, HealthConfig(excluded_sources=("law-job",)), NOW)
        assert plan.unregistered == []

    def test_manifest_without_period_stays_in_skipped_unknown_period(self, tmp_path):
        """有 manifest 但定不出周期 ≠ 没登记：前者是本仓的源，后者是没登记的新面孔。"""
        from fd_industry_data.health import inspector, triage
        from fd_industry_data.health.config import HealthConfig
        repo = tmp_path
        unit = repo / "spiders" / "known"
        unit.mkdir(parents=True)
        (unit / "manifest.yaml").write_text("name: known\n", encoding="utf-8")
        runs = {"known": [triage.Run(started_at="2026-11-08T00:00:00+00:00",
                                     status="success", rows=10)]}
        plan = inspector.plan_tickets(runs, repo, HealthConfig(), NOW)
        assert plan.unregistered == []
        assert plan.skipped_unknown_period == ["known"]


class TestTelemetryLineValidation:
    """psql 输出里混进非表格文本时，不能把它当成一条遥测。

    实证（2026-11-07 生产巡检 `last-run.json`）：`skipped_unknown_period` 第一项是
    `'at TCPConnectWrap.afterConnect [as oncomplete] (node:net:1638:16)'` ——
    一段 Node 调用栈被当成了源名。原实现把短行补成 7 列，于是任何文本都能变成记录。
    """

    def _run_with_stdout(self, monkeypatch, stdout):
        from fd_industry_data.health import inspector
        from fd_industry_data.health.config import HealthConfig
        text_holder = stdout

        class P:
            returncode = 0
            stderr = ""
            stdout = text_holder  # 真实 psql 结果在 .stdout，不是 .text

        monkeypatch.setattr(inspector.subprocess, "run", lambda *a, **k: P())
        return inspector.load_runs_from_psql(HealthConfig(), "postgres://x")

    def test_stack_trace_line_is_dropped_not_parsed_as_a_source(self, monkeypatch, capsys):
        records = self._run_with_stdout(
            monkeypatch,
            "at TCPConnectWrap.afterConnect [as oncomplete] (node:net:1638:16)\n")
        assert records == []
        assert "丢弃 1 行非遥测输出" in capsys.readouterr().err

    def test_legitimate_rows_survive_alongside_junk(self, monkeypatch):
        records = self._run_with_stdout(
            monkeypatch,
            "some driver warning\n"
            "bls\tsuccess\t2026-11-08T00:00:00+00:00\t12\t\t\n"
            "cisa\tfailed\t2026-11-08T01:00:00+00:00\t0\tboom\t\n")
        assert [r["source"] for r in records] == ["bls", "cisa"]
        assert [r["status"] for r in records] == ["success", "failed"]
        assert records[0]["rows"] == 12

    def test_running_row_is_accepted_not_discarded(self, monkeypatch):
        """`running` 是合法遥测（开了还没收尾），不能被当成"非遥测输出"丢掉。

        实证（2026-11-09 真实库）：`education_podcast` 有一条 running，开了约 10 小时。
        丢掉它等于对存量的真实记录撒谎；至于它不参与判词，由 triage 侧保证
        （`REACHED` 不含 running），两件事分开测。
        """
        records = self._run_with_stdout(
            monkeypatch,
            "education_podcast\trunning\t2026-11-09T12:47:26+00:00\t0\t\t\t\t\n")
        assert [r["status"] for r in records] == ["running"]

    def test_short_row_without_status_is_not_padded_into_a_record(self, monkeypatch):
        """补列本身合法（运维 SQL 可能少给 watermark 列），但补之前必须状态合法。"""
        records = self._run_with_stdout(
            monkeypatch,
            "bls\tsuccess\t2026-11-08T00:00:00+00:00\t12\t\n")   # 5 列 = 无 http_status
        assert len(records) == 1 and records[0]["source"] == "bls"


class TestDeferredCarriesVerdict:
    """顺延条目必须带判词类别：只记 {source, reason} 时看不出严重程度。

    实测（2026-11-09 预演）：12 个发现里 5 个出单、7 个被每日 5 单上限顺延，
    而顺延的 7 个类别全是既有规则——这恰恰是"上限有没有藏住新问题"的判据，
    丢掉类别就只能看到"顺延 7 个"这一句。
    """

    def test_daily_cap_deferral_names_the_category_and_rule(self, tmp_path):
        from fd_industry_data.health import inspector, triage
        from fd_industry_data.health.config import HealthConfig

        repo = tmp_path
        (repo / "spiders").mkdir()
        now = NOW
        runs = {}
        for name in ("aaa", "bbb"):
            unit = repo / "spiders" / name
            unit.mkdir()
            (unit / "manifest.yaml").write_text(f"name: {name}\ndata_period: day\n",
                                                encoding="utf-8")
            runs[name] = [triage.Run(started_at=(now - timedelta(hours=2)).isoformat(),
                                     status="success", rows=0)]
            # 声明下限，让 rule1c 必然出单
            (unit / "manifest.yaml").write_text(
                f"name: {name}\ndata_period: day\nmin_rows_per_run: 5\n", encoding="utf-8")

        cfg = HealthConfig(max_daily_tickets=1)
        plan = inspector.plan_tickets(runs, repo, cfg, now)
        stats = inspector.write_plan(plan, repo, tmp_path / "tickets", cfg, now)
        deferred = stats["deferred"]
        assert len(deferred) == 1, deferred
        assert deferred[0]["reason"] == "daily cap reached"
        assert deferred[0]["category"] == "structure"
        assert deferred[0]["rule"] == "rule1c"


class TestEvidenceOnlyCallsFailuresFailures:
    """工单证据里的 failing_runs 只能装真失败——partial/cancelled 不是失败。

    原来写的是 `status != "success"`，于是 `partial`（爬取成功、只是源自己的 accept
    条件没满足）和 `cancelled`（主动取消）都会出现在"失败运行"里。修复 agent 读到的
    就是一份把没失败的东西标成失败的证据。
    """

    def test_partial_and_cancelled_are_not_listed_as_failing(self):
        from fd_industry_data.health import inspector, triage
        runs = [
            triage.Run(started_at="2026-11-01T00:00:00+00:00", status="success", rows=10),
            triage.Run(started_at="2026-11-02T00:00:00+00:00", status="partial", rows=3),
            triage.Run(started_at="2026-11-03T00:00:00+00:00", status="cancelled", rows=0),
            triage.Run(started_at="2026-11-04T00:00:00+00:00", status="failed", rows=None,
                       error="timeout"),
        ]
        verdict = triage.Verdict("network", False, False, "查上游", "rule3:anti-bot")
        ev = inspector.build_evidence(runs, verdict)
        assert [f["status"] for f in ev["failing_runs"]] == ["failed"]

    def test_last_success_accepts_partial_and_reports_its_rows(self):
        from fd_industry_data.health import inspector, triage
        runs = [
            triage.Run(started_at="2026-11-01T00:00:00+00:00", status="success", rows=10),
            triage.Run(started_at="2026-11-02T00:00:00+00:00", status="partial", rows=7),
        ]
        verdict = triage.Verdict("structure", False, False, "核对", "rule1c:x")
        ev = inspector.build_evidence(runs, verdict)
        assert ev["last_success"] == {"started_at": "2026-11-02T00:00:00+00:00", "rows": 7}


class TestErrorHeadMustBeOneLine:
    """`error_head` 带换行会把一条遥测撑成多条 TSV 物理行。

    实证（2026-11-09，真实库）：`legal-sources-report` 的 error_head 是
    `exit=1 | [...] FATAL: Error: connect ECONNREFUSED 127.0.0.1:5432\\n    at
    TCPConnectWrap.afterConnect ...` —— psql 按物理行切，于是第二行被旧解析器
    补成一条假记录，源名取到那段调用栈。全库共 11 条这样的记录。
    """

    def test_newlines_are_folded_into_spaces_not_truncated(self):
        from fd_industry_data.writer import sanitize_error_head, ERROR_HEAD_MAX
        raw = ("exit=1 | FATAL: Error: connect ECONNREFUSED 127.0.0.1:5432\n"
               "    at TCPConnectWrap.afterConnect [as oncomplete] (node:net:1638:16)")
        out = sanitize_error_head(raw)
        assert "\n" not in out and "\t" not in out and "\r" not in out
        assert "ECONNREFUSED" in out, "第一行是现象，不能丢"
        assert "TCPConnectWrap" in out, "第二行才是真因，折叠不能截掉它"
        assert out == " ".join(raw.split())
        assert len(sanitize_error_head("x" * 900)) == ERROR_HEAD_MAX

    def test_none_and_blank_stay_none(self):
        from fd_industry_data.writer import sanitize_error_head
        assert sanitize_error_head(None) is None
        assert sanitize_error_head("   \n\t ") is None


class TestBlockedConfigSources:
    """缺必需环境变量（runner 写 status=blocked-config）的源要单列。

    不能落进 rule2「证据不足 / 需人工」——缺配置不是代码问题，runner 侧
    （`requires_env` 预检，2026-11-10）的设计明写着「不催修复工单」；
    也不能算健康——它确实没产出。所以单列：可见、不判定。
    """

    # 本类三个用例的遥测时间戳是写死的 2026-11-08/11-10，参考时刻必须一起钉住。
    # 用模块级 NOW（= datetime.now()）会让它们**随日历腐烂**：2026-11-14 实测
    # data_period=day 的新鲜度窗 = max(3×24h, 48h) = 86.4h，而 last_success 距今
    # 已 96.6h ⇒ "恢复后不再单列"那条其实撞上了 rule1b 源静默，healthy 变 0，
    # 断言红在一条与它要验的东西无关的规则上。
    NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)

    def _repo_with(self, tmp_path, name, body="data_period: day\n"):
        unit = tmp_path / "spiders" / name
        unit.mkdir(parents=True)
        (unit / "manifest.yaml").write_text(f"name: {name}\n{body}", encoding="utf-8")
        return tmp_path

    def test_latest_blocked_config_is_listed_not_ticketed(self, tmp_path):
        from fd_industry_data.health import inspector, triage
        from fd_industry_data.health.config import HealthConfig
        repo = self._repo_with(tmp_path, "needs-env")
        runs = {"needs-env": [triage.Run(
            started_at="2026-11-10T00:00:00+00:00", status="blocked-config", rows=0)]}
        plan = inspector.plan_tickets(runs, repo, HealthConfig(), self.NOW)
        assert plan.blocked_config == ["needs-env"]
        assert plan.emit == [], "缺配置不该出单（那是修复 agent 会被误导的归因）"
        assert plan.healthy == 0, "也不该计健康——确实没产出"

    def test_earlier_success_then_blocked_config_still_listed(self, tmp_path):
        """blocked-config 是最近一次 → 单列；不按"上次成功有点旧"去判静默。"""
        from fd_industry_data.health import inspector, triage
        from fd_industry_data.health.config import HealthConfig
        repo = self._repo_with(tmp_path, "needs-env")
        runs = {"needs-env": [
            triage.Run(started_at="2026-11-08T00:00:00+00:00", status="success", rows=100),
            triage.Run(started_at="2026-11-10T00:00:00+00:00", status="blocked-config", rows=0),
        ]}
        plan = inspector.plan_tickets(runs, repo, HealthConfig(), self.NOW)
        assert plan.blocked_config == ["needs-env"]
        assert plan.emit == []

    def test_recovery_clears_the_listing(self, tmp_path):
        """配好环境变量、恢复正常运行后，就不再单列。"""
        from fd_industry_data.health import inspector, triage
        from fd_industry_data.health.config import HealthConfig
        repo = self._repo_with(tmp_path, "needs-env")
        runs = {"needs-env": [
            triage.Run(started_at="2026-11-10T00:00:00+00:00", status="blocked-config", rows=0),
            triage.Run(started_at="2026-11-10T02:00:00+00:00", status="success", rows=100),
        ]}
        plan = inspector.plan_tickets(runs, repo, HealthConfig(), self.NOW)
        assert plan.blocked_config == []
        assert plan.healthy == 1
