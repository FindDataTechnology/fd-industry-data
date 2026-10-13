#!/usr/bin/env python3
"""Manifest 命令漂移检查（只读）。

对每个 spiders/<slug>/manifest.yaml：
1. 尝试导入 spiders/<slug>/spider.py（失败=单元已损坏，报告）；
2. 校验 functions[].command 指向真实存在的符号（字符串形态的函数条目按签名解析）；
3. 提示平台约定入口 run_<slug>(limit) 是否存在；
4. **校验入口签名满足 runner 调用契约**（新 schema 单元）：runner 以
   ``fn(limit=N, **params)`` 调用，故入口必须接受 ``limit`` 或带 ``**kwargs``。

第 4 条的由来（2026-10-13）：给 fao-food-price 加 scope 参数时发现 dispatcher
只转发 limit，于是补了 --params 通道；顺着这条线审计才看到 4 个单元
（gtarsc/hqcdc/intrinio/isee）的入口是 ``(urls=None, save=True)``——**不接受
limit**，一旦点亮就会 TypeError 崩。它们当时都在休眠，所以没人发现。本检查把
这类「manifest 说能跑、签名说不能」的漂移变成可复跑的 lint。

口径：只对**新 schema**（有 functions）单元校验；旧 schema（commands/
function_definitions，如 eastmoney-data/worldsteel）走各自的 shell 命令，不在
本契约内。在产单元违规 = 非零退出；休眠单元违规 = 提示不拦（激活前必修）。
只读，不写任何文件。
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
DORMANT_LIST = REPO / "scripts" / "runner-contract-dormant.txt"


def _dormant_known() -> set[str]:
    """已登记的休眠违契单元（激活前必修，报告为 HINT 而非 DRIFT）。"""
    if not DORMANT_LIST.is_file():
        return set()
    return {ln.strip() for ln in DORMANT_LIST.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")}


def _load_unit_module(spider: Path, slug: str):
    modname = f"manifest_check_{slug.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(modname, spider)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


def _runner_contract_violation(mod, slug: str) -> str | None:
    """入口是否满足 runner 调用契约 fn(limit=..., **params)。None = 满足。

    runner 恒定以 ``limit`` 关键字调用入口；入口既不收 limit 也无 **kwargs
    时必然 TypeError。返回人类可读的违规描述。
    """
    import inspect

    us = slug.replace("-", "_")
    fn = getattr(mod, f"run_{us}", None)
    if fn is None:
        runs = [n for n in dir(mod)
                if n.startswith("run_") and callable(getattr(mod, n))]
        fn = getattr(mod, runs[0]) if len(runs) == 1 else None
    if fn is None or not callable(fn):
        return None  # 入口缺失已由上面的 command 检查覆盖
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return None
    if "limit" in sig.parameters:
        return None
    if any(v.kind == inspect.Parameter.VAR_KEYWORD for v in sig.parameters.values()):
        return None
    return (f"入口 {fn.__name__}{sig} 不接受 limit 且无 **kwargs——"
            f"runner 以 fn(limit=...) 调用，点亮即 TypeError")


def _command_name(entry) -> str:
    if isinstance(entry, dict):
        return str(entry.get("command", ""))
    if isinstance(entry, str):
        return re.split(r"[(\s]", entry.strip(), maxsplit=1)[0]
    return ""


def main() -> int:
    problems: list[str] = []
    hints: list[str] = []
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
        # 第 4 条：入口签名须满足 runner 契约（仅新 schema；休眠单元只提示）
        if funcs:
            viol = _runner_contract_violation(mod, slug)
            if viol:
                if doc.get("schedule"):
                    problems.append(f"{slug}: RUNNER_CONTRACT {viol}")
                elif slug in _dormant_known():
                    hints.append(f"{slug}: RUNNER_CONTRACT(dormant) {viol}")
                else:
                    # 未登记的休眠违契：新出现的漂移，按问题报告
                    problems.append(
                        f"{slug}: RUNNER_CONTRACT(dormant-unregistered) {viol} "
                        f"— 修签名或登记进 {DORMANT_LIST.name}")

        expected = f"run_{slug.replace('-', '_')}"
        if funcs and not hasattr(mod, expected) and "cisa" not in slug:
            # 平台约定入口缺失（cisa 为历史双通道命名，不强制）
            names = [_command_name(f) for f in funcs]
            if not any(n.startswith("run_") for n in names):
                problems.append(f"{slug}: 无任何 run_* 入口（约定 run_{slug.replace('-', '_')}）")
    print(f"manifest-command check: scanned={scanned} problems={len(problems)}")
    for p in problems:
        print("  DRIFT", p)
    for h in hints:
        print("  HINT", h)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())