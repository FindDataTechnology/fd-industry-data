"""⑤ fleet_cost 通道单测（fake psql 注入，不依赖真库/真 usage ledger）。

覆盖 spec 场景（infra-telemetry-surface R2 第⑤类 / R3 失败归因 / D5 口径）：
- 聚合正确：同 fleet+post 合并；空 post / NULL post 归 other 不丢弃；排序稳定
- 拔通道：FD_TELEMETRY_COST_SQL 未设 → not_configured（诚实失败，不猜表名）；
  runner 抛 timeout / unauthorized → ok=False + reason 正确
- 新鲜度：as_of 在场
- 容错：契约外脏行（cost_usd 非数字、列数不足）归 0 不崩
- DSN 优先级：FD_TELEMETRY_COST_DSN 优先，回落 FD_CENTRAL_PG_DSN
"""
from __future__ import annotations

from infra_telemetry import cost
from infra_telemetry.envelope import ChannelError

FAKE_SQL = "SELECT fleet, post, tokens, cost_usd FROM whatever.usage_ledger"


def fake_psql_factory(rows, calls=None):
    def runner(dsn_env, sql, *, channel):
        if calls is not None:
            calls.append({"dsn_env": dsn_env, "sql": sql, "channel": channel})
        return rows

    return runner


def configure_env(monkeypatch):
    monkeypatch.setenv(cost.SQL_ENV, FAKE_SQL)
    monkeypatch.setenv(cost.DSN_ENV, "postgres://x@h/db")


# ── 1. 聚合正确 ──────────────────────────────────────────────────────────────

AGG_ROWS = [
    ["fleetA", "post1", "100", "1.0"],
    ["fleetA", "post1", "100", "1.0"],  # 同键两次 → 求和
    ["fleetA", "post2", "20", "0.5"],
    ["fleetA", "", "13", "0.25"],  # 空 post → other
    ["fleetB", "post1", "40", "0.75"],
]


def test_aggregate_merges_and_buckets_other(monkeypatch):
    configure_env(monkeypatch)
    out = cost.collect(psql=fake_psql_factory(AGG_ROWS))
    assert out["ok"] is True
    assert out["tool"] == "fleet_cost"
    items = out["items"]
    # fleet 分组排序，fleet 内 post 稳定排序（other < post1 < post2）
    assert [(i["fleet"], i["post"]) for i in items] == [
        ("fleetA", "other"),
        ("fleetA", "post1"),
        ("fleetA", "post2"),
        ("fleetB", "post1"),
    ]
    by = {(i["fleet"], i["post"]): i for i in items}
    assert by[("fleetA", "post1")]["tokens"] == 200
    assert by[("fleetA", "post1")]["cost_usd"] == 2.0
    assert by[("fleetA", "other")]["tokens"] == 13
    s = out["summary"]
    assert s["fleets"] == 2
    assert s["total_tokens"] == 273
    assert s["total_cost_usd"] == 3.5
    assert s["other_bucket_tokens"] == 13
    assert s["window_hours"] == 168


def test_null_post_goes_to_other(monkeypatch):
    configure_env(monkeypatch)
    rows = [["fleetA", "NULL", "7", "0.1"]]
    out = cost.collect(psql=fake_psql_factory(rows))
    assert out["items"] == [{"fleet": "fleetA", "post": "other", "tokens": 7, "cost_usd": 0.1}]
    assert out["summary"]["other_bucket_tokens"] == 7


# ── 2. 拔通道：诚实失败，禁假绿 ──────────────────────────────────────────────

def test_sql_env_unset_not_configured(monkeypatch):
    monkeypatch.delenv(cost.SQL_ENV, raising=False)
    calls = []
    out = cost.collect(psql=fake_psql_factory([], calls=calls))
    assert calls == []  # 未设 SQL 绝不发起查询、不猜表名
    assert out["ok"] is False
    assert out["items"] == []
    assert out["channels"] == [
        {"channel": "usage_ledger", "status": "failed", "reason": "not_configured", "detail": "set FD_TELEMETRY_COST_SQL"}
    ]
    assert out["summary"]["note"] == "channel failed — no data claimed"
    assert out["summary"]["fleets"] == 0


def test_channel_timeout_reason(monkeypatch):
    configure_env(monkeypatch)

    def runner(dsn_env, sql, *, channel):
        raise ChannelError(channel, "timeout", "psql exceeded 30s")

    out = cost.collect(psql=runner)
    assert out["ok"] is False
    assert out["items"] == []
    assert out["channels"][0]["reason"] == "timeout"
    assert out["summary"]["note"] == "channel failed — no data claimed"


def test_channel_unauthorized_reason(monkeypatch):
    configure_env(monkeypatch)

    def runner(dsn_env, sql, *, channel):
        raise ChannelError(channel, "unauthorized", "FATAL: password=hunter2 dsn=postgres://u:pw@h/db")

    out = cost.collect(psql=runner)
    assert out["ok"] is False
    assert out["channels"][0]["reason"] == "unauthorized"
    # detail 出信封前过 scrub（R4 零凭据）：凭据值不出现
    assert "hunter2" not in out["channels"][0]["detail"]
    assert "pw@h" not in out["channels"][0]["detail"]
    assert "[REDACTED]" in out["channels"][0]["detail"]


# ── 3. 新鲜度 + 脏数据容错 + DSN 优先级 ──────────────────────────────────────

def test_as_of_present(monkeypatch):
    configure_env(monkeypatch)
    out = cost.collect(psql=fake_psql_factory(AGG_ROWS))
    assert out["as_of"].endswith("+00:00") and "T" in out["as_of"]


def test_dirty_rows_tolerated(monkeypatch):
    configure_env(monkeypatch)
    rows = [
        ["fleetC", "post9", "abc", "not-a-number"],  # 非数字 → 归 0
        ["fleetD"],  # 列数不足 → 补齐不崩，post 空 → other
    ]
    out = cost.collect(psql=fake_psql_factory(rows))
    assert out["ok"] is True
    by = {(i["fleet"], i["post"]): i for i in out["items"]}
    assert by[("fleetC", "post9")] == {"fleet": "fleetC", "post": "post9", "tokens": 0, "cost_usd": 0.0}
    assert by[("fleetD", "other")]["tokens"] == 0


def test_dsn_env_priority(monkeypatch):
    monkeypatch.setenv(cost.SQL_ENV, FAKE_SQL)
    monkeypatch.setenv(cost.DSN_ENV, "postgres://a@h/usage")
    monkeypatch.setenv(cost.FALLBACK_DSN_ENV, "postgres://b@h/central")
    calls = []
    cost.collect(psql=fake_psql_factory([], calls=calls))
    assert calls[0]["dsn_env"] == cost.DSN_ENV
    # 回落：仅中央库 DSN 在场
    monkeypatch.delenv(cost.DSN_ENV)
    calls = []
    cost.collect(psql=fake_psql_factory([], calls=calls))
    assert calls[0]["dsn_env"] == cost.FALLBACK_DSN_ENV
    # SQL 原文透传（本模块不改写操作者 SQL）
    assert calls[0]["sql"] == FAKE_SQL


def test_register_tool_name():
    registered = {}

    class FakeMCP:
        def tool(self, name=None, description=None):
            def deco(fn):
                registered[name] = fn
                return fn

            return deco

    cost.register(FakeMCP())
    assert list(registered) == ["fleet_cost"]


# ── sqlite 后端（fleet usage 真源=platform hostPath fleets.db）────────────────

def test_sqlite_backend_reads_and_aggregates(tmp_path, monkeypatch):
    import sqlite3
    from infra_telemetry import cost

    db = tmp_path / "fleets.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE fleets (id TEXT, name TEXT);
        CREATE TABLE fleet_members (id TEXT, title TEXT);
        CREATE TABLE member_turn_events (fleet_id TEXT, member_id TEXT, usage_input INT, usage_output INT, started_at INT);
        INSERT INTO fleets VALUES ('f1','工厂集群'),('f2','别的舰');
        INSERT INTO fleet_members VALUES ('m1','巡检岗'),('m2',NULL);
        INSERT INTO member_turn_events VALUES ('f1','m1',100,50,1793400000000),('f1','m2',10,0,1793400000000),('f2','m1',7,7,1793400000000);
        """
    )
    con.commit(); con.close()
    monkeypatch.setenv("FD_TELEMETRY_COST_BACKEND", "sqlite")
    monkeypatch.setenv("FD_TELEMETRY_COST_DB", str(db))
    monkeypatch.setenv("FD_TELEMETRY_COST_SQL",
                       "SELECT f.name, m.title, COALESCE(t.usage_input,0)+COALESCE(t.usage_output,0), 0 "
                       "FROM member_turn_events t JOIN fleets f ON f.id=t.fleet_id "
                       "LEFT JOIN fleet_members m ON m.id=t.member_id")
    out = cost.collect()
    assert out["ok"] is True and out["summary"]["backend"] == "sqlite"
    by = {(i["fleet"], i["post"]): i["tokens"] for i in out["items"]}
    assert by[("工厂集群", "巡检岗")] == 150
    assert by[("工厂集群", "other")] == 10  # NULL 岗位归 other
    assert by[("别的舰", "巡检岗")] == 14


def test_sqlite_backend_missing_db_is_honest(tmp_path, monkeypatch):
    from infra_telemetry import cost
    monkeypatch.setenv("FD_TELEMETRY_COST_BACKEND", "sqlite")
    monkeypatch.setenv("FD_TELEMETRY_COST_DB", str(tmp_path / "nope.db"))
    monkeypatch.setenv("FD_TELEMETRY_COST_SQL", "SELECT 1,1,1,1")
    out = cost.collect()
    assert out["ok"] is False and out["channels"][0]["reason"] == "upstream_unreachable"


def test_sqlite_backend_not_configured_without_db_env(monkeypatch):
    from infra_telemetry import cost
    monkeypatch.setenv("FD_TELEMETRY_COST_BACKEND", "sqlite")
    monkeypatch.delenv("FD_TELEMETRY_COST_DB", raising=False)
    monkeypatch.setenv("FD_TELEMETRY_COST_SQL", "SELECT 1,1,1,1")
    out = cost.collect()
    assert out["ok"] is False and out["channels"][0]["reason"] == "not_configured"


def test_sqlite_backend_glob_picks_newest(tmp_path, monkeypatch):
    import sqlite3, os, time
    from infra_telemetry import cost
    cellA = tmp_path / "cellAAA" / "data" / "data"; cellA.mkdir(parents=True)
    cellB = tmp_path / "cellBBB" / "data" / "data"; cellB.mkdir(parents=True)
    for d, rows in ((cellA, [(1, 1)]), (cellB, [(1000, 2000)])):
        con = sqlite3.connect(d / "fleets.db")
        con.execute("CREATE TABLE member_turn_events (usage_input INT, usage_output INT)")
        con.executemany("INSERT INTO member_turn_events VALUES (?,?)", rows)
        con.commit(); con.close()
    os.utime(cellB / "fleets.db", (time.time() + 10, time.time() + 10))  # B 更新
    monkeypatch.setenv("FD_TELEMETRY_COST_BACKEND", "sqlite")
    monkeypatch.setenv("FD_TELEMETRY_COST_DB", str(tmp_path / "cell*" / "data" / "data" / "fleets.db"))
    monkeypatch.setenv("FD_TELEMETRY_COST_SQL", "SELECT 'f','p',COALESCE(usage_input,0)+COALESCE(usage_output,0),0 FROM member_turn_events")
    out = cost.collect()
    assert out["ok"] is True
    assert out["items"] == [{"fleet": "f", "post": "p", "tokens": 3000, "cost_usd": 0.0}]
