#!/usr/bin/env python3
"""Render k8s consumption manifests from the release manifest (2.1/2.2 of
change harden-crawl-gitops-foundation, design D2: image references have a
single source).

k8s/release.yaml declares every fd-industry-runner image as a component
(repo + tag) and maps consumption files to components. The consumption
manifests (k8s/dispatcher.yaml, k8s/xinru-dispatcher.yaml,
k8s/federation-observer.yaml) carry __RELEASE_IMAGE:<component>__ placeholders
instead of concrete tags.

Modes:
  python3 scripts/render_release.py            # render placeholders in place
  python3 scripts/render_release.py --check    # validate only, exit codes below

--check dual-state semantics (deliberate — must not be a green rubber stamp):
a consumption file is legal in exactly one of two states:
  PENDING_RENDER      the file contains its __RELEASE_IMAGE:<c>__ placeholder
                      (fresh git checkout, CI reality) and no concrete tag on
                      the component's repo;
  RENDERED_CONSISTENT the file contains the manifest's exact `repo:tag` (and
                      nothing else on that repo).
Anything else fails: a concrete tag that differs from the manifest (DRIFT,
e.g. someone committed a rendered file from a stale manifest or hand-tweaked
one character), placeholder mixed with a concrete tag (partial hand edit),
multiple tags on the same repo, a foreign/unknown placeholder, or neither
placeholder nor image found (broken line / wrong mapping).

Exit codes: 0 = consistent, 1 = violation (gate red), 2 = setup error
(manifest missing/malformed). No third-party imports: the Jenkins gate step
runs this on the bare agent, where PyYAML cannot be assumed.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "k8s" / "release.yaml"

PH_RE = re.compile(r"__RELEASE_IMAGE:([A-Za-z0-9_-]+)__")
TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
CONC_TAG_RE = r"[A-Za-z0-9_][A-Za-z0-9._-]*"  # one docker tag after repo:

CHECK_FAIL = "CHECK_FAIL"
SETUP_FAIL = "SETUP_FAIL"


class SetupError(Exception):
    """Manifest missing/malformed — exit 2, distinct from a content violation."""


def placeholder(component: str) -> str:
    return f"__RELEASE_IMAGE:{component}__"


def parse_manifest(path: Path):
    """Parse the restricted manifest YAML subset strictly; SetupError on
    anything unexpected (no silent misparse — this feeds production images).

    Recognized shape:
      components:
        <name>:
          repo: <registry/repo>
          tag: <tag>
      consumption:
        <repo-root-relative file path>: <component name>
    Comment-only lines and blank lines are ignored; values are verbatim
    (inline `#` is part of the value — keep comments on their own lines).
    """
    if not path.is_file():
        raise SetupError(f"release manifest not found: {path}")
    components: dict[str, dict[str, str]] = {}
    consumption: dict[str, str] = {}
    section = None
    current = None
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            if stripped in ("components:", "consumption:"):
                section = stripped[:-1]
                current = None
                continue
            raise SetupError(f"{path}:{lineno}: unexpected top-level line: {stripped!r}")
        if section is None:
            raise SetupError(f"{path}:{lineno}: unexpected line before any section: {stripped!r}")
        if section == "components":
            if indent == 2:
                if not (stripped.endswith(":") and ":" not in stripped[:-1]):
                    raise SetupError(f"{path}:{lineno}: expected a component header '<name>:', got: {stripped!r}")
                current = stripped[:-1]
                if current in components:
                    raise SetupError(f"{path}:{lineno}: duplicate component {current!r}")
                components[current] = {}
                continue
            if indent == 4:
                if current is None:
                    raise SetupError(f"{path}:{lineno}: key outside a component: {stripped!r}")
                key, sep, value = stripped.partition(": ")
                if not sep:
                    raise SetupError(f"{path}:{lineno}: expected 'key: value', got: {stripped!r}")
                if key not in ("repo", "tag") or key in components[current]:
                    raise SetupError(f"{path}:{lineno}: unexpected/duplicate key {key!r} in component {current!r}")
                components[current][key] = value
                continue
            raise SetupError(f"{path}:{lineno}: unexpected indentation: {stripped!r}")
        # consumption
        if indent != 2:
            raise SetupError(f"{path}:{lineno}: unexpected indentation: {stripped!r}")
        key, sep, value = stripped.partition(": ")
        if not sep:
            raise SetupError(f"{path}:{lineno}: expected '<file>: <component>', got: {stripped!r}")
        if key in consumption:
            raise SetupError(f"{path}:{lineno}: duplicate consumption file {key!r}")
        consumption[key] = value

    if not components:
        raise SetupError(f"{path}: no components defined")
    for name, comp in components.items():
        for key in ("repo", "tag"):
            if key not in comp:
                raise SetupError(f"{path}: component {name!r} missing {key!r}")
        if not REPO_RE.match(comp["repo"]):
            raise SetupError(f"{path}: component {name!r} repo not a plausible image ref: {comp['repo']!r}")
        if not TAG_RE.match(comp["tag"]):
            raise SetupError(f"{path}: component {name!r} tag not a plausible immutable tag: {comp['tag']!r}")
    for file, name in consumption.items():
        if name not in components:
            raise SetupError(f"{path}: consumption file {file!r} references unknown component {name!r}")
    return components, consumption


def concrete_tags(text: str, repo: str) -> list[str]:
    """All concrete tags present in text on the given repo (order of appearance)."""
    return re.findall(re.escape(repo) + r":" + CONC_TAG_RE, text)


def classify(text: str, component: str, comp: dict[str, str]):
    """Classify one consumption file's dual state.

    Returns (state, problems): state in
    PENDING_RENDER / RENDERED_CONSISTENT / VIOLATION; problems is a list of
    human-readable violations (empty iff state != VIOLATION).
    """
    ph = placeholder(component)
    expected = f"{comp['repo']}:{comp['tag']}"
    problems: list[str] = []

    found_ph = PH_RE.findall(text)
    foreign = sorted({c for c in found_ph if c != component})
    if foreign:
        problems.append(
            f"unexpected placeholder(s) for component(s) {foreign} (manifest maps this file to {component!r})"
        )
    n_ph = text.count(ph)
    conc = concrete_tags(text, comp["repo"])
    n_exp = text.count(expected)

    if n_ph and n_exp:
        problems.append(f"placeholder {ph} and manifest image {expected} both present (mixed state)")
        return "VIOLATION", problems

    if n_ph:
        if conc:
            problems.append(
                f"placeholder present but concrete tag(s) also present on {comp['repo']} "
                f"({', '.join(conc)}) — partial hand edit"
            )
            return "VIOLATION", problems
        return "PENDING_RENDER", problems

    if n_exp and len(conc) == n_exp:
        return "RENDERED_CONSISTENT", problems
    if n_exp:  # manifest tag present but extra tags on the same repo
        stale = [c for c in conc if c != expected]
        problems.append(f"manifest image {expected} present but also stale tag(s) {stale}")
        return "VIOLATION", problems
    if conc:
        problems.append(f"DRIFT: concrete image(s) {conc} != manifest {expected}")
        return "VIOLATION", problems

    problems.append(f"neither placeholder {ph} nor image {expected} found")
    hints = re.findall(r"\S*fd-industry-runner:[A-Za-z0-9._-]+", text)
    if hints:
        problems.append(f"found image ref(s) {sorted(set(hints))} with a different repo — mapping or repo mismatch?")
    return "VIOLATION", problems


def run(root: Path, manifest_path: Path, check: bool) -> int:
    components, consumption = parse_manifest(manifest_path)
    violations = 0
    rendered = 0
    up_to_date = 0

    for file_rel, component in sorted(consumption.items()):
        path = root / file_rel
        comp = components[component]
        expected = f"{comp['repo']}:{comp['tag']}"
        if not path.is_file():
            print(f"{CHECK_FAIL} {file_rel}: file not found (manifest consumption mapping stale?)")
            violations += 1
            continue
        text = path.read_text(encoding="utf-8")
        state, problems = classify(text, component, comp)
        if state == "VIOLATION":
            for p in problems:
                print(f"{CHECK_FAIL} {file_rel}: {p}")
            violations += 1
            continue
        if check:
            print(f"OK {state} {file_rel} (component={component}, image={expected})")
            continue
        # render mode
        if state == "RENDERED_CONSISTENT":
            up_to_date += 1
            print(f"up-to-date {file_rel} (component={component}, image={expected})")
            continue
        new_text = text.replace(placeholder(component), expected)
        path.write_text(new_text, encoding="utf-8")
        rendered += 1
        print(f"rendered {file_rel} (component={component}, image={expected})")

    if check:
        if violations:
            print(f"RELEASE_CHECK_DONE failed ({violations} violation(s)) — fix k8s/release.yaml vs consumption files, or re-render")
            return 1
        print(f"RELEASE_CHECK_DONE ok ({len(consumption)} consumption file(s) consistent with {manifest_path.name})")
        return 0

    if violations:
        print(f"RENDER_DONE refused ({violations} file(s) in violation — nothing written to them)")
        return 1
    print(f"RENDER_DONE ok (rendered={rendered}, up-to-date={up_to_date} of {len(consumption)})")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="validate only, write nothing (CI gate mode)")
    ap.add_argument("--root", type=Path, default=REPO_ROOT,
                    help="repo root for resolving consumption paths (default: script's parent dir)")
    ap.add_argument("--manifest", type=Path, default=None,
                    help=f"release manifest path (default: {DEFAULT_MANIFEST})")
    args = ap.parse_args(argv)
    manifest_path = args.manifest if args.manifest is not None else args.root / "k8s" / "release.yaml"
    try:
        return run(args.root, manifest_path, check=args.check)
    except SetupError as exc:
        print(f"{SETUP_FAIL} {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
