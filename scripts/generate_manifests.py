#!/usr/bin/env python3
"""Manifest generation: shard planning for verified units, protocol validation, collision-safe placement.

Pins the verified units of the revision-2 registry, cuts deterministic shards for
drafting agents, machine-validates draft manifests against fd-open-data-protocol,
and places valid non-colliding drafts into manifests/. Stdlib + yaml (via protocol env).
Spec: openspec/changes/manifest-generation/
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent          # fd-industry-data
REGISTRY = REPO_ROOT / "output" / "l3-reanalysis" / "revision-2" / "registry.json"
DRAFTS = REPO_ROOT / "output" / "manifest-drafts"
MANIFESTS_DIR = REPO_ROOT / "manifests"
MCP_VENV_PY = REPO_ROOT.parent / "fd-open-data-mcp" / ".venv" / "bin" / "python"
OUTCOMES = DRAFTS / "outcomes"
SHARDS = DRAFTS / "shards"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slug_of(unit_path: str) -> str:
    """Unit slug: last path segment, underscores kept, .py stripped."""
    name = unit_path.rstrip("/").split("/")[-1]
    if name.endswith(".py"):
        name = name[:-3]
    return name


# --- plan --------------------------------------------------------------------
def cmd_plan(n_shards: int) -> int:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    pinned = []
    for path, v in sorted(registry["units"].items()):
        if not v["verdict"].startswith("verified"):
            continue
        pinned.append({
            "unit": path,
            "slug": slug_of(path),
            "verdict": v["verdict"],
            "endpoint": v.get("endpoint"),
            "format": v.get("format"),
            "sample": v.get("sample"),
            "parsing_pointers": v.get("parsing_pointers"),
            "evidence": v.get("evidence"),
        })
    if not pinned:
        print("no verified units found in", REGISTRY)
        return 1
    SHARDS.mkdir(parents=True, exist_ok=True)
    (OUTCOMES).mkdir(parents=True, exist_ok=True)
    shards = [[] for _ in range(n_shards)]
    for i, u in enumerate(sorted(pinned, key=lambda x: x["unit"])):
        shards[i % n_shards].append(u)
    names = []
    for i, group in enumerate(shards, 1):
        if not group:
            continue
        name = f"shard-{i:02d}"
        names.append(name)
        (SHARDS / f"{name}.json").write_text(
            json.dumps({"name": name, "units": group}, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print(f"{name}: {len(group)} units")
    (DRAFTS / "plan.json").write_text(json.dumps(
        {"generated_at": utc_now(), "shards": names, "all_units": pinned},
        indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"plan: {len(pinned)} verified units in {len(names)} shards -> {DRAFTS}")
    return 0


# --- validate ----------------------------------------------------------------
def run_with_protocol(code: str) -> tuple[int, str]:
    """Run python code in an env where fd_open_data_protocol is importable."""
    for py in (MCP_VENV_PY, Path(sys.executable)):
        if py.is_file():
            r = subprocess.run([str(py), "-c", code], capture_output=True, text=True)
            if r.returncode == 0 or "fd_open_data_protocol" not in r.stderr:
                return r.returncode, r.stdout + r.stderr
    return 1, "no python env with fd_open_data_protocol found"


VALIDATE_SNIPPET = r"""
import json, sys
from pathlib import Path
from fd_open_data_protocol.loader import load_catalog

results = []
for f in sorted(Path(sys.argv[1]).glob("*.yaml")):
    entry = {"file": f.name, "valid": False, "errors": []}
    try:
        m = load_catalog(f)
        entry["valid"] = m is not None
        if m is None:
            entry["errors"].append("load_catalog returned None")
    except Exception as e:
        entry["errors"].append(f"{type(e).__name__}: {e}")
    results.append(entry)
print(json.dumps(results, ensure_ascii=False))
"""


def cmd_validate() -> int:
    drafts_dir = DRAFTS / "drafts"
    if not drafts_dir.is_dir():
        print("no drafts dir", drafts_dir)
        return 1
    code_full = VALIDATE_SNIPPET.replace("sys.argv[1]", repr(str(drafts_dir)))
    code2, out2 = run_with_protocol(code_full)
    if code2 != 0:
        print("validation runner failed:\n", out2[-1500:])
        return 1
    results = json.loads(out2.strip().splitlines()[-1])
    (DRAFTS / "validation_report.json").write_text(json.dumps(
        {"generated_at": utc_now(), "results": results}, indent=1,
        ensure_ascii=False) + "\n", encoding="utf-8")
    ok = sum(1 for r in results if r["valid"])
    print(f"validated {len(results)} drafts: {ok} valid, {len(results) - ok} invalid")
    for r in results:
        if not r["valid"]:
            print(f"  INVALID {r['file']}: {r['errors']}")
    return 0


# --- merge outcomes + place ----------------------------------------------------
OUTCOME_VOCAB = {"drafted", "skipped"}
SKIP_REASONS = {"metadata-only", "insufficient-knowledge", "no-live-data-shape"}


def cmd_merge_outcomes() -> int:
    plan = json.loads((DRAFTS / "plan.json").read_text(encoding="utf-8"))
    pinned = {u["unit"] for u in plan["all_units"]}
    outcomes: dict[str, dict] = {}
    problems: list[str] = []
    for f in sorted(OUTCOMES.glob("shard-*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        for o in data.get("outcomes", []):
            unit = o.get("unit")
            if unit not in pinned:
                problems.append(f"{f.name}: {unit} not in pinned set")
                continue
            if unit in outcomes:
                problems.append(f"{f.name}: duplicate outcome for {unit}")
                continue
            if o.get("outcome") not in OUTCOME_VOCAB:
                problems.append(f"{f.name}: bad outcome {o.get('outcome')!r} for {unit}")
                continue
            if o["outcome"] == "skipped" and o.get("reason") not in SKIP_REASONS:
                problems.append(f"{f.name}: skipped without valid reason for {unit}")
                continue
            if o["outcome"] == "drafted":
                draft = DRAFTS / "drafts" / f"{o.get('manifest_name', '')}.yaml"
                if not o.get("manifest_name") or not draft.is_file():
                    problems.append(f"{f.name}: drafted but manifest missing for {unit}")
                    continue
            outcomes[unit] = o
    missing = sorted(pinned - set(outcomes))
    problems += [f"missing outcome: {u}" for u in missing]
    if problems:
        print(f"OUTCOME MERGE INCOMPLETE: {len(problems)} problems")
        for p in problems[:20]:
            print(" -", p)
        if missing:
            (DRAFTS / "missing.json").write_text(json.dumps(
                {"units": missing}, indent=1) + "\n", encoding="utf-8")
        return 1

    drafted = {u: o for u, o in outcomes.items() if o["outcome"] == "drafted"}
    validation = {}
    vr = DRAFTS / "validation_report.json"
    if vr.is_file():
        validation = {r["file"]: r for r in
                      json.loads(vr.read_text(encoding="utf-8"))["results"]}
    valid_drafts = {u: o for u, o in drafted.items()
                    if validation.get(f"{o['manifest_name']}.yaml", {}).get("valid")}
    invalid = sorted(set(drafted) - set(valid_drafts))

    report = {
        "generated_at": utc_now(),
        "total": len(outcomes),
        "drafted": len(drafted),
        "valid_drafts": len(valid_drafts),
        "invalid_drafts": invalid,
        "skipped": {u: o["reason"] for u, o in outcomes.items()
                    if o["outcome"] == "skipped"},
    }
    (DRAFTS / "outcome_report.json").write_text(json.dumps(
        report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"outcomes: {len(outcomes)} units | drafted {len(drafted)} "
          f"(valid {len(valid_drafts)}, invalid {len(invalid)}) | "
          f"skipped {report['total'] - len(drafted)}")
    for u, reason in sorted(report["skipped"].items()):
        print(f"  skipped {u}: {reason}")
    if invalid:
        print("invalid drafts (excluded from placement):", invalid)
    return 0


def cmd_place() -> int:
    report_f = DRAFTS / "outcome_report.json"
    if not report_f.is_file():
        print("run --merge-outcomes first")
        return 1
    report = json.loads(report_f.read_text(encoding="utf-8"))
    plan = json.loads((DRAFTS / "plan.json").read_text(encoding="utf-8"))
    slug_by_unit = {u["unit"]: u["slug"] for u in plan["all_units"]}
    vr = {r["file"]: r for r in
          json.loads((DRAFTS / "validation_report.json").read_text(encoding="utf-8"))["results"]}
    placed, collided, already = [], [], []
    seen_targets: dict[str, str] = {}
    outcomes = {}
    for f in sorted(OUTCOMES.glob("shard-*.json")):
        for o in json.loads(f.read_text(encoding="utf-8")).get("outcomes", []):
            outcomes[o["unit"]] = o
    for unit, o in sorted(outcomes.items()):
        if o["outcome"] != "drafted":
            continue
        src = DRAFTS / "drafts" / f"{o['manifest_name']}.yaml"
        if not src.is_file() or not vr.get(src.name, {}).get("valid"):
            continue
        dst = MANIFESTS_DIR / src.name
        if dst.is_file():
            if dst.read_bytes() == src.read_bytes():
                already.append({"unit": unit, "manifest": src.name})
            else:
                collided.append({"unit": unit, "manifest": src.name})
            continue
        if src.name in seen_targets:
            collided.append({"unit": unit, "manifest": src.name,
                             "note": "duplicate draft name"})
            continue
        seen_targets[src.name] = unit
        shutil.copy2(src, dst)
        placed.append({"unit": unit, "manifest": src.name})
    preport = {"generated_at": utc_now(), "placed": placed,
               "collided": collided,
               "already_placed": already}
    (DRAFTS / "placement_report.json").write_text(json.dumps(
        preport, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"placed {len(placed)} manifests, {len(collided)} collisions "
          f"(left for human merge) -> {MANIFESTS_DIR}")
    for c in collided:
        print("  collision:", c)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--shards", type=int, default=4)
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--merge-outcomes", action="store_true")
    ap.add_argument("--place", action="store_true")
    args = ap.parse_args()

    if args.plan:
        return cmd_plan(args.shards)
    if args.validate:
        return cmd_validate()
    if args.merge_outcomes:
        return cmd_merge_outcomes()
    if args.place:
        return cmd_place()
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
