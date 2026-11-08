"""fd-runner: execute one content-repo source and report the run.

Usage (container entrypoint): fd-runner <src> [--limit N] [--params JSON]
[--window-start YYYY-MM-DD --window-end YYYY-MM-DD] [--dry-run]

Contract:
- <src> is a directory under the content spiders root (FD_CONTENT_DIR, default
  ./spiders). The git sparse-checkout overlay shadows the baked-in snapshot
  because the content root is prepended to sys.path.
- The source module must expose ``run_<src>(limit=N) -> list[dict]``.
- ``--params`` carries a JSON object of the source's **declared** parameters
  (manifest ``functions[].parameters``) beyond ``limit``. Unknown keys are
  dropped with a warning instead of raising ``TypeError`` — a manifest/entry
  mismatch must not fail a run. Without this channel any parameter a unit
  declares beyond ``limit`` is unreachable in production.
- ``--window-start/--window-end`` carry the dispatcher-computed first-crawl
  backfill window (industry-crawl-gitops): platform-owned run parameters on
  the same class of channel as ``--limit`` — deliberately OUTSIDE the
  ``functions[].parameters`` declaration surface, because the window is
  scheduler-computed and must not become a caller-overridable trigger param.
  When present they are injected into the spider-readable call context two
  ways: entry points whose signature explicitly names ``window_start`` /
  ``window_end`` receive them as kwargs (same conservative named-only rule
  as ``--params``; ``**kwargs`` does not count), and every spider sees them
  as ``FD_WINDOW_START`` / ``FD_WINDOW_END`` in ``os.environ``. Spiders that
  declare neither simply ignore the window — no fleet-wide signature change.
  The effective window lands in ``crawl_runs.metrics`` (auditable), and a
  malformed window fails the run loudly rather than being silently ignored.
- Exit codes: 0 ok, 2 source missing, 3 adapter failure, 4 no entry point.
- A run record (and its raw items) is written to the central DB unless
  --dry-run; reporting is best-effort and never masks the crawl result.
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime

from . import cancel_event
from .writer import cancel_requested, finish_run, open_run_for, report_run, start_run

EXIT_OK = 0
EXIT_SOURCE_MISSING = 2
EXIT_ADAPTER_ERROR = 3
EXIT_NO_ENTRY = 4
_EXIT_SKIPPED = 0  # single-flight skip is a successful no-op for the caller

_CANCEL_POLL_SECONDS = 15


def _extra_params(fn, raw: str | None) -> dict:
    """Parse ``--params`` JSON and keep only keys the entry point accepts.

    Filtering against the real signature is deliberate: a manifest that drifts
    from the code must degrade to "parameter ignored" rather than a
    ``TypeError`` that fails the whole run. Unknown keys and unparsable JSON
    are reported on stderr and dropped.
    """
    return _split_params(fn, raw)[0]


def _split_params(fn, raw: str | None) -> tuple[dict, list[str]]:
    """``(accepted, dropped_keys)`` for a ``--params`` payload.

    ``dropped_keys`` is what the caller asked for and did not get — surfaced in
    the run record (``crawl_runs.metrics.dropped_params``) because the
    dispatcher keeps no copy of the runner's stderr.

    ``limit`` is excluded from both sides: it travels on the dedicated
    ``--limit`` flag, so a ``limit`` inside the JSON payload is redundant
    rather than dropped — reporting it would be a false alarm.
    """
    if not raw:
        return {}, []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"fd-runner: --params is not valid JSON ({e}); ignoring", file=sys.stderr)
        return {}, []
    if not isinstance(parsed, dict):
        print("fd-runner: --params must be a JSON object; ignoring", file=sys.stderr)
        return {}, []
    parsed = {k: v for k, v in parsed.items() if k != "limit"}
    try:
        accepted = set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):  # builtins / C callables: pass nothing extra
        return {}, sorted(parsed)
    accepted.discard("limit")
    unknown = sorted(k for k in parsed if k not in accepted)
    if unknown:
        print(f"fd-runner: ignoring undeclared parameter(s) {unknown}", file=sys.stderr)
    return {k: v for k, v in parsed.items() if k in accepted}, unknown


def _parse_window(start: str | None, end: str | None) -> dict[str, str] | None:
    """Validated platform window params (``window_start``/``window_end``) or
    None when absent.

    Raises ``ValueError`` on malformed input — the caller lets it fail the
    run so a broken dispatch-side window stays visible in crawl_runs (a run
    parameter must never be silently ignored; see the dropped-params
    contract). Strict ``YYYY-MM-DD``: the window is a whole-month-truncated
    date range by construction, and loose parsing here would silently widen
    or shift what the spider fetches.
    """
    if not start and not end:
        # window-auto 对已有成功记录的源返回 ("", "") 表示 no-op；与
        # 显式窗口「只给一边」的失败语义区分开（后者必须报警）
        return None
    if not start or not end:
        raise ValueError(
            f"window params need both --window-start and --window-end, "
            f"got start={start!r} end={end!r}")
    try:
        s = datetime.strptime(start, "%Y-%m-%d").date()
        e = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(
            f"window params must be ISO YYYY-MM-DD dates, "
            f"got start={start!r} end={end!r}") from None
    if s > e:
        raise ValueError(f"window_start {start} is after window_end {end}")
    return {"window_start": s.isoformat(), "window_end": e.isoformat()}


def _auto_first_crawl_window(src: str) -> tuple[str, str]:
    """AppSet CronJob path: adopt the dispatcher's 12-month first-crawl window
    when the central DB has no successful run for this source.

    k8s-site sources are run directly by per-source CronJobs and never pass
    through ``dispatch.enqueue_due``, so the dispatcher cannot hand them a
    window. The runner itself asks the same question the dispatcher would
    (any successful crawl_runs row?), then computes the identical window via
    ``dispatch.first_crawl_window``. Any DB failure degrades to "no window"
    (established behavior) with a warning on stderr — never a crash.
    """
    try:
        from . import dispatch
        conn = dispatch.connect()
        try:
            succeeded = dispatch.successful_run_sources(conn, [src])
        finally:
            conn.close()
        if src in succeeded:
            return "", ""
        w = dispatch.first_crawl_window(__import__("datetime").date.today())
        return w["window_start"], w["window_end"]
    except Exception as exc:  # noqa: BLE001 — DB probe must never break a run
        print(f"fd-runner: window-auto probe failed ({exc}); continuing without window",
              file=sys.stderr)
        return "", ""


def _window_kwargs(fn, window: dict[str, str] | None) -> dict[str, str]:
    """The window subset the entry point explicitly names.

    Same conservative rule as ``_split_params``: only named signature
    parameters count, ``**kwargs`` does not (a typo'd key must be visible,
    not swallowed). Spiders that don't name the window get nothing here —
    they still see ``FD_WINDOW_START``/``FD_WINDOW_END`` in the environment.
    """
    if not window:
        return {}
    try:
        accepted = set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):  # builtins / C callables: pass nothing
        return {}
    return {k: v for k, v in window.items() if k in accepted}


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
    ap.add_argument("--params", default=None,
                    help="JSON object of declared source parameters beyond limit")
    ap.add_argument("--window-start", default=None,
                    help="first-crawl backfill window start (YYYY-MM-DD, "
                         "platform-owned; pairs with --window-end)")
    ap.add_argument("--window-end", default=None,
                    help="first-crawl backfill window end (YYYY-MM-DD, "
                         "platform-owned; pairs with --window-start)")
    ap.add_argument("--window-auto", action="store_true",
                    help="k8s AppSet CronJob path: when this source has no "
                         "successful run in the central DB, adopt the same "
                         "12-whole-month first-crawl window the dispatcher "
                         "would pass explicitly; no-op for established sources. "
                         "Explicit --window-start/--window-end always win.")
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
                       error_head=f"single-flight: run #{other} still open",
                       source=src)
            print(f"fd-runner: {src} skipped, run #{other} won the start race")
            return _EXIT_SKIPPED
        if run_id is None:
            # Never silent: no run row means no single-flight guard and no
            # cancel poller for this execution (writer._SCHEMA / start_run).
            print(f"fd-runner: WARNING: start_run failed for {src}; "
                  "single-flight and cancel are DISABLED for this run",
                  file=sys.stderr)

    status, rows, error_head, items = "success", 0, None, []
    extra, dropped = _split_params(fn, args.params)
    window = None
    try:
        # Malformed window raises inside the try so the failed run is
        # recorded (error_head carries the reason) instead of silently
        # crawling an unscoped window.
        win_start, win_end = args.window_start, args.window_end
        if args.window_auto and not (win_start or win_end):
            win_start, win_end = _auto_first_crawl_window(src)
        window = _parse_window(win_start, win_end)
        if window:
            # Spider-readable context, platform-side: every spider sees the
            # window via env; only entries that explicitly name window_start/
            # window_end additionally receive it as kwargs (below).
            os.environ["FD_WINDOW_START"] = window["window_start"]
            os.environ["FD_WINDOW_END"] = window["window_end"]
            # Backfill runs must land full history, not a capped slice: lift
            # the writer landing cap for windowed runs only (fix-writer-item-
            # cap). An operator-set FD_WRITER_MAX_ITEMS always wins.
            os.environ.setdefault("FD_WRITER_MAX_ITEMS", "100000")
        if run_id is not None:
            _watch_cancel(run_id)
        # Merged (not **) so a window key that also arrived via --params
        # cannot raise "multiple values for keyword argument": the
        # platform-owned window wins.
        call_kwargs = {**extra, **_window_kwargs(fn, window)}
        items = fn(limit=args.limit, **call_kwargs) or []
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

    # Auditability: the effective window travels in crawl_runs.metrics next
    # to dropped_params — the run record is the only surface that survives
    # the pod (the dispatcher runs this as a subprocess and keeps no stderr).
    metrics = {"dropped_params": dropped} if dropped else None
    if window:
        metrics = {**(metrics or {}), **window}

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
                error_head=error_head, items=items, metrics=metrics,
                source=src,
            )
        else:
            ok = report_run(
                source=src, kind="runtime", status=status,
                started_at=started, finished_at=finished, rows_written=rows,
                error_head=error_head,
                commit_sha=_commit_sha(),
                image_tag=os.environ.get("FD_IMAGE_TAG", "unknown"),
                items=items, metrics=metrics,
            )
        if not ok:
            print("fd-runner: DB report failed; fallback written, crawl result unaffected", file=sys.stderr)

    print(f"fd-runner: {src} -> {status} rows={rows} in {finished - started:.1f}s"
          + (f" (window {window['window_start']}..{window['window_end']})"
             if window else "")
          + (f" (dropped params: {', '.join(dropped)})" if dropped else ""))
    return EXIT_OK if status == "success" else EXIT_ADAPTER_ERROR


_AUTH_FAILURE_RE = ("401", "未登录", "login required", "login_required",
                    "logged-in", "please log in", "captcha", "验证码",
                    "登录失效", "session expired")


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
