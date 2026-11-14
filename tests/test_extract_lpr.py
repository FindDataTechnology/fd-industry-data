"""`extract_lpr.py` 的判据回归（2026-11-14 立）。

四条判据，每条都对应一个会静默出错的形态：

1. **双节奏连续性**：这条源 2019-08 前日频、之后月频。用日频的 5 天阈值会把整条月频
   序列判成"不连续"（23 个点的月频序列相邻间隔 28–35 天）⇒ 阈值必须是 45 天，且
   **要能拒掉真正的洞**（缺两个月 = 60+ 天）。
2. **两个时代都要写**：改革前的日频 1Y 与改革后的月度 1Y+5Y 是同一条 LPR 的历史，
   截掉前段等于删真实数据。
3. **5Y 起点晚**：`LPR5Y` 只在 2019-08-20 之后存在；判据按各自曲线独立计期数，
   不能被 1Y 的 713 期掩盖（同一个 payload，两条曲线各判各的）。
4. **SQL 里不许有裸 `'%'`**：psycopg2 会当占位符（参数少一位 ⇒ IndexError），
   而单测里的 psycopg2 是替身模块，永远测不出运行期这一层。
"""
import importlib.util
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "extract_lpr.py"


@pytest.fixture(scope="module")
def mod():
    import sys
    sys.modules.setdefault("psycopg2", types.ModuleType("psycopg2"))
    sys.path.insert(0, str(SCRIPT.parent))   # _semextract 是同目录模块
    spec = importlib.util.spec_from_file_location("extract_lpr", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _monthly(n: int, start=(2020, 1, 20)) -> list[str]:
    """每月的 20 日（模拟 2019-08 改革后的节奏）。"""
    out, y, mo = [], start[0], start[1]
    for _ in range(n):
        out.append(f"{y}-{mo:02d}-20")
        y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    return out


def _payloads(days, curves, value=3.5, scraped="2026-11-14T10:57:43+00:00"):
    return [{"curve": c, "date": d, "value": value, "unit": "%",
             "source": "chinamoney-lpr", "scraped_at": scraped}
            for d in days for c in curves]


def test_monthly_cadence_passes_the_45_day_threshold(mod):
    """月频 24 期（相邻 28–31 天）必须过闸——这是本源的主形态。

    同批的 `LPR5Y` 在这份 payload 里根本不存在（2019-08-20 之前它本就不存在），
    所以 `skips` 里会有一条「本轮 payload 里没有这条曲线」，那是**事实**不是闸的判定，
    与「仅 N 期」必须分开写。
    """
    days = _monthly(24)
    rows = mod.build_rows(mod.collect(_payloads(days, ["LPR1Y"]))[0], [], skips := {})
    assert len(rows) == 24
    assert set(skips) == {"rate.lpr.5y"}
    assert "没有这条曲线" in skips["rate.lpr.5y"] and "让闸" not in skips["rate.lpr.5y"]
    assert {c for c, _d, _v in rows} == {"rate.lpr.1y"}


def test_daily_era_and_monthly_era_both_written(mod):
    """改革前日频 + 改革后月频接在一起仍是一条连续序列（跳变是真实制度切换，不拦）。

    真实历史就是连续的：日频一直排到 2019-08-19，2019-08-20 起改月报。构造时
    必须让两段**时间上相接**（间隔 ≤45 天），否则测的是「洞」而不是「双节奏」。
    """
    daily = [f"2019-08-{d:02d}" for d in range(1, 20)]      # 2019-08-01..08-19
    days = daily + _monthly(24, start=(2019, 9, 20))
    rows = mod.build_rows(mod.collect(_payloads(days, ["LPR1Y"]))[0], [], skips := {})
    assert len(rows) == len(daily) + 24
    assert set(skips) == {"rate.lpr.5y"}


def test_long_gap_is_rejected(mod):
    """缺三个月（间隔 90 天）⇒ 不写，理由点名断点。

    起点必须够长：24 期里挖掉 3 期只剩 21 期，会先撞上「<24 期」那道闸，
    测不到断档判据（第一版就是这么假通过的）。
    """
    days = _monthly(30)
    broken = [d for i, d in enumerate(days) if not 10 <= i < 13]
    rows = mod.build_rows(mod.collect(_payloads(broken, ["LPR1Y"]))[0], [], skips := {})
    assert rows == []
    assert any("相隔" in v for v in skips.values())


def test_short_window_is_held_back(mod):
    """<24 期 ⇒ 一条都不写。"""
    rows = mod.build_rows(mod.collect(_payloads(_monthly(12), ["LPR1Y"]))[0], [], skips := {})
    assert rows == []
    assert "让闸按住" in skips["rate.lpr.1y"]
    assert "没有这条曲线" in skips["rate.lpr.5y"]


def test_each_curve_judged_independently(mod):
    """同一 payload 里 1Y 够 24 期、5Y 只有 12 期 ⇒ 只写 1Y（互相不能顶替）。"""
    data, _ = mod.collect(_payloads(_monthly(24), ["LPR1Y"]) +
                          _payloads(_monthly(12, start=(2025, 1, 20)), ["LPR5Y"]))
    rows = mod.build_rows(data, [], skips := {})
    assert {c for c, _d, _v in rows} == {"rate.lpr.1y"}
    assert "rate.lpr.5y" in skips


def test_unknown_curve_goes_to_not_mapped(mod):
    days = _monthly(24)
    data, others = mod.collect(_payloads(days, ["LPR1Y"]) + _payloads(days, ["LPR_OLD"]))
    rows = mod.build_rows(data, [], {})
    assert others == {"LPR_OLD"} and len(rows) == 24


def test_value_out_of_bounds_is_fatal(mod):
    with pytest.raises(SystemExit) as ei:
        mod.build_rows(mod.collect(_payloads(_monthly(24), ["LPR1Y"], value=350.0))[0], [], {})
    assert "值域" in str(ei.value)


def test_latest_scraped_at_wins(mod):
    days = _monthly(24)
    old = _payloads(days, ["LPR1Y"], value=3.0, scraped="2026-11-14T01:00:00+00:00")
    new = _payloads(days, ["LPR1Y"], value=3.5, scraped="2026-11-14T09:00:00+00:00")
    rows = mod.build_rows(mod.collect(old + new)[0], [], {})
    assert {v for _c, _d, v in rows} == {"3.5"}


def test_sql_has_no_bare_percent_literal(mod):
    """只扫 SQL 行（含 VALUES / template= / INSERT），不扫散文。"""
    import inspect
    import re
    bad = []
    for i, line in enumerate(inspect.getsource(mod).splitlines(), 1):
        s = line.lstrip()
        if s.startswith("#") or not re.search(r"VALUES|template=|INSERT", line):
            continue
        if re.search(r"%(?!s)", line.split("#")[0]):
            bad.append(f"{i}: {s}")
    assert not bad, "SQL 里有裸 '%'（psycopg2 会当占位符，改走绑定参数）：\n" + "\n".join(bad)
