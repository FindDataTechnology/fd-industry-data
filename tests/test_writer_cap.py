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
    # 路径必须从 __file__ 解析：本用例曾用相对路径 `Path("fd_industry_data/...")`，
    # 于是它的绿红取决于**进程 cwd**——全量跑时前面用例把 cwd 留在 tmp 里就假红
    # （2026-11-10 实测：单跑绿、全跑 FileNotFoundError）。源码侧已修 cwd 泄漏，
    # 这里再把用例自身做成路径无关，两头都不依赖执行顺序。
    from pathlib import Path as _P

    _repo = _P(__file__).resolve().parents[1]
    text = (_repo / "fd_industry_data" / "runner_cli.py").read_text(encoding="utf-8")
    assert 'os.environ.setdefault("FD_WRITER_MAX_ITEMS", "100000")' in text
    assert 'os.environ.setdefault' in text  # setdefault（不覆盖运维显式值）
