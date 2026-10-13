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
import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import triage_audit  # noqa: E402
import validate_manifests  # noqa: E402

MANIFEST_PROTOCOL = "output/manifest-drafts/MANIFEST-PROTOCOL.md"

# Inline tabular parsing: a unit re-implementing xlsx/xls reading instead of
# consuming the shared layer. Two independent hand-rolled implementations
# already existed (pboc-inclusive without inlineStr, edgar-emissions without
# sharedStrings fallback); this rule stops a third from appearing.
TABULAR_SHARED_IMPORT_RE = re.compile(
    r"fd_industry_data(?:\.tabular| import tabular)"
)
TABULAR_INLINE_PATTERNS = (
    re.compile(r"""["']xl/sharedStrings\.xml["']"""),
    re.compile(r"""["']xl/worksheets/"""),
    re.compile(r"""["']xl/workbook\.xml["']"""),
    re.compile(r"\bsharedStrings\b"),
)

# violation type -> one-line fix
FIXES = {
    "unit-outside-spiders": "move to spiders/<slug>/",
    "missing-manifest": f"draft per {MANIFEST_PROTOCOL}",
    "loose-spider-file": "move into its unit dir",
    "empty-nonstandard-dir": "delete",
    "adapter-dash-underscore-pair": "keep one form",
    "manifest-contract": "fix the manifest fields flagged by validate_manifests "
    "(schedule/memory_limit/cpu_limit/enabled/site vs fd_industry_data/sites.yaml)",
    "inline-tabular-parser": "consume fd_industry_data.tabular "
    "(read_grid/sheets/unzip/Table) instead of hand-rolling workbook parsing",
}


def _inline_tabular_units(root: Path, units: list[dict]) -> list[str]:
    """Units that parse workbooks inline without importing the shared layer.

    Only flags a unit when it shows workbook-parsing evidence *and* never
    references ``fd_industry_data.tabular`` — a unit that imports the shared
    layer is by definition not re-implementing it.
    """
    hits: list[str] = []
    for u in units:
        if u["kind"] not in ("spider_dir", "loose_file", "adapter"):
            continue
        unit_dir = root / u["path"]
        try:
            files = triage_audit.unit_py_files(unit_dir, u["kind"])
        except OSError:
            continue
        text = "\n".join(triage_audit.read_text(f) for f in files)
        if not text:
            continue
        if TABULAR_SHARED_IMPORT_RE.search(text):
            continue  # consumes the shared layer — not a violation
        if any(p.search(text) for p in TABULAR_INLINE_PATTERNS):
            hits.append(f"{root.name}/{u['path']}")
    return hits


def collect(roots: list[Path]) -> tuple[list[tuple[str, str]], int]:
    """Return (violations as (type, path), scanned unit count). Zero writes."""
    violations: list[tuple[str, str]] = []
    unit_count = 0
    adapters: dict[str, list[tuple[Path, dict]]] = {}

    for root in roots:
        for m in sorted((root / "spiders").glob("*/manifest.yaml")):
            for e in validate_manifests.validate(m):
                violations.append(("manifest-contract", e))
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

        for path in _inline_tabular_units(root, units):
            violations.append(("inline-tabular-parser", path))

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
