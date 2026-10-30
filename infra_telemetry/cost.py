"""⑤ fleet_cost — usage ledger 逐集群逐岗位 token 用量与配额消耗只读汇总
（spec: infra-telemetry-surface R2 第⑤类；口径 = design D5 open question）。

部署配置说明（表名/列位 = 运维配置，本模块不写死、不猜表名）：
- SQL 全文取 env ``FD_TELEMETRY_COST_SQL``；未设即诚实失败
  ``ChannelError(channel="usage_ledger", reason="not_configured",
  detail="set FD_TELEMETRY_COST_SQL")``——绝不臆造 usage ledger 的表名。
- DSN env：``FD_TELEMETRY_COST_DSN`` 优先，未设则回落 ``FD_CENTRAL_PG_DSN``。

SQL 返回列契约（TSV 四列，``-At -F '\\t'`` 路径同 io_.run_psql）：
    fleet <TAB> post <TAB> tokens <TAB> cost_usd
- fleet   集群/舰队逻辑名（string）
- post    岗位名；空串或 NULL → 归 "other"（spec：无岗位归因落 other，不丢弃）
- tokens  整数 token 用量；空/NULL/非数字 → 视作 0
- cost_usd 浮点美元成本；空/NULL/非数字 → 视作 0

聚合口径：同 fleet+post 求和；按 fleet（再 post）排序输出。
window_hours（默认 168=7 日）：操作者 SQL 已含窗口过滤时，本参数仅入 summary
标注（不假装能改写别人的 SQL）；未含时由操作者自行在 SQL 里过滤。两种配置皆可。

items: {fleet, post, tokens, cost_usd}
summary: {fleets, total_tokens, total_cost_usd, other_bucket_tokens, window_hours}

失败态照 crawl_summary：items=[]、summary.note、ok=False、reason 正确
（env 缺失/连不上/认证失败归因已在 io_.run_psql 内完成）。纯 stdlib。
"""
from __future__ import annotations

import os
from typing import Callable

from .envelope import ChannelError, envelope, scrub, utc_now_iso
from .io_ import run_psql

CHANNEL = "usage_ledger"
OTHER_POST = "other"
SQL_ENV = "FD_TELEMETRY_COST_SQL"
DSN_ENV = "FD_TELEMETRY_COST_DSN"
FALLBACK_DSN_ENV = "FD_CENTRAL_PG_DSN"


def _to_int(value: str) -> int:
    """契约外脏数据容错：空/NULL/非数字 → 0。"""
    s = (value or "").strip()
    if not s or s.upper() == "NULL":
        return 0
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return 0


def _to_float(value: str) -> float:
    s = (value or "").strip()
    if not s or s.upper() == "NULL":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _aggregate(rows: list[list[str]]) -> tuple[list[dict], dict]:
    buckets: dict[tuple[str, str], dict] = {}
    for r in rows:
        # r = [fleet, post, tokens, cost_usd]；列数不足按空串补齐，不崩。
        cols = list(r[:4])
        cols += [""] * (4 - len(cols))
        fleet, post, tokens_raw, cost_raw = cols
        fleet = (fleet or "").strip()
        post = (post or "").strip()
        if not post or post.upper() == "NULL":
            post = OTHER_POST
        b = buckets.setdefault((fleet, post), {"tokens": 0, "cost_usd": 0.0})
        b["tokens"] += _to_int(tokens_raw)
        b["cost_usd"] += _to_float(cost_raw)
    items = [
        {
            "fleet": fleet,
            "post": post,
            "tokens": b["tokens"],
            "cost_usd": round(b["cost_usd"], 6),
        }
        for (fleet, post), b in sorted(buckets.items())
    ]
    summary = {
        "fleets": len({fleet for fleet, _ in buckets}),
        "total_tokens": sum(b["tokens"] for b in buckets.values()),
        "total_cost_usd": round(sum(b["cost_usd"] for b in buckets.values()), 6),
        "other_bucket_tokens": sum(
            b["tokens"] for (_, post), b in buckets.items() if post == OTHER_POST
        ),
    }
    return items, summary


def collect(
    *,
    window_hours: int = 168,
    psql: Callable[..., list[list[str]]] | None = None,
) -> dict:
    """纯函数核心：fetcher 可注入（测试 fake），生产走 io_.run_psql。"""
    as_of = utc_now_iso()
    errors: dict[str, ChannelError] = {}
    rows: list[list[str]] = []
    runner = psql or run_psql
    sql = os.environ.get(SQL_ENV, "")
    if not sql.strip():
        # 诚实失败：不猜表名。
        err = ChannelError(CHANNEL, "not_configured", f"set {SQL_ENV}")
        errors[CHANNEL] = err
    else:
        dsn_env = DSN_ENV if os.environ.get(DSN_ENV) else FALLBACK_DSN_ENV
        try:
            rows = runner(dsn_env, sql, channel=CHANNEL)
        except ChannelError as e:
            errors[e.channel] = e
    if errors:
        items, counters = [], {"fleets": 0, "total_tokens": 0, "total_cost_usd": 0.0, "other_bucket_tokens": 0, "note": "channel failed — no data claimed"}
    else:
        items, counters = _aggregate(rows)
    return envelope(
        "fleet_cost",
        items,
        {**counters, "window_hours": window_hours},
        channels=[{"channel": CHANNEL, "status": "ok"}] if not errors else [{"channel": e.channel, "status": "failed", "reason": e.reason, "detail": scrub(e.detail)} for e in errors.values()],
        as_of=as_of,
    )


def register(mcp) -> None:
    @mcp.tool(name="fleet_cost", description="逐集群逐岗位 token 用量与配额消耗汇总（只读，SQL 全文=env FD_TELEMETRY_COST_SQL，无岗位归因落 other）")
    def _tool(window_hours: int = 168) -> dict:
        return collect(window_hours=window_hours)
