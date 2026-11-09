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


SMOKE_LIMIT = 5


def declared_columns(manifest: dict) -> list[str]:
    """Column names the manifest promises for its first function."""
    funcs = manifest.get("functions") or []
    if not funcs or not isinstance(funcs[0], dict):
        return []
    cols = funcs[0].get("columns") or []
    return [str(c.get("name")) for c in cols
            if isinstance(c, dict) and c.get("name")]


def smoke_fetch(unit: Path, src: str, manifest: dict,
                limit: int = SMOKE_LIMIT) -> list[str]:
    """Real fetch with a tiny limit; returns violation lines (empty = green).

    Change harden-crawl-delivery 4.1. The three local checks only prove the unit
    *compiles* and matches the schema; they say nothing about whether it can
    fetch. Landing used to be silent about that until the first scheduled run —
    a full cycle later. This calls the entry point exactly as the runner would,
    with a small limit, and asserts shape: rows exist and every declared column
    is present.

    It calls the spider module directly, so nothing is written to ``crawl_runs``
    and the upstream sees at most ``limit`` rows.
    """
    import importlib.util

    spider = unit / "spider.py"
    spec = importlib.util.spec_from_file_location(f"_smoke_{src.replace('-', '_')}", spider)
    if spec is None or spec.loader is None:
        return ["[smoke] cannot load spider.py"]
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(unit))
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - import failure is a landing failure
        return [f"[smoke] spider.py failed to import: {type(exc).__name__}: {exc}"]
    finally:
        try:
            sys.path.remove(str(unit))
        except ValueError:
            pass

    entry = getattr(module, entry_name(src), None)
    if not callable(entry):
        return [f"[smoke] no callable {entry_name(src)} after import"]

    try:
        rows = entry(limit=limit) or []
    except Exception as exc:  # noqa: BLE001
        return [f"[smoke] {entry_name(src)}(limit={limit}) raised "
                f"{type(exc).__name__}: {str(exc)[:300]}"]

    if not isinstance(rows, list) or not rows:
        return [f"[smoke] {entry_name(src)}(limit={limit}) returned no rows — "
                "a source that cannot produce data must not land"]

    missing = [c for c in declared_columns(manifest)
               if not all(c in (r or {}) for r in rows if isinstance(r, dict))]
    if missing:
        return [f"[smoke] declared column(s) absent from every row: {missing}"]
    return []


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


def land(manifest_path: Path, spider_path: Path, repo_root: Path,
         *, skip_smoke: bool = False) -> str:
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

        if not skip_smoke:
            violations = smoke_fetch(unit, src, data)
            if violations:
                raise LandError(
                    "smoke fetch failed (a unit that cannot fetch must not land):\n"
                    + "\n".join(violations))

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
    ap.add_argument("--skip-smoke", action="store_true",
                    help="skip the real-fetch smoke check (offline/dev only; "
                         "the landing then has no evidence it can fetch)")
    args = ap.parse_args(argv)
    try:
        if args.skip_smoke:
            print("land_source: WARNING: --skip-smoke — this landing has no "
                  "evidence the source can actually fetch", file=sys.stderr)
        land(args.manifest, args.spider, args.repo_root,
             skip_smoke=args.skip_smoke)
    except LandError as e:
        print(f"land_source: REJECTED — nothing committed\n{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
