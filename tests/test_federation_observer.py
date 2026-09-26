"""Federation observer mapping logic (crawl-platform tasks 6.1/6.2)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fd_industry_data.federation_observer import job_outcome, source_for


CRONJOBS = ["flk-law-crawl", "flk-law-load", "guide-cases-crawl"]


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
