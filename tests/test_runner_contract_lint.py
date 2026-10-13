"""runner 调用契约 lint 测试（check_manifest_commands 第 4 条）。

契约：runner 恒定以 fn(limit=N, **params) 调用入口。本组锁定三条口径：
在产违契=硬失败、已登记休眠违契=提示、未登记休眠违契=硬失败（新漂移不可静默）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "check_manifest_commands", REPO / "scripts" / "check_manifest_commands.py")
check = importlib.util.module_from_spec(_spec)
sys.modules["check_manifest_commands"] = check
_spec.loader.exec_module(check)


class _FakeMod:
    """最小模块替身：把入口挂成属性。"""

    def __init__(self, **fns):
        for name, fn in fns.items():
            setattr(self, name, fn)


class TestContractCheck:
    def test_limit_accepted_passes(self):
        def run_x(limit: int = 10):
            return []

        assert check._runner_contract_violation(_FakeMod(run_x=run_x), "x") is None

    def test_var_keyword_passes(self):
        """**kwargs 可吞 limit，契约满足。"""
        def run_x(**_):
            return []

        assert check._runner_contract_violation(_FakeMod(run_x=run_x), "x") is None

    def test_no_limit_no_kwargs_violates(self):
        def run_x(urls=None, save=True):
            return []

        v = check._runner_contract_violation(_FakeMod(run_x=run_x), "x")
        assert v is not None and "limit" in v

    def test_hyphenated_slug_resolves(self):
        def run_my_unit(limit: int = 5):
            return []

        assert check._runner_contract_violation(
            _FakeMod(run_my_unit=run_my_unit), "my-unit") is None

    def test_single_fallback_entry_used(self):
        def run_whatever(limit: int = 5):
            return []

        assert check._runner_contract_violation(
            _FakeMod(run_whatever=run_whatever), "no-such-name") is None

    def test_ambiguous_entries_not_flagged_here(self):
        """多个 run_* 且无约定名：入口缺失由别的检查覆盖，此处不误报。"""
        def run_a(limit: int = 1):
            return []

        def run_b(limit: int = 1):
            return []

        assert check._runner_contract_violation(
            _FakeMod(run_a=run_a, run_b=run_b), "zzz") is None

    def test_no_entry_returns_none(self):
        assert check._runner_contract_violation(_FakeMod(), "zzz") is None

    def test_positional_only_limit_ok(self):
        def run_x(limit, /):
            return []

        assert check._runner_contract_violation(_FakeMod(run_x=run_x), "x") is None


class TestDormantRegistry:
    def test_registry_file_exists_and_parses(self):
        assert check.DORMANT_LIST.is_file()
        names = check._dormant_known()
        assert names, "登记表不应为空（当前有 4 个已知休眠违契单元）"
        assert all("-" in n or n.isidentifier() for n in names)

    def test_registry_excludes_comments(self):
        assert not any(n.startswith("#") for n in check._dormant_known())

    def test_known_four_are_registered(self):
        """四个 legacy scrapling 脚手架单元必须在册（否则会变成硬失败）。"""
        assert {"gtarsc", "hqcdc", "intrinio", "isee"} <= check._dormant_known()
