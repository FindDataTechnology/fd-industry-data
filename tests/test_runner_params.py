"""fd-runner --params 通道测试（平台级修复：声明参数此前在生产不可达）。

49 个单元声明了 limit 之外的参数，但 dispatcher 只转发 limit —— 这些参数
在生产里从未生效。本组测试锁定修复后的契约：
声明参数透传、未声明参数丢弃不炸、坏 JSON 降级、无参保持原行为。
"""
from __future__ import annotations

import pytest

from fd_industry_data.runner_cli import _extra_params


def _fn(limit: int = 10, scope: str = "monthly", **kw):
    return []


def _fn_no_kwargs(limit: int = 10, scope: str = "monthly"):
    return []


class TestExtraParams:
    def test_declared_param_passes_through(self):
        assert _extra_params(_fn, '{"scope": "annual"}') == {"scope": "annual"}

    def test_limit_never_duplicated(self):
        """limit 由专用 flag 传，不该再出现在 extra 里（否则 TypeError）。"""
        out = _extra_params(_fn, '{"limit": 5, "scope": "annual"}')
        assert "limit" not in out
        assert out == {"scope": "annual"}

    def test_undeclared_key_dropped_not_fatal(self, capsys):
        out = _extra_params(_fn_no_kwargs, '{"nope": 1, "scope": "annual"}')
        assert out == {"scope": "annual"}
        assert "nope" in capsys.readouterr().err

    def test_malformed_json_degrades(self, capsys):
        assert _extra_params(_fn, "{oops") == {}
        assert "not valid JSON" in capsys.readouterr().err

    def test_non_object_json_degrades(self, capsys):
        assert _extra_params(_fn, '["a", "b"]') == {}
        assert "must be a JSON object" in capsys.readouterr().err

    def test_none_and_empty(self):
        assert _extra_params(_fn, None) == {}
        assert _extra_params(_fn, "") == {}

    def test_all_undeclared_yields_empty(self):
        assert _extra_params(_fn_no_kwargs, '{"a": 1, "b": 2}') == {}

    def test_nested_values_preserved(self):
        """参数值可以是任意 JSON（如 un-comtrade 的多段筛选）。"""

        def _fn_rich(limit: int = 10, years_back: int = 1, flags=None, nested=None):
            return []

        raw = '{"years_back": 3, "flags": ["x", "y"], "nested": {"k": 1}}'
        out = _extra_params(_fn_rich, raw)
        assert out["years_back"] == 3
        assert out["flags"] == ["x", "y"]
        assert out["nested"] == {"k": 1}

    def test_only_named_params_accepted(self, capsys):
        """``**kwargs`` 不算声明——只认签名里显式命名的参数。

        这是刻意的保守口径：``**kw`` 会吞下任何拼错的键，使 manifest 与代码
        漂移静默通过；显式签名才能让漂移被日志暴露。
        """
        assert _extra_params(_fn, '{"scope": "annual"}') == {"scope": "annual"}
        assert _extra_params(_fn, '{"typo": 1}') == {}
        assert "typo" in capsys.readouterr().err

    def test_signature_failure_is_safe(self):
        """不可内省的可调用对象不应抛错（C 实现/内建）。"""
        assert _extra_params(print, '{"scope": "annual"}') == {}
