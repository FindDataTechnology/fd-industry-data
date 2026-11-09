"""Zero-row line split tests (harden-crawl-delivery 3.1).

The point of the split is that the two numbers mean different things and must
never be quoted interchangeably: 1.5% zero-row on the industry line is normal
(snapshot sources occasionally have an empty period), 83% on the law/loader line
is the actual bleed.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "zero_row_by_line.py"
    spec = importlib.util.spec_from_file_location("zero_row", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


zero = _load_script()


def _repo_with(tmp_path, *sources):
    for s in sources:
        d = tmp_path / "spiders" / s
        d.mkdir(parents=True)
        (d / "manifest.yaml").write_text(f"name: {s}\n")
    return tmp_path


def test_manifest_presence_is_industry(tmp_path):
    repo = _repo_with(tmp_path, "gdacs")
    assert zero.classify("gdacs", set(), repo) == "industry"


def test_registered_member_without_manifest_is_law_loader(tmp_path):
    repo = _repo_with(tmp_path)  # 无 spiders 目录
    assert zero.classify("wbgapi", {"wbgapi"}, repo) == "law-loader"


def test_unknown_source_is_other(tmp_path):
    repo = _repo_with(tmp_path)
    assert zero.classify("example-stock", set(), repo) == "other"


def test_manifest_wins_over_scope_membership(tmp_path):
    """本仓有 manifest 就是行业线，即使它同时出现在跨仓排除表里。"""
    repo = _repo_with(tmp_path, "rmfyalk")
    assert zero.classify("rmfyalk", {"rmfyalk"}, repo) == "industry"


def test_summary_keeps_lines_separate(tmp_path):
    repo = _repo_with(tmp_path, "gdacs")
    rows = [("gdacs", 100, 1), ("wbgapi", 10, 10), ("example-stock", 5, 0)]
    s = zero.summarise(rows, {"wbgapi"})
    # 行业线 1/100，law-loader 10/10 —— 混算会得出 11/115 这个没意义的数
    assert s["per_line"]["industry"]["zero_pct"] == 1.0
    assert s["per_line"]["law-loader"]["zero_pct"] == 100.0
    assert s["per_line"]["other"]["zero_pct"] == 0.0
    assert s["total"]["zero_runs"] == 11
    assert s["total"]["ok_runs"] == 115


def test_summary_survives_zero_runs(tmp_path):
    s = zero.summarise([], set())
    assert s["total"]["zero_pct"] == 0.0
    assert all(s["per_line"][name]["zero_pct"] == 0.0 for name in zero.LINES)


def test_top_offenders_are_per_line(tmp_path):
    repo = _repo_with(tmp_path, "gdacs")
    rows = [("gdacs", 10, 3), ("wbgapi", 10, 10), ("akshare", 100, 50)]
    s = zero.summarise(rows, {"wbgapi", "akshare"})
    assert [r["source"] for r in s["per_line"]["industry"]["top"]] == ["gdacs"]
    assert {r["source"] for r in s["per_line"]["law-loader"]["top"]} == {"wbgapi", "akshare"}
    assert s["per_line"]["industry"]["top"][0]["zero_runs"] == 3
