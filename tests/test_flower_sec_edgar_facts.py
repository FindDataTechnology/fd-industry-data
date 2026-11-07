"""花卉线 SEC 抽取器的口径契约（add-flower-vertical，2026-11-08）。

钉的是**四条会伪造数据的默认值**，不是 SQL 拼写：

1. **含代收税口径不入序列**：HYFM 2026 起改用
   ``RevenueFromContractWithCustomerIncludingAssessedTax``。这条事实必须留证、
   不得并入 ``financials.revenue``——并入等于凭空造一个营收跳升。
2. **YTD 窗剔除**：XBRL 同一 tag 对同一 ``end`` 同时给单季与 fiscal-YTD，
   两者同写会撞 ``uq_sem_obs`` 且量级误读。
3. **非 USD 金额不入**：``USD/shares``、``shares`` 混进金额概念即量纲污染。
4. **宇宙读 YAML 不硬编码**：``docs/flower-universe.yaml`` 里 `lanes` 含
   ``sec-edgar-facts`` 的公司才是本抽取器的对象；换宇宙不换代码。

第 5 条是重述：同一 ``(concept, entity, end)`` 多条事实取 ``filed`` 最新。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import flower_extractors.sec_edgar_facts as mod  # noqa: E402

ENTITIES = [("FLWS", 301105), ("SMG", 301108), ("HYFM", 301109), ("EDBL", 301110)]


class _Cur:
    """只实现 extract() 用到的两步：取 payload、取实体 id。"""

    def __init__(self, payloads, entities=ENTITIES):
        self.payloads = payloads
        self.entities = entities
        self._rows = []

    def execute(self, sql, params=None):
        # ``unit_items`` 取单列，真实游标回的是 [(payload,), ...]
        self._rows = ([(p,) for p in self.payloads] if "crawl_items" in sql
                      else self.entities)

    def fetchall(self):
        return self._rows


def _fact(ticker, tag, start, end, value, *, unit="USD", filed="2026-08-01", kind="fact"):
    return {
        "kind": kind, "ticker": ticker, "tag": tag, "unit": unit,
        "start": start, "end": end, "value": value, "filed": filed,
        "form": "10-Q",
    }


def test_universe_comes_from_the_yaml_and_excludes_other_verticals():
    universe = mod._universe()
    assert set(universe) == {"FLWS", "CENT", "SMG", "HYFM", "EDBL"}
    assert "MCD" not in universe  # 餐饮票符同底账，但不是本抽取器的对象
    assert all("sec-edgar-facts" in c["lanes"] for c in universe.values())


def test_quarter_rows_are_written_for_each_concept():
    payloads = [
        _fact("FLWS", "Revenues", "2026-04-01", "2026-06-30", 400.0),
        _fact("FLWS", "OperatingIncomeLoss", "2026-04-01", "2026-06-30", 40.0),
        _fact("FLWS", "NetIncomeLoss", "2026-04-01", "2026-06-30", -9.0),
    ]
    notes: list[str] = []
    rows = mod.extract(_Cur(payloads), notes)
    assert len(rows) == 3
    assert {r[0] for r in rows} == {
        "financials.revenue", "financials.operating_profit", "financials.net_income",
    }
    assert all(r[4] == "quarter" and r[1] == "stock" for r in rows)


def test_including_assessed_tax_is_evidence_only_never_mapped():
    payloads = [
        _fact("HYFM", "RevenueFromContractWithCustomerExcludingAssessedTax",
              "2025-07-01", "2025-09-30", 100.0),
        _fact("HYFM", "RevenueFromContractWithCustomerIncludingAssessedTax",
              "2026-04-01", "2026-06-30", 120.0),
    ]
    notes: list[str] = []
    rows = mod.extract(_Cur(payloads), notes)
    assert len(rows) == 1, "含代收税口径不得并入营收序列"
    assert rows[0][3] == "2025-09-30"  # 行形状 (code, etype, eid, date, gran, value)
    assert any("IncludingAssessedTax" in n and "不可拼接" in n for n in notes)


def test_ytd_window_is_skipped_and_reported():
    payloads = [
        _fact("FLWS", "Revenues", "2025-10-01", "2026-06-30", 1200.0),  # 9 个月 YTD
        _fact("FLWS", "Revenues", "2026-04-01", "2026-06-30", 400.0),
    ]
    notes: list[str] = []
    rows = mod.extract(_Cur(payloads), notes)
    assert len(rows) == 1
    assert any("非单季窗" in n for n in notes)


def test_non_usd_units_are_dropped():
    payloads = [
        _fact("FLWS", "Revenues", "2026-04-01", "2026-06-30", 1.5, unit="USD/shares"),
    ]
    notes: list[str] = []
    assert mod.extract(_Cur(payloads), notes) == []
    assert any("非 USD" in n for n in notes)


def test_latest_filed_wins_on_restatement():
    payloads = [
        _fact("FLWS", "Revenues", "2026-04-01", "2026-06-30", 400.0, filed="2026-08-01"),
        _fact("FLWS", "Revenues", "2026-04-01", "2026-06-30", 415.0, filed="2026-10-05"),
    ]
    rows = mod.extract(_Cur(payloads), [])
    assert len(rows) == 1
    assert rows[0][5] == "415"


def test_event_rows_are_not_financials():
    payloads = [_fact("FLWS", None, None, "2026-06-30", None, kind="event")]
    assert mod.extract(_Cur(payloads), []) == []


def test_company_without_payload_is_named_not_silently_missing():
    payloads = [_fact("FLWS", "Revenues", "2026-04-01", "2026-06-30", 400.0)]
    notes: list[str] = []
    mod.extract(_Cur(payloads), notes)
    joined = " ".join(notes)
    assert "EDBL" in joined and "零 payload" in joined
