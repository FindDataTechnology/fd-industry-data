"""UN Comtrade 花卉三档抽取器的映射口径（add-flower-vertical 3.2）。

⚠️ 这组测试证明的是**映射口径**，不是「已入库」：fixture 取自 2026-11-08 官方免 key
预览端点的真实回包（2023 年度中国申报值），而产线取数路径需要 `COMTRADE_API_KEY`，
本机未配置，入库那一步尚未跑过。

钉四件会静默出错的事：

1. **串档必须拒**：回包的 cmdCode/flowCode/partnerCode/reporterCode 与请求不一致时，
   跳过而不是写进去——上游串档会让「玫瑰」变成「其他切花」。
2. **出口/进口落对概念**：X → `trade.export_flow.world`，M → `trade.import_flow.world`。
3. **三档不合并**：060311 / 060319 / 060120 各自独立行，任何合并都会掩盖结构。
4. **缺档不补 0**：某档本次零行要记 note，不能让板面读成「贸易额 0」。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

spec = importlib.util.spec_from_file_location(
    "comtrade_flowers", ROOT / "scripts" / "flower_extractors" / "comtrade_flowers.py")
cf = importlib.util.module_from_spec(spec)
sys.modules["comtrade_flowers"] = cf
spec.loader.exec_module(cf)

FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "comtrade_flowers_2023.json")
                     .read_text(encoding="utf-8"))["rows"]

IDS = {"060311": 1, "060319": 2, "060120": 3}


def test_real_2023_payload_maps_to_six_rows_with_correct_concepts():
    notes: list[str] = []
    out = cf.map_rows(FIXTURE, IDS, notes)
    assert len(out) == 6
    assert not notes
    by = {(row[0], row[2], row[3]): row[5] for row in out}
    assert by[(cf.CODE_EXPORT, 1, "2023-01-01")] == 17_283_341.0   # 060311 出口
    assert by[(cf.CODE_IMPORT, 1, "2023-01-01")] == 5_876_523.0    # 060311 进口
    assert by[(cf.CODE_EXPORT, 2, "2023-01-01")] == 39_250_772.0   # 060319 出口
    assert by[(cf.CODE_IMPORT, 2, "2023-01-01")] == 42_701_244.0   # 060319 进口
    assert by[(cf.CODE_EXPORT, 3, "2023-01-01")] == 19_371.0       # 060120 出口
    assert by[(cf.CODE_IMPORT, 3, "2023-01-01")] == 23_927.0       # 060120 进口
    # 三档恒不合并：六行、三个实体、两个概念
    assert len({row[2] for row in out}) == 3
    assert {row[0] for row in out} == {cf.CODE_EXPORT, cf.CODE_IMPORT}


def test_crossed_wire_rows_are_rejected_not_written():
    """回包串档（要 060311 却回了 060319）必须按**请求**判，不能被当成合法行。

    第一版只校验「属于三档之一」，于是把串档行照写——本测试就是抓这个的。
    """
    bad = [dict(FIXTURE[0], cmdCode="060319"),          # 请求 060311 却回 060319
           dict(FIXTURE[1], flowCode="Z"),              # 非法流向
           dict(FIXTURE[2], partnerCode=842),           # 非 world 伙伴
           dict(FIXTURE[3], reporterCode=842)]          # 非中国申报
    notes: list[str] = []
    out = cf.map_rows(bad, IDS, notes, expected={("060311", "X")})
    assert out == []
    assert any("口径不一致" in n for n in notes)


def test_the_right_row_for_the_requested_combo_still_passes():
    """口径闸不能把正确回包也拒掉（否则「拒得干净」变成「什么都不要」）。"""
    notes: list[str] = []
    out = cf.map_rows([FIXTURE[0]], IDS, notes, expected={("060311", "X")})
    assert len(out) == 1 and out[0][5] == 17_283_341.0


def test_missing_code_is_reported_not_zero_filled():
    only_rose = [r for r in FIXTURE if r["cmdCode"] == "060311"]
    notes: list[str] = []
    cf.map_rows(only_rose, IDS, notes)
    assert any("060120" in n and "060319" in n for n in notes)


def test_null_value_and_non_year_periods_are_skipped():
    rows = [dict(FIXTURE[0], primaryValue=None), dict(FIXTURE[1], period="2023-Q1")]
    notes: list[str] = []
    assert cf.map_rows(rows, IDS, notes) == []


def test_unregistered_hs_entity_is_skipped():
    notes: list[str] = []
    out = cf.map_rows(FIXTURE, {"060311": 1}, notes)
    assert {row[2] for row in out} == {1}


def test_concepts_declare_the_tradeflow_shape():
    """export 复用既有 TradeFlow 概念；import 是本波新增的同形概念。"""
    codes = {c[0] for c in cf.CONCEPTS}
    assert codes == {"trade.export_flow.world", "trade.import_flow.world"}
    for code, _zh, _en, _cat, unit, measure, freq, etype, src in cf.CONCEPTS:
        assert etype == "hs_code" and unit == "美元" and freq == "yearly" and src == "un-comtrade"
        assert measure in ("export", "import")


def test_missing_credential_is_a_loud_skip_not_a_crash(monkeypatch):
    """没 key 时必须跳过并留 note：既不落 0 行，也不能炸掉整条花卉抽取批次。"""
    monkeypatch.delenv("COMTRADE_API_KEY", raising=False)
    monkeypatch.delenv("COMTRADE_SUBSCRIPTION_KEY", raising=False)
    notes: list[str] = []
    assert cf.extract(None, notes) == []
    assert any("COMTRADE_API_KEY" in n and "跳过" in n for n in notes)


def test_with_a_credential_the_fetch_path_is_exercised(monkeypatch):
    """有 key 时真的去取数：这里用假单元证明「不再静默跳过」。"""
    monkeypatch.setenv("COMTRADE_API_KEY", "test-key")
    calls: list[dict] = []

    class Unit:
        @staticmethod
        def run_un_comtrade(**kw):
            calls.append(kw)
            return [{"cmdCode": kw["cmd_code"], "flowCode": kw["flow_code"],
                     "partnerCode": 0, "reporterCode": 156, "period": "2023",
                     "primaryValue": 1.0}]

    class Cur:
        def execute(self, *a, **k):
            pass

        def fetchall(self):
            return [("060311", 1), ("060319", 2), ("060120", 3)]

    cf._load_unit = lambda: Unit
    out = cf.extract(Cur(), [])
    assert len(calls) == 6, "3 档 × 出口/进口 = 6 次取数"
    assert len(out) == 6


def test_second_partner_breakdown_rows_are_rejected():
    """partner2 = 第二伙伴国（转口来源）。2016/2017 上游会返回它的明细行。

    实测：2016 年 29 行全是 partner=0，其中 28 行 partner2≠0（344/458/682…），
    只有 1 行 partner2=0 是世界合计（9,402,661）；全行合计正好是它的两倍
    （合计行 + 明细行）。首版没拦这一维，2016 被写成某条明细的 44.5 万。
    """
    total = {"cmd_code": "060311", "flow_code": "X", "partner_code": 0,
             "reporter_code": 156, "partner2_code": 0, "period": "2016",
             "primary_value": 9_402_661.0}
    detail = dict(total, partner2_code=344, primary_value=445_244.0)
    notes: list[str] = []
    out = cf.map_rows([detail, total], IDS, notes, expected={("060311", "X")})
    assert len(out) == 1, f"明细行必须被拒，实际产出 {len(out)} 行"
    assert out[0][5] == 9_402_661.0
