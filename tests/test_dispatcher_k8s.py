"""Dispatcher k8s execution branch (legal-line-federation 3.1/3.2): claim
routing between the federated in-cluster Job form and the platform
subprocess form, the cluster-side single-flight refusal, cancel handling,
and terminal-state write-back. The k8s API is faked at the module-function
seam (list/create/get/delete_job); the DB is a minimal record/fetch stand-in
in the same style as test_dispatch_site.

The identity segment (onboard-rmfyalk-platform-session 1.1/1.2) is faked at
the auth-module seam; every execute_k8s run now reads crawl_sources
.auth_profile first, so fetch queues lead with None for unprofiled sources.
"""
from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import fd_industry_data.dispatcher_cli as dcli
from fd_industry_data.dispatcher_cli import (
    active_job_for,
    execute_k8s,
    job_body,
    job_state,
    source_runner,
    uses_k8s_branch,
)

DECL = {"kind": "federated", "runner_image": "100.64.0.8:30880/finddata/law-runner:sha-abc123",
        "runner_command": ["node", "bin/flk-law-crawl.mjs", "--rate-ms", "1100"],
        "timeout_seconds": 1800}

ROW = {"id": 42, "source": "flk-law-crawl"}

JOB_ACTIVE = {"metadata": {"name": "flk-law-crawl-42"}, "status": {}}
JOB_DONE = {"metadata": {"name": "flk-law-crawl-42"},
            "status": {"conditions": [{"type": "Complete"}]}}
JOB_FAILED = {"metadata": {"name": "flk-law-crawl-42"},
              "status": {"conditions": [{"type": "Failed", "reason": "DeadlineExceeded",
                                         "message": "Job was active longer than deadline"}]}}


class FakeConn:
    """psycopg2 stand-in: records executes, answers fetchone from a queue."""

    def __init__(self, fetches=None, error_on=None):
        self.executed = []  # (single-spaced sql, params)
        self.fetches = list(fetches or [])
        self.error_on = error_on  # raise when the sql contains this fragment
        self.rolled_back = False

    def cursor(self):
        return _FakeCursor(self)

    def rollback(self):
        self.rolled_back = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        if self._conn.error_on and self._conn.error_on in flat:
            raise RuntimeError("column does not exist")
        self._conn.executed.append((flat, params))

    def fetchone(self):
        return self._conn.fetches.pop(0) if self._conn.fetches else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def finished(monkeypatch):
    calls = []
    monkeypatch.setattr(dcli.dispatch, "finish_pending",
                        lambda conn, pid, **kw: calls.append((pid, kw)))
    return calls


@pytest.fixture()
def no_sleep(monkeypatch):
    monkeypatch.setattr(dcli.time, "sleep", lambda s: None)


# --- claim routing: federated vs platform ---------------------------------

def test_uses_k8s_branch_routing():
    assert uses_k8s_branch(DECL)
    assert not uses_k8s_branch(None)                          # unregistered -> subprocess
    assert not uses_k8s_branch({**DECL, "kind": "platform"})  # platform-native
    assert not uses_k8s_branch({**DECL, "runner_image": None})  # no declaration yet
    assert not uses_k8s_branch({**DECL, "runner_command": None})


def test_source_runner_reads_declaration():
    conn = FakeConn(fetches=[("federated", "img:1", ["node", "bin/x.mjs"], 900,
                              ["law-runner-secrets"])])
    assert source_runner(conn, "flk-law-crawl") == {
        "kind": "federated", "runner_image": "img:1",
        "runner_command": ["node", "bin/x.mjs"], "timeout_seconds": 900,
        "runner_env_from": ["law-runner-secrets"]}


def test_source_runner_degrades_to_platform_before_migration():
    conn = FakeConn(error_on="runner_image")
    assert source_runner(conn, "flk-law-crawl") is None
    assert conn.rolled_back


# --- Job body: declaration-driven, hard-gated, mirror-exclusive -----------

def test_job_body_follows_declaration(monkeypatch):
    monkeypatch.setenv("FD_CRAWL_DB_URL", "postgresql://db")
    body = job_body("flk-law-crawl", 42, DECL, "scraw")
    assert body["metadata"]["name"] == "flk-law-crawl-42"
    assert body["metadata"]["namespace"] == "scraw"
    assert body["spec"]["activeDeadlineSeconds"] == 1800
    assert body["spec"]["backoffLimit"] == 0
    container = body["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == DECL["runner_image"]
    assert container["command"] == DECL["runner_command"]
    env = {e["name"]: e["value"] for e in container["env"]}
    assert env["FD_PENDING_RUN_ID"] == "42" and env["FD_CRAWL_DB_URL"] == "postgresql://db"
    # registration hard gate: limits always present (exemption precondition)
    assert container["resources"]["limits"] == {"memory": "2Gi", "cpu": "1"}
    assert container["resources"]["requests"] == {"memory": "256Mi", "cpu": "100m"}
    labels = body["metadata"]["labels"]
    assert labels["fd-industry/managed-by"] == "fd-dispatcher"
    assert labels["fd-industry/source"] == "flk-law-crawl"
    assert body["spec"]["template"]["metadata"]["labels"] == labels


def test_job_body_defaults_when_decl_minimal(monkeypatch):
    monkeypatch.delenv("FD_CRAWL_DB_URL", raising=False)
    body = job_body("mfa-treaty-crawl", 7,
                    {"kind": "federated", "runner_image": "img:2",
                     "runner_command": ["node", "bin/mfa-treaty-crawl.mjs"],
                     "timeout_seconds": None}, "scraw")
    assert body["spec"]["activeDeadlineSeconds"] == 3600
    container = body["spec"]["template"]["spec"]["containers"][0]
    assert {e["name"] for e in container["env"]} == {"PYTHONUNBUFFERED", "FD_PENDING_RUN_ID", "FD_SCHEMA_MANAGED"}


# --- runner_env_from: per-secret envFrom on the declared Job (drill 4.2) ---

def test_job_body_env_from_per_secret():
    decl = {**DECL, "runner_env_from": ["fd-industry-rustfs", "law-auth-secrets"]}
    body = job_body("flk-law-crawl", 42, decl, "scraw")
    container = body["spec"]["template"]["spec"]["containers"][0]
    assert container["envFrom"] == [{"secretRef": {"name": "fd-industry-rustfs"}},
                                    {"secretRef": {"name": "law-auth-secrets"}}]
    # env stays independent of envFrom
    assert {e["name"] for e in container["env"]} >= {"FD_PENDING_RUN_ID"}


def test_job_body_null_env_from_omits_key(monkeypatch):
    monkeypatch.setenv("FD_CRAWL_DB_URL", "postgresql://db")
    for decl in (DECL, {**DECL, "runner_env_from": None},
                 {**DECL, "runner_env_from": []}):
        body = job_body("flk-law-crawl", 42, decl, "scraw")
        container = body["spec"]["template"]["spec"]["containers"][0]
        assert "envFrom" not in container  # NULL/missing/empty: no crash, no key


def test_job_body_env_from_drops_junk_entries():
    decl = {**DECL, "runner_env_from": ["s1", 42, "", None, "  ", "s2"]}
    body = job_body("flk-law-crawl", 42, decl, "scraw")
    container = body["spec"]["template"]["spec"]["containers"][0]
    assert container["envFrom"] == [{"secretRef": {"name": "s1"}},
                                    {"secretRef": {"name": "s2"}}]


# --- single-flight refusal (third-layer counterpart on the cluster) -------

def test_active_job_blocks_creation(monkeypatch, finished, no_sleep):
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [JOB_ACTIVE])
    posted = []
    monkeypatch.setattr(dcli, "create_job", lambda ns, body: posted.append(body))
    execute_k8s(FakeConn(), ROW, "flk-law-crawl", DECL, "scraw")
    assert posted == []  # never created
    pid, kw = finished[0]
    assert pid == 42 and kw["status"] == "failed"
    assert "single-flight" in kw["error_head"] and kw["run_id"] is None


def test_active_job_for_matches_owner_and_prefix():
    owned = {"metadata": {"name": "flk-law-crawl-99",
                          "ownerReferences": [{"kind": "CronJob", "name": "flk-law-crawl"}]},
             "status": {}}
    assert active_job_for([owned], "flk-law-crawl") == owned
    assert active_job_for([JOB_DONE], "flk-law-crawl") is None      # finished: not active
    other = {"metadata": {"name": "mfa-treaty-crawl-5"}, "status": {}}
    assert active_job_for([other], "flk-law-crawl") is None


# --- full lifecycle: create -> poll -> terminal write-back ----------------

def test_success_with_direct_report(monkeypatch, finished, no_sleep):
    conn = FakeConn([None,                    # auth_profile: none
                     (None, None, False),     # poll 1: no linked run yet
                     (None, None, False),     # poll 2: still none
                     (12, "success", False)])  # terminal: linked report success
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [])
    created = {}
    monkeypatch.setattr(dcli, "create_job",
                        lambda ns, body: created.update(body=body) or body)
    views = iter([JOB_ACTIVE, JOB_DONE])
    monkeypatch.setattr(dcli, "get_job", lambda ns, name: next(views))
    execute_k8s(conn, ROW, "flk-law-crawl", DECL, "scraw")
    assert created["body"]["metadata"]["name"] == "flk-law-crawl-42"
    assert finished == [(42, {"run_id": 12, "status": "done", "error_head": None})]


def test_success_without_linked_report_is_done(monkeypatch, finished, no_sleep):
    conn = FakeConn([None, (None, None, False), (None, None, False), (None, None, False)])
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [])
    monkeypatch.setattr(dcli, "create_job", lambda ns, body: body)
    monkeypatch.setattr(dcli, "get_job", lambda ns, name: JOB_DONE)  # terminal at once
    execute_k8s(conn, ROW, "flk-law-crawl", DECL, "scraw")
    assert finished == [(42, {"run_id": None, "status": "done", "error_head": None})]


def test_failed_job_reports_condition_head(monkeypatch, finished, no_sleep):
    conn = FakeConn([None, (None, None, False), (None, None, False), (None, None, False)])
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [])
    monkeypatch.setattr(dcli, "create_job", lambda ns, body: body)
    monkeypatch.setattr(dcli, "get_job", lambda ns, name: JOB_FAILED)
    execute_k8s(conn, ROW, "flk-law-crawl", DECL, "scraw")
    pid, kw = finished[0]
    assert pid == 42 and kw["status"] == "failed" and kw["run_id"] is None
    assert "DeadlineExceeded" in kw["error_head"]


def test_conflict_409_adopts_existing_job(monkeypatch, finished, no_sleep):
    conn = FakeConn([None,                    # auth_profile: none
                     (None, None, False),     # poll: no linked run yet
                     (9, "success", False)])  # terminal: linked report success
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [])
    err = urllib.error.HTTPError("https://k8s", 409, "Conflict", {}, None)

    def fake_create(ns, body):
        raise err

    monkeypatch.setattr(dcli, "create_job", fake_create)
    monkeypatch.setattr(dcli, "get_job", lambda ns, name: JOB_DONE)
    execute_k8s(conn, ROW, "flk-law-crawl", DECL, "scraw")
    assert finished == [(42, {"run_id": 9, "status": "done", "error_head": None})]


def test_non_conflict_http_error_propagates(monkeypatch, finished, no_sleep):
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [])
    err = urllib.error.HTTPError("https://k8s", 403, "Forbidden", {}, None)
    monkeypatch.setattr(dcli, "create_job", lambda ns, body: (_ for _ in ()).throw(err))
    with pytest.raises(urllib.error.HTTPError):
        execute_k8s(FakeConn(), ROW, "flk-law-crawl", DECL, "scraw")


# --- cancel: cancel_requested -> delete Job -> cancel outcome -------------

def test_cancel_deletes_job_and_writes_outcome(monkeypatch, finished, no_sleep):
    conn = FakeConn([None,                    # auth_profile: none
                     (15, "running", True)])  # linked run carries the cancel flag
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [])
    monkeypatch.setattr(dcli, "create_job", lambda ns, body: body)
    deleted = []
    monkeypatch.setattr(dcli, "delete_job", lambda ns, name: deleted.append(name))
    execute_k8s(conn, ROW, "flk-law-crawl", DECL, "scraw")
    assert deleted == ["flk-law-crawl-42"]
    assert finished == [(42, {"run_id": 15, "status": "cancelled", "error_head": None})]


def test_delete_job_tolerates_404(monkeypatch):
    seen = []
    err = urllib.error.HTTPError("https://k8s", 404, "Not Found", {}, None)
    monkeypatch.setattr(dcli, "_k8s_api",
                        lambda m, p, body=None: seen.append(m) or (_ for _ in ()).throw(err))
    dcli.delete_job("scraw", "flk-law-crawl-42")  # must not raise
    assert seen == ["DELETE"]


def test_delete_job_cascades_so_the_crawler_pod_dies(monkeypatch):
    """取消一次爬取要真的杀掉 pod：裸 DELETE 默认 Orphan，pod 会继续跑。

    判据落在**请求本身**（同 `fd-open-data-mcp` 那一笔的两条）：集群侧
    「Job 已经没了」在改动前后一模一样，只看结果正是这个缺陷的藏身处。
    """
    seen = []
    monkeypatch.setattr(dcli, "_k8s_api",
                        lambda m, p, body=None: seen.append((m, p, body)) or {})
    dcli.delete_job("scraw", "flk-law-crawl-42")
    method, path, body = seen[-1]
    assert method == "DELETE" and path.endswith("/jobs/flk-law-crawl-42")
    assert body is not None, (
        "DELETE 没带 DeleteOptions —— API 默认 Orphan，爬虫 pod 会一直跑下去")
    assert body.get("propagationPolicy") == "Background", (
        f"传播策略是 {body.get('propagationPolicy')!r}，必须是 Background："
        "Foreground 会把 dispatcher 的轮询卡在优雅停机后面")


def test_job_state_transitions():
    assert job_state(JOB_ACTIVE) == ("active", None)
    assert job_state(JOB_DONE) == ("success", None)
    state, head = job_state(JOB_FAILED)
    assert state == "failed" and "DeadlineExceeded" in head

def test_cancel_writes_crawl_runs_terminal(monkeypatch):
    """取消时 dispatcher 必须回写 crawl_runs 终态（被杀 runner 写不了）。"""
    import inspect
    src = inspect.getsource(__import__("fd_industry_data.dispatcher_cli", fromlist=["x"]).execute_k8s)
    assert "status='cancelled'" in src and "crawl_runs" in src


# --- identity segment (onboard-rmfyalk-platform-session 1.1/1.2/1.3) -------
# A profiled source leases a pool identity before Job creation, inlines its
# jar + egress into the Job env, and settles the lease by run outcome. All
# auth-module functions are faked at the dcli._auth seam.

IDENT = {"id": 5, "account_alias": "acct001",
         "session_ref": "platform-sessions/rmfyalk-case-crawl/acct001/x.jar",
         "lease_token": "tok-5"}
JAR = {"cookies": [{"name": "sid", "value": "s3cr3t"}],
       "auth": {"headerName": "token", "token": "T"},
       "user_agent": "UA"}


@pytest.fixture()
def authpool(monkeypatch):
    """Fake identity-pool seams; records every call for assertions."""
    state = {"leased": [], "released": [], "outcomes": [], "auth_failed": [],
             "ident": dict(IDENT), "jar": dict(JAR),
             "egress_ref": "proxy:7",
             "egress": {"proxy_url": "http://u:p@10.0.0.7:8080", "proxy_id": 7},
             "jar_error": None, "release_error": None}

    def lease(conn, src, owner, ttl_seconds=3600):
        state["leased"].append({"src": src, "owner": owner, "ttl": ttl_seconds})
        return dict(state["ident"]) if state["ident"] else None

    def fetch_jar(session_ref):
        if state["jar_error"] is not None:
            raise state["jar_error"]
        return state["jar"]

    def resolve_egress(conn, egress_ref):
        # mirrors the pool contract: only a truthy 'proxy:<id>' ref resolves
        return (state["egress"] if egress_ref
                and egress_ref == state["egress_ref"] else None)

    def release(conn, identity_id, token, *, success=None):
        state["released"].append({"id": identity_id, "token": token,
                                  "success": success})
        if state["release_error"] is not None:
            raise state["release_error"]

    monkeypatch.setattr(dcli._auth, "lease_identity", lease)
    monkeypatch.setattr(dcli._auth, "fetch_jar", fetch_jar)
    monkeypatch.setattr(dcli._auth, "resolve_egress", resolve_egress)
    monkeypatch.setattr(dcli._auth, "release_identity", release)
    monkeypatch.setattr(dcli._auth, "record_run_outcome",
                        lambda conn, ident_id, rows: state["outcomes"].append((ident_id, rows)))
    monkeypatch.setattr(dcli._auth, "report_auth_failed",
                        lambda conn, src, alias, detail:
                        state["auth_failed"].append((src, alias, detail)))
    return state


def _job_env(created: dict) -> dict:
    container = created["body"]["spec"]["template"]["spec"]["containers"][0]
    return {e["name"]: e["value"] for e in container["env"]}


def test_identity_ttl_covers_declared_timeout():
    assert dcli._identity_ttl({"timeout_seconds": 21600}) == 22200  # 6h + slack
    assert dcli._identity_ttl({"timeout_seconds": 1800}) == 3600    # floor
    assert dcli._identity_ttl({"timeout_seconds": None}) == 4200    # job default 3600


def test_job_body_identity_env_param():
    env = [{"name": "FD_ACCOUNT", "value": "a1"},
           {"name": "FD_SESSION_JAR", "value": "{}"}]
    body = job_body("flk-law-crawl", 42, DECL, "scraw", identity_env=env)
    container = body["spec"]["template"]["spec"]["containers"][0]
    names = {e["name"] for e in container["env"]}
    assert {"FD_ACCOUNT", "FD_SESSION_JAR", "FD_PENDING_RUN_ID"} <= names
    # default stays identity-free (platform-native / unprofiled jobs)
    plain = job_body("flk-law-crawl", 42, DECL, "scraw")
    assert "FD_ACCOUNT" not in {e["name"] for e in
                                plain["spec"]["template"]["spec"]["containers"][0]["env"]}


def test_identity_injected_into_job_env(monkeypatch, finished, no_sleep, authpool):
    conn = FakeConn([("rmfyalk-case-crawl",),    # auth_profile
                     ("proxy:7",),               # identity egress_ref
                     (12, "success", False),     # poll: linked report success
                     (12, "success"),            # _lookup_run at terminal
                     (17, None)])                # crawl_runs rows/error for settle
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [])
    created = {}
    monkeypatch.setattr(dcli, "create_job",
                        lambda ns, body: created.update(body=body) or body)
    monkeypatch.setattr(dcli, "get_job", lambda ns, name: JOB_DONE)
    execute_k8s(conn, ROW, "flk-law-crawl", {**DECL, "timeout_seconds": 21600},
                "scraw", owner="tester")
    env = _job_env(created)
    assert env["FD_ACCOUNT"] == "acct001"
    assert json.loads(env["FD_SESSION_JAR"]) == JAR
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        assert env[k] == "http://u:p@10.0.0.7:8080"
    assert env["FD_EGRESS_REF"] == "proxy:7"
    # lease TTL covers the declared job deadline
    assert authpool["leased"] == [{"src": "rmfyalk-case-crawl", "owner": "tester",
                                   "ttl": 22200}]
    # terminal settle: success release + run outcome from the direct report
    assert authpool["released"] == [{"id": 5, "token": "tok-5", "success": True}]
    assert authpool["outcomes"] == [(5, 17)]
    assert finished == [(42, {"run_id": 12, "status": "done", "error_head": None})]


def test_identity_without_egress_injects_no_proxy(monkeypatch, finished, no_sleep,
                                                  authpool):
    # bound ref exists but does not resolve (retired/unknown proxy): no proxy env
    authpool["egress"] = None
    conn = FakeConn([("rmfyalk-case-crawl",), ("proxy:7",),
                     (12, "success", False), (12, "success"), (3, None)])
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [])
    created = {}
    monkeypatch.setattr(dcli, "create_job",
                        lambda ns, body: created.update(body=body) or body)
    monkeypatch.setattr(dcli, "get_job", lambda ns, name: JOB_DONE)
    execute_k8s(conn, ROW, "flk-law-crawl", DECL, "scraw")
    env = _job_env(created)
    assert env["FD_ACCOUNT"] == "acct001" and json.loads(env["FD_SESSION_JAR"]) == JAR
    assert "HTTPS_PROXY" not in env and "FD_EGRESS_REF" not in env
    assert authpool["outcomes"] == [(5, 3)]


def test_pool_dry_fails_pending_without_job(monkeypatch, finished, authpool):
    authpool["ident"] = None
    conn = FakeConn([("rmfyalk-case-crawl",)])
    posted = []
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [])
    monkeypatch.setattr(dcli, "create_job", lambda ns, body: posted.append(body))
    execute_k8s(conn, ROW, "flk-law-crawl", DECL, "scraw")
    assert posted == []  # no unauthenticated execution
    assert finished == [(42, {"run_id": None, "status": "failed",
                              "error_head": "auth pool dry: no active unleased identity"})]
    assert authpool["released"] == []  # nothing was leased


def test_jar_failure_releases_lease_and_fails_without_job(monkeypatch, finished,
                                                          authpool):
    authpool["jar_error"] = RuntimeError("RUSTFS_ENDPOINT not set")
    conn = FakeConn([("rmfyalk-case-crawl",)])
    posted = []
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [])
    monkeypatch.setattr(dcli, "create_job", lambda ns, body: posted.append(body))
    execute_k8s(conn, ROW, "flk-law-crawl", DECL, "scraw")
    assert posted == []
    pid, kw = finished[0]
    assert pid == 42 and kw["status"] == "failed" and kw["run_id"] is None
    assert kw["error_head"].startswith("session jar unavailable: RUSTFS_ENDPOINT")
    assert authpool["released"] == [{"id": 5, "token": "tok-5", "success": False}]
    assert authpool["outcomes"] == []


def test_failed_job_with_auth_error_reports_auth_failed(monkeypatch, finished,
                                                        no_sleep, authpool):
    conn = FakeConn([("rmfyalk-case-crawl",), ("proxy:7",),
                     (12, "failed", False),          # poll: linked report failed
                     (12, "failed"),                 # _lookup_run at terminal
                     (0, "HTTP 401 未登录")])         # runner report error head
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [])
    monkeypatch.setattr(dcli, "create_job", lambda ns, body: body)
    monkeypatch.setattr(dcli, "get_job", lambda ns, name: JOB_FAILED)
    execute_k8s(conn, ROW, "flk-law-crawl", DECL, "scraw")
    assert finished[0][1]["status"] == "failed"
    assert authpool["released"] == [{"id": 5, "token": "tok-5", "success": False}]
    assert authpool["outcomes"] == []
    src, alias, detail = authpool["auth_failed"][0]
    assert (src, alias) == ("rmfyalk-case-crawl", "acct001")
    assert "401" in detail and "dispatcher k8s heuristic" in detail


def test_failed_job_without_auth_error_skips_feedback(monkeypatch, finished,
                                                      no_sleep, authpool):
    conn = FakeConn([("rmfyalk-case-crawl",), ("proxy:7",),
                     (12, "failed", False), (12, "failed"),
                     (0, "connection timeout")])
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [])
    monkeypatch.setattr(dcli, "create_job", lambda ns, body: body)
    monkeypatch.setattr(dcli, "get_job", lambda ns, name: JOB_FAILED)
    execute_k8s(conn, ROW, "flk-law-crawl", DECL, "scraw")
    assert authpool["released"] == [{"id": 5, "token": "tok-5", "success": False}]
    assert authpool["auth_failed"] == []  # no vocabulary hit: no dual-path event
    assert authpool["outcomes"] == []


def test_cancel_releases_identity_without_feedback(monkeypatch, finished,
                                                   no_sleep, authpool):
    conn = FakeConn([("rmfyalk-case-crawl",), ("proxy:7",),
                     (15, "running", True)])  # linked run carries the cancel flag
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [])
    monkeypatch.setattr(dcli, "create_job", lambda ns, body: body)
    deleted = []
    monkeypatch.setattr(dcli, "delete_job", lambda ns, name: deleted.append(name))
    execute_k8s(conn, ROW, "flk-law-crawl", DECL, "scraw")
    assert deleted == ["flk-law-crawl-42"]
    assert finished == [(42, {"run_id": 15, "status": "cancelled", "error_head": None})]
    assert authpool["released"] == [{"id": 5, "token": "tok-5", "success": False}]
    assert authpool["outcomes"] == [] and authpool["auth_failed"] == []


def test_lease_released_when_job_creation_fails(monkeypatch, finished, authpool):
    conn = FakeConn([("rmfyalk-case-crawl",), ("proxy:7",)])
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [])
    err = urllib.error.HTTPError("https://k8s", 403, "Forbidden", {}, None)
    monkeypatch.setattr(dcli, "create_job",
                        lambda ns, body: (_ for _ in ()).throw(err))
    with pytest.raises(urllib.error.HTTPError):
        execute_k8s(conn, ROW, "flk-law-crawl", DECL, "scraw")
    # the caller writes the failed pending row; the lease must not leak to TTL
    assert authpool["released"] == [{"id": 5, "token": "tok-5", "success": False}]


def test_unprofiled_source_never_touches_pool(monkeypatch, finished, no_sleep,
                                              authpool):
    conn = FakeConn([None,                    # auth_profile: none
                     (12, "success", False),  # linked report success
                     (12, "success")])
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [])
    monkeypatch.setattr(dcli, "create_job", lambda ns, body: body)
    monkeypatch.setattr(dcli, "get_job", lambda ns, name: JOB_DONE)
    execute_k8s(conn, ROW, "flk-law-crawl", DECL, "scraw")
    assert finished == [(42, {"run_id": 12, "status": "done", "error_head": None})]
    assert authpool["leased"] == [] and authpool["released"] == []
    assert authpool["outcomes"] == [] and authpool["auth_failed"] == []


def test_settle_failure_keeps_terminal_pending_and_lease_tolerant(
        monkeypatch, finished, no_sleep, authpool):
    """释放/留痕失败（租约已过期回收等）不得改写已知终态、不得抛出。"""
    authpool["release_error"] = RuntimeError("lease already recycled")  # idempotent-ish
    conn = FakeConn([("rmfyalk-case-crawl",), ("proxy:7",),
                     (12, "success", False), (12, "success"), (5, None)])
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [])
    monkeypatch.setattr(dcli, "create_job", lambda ns, body: body)
    monkeypatch.setattr(dcli, "get_job", lambda ns, name: JOB_DONE)
    execute_k8s(conn, ROW, "flk-law-crawl", DECL, "scraw")  # must not raise
    assert finished == [(42, {"run_id": 12, "status": "done", "error_head": None})]
    assert authpool["outcomes"] == [(5, 5)]  # outcome still recorded best-effort


# --- orphan sweep (reap-orphaned-runs): single-flight release --------------
# 2026-10-14: a running crawl_runs row whose Job Failed and whose poller died
# blocked every later dispatch for its source, with no reaper. The sweep must
# close such rows AND never touch a run whose Job is still active.

class _SweepCursor:
    """Cursor answering the candidate query then recording reap UPDATEs.

    Rows come back as tuples, like psycopg2 (the real ``orphaned_run_candidates``
    indexes row[0..3]); ``rowcount`` is what ``reap_orphaned_run`` reads.
    """

    def __init__(self, conn):
        self._conn = conn
        self._last = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self._conn.executed.append((flat, params))
        if "GROUP BY 1" in flat:
            # 诊断面：running 行按站点分组（`running_counts_by_site`）
            self._last = "counts"
            self.rowcount = 0
            return
        self._last = ("candidates" if "FROM crawl_runs r" in flat else "reap")
        if self._last == "reap":
            self._conn.reaped.append(params[1])  # run_id
            self.rowcount = 1

    def fetchall(self):
        if self._last == "counts":
            return list(self._conn.site_counts.items())
        return list(self._conn.candidates)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class SweepConn:
    """Stand-in for the orphan sweep: candidates list + reap recorder."""

    def __init__(self, candidates, site_counts=None):
        self.candidates = candidates
        # 诊断面查询的返回：{site: n}（None 键 = 无站点归属，回退到查询方自己的站点）
        self.site_counts = site_counts if site_counts is not None else {}
        self.executed = []
        self.reaped = []

    def cursor(self):
        return _SweepCursor(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# psycopg2-shaped row: (id, source, started_at, budget)
CAND = (2033, "gov-rules-crawl", "2026-10-13 23:19:30+00", 14400)


def test_orphan_sweep_reaps_when_no_active_job(monkeypatch):
    """超龄 running 行 + 集群侧无活动 Job → 回收为 failed 并留痕。"""
    conn = SweepConn([CAND])
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [])  # nothing running
    monkeypatch.setenv("FD_DISPATCH_NAMESPACE", "scraw")
    assert dcli.reap_orphaned_runs(conn, "xinru-server1") == 1
    assert conn.reaped == [2033]
    reap_sql = [s for s, _ in conn.executed if "UPDATE crawl_runs" in s]
    assert reap_sql and "status = 'running'" in reap_sql[0]  # conditional update
    reason = [p[0] for s, p in conn.executed if "UPDATE crawl_runs" in s][0]
    assert "orphan-reaped" in reason and "gov-rules-crawl" in reason


def test_orphan_sweep_skips_run_with_active_job(monkeypatch):
    """真在跑（集群侧有活动 Job）→ 一行不动，即便已超龄。"""
    conn = SweepConn([CAND])
    active = {"metadata": {"name": "gov-rules-crawl-182"}, "status": {}}
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [active])
    monkeypatch.setenv("FD_DISPATCH_NAMESPACE", "scraw")
    assert dcli.reap_orphaned_runs(conn, "xinru-server1") == 0
    assert conn.reaped == []


def test_orphan_sweep_disabled_by_zero(monkeypatch):
    """FD_ORPHAN_GRACE_SECONDS=0 → 不查不算不写（排障期冻结）。"""
    conn = SweepConn([CAND])
    monkeypatch.setattr(dcli, "list_jobs",
                        lambda ns: pytest.fail("must not query k8s when disabled"))
    monkeypatch.setenv("FD_ORPHAN_GRACE_SECONDS", "0")
    assert dcli.reap_orphaned_runs(conn, "xinru-server1") == 0
    assert conn.executed == []


def test_orphan_sweep_no_candidates_is_quiet(monkeypatch):
    """无候选 → 不发 k8s 请求、不写库（常态零成本）。"""
    conn = SweepConn([])
    monkeypatch.setattr(dcli, "list_jobs",
                        lambda ns: pytest.fail("must not query k8s without candidates"))
    monkeypatch.setenv("FD_DISPATCH_NAMESPACE", "scraw")
    assert dcli.reap_orphaned_runs(conn, "xinru-server1") == 0
    assert conn.reaped == []


def test_orphan_sweep_k8s_error_never_breaks_tick(monkeypatch):
    """k8s 不可达 → 只告警不抛出（回收绝不能拖垮 tick）。"""
    conn = SweepConn([CAND])
    def boom(ns):
        raise RuntimeError("k8s down")
    monkeypatch.setattr(dcli, "list_jobs", boom)
    monkeypatch.setenv("FD_DISPATCH_NAMESPACE", "scraw")
    assert dcli.reap_orphaned_runs(conn, "xinru-server1") == 0  # no raise
    assert conn.reaped == []


def test_orphan_sweep_only_judges_its_own_site(monkeypatch):
    """回收判据是「**本集群**里没有它的活动 Job」⇒ 只能判自己站点的行。

    中央库是所有站点共写的（`FD_CRAWL_DB_URL`）：tencent 的 dispatcher（命名空间
    `fd-industry`）与 xinru 的 dispatcher（`k8s/xinru-dispatcher.yaml`，
    `FD_DISPATCH_SITE=xinru-server1`、命名空间 `scraw`）各写各的行。不过滤站点时
    tencent 会拿自己集群的 Job 表去判 xinru 的行——那边正在跑的 Job 它永远看不见，
    **活得好好的一行会被当孤儿回收**。`reap_orphaned_runs(conn, site)` 的 `site`
    参数此前完全没被读过（AST 扫「未用参数」扫出来的）。
    """
    conn = SweepConn([CAND])
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [])
    monkeypatch.setenv("FD_DISPATCH_NAMESPACE", "fd-industry")
    dcli.reap_orphaned_runs(conn, "tencent")

    cand_sql = [(s, p) for s, p in conn.executed if "FROM crawl_runs r" in s]
    assert cand_sql, "候选查询必须发生"
    sql, params = cand_sql[0]
    assert "pending_runs p" in sql and "p.site" in sql, sql
    assert "= %s" in sql and params and "tencent" in params, (sql, params)

    # 判不出自己的站点 ⇒ 一条都不判（回收是破坏性动作，异常时退回「什么都不做」）
    quiet = SweepConn([CAND])
    monkeypatch.setattr(dcli, "list_jobs",
                        lambda ns: pytest.fail("没有站点就不该查 k8s"))
    assert dcli.reap_orphaned_runs(quiet, "") == 0
    assert quiet.executed == [] and quiet.reaped == []


def test_orphan_sweep_failure_is_distinguishable_and_never_leaks_dsn(monkeypatch, capsys):
    """回收器自己坏了，必须与「今天确实没有孤儿」长得不一样。

    旧版把异常打成 `orphan sweep skipped: {e}` —— 与 `0 candidate(s)` 一样都是「回收器
    没动静」，而回收器停摆不产生 backlog、不产生错误行（同族于 federation-observer 卡
    DeadlineExceeded 那次）。所以现在：① 成功/空行点名站点；② 失败行点名站点与异常
    **类型**；③ **不打印异常原文**——psycopg2 的连接错误会把 DSN 带出来。
    """
    def boom(*a, **kw):
        raise RuntimeError("connection to server at db.internal failed: password=sup3rs3cret")

    monkeypatch.setattr(dcli.dispatch, "orphaned_run_candidates", boom)
    monkeypatch.setenv("FD_DISPATCH_NAMESPACE", "fd-industry")
    assert dcli.reap_orphaned_runs(SweepConn([]), "tencent") == 0  # tick 不被拖垮

    err = capsys.readouterr().err
    assert "FAILED" in err and "site=tencent" in err, err
    assert "RuntimeError" in err, err                      # 点名类型，便于归因
    assert "sup3rs3cret" not in err, "异常原文会把 DSN/口令带进日志：" + err
    assert "不是「没有孤儿」" in err, err

    # 对照：正常空轮必须说清「扫的是哪个站点」，且不带 FAILED
    monkeypatch.undo()
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [])
    monkeypatch.setenv("FD_DISPATCH_NAMESPACE", "fd-industry")
    monkeypatch.setattr(dcli.dispatch, "orphaned_run_candidates",
                        lambda *a, **kw: [])
    assert dcli.reap_orphaned_runs(SweepConn([]), "xinru-server1") == 0
    out = capsys.readouterr().out
    assert "site=xinru-server1" in out and "0 candidate(s)" in out, out
    assert "FAILED" not in out, out


def test_orphan_sweep_reports_running_rows_of_sites_it_cannot_judge(monkeypatch, capsys):
    """别家站点还有 running 行 ⇒ 必须念出来，不许把「没人收」读成「没有孤儿」。

    站点收窄（4ce1299）之后本回收器只判自己站点；docker 站点（zihan /
    xinru-server2 / nbs-workers）的执行者是宿主 worker、根本不起 Job，**这一档
    没有任何回收器**。2026-11-15 只读实测：三个 docker 站点的 pending_runs 合计
    129 行 —— 盲区不是空的，只是今天恰好没有挂着的行。
    """
    conn = SweepConn([], {"tencent": 2, "zihan": 1, "xinru-server2": 3, None: 1})
    monkeypatch.setenv("FD_DISPATCH_NAMESPACE", "fd-industry")
    dcli.reap_orphaned_runs(conn, "tencent")
    out = capsys.readouterr().out
    assert "zihan=1" in out and "xinru-server2=3" in out, out
    assert "没有回收器" in out, out
    # 自己的站点不进「别家」榜；无归属的行回退到本站点，也不进
    assert "tencent=" not in out.replace("site=tencent", ""), out
    assert "None=" not in out, out


def test_orphan_sweep_says_nothing_when_no_other_site_is_running(monkeypatch, capsys):
    """没有别家在跑 ⇒ 一个字的诊断都不加，正常日志保持干净。"""
    conn = SweepConn([], {"tencent": 2})
    monkeypatch.setenv("FD_DISPATCH_NAMESPACE", "fd-industry")
    assert dcli.reap_orphaned_runs(conn, "tencent") == 0
    out = capsys.readouterr().out
    assert "0 candidate(s)" in out, out
    assert "别家站点" not in out, out


def test_orphan_sweep_counts_failure_is_named_not_swallowed(monkeypatch, capsys):
    """诊断查询自己坏了 ⇒ 点名异常类型（stderr），且不影响回收本身。

    「数不出来」与「数出来是 0」必须长得不一样 —— 否则统计查询挂掉的那天，
    日志照旧打「没有孤儿」，而别家站点可能已经挂了一周。
    """
    def boom(conn):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(dcli.dispatch, "running_counts_by_site", boom)
    monkeypatch.setenv("FD_DISPATCH_NAMESPACE", "fd-industry")
    assert dcli.reap_orphaned_runs(SweepConn([]), "tencent") == 0
    cap = capsys.readouterr()          # 只读一次：readouterr() 会把缓冲清空
    assert "没数出来" in cap.err and "RuntimeError" in cap.err, cap.err
    assert "0 candidate(s)" in cap.out, cap.out   # 回收本身照常
    assert "别家站点" not in cap.out, cap.out      # 没数出来就不许装作数过了
