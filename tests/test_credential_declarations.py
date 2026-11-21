"""凭据面 ↔ requires_env 对账闸的自证（change fix-crawl-reliability-floor 1.2）。

**为什么要有这条测试**：这条闸是「声明了但值面没有」与「值面有但无人声明」两个方向
的护栏。它最容易坏成两种静默假绿：

* 值面不可读时把「读不到」当成「全都没有」——那会把每个声明报成缺值（假红），
  或者反过来把孤儿检查清空（假绿）；
* 覆盖面说得比实际大——`declared_units / lit_units` 不写出来，读数方就会以为
  125 个源都被查了，实际只有 10 个。

用例覆盖 check() 纯函数（不读盘）+ 真实盘面上的覆盖边界。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import check_credential_declarations as ccd  # noqa: E402


BASE = dict(decl={"A": ["u1"], "B": ["u2"]}, non_unit=set(), pending={}, lit=10)


def test_consistent_both_directions():
    r = ccd.check(**dict(BASE, vals={"A", "B"}))
    assert r["ok"] and not r["missing_values"] and not r["orphan_values"]


def test_declared_but_missing_from_values():
    r = ccd.check(**dict(BASE, vals={"A"}))
    assert not r["ok"] and r["missing_values"] == ["B"]


def test_orphan_value_is_red():
    r = ccd.check(**dict(BASE, vals={"A", "B", "X"}))
    assert not r["ok"] and r["orphan_values"] == ["X"]


def test_non_unit_key_is_not_orphan():
    r = ccd.check(**dict(BASE, vals={"A", "B", "PW"}, non_unit={"PW"}))
    assert r["ok"], r


def test_pending_key_is_hint_not_red():
    r = ccd.check(decl={"A": ["u1"], "P": ["u1"]}, vals={"A"},
                  non_unit=set(), pending={"P": ["u1"]}, lit=10)
    assert r["ok"] and r["pending"] == ["P"]
    assert r["missing_values"] == []


def test_coverage_boundary_is_reported():
    """覆盖边界必须可见：declared_units 与 lit_units 都在产物里。"""
    r = ccd.check(**dict(BASE, vals={"A", "B"}))
    assert r["declared_units"] == 2 and r["lit_units"] == 10


def test_values_reader_counts_export_form(tmp_path):
    """值面是 `export KEY=` 形态；只读键名、不读值。"""
    p = tmp_path / "keys.env"
    p.write_text("# comment\nexport FOO=1\nBAR=2\n", encoding="utf-8")
    got = ccd.values(p)
    assert got == {"FOO", "BAR"}, "空格与 export 前缀都要认"


def test_real_repo_coverage_is_honest():
    """真盘面：覆盖边界必须如实报出，不许假装覆盖全部亮着单元。"""
    if not ccd.SPIDERS.is_dir():
        pytest.skip("无 spiders/")
    d = ccd.declared()
    lit = ccd.lit_count()
    assert lit is not None and lit > 0
    assert len({u for us in d.values() for u in us}) < lit, (
        "若某天所有亮着单元都声明了 requires_env，这条断言会红——"
        "那时说明覆盖面变全了，应改成本测试而不是放宽断言")
