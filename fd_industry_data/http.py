"""http — 共享的请求节流（同源礼貌间隔）。

**为什么存在**：节流逻辑此前在 19 个单元里各写一份（``SAME_HOST_GAP`` /
``_throttle`` / 裸 ``time.sleep``），新单元只能照抄。本模块把它做成平台能力。

用法::

    from fd_industry_data.http import SAME_HOST_GAP_S, throttle

    gate = time.monotonic()
    gate = throttle(gate)          # 保证距上次请求 >= SAME_HOST_GAP_S
    resp = await session.get(url)

约定：``throttle`` 接收并返回 ``time.monotonic()`` 时间戳，调用方持有它——
不用全局状态，避免多协程/多单元互相干扰。

既有单元**不强制迁移**（见 tabular-extraction 规格）；新单元使用本入口。
"""
from __future__ import annotations

import time

__all__ = ["SAME_HOST_GAP_S", "throttle"]

# 同源请求最小间隔（秒）。0.4s 是仓内既有约定（见 cninfo-operating）：
# 实测无间隔连发时部分站点返回 200 空壳或直接限流。
SAME_HOST_GAP_S = 0.4


def throttle(last_at: float, gap: float = SAME_HOST_GAP_S) -> float:
    """保证距 ``last_at`` 至少 ``gap`` 秒，返回新的时间戳。

    Args:
        last_at: 上次请求的 ``time.monotonic()`` 值。
        gap: 最小间隔（秒），缺省 ``SAME_HOST_GAP_S``。

    Returns:
        本次调用后的 ``time.monotonic()``，调用方应保存并在下次传入。
    """
    elapsed = time.monotonic() - last_at
    if 0 <= elapsed < gap:
        time.sleep(gap - elapsed)
    return time.monotonic()
