"""`scan_unmapped_series.py` 的判据回归（2026-11-14 立）。

它是只读诊断脚本，但「频率推断」这一步写错了会**把真实可用的月频序列报成断档**，
而那正是决定「哪个源值得补映射」的那一步——所以给这一步钉用例。

真实踩坑：`chinamoney-lpr` 的期值是 `2022-09-20` / `2022-10-20` 这种完整日期，
按字符串形状只能判成「日频」，于是 30 天的正常月间隔被当成断档。修法是拿**间隔的
中位数**推频率，不再依赖期字符串的形状。
"""
import types
import importlib.util
from datetime import date
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "scan_unmapped_series.py"


@pytest.fixture(scope="module")
def mod():
    import sys
    sys.modules.setdefault("psycopg2", types.ModuleType("psycopg2"))
    spec = importlib.util.spec_from_file_location("scan_unmapped_series", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _monthly_20th(start=(2022, 9), n=50):
    y, mo = start
    out = []
    for _ in range(n):
        out.append(date(y, mo, 20).toordinal())
        mo += 1
        if mo == 13:
            y, mo = y + 1, 1
    return out


def test_monthly_on_a_fixed_day_is_not_a_gap(mod):
    freq, ok = mod.frequency("day", _monthly_20th())
    assert (freq, ok) == ("month", True)


def test_daily_with_weekends_is_continuous(mod):
    base = date(2026, 8, 17).toordinal()
    days = sorted({base + i for i in range(60)} - {base + 3, base + 9, base + 30})
    assert mod.frequency("day", days) == ("day", True)


def test_real_hole_is_reported(mod):
    base = date(2026, 8, 17).toordinal()
    days = [base + i for i in range(30)] + [base + 430 + i for i in range(30)]
    freq, ok = mod.frequency("day", days)
    assert ok is False


def test_yearly_series(mod):
    ords = [date(y, 1, 1).toordinal() for y in range(2000, 2024)]
    assert mod.frequency("year", ords) == ("year", True)


def test_ordinals_share_one_scale(mod):
    """年/季/月/日一律折成**天序数**：混量纲会把年频间隔 12 当成「月频」。"""
    assert mod.ordinal("year", "2026") == date(2026, 1, 1).toordinal()
    assert mod.ordinal("month", "2026-10") == date(2026, 10, 1).toordinal()
    assert mod.ordinal("quarter", "2026Q3") == date(2026, 7, 1).toordinal()


def test_period_shapes(mod):
    assert mod.period_kind("2026") == "year"
    assert mod.period_kind("2026-10") == "month"
    assert mod.period_kind("2026-10-01") == "day"
    assert mod.period_kind("2026Q3") == "quarter"
    assert mod.period_kind("2026-M10") == "month"
    assert mod.period_kind("not a date") is None


def test_profile_finds_indicator_series(mod):
    """一个最小 payload 集：2 个指标 × 30 个月 ⇒ 认得出 30 期、并选中指标维度。"""
    payloads = []
    y, mo = 2024, 5
    for _ in range(30):
        for name in ("CPI 当月(全国)", "PPI 当月(全国)"):
            payloads.append({"indicator_name": name, "period": f"{y}-{mo:02d}",
                             "indicator_value": "100.7"})
        mo += 1
        if mo == 13:
            y, mo = y + 1, 1
    prof = mod.profile(payloads)
    assert prof is not None
    assert prof["periods"] == 30
    assert prof["passes_gate"] is True
    assert prof["dim_key"] == "indicator_name"
    assert prof["period_key"] == "period"
    assert prof["value_key"] == "indicator_value"


def test_profile_gives_up_on_non_series_payloads(mod):
    """文档/榜单类 payload（没有期字段）必须返回 None，而不是硬凑一条序列。"""
    assert mod.profile([{"title": "公告", "url": "https://x/1", "amount": "12"}]) is None
