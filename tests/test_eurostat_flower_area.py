"""EU 花卉主面积抽取器的口径闸（add-flower-vertical 3.3）。

钉三件**会把面积写成产量**的事：

1. 只认 ``(crops=N0000, strucpro=MAR_THS_HA)``：上游以后新增产量/单产口径时，
   抽取器必须跳过而不是把「吨」写进「公顷」概念。
2. 千公顷 → 公顷（×1000）：协议词表以公顷为基本单位，不折算会让 Eurostat 与
   AIPH 两个来源的面积差 1000 倍。
3. 上游零行即失败：实测口径应有 8 geo × 10 年，取不到要抛错，不能落成 0 行。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

spec = importlib.util.spec_from_file_location(
    "eu_flower_area", ROOT / "scripts" / "flower_extractors" / "eurostat_flower_area.py")
efa = importlib.util.module_from_spec(spec)
sys.modules["eu_flower_area"] = efa
spec.loader.exec_module(efa)


class Cur:
    def __init__(self, ids):
        self.ids = ids

    def execute(self, sql, params=None):
        self.params = params

    def fetchall(self):
        return [(code, eid) for code, eid in self.ids.items()]


def _run(rows, ids=None):
    """把假单元塞进抽取器的 _load_unit，返回 (rows, notes)。"""
    class Unit:
        @staticmethod
        def run_eurostat_flower_area(limit=500):
            return rows

    efa._load_unit = lambda: Unit
    notes: list[str] = []
    out = efa.extract(Cur(ids or {"EU27_2020": 300990, "NL": 16}), notes)
    return out, notes


def _row(geo="EU27_2020", period="2024", value=66.51,
         crops="N0000", strucpro="MAR_THS_HA"):
    return {"dataset": "apro_cpsh1", "geo": geo, "freq": "A", "unit": "",
            "crops": crops, "strucpro": strucpro, "period": period,
            "value": value, "indic": "", "nace_r2": "", "s_adj": "", "p_adj": ""}


def test_thousand_hectares_are_converted_to_hectares():
    out, _ = _run([_row(value=66.51)])
    assert out == [("area.flower.all.planted", "country", 300990, "2024-01-01",
                    "year", 66510.0)]


def test_production_measure_is_rejected_not_mislabelled_as_area():
    """上游若给产量（HPRD_HUMD_EU_THS_T），绝不能写进「面积」概念。"""
    out, notes = _run([_row(value=123.0, strucpro="HPRD_HUMD_EU_THS_T")])
    assert out == []
    assert any("主面积" in n for n in notes)


def test_other_crop_codes_are_rejected():
    out, notes = _run([_row(value=1.0, crops="C0000")])
    assert out == []
    assert any("主面积" in n for n in notes)


def test_unregistered_geo_is_skipped_with_a_note():
    out, notes = _run([_row(geo="PT", value=9.0)])
    assert out == []
    assert any("PT" in n for n in notes)


def test_empty_upstream_raises_instead_of_writing_zero_rows():
    try:
        _run([])
    except RuntimeError as exc:
        assert "0 行" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("零行必须抛错，不能静默返回 []")


def test_non_year_periods_are_skipped():
    out, _ = _run([_row(period="2024-Q1", value=1.0)])
    assert out == []
