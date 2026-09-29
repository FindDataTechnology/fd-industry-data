#!/usr/bin/env python3
"""Source x site egress reachability matrix (tencent-crawl-fleet-expansion 3.1).

Runs ON a site machine (or any host standing in for one) and probes every
candidate source's declared `source_url` from THIS machine's egress — no
proxy, no forwarding — writing one shard per site. A later `--merge` pass
combines shards into a matrix + routing report.

Politeness: requests to the same host are serialized with a delay, each
probe has a timeout, and the whole round has a wall-clock budget; past the
budget remaining combos are recorded as `uncovered`, never silently dropped.

Usage:
  python3 scripts/egress_matrix.py --site zihan [--only src1,src2]
  python3 scripts/egress_matrix.py --merge            # combine shards + report
Output: output/egress-matrix/<site>.json, matrix.json, report.md
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SPIDERS = REPO / "spiders"
OUT_DIR = REPO / "output" / "egress-matrix"
PROBE_TIMEOUT_S = 10
SAME_HOST_GAP_S = 2.0
DEFAULT_BUDGET_S = 600
UA = "fd-industry-egress-matrix/1.0 (reachability probe)"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_candidates(only: list[str] | None) -> dict[str, str]:
    """source name -> probe URL from each spider's manifest."""
    import yaml

    out: dict[str, str] = {}
    for mdir in sorted(SPIDERS.iterdir()):
        mfile = mdir / "manifest.yaml"
        if not mfile.is_file() or mdir.name in ("data", "__pycache__"):
            continue
        try:
            data = yaml.safe_load(mfile.read_text()) or {}
        except yaml.YAMLError:
            continue
        name = data.get("name") or mdir.name
        url = (data.get("source_url") or "").strip()
        if only and name not in only:
            continue
        if url:
            out[name] = url
    return out


def probe(url: str) -> dict:
    """One HEAD (GET fallback) from the local egress; verdict + evidence."""
    started = time.monotonic()
    evidence: dict = {"url": url, "probed_at": utcnow()}
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_S) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        if e.code == 405:  # HEAD refused: retry once with GET
            return probe_get(url, started, evidence)
        status = e.code
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        reason = getattr(e, "reason", e)
        evidence.update(latency_ms=int((time.monotonic() - started) * 1000),
                        error=type(reason).__name__ + (
                            f": {reason}" if str(reason) else ""))
        return {"verdict": "unreachable", **evidence}
    return classify(status, started, evidence)


def probe_get(url: str, started: float, evidence: dict) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_S) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        reason = getattr(e, "reason", e)
        evidence.update(latency_ms=int((time.monotonic() - started) * 1000),
                        error=type(reason).__name__ + (
                            f": {reason}" if str(reason) else ""),
                        method="GET-fallback")
        return {"verdict": "unreachable", **evidence}
    return classify(status, started, evidence, method="GET-fallback")


def classify(status: int, started: float, evidence: dict,
             method: str = "HEAD") -> dict:
    latency = int((time.monotonic() - started) * 1000)
    if status < 400:
        verdict = "reachable"
    elif status in (401, 403, 429):
        verdict = "unreachable"  # alive but blocked for this egress/bot
    else:
        verdict = "degraded"     # endpoint trouble, not egress-specific
    return {"verdict": verdict, "http_status": status,
            "latency_ms": latency, "method": method, **evidence}


def run_site(site: str, only: list[str] | None, budget_s: int) -> Path:
    candidates = load_candidates(only)
    shard = {"site": site, "generated_at": utcnow(),
             "probe_timeout_s": PROBE_TIMEOUT_S, "results": {}}
    deadline = time.monotonic() + budget_s
    last_host: str | None = None
    for name, url in sorted(candidates.items()):
        if time.monotonic() > deadline:
            shard["results"][name] = {"verdict": "uncovered",
                                      "url": url, "probed_at": utcnow()}
            continue
        host = urllib.parse.urlparse(url).netloc
        if host == last_host:
            time.sleep(SAME_HOST_GAP_S)
        last_host = host
        try:
            shard["results"][name] = probe(url)
        except Exception as e:  # noqa: BLE001 - probe infra failure != unreachable
            shard["results"][name] = {"verdict": "probe_failed", "url": url,
                                      "error": f"{type(e).__name__}: {e}",
                                      "probed_at": utcnow()}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{site}.json"
    out.write_text(json.dumps(shard, ensure_ascii=False, indent=1))
    n = len(shard["results"])
    ok = sum(1 for r in shard["results"].values()
             if r["verdict"] == "reachable")
    print(f"egress-matrix[{site}]: {ok}/{n} reachable -> {out}")
    return out


def merge() -> Path:
    shards = [json.loads(p.read_text()) for p in sorted(OUT_DIR.glob("*.json"))
              if p.name not in ("matrix.json",)]
    if not shards:
        sys.exit("no site shards found; run --site first")
    sites = [s["site"] for s in shards]
    sources: dict[str, dict[str, dict]] = {}
    for s in shards:
        for name, res in s["results"].items():
            sources.setdefault(name, {})[s["site"]] = res

    assignments: dict[str, dict] = {}
    conflicts: list[dict] = []
    all_unreachable: list[str] = []
    for name, per_site in sorted(sources.items()):
        reachable = {st: r for st, r in per_site.items()
                     if r.get("verdict") == "reachable"}
        if reachable:
            best = min(reachable, key=lambda st: reachable[st]["latency_ms"])
            assignments[name] = {"suggested_site": best,
                                 "latency_ms": reachable[best]["latency_ms"],
                                 "reachable_sites": sorted(reachable)}
        else:
            all_unreachable.append(name)
        mfile = SPIDERS / name / "manifest.yaml"
        if mfile.is_file():
            import yaml
            data = yaml.safe_load(mfile.read_text()) or {}
            current = data.get("site") or "tencent"
            if name in assignments and current not in \
                    assignments[name]["reachable_sites"]:
                conflicts.append({"source": name, "current_site": current,
                                  "suggested_site":
                                      assignments[name]["suggested_site"]})

    matrix = {"generated_at": utcnow(), "sites": sites,
              "assignments": assignments, "all_unreachable": all_unreachable,
              "conflicts": conflicts, "sources": sources}
    mpath = OUT_DIR / "matrix.json"
    mpath.write_text(json.dumps(matrix, ensure_ascii=False, indent=1))

    lines = [f"# Egress reachability matrix — {utcnow()}",
             f"Sites: {', '.join(sites)}",
             f"Sources probed: {len(sources)}; routable: {len(assignments)}; "
             f"all-unreachable: {len(all_unreachable)}", "",
             "## Suggested routing", "",
             "| source | suggested site | latency ms | reachable on |",
             "|---|---|---|---|"]
    for name, a in sorted(assignments.items()):
        lines.append(f"| {name} | {a['suggested_site']} | {a['latency_ms']} "
                     f"| {', '.join(a['reachable_sites'])} |")
    lines += ["", "## All-site unreachable (do not light up)", ""]
    lines += [f"- {n}" for n in all_unreachable] or ["- (none)"]
    lines += ["", "## Assignment conflicts (manifest vs matrix)", ""]
    lines += [f"- {c['source']}: manifest={c['current_site']} "
              f"-> suggested={c['suggested_site']}" for c in conflicts] or \
             ["- (none)"]
    rpath = OUT_DIR / "report.md"
    rpath.write_text("\n".join(lines) + "\n")
    print(f"egress-matrix: merged {len(sites)} sites, {len(sources)} sources "
          f"-> {mpath}, {rpath}")
    return rpath


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--site", help="site id this host probes as")
    ap.add_argument("--only", help="comma-separated source names")
    ap.add_argument("--merge", action="store_true",
                    help="combine site shards into matrix + report")
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET_S,
                    help="wall-clock budget per site round (s)")
    args = ap.parse_args()
    if args.merge:
        merge()
        return 0
    if not args.site:
        ap.error("--site or --merge required")
    run_site(args.site, [s for s in (args.only or "").split(",") if s],
             args.budget)
    return 0


if __name__ == "__main__":
    sys.exit(main())
