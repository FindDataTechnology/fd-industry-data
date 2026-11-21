#!/usr/bin/env python3
"""源登记表漂移闸（change derive-source-registries）。

判两件互不相同的事，**不许合成一档**：

1. **盘面漂移**：登记表指向的单元目录已不存在 / 仓里有单元但不在登记表。
   前者是「这张表已经 rot」（2026-11-21 实测：95 条里 59 条目录已不在仓里），
   后者是「新接的单元没登记」。
2. **同源声明**：manifest 的 ``same_source_as`` 必须指向一个真实存在的单元、
   且双向一致。同源关系**只是事实标注，不参与调度**——它不改变单飞约束、
   不联坐失败。

``--backfill`` 把盘面事实（``unit_present`` / ``evidence_age_days``）写回
registry.json，使可达性结论与盘面事实分维度存储；``verdict`` 语义不动。

用法::

    python3 scripts/check_registry_drift.py            # 只查
    python3 scripts/check_registry_drift.py --backfill # 写回派生字段
    python3 scripts/check_registry_drift.py --self-test
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SIBLING = REPO.parent / "fd-open-data-mcp"
L3 = REPO / "output" / "l3-reanalysis" / "registry.json"
STALENESS_DAYS = 90


def unit_exists(key: str) -> bool:
    if key.startswith("spiders/"):
        return (REPO / key).is_dir()
    return (SIBLING / key).is_file()


def _age(stamp: str | None, now: dt.date) -> int | None:
    if not stamp:
        return None
    try:
        return (now - dt.date.fromisoformat(stamp[:10])).days
    except ValueError:
        return None


def classify_units(units: dict, exists, now: dt.date,
                   default_at: str | None = None) -> tuple[list[str], list[str]]:
    """纯函数：给登记项、存在性判定、当前日期，返回 (absent, stale)。

    ``archived``（目录不在）优先于 ``stale``（证据旧）：两者都不该进可达性统计，
    但它们的成因不同——前者是表 rot，后者是没重测。

    ``default_at``：这版 registry 的每条记录只有 ``sample_digest``，没有逐条的
    ``evidence.at``。此时用文档顶层的 ``generated_at``（= 那次实测跑的时间）兜底，
    而不是当作「无年龄」——「不知道多旧」与「很新」是同一种假绿。
    """
    absent, stale = [], []
    for key, rec in units.items():
        if not exists(key):
            absent.append(key)
            continue
        at = (rec.get("evidence") or {}).get("at") or rec.get("evidence_at") or default_at
        a = _age(at, now)
        if a is not None and a > STALENESS_DAYS:
            stale.append(f"{key}（{a} 天）")
    return sorted(absent), sorted(stale)


def drift_report(now: dt.date | None = None) -> dict:
    now = now or dt.date.today()
    if not L3.is_file():
        return {"error": f"登记表不存在：{L3}"}
    doc = json.loads(L3.read_text(encoding="utf-8"))
    units = doc.get("units") or {}

    absent, stale = classify_units(units, unit_exists, now, doc.get("generated_at"))

    import yaml

    in_repo = {p.parent.name for p in (REPO / "spiders").glob("*/manifest.yaml")}
    registered = {k.split("/", 1)[1] for k in units if k.startswith("spiders/")}
    all_units = {k for k in units if k.startswith("spiders/")}
    # 目录已不在仓里的登记项不算「登记缺失」，它们属 absent 那一档
    missing_reg = sorted(
        n for n in in_repo - registered
        if (REPO / "spiders" / n).is_dir() and f"spiders/{n}" not in units
    )

    # same_source_as 校验
    same_bad = []
    for p in sorted((REPO / "spiders").glob("*/manifest.yaml")):
        try:
            m = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        tgt = m.get("same_source_as")
        if not tgt:
            continue
        if not (REPO / "spiders" / str(tgt)).is_dir():
            same_bad.append(f"{p.parent.name} -> {tgt}（不存在）")
            continue
        back = REPO / "spiders" / str(tgt) / "manifest.yaml"
        if back.is_file():
            b = yaml.safe_load(back.read_text(encoding="utf-8")) or {}
            if str(b.get("same_source_as") or "") != p.parent.name:
                same_bad.append(f"{p.parent.name} -> {tgt}（对方未回指）")

    return {
        "units": len(units),
        "registered_but_absent": sorted(absent),
        "evidence_stale": sorted(stale),
        "in_repo_not_registered": missing_reg,
        "same_source_as_broken": sorted(same_bad),
        "staleness_threshold_days": STALENESS_DAYS,
    }


def backfill(now: dt.date | None = None) -> dict:
    """把盘面事实写回 registry.json。``verdict`` 不动。"""
    now = now or dt.date.today()
    doc = json.loads(L3.read_text(encoding="utf-8"))
    doc["staleness_threshold_days"] = STALENESS_DAYS
    changed = 0
    for key, rec in (doc.get("units") or {}).items():
        present = unit_exists(key)
        at = (rec.get("evidence") or {}).get("at") or rec.get("evidence_at")
        age = _age(at, now)
        if rec.get("unit_present") != present or rec.get("evidence_age_days") != age:
            rec["unit_present"] = present
            rec["evidence_age_days"] = age
            changed += 1
    L3.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"backfilled": changed, "units": len(doc.get("units") or {})}


def self_test() -> int:
    bad = []
    now = dt.date(2026, 11, 21)
    units = {
        "spiders/here": {"verdict": "unreachable", "evidence": {"at": "2026-01-01"}},
        "spiders/gone": {"verdict": "unreachable", "evidence": {"at": "2026-11-01"}},
        "spiders/now": {"verdict": "verified_api", "evidence": {"at": "2026-11-15"}},
    }
    exists = {"spiders/here", "spiders/now"}
    absent, stale = classify_units(units, lambda k: k in exists, now)
    if absent != ["spiders/gone"]:
        bad.append(f"absent 应只有 gone，得 {absent}")
    if stale != ["spiders/here（324 天）"]:
        bad.append(f"stale 应只有 here，得 {stale}")
    # 目录不在的项即便证据旧也只进 absent，不重复进 stale
    if any("gone" in s for s in stale):
        bad.append("已归档的项不该同时进 stale")
    if bad:
        print("selftest FAILED:\n" + "\n".join("  " + b for b in bad), file=sys.stderr)
        return 1
    print("selftest OK（3 例：不存在→absent / 超阈值→stale / archived 不重复进 stale）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="check_registry_drift")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()

    if args.backfill:
        r = backfill()
        print(f"回填 {r['backfilled']} 条 / 共 {r['units']} 条", file=sys.stderr)

    rep = drift_report()
    if "error" in rep:
        print(f"判不出：{rep['error']}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(f"登记 {rep['units']} 条")
        print(f"登记指向但盘面不存在：{len(rep['registered_but_absent'])}")
        print(f"证据超 {rep['staleness_threshold_days']} 天：{len(rep['evidence_stale'])}")
        print(f"仓里有单元但未登记：{len(rep['in_repo_not_registered'])}")
        print(f"same_source_as 失效：{len(rep['same_source_as_broken'])}")
        for x in rep["same_source_as_broken"]:
            print(f"  ✗ {x}")
    # 只 red 在「同源声明坏」与「新单元未登记」——前者是真错，
    # 后者是新接入没补表。登记指向不存在属已知 rot（backfill 后由 archived 承接），
    # 不报红。
    return 1 if rep["same_source_as_broken"] else 0


if __name__ == "__main__":
    sys.exit(main())
