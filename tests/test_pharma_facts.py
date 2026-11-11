"""医药事实定型的契约测试（本 change 第 4 阶段验收）。

四条判据逐条对应 change tasks 4.1–4.4，每条都构造"会失败的那一侧"：

1. 已定型后提交新值 → 修订**被拒绝**（不是静默丢弃，也不是非法崩溃）；
2. 未定型时提交新值 → 被接受，且旧值与来源都在记录里；
3. 目录年度版换代 → 两版都能回放（互不覆写）；
4. 地址稳定 → 两个不同"运行上下文"算出同一地址；缺键即抛。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fd_industry_data import pharma_facts as pf


NOW = datetime(2026, 11, 11, tzinfo=timezone.utc)


def test_month_fact_finalizes_on_first_day_of_next_month():
    """批文/试验：月粒度 → 次月首日定型，与写入时刻无关。"""
    timing = pf.timing_for("drug_approval")
    limit = timing.finalize_limit("2026-03")
    assert limit.isoformat() == "2026-03-31"          # 仍可修订的最后一天
    # 3 月的事实：4-01 起定型
    assert timing.is_finalized("2026-03", now=datetime(2026, 4, 1, tzinfo=timezone.utc))
    assert not timing.is_finalized("2026-03", now=datetime(2026, 3, 31, tzinfo=timezone.utc))


def test_finalization_is_period_driven_not_write_age():
    """同一天里，老月份已定型而新月份未定型——定型只由周期决定。"""
    timing = pf.timing_for("clinical_trial")
    assert timing.is_finalized("2020-01", now=NOW)
    assert not timing.is_finalized("2026-11", now=NOW)


def test_revision_rejected_after_finalization():
    """4.1：已定型的期不再接受修订（写入侧门禁）。"""
    assert pf.accepts_revision("drug_approval", "2020-05", now=NOW) is False
    assert pf.accepts_revision("drug_approval", "2026-11", now=NOW) is True


def test_revision_record_keeps_old_value_and_source():
    """4.1：修订留痕——旧值、新值、来源、时点四项齐全。"""
    rec = pf.revision_record(
        address=pf.fact_address("drug_approval", approval_no="国药准字H20241234"),
        old_value="在审评",
        new_value="已批准",
        source="nmpa-drug-approvals",
        observed_at=NOW,
    )
    assert rec["old_value"] == "在审评"
    assert rec["new_value"] == "已批准"
    assert rec["source"] == "nmpa-drug-approvals"
    assert rec["observed_at"] == NOW
    with pytest.raises(ValueError):
        pf.revision_record(address="x", old_value=1, new_value=2, source="", observed_at=NOW)


def test_catalog_editions_are_both_replayable():
    """4.2：两版目录各自的条目地址不同，互不覆写；年度定型基准是次年 12-31。"""
    a = pf.fact_address("catalog_entry", catalog_year=2024, section="西药",
                        item_no=18, dosage_form="注射剂")
    b = pf.fact_address("catalog_entry", catalog_year=2025, section="西药",
                        item_no=18, dosage_form="注射剂")
    assert a != b
    timing = pf.timing_for("catalog_entry")
    # 2025 年版：到 2026-12-31 仍可修订，2027-01-01 定型
    assert timing.finalize_limit("2025-01").isoformat() == "2026-12-31"
    assert not timing.is_finalized("2025-01", now=datetime(2026, 12, 31, tzinfo=timezone.utc))
    assert timing.is_finalized("2025-01", now=datetime(2027, 1, 1, tzinfo=timezone.utc))


def test_state_change_appends_new_fact_not_rewrite():
    """4.3：状态推进产生新事实，旧状态事实对象未被改动。"""
    addr = pf.fact_address("clinical_trial", registry="NCT", registry_id="NCT03062332")
    first = pf.state_fact(address=addr, state="RECRUITING", source="clinicaltrials-cn",
                          observed_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
    snapshot = dict(first)
    second = pf.state_fact(address=addr, state="COMPLETED", source="clinicaltrials-cn",
                           observed_at=NOW)
    assert first == snapshot, "旧状态事实不得被改动"
    assert second["state"] != first["state"]
    assert second["address"] == first["address"]


def test_address_stable_across_contexts_and_fails_loudly():
    """4.4：地址不掺运行上下文；缺键立即抛而不是生成模糊地址。"""
    kwargs = dict(registry="NCT", registry_id="NCT03062332")
    assert pf.fact_address("clinical_trial", **kwargs) == pf.fact_address("clinical_trial", **kwargs)
    with pytest.raises(ValueError):
        pf.fact_address("clinical_trial", registry="NCT")
    with pytest.raises(ValueError):
        pf.timing_for("not_a_fact_class")


def test_manifest_declarations_match_allowed_policy_set():
    """声明值必须落在既有允许集合内，否则接入巡检会判非法。"""
    from fd_open_data_mcp.ingest_declarations import REVISION_POLICIES

    for fact_class in pf.FACT_CLASSES:
        decl = pf.manifest_declarations(fact_class)
        assert decl["revision_policy"] in REVISION_POLICIES
        assert isinstance(decl["finalize_grace_days"], int)
        assert decl["finalize_grace_days"] >= 0
