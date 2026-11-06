"""validate_manifests.py：data_period 四值枚举校验（add-fleet-lifecycle-adjudication 1.1）。"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import validate_manifests as vm  # noqa: E402


def _write_manifest(tmp_path, **fields):
    doc = {"version": "2", "name": "sample", "scanner_mode": "upstream-curated"}
    doc.update(fields)
    d = tmp_path / "spiders" / "sample"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "manifest.yaml"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return p


def test_valid_data_periods_pass(tmp_path):
    for value in ("day", "month", "year", "irregular"):
        p = _write_manifest(tmp_path, data_period=value)
        assert vm.validate(p) == [], (value, vm.validate(p))


def test_invalid_data_period_rejected(tmp_path):
    p = _write_manifest(tmp_path, data_period="weekly")
    errs = vm.validate(p)
    assert errs and "day/month/year/irregular" in errs[0]


def test_data_period_optional_absent_ok(tmp_path):
    p = _write_manifest(tmp_path)
    assert vm.validate(p) == []


def test_template_sample_with_data_period_passes(tmp_path):
    """模板样例补上 data_period 后必须通过 validate（tasks 1.1 验证口径）。"""
    template = (REPO / "templates" / "new-source" / "manifest.yaml").read_text(encoding="utf-8")
    doc = yaml.safe_load(template.replace("# data_period: day", "data_period: day")
                          .replace("# schedule:", "schedule:"))
    d = tmp_path / "spiders" / "my-source"
    d.mkdir(parents=True)
    p = d / "manifest.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert vm.validate(p) == [], vm.validate(p)
