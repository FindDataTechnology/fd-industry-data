"""④ crawl_summary — 中央库 crawl_runs 只读汇总（spec: infra-telemetry-surface R5）。

口径单一事实源 = crawl-run-telemetry spec：直报/兜底互斥、失败留痕、质量字段
——本通道只读消费，不改写、不加严、不放宽。列名实况（health/config.py 2026-10-04
核验）：source/status/started_at/rows_written/error_head。

逐源聚合：last_run_at、last_status、last_success_at、连续失败计数（从最近往回数，
成功即停）、最近成功 rows_written（零产出标记）、error_head（脱敏）。

flags（机器可数，与工厂集群巡检岗阈值判据对齐）：
- no_success      lookback 窗口内无任何 success
- fail_streak     连续失败 >= fail_streak_threshold（默认 3）
- zero_output     最近一次成功 rows_written == 0
- stale_success   有成功但 last_success_at 距 as_of 超过 stale_hours（默认 48）
"""
from __future__ import annotations

from typing import Callable

from .envelope import ChannelError, envelope, scrub, utc_now_iso
from .io_ import run_psql

CHANNEL = "central_pg"
SQL = (
    "SELECT source, status, started_at, COALESCE(rows_written,0), "
    "COALESCE(error_head,'') "
    "FROM crawl_runs WHERE started_at >= now() - (make_interval(hours => %d)) "
    "ORDER BY source, started_at DESC"
)


def _aggregate(rows: list[list[str]], *, fail_streak_threshold: int, stale_hours: int, as_of: str) -> tuple[list[dict], dict]:
    by_source: dict[str, list[list[str]]] = {}
    for r in rows:
        # r = [source, status, started_at, rows_written, error_head]
        by_source.setdefault(r[0], []).append(r)

    import datetime as _dt

    def parse(ts: str) -> _dt.datetime | None:
        try:
            return _dt.datetime.fromisoformat(ts[:19].replace(" ", "T"))
        except (ValueError, TypeError):
            return None

    now = parse(as_of)
    items: list[dict] = []
    counters = {"sources": 0, "no_success": 0, "fail_streak": 0, "zero_output": 0, "stale_success": 0}
    for source in sorted(by_source):
        runs = by_source[source]  # 已按 started_at DESC
        last = runs[0]
        streak = 0
        for r in runs:  # 从最近往回数连续失败
            if r[1] == "success":
                break
            streak += 1
        successes = [r for r in runs if r[1] == "success"]
        last_ok = successes[0] if successes else None
        flags: list[str] = []
        if last_ok is None:
            flags.append("no_success")
        else:
            try:
                if int(last_ok[3]) == 0:
                    flags.append("zero_output")
            except (ValueError, IndexError):
                pass
            ok_dt = parse(last_ok[2])
            if ok_dt and now and (now - ok_dt).total_seconds() > stale_hours * 3600:
                flags.append("stale_success")
        if streak >= fail_streak_threshold:
            flags.append("fail_streak")
        item = {
            "source": source,
            "last_run_at": last[2],
            "last_status": last[1],
            "last_success_at": last_ok[2] if last_ok else None,
            "fail_streak": streak,
            "last_success_rows": int(last_ok[3]) if last_ok and last_ok[3].lstrip("-").isdigit() else None,
            "error_head": scrub(last[4]) if last[4] else None,
            "flags": flags,
        }
        items.append(item)
        counters["sources"] += 1
        for f in flags:
            counters[f] += 1
    return items, counters


def collect(
    *,
    lookback_hours: int = 720,
    fail_streak_threshold: int = 3,
    stale_hours: int = 48,
    dsn_env: str = "FD_CENTRAL_PG_DSN",
    psql: Callable[..., list[list[str]]] | None = None,
) -> dict:
    """纯函数核心：fetcher 可注入（测试 fake），生产走 io_.run_psql。"""
    as_of = utc_now_iso()
    errors: dict[str, ChannelError] = {}
    rows: list[list[str]] = []
    runner = psql or run_psql
    try:
        rows = runner(dsn_env, SQL % int(lookback_hours), channel=CHANNEL)
    except ChannelError as e:
        errors[e.channel] = e
    if errors:
        items, counters = [], {"sources": 0, "note": "channel failed — no data claimed"}
    else:
        items, counters = _aggregate(rows, fail_streak_threshold=fail_streak_threshold, stale_hours=stale_hours, as_of=as_of)
    return envelope(
        "crawl_summary",
        items,
        {**counters, "fail_streak_threshold": fail_streak_threshold, "stale_hours": stale_hours, "lookback_hours": lookback_hours},
        channels=[{"channel": CHANNEL, "status": "ok"}] if not errors else [{"channel": e.channel, "status": "failed", "reason": e.reason, "detail": scrub(e.detail)} for e in errors.values()],
        as_of=as_of,
    )


def register(mcp) -> None:
    @mcp.tool(name="crawl_summary", description="每爬取源最近成功/连续失败/零产出/失速标记（只读，口径=crawl-run-telemetry）")
    def _tool(lookback_hours: int = 720, fail_streak_threshold: int = 3, stale_hours: int = 48) -> dict:
        return collect(lookback_hours=lookback_hours, fail_streak_threshold=fail_streak_threshold, stale_hours=stale_hours)
