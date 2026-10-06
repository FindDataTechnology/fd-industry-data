"""Federation observer mapping logic (crawl-platform tasks 6.1/6.2) plus
the allow-list mirror contract (legal-line-federation task 2.4, amended):
the fallback mirror default-denies and only watches CronJobs explicitly
listed in ALLOWED_CRONJOBS (executors that cannot direct-report), while
the observer itself, every *-load CronJob and dispatcher-created Jobs
stay hard-excluded even over an allow-list entry."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fd_industry_data.federation_observer import (
    ALLOWED_CRONJOBS,
    excluded_cronjob,
    is_dispatcher_job,
    job_outcome,
    owned_by_excluded,
    source_for,
    watched,
)


CRONJOBS = ["flk-law-crawl", "flk-law-load", "guide-cases-crawl", "flk-law-embed"]


def test_owner_reference_wins():
    job = {"metadata": {"name": "flk-law-crawl-29839123",
                        "ownerReferences": [{"kind": "CronJob", "name": "flk-law-crawl"}]}}
    assert source_for(job, CRONJOBS) == "flk-law-crawl"


def test_manual_job_matched_by_prefix():
    job = {"metadata": {"name": "flk-law-crawl-manual-20260924"}}
    assert source_for(job, CRONJOBS) == "flk-law-crawl"


def test_unrelated_job_skipped():
    job = {"metadata": {"name": "random-one-off"}}
    assert source_for(job, CRONJOBS) is None


def test_outcomes():
    ok = {"status": {"conditions": [{"type": "Complete"}]}}
    assert job_outcome(ok) == ("success", None)
    bad = {"status": {"conditions": [
        {"type": "Failed", "reason": "BackoffLimitExceeded", "message": "job reached backoff"}]}}
    status, head = job_outcome(bad)
    assert status == "failed" and "BackoffLimitExceeded" in head


# --- allow-list mirror contract (default deny; exclusions win) ------------

def test_allow_list_covers_only_legacy_no_report_executor():
    assert ALLOWED_CRONJOBS == {"flk-law-embed"}
    ns = ["flk-law-crawl", "flk-law-load", "guide-cases-crawl",
          "guide-cases-load", "flk-law-embed", "federation-observer",
          "mfa-treaty-crawl", "legal-sources-report"]
    assert watched(ns) == ["flk-law-embed"]


def test_allow_list_miss_is_default_deny():
    # wave2+ / direct-report sources need zero observer maintenance
    assert watched(["mfa-treaty-crawl", "gov-rules-crawl",
                    "party-regulation-crawl"]) == []
    assert watched([]) == []


def test_hard_exclusions_win_over_allow_list(monkeypatch):
    import fd_industry_data.federation_observer as fo
    monkeypatch.setattr(fo, "ALLOWED_CRONJOBS",
                        frozenset({"flk-law-embed", "federation-observer",
                                   "rmfyalk-load", "x-crawl"}))
    # self and *-load stay excluded even when explicitly listed;
    # x-crawl is legitimately listed so it is kept
    assert fo.watched(["flk-law-embed", "federation-observer",
                       "rmfyalk-load", "x-crawl"]) == ["flk-law-embed", "x-crawl"]


def test_excluded_cronjob_names():
    assert excluded_cronjob("federation-observer")  # the observer itself
    assert excluded_cronjob("flk-law-load")         # loaders direct-report
    assert excluded_cronjob("rmfyalk-load")
    assert not excluded_cronjob("flk-law-embed")
    assert not excluded_cronjob("guide-cases-crawl")


def test_direct_report_job_not_matched_after_watch_filtering():
    # non-allowed CronJob dropped from the list -> its Jobs match nothing
    job = {"metadata": {"name": "flk-law-load-29380621"}}
    assert source_for(job, watched(["flk-law-load", "flk-law-embed"])) is None


def test_job_owned_by_unwatched_cronjob_never_mirrored_even_on_prefix_hit():
    # owner is a non-allowed CronJob whose Job name would prefix-match the
    # allowed flk-law-embed — the owner guard wins over the prefix heuristic
    job = {"metadata": {"name": "flk-law-embed-123",
                        "ownerReferences": [{"kind": "CronJob", "name": "guide-cases-crawl"}]}}
    watched_names = watched(CRONJOBS)
    assert watched_names == ["flk-law-embed"]
    assert source_for(job, watched_names) == "flk-law-embed"  # prefix alone would hit
    assert owned_by_excluded(job, watched_names)


def test_job_owned_by_allowed_cronjob_mirrors():
    job = {"metadata": {"name": "flk-law-embed-29839123",
                        "ownerReferences": [{"kind": "CronJob", "name": "flk-law-embed"}]}}
    assert not owned_by_excluded(job, ["flk-law-embed"])
    assert source_for(job, ["flk-law-embed"]) == "flk-law-embed"


def test_orphan_job_falls_through_to_prefix():
    job = {"metadata": {"name": "flk-law-embed-manual-20260924"}}
    assert not owned_by_excluded(job, ["flk-law-embed"])  # no owner: prefix decides
    assert source_for(job, ["flk-law-embed"]) == "flk-law-embed"


def test_dispatcher_created_jobs_are_excluded():
    run_job = {"metadata": {"name": "flk-law-crawl-42",
                            "labels": {"fd-industry/managed-by": "fd-dispatcher"}}}
    tick_job = {"metadata": {"name": "fd-industry-dispatcher-29380675",
                             "labels": {"fd-industry/managed-by": "fd-dispatcher"}}}
    assert is_dispatcher_job(run_job) and is_dispatcher_job(tick_job)
    assert not is_dispatcher_job({"metadata": {"name": "flk-law-crawl-29839123"}})
    assert not is_dispatcher_job({"metadata": {}})
    assert not is_dispatcher_job({"metadata": {"labels": {"app": "x"}}})
