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
