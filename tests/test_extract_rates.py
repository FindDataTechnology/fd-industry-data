"""`extract_rates.py` 的判据回归（2026-11-14 立）。

三条判据，每条都对应一个当天真实见过的静默形态：

1. **趋势闸**：每个期限 <24 个交易日、或中间有长假断档 ⇒ 该期限整条不写。
   Shibor 当时只有 15 个交易日，靠这条把写入按住，等它自己攒够。
2. **只取最近一轮运行**：这条源是「自带完整窗口的快照」（最近 limit 行 + 首日锚行），
   不是每次追加新期的累计流。按历史并集取会撞上 2026-09-30 → 2026-10-30 的一月洞
   （早期几轮窗口不一致留下的），而那个洞**不会自己长回来** ⇒ 序列会被永久按住。
3. **只写 Shibor 八档、不写 FR/FDR**：同一个 payload 里还有回购定盘利率，量纲相同
   但语义不同；混进 `rate.shibor.*` 会让「Shibor 3M」返回回购利率，而没有任何判据会红。
"""
import importlib.util
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "extract_rates.py"


@pytest.fixture(scope="module")
def mod():
    import sys
    sys.modules.setdefault("psycopg2", types.ModuleType("psycopg2"))
    sys.path.insert(0, str(SCRIPT.parent))   # _semextract 是同目录模块
    spec = importlib.util.spec_from_file_location("extract_rates", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _payloads(days: list[str], curves: list[str], value=1.5, scraped="2026-11-14T03:00:00+00:00"):
    return [{"curve": c, "date": d, "value": value, "unit": "%",
             "source": "chinamoney-shibor", "scraped_at": scraped}
            for d in days for c in curves]


def _trading_days(n: int) -> list[str]:
    """从 2026-06-01 起取 n 个「工作日」，跳过周末（间隔 ≤3 天，过 gap_note）。"""
    from datetime import date, timedelta
    out, d = [], date(2026, 6, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


SHIBOR8 = ["ShiborO/N", "Shibor1W", "Shibor2W", "Shibor1M",
           "Shibor3M", "Shibor6M", "Shibor9M", "Shibor1Y"]


def test_writes_all_eight_tenors_when_window_is_long_enough(mod):
    days = _trading_days(28)
    data = mod.collect(_payloads(days, SHIBOR8))
    rows = mod.build_rows(data, [], skips := {})
    assert len(rows) == 28 * 8
    assert skips == {}
    codes = {c for c, _d, _v in rows}
    assert codes == {v[0] for v in mod.SERIES.values()}


def test_gate_holds_back_short_window(mod):
    """15 个交易日 ⇒ 一条都不写（Shibor 2026-11-14 的真实状态）。"""
    days = _trading_days(15)
    rows = mod.build_rows(mod.collect(_payloads(days, SHIBOR8)), [], skips := {})
    assert rows == []
    assert len(skips) == 8
    assert all("15" in v for v in skips.values())


def test_gate_rejects_long_holiday_gap(mod):
    """序列中间缺掉一整周（长假）⇒ 不写，理由点名断点。

    注意这里要缺**连续 8 个交易日**：`gap_note` 允许 5 天（周末 + 一个调休），
    只缺 1 天算出来的间隔是 4 天，判不出洞。
    """
    days = _trading_days(40)                      # 缺 8 个后仍有 32 ≥ 24，才轮得到断档判据
    broken = [d for i, d in enumerate(days) if not 12 <= i < 20]
    rows = mod.build_rows(mod.collect(_payloads(broken, SHIBOR8)), [], skips := {})
    assert rows == []
    assert any("相隔" in v for v in skips.values())


def test_fr_fdr_are_not_mapped(mod):
    """回购定盘利率不进 `rate.shibor.*`。"""
    days = _trading_days(28)
    repo = ["FR001", "FR007", "FR014", "FDR001", "FDR007", "FDR014"]
    rows = mod.build_rows(mod.collect(_payloads(days, SHIBOR8 + repo)), [], {})
    assert len(rows) == 28 * 8           # 只有八档，14 → 8
    assert all(c.startswith("rate.shibor.") for c, _d, _v in rows)
    assert set(mod.NOT_MAPPED) == {"FR001/FR007/FR014", "FDR001/FDR007/FDR014"}


def test_value_out_of_bounds_is_fatal(mod):
    """值域护栏：把 bp 当成 % 写进去（如 130 而不是 1.30）⇒ 拒绝写入。"""
    days = _trading_days(28)
    with pytest.raises(SystemExit) as ei:
        mod.build_rows(mod.collect(_payloads(days, SHIBOR8, value=130.0)), [], {})
    assert "值域" in str(ei.value)


def test_latest_scraped_at_wins(mod):
    days = _trading_days(28)
    old = _payloads(days, SHIBOR8, value=1.0, scraped="2026-11-14T03:00:00+00:00")
    new = _payloads(days, SHIBOR8, value=2.0, scraped="2026-11-14T09:00:00+00:00")
    rows = mod.build_rows(mod.collect(old + new), [], {})
    assert {v for _c, _d, v in rows} == {"2"}


def test_non_day_or_missing_value_ignored(mod):
    """月值、缺值、"---" 都不进序列（上游 FrrHis 用 "---" 表示未定盘）。"""
    days = _trading_days(28)
    junk = [{"curve": "Shibor3M", "date": "2026-11", "value": 1.2, "scraped_at": "x"},
            {"curve": "Shibor3M", "date": "2026-11-16", "value": None, "scraped_at": "x"},
            {"curve": "Shibor3M", "date": "2026-11-16", "value": "---", "scraped_at": "x"}]
    rows = mod.build_rows(mod.collect(_payloads(days, SHIBOR8) + junk), [], {})
    assert len(rows) == 28 * 8


def test_period_helpers(mod):
    assert mod.obs_month_of("2026-11-13") == "2026-11"
    assert mod.obs_month_of("2026-11") is None      # 日频源不接受月值
    assert mod.gap_note(["2026-11-13", "2026-11-16"]) is None      # 3 天，周末
    assert mod.gap_note(["2026-11-13", "2026-12-01"]) is not None  # 18 天


def test_sql_has_no_bare_percent_literal(mod):
    """SQL 字面量里不许出现裸 `'%'`。

    psycopg2 把 `%` 当占位符解析 ⇒ 参数元组少一位、抛
    `IndexError: tuple index out of range`。而单测用的是 stand-in 的 psycopg2 模块，
    **这条永远测不出来**（2026-11-14 就是这么上了一次产才发现的）。所以改成查字面量：
    单位一律走绑定参数，不写进 SQL 文本。
    """
    import inspect
    src = "\n".join(l for l in inspect.getsource(mod).splitlines()
                    if not l.lstrip().startswith("#"))
    assert "'%'" not in src, "SQL 里有裸 '%'：psycopg2 会把它当占位符，改走绑定参数"
