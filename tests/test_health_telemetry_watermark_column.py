"""遥测 SQL 必须真的把「数据期本体」选出来（2026-11-16）。

这条用例的由来是**我自己踩的坑**：`triage` 的规则 1d（水位冻结）读 `Run.watermark`，
而默认 `telemetry_sql` 的第 7 列当时是一个**空字符串字面量**：

```sql
SELECT source, status, started_at, COALESCE(rows_written,0),
       COALESCE(error_head,''), '', '',            -- ← 第 7 列恒空
       COALESCE(metrics->>'limit',''),
       COALESCE(metrics->'watermark'->>'state','')
```

于是 `Run.watermark` 在生产上**恒为 None**：规则写了、有 9 条单测、红证也做了，却一次
都不会触发——「判据存在但人口为空」。而所有单测都是拿 `Run(...)` 直接构造的，永远
造不出这个形态，所以必须**在 SQL 文本与解析链两处**各断一条。

解析走 `load_runs_from_psql`（真子进程）：这里塞一个假的 `psql`，它只按 `-At -F \t`
的约定吐 TSV，于是被验的是真实解析路径，而不是夹具自己填字段。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fd_industry_data.health import config, inspector, triage

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def _row(source: str, status: str, hours_ago: float, *, period: str = "",
         limit: str = "", state: str = "", rows: int = 100) -> str:
    started = (NOW - timedelta(hours=hours_ago)).isoformat()
    return "\t".join([source, status, started, str(rows), "", "", period, limit, state])


def _fake_psql(tmp_path: Path, lines: list[str]) -> Path:
    """生成一个只 echo 固定 TSV 的假 psql（参数是 dsn/-At/-F/-c，全部忽略）。"""
    body = "#!/bin/sh\ncat <<'EOF'\n" + "\n".join(lines) + "\nEOF\n"
    p = tmp_path / "psql-stub"
    p.write_text(body, encoding="utf-8")
    p.chmod(0o755)
    return p


def _top_level_columns(sql: str) -> list[str]:
    """按最外层逗号切 SELECT 列表。**不能数逗号**：`COALESCE(a,'')` 里的逗号也在
    字符串里，一数就把 9 列数成 13（这条用例第一版就这么假红了）。"""
    head = sql.split("FROM")[0]
    assert head.upper().startswith("SELECT")
    body = head[len("SELECT"):]
    cols, depth, cur = [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            cols.append(cur.strip())
            cur = ""
            continue
        cur += ch
    if cur.strip():
        cols.append(cur.strip())
    return cols


def test_default_sql_selects_the_watermark_period():
    sql = config.DEFAULTS.telemetry_sql
    cols = _top_level_columns(sql)
    assert len(cols) == 9, f"解析器按固定下标取 6/7/8，列数必须是 9：{len(cols)} {cols}"
    assert "watermark'->>'period" in cols[6], (
        f"第 7 列（index 6）必须是数据期本体，现在是 {cols[6]!r}；"
        "改回空字面量会让 rule1d 永远不触发")
    assert "watermark'->>'state" in cols[8]
    assert "->>'limit'" in cols[7], f"第 8 列（index 7）应是行数上限：{cols[7]!r}"


def test_period_column_reaches_run_watermark(tmp_path):
    stub = _fake_psql(tmp_path, [
        _row("carbon-monitor", "success", 2 + 24 * i, period="2026-08-31", state="ok")
        for i in range(3)
    ])
    records = inspector.load_runs_from_psql(config.DEFAULTS, "dsn-x", binary=str(stub))
    runs = inspector.group_runs(records)["carbon-monitor"]
    assert [r.watermark for r in runs] == ["2026-08-31"] * 3, \
        "period 列必须落到 Run.watermark，否则规则没有输入"
    assert [r.watermark_state for r in runs] == ["ok"] * 3


def test_wiring_is_what_makes_rule1d_fire(tmp_path):
    """成对：同一份遥测，去掉 period 列则健康、留着则出单。

    这就是「判据存在但人口为空」与「判据真的生效」之间那一步的证据。
    """
    def classify(lines: list[str]):
        stub = _fake_psql(tmp_path, lines)
        runs = inspector.group_runs(
            inspector.load_runs_from_psql(config.DEFAULTS, "dsn-x", binary=str(stub))
        )["carbon-monitor"]
        return triage.classify("carbon-monitor", "spiders/carbon-monitor/", 24.0,
                               runs, NOW, data_period="day")

    with_period = classify([_row("carbon-monitor", "success", 2 + 24 * i,
                                 period="2026-08-31", state="ok") for i in range(3)])
    without = classify([_row("carbon-monitor", "success", 2 + 24 * i, state="ok")
                        for i in range(3)])

    assert with_period.needs_human and "rule1d" in with_period.rule_trace
    assert not without.needs_human, "没有 period 列时必须退回老行为（零回归）"
