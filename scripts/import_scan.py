#!/usr/bin/env python3
"""Import every content spider against the runtime image's dependency surface.

Runs inside the Dockerfile `scan` stage (FROM runner + fresh spiders/). A
ModuleNotFoundError here means content references a dependency the runtime
image does not preinstall — the exact skew the admission gate exists to catch.
"""
from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fd_industry_data.loader import load_spider_module  # noqa: E402


def _quarantine() -> set[str]:
    qpath = pathlib.Path(os.environ.get("FD_QUARANTINE")
                         or pathlib.Path(__file__).resolve().parent / "quarantine.txt")
    if not qpath.is_file():
        return set()
    return {ln.strip() for ln in qpath.read_text().splitlines()
            if ln.strip() and not ln.strip().startswith("#")}


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                        else os.environ.get("FD_CONTENT_DIR")
                        or pathlib.Path(__file__).resolve().parents[1] / "spiders")
    quarantined = _quarantine()
    failed: list[str] = []
    scanned = 0
    for d in sorted(root.iterdir()):
        if d.name.startswith((".", "_")) or not (d / "spider.py").is_file():
            continue
        scanned += 1
        if d.name in quarantined:
            continue
        try:
            load_spider_module(d.name, str(root))
        except Exception as e:  # noqa: BLE001 - report every failure mode
            failed.append(f"{d.name}: {type(e).__name__}: {e}")
    if failed:
        print("\n".join(failed), file=sys.stderr)
        print(f"import scan: {len(failed)} failed of {scanned} scanned "
              f"({scanned - len(failed) - sum(1 for q in quarantined if (root / q).is_dir())} clean, "
              f"{len(quarantined)} quarantined)", file=sys.stderr)
        return 1
    print(f"import scan: all {scanned} spider modules pass "
          f"({len(quarantined)} quarantined: {', '.join(sorted(quarantined)) or 'none'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
