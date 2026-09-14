#!/usr/bin/env python3
"""Mechanical conformance gate over generated crawler code.

Read-only single-pass validator: imports triage_audit's discovery functions so
auditor and enforcer share the same heuristics and ignore lists (they cannot
drift apart). Fails with one line per violation, each naming its fix, when:
a spider unit exists outside spiders/<slug>/ (including misspelled directories),
a standard-layout unit lacks manifest.yaml, a loose *_spider.py exists outside
a unit directory, an empty non-standard top-level directory exists, or two
adapters differ only by dash-versus-underscore naming. Writes nothing, ever.

Spec: openspec/changes/estate-hygiene/ (capability estate-hygiene).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import triage_audit  # noqa: E402

MANIFEST_PROTOCOL = "output/manifest-drafts/MANIFEST-PROTOCOL.md"

# violation type -> one-line fix
FIXES = {
    "unit-outside-spiders": "move to spiders/<slug>/",
    "missing-manifest": f"draft per {MANIFEST_PROTOCOL}",
    "loose-spider-file": "move into its unit dir",
    "empty-nonstandard-dir": "delete",
    "adapter-dash-underscore-pair": "keep one form",
}


def collect(roots: list[Path]) -> tuple[list[tuple[str, str]], int]:
    """Return (violations as (type, path), scanned unit count). Zero writes."""
    violations: list[tuple[str, str]] = []
    unit_count = 0
    adapters: dict[str, list[tuple[Path, dict]]] = {}

    for root in roots:
        units, _litter, empty_dirs, _codeless = triage_audit.discover_units(root)
        triage_audit.manifest_coverage(units, root)
        unit_count += len(units)
        for d in empty_dirs:
            violations.append(("empty-nonstandard-dir", f"{root.name}/{d}"))
        for u in units:
            path = f"{root.name}/{u['path']}"
            if u["kind"] == "spider_dir":
                if not u["path"].startswith("spiders/"):
                    violations.append(("unit-outside-spiders", path))
                elif u.get("manifest_present") is False:
                    violations.append(("missing-manifest", path))
            elif u["kind"] == "loose_file":
                violations.append(("loose-spider-file", path))
            elif u["kind"] == "adapter":
                adapters.setdefault(u["normalized_slug"], []).append((root, u))

    for group in (g for g in adapters.values() if len(g) > 1
                  and len({u["slug"] for _, u in g}) > 1):
        members = ", ".join(f"{root.name}/{u['path']}"
                            for root, u in sorted(group, key=lambda x: x[1]["path"]))
        violations.append(("adapter-dash-underscore-pair", members))

    return sorted(violations), unit_count


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--roots", default=None,
                    help="comma-separated repo roots to scan "
                         "(default: fd-industry-data + fd-open-data-mcp)")
    args = ap.parse_args()

    roots = [Path(p) for p in args.roots.split(",")] if args.roots else triage_audit.DEFAULT_ROOTS
    violations, unit_count = collect(roots)

    if violations:
        for vtype, path in violations:
            print(f"VIOLATION [{vtype}] {path}\n  fix: {FIXES[vtype]}")
        print(f"conformance gate: FAIL — {len(violations)} violation(s) "
              f"over {unit_count} units")
        return 1
    print(f"conformance gate: PASS — {unit_count} units, 0 violations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
