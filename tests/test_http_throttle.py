"""共享节流入口：一处实现，且单元真的在用它。

钉两条：

1. **间隔是行为，不是常量**：``throttle`` 必须真的等到 ``SAME_HOST_GAP_S``，
   用假时钟验证，不靠读代码。
2. **没有第二份实现**：曾经有 19 个单元各抄一份 `_throttle`，抄到后来没人知道
   哪份是"对的那份"。这个测试导入真实单元并检查函数**来自哪个模块**——有人
   再复制一份函数体，这里就会红。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fd_industry_data import http as shared_http  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fake_clock(monkeypatch):
    """Deterministic monotonic + recorded sleeps."""
    state = {"now": 1000.0}
    slept: list[float] = []

    monkeypatch.setattr(shared_http.time, "monotonic", lambda: state["now"])

    def _sleep(seconds: float) -> None:
        slept.append(seconds)
        state["now"] += seconds

    monkeypatch.setattr(shared_http.time, "sleep", _sleep)
    state["slept"] = slept
    return state


def test_throttle_waits_the_configured_gap(fake_clock):
    # First call after a long idle: nothing to wait for.
    stamp = shared_http.throttle(0.0)
    assert fake_clock["slept"] == []
    assert stamp == pytest.approx(fake_clock["now"])

    # Immediately again: must wait the remainder of the gap.
    shared_http.throttle(stamp)
    assert fake_clock["slept"] == [pytest.approx(shared_http.SAME_HOST_GAP_S)]


def test_throttle_does_not_wait_when_gap_already_elapsed(fake_clock):
    stamp = shared_http.throttle(0.0)
    fake_clock["now"] += shared_http.SAME_HOST_GAP_S  # plenty of time passed
    shared_http.throttle(stamp)
    # Exactly at the boundary the subtraction can land a hair under the gap
    # (0.4 - 0.4 == 2.3e-14 in binary floating point), so the call sleeps an
    # infinitesimal amount. Assert "no meaningful wait" rather than "no call".
    assert sum(fake_clock["slept"]) < 1e-6


def test_throttle_ignores_a_future_stamp(fake_clock):
    """A stamp in the future (clock skew) must not sleep a wrong amount."""
    shared_http.throttle(fake_clock["now"] + 5.0)
    assert fake_clock["slept"] == []


@pytest.mark.parametrize(
    "unit, attr",
    [
        ("cninfo-operating", "_sleep_gap"),
        ("cpcaauto-monthly", "_throttle"),
        ("fangchan-market", "_throttle"),
    ],
)
def test_units_use_the_shared_throttle(unit: str, attr: str):
    """The unit's local name must resolve to the shared function, not a copy."""
    path = ROOT / "spiders" / unit / "spider.py"
    assert path.exists(), path
    spec = importlib.util.spec_from_file_location(f"_spider_{unit.replace('-', '_')}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    fn = getattr(module, attr)
    assert fn is shared_http.throttle, (
        f"{unit}.{attr} is a local copy again; import it from "
        f"fd_industry_data.http instead"
    )
