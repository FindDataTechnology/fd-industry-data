#!/usr/bin/env python3
"""Mechanical conformance gate over generated crawler code.

Read-only single-pass validator: imports triage_audit's discovery functions so
auditor and enforcer share the same heuristics and ignore lists (they cannot
drift apart). Fails with one line per violation, each naming its fix, when:
a spider unit exists outside spiders/<slug>/ (including misspelled directories),
a standard-layout unit lacks manifest.yaml, a loose *_spider.py exists outside
a unit directory, an empty non-standard top-level directory exists, or two
adapters differ only by dash-versus-underscore naming; additionally, a unit
re-implements workbook parsing instead of consuming the shared tabular layer,
or declares a function parameter its own entry point cannot receive. Writes
nothing, ever.

Spec: openspec/changes/estate-hygiene/ (capability estate-hygiene).
"""
from __future__ import annotations

import argparse
import ast
import collections
import json
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

# Declared-parameter reachability. manifest functions[].parameters is an
# outward contract (it reaches the central `functions.parameters` and the MCP
# tool surface), but the runner passes only keys the entry point names
# explicitly (`runner_cli._extra_params`; `**kwargs` deliberately does not
# count). A declaration the entry cannot receive is therefore a promise shown
# to callers that silently does nothing -- measured live: 15 pending_runs
# carried `window: "12m"` on units that never declared or accepted it, every
# one reported `done`.
#
# The gate stage installs only pyyaml, so this must not import unit modules
# (most need scrapling/scrapy/psycopg2). Static AST analysis instead.
DECLARED_PARAM_REACHABILITY = "declared-param-reachability"

# Declared commands no platform surface can invoke. runner_cli always loads
# ``run_<slug>`` and there is no second entry, so a command that is neither the
# entry nor reachable from it nor dispatched by the provider is dead — even if
# it carries a golden (which only proves the function runs, never that
# production calls it). Measured: eurostat-api declared four Wave-C extension
# commands with goldens 002-005, all green, while crawl_items held zero rows
# from them.
DECLARED_COMMAND_REACHABILITY = "declared-command-reachability"

# Golden sample schema conformance. health_verify.py runs every sample through
# health.samples.validate_sample before replaying it, and rows_contains rules
# support exactly {field, equals}: an unsupported operator is reported as a
# malformed rule and fails the replay, so the unit silently loses its health
# gate until an incident triggers verify. Measured 2026-10-14: five units built
# in Wave C / batch 3 carried {"field", "matches"} rules and all five failed
# validate_sample. samples.py is stdlib-only and never imports unit modules, so
# this check is safe at gate stage.
GOLDEN_SCHEMA_INVALID = "golden-schema-invalid"

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
    "declared-param-reachability": "make the declaration match the entry point: "
    "either add the parameter to the entry signature (the runner passes only "
    "explicitly named parameters -- **kwargs does not count), or drop the "
    "declaration; `parameters` must be a list of {name, type, ...} entries",
    "declared-command-reachability": "make the declared command reachable: have "
    "the run_<slug> entry call it (directly or via a helper), or drop the "
    "declaration; a golden alone is not enough — it proves the function runs, "
    "not that production invokes it",
    "golden-schema-invalid": "fix the sample so health.samples.validate_sample "
    "passes: rows_contains rules support exactly {field, equals} (compare with "
    "str(actual) == str(expected)) — an unsupported operator such as 'matches' "
    "fails the replay",
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


def _declared_names(entry, field: str) -> tuple[list[str], str | None]:
    """Declared names for one functions[] entry's ``field`` (parameters/columns).

    Returns ``(names, error)``; a non-None error is a shape violation. The
    protocol loader rejects dict-form outright (pydantic expects a list), so
    the shape is part of the contract, not a style preference — a dict-form
    manifest cannot be loaded, which silently removes the source from the
    catalog surface.
    """
    if not isinstance(entry, dict):
        return [], None
    value = entry.get(field)
    if value is None:
        return [], None
    if isinstance(value, dict):
        return [], (f"{field} is a mapping; the protocol schema requires a "
                    f"list of {{name, ...}} entries")
    if not isinstance(value, list):
        return [], f"{field} must be a list, got {type(value).__name__}"
    names: list[str] = []
    for item in value:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("name")
            if not name:
                return [], f"{field} entry lacks a name: {item!r}"
            names.append(str(name))
        else:
            return [], f"{field} entry must be a mapping or string, got {item!r}"
    return names, None


def _explicit_params_by_def(py_files: list[Path]) -> dict[str, set[str]]:
    """Explicitly named parameters per function name, unioned over same-name defs.

    Mirrors ``runner_cli._extra_params``: only ``posonlyargs``/``args``/
    ``kwonlyargs`` count. ``*args``/``**kwargs`` are excluded on purpose --
    counting ``**kwargs`` would let a manifest typo pass silently, which is the
    conservative reading the runner already implements. Same-name defs are
    unioned because which one runs is decided at runtime; the static rule must
    not guess (runtime drop-evidence covers the branch case instead).
    """
    out: dict[str, set[str]] = collections.defaultdict(set)
    for path in py_files:
        try:
            tree = ast.parse(Path(path).read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            named = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
            out[node.name] |= named
    return out


def _command_name(entry) -> str:
    if isinstance(entry, dict):
        return str(entry.get("command") or "")
    if isinstance(entry, str):
        return re.split(r"[(\s]", entry.strip(), maxsplit=1)[0]
    return ""


def _main_block_nodes(tree: ast.Module) -> set[int]:
    """ids of nodes inside ``if __name__ == "__main__":`` — not a platform surface.

    A call reachable only from ``__main__`` is reachable by a developer running
    the CLI by hand, not by the scheduler. Counting it would let "it runs
    locally" stand in for "production runs it", which is exactly the illusion
    that hid the eurostat-api extensions (declared, goldened, never invoked).
    """
    marks: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
                and test.left.id == "__name__"):
            for sub in ast.walk(node):
                marks.add(id(sub))
    return marks


def _reachable_from_entry(unit_dir: Path, entry: str) -> set[str]:
    """Names reachable from ``entry`` inside the unit (call-closure, AST).

    Union over the unit's Python files so a helper module counts. ``self.x()``
    is normalized to ``x`` so methods resolve by name.
    """
    graph: dict[str, set[str]] = collections.defaultdict(set)
    for path in sorted(unit_dir.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            continue
        main = _main_block_nodes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if id(node) in main:
                continue
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call) or id(sub) in main:
                    continue
                fn = sub.func
                if isinstance(fn, ast.Name):
                    graph[node.name].add(fn.id)
                elif isinstance(fn, ast.Attribute):
                    graph[node.name].add(fn.attr)
    seen: set[str] = set()
    stack = [entry]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(graph.get(cur, ()))
    return seen


def _provider_dispatched(root: Path) -> set[str]:
    """Commands the DataProvider dispatches by name (the MCP command surface)."""
    provider = root / "fd_industry_data" / "provider.py"
    if not provider.is_file():
        return set()
    try:
        return set(re.findall(r'command\s*==\s*"([^"]+)"', provider.read_text(
            encoding="utf-8", errors="ignore")))
    except OSError:
        return set()


def _declared_command_units(root: Path) -> list[str]:
    """Units declaring a command no platform surface can invoke.

    A declared command is reachable when it *is* the ``run_<slug>`` entry, or
    sits in that entry's call closure, or is dispatched by name in
    ``provider.py``. Otherwise it is a dead declaration: ``runner_cli`` always
    loads ``run_<slug>`` and there is no second entry, so nothing ever calls it
    — regardless of whether it has a golden.
    """
    import yaml

    dispatched = _provider_dispatched(root)
    hits: list[str] = []
    for manifest in sorted((root / "spiders").glob("*/manifest.yaml")):
        try:
            doc = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(doc, dict):
            continue
        funcs = doc.get("functions") or []
        if not funcs:
            continue  # old schema (shell commands): outside this contract
        slug = manifest.parent.name
        entry = f"run_{slug.replace('-', '_')}"
        reachable = _reachable_from_entry(manifest.parent, entry)
        for e in funcs:
            command = _command_name(e)
            if not command or command == entry or command in dispatched:
                continue
            if command not in reachable:
                hits.append(f"{root.name}/spiders/{slug}: declares command "
                            f"{command!r} that no platform surface invokes "
                            f"(entry {entry} never reaches it; provider "
                            f"dispatches: {', '.join(sorted(dispatched)) or 'none'})")
    return hits


def _declared_param_units(root: Path) -> list[str]:
    """Units whose manifest declares parameters the entry point cannot receive."""
    import yaml

    hits: list[str] = []
    for manifest in sorted((root / "spiders").glob("*/manifest.yaml")):
        try:
            doc = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(doc, dict):
            continue
        funcs = doc.get("functions") or []
        if not funcs:
            continue  # old schema (shell commands): outside this contract
        by_def = _explicit_params_by_def(sorted(manifest.parent.glob("*.py")))
        for entry in funcs:
            command = _command_name(entry)
            if not command:
                continue
            names, shape_err = _declared_names(entry, "parameters")
            if shape_err:
                hits.append(f"{root.name}/spiders/{manifest.parent.name}: "
                            f"{command} {shape_err}")
                continue
            # columns is also list-typed in the protocol schema; dict-form there
            # breaks load_catalog the same way.
            _, cols_err = _declared_names(entry, "columns")
            if cols_err:
                hits.append(f"{root.name}/spiders/{manifest.parent.name}: "
                            f"{command} {cols_err}")
            if not names:
                continue
            if command not in by_def:
                hits.append(f"{root.name}/spiders/{manifest.parent.name}: "
                            f"{command} declares parameters but no such def "
                            f"exists in the unit")
                continue
            accepted = by_def[command]
            for name in names:
                if name not in accepted:
                    hits.append(
                        f"{root.name}/spiders/{manifest.parent.name}: {command} "
                        f"declares parameter {name!r} that the entry point does "
                        f"not accept (accepted: "
                        f"{', '.join(sorted(accepted)) or 'none'})")
    return hits


_SAMPLES_MOD = None


def _load_samples_mod(root: Path):
    """Load health.samples from ``root`` (fallback: this script's repo).

    Lazy import by file path: the health package must stay optional at gate
    stage, and only samples.py (stdlib-only) is needed. Cached per process.
    """
    global _SAMPLES_MOD
    if _SAMPLES_MOD is not None:
        return _SAMPLES_MOD
    import importlib.util

    candidates = [root / "fd_industry_data" / "health" / "samples.py",
                  SCRIPTS_DIR.parent / "fd_industry_data" / "health" / "samples.py"]
    for path in candidates:
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("_gate_health_samples", path)
        if spec is None or spec.loader is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _SAMPLES_MOD = mod
        return mod
    return None


def _golden_schema_units(root: Path) -> list[str]:
    """Units whose golden samples fail health.samples.validate_sample."""
    samples_mod = _load_samples_mod(root)
    if samples_mod is None:
        return []

    hits: list[str] = []
    for unit_dir in sorted((root / "spiders").glob("*/")):
        golden_dir = unit_dir / "golden"
        if not golden_dir.is_dir():
            continue
        for golden in sorted(golden_dir.glob("*.json")):
            try:
                doc = json.loads(golden.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                hits.append(f"{root.name}/{unit_dir.name}: {golden.name} unreadable ({exc})")
                continue
            for err in samples_mod.validate_sample(doc):
                hits.append(f"{root.name}/{unit_dir.name}: {golden.name}: {err}")
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

        for path in _declared_param_units(root):
            violations.append((DECLARED_PARAM_REACHABILITY, path))

        for path in _declared_command_units(root):
            violations.append((DECLARED_COMMAND_REACHABILITY, path))

        for path in _golden_schema_units(root):
            violations.append((GOLDEN_SCHEMA_INVALID, path))

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
