#!/usr/bin/env python3
"""凭据面 ↔ requires_env 双向对账（change fix-crawl-reliability-floor）。

三层
────
声明面  ``spiders/*/manifest.yaml`` 的 ``requires_env`` —— 单元运行时读哪些键
登记面  ``config/credential-surface.yaml`` —— 谁不是单元依赖、谁还等着补
值面    ``secrets/fd-industry-source-keys.env`` —— 键的实际取值（不入 git）

双向判据
────────
1. 声明了但值面没有，且不在 ``pending_keys`` ⇒ **红**（该单元上线即 blocked-config）
2. 值面有、无人声明、且不在 ``non_unit_keys`` ⇒ **红**（孤儿键：没有任何单元在用它）
3. 在 ``pending_keys`` 里 ⇒ 提示（等着补，不是不需要）

覆盖面边界（不许藏着）
──────────────────────
本闸只断言**已声明 requires_env 的单元**。一个需要凭据却没声明的单元在这条闸上是
「通过」——那是声明缺失，不是凭据齐备。产物必须给出 ``declared_units / lit_units``，
否则读数方会以为它覆盖了全部源。2026-11-21 实测：10 / 125。

用法::

    python3 scripts/check_credential_declarations.py
    python3 scripts/check_credential_declarations.py --json
    python3 scripts/check_credential_declarations.py --self-test   # 不读盘

退出码：0 无红；1 有红；2 判不出（清单/值面不可读）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SPIDERS = REPO / "spiders"
SURFACE = REPO / "config" / "credential-surface.yaml"
#: 值面在仓外（不入 git）。本地校验用仓库根的 secrets/；集群侧由 secret 注入。
VALUES = REPO.parent / "secrets" / "fd-industry-source-keys.env"

_ENV_ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Z0-9_]+)=", re.M)


def declared() -> dict[str, list[str]]:
    """key -> 声明它的单元名列表。"""
    import yaml

    out: dict[str, list[str]] = {}
    for p in sorted(SPIDERS.glob("*/manifest.yaml")):
        try:
            m = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 - 清单坏了一个不拖垮整表，但它会少一个声明
            continue
        name = m.get("name") or p.parent.name
        for k in (m.get("requires_env") or []):
            out.setdefault(str(k), []).append(name)
    return out


def lit_count() -> int | None:
    """亮着的单元数——覆盖面的分母。"""
    import yaml

    if not SPIDERS.is_dir():
        return None
    n = 0
    for p in SPIDERS.glob("*/manifest.yaml"):
        try:
            m = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        if (m.get("schedule") or "").strip():
            n += 1
    return n


def values(env_file: Path = VALUES) -> set[str]:
    """值面里的键名。只读键名，不读值。"""
    if not env_file.is_file():
        return set()
    return set(_ENV_ASSIGN.findall(env_file.read_text(encoding="utf-8")))


def surface() -> tuple[set[str], dict[str, list[str]]]:
    """返回 (non_unit_keys, pending):pending 是 key -> 声明它的单元。"""
    import yaml

    if not SURFACE.is_file():
        return set(), {}
    d = yaml.safe_load(SURFACE.read_text(encoding="utf-8")) or {}
    non_unit = {str(x.get("key")) for x in (d.get("non_unit_keys") or []) if x.get("key")}
    pending = {str(x.get("key")): list(x.get("declared_by") or [])
               for x in (d.get("pending_keys") or []) if x.get("key")}
    return non_unit, pending


def check(*, decl: dict[str, list[str]], vals: set[str], non_unit: set[str],
          pending: dict[str, list[str]], lit: int | None) -> dict:
    """纯函数：给三个集合，返回红/提示/统计。自证与真跑共用同一条路径。"""
    declared_keys = set(decl)
    missing = sorted(declared_keys - vals)                 # 声明了但值面没有
    hard_missing = [k for k in missing if k not in pending]
    orphan = sorted(vals - declared_keys - non_unit - set(pending))
    pending_present = sorted(k for k in pending if k in decl)
    return {
        "declared_units": len({u for us in decl.values() for u in us}),
        "declared_keys": len(declared_keys),
        "lit_units": lit,
        "values_keys": len(vals),
        "missing_values": hard_missing,      # 红
        "pending": pending_present,          # 提示
        "orphan_values": orphan,             # 红
        "ok": not hard_missing and not orphan,
    }


def self_test() -> int:
    base = dict(decl={"A": ["u1"], "B": ["u2"]}, non_unit=set(),
                pending={}, lit=10)
    cases = [
        (dict(base, vals={"A", "B"}), True, "两边一致"),
        (dict(base, vals={"A"}), False, "声明了 B 但值面没有 ⇒ 红"),
        (dict(base, vals={"A", "B", "X"}), False, "孤儿键 X ⇒ 红"),
        (dict(base, vals={"A", "B", "PW"}, non_unit={"PW"}), True,
         "PW 已登记为非单元键 ⇒ 不红"),
        (dict(decl={"A": ["u1"], "P": ["u1"]}, vals={"A"}, non_unit=set(),
              pending={"P": ["u1"]}, lit=10), True, "pending 键缺失 ⇒ 提示不红"),
    ]
    bad = []
    for kw, want_ok, desc in cases:
        got = check(**kw)
        if got["ok"] != want_ok:
            bad.append(f"  {desc}: ok={got['ok']}（期望 {want_ok}）{got}")
    if bad:
        print("selftest FAILED:\n" + "\n".join(bad), file=sys.stderr)
        return 1
    print(f"selftest OK（{len(cases)} 例：一致 / 缺值 / 孤儿 / 非单元键 / pending）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="check_credential_declarations")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()

    if not SPIDERS.is_dir():
        print("判不出：spiders/ 目录不可用", file=sys.stderr)
        return 2

    d = declared()
    vals = values()
    if not vals:
        # 值面在仓外。读不到时**不许**把「读不到」当成「全都没有」——
        # 那会把每个声明都报成缺值（假红），也会让孤儿检查全空（假绿）。
        print(f"判不出：值面不可读（{VALUES}）——这是本地校验用的镜像，"
              "集群侧由 secret 注入；本地无此文件时本闸不适用", file=sys.stderr)
        return 2
    non_unit, pending = surface()
    report = check(decl=d, vals=vals, non_unit=non_unit, pending=pending, lit=lit_count())

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    print(f"覆盖面：已声明 requires_env 的单元 {report['declared_units']} / "
          f"亮着 {report['lit_units']}（未声明的单元不在本闸范围内）")
    print(f"键：声明 {report['declared_keys']} / 值面 {report['values_keys']}")
    for k in report["missing_values"]:
        print(f"  ✗ 声明了但值面没有：{k}（{', '.join(d[k])}）")
    for k in report["pending"]:
        print(f"  · 待补（不报红）：{k}（{', '.join(pending[k])}）")
    for k in report["orphan_values"]:
        print(f"  ✗ 值面有但无人声明（且未登记为非单元键）：{k}")
    print("结论：" + ("通过" if report["ok"] else "有红"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
