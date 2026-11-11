"""⑤ fleet_cost — usage ledger 逐 cell 逐集群逐岗位 token 用量与配额消耗只读汇总
（spec: infra-telemetry-surface R2 第⑤类；口径 = design D5 open question）。

部署配置说明（表名/列位 = 运维配置，本模块不写死、不猜表名）：
- SQL 全文取 env ``FD_TELEMETRY_COST_SQL``；未设即诚实失败
  ``ChannelError(channel="usage_ledger", reason="not_configured",
  detail="set FD_TELEMETRY_COST_SQL")``——绝不臆造 usage ledger 的表名。
- DSN env：``FD_TELEMETRY_COST_DSN`` 优先，未设则回落 ``FD_CENTRAL_PG_DSN``。

SQL 返回列契约（TSV，``-At -F '\\t'`` 路径同 io_.run_psql）：
    fleet <TAB> post <TAB> tokens <TAB> cost_usd [<TAB> cell]
- fleet   集群/舰队逻辑名（string）
- post    岗位名；空串或 NULL → 归 "other"（spec：无岗位归因落 other，不丢弃）
- tokens  整数 token 用量；空/NULL/非数字 → 视作 0
- cost_usd 浮点美元成本；空/NULL/非数字 → 视作 0
- cell    **可选第 5 列**：这一行属于哪个 cell。4 列时视为未知（空串），
          聚合结果与改造前逐字节一致。sqlite 后端自己知道读的是哪个库，
          由读取层追加这一列，部署 SQL 不需要改（add-cell-identity-to-fleet-cost D2）。

聚合口径：同 (cell, fleet, post) 求和；按 cell（再 fleet、post）排序输出。
            丢 cell 会静默失真——两个 cell 的同名舰队要么被合并成一行，要么只有
            mtime 最新那个 cell 的数据被当成"全集群"（design D1/D4）。
window_hours（默认 168=7 日）：操作者 SQL 已含窗口过滤时，本参数仅入 summary
标注（不假装能改写别人的 SQL）；未含时由操作者自行在 SQL 里过滤。两种配置皆可。

items: {fleet, post, tokens, cost_usd, cell}
summary: {fleets, cells, total_tokens, total_cost_usd, other_bucket_tokens,
          window_hours, cell_errors?}

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
BACKEND_ENV = "FD_TELEMETRY_COST_BACKEND"  # psql(default) | sqlite
DB_ENV = "FD_TELEMETRY_COST_DB"          # sqlite 模式：fleets.db 路径（hostPath 只读）


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


def _cell_id(db_path: str) -> str:
    """从 fleets.db 路径反推 cell 标识（cell 目录名）。

    platform 的 cell 库布局是 ``<root>/<cell>/data/data/fleets.db``，所以
    ``data/data`` 前那一段就是 cell。取不到就返回空串（未知），**不猜**。
    """
    from pathlib import PurePath

    parts = PurePath(db_path).parts
    if len(parts) >= 4 and parts[-2] == "data" and parts[-3] == "data":
        return parts[-4]
    return ""


def _aggregate(rows: list[list[str]]) -> tuple[list[dict], dict]:
    buckets: dict[tuple[str, str, str], dict] = {}
    for r in rows:
        # r = [fleet, post, tokens, cost_usd, (cell)]；列数不足按空串补齐，不崩。
        cols = list(r[:5])
        cols += [""] * (5 - len(cols))
        fleet, post, tokens_raw, cost_raw, cell = cols
        fleet = (fleet or "").strip()
        post = (post or "").strip()
        cell = (cell or "").strip()
        if not post or post.upper() == "NULL":
            post = OTHER_POST
        # 键里带 cell：同名舰队分属两个 cell 是两条事实，合并就是失真（design D1/D4）。
        b = buckets.setdefault((cell, fleet, post), {"tokens": 0, "cost_usd": 0.0})
        b["tokens"] += _to_int(tokens_raw)
        b["cost_usd"] += _to_float(cost_raw)
    items = [
        {
            "fleet": fleet,
            "post": post,
            "tokens": b["tokens"],
            "cost_usd": round(b["cost_usd"], 6),
            "cell": cell,
        }
        for (cell, fleet, post), b in sorted(buckets.items())
    ]
    summary = {
        # 口径变化（有意）：从"舰队名去重数"改为"（cell, 舰队）对数"。
        "fleets": len({(cell, fleet) for cell, fleet, _ in buckets}),
        "cells": len({cell for cell, _, _ in buckets if cell}),
        "total_tokens": sum(b["tokens"] for b in buckets.values()),
        "total_cost_usd": round(sum(b["cost_usd"] for b in buckets.values()), 6),
        "other_bucket_tokens": sum(
            b["tokens"] for (_, _, post), b in buckets.items() if post == OTHER_POST
        ),
    }
    return items, summary


def _read_one_db(sql: str, db_path: str, *, channel: str) -> list[list[str]]:
    """只读打开一个库取全部行（mode=ro，不持写锁）。"""
    import sqlite3

    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            cur = con.execute(sql)
            return [[("" if v is None else str(v)) for v in row] for row in cur.fetchall()]
        finally:
            con.close()
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        reason = "unauthorized" if ("readonly" in msg or "readonly" in msg.replace("-", "")) else "upstream_unreachable"
        if "locked" in msg:
            reason = "timeout"
        raise ChannelError(channel, reason, str(e)[:200])
    except sqlite3.Error as e:
        raise ChannelError(channel, "upstream_unreachable", str(e)[:200])


def _rows_sqlite(sql: str, *, channel: str, db_env: str = DB_ENV):
    """sqlite 后端（stdlib in-process 只读）——fleet usage 真源=platform 节点
    hostPath 上的 fleets.db（member_turn_events.usage_input/output），不在中央 PG。

    返回 ``(rows, cell_errors)``：rows 的每行追加第 5 列 = cell 标识（design D2/D3）。
    glob 形态**读全部匹配的 cell 库**（原逻辑只读 mtime 最新那一个，等于让归档 cell
    冒充全集群，见 design D1）；单个库坏掉不拖垮通道，失败清单交回调用方点名。
    """
    import glob as _glob

    raw = os.environ.get(db_env, "").strip()
    if not raw:
        raise ChannelError(channel, "not_configured", f"set {db_env}")
    if any(ch in raw for ch in "*?["):
        matches = sorted(m for m in _glob.glob(raw) if os.path.exists(m))
        if not matches:
            raise ChannelError(channel, "upstream_unreachable", f"db glob no match: {raw}")
    else:
        if not os.path.exists(raw):
            raise ChannelError(channel, "upstream_unreachable", f"db file missing: {raw}")
        matches = [raw]

    rows: list[list[str]] = []
    cell_errors: list[dict] = []
    newest_ms = 0
    for db_path in matches:
        cell = _cell_id(db_path)
        try:
            one = _read_one_db(sql, db_path, channel=channel)
        except ChannelError as e:
            # 一个库里归档/迁移而坏掉是常态；吞掉它但要点名（design D3）。
            cell_errors.append({"cell": cell or db_path, "reason": e.reason, "detail": e.detail})
            continue
        for row in one:
            rows.append(list(row) + [cell])
        # 数据新鲜度（2026-11-12 假数据事故后加）：envelope 的 as_of 是「问的时刻」，
        # 常量堆里读到的旧副本会让它看起来完全正常。这里取库内最新回合时间，
        # 与 as_of 一比就知道自己读的是不是活数据。
        try:
            import sqlite3

            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
            try:
                v = con.execute("SELECT MAX(CAST(started_at AS INTEGER)) FROM member_turn_events").fetchone()[0]
            finally:
                con.close()
            if v and int(v) > newest_ms:
                newest_ms = int(v)
        except Exception:
            pass  # 没有该表/打不开：只影响新鲜度标注，不影响数据

    if cell_errors and not rows and len(cell_errors) == len(matches):
        # 全部失败才诚实失败，不冒充空集。
        detail = "; ".join(f"{c['cell']}: {c['detail']}" for c in cell_errors)[:200]
        raise ChannelError(channel, cell_errors[0]["reason"], detail)
    data_asof = None
    if newest_ms:
        import datetime as _dt

        data_asof = _dt.datetime.fromtimestamp(newest_ms / 1000, _dt.timezone.utc).isoformat()
    return rows, cell_errors, data_asof



def collect(
    *,
    window_hours: int = 168,
    psql: Callable[..., list[list[str]]] | None = None,
    sqlite_reader: Callable[..., list[list[str]]] | None = None,
) -> dict:
    """纯函数核心：fetcher 可注入（测试 fake），生产按 BACKEND env 走 run_psql/_rows_sqlite。"""
    as_of = utc_now_iso()
    errors: dict[str, ChannelError] = {}
    rows: list[list[str]] = []
    # 逐 cell 的读取失败：不算通道失败，但要回给消费方点名（design D3）。
    cell_errors: list[dict] = []
    # 库内最新回合时刻（sqlite 后端才有）。envelope 的 as_of 是「问的时刻」，
    # 两者差得远＝读到了旧副本（2026-11-12：pod 钉在旧节点，fleet_cost 报 11-04 的数）。
    data_asof: str | None = None
    sql = os.environ.get(SQL_ENV, "")
    backend = os.environ.get(BACKEND_ENV, "psql").strip().lower() or "psql"
    if not sql.strip():
        # 诚实失败：不猜表名。
        err = ChannelError(CHANNEL, "not_configured", f"set {SQL_ENV}")
        errors[CHANNEL] = err
    elif backend == "sqlite":
        try:
            out = (sqlite_reader or _rows_sqlite)(sql, channel=CHANNEL)
            # 读取层可以返回 rows（旧契约）或 (rows, cell_errors[, data_asof])（带身份/新鲜度）。
            if isinstance(out, tuple) and len(out) >= 2:
                rows, cell_errors = list(out[0]), list(out[1] or [])
                if len(out) >= 3 and out[2]:
                    data_asof = str(out[2])
            else:
                rows = list(out or [])
        except ChannelError as e:
            errors[e.channel] = e
    else:
        dsn_env = DSN_ENV if os.environ.get(DSN_ENV) else FALLBACK_DSN_ENV
        try:
            rows = (psql or run_psql)(dsn_env, sql, channel=CHANNEL)
        except ChannelError as e:
            errors[e.channel] = e
    if errors:
        items, counters = [], {"fleets": 0, "cells": 0, "total_tokens": 0, "total_cost_usd": 0.0, "other_bucket_tokens": 0, "note": "channel failed — no data claimed"}
    else:
        items, counters = _aggregate(rows)
        if cell_errors:
            counters = {**counters, "cell_errors": cell_errors}
        if data_asof:
            counters = {**counters, "data_asof": data_asof}
    return envelope(
        "fleet_cost",
        items,
        {**counters, "window_hours": window_hours, "backend": backend},
        channels=[{"channel": CHANNEL, "status": "ok"}] if not errors else [{"channel": e.channel, "status": "failed", "reason": e.reason, "detail": scrub(e.detail)} for e in errors.values()],
        as_of=as_of,
    )


def register(mcp) -> None:
    @mcp.tool(name="fleet_cost", description="逐 cell 逐集群逐岗位 token 用量与配额消耗汇总（只读；backend=psql|sqlite 由 env 定，SQL 全文=env FD_TELEMETRY_COST_SQL，无岗位归因落 other；sqlite 后端读全部 cell 库并逐行标明 cell，单 cell 库不可读时点名到 summary.cell_errors）")
    def _tool(window_hours: int = 168) -> dict:
        return collect(window_hours=window_hours)
