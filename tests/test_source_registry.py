"""四表对照与漂移判据（change derive-source-registries 1.1）。

**为什么要有这条测试**：L3 可达性注册表是 `generate_manifests.py` /
`register_manifests.py` / `preclassify_stock.py` 三个脚本的输入，还是工单
「这个源该 retire 还是 repair」的证据。它 2026-09-12 生成，95 条里 59 条
指向的单元目录已不在仓里，却仍被当作 `unreachable` 引用——「已删掉」与
「打不通」被压成了一档。

用例守两件事：
1. `archived`（目录不在）优先于任何可达性结论，且不重复进 `stale`；
2. 证据超阈值判 `stale` 而不是继续当当前事实；「无逐条 at」时用文档顶层
   `generated_at` 兜底，不许当成「很新」。
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import source_registry as sr  # noqa: E402
import check_registry_drift as crd  # noqa: E402

NOW = dt.date(2026, 11, 21)


def test_archived_wins_over_reachability():
    """目录不在 ⇒ archived，即便记录标 verified_html。"""
    got, age = sr.classify_l3({"verdict": "verified_html", "evidence": {"at": "2026-11-01"}},
                              present=False, now=NOW)
    assert got == "archived"
    assert age is not None


def test_stale_after_threshold():
    got, age = sr.classify_l3({"verdict": "verified_api", "evidence": {"at": "2026-01-01"}},
                              present=True, now=NOW)
    assert got == "stale" and age > sr.STALENESS_DAYS


def test_fresh_evidence_keeps_verdict():
    got, _ = sr.classify_l3({"verdict": "degraded", "evidence": {"at": "2026-11-15"}},
                            present=True, now=NOW)
    assert got == "degraded"


def test_no_record_is_unmeasured():
    got, age = sr.classify_l3(None, present=True, now=NOW)
    assert got == "unmeasured" and age is None


def test_drift_absent_not_duplicated_in_stale():
    """目录不在的项进 absent，不进 stale——两档成因不同。"""
    units = {
        "spiders/gone": {"verdict": "unreachable", "evidence": {"at": "2026-01-01"}},
        "spiders/here": {"verdict": "unreachable", "evidence": {"at": "2026-01-01"}},
        "spiders/now": {"verdict": "verified_api", "evidence": {"at": "2026-11-15"}},
    }
    exists = {"spiders/here", "spiders/now"}
    absent, stale = crd.classify_units(units, lambda k: k in exists, NOW)
    assert absent == ["spiders/gone"]
    assert stale == ["spiders/here（324 天）"]
    assert not any("gone" in s for s in stale)


def test_default_at_fallback_prevents_false_green():
    """无逐条 evidence.at 时用顶层 generated_at 兜底，不许当成「很新」。"""
    units = {"spiders/x": {"verdict": "verified_html"}}
    absent, stale = crd.classify_units(units, lambda k: True, NOW,
                                       default_at="2026-01-01T00:00:00+00:00")
    assert absent == [] and stale == ["spiders/x（324 天）"], stale
    # 无兜底时判不出年龄 → 不进 stale（这是「不知道」不是「没问题」，见 design D3）
    absent2, stale2 = crd.classify_units(units, lambda k: True, NOW, default_at=None)
    assert stale2 == []


def test_real_registry_fields_present():
    """真盘面：回填后每条都带 unit_present/evidence_age_days，verdict 语义不变。"""
    import json

    p = REPO / "output" / "l3-reanalysis" / "registry.json"
    if not p.is_file():
        pytest.skip("无 L3 注册表")
    d = json.loads(p.read_text(encoding="utf-8"))
    units = d.get("units") or {}
    assert units, "注册表为空"
    assert "staleness_threshold_days" in d
    missing = [k for k, v in units.items() if "unit_present" not in v]
    assert not missing, f"未回填的条目：{missing[:5]}"
    # verdict 取值集合不变
    vs = {v["verdict"] for v in units.values()}
    assert vs <= {"verified_api", "verified_html", "degraded", "needs_browser",
                  "unreachable"}, vs
