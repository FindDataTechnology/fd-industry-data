"""`extract_usgs_nwis.py` / `extract_nasa_power.py` 的判据回归（2026-11-14 立）。

这两个源都是**固定历史数据集**（usgs 站点 2020 整年、nasa 乌鲁木齐 1984 整年），
语义覆盖盘点把它们登记在 `HISTORICAL` 里（R0b：可映射，不是窗口冻死）。判据要盯的是
「历史快照也能被正确映射」，以及各自最容易静默出错的形态：

1. **参数/站点筛错**：usgs 的 `parameter_cd` 不是 00060 的行、nasa 的未知 `parameter`
   都必须丢掉，否则会把别的指标写进这个概念。
2. **单位串列**：nasa 的 T2M（degC）与 PRECTOTCORR（mm）量纲完全不同，串进同一个
   概念就是把摄氏度当毫米——值域护栏各给各的。
3. **实体是站点/点位，不是国家**：每行自带 entity_id（`write_rows` 的签名如此），
   写死一个全局 entity 会把站点数据挂到 entity_id=0 上。
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    sys.modules.setdefault("psycopg2", types.ModuleType("psycopg2"))
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def nwis():
    return _load("extract_usgs_nwis")


@pytest.fixture(scope="module")
def nasa():
    return _load("extract_nasa_power")


def _days_2020():
    from datetime import date, timedelta
    d, out = date(2020, 1, 1), []
    while d.year == 2020:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


# ── usgs-nwis ──────────────────────────────────────────────────────────────

def _nwis_payloads(days, site="01646500", pcd="00060", value=5000.0,
                   scraped="2026-11-14T08:40:00+00:00"):
    return [{"site": site, "site_name": "POTOMAC RIVER", "parameter_cd": pcd,
             "date": d, "value": value, "unit": "ft3/s", "qualifiers": "A",
             "source": "usgs-nwis", "scraped_at": scraped} for d in days]


def test_nwis_writes_full_year(nwis):
    data, names = nwis.collect(_nwis_payloads(_days_2020()))
    rows = nwis.build_rows(data, [], skips := {})
    assert len(rows) == 366 and skips == {}
    assert names == {"01646500": "POTOMAC RIVER"}
    assert {c for c, _s, _d, _v in rows} == {"hydro.usgs.discharge.01646500"}


def test_nwis_row_carries_its_own_site(nwis):
    """每行带站点号（不是全局 entity）—— 写库时用它查 entity_id。"""
    data, _ = nwis.collect(_nwis_payloads(_days_2020()))
    rows = nwis.build_rows(data, [], {})
    assert {s for _c, s, _d, _v in rows} == {"01646500"}


def test_nwis_other_parameter_is_dropped(nwis):
    days = _days_2020()
    data, _ = nwis.collect(_nwis_payloads(days) + _nwis_payloads(days, pcd="00065"))
    rows = nwis.build_rows(data, [], {})
    assert len(rows) == 366          # 00065（水位）不该被写进来


def test_nwis_unknown_site_is_dropped(nwis):
    days = _days_2020()
    data, names = nwis.collect(_nwis_payloads(days) + _nwis_payloads(days, site="99999999"))
    assert set(names) == {"01646500"} == set(data)      # 未登记站点不进实体也不进序列
    assert len(nwis.build_rows(data, [], {})) == 366


def test_nwis_unit_swap_is_fatal(nwis):
    """m3/s 与 ft3/s 差 35 倍；上界 1e7 兜不住 m3/s 换算错位之外的浮夸值。"""
    data, _ = nwis.collect(_nwis_payloads(_days_2020(), value=5e7))
    with pytest.raises(SystemExit) as ei:
        nwis.build_rows(data, [], {})
    assert "值域" in str(ei.value)


def test_nwis_short_window_held(nwis):
    data, _ = nwis.collect(_nwis_payloads(_days_2020()[:12]))
    rows = nwis.build_rows(data, [], skips := {})
    assert rows == [] and "让闸按住" in skips["hydro.usgs.discharge.01646500"]


def test_nwis_latest_scraped_at_wins(nwis):
    days = _days_2020()[:30]
    old = _nwis_payloads(days, value=1.0, scraped="2026-11-14T01:00:00+00:00")
    new = _nwis_payloads(days, value=2.0, scraped="2026-11-14T09:00:00+00:00")
    rows = nwis.build_rows(nwis.collect(old + new)[0], [], {})
    assert {v for _c, _s, _d, v in rows} == {"2"}


# ── nasa-power ─────────────────────────────────────────────────────────────

def _nasa_days_1984():
    from datetime import date, timedelta
    d, out = date(1984, 1, 1), []
    while d.year == 1984:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _nasa_payloads(days, param="T2M", value=10.0, unit="degC",
                   scraped="2026-11-14T06:36:00+00:00"):
    return [{"parameter": param, "date": d, "value": value, "unit": unit,
             "lat": 43.8, "lon": 87.6, "source": "nasa-power",
             "scraped_at": scraped} for d in days]


def test_nasa_writes_both_parameters(nasa):
    days = _nasa_days_1984()
    data = nasa.collect(_nasa_payloads(days) + _nasa_payloads(days, "PRECTOTCORR", 0.5, "mm"))
    rows = nasa.build_rows(data, [], skips := {})
    assert len(rows) == 366 * 2 and skips == {}
    assert {c for c, _s, _d, _v in rows} == {"meteo.t2m.daily", "meteo.precip.daily"}
    assert {s for _c, s, _d, _v in rows} == {"POWER-43.8N-87.6E"}


def test_nasa_null_fill_sentinel_skipped(nasa):
    """spider 已把 -999 哨兵置 null；null 不许写进序列。"""
    days = _nasa_days_1984()
    payloads = _nasa_payloads(days)
    payloads[0]["value"] = None
    rows = nasa.build_rows(nasa.collect(payloads), [], {})
    assert len(rows) == 365


def test_nasa_unknown_parameter_dropped(nasa):
    days = _nasa_days_1984()
    data = nasa.collect(_nasa_payloads(days) + _nasa_payloads(days, "RH2M", 50.0, "%"))
    assert set(data) == {"T2M", "PRECTOTCORR"}
    assert len(nasa.build_rows(data, [], {})) == 366


def test_nasa_temperature_bound_rejects_precip_scale(nasa):
    """把 mm 写进气温概念（或反之）必须 FATAL —— 两个参数值域各给各的。"""
    days = _nasa_days_1984()
    data = nasa.collect(_nasa_payloads(days, "T2M", value=500.0, unit="mm"))
    with pytest.raises(SystemExit) as ei:
        nasa.build_rows(data, [], {})
    assert "值域" in str(ei.value)


def test_nasa_units_differ_per_concept(nasa):
    """两个参数单位不同 ⇒ 概念表里各自的 unit 必须分开（同 code 两 unit 会随机取行）。"""
    assert nasa.SERIES["T2M"][3] == "degC"
    assert nasa.SERIES["PRECTOTCORR"][3] == "mm"


def test_nasa_short_window_held(nasa):
    days = _nasa_days_1984()[:12]
    rows = nasa.build_rows(nasa.collect(_nasa_payloads(days)), [], skips := {})
    assert rows == []
    assert "让闸按住" in skips["meteo.t2m.daily"]
    assert "没有这个参数" in skips["meteo.precip.daily"]


def test_shared_module_has_no_bare_percent_in_sql():
    """`_semextract.write_rows` 的 SQL 里也不许有裸 `%`（单位已是绑定参数）。"""
    import inspect
    import re
    sys.modules.setdefault("psycopg2", types.ModuleType("psycopg2"))
    sys.path.insert(0, str(SCRIPTS))
    import _semextract
    bad = []
    for i, line in enumerate(inspect.getsource(_semextract).splitlines(), 1):
        s = line.lstrip()
        if s.startswith("#") or not re.search(r"VALUES|template=|INSERT", line):
            continue
        if re.search(r"%(?!s)", line.split("#")[0]):
            bad.append(f"{i}: {s}")
    assert not bad, "SQL 里有裸 '%'：\n" + "\n".join(bad)
