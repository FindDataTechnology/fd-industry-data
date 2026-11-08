"""无驱动环境下的只读 PG 连接（psql 子进程兜底）。

**为什么需要**：定时告警跑在 self-hosted runner 上，那里既没有 ``psycopg`` 也没有
``psycopg2``（2026-11-08 实测 `ModuleNotFoundError`）。健康巡检与花卉线早就为同一原因
走 ``psql`` 子进程（`health/inspector.py`：「纯 stdlib；中央库读取经 psql 子进程」），
农业线照此补齐。

**为什么不直接抄第四份**：`flower_alerts.py` 里已有一份等价实现，但它是脚本内部类。
本模块把那份实现提出来共用；`flower_alerts.py` 保持原样（线上在跑，不在本次改动范围），
后续可择机切换到本模块。

契约：只提供 ``cursor().execute()/fetchall()/fetchone()``，行为对齐 DB-API 的最小面。
返回值一律是**字符串或 None**（psql 文本输出），调用方需要数值时自行转换——
这与 psycopg 会把 numeric 解成 Decimal 不同，是本模块**已知的语义差异**，
因此只在「无驱动」这条回退路径上使用。
"""
from __future__ import annotations

import os
import subprocess  # noqa: S404 - psql 兜底通道
import sys
from datetime import date, datetime


def sql_literal(value) -> str:
    """Python 值 → SQL 字面量（psql 模式用；参数都是常量或库内代码，仍严格转义）。"""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (datetime, date)):
        return "'" + value.isoformat() + "'"
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        if not items:
            return "ARRAY[]::text[]"
        return "ARRAY[" + ", ".join(sql_literal(v) for v in items) + "]"
    return "'" + str(value).replace("'", "''") + "'"


class PsqlCursor:
    """经 ``psql`` 子进程执行查询的游标（纯 stdlib）。"""

    def __init__(self, dsn: str, binary: str = "psql") -> None:
        self.dsn = dsn
        self.binary = binary
        self._rows: list[tuple] = []

    def execute(self, sql: str, params=None) -> None:
        params = tuple(params or ())
        parts = " ".join(sql.split()).split("%s")
        if len(parts) - 1 != len(params):
            raise RuntimeError(
                f"SQL 占位符 {len(parts) - 1} 个、参数 {len(params)} 个，不匹配")
        query = parts[0]
        for value, tail in zip(params, parts[1:]):
            query += sql_literal(value) + tail
        proc = subprocess.run(
            [self.binary, self.dsn, "-A", "-F", "\t", "-t", "-q",
             "-v", "ON_ERROR_STOP=1", "-c", query],
            capture_output=True, text=True, timeout=180,
        )
        if proc.returncode != 0:
            err = proc.stderr.strip()
            if "permission denied" in err.lower():
                print(f"ERROR: psql 权限不足：{err[:300]}", file=sys.stderr)
                raise SystemExit(3)  # 环境不满足（缺 SELECT），不是脚本崩溃
            raise RuntimeError(f"psql failed ({proc.returncode}): {err[:400]}")
        self._rows = [tuple(line.split("\t"))
                      for line in proc.stdout.splitlines() if line]

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self) -> None:
        self._rows = []


class PsqlConn:
    """无驱动环境下 ``connect()`` 返回的连接壳（只提供 cursor/close）。"""

    def __init__(self, dsn: str, binary: str = "psql") -> None:
        self._cursor = PsqlCursor(dsn, binary)

    def cursor(self):
        return self._cursor

    def close(self) -> None:
        self._cursor.close()


def connect_readonly(dsn: str | None, *, force_env: str = "FD_ALERTS_FORCE_PSQL"):
    """只读连接：psycopg3 → psycopg2 → psql 子进程。

    ``force_env`` 置 1 时强制走 psql 路径（测试钩子，用来在**有驱动**的机器上
    验证无驱动路径，避免"只在 CI 才炸"）。
    """
    value = dsn or os.environ.get("FD_OPEN_DATA_DSN", "") \
        or os.environ.get("FD_CENTRAL_PG_DSN", "")
    if not value:
        print("ERROR: 需要 FD_OPEN_DATA_DSN 或 FD_CENTRAL_PG_DSN", file=sys.stderr)
        raise SystemExit(3)

    if os.environ.get(force_env) == "1":
        return PsqlConn(value)
    try:
        import psycopg  # noqa: PLC0415

        conn = psycopg.connect(value, connect_timeout=40)
        conn.read_only = True
        return conn
    except ImportError:
        pass
    try:
        import psycopg2  # noqa: PLC0415

        conn = psycopg2.connect(value, connect_timeout=40)
        conn.set_session(readonly=True, autocommit=True)
        return conn
    except ImportError:
        return PsqlConn(value)
