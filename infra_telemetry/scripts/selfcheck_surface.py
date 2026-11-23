#!/usr/bin/env python3
"""tool-surface manifest 双向 selfcheck（mcp-surface-contract）。

infra_telemetry 的工具面是闭集，源码里已有 `TOOL_NAMES` frozenset 作单一锚点；
本脚本断言 manifest 与运行时的 `list_tools()` 双向一致，并与 `TOOL_NAMES` 三方一致。

用法：python3 infra_telemetry/scripts/selfcheck_surface.py
退出码：0 通过 / 1 失败。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "tool-surface.json"


def fail(problems):
    print("SELFCHECK FAIL:")
    for p in problems:
        print(f"  - {p}")
    sys.exit(1)


def main() -> None:
    if not MANIFEST.exists():
        fail([f"manifest 缺失：{MANIFEST}（契约：缺失即未上牌）"])
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    levels: dict[str, str] = {}
    for g in m.get("tool_groups", []):
        level = g.get("level")
        if level not in ("commercial", "internal"):
            fail([f"tool_groups.{g.get('group')} 的 level 非法：{level!r}"])
        for t in g.get("tools", []):
            if t in levels and levels[t] != level:
                fail([f"工具 {t} 在多个 group 中级别冲突"])
            levels[t] = level

    sys.path.insert(0, str(REPO))
    from infra_telemetry.server import mcp, TOOL_NAMES

    runtime_set = {t.name for t in asyncio.run(mcp.list_tools())}
    declared = set(levels)
    problems = []
    if declared - runtime_set:
        problems.append(f"manifest 多声明（代码未注册）：{sorted(declared - runtime_set)}")
    if runtime_set - declared:
        problems.append(f"代码多注册（manifest 未声明）：{sorted(runtime_set - declared)}")
    if set(TOOL_NAMES) != runtime_set:
        problems.append(f"源码 TOOL_NAMES 与运行时不一致：{sorted(set(TOOL_NAMES) ^ runtime_set)}")
    if problems:
        fail(problems)
    print(f"SELFCHECK OK：{len(declared)} 工具，commercial={len([x for x in levels if levels[x]=='commercial'])}")


if __name__ == "__main__":
    main()
