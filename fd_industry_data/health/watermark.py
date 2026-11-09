"""Data-period watermark: did this period actually bring anything new?

Change harden-crawl-delivery D5 / task 3.2. ``rows_written > 0`` does not prove
freshness: a spider that re-writes the same old month every day has a beautiful
row count and a frozen watermark. The watermark is the newest data period the
run actually carried.

Two things make this harder than ``max(payload->>'period')``:

1. **Only 33 of 119 sources expose a period key** (2026-11-09), and the key is
   not always called ``period``. So the field is *declared per source* in the
   manifest (``data_period_field``), never guessed.
2. **The values are heterogeneous** — measured on the same fleet:
   ``2026-09``, ``2026-11``, ``2026-10-21~2026-10-31`` (a range), ``2026``.
   Normalisation therefore reports a granularity, and a range compares by its
   **end** (what the row covers up to).

States are three-way on purpose (unknown is not empty):

- ``ok``          — a comparable period was found
- ``unavailable`` — no field declared (or no items): we cannot judge
- ``unknown``     — a field was declared but the values cannot be normalised
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

# 期望发布节奏（天/月/年）。irregular 没有节奏，判不了停滞——那是 unavailable，
# 不是「健康」。
_CADENCE = {"day": "day", "month": "month", "year": "year"}

#: 粒度由细到粗。水位粒度比周期粗时不可比：年值代表 12-31，在月频下永远"未来"。
_RANK = {"day": 0, "week": 1, "month": 2, "year": 3}

_YEAR_RE = re.compile(r"^(\d{4})$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_DAY_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?$")
_WEEK_RE = re.compile(r"^(\d{4})-W(\d{1,2})$")
_RANGE_SPLIT = re.compile(r"\s*[~—–至]\s*|\s+\.\.\s+")

ALERT_AFTER_PERIODS = 2


@dataclass(frozen=True)
class Period:
    """A normalised data period with a total order across granularities."""

    raw: str
    canonical: str
    granularity: str  # year | month | day | week
    sort_key: tuple[int, int, int]  # (year, month, day) — a comparable date
    end: date

    def days_behind(self, today: date) -> int:
        return (today - self.end).days


def _last_day(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - date(year, month, 1)).days


def normalise(value) -> Period | None:
    """Parse one period value; None when it cannot be compared.

    Ranges take their end (``2026-10-21~2026-10-31`` covers up to 10-31) — that
    is the newest data the row actually carries.
    """
    text = str(value or "").strip()
    if not text:
        return None
    parts = _RANGE_SPLIT.split(text)
    if len(parts) > 1:
        text = parts[-1].strip()
    if m := _YEAR_RE.match(text):
        y = int(m.group(1))
        return Period(text, f"{y:04d}", "year", (y, 12, 31), date(y, 12, 31))
    if m := _MONTH_RE.match(text):
        y, mo = int(m.group(1)), int(m.group(2))
        if not 1 <= mo <= 12:
            return None
        d = _last_day(y, mo)
        return Period(text, f"{y:04d}-{mo:02d}", "month", (y, mo, d), date(y, mo, d))
    if m := _DAY_RE.match(text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            day = date(y, mo, d)
        except ValueError:
            return None
        return Period(text, day.isoformat(), "day", (y, mo, d), day)
    if m := _WEEK_RE.match(text):
        y, w = int(m.group(1)), int(m.group(2))
        try:
            day = date.fromisocalendar(y, w, 7)  # ISO week ends Sunday
        except ValueError:
            return None
        return Period(text, f"{y:04d}-W{w:02d}", "week", (day.year, day.month, day.day), day)
    return None


def max_period(items, field: str | None) -> dict:
    """Newest comparable period among ``items[field]``.

    Returns ``{"state": ok|unavailable|unknown, "period": Period|None,
    "raw": str|None, "seen": int}``. A declared field whose values never parse
    is ``unknown`` — never silently treated as "no new data".
    """
    if not field:
        return {"state": "unavailable", "period": None, "raw": None, "seen": 0,
                "reason": "no data_period_field declared"}
    seen = 0
    best: Period | None = None
    raw_of_best: str | None = None
    unparsed = 0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        value = item.get(field)
        if value in (None, ""):
            continue
        seen += 1
        period = normalise(value)
        if period is None:
            unparsed += 1
            continue
        if best is None or period.sort_key > best.sort_key:
            best, raw_of_best = period, str(value)
    if best is None:
        if seen:
            return {"state": "unknown", "period": None, "raw": None, "seen": seen,
                    "reason": f"declared field {field!r} had {unparsed} unparsable values"}
        return {"state": "unavailable", "period": None, "raw": None, "seen": 0,
                "reason": f"declared field {field!r} absent from every item"}
    return {"state": "ok", "period": best, "raw": raw_of_best, "seen": seen,
            "unparsed": unparsed}


def periods_behind(period: Period, data_period: str | None, today: date) -> float | None:
    """How many expected publish periods have elapsed since ``period`` ended.

    ``None`` when there is no cadence (``irregular`` / unknown ``data_period``):
    "cannot judge" must not look like "0 periods behind". Also ``None`` when the
    watermark is **coarser** than the cadence — a year-only watermark under a
    monthly cadence represents Dec 31 of that year, which is always in the
    future, so it would report 0 behind forever (measured on `amis-market`:
    `year` field + `data_period: month` → "advance", which is false).
    """
    cadence = _CADENCE.get(str(data_period or "").strip().lower())
    if cadence is None:
        return None
    if _RANK[period.granularity] > _RANK[cadence]:
        return None
    days = period.days_behind(today)
    if days < 0:
        return 0.0
    if cadence == "day":
        return float(days)
    if cadence == "month":
        return days / 30.44
    return days / 365.25


def judge(watermark: dict, data_period: str | None, today: date) -> dict:
    """Final state: advance / stall / unavailable / unknown."""
    state = watermark.get("state")
    if state != "ok":
        return {"state": state, "periods_behind": None,
                "reason": watermark.get("reason", state)}
    period: Period = watermark["period"]
    behind = periods_behind(period, data_period, today)
    if behind is None:
        cadence = _CADENCE.get(str(data_period or "").strip().lower())
        reason = (f"watermark granularity {period.granularity!r} is coarser than "
                  f"cadence {cadence!r}; staleness cannot be judged"
                  if cadence and _RANK[period.granularity] > _RANK[cadence]
                  else f"data_period {data_period!r} has no cadence")
        return {"state": "unavailable", "periods_behind": None,
                "period": period.canonical, "granularity": period.granularity,
                "reason": reason}
    stalled = behind >= ALERT_AFTER_PERIODS
    return {
        "state": "stall" if stalled else "advance",
        "period": period.canonical,
        "granularity": period.granularity,
        "periods_behind": round(behind, 2),
        "threshold": ALERT_AFTER_PERIODS,
        "reason": (f"watermark {period.canonical} is {behind:.1f} periods behind"
                   if stalled else f"watermark {period.canonical} current"),
    }


def utc_today() -> date:
    return datetime.utcnow().date()
