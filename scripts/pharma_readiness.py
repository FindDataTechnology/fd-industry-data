#!/usr/bin/env python3
"""医药域就绪面：把"哪族有源、哪族降级、判据是什么"打印成可核对的结论。

为什么不是把状态写死在脚本里
----------------------------
声明在 ``config/pharma-families.yaml``，本脚本做的是**实探校验**：声明 live 的族
必须真的有单元目录 + manifest + 至少一个 golden；声明 degraded 的族必须**没有**
单元目录（有空壳单元说明有人建了壳，那会让 gate 计数虚增、让就绪面看起来已接入）。

两个方向都会红：
- 声明 live 但探不到单元 → 撒谎（声明≠挂载）。
- 声明 degraded 但目录存在 → 空壳（违反 design D8.3）。

用法
----
    python3 scripts/pharma_readiness.py          # 表格
    python3 scripts/pharma_readiness.py --json   # 机器可读
退出码：0 = 声明与实际一致；1 = 不一致。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
DECL = REPO / "config" / "pharma-families.yaml"


def probe_unit(unit: str | None) -> dict:
    """实探一个单元：目录 / manifest / golden 三件是否齐。"""
    if not unit:
        return {"exists": False, "manifest": False, "goldens": 0}
    path = REPO / unit
    if not path.is_dir():
        return {"exists": False, "manifest": False, "goldens": 0}
    return {
        "exists": True,
        "manifest": (path / "manifest.yaml").is_file(),
        "goldens": len(list((path / "golden").glob("*.json"))),
    }


def shell_target(fam: dict) -> str | None:
    """降级族"若有壳会在哪"：优先声明的 unit，否则按 source 推 ``spiders/<source>``。

    踩过的坑：降级族声明里写的是 ``unit: null``（它本来就没有单元），首版据此探测，
    等于**永远探不到空壳**——负例（手动 mkdir 一个空目录）下脚本照样绿，是自证。
    必须用 source 推算目录名才能查出"有人建了壳"。
    """
    if fam.get("unit"):
        return fam["unit"]
    source = fam.get("source")
    return f"spiders/{source}" if source else None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    doc = yaml.safe_load(DECL.read_text(encoding="utf-8")) or {}
    families = doc.get("families") or []
    rows, problems = [], []
    for fam in families:
        status = fam.get("status")
        if status == "live":
            found = probe_unit(fam.get("unit"))
            target = fam.get("unit")
        else:
            target = shell_target(fam)
            found = probe_unit(target)
        ok = True
        if status == "live":
            ok = found["exists"] and found["manifest"] and found["goldens"] >= 1
            if not ok:
                problems.append(
                    f"{fam['family']}: 声明 live 但实探不齐（exists={found['exists']}, "
                    f"manifest={found['manifest']}, goldens={found['goldens']}）"
                )
        elif status == "degraded":
            ok = not found["exists"]
            if not ok:
                problems.append(
                    f"{fam['family']}: 声明 degraded 但存在单元目录 {target}"
                    "（空壳单元会让 gate 计数虚增，违反 design D8.3）"
                )
            if not fam.get("evidence"):
                problems.append(f"{fam['family']}: 声明 degraded 但没给 evidence（状态码）")
        else:
            ok = False
            problems.append(f"{fam['family']}: 未知 status {status!r}")
        rows.append(
            {
                "family": fam["family"],
                "concept_family": fam.get("concept_family"),
                "status": status,
                "source": fam.get("source"),
                "unit": target,
                "goldens": found["goldens"],
                "consistent": ok,
                "evidence": fam.get("evidence") or [],
                "unblock": fam.get("unblock"),
            }
        )

    if args.json:
        print(json.dumps({"families": rows, "problems": problems}, ensure_ascii=False, indent=2))
    else:
        print(f"{'指标族':<14}{'状态':<10}{'源':<26}{'golden':<7}一致")
        print("-" * 68)
        for r in rows:
            print(f"{r['family']:<14}{r['status']:<10}{str(r['source']):<26}"
                  f"{r['goldens']:<7}{'✓' if r['consistent'] else '✗'}")
        degraded = [r for r in rows if r["status"] == "degraded"]
        if degraded:
            print("\n降级中的族（各带状态码判据）：")
            for r in degraded:
                print(f"  {r['family']} ← {r['source']}")
                for ev in r["evidence"]:
                    print(f"      · {ev}")
                if r["unblock"]:
                    print(f"      解除条件：{r['unblock']}")
        live = sum(1 for r in rows if r["status"] == "live")
        print(f"\n合计：live={live} degraded={len(degraded)} 共 {len(rows)} 族")

    if problems:
        print("\n不一致：")
        for p in problems:
            print(f"  - {p}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
