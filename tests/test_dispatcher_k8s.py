"""Dispatcher k8s execution branch (legal-line-federation 3.1/3.2): claim
routing between the federated in-cluster Job form and the platform
subprocess form, the cluster-side single-flight refusal, cancel handling,
and terminal-state write-back. The k8s API is faked at the module-function
seam (list/create/get/delete_job); the DB is a minimal record/fetch stand-in
in the same style as test_dispatch_site."""
from __future__ import annotations

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
    conn = FakeConn(fetches=[("federated", "img:1", ["node", "bin/x.mjs"], 900)])
    assert source_runner(conn, "flk-law-crawl") == {
        "kind": "federated", "runner_image": "img:1",
        "runner_command": ["node", "bin/x.mjs"], "timeout_seconds": 900}


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
    assert {e["name"] for e in container["env"]} == {"PYTHONUNBUFFERED", "FD_PENDING_RUN_ID"}


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
    conn = FakeConn([(None, None, False),    # poll 1: no linked run yet
                     (None, None, False),    # poll 2: still none
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
    conn = FakeConn([(None, None, False), (None, None, False), (None, None, False)])
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [])
    monkeypatch.setattr(dcli, "create_job", lambda ns, body: body)
    monkeypatch.setattr(dcli, "get_job", lambda ns, name: JOB_DONE)  # terminal at once
    execute_k8s(conn, ROW, "flk-law-crawl", DECL, "scraw")
    assert finished == [(42, {"run_id": None, "status": "done", "error_head": None})]


def test_failed_job_reports_condition_head(monkeypatch, finished, no_sleep):
    conn = FakeConn([(None, None, False), (None, None, False), (None, None, False)])
    monkeypatch.setattr(dcli, "list_jobs", lambda ns: [])
    monkeypatch.setattr(dcli, "create_job", lambda ns, body: body)
    monkeypatch.setattr(dcli, "get_job", lambda ns, name: JOB_FAILED)
    execute_k8s(conn, ROW, "flk-law-crawl", DECL, "scraw")
    pid, kw = finished[0]
    assert pid == 42 and kw["status"] == "failed" and kw["run_id"] is None
    assert "DeadlineExceeded" in kw["error_head"]


def test_conflict_409_adopts_existing_job(monkeypatch, finished, no_sleep):
    conn = FakeConn([(None, None, False),    # poll: no linked run yet
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
    conn = FakeConn([(15, "running", True)])  # linked run carries the cancel flag
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
