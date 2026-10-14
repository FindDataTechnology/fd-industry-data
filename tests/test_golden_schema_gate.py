"""Golden 样本 schema 规则测试（conformance_gate `golden-schema-invalid`）。

口径与 health.samples.validate_sample 逐字一致：rows_contains 规则只支持
{field, equals} 两个键。`matches` 等不受支持的算子会被重放侧判为畸形规则
（health_verify.py 先 validate_sample 再 replay_sample），单元的 health
gate 因此静默失效——本规则把它挡在合入前。
"""
from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "conformance_gate", REPO / "scripts" / "conformance_gate.py")
gate = importlib.util.module_from_spec(_spec)
sys.modules["conformance_gate"] = gate
_spec.loader.exec_module(gate)


def _unit(tmp_path: Path, golden: str) -> Path:
    """Materialize a minimal repo root with one unit and one golden sample."""
    root = tmp_path / "repo"
    d = root / "spiders" / "my-unit" / "golden"
    d.mkdir(parents=True)
    (d / "001-my-unit.json").write_text(textwrap.dedent(golden), encoding="utf-8")
    return root


def _run(root: Path) -> list[str]:
    return gate._golden_schema_units(root)


VALID_GOLDEN = """\
    {
      "sample_id": "001-my-unit",
      "created": "2026-10-14",
      "target": "run_my_unit",
      "params": {"limit": 5},
      "expect": {
        "min_rows": 1,
        "rows_contains": [{"field": "source", "equals": "my-unit"}]
      },
      "whitelist_fields": ["scraped_at"]
    }
"""

UNSUPPORTED_OP_GOLDEN = """\
    {
      "sample_id": "001-my-unit",
      "created": "2026-10-14",
      "target": "run_my_unit",
      "params": {"limit": 5},
      "expect": {
        "min_rows": 1,
        "rows_contains": [
          {"field": "source", "equals": "my-unit"},
          {"field": "period", "matches": "^\\\\d{4}-\\\\d{2}-\\\\d{2}$"}
        ]
      },
      "whitelist_fields": ["scraped_at"]
    }
"""


class TestGoldenSchema:
    def test_equals_rule_passes(self, tmp_path):
        assert _run(_unit(tmp_path, VALID_GOLDEN)) == []

    def test_unsupported_operator_is_flagged(self, tmp_path):
        hits = _run(_unit(tmp_path, UNSUPPORTED_OP_GOLDEN))
        assert len(hits) == 1
        assert "my-unit" in hits[0]
        assert "field and equals" in hits[0]

    def test_unparseable_golden_is_flagged(self, tmp_path):
        hits = _run(_unit(tmp_path, "{not json"))
        assert len(hits) == 1
        assert "unreadable" in hits[0]

    def test_unit_without_golden_dir_is_ignored(self, tmp_path):
        root = tmp_path / "repo"
        (root / "spiders" / "bare-unit").mkdir(parents=True)
        assert _run(root) == []
