"""`extract_ncd.py` 的判据回归（2026-11-14 立）。

四条判据，每条都对应一个会静默出错的形态：

1. **趋势闸**：每档 <24 个交易日、或中间有长假断档 ⇒ 该档整条不写。
2. **期限档按 tenor_days 认、不按标签**：上游给的是固定网格（7…365），一档一个概念；
   认错一档就是把 3M 的收益率写进 6M 的概念，任何下游判据都不会红。
3. **只写 NCD_AAA**：同一个 payload 里若有别的评级曲线，必须落进 NOT_MAPPED 而不是混写。
4. **SQL 里不许有裸 `'%'`**：psycopg2 会把裸 `%` 当占位符（参数少一位 ⇒ IndexError），
   而单测里的 psycopg2 是替身模块，永远测不出运行期这一层。
"""
import importlib.util
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "extract_ncd.py"


@pytest.fixture(scope="module")
def mod():
    import sys
    sys.modules.setdefault("psycopg2", types.ModuleType("psycopg2"))
    sys.path.insert(0, str(SCRIPT.parent))   # _semextract 是同目录模块
    spec = importlib.util.spec_from_file_location("extract_ncd", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _trading_days(n: int, start=(2026, 6, 1)) -> list[str]:
    from datetime import date, timedelta
    out, d = [], date(*start)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _payloads(days, tenors, value=1.4, curve="NCD_AAA", scraped="2026-11-14T13:10:11+00:00"):
    return [{"curve": curve, "curve_name": "同业存单(AAA)", "date": d, "unit": "%",
             "reference": "maturity", "yield_pct": value, "tenor_days": t,
             "source": "chinamoney-ncd-curve", "scraped_at": scraped}
            for d in days for t in tenors]


def test_writes_all_tenors_when_window_is_long_enough(mod):
    days = _trading_days(27)
    data, others = mod.collect(_payloads(days, mod.SERIES))
    rows = mod.build_rows(data, [], skips := {})
    assert len(rows) == 27 * len(mod.SERIES)
    assert skips == {} and others == set()
    assert {c for c, _d, _v in rows} == {v[0] for v in mod.SERIES.values()}


def test_gate_holds_back_short_window(mod):
    """12 个交易日（chinabond 的真实形态）⇒ 一档都不写。"""
    days = _trading_days(12)
    rows = mod.build_rows(mod.collect(_payloads(days, mod.SERIES))[0], [], skips := {})
    assert rows == []
    assert len(skips) == len(mod.SERIES)


def test_gate_rejects_long_holiday_gap(mod):
    days = _trading_days(40)
    broken = [d for i, d in enumerate(days) if not 12 <= i < 20]
    rows = mod.build_rows(mod.collect(_payloads(broken, mod.SERIES))[0], [], skips := {})
    assert rows == []
    assert any("相隔" in v for v in skips.values())


def test_tenor_is_matched_by_days_not_label(mod):
    """3M（91 天）的收益率必须落进 3m 概念；把 tenor_days 改成 182 就进 6m。"""
    days = _trading_days(27)
    data, _ = mod.collect(_payloads(days, [91]))
    assert list(data[mod.SERIES[91][0]]) == days
    assert data[mod.SERIES[182][0]] == {}


def test_unknown_tenor_ignored(mod):
    days = _trading_days(27)
    rows = mod.build_rows(mod.collect(_payloads(days, [7, 999]))[0], [], {})
    assert len(rows) == 27                       # 999 天不在网格里，丢掉


def test_other_curves_go_to_not_mapped(mod):
    days = _trading_days(27)
    data, others = mod.collect(_payloads(days, [365]) +
                               _payloads(days, [365], curve="NCD_AA+"))
    rows = mod.build_rows(data, [], {})
    assert others == {"NCD_AA+"}
    assert len(rows) == 27


def test_value_out_of_bounds_is_fatal(mod):
    days = _trading_days(27)
    with pytest.raises(SystemExit) as ei:
        mod.build_rows(mod.collect(_payloads(days, [7], value=140.0))[0], [], {})
    assert "值域" in str(ei.value)


def test_latest_scraped_at_wins(mod):
    days = _trading_days(27)
    old = _payloads(days, [365], value=1.0, scraped="2026-11-14T01:00:00+00:00")
    new = _payloads(days, [365], value=2.0, scraped="2026-11-14T09:00:00+00:00")
    rows = mod.build_rows(mod.collect(old + new)[0], [], {})
    assert {v for _c, _d, v in rows} == {"2"}


def test_sql_has_no_bare_percent_literal(mod):
    """只扫 SQL 行（含 VALUES / template= / INSERT），不扫散文。

    判据落在「SQL 字面量」上而不是整份源码：脚本的注释与 docstring 里**必须**能提到
    `'%'` 这个坑，按整份源码扫会把讲解本身判成缺陷（第一版就是这么假红的）。
    """
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
