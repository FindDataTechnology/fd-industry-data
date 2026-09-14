#!/usr/bin/env python3
"""L3 source re-analysis: deterministic shard planning, polite mechanical probing, verdict merging.

The script is the ONLY network actor: subagents run its probe commands, interpret the
reports against each unit's parsing code, and write verdict files; --merge validates
coverage and integrates the registry. Stdlib only.
Spec: openspec/changes/l3-source-reanalysis/
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent          # fd-industry-data
TRIAGE = REPO_ROOT / "output" / "triage-post-cleanup" / "triage.json"
OUT = REPO_ROOT / "output" / "l3-reanalysis"
REV2 = OUT / "revision-2"
CREDS_PATH = Path(os.environ.get("FD_PROXY_CREDS",
                                 str(Path.home() / ".finddata" / "proxy-creds.json")))
UA = "fd-research-verify/1.0 (one-shot source verification)"
TIMEOUT = 15
MAX_URLS = 3
VERDICTS = {"verified_api", "verified_html", "degraded", "unreachable", "needs_browser"}
HTML_DATA_MARKERS = ["<table", "<tbody", "<li", "<tr class", "datagrid", "data-table"]
SPA_MARKERS = ['id="root"', 'id="app"', "window.__nuxt", "__next_data__", "ng-app",
               'id="___gatsby"', "window.__initial_state__"]

# retest egress pool (design D1): name -> creds-file port
EGRESS_PORTS = {"gost@30080": 30080, "fx01@30081": 30081,
                "fx04@30084": 30084, "fx11@30091": 30091}
RETEST_VERDICTS = {"unreachable", "degraded"}   # pinned units for the retest


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- plan --------------------------------------------------------------------
def load_l3_units() -> list[dict]:
    data = json.loads(TRIAGE.read_text(encoding="utf-8"))
    units: dict[str, dict] = {}
    for rec in data.get("domains", {}).values():
        for u in rec["units"]:
            if u["level"] == "L3":
                units[u["path"]] = u
    for u in data.get("unattributed_units", []):
        if u["level"] == "L3":
            units[u["path"]] = u
    out = []
    for path in sorted(units):
        u = units[path]
        out.append({
            "path": path,
            "kind": u["kind"],
            "domains": u.get("domains", []),
            "urls": u.get("urls", [])[:MAX_URLS],
        })
    return out


def cut_shards(units: list[dict], n: int) -> list[list[dict]]:
    """Balanced cut with best-effort domain affinity. Units sharing any domain form
    a connected component (union-find); components up to CAP units land whole on one
    shard. Larger components (e.g. 45 units all on one API host) are split by first
    domain, then hard-cut by path — splitting a same-host group across shards is
    acceptable: each shard still probes each unit only once (politeness per request,
    not per host)."""
    CAP = 24
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for u in units:
        union(f"unit:{u['path']}", f"unit:{u['path']}")
        for d in u.get("domains", []):
            union(f"unit:{u['path']}", f"dom:{d}")
    components: dict[str, list[dict]] = {}
    for u in units:
        components.setdefault(find(f"unit:{u['path']}"), []).append(u)

    groups: list[list[dict]] = []
    for comp in components.values():
        if len(comp) <= CAP:
            groups.append(comp)
            continue
        by_dom: dict[str, list[dict]] = {}
        for u in comp:
            by_dom.setdefault(u["domains"][0] if u.get("domains") else f"unit:{u['path']}",
                              []).append(u)
        for sub in by_dom.values():
            for i in range(0, len(sub), CAP):
                groups.append(sub[i:i + CAP])

    shards: list[list[dict]] = [[] for _ in range(n)]
    loads = [0] * n
    for g in sorted(groups, key=lambda g: (-len(g), g[0]["path"])):
        i = loads.index(min(loads))
        shards[i].extend(g)
        loads[i] += len(g)
    return [sorted(s, key=lambda u: u["path"]) for s in shards]


def cmd_plan(n_shards: int) -> int:
    units = load_l3_units()
    if not units:
        print("no L3 units found in", TRIAGE)
        return 1
    shards_dir = OUT / "shards"
    (verdicts_dir := OUT / "verdicts").mkdir(parents=True, exist_ok=True)
    shards_dir.mkdir(parents=True, exist_ok=True)
    cut = cut_shards(units, n_shards)
    shard_names = []
    for i, group in enumerate(cut, 1):
        if not group:
            continue
        name = f"shard-{i:02d}"
        shard_names.append(name)
        payload = {"name": name, "units": group}
        (shards_dir / f"{name}.json").write_text(
            json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        probe_ct = sum(min(len(u["urls"]), MAX_URLS) for u in group)
        print(f"{name}: {len(group)} units, ~{probe_ct} probes")
    (OUT / "shards.json").write_text(json.dumps(
        {"generated_at": utc_now(), "shards": shard_names, "all_units": units},
        indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"total: {len(units)} units in {len(shard_names)} shards -> {shards_dir}")
    return 0


def cmd_make_shard(paths: list[str], out: Path) -> int:
    units = load_l3_units()
    by_path = {u["path"]: u for u in units}
    picked = [by_path[p] for p in paths if p in by_path]
    missing = [p for p in paths if p not in by_path]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"name": out.stem, "units": picked},
                              indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if missing:
        print("WARN not L3/unknown:", missing)
    print(f"wrote {len(picked)} units -> {out}")
    return 0


# --- probe -------------------------------------------------------------------
def analyze_body(result: dict, body: bytes) -> None:
    result["body_sha256_4k"] = hashlib.sha256(body[:4096]).hexdigest()
    text = body.decode("utf-8", errors="replace")
    result["sample"] = re.sub(r"\s+", " ", text)[:300]
    ct = (result.get("content_type") or "").lower()
    stripped = text.lstrip()
    if "json" in ct or stripped[:1] in "{[":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            result["markers"].append("json-like(truncated?)")
        else:
            if isinstance(data, dict):
                result["json_keys"] = sorted(str(k) for k in data.keys())[:30]
                result["markers"].append("json-object")
            elif isinstance(data, list):
                result["markers"].append("json-array")
    low = text.lower()
    for m in HTML_DATA_MARKERS:
        if m in low:
            result["markers"].append(m)
    for m in SPA_MARKERS:
        if m in low:
            result["markers"].append("spa:" + m)
    if len(body) < 2000 and low.count("<script") > low.count("<p"):
        result["markers"].append("script-heavy-tiny")


def probe_url(url: str, cred: dict | None = None, egress: str | None = None) -> dict:
    result = {"url": url, "status": None, "content_type": None, "final_url": None,
              "markers": [], "json_keys": None, "body_sha256_4k": None, "sample": None,
              "error": None, "attempts": 1, "egress": egress}
    opener = urllib.request.build_opener()
    if cred:
        proxy = f"http://{cred['user']}:{cred['pass']}@100.64.0.7:{cred['port']}"
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    for attempt in (1, 2):
        result["attempts"] = attempt
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        try:
            with opener.open(req, timeout=TIMEOUT) as resp:
                body = resp.read(65536)
                result.update(status=resp.status,
                              content_type=resp.headers.get("Content-Type", ""),
                              final_url=resp.geturl())
                analyze_body(result, body)
                return result
        except urllib.error.HTTPError as e:
            result.update(status=e.code, error=f"HTTP {e.code}")
            return result                       # no retry on HTTP-level errors
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError,
                ValueError, TimeoutError) as e:  # connection-level -> single retry
            result["error"] = f"{type(e).__name__}: {str(e)[:150]}"
    return result


def probe_shard(shard_file: Path) -> int:
    shard = json.loads(shard_file.read_text(encoding="utf-8"))
    jobs = [(u["path"], url) for u in shard["units"] for url in u["urls"]]
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda j: {"unit": j[0], **probe_url(j[1])}, jobs))
    out = OUT / "probes" / f"{shard['name']}.probes.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"shard": shard["name"], "probes": results},
                              indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    ok = sum(1 for r in results if r["status"] == 200)
    print(f"probed {len(jobs)} urls / {len(shard['units'])} units: {ok} got HTTP 200 -> {out}")
    return 0


def cmd_probe_url(url: str) -> int:
    print(json.dumps(probe_url(url), indent=1, ensure_ascii=False))
    return 0


# --- retest (revision-2) -------------------------------------------------------
def load_egress_pool() -> dict:
    creds = json.loads(CREDS_PATH.read_text(encoding="utf-8"))
    by_port = {c["port"]: c for c in creds}
    pool = {name: by_port.get(port) for name, port in EGRESS_PORTS.items()}
    missing = [k for k, v in pool.items() if not v]
    if missing:
        raise SystemExit(f"missing credentials for {missing} in {CREDS_PATH} "
                         f"(or set FD_PROXY_CREDS)")
    return pool


def is_fred_family(domains: list[str], urls: list[str]) -> bool:
    joined = " ".join(domains) + " " + " ".join(urls)
    return "stlouisfed.org" in joined or re.search(r"\bfred\b", joined) is not None


def retest_egresses(domains: list[str], urls: list[str]) -> list[str]:
    """Ordered egress list per design D1/D3 routing rules."""
    if is_fred_family(domains, urls):
        return ["fx01@30081", "fx04@30084"]          # egress-independent suspect: cap 2
    cn = any(d.endswith(".cn") for d in domains)
    if cn:
        return ["gost@30080", "fx01@30081"]          # CN-hosted: CN egress first
    return ["fx01@30081", "fx04@30084", "fx11@30091", "gost@30080"]


def cmd_retest_plan(n_shards: int) -> int:
    registry = json.loads((OUT / "registry.json").read_text(encoding="utf-8"))
    units_meta = {u["path"]: u for u in
                  json.loads((OUT / "shards.json").read_text(encoding="utf-8"))["all_units"]}
    pinned = []
    for path, verdict in registry["units"].items():
        if verdict["verdict"] not in RETEST_VERDICTS:
            continue
        meta = units_meta.get(path)
        if not meta:
            print(f"WARN no unit metadata for {path}; skipped")
            continue
        pinned.append(meta)
    pinned.sort(key=lambda u: u["path"])

    plan = {}
    for u in pinned:
        plan[u["path"]] = {"egresses": retest_egresses(u.get("domains", []), u.get("urls", [])),
                           "previous_verdict": registry["units"][u["path"]]["verdict"]}
    shards_dir = REV2 / "shards"
    (REV2 / "verdicts").mkdir(parents=True, exist_ok=True)
    shards_dir.mkdir(parents=True, exist_ok=True)
    cut = cut_shards(pinned, n_shards)
    names = []
    for i, group in enumerate(cut, 1):
        if not group:
            continue
        name = f"shard-{i:02d}"
        names.append(name)
        (shards_dir / f"{name}.json").write_text(json.dumps(
            {"name": name, "units": group}, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8")
        probes = sum(len(u["urls"][:MAX_URLS]) * len(plan[u["path"]]["egresses"])
                     for u in group)
        print(f"{name}: {len(group)} units, ~{probes} max probes")
    (REV2 / "egress-plan.json").write_text(json.dumps(
        {"generated_at": utc_now(), "pool": sorted(EGRESS_PORTS), "plan": plan,
         "all_units": pinned}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"retest plan: {len(pinned)} units in {len(names)} shards -> {REV2}")
    return 0


def probe_shard_retest(shard_file: Path, pool: dict) -> int:
    plan_data = json.loads((REV2 / "egress-plan.json").read_text(encoding="utf-8"))
    plan = plan_data["plan"]
    shard = json.loads(shard_file.read_text(encoding="utf-8"))
    jobs = []
    for u in shard["units"]:
        egresses = plan[u["path"]]["egresses"]
        for url in u["urls"][:MAX_URLS]:
            jobs.append((u["path"], url, egresses))
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda j: _probe_multiegross(j, pool), jobs))
    out = REV2 / "probes" / f"{shard['name']}.probes.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"shard": shard["name"], "probes": results},
                              indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    ok = sum(1 for r in results if r["succeeded_egress"])
    print(f"retest-probed {len(jobs)} urls / {len(shard['units'])} units: "
          f"{ok} got HTTP 200 on some egress -> {out}")
    return 0


def _probe_multiegross(job: tuple, pool: dict) -> dict:
    unit, url, egresses = job
    attempts = []
    for eg in egresses:
        r = probe_url(url, cred=pool[eg], egress=eg)
        attempts.append(r)
        if r["status"] == 200:
            break
    return {"unit": unit, "url": url, "attempts": attempts,
            "succeeded_egress": attempts[-1].get("egress")
            if attempts[-1]["status"] == 200 else None}


def validate_retest_verdict(v: dict, where: str) -> list[str]:
    errs = validate_verdict(v, where)
    if not v.get("previous_verdict"):
        errs.append(f"{where}: missing 'previous_verdict'")
    if v["verdict"] == "unreachable" and not (v.get("egress_evidence") or
                                              v.get("evidence", {}).get("per_egress")):
        errs.append(f"{where}: unreachable requires per-egress evidence "
                    f"(egress_evidence or evidence.per_egress)")
    return errs


def cmd_merge_revision() -> int:
    plan_data = json.loads((REV2 / "egress-plan.json").read_text(encoding="utf-8"))
    pinned = {u["path"] for u in plan_data["all_units"]}
    original = json.loads((OUT / "registry.json").read_text(encoding="utf-8"))

    verdicts: dict[str, dict] = {}
    problems: list[str] = []
    vdir = REV2 / "verdicts"
    for vf in sorted(vdir.glob("*.json")) if vdir.is_dir() else []:
        data = json.loads(vf.read_text(encoding="utf-8"))
        for v in data.get("verdicts", []):
            errs = validate_retest_verdict(v, vf.name)
            problems += errs
            if errs:
                continue
            if v["unit"] not in pinned:
                problems.append(f"{vf.name}: {v['unit']} is not in the retest set")
                continue
            if v["unit"] in verdicts:
                problems.append(f"{vf.name}: duplicate verdict for {v['unit']}")
                continue
            verdicts[v["unit"]] = v
    missing = sorted(pinned - set(verdicts))
    problems += [f"missing verdict: {p}" for p in missing]
    if problems:
        print(f"REVISION MERGE INCOMPLETE: {len(problems)} problems")
        for p in problems[:20]:
            print(" -", p)
        if missing:
            (REV2 / "missing.json").write_text(json.dumps(
                {"units": missing}, indent=1) + "\n", encoding="utf-8")
        return 1

    units_out = {}
    for path in sorted(original["units"]):
        if path in verdicts:
            v = dict(verdicts[path])
            orig = original["units"][path]
            v["previous_verdict"] = orig["verdict"]
            units_out[path] = v
        else:
            units_out[path] = original["units"][path]
    counts = {k: 0 for k in sorted(VERDICTS)}
    for v in units_out.values():
        counts[v["verdict"]] += 1
    rescued = sum(1 for p in pinned if units_out[p]["verdict"].startswith("verified"))
    confirmed_dead = sum(1 for p in pinned
                         if units_out[p]["verdict"] == "unreachable"
                         and original["units"][p]["verdict"] == "unreachable")
    registry = {
        "generated_at": utc_now(),
        "revision": 2,
        "summary": {"total": len(units_out), "by_verdict": counts,
                    "retested": len(pinned), "rescued_to_verified": rescued,
                    "confirmed_unreachable": confirmed_dead},
        "units": units_out,
    }
    (REV2 / "registry.json").write_text(json.dumps(
        registry, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = ["# L3 re-analysis report (revision 2: egress-diversified retest)", "",
             f"Generated at: {registry['generated_at']}",
             f"Retested: {len(pinned)} | rescued: {rescued} | confirmed unreachable: "
             f"{confirmed_dead}", "",
             " | ".join(f"{k} {n}" for k, n in counts.items()), "",
             "| Unit | Verdict | Previous | Endpoint |", "|---|---|---|---|"]
    for p in sorted(units_out):
        v = units_out[p]
        prev = f"{v['previous_verdict']}*" if p in pinned else v["verdict"]
        lines.append(f"| `{p}` | {v['verdict']} | {prev} | {v.get('endpoint', '-')} |")
    (REV2 / "reanalysis_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"REV2 MERGED: {len(units_out)} units | rescued {rescued} | "
          f"confirmed unreachable {confirmed_dead} -> {REV2 / 'registry.json'}")
    return 0


# --- merge -------------------------------------------------------------------
def validate_verdict(v: dict, where: str) -> list[str]:
    errs = []
    for k in ("unit", "verdict", "evidence"):
        if k not in v:
            errs.append(f"{where}: missing '{k}'")
    if v.get("verdict") not in VERDICTS:
        errs.append(f"{where}: bad verdict {v.get('verdict')!r}")
    ev = v.get("evidence") or {}
    if not isinstance(ev, dict) or not ev:
        errs.append(f"{where}: evidence must be a non-empty object")
    if v.get("verdict", "").startswith("verified"):
        for k in ("endpoint", "format", "parsing_pointers"):
            if not v.get(k):
                errs.append(f"{where}: verified verdict missing '{k}'")
    return errs


def cmd_merge() -> int:
    plan = json.loads((OUT / "shards.json").read_text(encoding="utf-8"))
    expected = {u["path"] for u in plan["all_units"]}
    verdicts: dict[str, dict] = {}
    problems: list[str] = []
    vdir = OUT / "verdicts"
    seen_shards = set()
    for vf in sorted(vdir.glob("*.json")) if vdir.is_dir() else []:
        data = json.loads(vf.read_text(encoding="utf-8"))
        seen_shards.add(vf.stem)
        for v in data.get("verdicts", []):
            errs = validate_verdict(v, vf.name)
            problems += errs
            if errs:
                continue
            if v["unit"] in verdicts:
                problems.append(f"{vf.name}: duplicate verdict for {v['unit']}")
                continue
            verdicts[v["unit"]] = v
    missing = sorted(expected - set(verdicts))
    extra = sorted(set(verdicts) - expected)
    problems += [f"missing verdict: {p}" for p in missing]
    problems += [f"unknown unit verdict: {p}" for p in extra]

    counts = {v: 0 for v in sorted(VERDICTS)}
    for v in verdicts.values():
        counts[v["verdict"]] += 1

    if missing or extra or problems:
        print(f"MERGE INCOMPLETE: {len(missing)} missing, {len(extra)} unknown, "
              f"{len(problems)} problems")
        for p in problems[:20]:
            print(" -", p)
        if missing:
            miss_file = OUT / "missing.json"
            miss_file.write_text(json.dumps({"units": missing}, indent=1) + "\n",
                                 encoding="utf-8")
            print(f"missing list -> {miss_file}")
        return 1

    registry = {
        "generated_at": utc_now(),
        "summary": {"total": len(verdicts), "by_verdict": counts},
        "units": {p: verdicts[p] for p in sorted(verdicts)},
    }
    (OUT / "registry.json").write_text(
        json.dumps(registry, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = ["# L3 re-analysis report", "",
             f"Generated at: {registry['generated_at']}", "",
             f"Units: {len(verdicts)} | " +
             " | ".join(f"{k} {n}" for k, n in counts.items()), "",
             "| Unit | Verdict | Endpoint |", "|---|---|---|"]
    for p in sorted(verdicts):
        v = verdicts[p]
        lines.append(f"| `{p}` | {v['verdict']} | {v.get('endpoint', '-')} |")
    (OUT / "reanalysis_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"MERGED: {len(verdicts)} units, {counts} -> {OUT / 'registry.json'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--shards", type=int, default=8)
    ap.add_argument("--probe-shard", type=Path, default=None)
    ap.add_argument("--probe-url", default=None, help="probe a single URL (for no-URL units)")
    ap.add_argument("--make-shard", default=None, help="comma-separated unit paths")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--retest-plan", action="store_true",
                    help="pin unreachable+degraded units, route egresses, cut shards")
    ap.add_argument("--egress-plan", action="store_true",
                    help="with --probe-shard: multi-egress retest probing")
    ap.add_argument("--merge-revision", action="store_true",
                    help="merge retest verdicts into revision-2 registry")
    args = ap.parse_args()

    if args.retest_plan:
        return cmd_retest_plan(args.shards if args.shards != 8 else 4)
    if args.probe_shard and args.egress_plan:
        return probe_shard_retest(args.probe_shard, load_egress_pool())
    if args.merge_revision:
        return cmd_merge_revision()
    if args.plan:
        return cmd_plan(args.shards)
    if args.probe_shard:
        return probe_shard(args.probe_shard)
    if args.probe_url:
        return cmd_probe_url(args.probe_url)
    if args.make_shard:
        return cmd_make_shard([p.strip() for p in args.make_shard.split(",") if p.strip()],
                              args.out or (OUT / "shards" / "followup.json"))
    if args.merge:
        return cmd_merge()
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
