"""``limit_kind`` 语义闸的自证（change fix-crawl-reliability-floor 2.1）。

**为什么要有这条测试**：这条闸决定「哪些源算正在被截断」。它最容易坏成两个方向：

* 把 rolling 也报红 —— 100 个滚动型源天天假红，闸被无视；
* 把 full_set 缺 limit 放成绿 —— 正在截断的源被判成没事，正是本闸要防的。

用例覆盖 judge() 纯函数（不读盘）+ 三种豁免/基线路径。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import check_limit_semantics as cls  # noqa: E402


UNITS = [
    {"name": "roll", "limit_kind": "rolling", "limit": None, "data_period": "day"},
    {"name": "fs-ok", "limit_kind": "full_set", "limit": 5000, "data_period": None},
    {"name": "fs-bad", "limit_kind": "full_set", "limit": None, "data_period": None},
    {"name": "missing", "limit_kind": None, "limit": None, "data_period": None},
    {"name": "bogus", "limit_kind": "sometimes", "limit": None, "data_period": None},
]


def test_rolling_without_limit_is_green():
    r = cls.judge(UNITS, exempt={}, baseline={"fs-bad"})
    assert "roll" not in r["full_set_without_limit"]


def test_full_set_with_limit_is_green():
    r = cls.judge(UNITS, exempt={}, baseline={"fs-bad"})
    assert "fs-ok" not in r["full_set_without_limit"]
    assert "fs-ok" in r["full_set_units"]


def test_full_set_without_limit_is_new_violation():
    r = cls.judge(UNITS, exempt={}, baseline=set())
    assert "fs-bad" in r["new_violations"]


def test_baseline_known_does_not_red():
    r = cls.judge(UNITS, exempt={}, baseline={"fs-bad"})
    assert r["new_violations"] == []
    assert r["known_violations"] == ["fs-bad"]


def test_exemption_silences():
    r = cls.judge(UNITS, exempt={"fs-bad": "已定性未测量"}, baseline=set())
    assert r["full_set_without_limit"] == []


def test_missing_field_is_unassigned_not_defaulted():
    r = cls.judge(UNITS, exempt={}, baseline=set())
    assert "missing" in r["unassigned"]
    # 缺字段不许被默认成 rolling 或 full_set
    assert r["rolling"] == 1 and r["full_set"] == 2


def test_bad_kind_is_reported():
    r = cls.judge(UNITS, exempt={}, baseline=set())
    assert "bogus=sometimes" in r["bad_kind"]


def test_real_repo_field_present_on_lit_units():
    """真盘面：亮着单元应几乎都有 limit_kind（unassigned 只留确证的少数）。"""
    if not cls.SPIDERS.is_dir():
        pytest.skip("无 spiders/")
    units = cls.read_units()
    assert units, "读不到亮着单元"
    r = cls.judge(units, cls.read_exempt(), cls.read_baseline())
    assert len(r["unassigned"]) <= 3, f"unassigned 太多：{r['unassigned']}"
    assert not r["bad_kind"]
