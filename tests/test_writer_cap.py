"""fix-writer-item-cap: 落库帽可配置 + 窗口运行自动提帽。"""
import os
from unittest import mock

import pytest


def test_default_cap_unchanged():
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("FD_WRITER_MAX_ITEMS", None)
        from fd_industry_data import writer
        assert writer._max_items() == 1000


def test_env_override():
    with mock.patch.dict(os.environ, {"FD_WRITER_MAX_ITEMS": "5000"}):
        from fd_industry_data import writer
        assert writer._max_items() == 5000


def test_window_run_lifts_cap_via_env():
    # runner 收到窗口参数时应 setdefault FD_WRITER_MAX_ITEMS=100000（源码语义验证）
    src = (pytest.importorskip("pathlib") and None) or None
    text = __import__("pathlib").Path("fd_industry_data/runner_cli.py").read_text()
    assert 'os.environ.setdefault("FD_WRITER_MAX_ITEMS", "100000")' in text
    assert 'os.environ.setdefault' in text  # setdefault（不覆盖运维显式值）
