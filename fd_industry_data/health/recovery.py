"""Self-recovery terminal (harden-crawl-delivery 2.9).

A ticket that says "this source is broken" must have a way to say "it stopped
being broken" — otherwise it hangs forever. On 2026-11-09 seventeen tickets had
no terminal for exactly this reason: the terminal set had no automatic
"recovered" state, and `closed` is reserved for a human.

This module decides, per open ticket, whether the source has recovered. The bar
is deliberately the same shape as [[ALERT_AFTER_CYCLES]]: N consecutive good
runs *after the ticket was opened*, so a run that predates the ticket cannot
count as evidence of recovery.
"""
from __future__ import annotations

from datetime import datetime, timezone

# Three consecutive good runs: one could be a lucky retry, two could be a
# transient upstream gap; three spans at least two publication cycles for a
# daily source.
RECOVER_AFTER_RUNS = 3


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def consecutive_good_since(runs, since: str | None) -> int:
    """Trailing count of good runs started after ``since``.

    ``runs`` is oldest→newest. A run must be ``ok`` or ``ok_empty`` (it reached
    its data surface) — ``blocked-config`` proves nothing about the crawler and
    must not count either way.
    """
    cutoff = _ts(since)
    count = 0
    for run in reversed(list(runs or [])):
        if cutoff is not None:
            started = _ts(getattr(run, "started_at", None))
            if started is not None and started <= cutoff:
                break
        outcome = getattr(run, "outcome", None)
        status = getattr(run, "status", None)
        good = outcome in ("ok", "ok_empty") or (
            outcome is None and status in ("success", "partial"))
        if not good:
            break
        count += 1
    return count


def should_recover(ticket: dict, runs, threshold: int = RECOVER_AFTER_RUNS, *,
                   now: datetime | None = None,
                   max_age_hours: float = 72.0,
                   stale_cycles: int | None = None) -> tuple[bool, dict]:
    """(decided, evidence) for one open ticket.

    Two guards, because "it succeeded three times" alone is not health:

    - ``max_age_hours``: the last success must still be recent. A source that
      ran three times right after the ticket opened and then went silent
      satisfies the streak while being *less* available than before
      (2026-11-09 预演实测 sec-edgar-facts / hkex-disclosure 就是这个形状).
    - ``stale_cycles``: cycles missed according to the source's own cron
      (``freshness.cycles_since_last_good``). This is the authoritative check
      for sources whose ``data_period`` is ``irregular``, where a period-derived
      tolerance is undefined (it came out as ``inf`` and disabled the age guard
      entirely). Recovery requires ``stale_cycles <= 1``.

    Returns evidence either way so the caller can leave a trace when the
    decision is "not yet" — a silent "no" is indistinguishable from a bug.
    """
    created = ticket.get("created_at")
    streak = consecutive_good_since(runs, created)
    last_good_at = getattr(runs[-1], "started_at", None) if runs else None
    age_hours = None
    if last_good_at:
        parsed = _ts(last_good_at)
        if parsed is not None and now is not None:
            age_hours = (now - parsed).total_seconds() / 3600.0
    fresh = age_hours is not None and age_hours <= max_age_hours
    not_stale = stale_cycles is None or stale_cycles <= 1
    decided = streak >= threshold and (now is None or fresh) and not_stale
    return decided, {
        "consecutive_good": streak,
        "threshold": threshold,
        "since": created,
        "last_run_at": last_good_at,
        "last_good_age_hours": None if age_hours is None else round(age_hours, 1),
        "max_age_hours": max_age_hours,
        "stale_cycles": stale_cycles,
        "fresh": fresh,
    }
