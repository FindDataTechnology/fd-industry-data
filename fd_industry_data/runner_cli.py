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
import threading
import time
import traceback

from . import cancel_event
from .writer import cancel_requested, finish_run, open_run_for, report_run, start_run

EXIT_OK = 0
EXIT_SOURCE_MISSING = 2
EXIT_ADAPTER_ERROR = 3
EXIT_NO_ENTRY = 4
_EXIT_SKIPPED = 0  # single-flight skip is a successful no-op for the caller

_CANCEL_POLL_SECONDS = 15


def _content_dir() -> str:
    d = os.environ.get("FD_CONTENT_DIR") or os.path.join(os.getcwd(), "spiders")
    return os.path.abspath(d)


def _load_entry(src: str, content_dir: str):
    """Return the run_<src> callable for the source (handles hyphenated dirs)."""
    from .loader import load_spider_module

    us = src.replace("-", "_")
    src_dir = os.path.join(content_dir, src)
    if not os.path.isdir(src_dir):
        print(f"fd-runner: source '{src}' not found under {content_dir}", file=sys.stderr)
        sys.exit(EXIT_SOURCE_MISSING)

    os.chdir(src_dir)  # spiders may read sibling data/ files
    mod = load_spider_module(src, content_dir)

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

    if not args.dry_run and (open_id := open_run_for(src)) is not None:
        # single-flight: another execution of this source is still running
        report_run(
            source=src, kind="runtime", status="skipped",
            started_at=started, finished_at=time.time(), rows_written=0,
            error_head=f"single-flight: run #{open_id} still open",
            commit_sha=_commit_sha(),
            image_tag=os.environ.get("FD_IMAGE_TAG", "unknown"), items=[],
        )
        print(f"fd-runner: {src} skipped, run #{open_id} still open")
        return _EXIT_SKIPPED

    pending_run_id = None
    if (pid_env := os.environ.get("FD_PENDING_RUN_ID")):
        try:
            pending_run_id = int(pid_env)
        except ValueError:
            pending_run_id = None

    account = os.environ.get("FD_ACCOUNT") or None
    run_id = None
    if not args.dry_run:
        run_id = start_run(
            source=src, commit_sha=_commit_sha(),
            image_tag=os.environ.get("FD_IMAGE_TAG", "unknown"),
            pending_run_id=pending_run_id, identity_alias=account,
        )
        if run_id is not None and (other := open_run_for(src)) not in (None, run_id):
            # lost the start race: another run opened between guard and start
            finish_run(run_id, status="skipped", finished_at=time.time(), rows_written=0,
                       error_head=f"single-flight: run #{other} still open")
            print(f"fd-runner: {src} skipped, run #{other} won the start race")
            return _EXIT_SKIPPED

    status, rows, error_head, items = "success", 0, None, []
    try:
        if run_id is not None:
            _watch_cancel(run_id)
        items = fn(limit=args.limit) or []
        rows = len(items)
        if cancel_event.is_set():
            status = "cancelled"
    except ModuleNotFoundError as e:
        status, error_head = "failed", f"missing dependency: {e}"
        print(f"fd-runner: {error_head}", file=sys.stderr)
        traceback.print_exc()
    except Exception:
        status = "failed"
        error_head = (traceback.format_exc() or "unknown error").strip().splitlines()[-1][:500]
        traceback.print_exc()
    finished = time.time()

    if status == "failed" and account and _looks_like_auth_failure(error_head):
        # dual-path feedback, runner side: return the identity to the pool
        try:
            from . import auth as _auth
            from .dispatch import connect as _connect
            _auth.report_auth_failed(_connect(), src, account,
                                     f"runner heuristic: {error_head}")
            print(f"fd-runner: auth failure reported for account '{account}'")
        except Exception as e:  # noqa: BLE001 - never mask the crawl result
            print(f"fd-runner: auth event report failed: {e}", file=sys.stderr)

    if not args.dry_run:
        ok = False
        if run_id is not None:
            ok = finish_run(
                run_id, status=status, finished_at=finished, rows_written=rows,
                error_head=error_head, items=items,
            )
        else:
            ok = report_run(
                source=src, kind="runtime", status=status,
                started_at=started, finished_at=finished, rows_written=rows,
                error_head=error_head,
                commit_sha=_commit_sha(),
                image_tag=os.environ.get("FD_IMAGE_TAG", "unknown"),
                items=items,
            )
        if not ok:
            print("fd-runner: DB report failed; fallback written, crawl result unaffected", file=sys.stderr)

    print(f"fd-runner: {src} -> {status} rows={rows} in {finished - started:.1f}s")
    return EXIT_OK if status == "success" else EXIT_ADAPTER_ERROR


_AUTH_FAILURE_RE = ("401", "未登录", "login required", "please log in",
                    "captcha", "验证码", "登录失效", "session expired")


def _looks_like_auth_failure(error_head) -> bool:
    if not error_head:
        return False
    head = error_head.lower()
    return any(k in head for k in _AUTH_FAILURE_RE)


def _watch_cancel(run_id: int) -> None:
    """Background poller: set the cooperative cancel event when flagged."""
    def loop():
        while not cancel_event.is_set():
            time.sleep(_CANCEL_POLL_SECONDS)
            if cancel_requested(run_id):
                cancel_event.EVENT.set()
                print(f"fd-runner: cancel requested for run #{run_id}", file=sys.stderr)
                return

    t = threading.Thread(target=loop, daemon=True)
    t.start()


if __name__ == "__main__":
    sys.exit(main())
