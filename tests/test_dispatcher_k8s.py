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
    assert authpool["leased"] == [{"src": "flk-law-crawl", "owner": "tester",
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
    assert (src, alias) == ("flk-law-crawl", "acct001")
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
