#!/usr/bin/env python3
"""语义回填的公共件 —— 被 `extract_rates.py`／`extract_ncd.py`／`extract_lpr.py`／
`extract_usgs_nwis.py`／`extract_nasa_power.py` 共用（2026-11-14 抽出）。

**为什么值得抽**：这五个脚本原本各自复制了 ~120 行一模一样的辅助件（`num`/`fmt`/
`obs_month_of`/`gap_note`/`latest_run_items`/`ensure_concepts`/写库），第五份之后
任何一处契约变更（比如单位绑定参数那个 `%` 的坑）都要改五遍，而漏改一处只有一个源
在轮次里报错——那种错在「其它四个都绿」的背景里几乎看不出来。

**契约（与 `extract_batch1/2` 逐条相同，改动要全体一起动）**：
- additive：`ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING`；不插值不补零；
  同 (series, period) 取 `scraped_at` 最新；写 `obs_month` 治理桶。
- **单位/常量一律走绑定参数**：SQL 字面量里的裸 `'%'` 会被 psycopg2 当占位符解析，
  参数元组少一位 ⇒ `IndexError: tuple index out of range`，而单测里 psycopg2 是替身
  模块、永远测不出这一层（2026-11-14 上产即崩过）。
- **snapshot 形状的源只读最近一次成功运行**（`latest_run_items`）：历史并集里可能留着
  早期窗口不一致造成的洞，洞不会自己长回来 ⇒ 按并集判连续会把序列永久按住。
"""

from __future__ import annotations

import re
from datetime import date

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_dsn(s: str) -> str:
    """剥掉 SQLAlchemy 风格的 `+psycopg2` 后缀（libpq 不认）。"""
    return re.sub(r"^(postgres(?:ql)?)\+[a-z0-9_]+://", r"\1://", (s or "").strip())


def num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("%", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fmt(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s or "0"


def obs_month_of(d: str | None) -> str | None:
    """日频源只认完整日期；`2026-11` 这种月值不给桶（granularity 是 day）。"""
    if not d or not _DAY.match(d):
        return None
    return d[:7]


def gap_note(dates: list[str], max_days: int) -> str | None:
    """相邻两期不得超过 `max_days` 天。

    **必须取最大间隔，不是最后一个**：洞通常出现在中间。日频源给 5（周末 + 调休）；
    月频/混合节奏给 45（实测月频报价遇周末顺延最大 35 天）。
    """
    worst, where = 0, None
    for a, b in zip(dates, dates[1:]):
        try:
            span = (date.fromisoformat(b) - date.fromisoformat(a)).days
        except ValueError:
            return f"期值不是 YYYY-MM-DD：{a!r}/{b!r}"
        if span > worst:
            worst, where = span, (a, b)
    if worst > max_days:
        return f"{where[0]} 与 {where[1]} 相隔 {worst} 天（>{max_days}）"
    return None


def latest_run_items(cur, source: str) -> tuple[int | None, list[dict]]:
    """**只取最近一次成功运行**的 payload，不取历史并集（见模块 docstring）。"""
    cur.execute("SELECT id FROM crawl_runs WHERE source = %s AND status = 'success'"
                " ORDER BY started_at DESC LIMIT 1", (source,))
    row = cur.fetchone()
    if not row:
        return None, []
    rid = row[0]
    cur.execute("SELECT payload FROM crawl_items WHERE run_id = %s ORDER BY idx", (rid,))
    return rid, [r[0] for r in cur.fetchall()]


def ensure_concepts(cur, series: dict, *, source: str, category: str = "利率",
                    unit: str = "%", measure: str = "value", frequency: str = "daily",
                    entity_type: str = "country") -> dict[str, int]:
    """按 (code, name_zh, name_en) 建概念并返回 code -> id。

    `series` 的 value 可以是 `(code, zh, en)` 或带更多尾巴的元组（取前三个）。
    册内同 code 多行 ⇒ FATAL（`setdefault` 取任意一行会让错配静默通过）。
    """
    codes = []
    for v in series.values():
        code, zh, en = v[0], v[1], v[2]
        codes.append(code)
        cur.execute(
            "INSERT INTO concepts (code, name_en, name_zh, category, unit, measure, frequency,"
            " entity_type, source, verified, deprecated, concept_code)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,false,false,NULL)"
            " ON CONFLICT ON CONSTRAINT uq_concept_identity DO NOTHING",
            (code, en, zh, category, unit, measure, frequency, entity_type, source))
    ids: dict[str, int] = {}
    cur.execute("SELECT id, code FROM concepts WHERE deprecated = false AND code = ANY(%s)",
                (codes,))
    for cid, code in cur.fetchall():
        if code in ids:
            raise SystemExit(f"FATAL: concept {code} 册内多行，拒绝猜")
        ids[code] = cid
    missing = sorted(set(codes) - set(ids))
    if missing:
        raise SystemExit(f"FATAL: 解析不到 concept: {missing}")
    return ids


def write_rows(conn, rows: list[tuple], *, ids: dict[str, int], source: str,
               unit: str, granularity: str = "day",
               entity_type: str = "country", batch: int = 1000) -> tuple[int, int]:
    """把 `(code, entity_id, date, value)` 写成观测，返回 (before, after)。

    **entity_id 每行自带**，不是全局一个：`usgs-nwis` 是站点、`nasa-power` 是点位、
    `shibor` 是国家 —— 三种粒度，硬塞一个全局 entity 会把后两种写成 entity_id=0。

    **entity_type / unit / granularity 全是绑定参数**，不进 SQL 字面量（见模块 docstring）。
    行数上万时必须批量：逐条 `execute()` 的往返会把作业的 `activeDeadlineSeconds` 顶爆
    （uk-hpi 实测 1.9 万行 6 分钟还没提交）。
    """
    from psycopg2.extras import execute_values
    with conn:
        with conn.cursor() as c2:
            c2.execute("SELECT count(*) FROM semantic_observations")
            before = c2.fetchone()[0]
            payload = [(ids[c], entity_type, eid, d, granularity, v, unit, source,
                        obs_month_of(d) if granularity == "day" else None)
                       for (c, eid, d, v) in rows]
            for i in range(0, len(payload), batch):
                execute_values(
                    c2,
                    "INSERT INTO semantic_observations"
                    " (concept_id, entity_type, entity_id, date, granularity,"
                    "  value, unit, source_used, fetched_at, obs_month) VALUES %s"
                    " ON CONFLICT ON CONSTRAINT uq_sem_obs DO NOTHING",
                    payload[i:i + batch],
                    template="(%s,%s,%s,%s,%s,%s,%s,%s,now(),%s)")
            c2.execute("SELECT count(*) FROM semantic_observations")
            return before, c2.fetchone()[0]
