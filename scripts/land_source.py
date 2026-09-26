#!/usr/bin/env python3
"""Land a harness-generated source into the content repo (crawl-platform
harness-source-landing).

Takes an exported manifest YAML + spider file from fd-scraw-harness, aligns
them to the content contract, writes spiders/<src>/, and commits ONLY when
the local admission checks pass (validate_manifests + conformance gate +
py_compile). The landing never lights the source up: any `schedule` in the
input is stripped and reported. Runs inside the fd-industry-data checkout —
it uses the workspace's existing git credentials and adds none elsewhere.

Usage:
  python3 scripts/land_source.py /tmp/x/manifest.yaml /tmp/x/spider.py \
      [--repo-root /path/to/fd-industry-data]
"""
from __future__ import annotations

import argparse
import py_compile
import re
import subprocess
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class LandError(Exception):
    """Landing rejected; str(err) lists violations with fixes."""


def entry_name(src: str) -> str:
    return "run_" + src.replace("-", "_")


def load_and_align(manifest_path: Path, spider_path: Path) -> tuple[str, dict, str, list[str]]:
    """Validate inputs and align to the v2 contract.

    Returns (src, manifest, spider_code, stripped_fields)."""
    import yaml

    try:
        data = yaml.safe_load(manifest_path.read_text())
    except yaml.YAMLError as e:
        raise LandError(f"manifest is unparseable YAML: {e}")
    if not isinstance(data, dict):
        raise LandError("manifest is not a mapping")

    src = str(data.get("name") or "").strip()
    if not SLUG_RE.match(src):
        raise LandError(
            f"manifest name {src!r} is not a valid source slug "
            "(lowercase letters/digits/-/_); it must equal the directory name")

    spider_code = spider_path.read_text()
    if f"def {entry_name(src)}(" not in spider_code:
        raise LandError(
            f"spider has no `def {entry_name(src)}(limit: int = 100)` entry — "
            "the runner contract requires it; see templates/new-source/spider.py")

    stripped = []
    if data.get("schedule"):
        stripped.append("schedule")  # landing never lights up
        data.pop("schedule", None)
    if str(data.get("version", "2")) != "2":
        data["version"] = "2"

    return src, data, spider_code, stripped


def run_local_checks(repo_root: Path) -> list[str]:
    """The three local admission checks; returns violation lines (empty = green)."""
    violations: list[str] = []
    checks = [
        ("manifest validation", [sys.executable, "scripts/validate_manifests.py"]),
        ("conformance gate", [sys.executable, "scripts/conformance_gate.py", "--roots", "."]),
    ]
    for name, cmd in checks:
        proc = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
        if proc.returncode != 0:
            out = (proc.stderr or proc.stdout).strip()
            violations.append(f"[{name}] failed:\n{out}")
    return violations


def land(manifest_path: Path, spider_path: Path, repo_root: Path) -> str:
    """Land + check + commit; returns the committed source name."""
    src, data, spider_code, stripped = load_and_align(manifest_path, spider_path)

    import yaml

    unit = repo_root / "spiders" / src
    if unit.exists():
        raise LandError(f"spiders/{src} already exists — landing must not overwrite")
    unit.mkdir(parents=True)
    dirty = False
    try:
        (unit / "manifest.yaml").write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        (unit / "spider.py").write_text(spider_code)
        py_compile.compile(str(unit / "spider.py"), doraise=True)
        dirty = True

        violations = run_local_checks(repo_root)
        if violations:
            raise LandError("local admission checks failed:\n" + "\n".join(violations))

        proc = subprocess.run(
            ["git", "add", f"spiders/{src}"], cwd=repo_root, capture_output=True, text=True)
        if proc.returncode != 0:
            raise LandError(f"git add failed: {proc.stderr.strip()}")
        proc = subprocess.run(
            ["git", "commit", "-m",
             f"content({src}): land harness-generated source (unlit)"],
            cwd=repo_root, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise LandError(f"git commit failed: {proc.stderr.strip()}")
        note = (f" (stripped {', '.join(stripped)})" if stripped else "")
        print(f"land_source: {src} landed and committed{note}")
        print(f"next steps: 1) trigger a test run: MCP platform_trigger "
              f"(source='{src}')  2) light it up: add `schedule:` to "
              f"spiders/{src}/manifest.yaml in a separate commit")
        return src
    except LandError:
        if unit.exists():
            shutil.rmtree(unit)
        if dirty:
            subprocess.run(["git", "reset", "HEAD", f"spiders/{src}"],
                           cwd=repo_root, capture_output=True)
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="land_source")
    ap.add_argument("manifest", type=Path, help="exported manifest.yaml from the harness")
    ap.add_argument("spider", type=Path, help="spider .py file with run_<src>(limit)")
    ap.add_argument("--repo-root", type=Path,
                    default=Path(__file__).resolve().parents[1])
    args = ap.parse_args(argv)
    try:
        land(args.manifest, args.spider, args.repo_root)
    except LandError as e:
        print(f"land_source: REJECTED — nothing committed\n{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
