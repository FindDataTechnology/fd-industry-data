"""health_verify 生成单适配：缺失单元结构化失败 + 样本强制（全离线）。

载入 scripts/health_verify.py（importlib）并以 tmp 仓驱动 build_report。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location("health_verify_mod", REPO / "scripts" / "health_verify.py")
hv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hv)

from fd_industry_data.health.ticket import dump_ticket, new_ticket  # noqa: E402


def _mk_repo(tmp_path, with_unit=False, with_sample=False):
    repo = tmp_path / "repo"
    if with_unit:
        u = repo / "spiders" / "nmc-weather"
        u.mkdir(parents=True)
        (u / "manifest.yaml").write_text(
            'version: "1"\nname: nmc-weather\nfunctions:\n'
            "  - command: run_nmc_weather\n    frequency: hourly\n",
            encoding="utf-8",
        )
        (u / "spider.py").write_text(
            'def run_nmc_weather(limit: int = 5):\n    return [{"ok": 1}]\n',
            encoding="utf-8",
        )
        if with_sample:
            g = u / "golden"
            g.mkdir()
            (g / "001-nmc-weather.json").write_text(json.dumps({
                "sample_id": "001-nmc-weather",
                "created": "2026-10-04",
                "target": "run_nmc_weather",
                "params": {"limit": 1},
                "expect": {"min_rows": 1, "rows_contains": [{"field": "ok", "equals": "1"}]},
                "whitelist_fields": [],
            }), encoding="utf-8")
    return repo


def _gen_ticket(tmp_path, name="t.yaml"):
    doc = new_ticket(
        source="nmc-weather", unit="spiders/nmc-weather/", kind="generate",
        brief={"source_urls": ["https://www.nmc.cn/x"], "expectations": "实况数据"},
        evidence={}, verify_commands=["x"], golden_paths=["spiders/nmc-weather/golden/"],
    )
    p = tmp_path / name
    p.write_text(dump_ticket(doc), encoding="utf-8")
    return p


def test_missing_unit_structured_failure(tmp_path):
    repo = _mk_repo(tmp_path)  # 单元尚未建立
    p = _gen_ticket(tmp_path)
    r = hv.build_report(p, repo, run_network=False, run_gate=False)
    assert r["verdict"] == "failed" and r["unit_exists"] is False
    assert "单元未建立" in r["reason"]          # 结构化失败而非异常


def test_generate_requires_samples(tmp_path):
    repo = _mk_repo(tmp_path, with_unit=True)  # 有单元、无样本
    p = _gen_ticket(tmp_path)
    r = hv.build_report(p, repo, run_network=False, run_gate=False)
    assert r["samples_required"] is True
    assert any(s["ok"] is False and "缺少 golden 样本" in s["diffs"][0] for s in r["samples"])


def test_generate_with_sample_ok(tmp_path):
    repo = _mk_repo(tmp_path, with_unit=True, with_sample=True)
    p = _gen_ticket(tmp_path)
    r = hv.build_report(p, repo, run_network=False, run_gate=False)
    assert r["samples_required"] is True
    assert r["samples"] and all(s["ok"] for s in r["samples"])


def test_repair_declared_golden_missing_is_red(tmp_path):
    # 修复单声明了 golden 但样本被删 → 红（堵"删样本自证"）
    repo = _mk_repo(tmp_path, with_unit=True)
    doc = new_ticket(source="nmc-weather", unit="spiders/nmc-weather/", evidence={},
                     verify_commands=["x"], category="structure",
                     golden_paths=["spiders/nmc-weather/golden/"])
    p = tmp_path / "r.yaml"
    p.write_text(dump_ticket(doc), encoding="utf-8")
    r = hv.build_report(p, repo, run_network=False, run_gate=False)
    assert r["samples_required"] is True
    assert any(s["ok"] is False for s in r["samples"])


def test_repair_without_golden_unchanged(tmp_path):
    # 未声明 golden 的修复单：无样本不判红（存量行为不变）
    repo = _mk_repo(tmp_path, with_unit=True)
    doc = new_ticket(source="nmc-weather", unit="spiders/nmc-weather/", evidence={},
                     verify_commands=["x"], category="structure")
    p = tmp_path / "r2.yaml"
    p.write_text(dump_ticket(doc), encoding="utf-8")
    r = hv.build_report(p, repo, run_network=False, run_gate=False)
    assert r["samples_required"] is False and r["samples"] == []