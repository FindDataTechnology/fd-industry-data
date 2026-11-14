"""`extract_uk_hpi.py` 的判据回归（2026-11-14 立）。

三条判据，每条都对应一个真实会静默出错的形态：

1. **趋势闸**：<24 连续期、或中间断月 ⇒ 该区域整条不写（上游是"区域×月"长表，
   截断时会只剩第一个区域的 100 个月——2026-11-14 就是这个形态）。
2. **值域护栏**：指数与均价是两种量纲，串列（把 `average_price` 写进指数概念）不会
   被任何下游判据发现 ⇒ 越界即 FATAL。
3. **实体按区码不按名字**：CSV 里 `RegionName` 有重名与拼写变体，区码（E06000008）
   才是稳定键；用名字建实体迟早建出两条指同一块地方的行。
"""
import types
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "extract_uk_hpi.py"


@pytest.fixture(scope="module")
def mod():
    import sys
    sys.modules.setdefault("psycopg2", types.ModuleType("psycopg2"))
    spec = importlib.util.spec_from_file_location("extract_uk_hpi", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _items(n=30, *, first=(2015, 1), area="E06000008", region="Blackburn",
           idx=100.0, price=150000.0):
    y, mo = first
    out = []
    for i in range(n):
        out.append({"area_code": area, "region": region, "month": f"{y}-{mo:02d}",
                    "index": idx + i, "average_price": price + i * 1000,
                    "sales_volume": 100.0, "scraped_at": "2026-11-14T00:00:00"})
        mo += 1
        if mo == 13:
            y, mo = y + 1, 1
    return out


def test_thirty_months_two_series(mod):
    regions, data = mod.collect(_items(30))
    notes, skips = [], {}
    rows = mod.build_rows(data, notes, skips)
    assert regions == {"E06000008": "Blackburn"}
    assert len(rows) == 60, "30 期 × 2 条序列"
    assert not skips
    assert {r[0] for r in rows} == set(mod.SERIES)


def test_twenty_months_below_gate(mod):
    _, data = mod.collect(_items(20))
    skips = {}
    rows = mod.build_rows(data, [], skips)
    assert rows == []
    assert any("仅 20 期" in v for v in skips.values())


def test_gap_in_middle_skipped(mod):
    items = [i for i in _items(30) if i["month"] != "2016-03"]
    _, data = mod.collect(items)
    skips = {}
    assert mod.build_rows(data, [], skips) == []
    assert any("序列不连续" in v for v in skips.values())


def test_swapped_columns_are_fatal(mod):
    """把均价当指数（或反之）写进去，护栏必须拦下。"""
    _, data = mod.collect(_items(30, idx=150000.0, price=150000.0))
    with pytest.raises(SystemExit) as e:
        mod.build_rows(data, [], {})
    assert "值域" in str(e.value)


def test_latest_scraped_at_wins(mod):
    items = _items(30)
    dup = dict(items[-1], index=999.0, scraped_at="2026-01-01T00:00:00")   # 更旧
    dup2 = dict(items[-1], index=123.0, scraped_at="2026-12-01T00:00:00")  # 更新
    _, data = mod.collect(items + [dup, dup2])
    assert data["price.house_uk.index.monthly"]["E06000008"]["2017-06"]["index"] == 123.0


def test_non_month_and_missing_area_ignored(mod):
    items = _items(30) + [
        {"area_code": "", "month": "2015-01", "index": 1.0, "average_price": 1.0},
        {"area_code": "X1", "month": "2015-Q1", "index": 1.0, "average_price": 1.0},
    ]
    regions, data = mod.collect(items)
    assert set(regions) == {"E06000008"}
    assert "X1" not in data["price.house_uk.index.monthly"]


def test_sales_volume_not_mapped(mod):
    assert "sales_volume" in mod.NOT_MAPPED
    assert all(spec[0] != "sales_volume" for spec in mod.SERIES.values())


def test_period_helpers(mod):
    assert mod.prev_month("2015-01") == "2014-12"
    assert mod.prev_month("2015-06") == "2015-05"
    assert mod.continuity_gap(["2015-01", "2015-02"]) is None
    assert mod.continuity_gap(["2015-01", "2015-03"]) is not None
    assert mod.obs_month_of("2015-06") == "2015-06"
    assert mod.obs_month_of("2015-Q1") is None
