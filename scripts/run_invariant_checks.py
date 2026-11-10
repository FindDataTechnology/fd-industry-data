#!/usr/bin/env python3
"""只跑「三条结构不变量」，失败时把**候选**记进账本旁边的候选文件。

为什么不用 pytest
------------------
巡检 runner 镜像（`runner/Dockerfile`）只装了 `python3` + `python3-yaml`，
**没有 pytest、也没有 pip**。而这三条不变量用例本来只用 `unittest`（stdlib）+ `yaml`，
所以用 `python3 -m unittest` 就能跑——不必为它重新出片。这一点是 2026-11-11 核实的，
不是推测（Dockerfile 的 apt 列表 + 容器里 `command -v` 双重确认）。

为什么记「候选」而不是直接记「误报」
------------------------------------
**误报是判断，不是事实。** 用例失败有两种可能：
  ①真的坏了（比如有人删了 `--from-pg`）——那是缺陷，记成误报会污染计数；
  ②合法重构导致文本断言误报——那才是误报。
机器分不出这两者，所以这里只写**候选**（`status: unreviewed`），由人复核后才进
`invariant-fp-ledger.json` 的 `entries`。误报计数**只认已复核条目**，
否则一次真回归就能把"≥2 次就换行为断言"提前触发。

用法
----
    python3 scripts/run_invariant_checks.py            # 跑 + 失败时记候选
    python3 scripts/run_invariant_checks.py --no-record  # 只跑不记（本地调试）

退出码：0 = 三条全过；3 = 有失败（已记候选）。
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

CANDIDATES = REPO / "docs" / "invariant-fp-candidates.jsonl"
CLASS = "ObservationStepContractTests"
MODULE = "test_health_workflow_summary"

#: 测试方法名 → 账本里的不变量标识（改测试函数名要同步改这里，否则候选归错堆）
METHOD_TO_INVARIANT = {
    "test_must_use_from_pg": "from_pg",
    "test_must_run_before_landing_gate": "before_gate",
    "test_landing_gate_stages_the_log": "gate_stages_log",
}


def run_checks() -> tuple[list[str], list[str]]:
    """跑三条不变量，返回 (通过的方法名, 失败的方法名)。"""
    mod = __import__(MODULE)
    cls = getattr(mod, CLASS)
    passed: list[str] = []
    failed: list[str] = []
    for name in sorted(METHOD_TO_INVARIANT):
        case = cls(name)
        buf = io.StringIO()
        res = unittest.TextTestRunner(stream=buf, verbosity=0).run(case)
        (failed if (res.failures or res.errors) else passed).append(name)
    return passed, failed


def record_candidates(failed: list[str], log: str) -> list[dict]:
    """把失败记成待复核候选；同一天同一不变量只记一条（幂等）。"""
    at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    existing = set()
    if CANDIDATES.is_file():
        for line in CANDIDATES.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                doc = json.loads(line)
            except json.JSONDecodeError:
                continue
            existing.add((doc.get("date"), doc.get("invariant")))

    written: list[dict] = []
    for name in failed:
        key = METHOD_TO_INVARIANT[name]
        if (at, key) in existing:
            continue
        rec = {
            "date": at,
            "invariant": key,
            "test": name,
            "status": "unreviewed",
            "why": "用例失败，未判定是真回归还是合法重构导致的误报",
            "evidence": (log or "").strip().splitlines()[-1][:200] if log.strip() else "",
        }
        written.append(rec)
    if written:
        CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
        with CANDIDATES.open("a", encoding="utf-8") as fh:
            for rec in written:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return written


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-record", action="store_true", help="只跑不记候选")
    args = ap.parse_args(argv)

    passed, failed = run_checks()
    for name in passed:
        print(f"  ✓ {METHOD_TO_INVARIANT[name]:16} {name}")
    for name in failed:
        print(f"  ✗ {METHOD_TO_INVARIANT[name]:16} {name}")

    if not failed:
        print("三条结构不变量全部通过。")
        return 0

    print("", "检测到失败：这可能是真回归，也可能是合法重构导致的文本断言误报 —— "
              "机器分不出，已记为**待复核候选**，不进误报计数。", sep="\n")
    if args.no_record:
        print("（--no-record：未写候选文件）")
        return 3
    written = record_candidates(failed, "")
    for rec in written:
        print(f"  候选已记：{rec['date']} {rec['invariant']}"
              f"（复核后确认是误报，再登记进 docs/invariant-fp-ledger.json）")
    if not written:
        print("  （今天已记过相同候选，未重复）")
    return 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
