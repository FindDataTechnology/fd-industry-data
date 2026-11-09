"""Self-recovery terminal tests (harden-crawl-delivery 2.9)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from fd_industry_data.health import recovery, ticket

NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)


class _Run:
    def __init__(self, hours_ago, status="success", outcome="ok"):
        self.started_at = (NOW - timedelta(hours=hours_ago)).isoformat()
        self.status = status
        self.outcome = outcome


def _ticket(created_hours_ago=24, **kw):
    return ticket.new_ticket(
        source="alpha", unit="spiders/alpha/", evidence={}, verify_commands=["echo"],
        created_at=(NOW - timedelta(hours=created_hours_ago)).isoformat(), **kw)


def test_three_good_runs_after_ticket_recover():
    doc = _ticket(created_hours_ago=72)
    runs = [_Run(60), _Run(48), _Run(12)]  # oldest→newest
    decided, ev = recovery.should_recover(doc, runs, now=NOW)
    assert decided and ev["consecutive_good"] == 3


def test_runs_before_the_ticket_do_not_count():
    doc = _ticket(created_hours_ago=10)
    runs = [_Run(60), _Run(48), _Run(12)]  # all before creation except none
    decided, ev = recovery.should_recover(doc, runs, now=NOW)
    assert not decided


def test_streak_breaks_on_a_failure():
    doc = _ticket(created_hours_ago=72)
    runs = [_Run(60, status="failed", outcome="failed"), _Run(48), _Run(12)]
    decided, ev = recovery.should_recover(doc, runs, now=NOW)
    assert not decided and ev["consecutive_good"] == 2


def test_blocked_config_does_not_count_as_recovery():
    doc = _ticket(created_hours_ago=72)
    runs = [_Run(48), _Run(12, status="blocked-config", outcome="blocked-config")]
    decided, _ = recovery.should_recover(doc, runs)
    assert not decided


def test_ok_empty_counts_as_good():
    doc = _ticket(created_hours_ago=72)
    runs = [_Run(60, outcome="ok_empty"), _Run(48, outcome="ok_empty"), _Run(12, outcome="ok")]
    decided, ev = recovery.should_recover(doc, runs, now=NOW)
    assert decided and ev["consecutive_good"] == 3


def test_stale_success_does_not_count_as_recovery():
    """跑过三次随后停摆 ≠ 恢复：末次成功必须仍然新鲜。

    2026-11-09 预演实测 sec-edgar-facts / hkex-disclosure 就是这形状（11-07 连续
    成功，此后 54h 无运行），按不加新鲜度前置的判据会被误判为已恢复。
    """
    doc = _ticket(created_hours_ago=120)
    runs = [_Run(96, outcome="ok"), _Run(90, outcome="ok"), _Run(84, outcome="ok")]
    decided, ev = recovery.should_recover(doc, runs, now=NOW, max_age_hours=48)
    assert not decided
    assert ev["consecutive_good"] == 3
    assert ev["fresh"] is False
    assert ev["last_good_age_hours"] == 84.0


def test_fresh_success_allows_recovery():
    doc = _ticket(created_hours_ago=72)
    runs = [_Run(60), _Run(48), _Run(12)]
    decided, ev = recovery.should_recover(doc, runs, now=NOW, max_age_hours=48)
    assert decided and ev["fresh"] is True


def test_recovered_is_a_legal_terminal_and_closed_is_not_automatic():
    doc = _ticket()
    ticket.set_terminal(doc, "recovered", note="3 consecutive ok")
    assert doc["terminal"] == "recovered"
    assert "recovered" in ticket.TERMINALS
    assert "recovered" in ticket.AUTO_TERMINALS


def test_automatic_path_may_not_close():
    doc = _ticket()
    with pytest.raises(ValueError, match="closed"):
        ticket.set_terminal(doc, "closed")
    # the human path (STATUS readback of a merge) may
    ticket.set_terminal(doc, "closed", human=True)
    assert doc["terminal"] == "closed"
