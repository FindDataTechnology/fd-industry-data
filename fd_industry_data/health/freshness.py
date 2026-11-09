"""Cadence-aware freshness: how many scheduled cycles produced no good run.

Change harden-crawl-delivery D2. The old judgement multiplied a source's
period by 2.5 hours, which is a patience bound, not a cycle count: a monthly
source was permanently "fresh" for 75 days and a daily one stayed silent for
two and a half days. This module answers the question directly — *how many of
the moments this source was supposed to run have passed without a good run?*

A good run is ``ok`` or ``ok_empty``: the source executed and reached its data
surface. ``blocked-config`` is not good (nothing was fetched) but it is also
not evidence of a broken crawler, so callers should classify it separately
before deciding to alert.

The window is capped by ``lookback_days`` so a source that has been dead for
years does not cost millions of minute steps; when the cap bites, the result
says so instead of pretending the count is exact.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# A cycle counts as "missed" once its scheduled fire time has passed without a
# good run after it. Two consecutive misses is the alert threshold (D2): one
# gives a single network blip room to clear.
ALERT_AFTER_CYCLES = 2

DEFAULT_LOOKBACK_DAYS = 30


def _expand(field: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            part, _, raw_step = part.partition("/")
            try:
                step = max(1, int(raw_step))
            except ValueError:
                return set()
        if part == "*":
            out.update(range(lo, hi + 1, step))
        elif "-" in part:
            a, _, b = part.partition("-")
            try:
                start, end = int(a), int(b)
            except ValueError:
                return set()
            if start <= end:
                out.update(range(start, end + 1, step))
            else:  # wraparound range such as 22-2 (hours)
                out.update(range(start, hi + 1, step))
                out.update(range(lo, end + 1, step))
        else:
            try:
                out.add(int(part))
            except ValueError:
                return set()
    return out


@dataclass(frozen=True)
class Schedule:
    minutes: set[int]
    hours: set[int]
    doms: set[int]
    months: set[int]
    dows: set[int]
    dom_restricted: bool
    dow_restricted: bool

    @classmethod
    def parse(cls, expr: str) -> "Schedule | None":
        f = str(expr or "").split()
        if len(f) != 5:
            return None
        minutes, hours, dom, month, dow = f
        parsed = cls(
            minutes=_expand(minutes, 0, 59),
            hours=_expand(hours, 0, 23),
            doms=_expand(dom, 1, 31),
            months=_expand(month, 1, 12),
            dows=_expand(dow, 0, 6),
            dom_restricted=dom != "*",
            dow_restricted=dow != "*",
        )
        if not (parsed.minutes and parsed.hours and parsed.doms and parsed.months):
            return None
        return parsed

    def matches(self, t: datetime) -> bool:
        if t.minute not in self.minutes or t.hour not in self.hours:
            return False
        if t.month not in self.months:
            return False
        # ``dom`` must NOT be a hard gate here: with both dom and dow
        # restricted, Vixie's rule is OR (a test on 2026-11-09, a Monday with
        # dom="1", caught this by returning False).
        dom_ok = t.day in self.doms
        # Python weekday Mon=0 -> cron dow Sun=0
        dow_ok = ((t.weekday() + 1) % 7) in self.dows
        if self.dom_restricted and self.dow_restricted:
            return dom_ok or dow_ok
        return dom_ok and dow_ok


@dataclass(frozen=True)
class Freshness:
    cycles: int
    capped: bool
    first_fire: datetime | None
    last_fire: datetime | None
    note: str | None = None

    @property
    def alerting(self) -> bool:
        return self.cycles >= ALERT_AFTER_CYCLES


def _zone(tz: str | None) -> tuple[ZoneInfo, str | None]:
    if not tz:
        return ZoneInfo("UTC"), None
    try:
        return ZoneInfo(tz), None
    except Exception:  # noqa: BLE001 - unknown tz degrades, never raises
        return ZoneInfo("UTC"), f"unknown schedule_tz {tz!r}, treated as UTC"


def cycles_since_last_good(
    *,
    schedule: str,
    tz: str | None,
    last_good_at: datetime | None,
    now: datetime,
    created_at: datetime | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> Freshness:
    """Count scheduled fire times after ``last_good_at`` up to ``now``.

    Each fire time marks the start of a cycle; a cycle is missed when the next
    fire arrives and no good run landed in between. Counting fires *after* the
    last good run is equivalent and cheaper: a good run at or after a fire
    time proves that cycle delivered.

    With no good run at all, counting starts at ``created_at`` (never before a
    workload existed) clamped to the lookback window — a brand-new source whose
    first fire has not arrived scores 0, not 1.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local_tz, note = _zone(tz)
    parsed = Schedule.parse(schedule)
    if parsed is None:
        return Freshness(0, False, None, None, note or f"unparseable schedule {schedule!r}")

    if last_good_at is not None:
        start = last_good_at
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
    else:
        if created_at is None:
            # Never succeeded AND no known workload start: counting from the
            # whole lookback window would invent 30 missed cycles for a source
            # created an hour ago. Report "unknown" instead of a number.
            return Freshness(0, False, None, None,
                             note or "no good run and no known start; cycles unknown")
        start = max(created_at, now - timedelta(days=lookback_days))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)

    capped = False
    if start < now - timedelta(days=lookback_days):
        start = now - timedelta(days=lookback_days)
        capped = last_good_at is None or last_good_at < start

    if start >= now:
        return Freshness(0, capped, None, None, note)

    # Walk local minutes from the first minute after ``start``.
    cursor = start.astimezone(local_tz).replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = now.astimezone(local_tz)
    count = 0
    first_fire = last_fire = None
    while cursor <= end:
        if parsed.matches(cursor):
            count += 1
            first_fire = first_fire or cursor
            last_fire = cursor
        cursor += timedelta(minutes=1)

    return Freshness(count, capped, first_fire, last_fire, note)
