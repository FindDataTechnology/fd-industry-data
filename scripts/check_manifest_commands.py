#!/usr/bin/env python3
"""Manifest 命令漂移检查（只读）。

对每个 spiders/<slug>/manifest.yaml：
1. 尝试导入 spiders/<slug>/spider.py（失败=单元已损坏，报告）；
2. 校验 functions[].command 指向真实存在的符号（字符串形态的函数条目按签名解析）；
3. 提示平台约定入口 run_<slug>(limit) 是否存在。

背景（2026-10-04 发现）：存量模板把 functions[].command 写成 get_<x>_data，
而平台约定与真实入口是 run_<slug>；本检查把这类元数据漂移变成可复跑的 lint。
只读，不写任何文件；发现漂移时退出码非零。
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def _load_unit_module(spider: Path, slug: str):
    modname = f"manifest_check_{slug.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(modname, spider)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


def _command_name(entry) -> str:
    if isinstance(entry, dict):
        return str(entry.get("command", ""))
    if isinstance(entry, str):
        return re.split(r"[(\s]", entry.strip(), maxsplit=1)[0]
    return ""


def main() -> int:
    problems: list[str] = []
    scanned = 0
    for manifest in sorted((REPO / "spiders").glob("*/manifest.yaml")):
        slug = manifest.parent.name
        spider = manifest.parent / "spider.py"
        if not spider.exists():
            continue
        scanned += 1
        doc = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        funcs = doc.get("functions") or []
        try:
            mod = _load_unit_module(spider, slug)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{slug}: IMPORT_FAIL {type(e).__name__}: {e}")
            continue
        for entry in funcs:
            name = _command_name(entry)
            if not name:
                problems.append(f"{slug}: functions 条目无法解析: {entry!r}")
                continue
            if not hasattr(mod, name):
                real = f"run_{slug.replace('-', '_')}"
                hint = real if hasattr(mod, real) else "(run_<slug> 也不存在!)"
                problems.append(f"{slug}: command '{name}' 不存在；真实入口: {hint}")
        expected = f"run_{slug.replace('-', '_')}"
        if funcs and not hasattr(mod, expected) and "cisa" not in slug:
            # 平台约定入口缺失（cisa 为历史双通道命名，不强制）
            names = [_command_name(f) for f in funcs]
            if not any(n.startswith("run_") for n in names):
                problems.append(f"{slug}: 无任何 run_* 入口（约定 run_{slug.replace('-', '_')}）")
    print(f"manifest-command check: scanned={scanned} problems={len(problems)}")
    for p in problems:
        print("  DRIFT", p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())