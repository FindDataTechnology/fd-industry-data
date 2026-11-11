"""抽取器的写库语义守卫（change fix-concept-freshness-window）。

抽取此前零单测，全部靠真库跑；而这次两条改动恰好是"在真库里看起来成功"的那类：

* `unit` 从 NULL 改成概念声明的单位——真库里两种写法都"跑得通"，只有读侧才看得出缺失；
* `ON CONFLICT` 从 `DO NOTHING` 改成 `DO UPDATE`——值未变时两种写法的插入行数都是 0，
  只有 `fetched_at` 是否前移能区分。

所以这里钉住 SQL 的形状而不是执行它：不需要数据库，也不需要 psycopg2 能连上。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import extract_pharma as ep  # noqa: E402


def test_upsert_refreshes_verification_time():
    """冲突时必须 DO UPDATE，而不是 DO NOTHING。"""
    sql = " ".join(ep.OBS_UPSERT_SQL.split())
    assert "ON CONFLICT ON CONSTRAINT uq_sem_obs DO UPDATE" in sql, (
        "写回退成 DO NOTHING 了：重复运行不会前移 fetched_at，"
        "长周期概念会在首次写入若干天后被永久判为过期"
    )
    assert "fetched_at = now()" in sql
    assert "DO NOTHING" not in sql


def test_upsert_keeps_the_same_conflict_target():
    """冲突目标必须是 uq_sem_obs —— 换键等于换了一张表。"""
    assert "ON CONFLICT ON CONSTRAINT uq_sem_obs" in " ".join(ep.OBS_UPSERT_SQL.split())


def test_upsert_updates_value_and_unit():
    sql = " ".join(ep.OBS_UPSERT_SQL.split())
    assert "value = EXCLUDED.value" in sql, "上游修订（目录换版/入组数修订）会被冻结成旧值"
    assert "unit = EXCLUDED.unit" in sql, "单位补不上，读侧继续拿 null"


def test_unit_map_covers_every_concept():
    """新增概念忘了配单位 → 红，而不是静默写 NULL。"""
    assert set(ep.CONCEPT_UNIT) == set(ep.CONCEPT_ENTITY)


def test_units_match_the_vocabulary():
    """单位取值必须与词表声明一致（count / persons / percent）。"""
    assert ep.CONCEPT_UNIT == {
        "trial.new": "count",
        "trial.enrollment.planned": "persons",
        "catalog.drug.entries": "count",
        "catalog.drug.class_a_share": "percent",
        "trial.new.by_phase": "count",
        "catalog.drug.entries.by_section": "count",
    }


# ── 构成维度（change expand-pharma-structure-concepts）─────────────────────────

import pytest  # noqa: E402


@pytest.mark.parametrize("raw,expected", [
    ("PHASE1", "PHASE1"),
    ("phase2", "PHASE2"),                      # 大小写不敏感
    ("", "UNSPECIFIED"),                        # 上游没填
    (None, "UNSPECIFIED"),
    ("NA", "NOT_APPLICABLE"),                   # 明确不适用（≠ 没有值）
    ("PHASE1,PHASE2", "PHASE1_PHASE2"),         # 复合值独立成桶，不拆、不归约
    ("PHASE2, PHASE3", "PHASE2_PHASE3"),
])
def test_phase_code_is_faithful(raw, expected):
    assert ep.phase_code(raw) == expected


def test_phase_and_unspecified_are_distinct():
    """空值与 NA 必须落进不同取值：'缺数'与'不适用'是两回事。"""
    assert ep.phase_code("") != ep.phase_code("NA")


def test_unknown_phase_tokens_are_reported_not_dropped():
    assert ep.unknown_phase_tokens("PHASE3,PHASE9") == ["PHASE9"]
    assert ep.unknown_phase_tokens("PHASE1") == []
    assert ep.phase_code("PHASE3,PHASE9") == "PHASE3_PHASE9"  # 仍单列，不丢弃


@pytest.mark.parametrize("raw,expected", [
    ("西药", "western"), ("中成药", "patent_cn"),
    ("谈判药品", "negotiated"), ("中药饮片", "herbal_slice"),
])
def test_section_code_mapping(raw, expected):
    assert ep.section_code(raw) == expected


def test_unknown_section_is_fatal():
    with pytest.raises(SystemExit):
        ep.section_code("生物制品")


def _row(code, date, value):
    return (code, "x", 1, date, "month", str(value))


def test_sum_invariant_passes_when_parts_add_up():
    ep.check_dimension_sums([
        _row("trial.new", "2026-09-01", 10),
        _row("trial.new.by_phase", "2026-09-01", 7),
        _row("trial.new.by_phase", "2026-09-01", 3),
        ("catalog.drug.entries", "x", 1, "2025-01-01", "year", "100"),
        ("catalog.drug.entries.by_section", "x", 1, "2025-01-01", "year", "60"),
        ("catalog.drug.entries.by_section", "x", 1, "2025-01-01", "year", "40"),
    ])


@pytest.mark.parametrize("parts,total,why", [
    ([7], 10, "漏分类 -> 和偏小"),
    ([7, 3, 1], 10, "重复计数 -> 和偏大"),
])
def test_sum_invariant_goes_red(parts, total, why):
    rows = [_row("trial.new", "2026-09-01", total)]
    rows += [_row("trial.new.by_phase", "2026-09-01", p) for p in parts]
    with pytest.raises(SystemExit):
        ep.check_dimension_sums(rows)


def test_new_concepts_are_wired_into_unit_and_entity_maps():
    for code in ("trial.new.by_phase", "catalog.drug.entries.by_section"):
        assert code in ep.CONCEPT_UNIT and code in ep.CONCEPT_ENTITY
