"""巡检器：遥测→分诊→出单→（注入式）SUBMIT/STATUS 接线。全离线。"""
from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.health import inspector  # noqa: E402
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


def _runs(source, *, fails=0, error="", http=None, last_success_hours=1.0):
    recs = []
    if last_success_hours is not None:
        recs.append({"source": source, "status": "success",
                     "started_at": (NOW - timedelta(hours=last_success_hours)).isoformat(),
                     "rows": 10, "error": "", "http_status": 200})
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


def _mk_ticket(outdir, source, terminal=None):
    doc = new_ticket(source=source, unit=f"spiders/{source}/", evidence={}, verify_commands=["echo"])
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
    assert any(d["source"] == "alpha" and "nonterminal" in d["reason"] for d in stats["deferred"])


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
    assert calls[0][3] == f"SUBMIT Org/repo reports/health-tickets/{p.name}"
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