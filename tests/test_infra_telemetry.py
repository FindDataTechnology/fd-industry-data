"""envelope + crawl_summary 单测（fake psql 注入，不依赖真库）。

覆盖 spec 场景：
- 逐条目可见（flags/计数逐源列名）
- 通道故障不冒充健康（拔通道用例：env 未设/连不上/超时各一，ok=False+reason）
- 新鲜度可判（as_of 在场）
- 凭据脱敏（error_head 含 token 形态 → [REDACTED]）
- 口径不漂移（零产出/失速/连败计数公式）
"""
from __future__ import annotations

import pytest

from infra_telemetry import envelope as env
from infra_telemetry import crawl_summary
from infra_telemetry.envelope import ChannelError


def fake_psql_factory(rows):
    def runner(dsn_env, sql, *, channel):
        assert "crawl_runs" in sql
        return rows

    return runner


BASE_ROWS = [
    ["cninfo", "success", "2026-10-30 10:00:00", "12", ""],
    ["cninfo", "failed", "2026-10-30 11:00:00", "0", "timeout fetching https://x?token=abc123secret"],
    ["ministry", "failed", "2026-10-30 09:00:00", "0", "HTTP 500"],
    ["ministry", "failed", "2026-10-30 08:00:00", "0", "HTTP 500"],
    ["ministry", "failed", "2026-10-30 07:00:00", "0", "HTTP 500"],
    ["govempty", "success", "2026-10-30 06:00:00", "0", ""],
    # rows are fed as already DESC per source per SQL contract; emulate:
]
# crawl_summary expects started_at DESC per source. Sort helper for test clarity.
def desc(rows):
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r[0], []).append(r)
    res = []
    for k, v in out.items():
        res.extend(sorted(v, key=lambda r: r[2], reverse=True))
    return res


def test_flags_and_counts():
    as_of = "2026-10-31T00:00:00+00:00"
    rows = desc(BASE_ROWS)
    orig = crawl_summary.utc_now_iso
    crawl_summary.utc_now_iso = lambda: as_of
    try:
        out = crawl_summary.collect(psql=fake_psql_factory(rows), stale_hours=48)
    finally:
        crawl_summary.utc_now_iso = orig
    assert out["ok"] is True
    by = {i["source"]: i for i in out["items"]}
    # cninfo: last run is failed streak=1, last success 12 rows -> flags: stale_success? 10-30 10:00 vs 10-31 00:00 = 14h <48 -> no stale; fail_streak 1 <3 -> no; zero_output rows=12 -> no
    assert by["cninfo"]["fail_streak"] == 1
    assert by["cninfo"]["flags"] == []
    assert by["cninfo"]["last_status"] == "failed"
    assert by["cninfo"]["last_success_rows"] == 12
    # error_head 脱敏：token=abc123secret -> [REDACTED]
    assert "abc123secret" not in by["cninfo"]["error_head"]
    assert "[REDACTED]" in by["cninfo"]["error_head"]
    # ministry: 3 连败 -> fail_streak flag
    assert by["ministry"]["fail_streak"] == 3
    assert "fail_streak" in by["ministry"]["flags"]
    assert "last_success_at" in by["ministry"] and by["ministry"]["last_success_at"] is None
    assert "no_success" in by["ministry"]["flags"]
    # govementpty: success with rows=0 -> zero_output
    assert "zero_output" in by["govementpty"]["flags"] if "govementpty" in by else "zero_output" in by["govempty"]["flags"]
    assert out["summary"]["sources"] == 3
    assert out["as_of"] == as_of


def test_channel_failure_not_fake_green():
    def runner(dsn_env, sql, *, channel):
        raise ChannelError(channel, "not_configured", f"env {dsn_env} is unset")

    out = crawl_summary.collect(psql=runner)
    assert out["ok"] is False
    assert out["channels"] == [
        {"channel": "central_pg", "status": "failed", "reason": "not_configured", "detail": "env FD_CENTRAL_PG_DSN is unset"}
    ]
    assert out["items"] == []
    assert out["summary"]["note"] == "channel failed — no data claimed"


def test_channel_timeout_reason():
    def runner(dsn_env, sql, *, channel):
        raise ChannelError(channel, "timeout", "psql exceeded 30s")

    out = crawl_summary.collect(psql=runner)
    assert out["ok"] is False
    assert out["channels"][0]["reason"] == "timeout"


def test_scrub_patterns():
    assert "ghp_ABCDEF1234567890" not in env.scrub("token ghp_ABCDEF1234567890 seen")
    assert "[REDACTED]" in env.scrub("Bearer abcdef-1234567890")
    assert "[REDACTED]" in env.scrub("password=SuperSecret123")
    assert "[REDACTED]" in env.scrub("postgresql://u:p@host:5432/db")
    assert "[REDACTED]" in env.scrub({'error': 'password="hunter2"'})['error']
    # 合法逻辑名不被误杀
    assert env.scrub("fd-prod/cn-report") == "fd-prod/cn-report"


def test_envelope_shape():
    e = env.envelope("t", [], {"a": 1}, channels=[{"channel": "c", "status": "ok"}])
    assert set(e) == {"tool", "ok", "as_of", "channels", "summary", "items"}


# ── 多行错误正文不得让整类聚合不可用（fix-crawl-summary-multiline-error-head）──
#
# 2026-11-12 在产实证：crawl_runs.error_head 存过 Node 网络错误的多行栈，
# TSV 一换行就多出一条只有 1 列的续行，_aggregate 的 r[1] 于是 IndexError，
# 1210 行里的 12 条续行让 crawl_summary **每次调用都崩**。

def test_multiline_error_head_does_not_crash():
    as_of = "2026-10-31T00:00:00+00:00"
    rows = desc(BASE_ROWS) + [
        # 续行形态：只有 1 列（真实样本：'    at TCPConnectWrap.afterConnect ...'）
        ["    at TCPConnectWrap.afterConnect [as oncomplete] (node:net:1638:16)"],
    ]
    orig = crawl_summary.utc_now_iso
    crawl_summary.utc_now_iso = lambda: as_of
    try:
        out = crawl_summary.collect(psql=fake_psql_factory(rows), stale_hours=48)
    finally:
        crawl_summary.utc_now_iso = orig
    assert out["ok"] is True, "一条脏正文不许把整类聚合打成异常"
    assert out["summary"]["sources"] == 3, "正常行照常聚合"
    assert out["summary"]["malformed_rows"] == 1, "畸形续行要被计数，不许静默丢"


def test_short_but_usable_rows_are_padded():
    as_of = "2026-10-31T00:00:00+00:00"
    rows = [["srcx", "success", "2026-10-30 10:00:00"]]  # 3 列：缺 rows_written/error_head
    orig = crawl_summary.utc_now_iso
    crawl_summary.utc_now_iso = lambda: as_of
    try:
        out = crawl_summary.collect(psql=fake_psql_factory(rows), stale_hours=48)
    finally:
        crawl_summary.utc_now_iso = orig
    assert out["ok"] is True
    assert out["items"][0]["source"] == "srcx"
    assert out["summary"]["malformed_rows"] == 0


def test_sql_normalizes_error_head_to_single_line():
    # 防回归：源头必须归一换行，否则行结构会被正文破坏。
    # 判据写成「error_head 只允许出现在 regexp_replace(...) 里面」——不能只 grep
    # 子串（regexp_replace 内层本来就有 COALESCE(error_head,'')，窄正则会把对的判成错的）。
    sql = crawl_summary.SQL
    assert "regexp_replace" in sql
    assert sql.count("error_head") == 1, "error_head 只该在归一表达式里出现一次（多处出现=有的没被归一）"
    pos = sql.index("error_head")
    assert sql[:pos].endswith("regexp_replace(COALESCE("), (
        "error_head 不在 regexp_replace(COALESCE(...)) 内：多行正文会再次把 TSV 行拆开"
    )
    # 显式归一 CR/LF（纯字符串包含判断，别用正则——转义层数一多就写成假的判据）
    assert "[\\r\\n]+" in sql
