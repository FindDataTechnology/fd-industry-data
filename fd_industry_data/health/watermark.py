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

#: 判「停滞」的阈值，单位＝该源自己的期望周期数。
#:
#: 2026-11-16 生产实测（run 43812798047，150 个源）把「全节奏共用一个 2」的缺陷
#: 暴露出来：`periods_behind()` 对 `day` 节奏返回的是**日历天数**，于是「落后 2」
#: 等于「两天没有新日期」——而周末两天没有新交易日是常态。周一早上巡检，周五的
#: 水位一律被判成「落后 3 个周期 = 冻结」：当天命中 16 条，其中
#: `chinabond-yield-curve` / `chinamoney-ncd-curve` / `dce` 的水位正是 11-13（周五，
#: 11-14/15 为周末）。代价不只是噪音：每日出单配额（5 张）被这批吃光、11 条顺延
#: ⇒ **假红在挤掉真故障的位置**。
#:
#: 所以日频阈值按「最长合法静默」定：周末 3 天 ＋ 法定长假 8 天（国庆/春节一档）
#: ＋ 发布延迟 1 天 ＝ 10 个日历日。月/年频仍是 2 个周期（60.9 / 730 天），
#: 那两档本来没有周末问题。
ALERT_AFTER_PERIODS = 2.0
ALERT_AFTER_PERIODS_BY_CADENCE = {"day": 10.0, "month": 2.0, "year": 2.0}


def alert_threshold(cadence: str | None) -> float:
    """该节奏的停滞阈值（单位＝期望周期数）；未列出的节奏退回 `ALERT_AFTER_PERIODS`。"""
    return ALERT_AFTER_PERIODS_BY_CADENCE.get(str(cadence or "").strip().lower(),
                                               ALERT_AFTER_PERIODS)


#: 期望周期长度（天）。把「周期」换算成日历天只在这一处做。
_PERIOD_DAYS = {"day": 1.0, "month": 30.44, "year": 365.25}

#: 水位推进率的及格线（推进天数 ÷ 经过天数，单位无关）。
#:
#: 为什么必须有这一条（2026-11-16 实历）：`periods_behind()` 量的是「离今天多远」，
#: 它把两件事混成一个数——上游**结构性发布滞后**（水位永远离今天很远，但它天天在
#: 前进）与**真的停更**（水位停在同一个值不动）。`carbon-monitor` 的 `spider.py`
#: 第 5 行自己写着 "published with ~2 months lag"：日频数据、上游恒定滞后约两个月，
#: 于是「落后 47 个 day 周期」对它**永远**成立，任何「落后 N 个周期」的阈值都救不了
#: 它，而每日 5 张出单配额会被它占掉、真故障顺延。
#:
#: 健康值≈1（日频遇周末约 0.71：5/7）；真正的冻结≈0。取一半当及格线，两侧都留余量。
MIN_ADVANCE_RATE = 0.5


def window_advance_rate(points, data_period: str | None) -> float | None:
    """窗口内水位推进速度：推进天数 ÷ 经过天数。``None`` = 判不出（样本不足）。

    ``points`` 是 ``(运行时刻 datetime, Period)`` 序列，按时间升序；调用方负责丢掉
    水位不可归一的运行。两个门槛都是为了「测不出就说测不出」，而不是把噪声当结论：

    - 少于 2 个可归一水位 ⇒ 没有跨度可比；
    - 跨度不足该节奏的 2 个周期 ⇒ 一天的抖动除以一天的跨度没有意义。

    返回值的单位无关（天/天＝周期/周期），所以不必再按节奏缩放。
    """
    cadence = _CADENCE.get(str(data_period or "").strip().lower())
    if cadence is None:
        return None
    usable = [(at, period) for at, period in points if period is not None]
    if len(usable) < 2:
        return None
    # 2026-11-16（watermark-history-for-coarse-cadence 2.3）：**先切片再测**。
    # 调用方现在可能给 400 天的宽窗历史（粗节奏源要长跨度才测得出推进率），但
    # 整窗直接测会把近期停更稀释掉——一个半年前停掉的月频源，400 天窗里推进率
    # 还能有 0.55，混进太多「还在推进」的旧样本。只取**末尾** ``max(30 天,
    # 4 个期望周期)``：日频仍是 30 天（与遥测窗同宽，既有判决零变化），月频约
    # 122 天（仍 ≥ 2 个周期的最小可测跨度）。切片后不足 2 点 ⇒ 判不出。
    slice_days = max(30.0, 4.0 * _PERIOD_DAYS[cadence])
    newest_at = usable[-1][0]
    usable = [(at, period) for at, period in usable
              if (newest_at - at).total_seconds() / 86400.0 <= slice_days]
    if len(usable) < 2:
        return None
    (first_at, first), (last_at, last) = usable[0], usable[-1]
    elapsed_days = (last_at - first_at).total_seconds() / 86400.0
    if elapsed_days <= 0 or elapsed_days < 2 * _PERIOD_DAYS[cadence]:
        return None
    advanced_days = (last.end - first.end).days
    return advanced_days / elapsed_days


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


def judge(watermark: dict, data_period: str | None, today: date,
          advance_rate: float | None = None) -> dict:
    """Final state: advance / stall / unavailable / unknown.

    ``advance_rate`` 是窗口内水位推进速度（见 :func:`window_advance_rate`）。
    它把「落后很多」拆成两种：上游结构性发布滞后（仍在按节奏前进）与真的停更
    （停在同一个值不动）。可测且达到 :data:`MIN_ADVANCE_RATE` 时不判冻结——
    否则一条恒定滞后两个月的日频源会被永久误判，而每日出单配额被它占掉。
    ``None``（测不出）时不放松：仍按「落后达阈值」判。
    """
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
    # 阈值按节奏给：日频的「周期」是日历日，周末与长假没有新期是常态（见
    # ALERT_AFTER_PERIODS_BY_CADENCE 上方那段实历）。判词里带出生效阈值，
    # 工单正文因此自解释，读的人不必回查代码。
    threshold = alert_threshold(data_period)
    stalled = behind >= threshold
    if stalled and advance_rate is not None and advance_rate >= MIN_ADVANCE_RATE:
        # 落后只是上游发布结构性滞后：水位仍在按节奏前进。见 MIN_ADVANCE_RATE。
        stalled = False
        reason = (f"watermark {period.canonical} is {behind:.1f} periods behind "
                  f"(threshold {threshold:g}) but advancing at {advance_rate:.2f}x — "
                  "structural publication lag, not a freeze")
    else:
        reason = (f"watermark {period.canonical} is {behind:.1f} periods behind "
                  f"(threshold {threshold:g})"
                  if stalled else f"watermark {period.canonical} current")
    return {
        "state": "stall" if stalled else "advance",
        "period": period.canonical,
        "granularity": period.granularity,
        "periods_behind": round(behind, 2),
        "threshold": threshold,
        "advance_rate": None if advance_rate is None else round(advance_rate, 2),
        "reason": reason,
    }


def utc_today() -> date:
    return datetime.utcnow().date()
