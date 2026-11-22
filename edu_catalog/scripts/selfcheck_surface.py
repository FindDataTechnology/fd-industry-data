#!/usr/bin/env python3
"""tool-surface manifest 双向 selfcheck（mcp-surface-contract）。

判两件：manifest 声明的工具集 == 运行时注册的工具集（双向），以及每组的 level 合法。
`edu_catalog` 在 hosted/只读两种启动形态下工具面不同，本脚本按默认 build_server()
（全量鉴权面）比对；只读面（read_only=True）的闭合由调用方另行断言。

用法：python3 edu_catalog/scripts/selfcheck_surface.py
退出码：0 通过 / 1 失败。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]  # = edu_catalog/ (manifest 同级)
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

    async def runtime():
        # `edu_catalog` 是 fd-industry-data 下的包：import 需要父目录在 sys.path。
        sys.path.insert(0, str(REPO.parent))
        from edu_catalog.server import build_server

        return {t.name for t in await build_server().list_tools()}

    declared = set(levels)
    runtime_set = asyncio.run(runtime())
    problems = []
    if declared - runtime_set:
        problems.append(f"manifest 多声明（代码未注册）：{sorted(declared - runtime_set)}")
    if runtime_set - declared:
        problems.append(f"代码多注册（manifest 未声明）：{sorted(runtime_set - declared)}")
    # rules 与组级 level 自洽
    rules = m.get("rules", {})
    if set(rules.get("commercial_tools", [])) != {t for t, lv in levels.items() if lv == "commercial"}:
        problems.append("rules.commercial_tools 与组级 level=commercial 的工具集不一致")
    if set(rules.get("internal_tools", [])) != {t for t, lv in levels.items() if lv == "internal"}:
        problems.append("rules.internal_tools 与组级 level=internal 的工具集不一致")
    if problems:
        fail(problems)
    print(f"SELFCHECK OK：{len(declared)} 工具，commercial={len([t for t in levels if levels[t]=='commercial'])}")


if __name__ == "__main__":
    main()
