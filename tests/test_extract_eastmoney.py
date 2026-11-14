"""eastmoney-data 语义层回填的判据回归（2026-11-14 立）。

四组判据，每组都对应一次真实踩坑：

1. **趋势闸是硬断言**：<24 连续期、或中间断月 ⇒ 整条序列不写。`eastmoney-data`
   长期进不了语义层就是因为 spider 的 `pageSize` 是 20（差一本），而不是因为没跑。
2. **单位不混**：「当月」列是 100 基指数（实测 100.7），「同比」列才是百分比（0.7）。
   指数被写进 `%` 概念后没有任何下游判据会红 —— 所以脚本里有一道值域护栏。
3. **同比两族故意不写**：`CPI_YOY`/`PPI_YOY` 已被 akshare 覆盖且数值逐月相同，
   而 `date` 是文本（`2026-10-01` vs `2026-10`），重复写不会去重而是每月多一个点。
4. **空壳行不写**：修复前那 118 条 `indicator_value` 为空串的行必须被过滤，不能变 0。
"""
import types
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "extract_eastmoney.py"


@pytest.fixture(scope="module")
def mod():
    # 脚本只 import psycopg2；纯函数测试不需要驱动
    import sys
    sys.modules.setdefault("psycopg2", types.ModuleType("psycopg2"))
    spec = importlib.util.spec_from_file_location("extract_eastmoney", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _items(n_periods, *, first=(2024, 5), cpi_base=100.7, cpi_same=0.7,
           ppi_base=99.5, ppi_same=-1.2):
    """按真实 payload 的形状造 n 期四个指标族。"""
    out = []
    y, mo = first
    for _ in range(n_periods):
        p = f"{y}-{mo:02d}"
        for name, val in (("CPI 当月(全国)", cpi_base), ("CPI 同比(全国)", cpi_same),
                          ("PPI 当月(全国)", ppi_base), ("PPI 同比(全国)", ppi_same)):
            out.append({"indicator_name": name, "period": p,
                        "indicator_value": str(val), "scraped_at": "2026-11-14T00:00:00"})
        mo += 1
        if mo == 13:
            y, mo = y + 1, 1
    return out


def test_parse_indicator_rejects_other_shapes(mod):
    assert mod.parse_indicator("CPI 当月(全国)") == ("CPI", "当月")
    assert mod.parse_indicator("PPI 同比(全国)") == ("PPI", "同比")
    assert mod.parse_indicator("") is None
    assert mod.parse_indicator("CPI 累计(全国)") is None


def test_thirty_periods_writes_sixty_index_rows(mod):
    mod.SKIP_NOTES.clear()
    rows, ranges = mod.build_rows(mod.collect(_items(30)), [])
    assert len(rows) == 60, "30 期 × 2 个指数族；同比两族不写"
    assert {r[0] for r in rows} == {"price.cpi.index.monthly",
                                   "price.ppi.index.monthly"}
    assert not mod.SKIP_NOTES
    assert 50 <= ranges["price.cpi.index.monthly"][1] <= 200


def test_yoy_families_are_deliberately_not_mapped(mod):
    """同比 → 已有 akshare 覆盖；写它会每月多一个 date 形态不同的点。"""
    assert ("CPI", "同比") not in mod.SERIES
    assert ("PPI", "同比") not in mod.SERIES
    assert set(mod.NOT_MAPPED) == {("CPI", "同比"), ("PPI", "同比")}


def test_twenty_periods_is_below_the_trend_gate(mod):
    mod.SKIP_NOTES.clear()
    rows, _ = mod.build_rows(mod.collect(_items(20)), [])
    assert rows == [], "20 期（旧 pageSize）必须被闸挡下"
    assert mod.SKIP_NOTES["CPI 当月"].startswith("仅 20 期")


def test_gap_in_the_middle_is_refused(mod):
    items = [i for i in _items(30) if i["period"] != "2025-06"]
    mod.SKIP_NOTES.clear()
    rows, _ = mod.build_rows(mod.collect(items), [])
    assert rows == []
    assert mod.SKIP_NOTES["CPI 当月"].startswith("序列不连续")


def test_hollow_rows_are_dropped_not_zero_filled(mod):
    items = _items(30) + [
        {"indicator_name": "", "period": "", "indicator_value": "",
         "scraped_at": "2026-01-01"},
        {"indicator_name": "CPI 当月(全国)", "period": "2026-10",
         "indicator_value": "", "scraped_at": "2026-11-14T00:00:00"},
    ]
    latest = mod.collect(items)
    # 空值那期仍取到前一份有值的 payload
    assert mod.num(latest["price.cpi.index.monthly"]["2026-10"]["indicator_value"]) == 100.7


def test_out_of_band_values_are_refused(mod):
    """上游把「百分比」列当「当月」列给了（值只有几）时，护栏必须 FATAL。"""
    mod.SKIP_NOTES.clear()
    with pytest.raises(SystemExit) as e:
        mod.build_rows(mod.collect(_items(30, cpi_base=0.7, ppi_base=-1.2)), [])
    assert "值域" in str(e.value)
