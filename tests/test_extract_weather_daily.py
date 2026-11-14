"""`extract_weather_daily.py` 的判据回归（2026-11-14 立）。

一个脚本覆盖三个形状同构的源（elexon-bmrs / noaa-ncei / open-meteo），所以判据要盯
「配置驱动的部分会不会指错」，而不只是「算得对不对」：

1. **期字段是配置项**：`elexon-bmrs` 的期字段叫 `period` 不叫 `date`；按 `date` 取会得到
   0 期、然后静默显示成"没有数据"（本轮最贵的一课同族）。
2. **城市码要指到册内已存在的实体**：册内北京/上海/深圳是 `BJS`/`SHA`/`SZX`，广州是
   历史遗留的 `CN-CITY-广州`；给广州新建 `GZ` 会让同一座城市分裂成两个实体，
   而没有任何下游判据会把它们算成同一处。
3. **按单位分组写库时一条行都不能少**：第一版用「概念码 -> 元组」的字典分组，
   同一概念的多行（多实体 × 多日期）被压成一行 —— 静默丢数据。
4. **值域按指标各给**：tmax 与 prcp 量纲不同，串列不会被任何下游发现。
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture(scope="module")
def ewd():
    sys.modules.setdefault("psycopg2", types.ModuleType("psycopg2"))
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("extract_weather_daily",
                                                  SCRIPTS / "extract_weather_daily.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _days(n=30, start=(2026, 10, 13)):
    from datetime import date, timedelta
    d = date(*start)
    return [(d + timedelta(days=i)).isoformat() for i in range(n)]


def _elexon(days, temp=12.0):
    return [{"period": d, "temperature": temp, "source": "elexon-bmrs",
             "scraped_at": "2026-11-14T06:33:00+00:00"} for d in days]


def _noaa(days, station="USW00023174"):
    return [{"date": d, "station": station, "tmax": 22.0, "tmin": 11.0, "prcp": 0.0,
             "source": "noaa-ncei", "scraped_at": "2026-11-14T07:09:00+00:00"} for d in days]


def _openmeteo(days, city="beijing"):
    return [{"date": d, "city": city, "temperature_2m_mean": 15.0, "precipitation_sum": 1.0,
             "source": "open-meteo", "scraped_at": "2026-11-14T07:38:00+00:00"} for d in days]


def test_elexon_uses_period_not_date_field(ewd):
    """期字段是 `period`：用 `date` 取会拿到 0 期。"""
    spec = ewd.SOURCES["elexon-bmrs"]
    rows = ewd.build_rows(ewd.collect(spec, _elexon(_days())), spec, [], skips := {})
    assert len(rows) == 30 and skips == {}
    assert {c for c, *_ in rows} == {"meteo.gb_effective_temp.daily"}
    assert {e for _c, e, *_ in rows} == {"GB"}


def test_config_declares_matching_period_field(ewd):
    assert ewd.SOURCES["elexon-bmrs"]["period"] == "period"
    assert ewd.SOURCES["noaa-ncei"]["period"] == "date"
    assert ewd.SOURCES["open-meteo"]["period"] == "date"


def test_noaa_writes_three_metrics_per_station(ewd):
    spec = ewd.SOURCES["noaa-ncei"]
    rows = ewd.build_rows(ewd.collect(spec, _noaa(_days())), spec, [], {})
    assert len(rows) == 30 * 3
    assert {c for c, *_ in rows} == {"meteo.station.tmax.daily",
                                     "meteo.station.tmin.daily",
                                     "meteo.station.prcp.daily"}


def test_openmeteo_city_codes_match_existing_entities(ewd):
    """city 名要折到**册内已存在**的码；广州沿用 `CN-CITY-广州` 而不是新建 `GZ`。"""
    spec = ewd.SOURCES["open-meteo"]
    payload = (_openmeteo(_days(), "beijing") + _openmeteo(_days(), "shanghai")
               + _openmeteo(_days(), "guangzhou"))
    rows = ewd.build_rows(ewd.collect(spec, payload), spec, [], {})
    ents = {e for _c, e, *_ in rows}
    assert ents == {"BJS", "SHA", "CN-CITY-广州"}
    assert "GZ" not in ents


def test_openmeteo_unknown_city_dropped(ewd):
    spec = ewd.SOURCES["open-meteo"]
    payload = _openmeteo(_days(), "beijing") + _openmeteo(_days(), "atlantis")
    rows = ewd.build_rows(ewd.collect(spec, payload), spec, [], {})
    assert {e for _c, e, *_ in rows} == {"BJS"}


def test_truncated_entity_held_back(ewd):
    """深圳被 limit 切到 7 天时不许写（open-meteo 2026-11-14 的真实形态）。"""
    spec = ewd.SOURCES["open-meteo"]
    payload = _openmeteo(_days(), "beijing") + _openmeteo(_days(7), "shenzhen")
    rows = ewd.build_rows(ewd.collect(spec, payload), spec, [], skips := {})
    assert {e for _c, e, *_ in rows} == {"BJS"}
    assert any(k.endswith("@SZX") and "让闸按住" in v for k, v in skips.items())



def test_metric_swap_is_fatal(ewd):
    """把 mm 当气温写（或反之）必须 FATAL。"""
    spec = ewd.SOURCES["noaa-ncei"]
    payload = _noaa(_days())
    for p in payload:
        p["tmax"] = 5e4
    with pytest.raises(SystemExit) as ei:
        ewd.build_rows(ewd.collect(spec, payload), spec, [], {})
    assert "值域" in str(ei.value)


def test_each_metric_has_its_own_unit(ewd):
    units = {code: rest[2] for code, *rest in ewd.SOURCES["noaa-ncei"]["metrics"].values()}
    assert units["meteo.station.tmax.daily"] == "degC"
    assert units["meteo.station.tmin.daily"] == "degC"
    assert units["meteo.station.prcp.daily"] == "mm"


def test_short_series_held_back(ewd):
    spec = ewd.SOURCES["elexon-bmrs"]
    rows = ewd.build_rows(ewd.collect(spec, _elexon(_days(10))), spec, [], skips := {})
    assert rows == []
    # 带实体分组的源 skip 键是 `code@entity`（elexon 是单一序列但同样走这条路径）
    assert any("让闸按住" in v for v in skips.values())
