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
    }
