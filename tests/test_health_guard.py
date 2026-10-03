"""口径守卫单测：golden 重放 / 序列校验 / 分诊 / 样本 schema。

全部离线：真实执行路径用 tmp_path 造单元，网络路径用注入 call_entry。
风格对齐 tests/test_site_contract.py（sys.path.insert 后 import 包成员）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.health.guard import (  # noqa: E402
    ReplayResult,
    SequenceResult,
    check_sequence,
    classify_failure,
    load_unit_entry,
    replay_sample,
)
from fd_industry_data.health.samples import load_samples, validate_sample  # noqa: E402


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _valid_sample(**overrides) -> dict:
    sample = {
        "sample_id": "001-demo",
        "created": "2026-10-03",
        "target": "run_demo",
        "params": {"limit": 5},
        "expect": {"min_rows": 1, "rows_contains": [{"field": "series_id", "equals": "GDPC1"}]},
        "whitelist_fields": ["scraped_at", "timestamp", "url"],
        "sequence": {"key_field": "series_id", "value_field": "value", "tolerance_pct": 1.0},
    }
    sample.update(overrides)
    return sample


def _make_unit(tmp_path: Path, body: str) -> Path:
    unit = tmp_path / "demo-unit"
    unit.mkdir()
    (unit / "spider.py").write_text(body, encoding="utf-8")
    return unit


# --------------------------------------------------------------------------
# replay: all green / min_rows / rows_contains / exception
# --------------------------------------------------------------------------

def test_replay_all_green_with_injected_entry(tmp_path):
    sample = _valid_sample()
    rows = [{"series_id": "GDPC1", "value": 100.0, "scraped_at": "t"}]
    calls = []

    def fake_entry(target, params):
        calls.append((target, params))
        return rows

    result = replay_sample(tmp_path, sample, call_entry=fake_entry)

    assert isinstance(result, ReplayResult)
    assert result.ok is True
    assert result.diffs == []
    assert result.rows == 1
    assert result.sample_id == "001-demo"
    assert calls == [("run_demo", {"limit": 5})]


def test_replay_min_rows_mismatch(tmp_path):
    sample = _valid_sample(expect={"min_rows": 3, "rows_contains": []})
    result = replay_sample(
        tmp_path, sample, call_entry=lambda target, params: [{"series_id": "GDPC1"}]
    )
    assert result.ok is False
    assert result.rows == 1
    assert any("min_rows" in d and "3" in d for d in result.diffs)


def test_replay_rows_contains_mismatch(tmp_path):
    sample = _valid_sample()
    rows = [{"series_id": "OTHER", "value": 1}]
    result = replay_sample(tmp_path, sample, call_entry=lambda t, p: rows)
    assert result.ok is False
    assert result.rows == 1
    assert any("GDPC1" in d and "series_id" in d for d in result.diffs)


def test_replay_rows_contains_stringified_comparison(tmp_path):
    sample = _valid_sample(
        expect={"min_rows": 1, "rows_contains": [{"field": "value", "equals": "3.14"}]}
    )
    result = replay_sample(tmp_path, sample, call_entry=lambda t, p: [{"value": 3.14}])
    assert result.ok is True, result.diffs


def test_replay_entry_exception_is_captured(tmp_path):
    sample = _valid_sample()

    def boom(target, params):
        raise RuntimeError("upstream 503")

    result = replay_sample(tmp_path, sample, call_entry=boom)
    assert result.ok is False
    assert result.rows == 0
    assert result.sample_id == "001-demo"
    assert any("RuntimeError" in d and "upstream 503" in d for d in result.diffs)


def test_replay_non_list_return_is_captured(tmp_path):
    sample = _valid_sample()
    result = replay_sample(tmp_path, sample, call_entry=lambda t, p: {"not": "a list"})
    assert result.ok is False
    assert any("list" in d for d in result.diffs)


def test_replay_default_path_loads_real_unit(tmp_path):
    """不注入 call_entry：真实 importlib 加载 tmp 单元并执行入口。"""
    unit = _make_unit(
        tmp_path,
        "def run_demo(limit=5):\n"
        "    return [{'series_id': 'GDPC1', 'value': float(limit)}][:limit]\n",
    )
    sample = _valid_sample(expect={"min_rows": 1})
    result = replay_sample(unit, sample)
    assert result.ok is True, result.diffs
    assert result.rows == 1


def test_load_unit_entry_uses_unique_module_name_and_errors(tmp_path):
    unit = _make_unit(
        tmp_path,
        "def run_demo(limit=5):\n    return [{'series_id': 'X'}]\n",
    )
    entry = load_unit_entry(unit, "run_demo")
    assert entry(limit=1) == [{"series_id": "X"}]
    assert "unit_demo_unit" in sys.modules

    try:
        load_unit_entry(unit, "run_missing")
    except AttributeError as e:
        assert "run_missing" in str(e)
    else:
        raise AssertionError("expected AttributeError for missing entry")

    try:
        load_unit_entry(tmp_path / "nope", "run_demo")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError for missing spider.py")


# --------------------------------------------------------------------------
# sequence: ok / out-of-tolerance / skipped
# --------------------------------------------------------------------------

def test_sequence_within_tolerance():
    sample = _valid_sample()
    rows = [{"series_id": "A", "value": 100.0}, {"series_id": "GDPC1", "value": 100.5}]
    result = check_sequence(sample, rows, {"value": 100.0, "period": "2026-Q1"})
    assert isinstance(result, SequenceResult)
    assert result.ok is True
    assert result.skipped is False
    assert "GDPC1" in result.note


def test_sequence_out_of_tolerance():
    sample = _valid_sample()
    rows = [{"series_id": "GDPC1", "value": 120.0}]
    result = check_sequence(sample, rows, {"value": 100.0})
    assert result.ok is False
    assert result.skipped is False
    assert "diff" in result.note


def test_sequence_skipped_without_reference():
    sample = _valid_sample()
    result = check_sequence(sample, [{"series_id": "GDPC1", "value": 1}], None)
    assert result.ok is True
    assert result.skipped is True


def test_sequence_skipped_without_sequence_section():
    sample = _valid_sample()
    del sample["sequence"]
    result = check_sequence(sample, [], {"value": 1.0})
    assert result.ok is True
    assert result.skipped is True


def test_sequence_explicit_tolerance_overrides_sample():
    sample = _valid_sample()  # sample tolerance 1.0
    rows = [{"series_id": "GDPC1", "value": 103.0}]
    ref = {"value": 100.0}
    assert check_sequence(sample, rows, ref).ok is False
    assert check_sequence(sample, rows, ref, tolerance_pct=5.0).ok is True


def test_sequence_missing_row_is_not_silently_ok():
    sample = _valid_sample()
    result = check_sequence(sample, [{"other": 1}], {"value": 100.0})
    assert result.ok is False
    assert result.skipped is True


def test_sequence_non_numeric_value():
    sample = _valid_sample()
    rows = [{"series_id": "GDPC1", "value": "n/a"}]
    result = check_sequence(sample, rows, {"value": 100.0})
    assert result.ok is False
    assert result.skipped is False


# --------------------------------------------------------------------------
# classify_failure: three branches
# --------------------------------------------------------------------------

def test_classify_failure_three_branches():
    assert classify_failure(True, False) == "ok"
    assert classify_failure(True, True) == "ok"
    assert classify_failure(False, True) == "sample-update-pending-human"
    assert classify_failure(False, False) == "repair-failed"


# --------------------------------------------------------------------------
# samples: validate_sample / load_samples
# --------------------------------------------------------------------------

def test_validate_sample_accepts_reference_shape():
    assert validate_sample(_valid_sample()) == []


def test_validate_sample_flags_missing_and_wrong_types():
    errors = validate_sample({})
    joined = "\n".join(errors)
    for key in ("sample_id", "target", "created", "params", "expect", "whitelist_fields"):
        assert key in joined, f"{key} not flagged: {errors}"

    errors = validate_sample(
        _valid_sample(
            sample_id=123,
            created="2026/10/03",
            expect={"min_rows": "3"},
            whitelist_fields=["ok", 7],
        )
    )
    joined = "\n".join(errors)
    assert "sample_id" in joined
    assert "created" in joined
    assert "expect.min_rows" in joined
    assert "whitelist_fields[1]" in joined


def test_validate_sample_flags_bad_rows_contains_and_sequence():
    errors = validate_sample(
        _valid_sample(
            expect={"rows_contains": [{"field": "f"}, {"equals": 1}, "x"]},
            sequence={"key_field": "", "value_field": "v", "tolerance_pct": -1},
        )
    )
    joined = "\n".join(errors)
    assert "rows_contains[0]" in joined
    assert "rows_contains[1]" in joined
    assert "rows_contains[2]" in joined
    assert "sequence.key_field" in joined
    assert "tolerance_pct" in joined


def test_validate_sample_requires_expect_content():
    assert "expect" in "\n".join(validate_sample(_valid_sample(expect={})))


def test_load_samples_sorted_and_missing_dir(tmp_path):
    unit = tmp_path / "unit"
    golden = unit / "golden"
    golden.mkdir(parents=True)
    for name, sid in (("002-b.json", "002-b"), ("001-a.json", "001-a")):
        (golden / name).write_text(
            json.dumps(_valid_sample(sample_id=sid)), encoding="utf-8"
        )
    docs = load_samples(unit)
    assert [d["sample_id"] for d in docs] == ["001-a", "002-b"]
    assert load_samples(tmp_path / "no-such-unit") == []


def test_load_samples_rejects_malformed_json(tmp_path):
    unit = tmp_path / "unit"
    golden = unit / "golden"
    golden.mkdir(parents=True)
    (golden / "001-bad.json").write_text("{not json", encoding="utf-8")
    try:
        load_samples(unit)
    except ValueError as e:
        assert "001-bad.json" in str(e)
    else:
        raise AssertionError("expected ValueError for malformed golden json")