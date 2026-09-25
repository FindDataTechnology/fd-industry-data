"""fd-runner: execute one content-repo source and report the run.

Usage (container entrypoint): fd-runner <src> [--limit N] [--dry-run]

Contract:
- <src> is a directory under the content spiders root (FD_CONTENT_DIR, default
  ./spiders). The git sparse-checkout overlay shadows the baked-in snapshot
  because the content root is prepended to sys.path.
- The source module must expose ``run_<src>(limit=N) -> list[dict]``.
- Exit codes: 0 ok, 2 source missing, 3 adapter failure, 4 no entry point.
- A run record (and its raw items) is written to the central DB unless
  --dry-run; reporting is best-effort and never masks the crawl result.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
import traceback

from .writer import report_run

EXIT_OK = 0
EXIT_SOURCE_MISSING = 2
EXIT_ADAPTER_ERROR = 3
EXIT_NO_ENTRY = 4


def _content_dir() -> str:
    d = os.environ.get("FD_CONTENT_DIR") or os.path.join(os.getcwd(), "spiders")
    return os.path.abspath(d)


def _load_entry(src: str, content_dir: str):
    """Return the run_<src> callable, supporting both import styles."""
    us = src.replace("-", "_")
    src_dir = os.path.join(content_dir, src)
    if not os.path.isdir(src_dir):
        print(f"fd-runner: source '{src}' not found under {content_dir}", file=sys.stderr)
        sys.exit(EXIT_SOURCE_MISSING)

    sys.path.insert(0, os.path.dirname(content_dir))  # enables spiders.<src>.spider
    sys.path.insert(0, src_dir)  # enables legacy `from spider import X`
    os.chdir(src_dir)  # spiders may read sibling data/ files

    try:
        mod = importlib.import_module(f"spiders.{us}.spider")
    except ModuleNotFoundError:
        mod = importlib.import_module("spider")

    fn = getattr(mod, f"run_{us}", None)
    if fn is None:
        runs = [n for n in dir(mod) if n.startswith("run_") and callable(getattr(mod, n))]
        if len(runs) == 1:
            fn = getattr(mod, runs[0])
        else:
            print(
                f"fd-runner: no run_{us} in spiders/{src}/spider.py "
                f"(candidates: {', '.join(runs) or 'none'})",
                file=sys.stderr,
            )
            sys.exit(EXIT_NO_ENTRY)
    return fn


def _commit_sha() -> str:
    p = os.environ.get("FD_CONTENT_COMMIT")
    if p and os.path.isfile(p):
        try:
            with open(p) as f:
                return f.read().strip()[:12]
        except OSError:
            pass
    for cand in ("../../.git/HEAD", "../.git/HEAD"):
        pass  # .git handling stays with the initContainer; env/file only
    return os.environ.get("FD_CONTENT_COMMIT", "unknown")[:12]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fd-runner")
    ap.add_argument("source", help="source id, i.e. spiders/<src>/ directory name")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--dry-run", action="store_true", help="skip DB reporting")
    args = ap.parse_args(argv)

    src = args.source
    content_dir = _content_dir()
    started = time.time()
    fn = _load_entry(src, content_dir)

    status, rows, error_head, items = "success", 0, None, []
    try:
        items = fn(limit=args.limit) or []
        rows = len(items)
    except ModuleNotFoundError as e:
        status, error_head = "failed", f"missing dependency: {e}"
        print(f"fd-runner: {error_head}", file=sys.stderr)
        traceback.print_exc()
    except Exception:
        status = "failed"
        error_head = (traceback.format_exc() or "unknown error").strip().splitlines()[-1][:500]
        traceback.print_exc()
    finished = time.time()

    if not args.dry_run:
        ok = report_run(
            source=src,
            kind="runtime",
            status=status,
            started_at=started,
            finished_at=finished,
            rows_written=rows,
            error_head=error_head,
            commit_sha=_commit_sha(),
            image_tag=os.environ.get("FD_IMAGE_TAG", "unknown"),
            items=items,
        )
        if not ok:
            print("fd-runner: DB report failed; fallback written, crawl result unaffected", file=sys.stderr)

    print(f"fd-runner: {src} -> {status} rows={rows} in {finished - started:.1f}s")
    return EXIT_OK if status == "success" else EXIT_ADAPTER_ERROR


if __name__ == "__main__":
    sys.exit(main())
