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
from pathlib import Path
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


#: `source_url:` at the start of a line in a manifest. Deliberately not a YAML
#: parse: `yaml` is not installed in every environment this runs in (measured —
#: importing it here raised ModuleNotFoundError on the dev box), and a missing
#: optional dependency must not turn "could not read" into "reads nothing". One
#: top-level scalar needs one pattern, not a parser.
_SOURCE_URL_RE = __import__("re").compile(
    r"^source_url:\s*[\"']?([^\"'\r\n#]+)", __import__("re").MULTILINE
)


def _upstreams_of(source: str) -> list[str] | str:
    """The upstream URL(s) a source reads, or "unknown" when it cannot be read.

    "unknown" rather than [] on purpose: an empty list is a claim ("reads
    nothing"), and a caller cannot tell it apart from a failed read.
    """
    if not source or "/" in source or "\\" in source or source.startswith("."):
        return "unknown"
    # Resolve the manifest the same way the runner resolves the source: through
    # FD_CONTENT_DIR when it is set, else next to this package. Looking only at the
    # package directory was wrong — a dispatcher runs with FD_CONTENT_DIR pointing
    # at a checkout, and a test fixture lives in a temp dir, so the lookup silently
    # returned "unknown" for every source outside the package.
    roots: list[Path] = []
    content_dir = os.environ.get("FD_CONTENT_DIR")
    if content_dir:
        roots.append(Path(content_dir))
    roots.append(Path(__file__).resolve().parent.parent / "spiders")
    text = None
    for root in roots:
        candidate = root / source / "manifest.yaml"
        try:
            text = candidate.read_text(encoding="utf-8")
            break
        except OSError:
            continue
    if text is None:
        return "unknown"
    match = _SOURCE_URL_RE.search(text)
    if not match:
        return "unknown"
    url = match.group(1).strip()
    return [url] if url else "unknown"


#: The manifest already had a way to say "one successful run must write at least N
#: rows": `min_rows_per_run`, read by `health.inspector.min_rows_floor`.

#: An earlier version of this file invented `accept.min_rows` for the same concept.
#: That was a duplicate of a mechanism that already existed and was already declared
#: by one source — two spellings of one promise, which is the defect this whole
#: workspace keeps rediscovering. This reader now consumes the EXISTING field and
#: adds only what was missing: **the evaluation happens before the status is
#: committed**, instead of only after the fact in the inspector.

#: `accept:` may additionally declare bounds the inspector cannot express. Kept as
#: a separate, optional block so it never shadows `min_rows_per_run`.
_ACCEPT_MAX_AGE_DAYS = "max_age_days"


def _manifest_of(source: str):
    """Locate and read a source's manifest text, or None. Never raises."""
    if not source or "/" in source or "\\" in source or source.startswith("."):
        return None
    roots: list[Path] = []
    content_dir = os.environ.get("FD_CONTENT_DIR")
    if content_dir:
        roots.append(Path(content_dir))
    roots.append(Path(__file__).resolve().parent.parent / "spiders")
    for root in roots:
        try:
            return (root / source / "manifest.yaml").read_text(encoding="utf-8")
        except OSError:
            continue
    return None


def _min_rows_per_run(source: str) -> int | None:
    """The manifest's `min_rows_per_run`, or None when unset.

    Same rules as `health.inspector.min_rows_floor`: a positive int, anything else
    means "not declared". Duplicated logic would eventually disagree; this reads the
    same key with the same interpretation, only at a different moment.
    """
    text = _manifest_of(source)
    if text is None:
        return None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.startswith("min_rows_per_run:"):
            continue
        value = line.split(":", 1)[1].strip()
        # A QUOTED value is a string in YAML, and the inspector's `min_rows_floor`
        # rejects it (`isinstance(raw, int)` is False). Matching that here matters
        # more than being lenient: two readers that disagree on the same manifest
        # mean the run and the ticket can reach different conclusions about one
        # source. Strictness is the shared answer.
        if value[:1] in ("'", '"'):
            return None
        try:
            number = int(value)
        except ValueError:
            return None
        return number if number > 0 else None
    return None


def _accept_block(source: str) -> dict | None:
    """The manifest's optional `accept:` block as a flat dict, or None when absent.

    Parsed with a line scanner rather than a YAML load: `yaml` is not installed in
    every environment this runs in, and an acceptance rule must never be the reason
    a crawl fails. The block is a flat mapping of scalars, which is all a line
    scanner needs to handle.
    """
    text = _manifest_of(source)
    if text is None:
        return None
    out: dict = {}
    inside = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            inside = line.strip() == "accept:"
            continue
        if not inside:
            continue
        key, _, value = line.strip().partition(":")
        key = key.strip()
        value = value.split("#", 1)[0].strip().strip("'\"")
        if not key or not value:
            continue
        try:
            out[key] = int(value)
        except ValueError:
            try:
                out[key] = float(value)
            except ValueError:
                out[key] = value
    return out or None


def _evaluate_accept(source: str, *, status: str, rows: int, env) -> dict | None:
    """Evaluate a source's declared accept-conditions. None means "not declared".

    Returning None matters: a source with no `accept:` block must produce the exact
    same metrics it produced before this existed. Writing `{"ok": true}` for sources
    that declared nothing would turn "not asked" into "passed", which is the
    confusion the whole fact layer exists to prevent.
    """
    try:
        block = _accept_block(source)
    except Exception:  # noqa: BLE001 - never break a crawl for a rule
        return None
    if not block and _min_rows_per_run(source) is None:
        return None

    checks: list[dict] = []
    floor = _min_rows_per_run(source)
    if floor is not None:
        checks.append({"name": "min_rows_per_run", "ok": rows >= floor,
                       "actual": rows, "expected": floor})

    # `block` can be None while `min_rows_per_run` is set — the two declarations are
    # independent, and only one of them is required.
    if block and _ACCEPT_MAX_AGE_DAYS in block and isinstance(
        block[_ACCEPT_MAX_AGE_DAYS], (int, float)
    ):
        # The runner does not know the data's age — that is the payload's business.
        # Recording the declared bound without judging it is the honest option:
        # claiming a pass we did not verify would be worse than not checking.
        checks.append({"name": _ACCEPT_MAX_AGE_DAYS, "ok": None,
                       "actual": "unknown", "expected": block[_ACCEPT_MAX_AGE_DAYS]})

    unknown_keys = sorted(set(block or {}) - {_ACCEPT_MAX_AGE_DAYS})
    if unknown_keys:
        checks.append({"name": "unknown-keys", "ok": False,
                       "actual": unknown_keys,
                       "expected": "only max_age_days (min_rows_per_run is a top-level key)"})

    if not checks:
        return None
    # A check whose `ok` is None is unjudged, not failed: an unverifiable condition
    # must not silently pass, but it must not fail a crawl either.
    judged = [c for c in checks if c["ok"] is not None]
    ok = all(c["ok"] for c in judged) and not any(c["ok"] is False for c in checks)
    return {"ok": ok, "checks": checks, "statusAtEvaluation": status}


def _attribution(source: str, env) -> dict:
    """Who holds this run, and what upstream it reads. Never raises.

    Both are best-effort by design: a run must not fail because attribution could
    not be collected. The failure mode this avoids is the one measured in
    `fd-agent-run/docs/FACT-LAYER-TRANSLATION.md`: when a field is required and
    unavailable, implementations invent a plausible value, and the invention is
    indistinguishable from a fact downstream.
    """
    out: dict = {}

    # The holder: a pod name under the dispatcher, a job/host name otherwise.
    # `HOSTNAME` is only a shell variable on some platforms (macOS zsh does not
    # export it), so the real hostname is the fallback — measured: on the dev box
    # all three env vars were empty and every run recorded "unknown".
    holder = (
        env.get("FD_RUN_OWNER")
        or env.get("HOSTNAME")
        or env.get("COMPUTERNAME")
        or ""
    ).strip()
    if not holder:
        try:
            import socket

            holder = socket.gethostname().strip()
        except Exception:  # noqa: BLE001 - attribution must never break a run
            holder = ""
    out["owner"] = holder or "unknown"

    # The upstream: the manifest already declares `source_url`. Read it directly
    # rather than through the dispatcher so the runner stays usable standalone.
    out["upstreams"] = _upstreams_of(source)

    return out


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

    # Accept-conditions: the completion predicate, evaluated at the moment the run
    # would otherwise be declared a success.
    #
    # Why here and not in the inspector: the inspector judges a run *after* the fact
    # and can only write down a verdict. Evaluating before the status is committed
    # is what lets a source say "0 rows is not a success" and have that be true
    # rather than recorded later. This is the difference between a report and a gate.
    #
    # Strictly opt-in: a manifest with no `accept:` block takes the same path it
    # always did, byte for byte. Every failure mode here is non-fatal by design —
    # an acceptance rule that breaks a crawl would be worse than no rule.
    accept_failures = _evaluate_accept(src, status=status, rows=rows, env=os.environ)

    # Auditability: the effective window travels in crawl_runs.metrics next
    # to dropped_params — the run record is the only surface that survives
    # the pod (the dispatcher runs this as a subprocess and keeps no stderr).
    metrics = {"dropped_params": dropped} if dropped else None
    if window:
        metrics = {**(metrics or {}), **window}

    # Two facts every run already has but never recorded: WHO ran it and WHAT it
    # read from. Without them a row says "something wrote 133 rows" and nothing
    # about attribution or the upstream that failed. Both go into the existing
    # `metrics` jsonb so no schema change is needed on 160 sources.
    #
    # Missing information is recorded as the literal string "unknown", never as an
    # empty value: `owner: ""` and `upstreams: []` read downstream as "nobody" and
    # "reads nothing", which are different claims from "we did not look".
    attribution = _attribution(src, os.environ)
    if attribution:
        metrics = {**(metrics or {}), **attribution}

    # WHAT the run was allowed to return. Without this, `rows_written=20` means
    # either "the upstream only had 20" or "we asked for at most 20", and no
    # downstream reader can tell them apart — measured 2026-11-09: a bulk batch on
    # 2026-11-06 ran 48 sources once each, untagged, all producing exactly 20 rows,
    # and the only way to see it was that all 48 matched to the digit.
    #
    # The health inspector's `min_rows_per_run` floor is a statement about a *full*
    # run; comparing it against a deliberately truncated run is what makes a
    # threshold look wrong when it isn't. Recording the effective limit is the
    # enabling step for that comparison — no schema change, same jsonb.
    metrics = {**(metrics or {}), "limit": args.limit}

    if accept_failures is not None:
        metrics = {**(metrics or {}), "accept": accept_failures}
        # A run that met no crawl error but missed its own declared condition is
        # `partial`, not `success`: the data is there, but the source's own promise
        # was not kept. Leaving it as `success` is exactly the "green but empty"
        # failure this rule exists to end.
        if status == "success" and not accept_failures["ok"]:
            status = "partial"

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
    # `partial` 也算这个进程干完了活：爬取成功、数据已写，只是源自己声明的
    # accept 条件没满足。让它返回 EXIT_ADAPTER_ERROR 会自相矛盾——dispatcher 与
    # auth 层都按"不是失败"处理它（pending=done、身份按成功归还），k8s Job 却会
    # 因此显示 Failed，排查失败的人先去追一批其实跑完的运行。
    # 判定权在下游：DB 里的 status=partial + metrics.accept 已足够巡检出单。
    return EXIT_OK if status in ("success", "partial") else EXIT_ADAPTER_ERROR


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
