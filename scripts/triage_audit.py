#!/usr/bin/env python3
"""Read-only triage audit of generated crawler code.

Inventories every generated crawler unit (spider directories, loose spider
files, adapters, abandoned scraw-* scaffolds) in fd-industry-data and
fd-open-data-mcp, classifies each into L1-L4 by static evidence, aggregates
by canonical source domain, and emits a triage report plus a derived cleanup
backlog. Never executes scanned code and never modifies scanned files.

Spec: openspec/changes/spider-knowledge-triage/ (capability spider-knowledge-triage)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent          # fd-industry-data
FINDDATA_ROOT = REPO_ROOT.parent
DEFAULT_ROOTS = [REPO_ROOT, FINDDATA_ROOT / "fd-open-data-mcp"]
DEFAULT_OUTPUT = REPO_ROOT / "output" / "triage"

# --- discovery ---------------------------------------------------------------
PRUNE_DIRS = {"archive", ".venv", ".git", "__pycache__", "node_modules", ".idea",
              ".vscode", "data", "output", "logs"}
PRUNE_SUFFIXES = (".egg-info",)
SPIDER_FILENAME = "spider.py"
LOOSE_SPIDER_RE = re.compile(r"_spider\.py$")
ADAPTERS_REL = Path("fd_open_data_mcp") / "adapters"

# fd-industry-data top-level dirs that are intentional; anything else that is
# (near-)empty at top level is typo-dir debris.
STANDARD_TOPDIRS = {"fd_industry_data", "manifests", "spiders", "scripts", "tests",
                    "docs", "k8s"}
DOC_WHITELIST = {"README.md", "README.zh-CN.md", "INSTALL.md", "CONFIG.md",
                 "CHANGELOG.md", "AGENTS.md", "CLAUDE.md"}
LITTER_SUFFIXES = (".md", ".sh", ".txt")

# --- static signals ----------------------------------------------------------
# Net-call evidence: explicit HTTP call sites outrank bare library imports so a
# bare `import scrapling.spiders` (base class only) never marks a unit as fetching.
NET_CALL_RES = [
    r"requests\.(?:get|post|head|put|delete|Session|session)",
    r"urllib\.request", r"\burlopen\b",
    r"from scrapling\.fetchers", r"FetcherSession", r"\.fetch\(",
    r"\bhttpx\b", r"\baiohttp\b",
    r"\b(?:import|from)\s+akshare\b", r"\b(?:import|from)\s+wbgapi\b", r"\bdatacommons",
]
NET_CALL_STRONG_RES = [
    r"requests\.(?:get|post|head|put|delete|Session|session)",
    r"urllib\.request", r"\burlopen\b",
    r"from scrapling\.fetchers", r"FetcherSession", r"\.fetch\(",
]
DATAFRAME_RE = re.compile(r"pd\.DataFrame\s*\(")
DATE_LITERAL_RE = re.compile(r"[\"']20\d{2}-\d{2}-\d{2}")
URL_RE = re.compile(r"https?://[^\s\"'<>]+")
API_LIKE_RE = re.compile(r"/api/|\.json|ajax|rest/|interface", re.IGNORECASE)
DOMAIN_SKIP = {"localhost", "127.0.0.1", "example.com"}
MULTI_SUFFIXES = {"com.cn", "org.cn", "net.cn", "gov.cn", "edu.cn", "ac.cn",
                  "co.jp", "co.uk", "org.uk", "gov.uk", "com.au", "com.hk",
                  "com.tw", "org.hk"}
HOST_PREFIX_STRIP = {"www", "data", "m", "en", "mp"}
LEVEL_RANK = {"L1": 1, "L2": 2, "L3": 3, "L4": 4}

LEVEL_REASONS = {
    "L1": "net fetch + output artifacts + API/JSON endpoints",
    "L2": "net fetch + output artifacts (HTML)",
    "L3": "net fetch present but no run artifacts; unverified",
    "L4-fake": "no net call; hardcoded sample rows",
    "L4-stub": "no net call, no hardcoded rows, no artifacts",
    "L4-husk": "empty scaffold directory",
}


def is_pruned(path: Path) -> bool:
    return path.name in PRUNE_DIRS or path.name.endswith(PRUNE_SUFFIXES)


def walk_dirs(root: Path):
    """All non-pruned directories under root (root included), deterministically."""
    stack = [root]
    while stack:
        d = stack.pop()
        yield d
        for child in sorted(d.iterdir(), key=lambda p: p.name):
            if child.is_dir() and not is_pruned(child):
                stack.append(child)


def non_empty(dirpath: Path) -> bool:
    if not dirpath.is_dir():
        return False
    return any(p.name not in {".DS_Store"} and p.name != "__pycache__"
               for p in dirpath.iterdir())


def has_any_files(dirpath: Path) -> bool:
    """True if the tree holds any regular file besides .DS_Store / __pycache__."""
    for p in dirpath.rglob("*"):
        if p.is_file() and p.name not in {".DS_Store"} and "__pycache__" not in p.parts:
            return True
    return False


# --- signals & classification ------------------------------------------------
def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def unit_py_files(unit_dir: Path, kind: str) -> list[Path]:
    if kind in ("loose_file", "adapter"):
        return [unit_dir]
    return sorted(p for p in unit_dir.iterdir() if p.is_file() and p.suffix == ".py")


def extract_urls(text: str) -> list[str]:
    urls = []
    for m in URL_RE.finditer(text):
        urls.append(m.group(0).rstrip(")];,."))
    return urls


def url_host(url: str) -> str:
    raw = url.split("://", 1)[1].split("/", 1)[0]
    raw = raw.split("{")[0]  # f-string template placeholders are not host chars
    return raw.split("@")[-1].split(":")[0].lower().rstrip(".")


PURE_SUFFIXES = MULTI_SUFFIXES | {"com", "org", "net", "gov", "edu", "cn", "io", "info"}


def canon_domain(host: str) -> str | None:
    if not host or host in DOMAIN_SKIP:
        return None
    if re.fullmatch(r"[\d.]+", host):  # bare IP
        return None
    parts = host.split(".")
    while len(parts) > 2 and parts[0] in HOST_PREFIX_STRIP:
        parts = parts[1:]
    if ".".join(parts[-2:]) in MULTI_SUFFIXES and len(parts) >= 3:
        candidate = ".".join(parts[-3:])
    elif len(parts) >= 2:
        candidate = ".".join(parts[-2:])
    else:
        return None
    if candidate in PURE_SUFFIXES:  # e.g. templated URL degraded to bare suffix
        return None
    return candidate


def norm_slug(name: str) -> str:
    s = re.sub(r"[-_]", "", name.lower())
    for suffix in ("spider", "scraper"):
        if s.endswith(suffix) and len(s) - len(suffix) >= 3:
            s = s[: -len(suffix)]
    return s


def signals_for(files: list[Path], unit_dir: Path, kind: str) -> dict:
    texts = [(p.name, read_text(p)) for p in files]
    joined = "\n".join(t for _, t in texts)
    strong = any(re.search(rx, joined) for rx in NET_CALL_STRONG_RES)
    weak = any(re.search(rx, joined) for rx in NET_CALL_RES)
    net_call = strong or weak
    return {
        "net_call": net_call,
        "net_call_strong": strong,
        "hardcoded_rows": (not net_call and bool(DATAFRAME_RE.search(joined))
                           and bool(DATE_LITERAL_RE.search(joined))),
        "has_output_artifacts": (non_empty(unit_dir / "data") or non_empty(unit_dir / "output")),
        "is_empty_husk": kind == "scraw_husk" or not texts,
        "stub": None,  # derived below
    }


def registry_referenced(unit_path: Path, kind: str) -> bool:
    """Adapter module wired into adapters/__init__.py (register-style param-mapping
    registrar). Such modules intentionally never fetch — the fetching happens in
    fetch/runner at call time — so name-reference in the registry file is the signal."""
    if kind != "adapter":
        return False
    init = unit_path.parent / "__init__.py"
    return bool(re.search(rf"\b{re.escape(unit_path.stem)}\b", read_text(init)))


def classify(sig: dict, api_like: bool) -> tuple[str, str]:
    if sig["is_empty_husk"]:
        return "L4", "husk"
    if not sig["net_call"]:
        if sig["hardcoded_rows"] and not sig.get("register_style"):
            return ("L4", "fake")
        return "L4", "stub"
    if sig["has_output_artifacts"]:
        return ("L1", "api") if api_like else ("L2", "html")
    return "L3", "net-unverified"


# --- unit discovery ----------------------------------------------------------
def make_unit(unit_dir: Path, repo_root: Path, kind: str, slug: str) -> dict:
    files = unit_py_files(unit_dir, kind)
    sig = signals_for(files, unit_dir if kind != "loose_file" else unit_dir.parent, kind)
    register_style = registry_referenced(unit_dir, kind)
    sig["register_style"] = register_style
    if not sig["net_call"] and not sig["hardcoded_rows"] and not sig["has_output_artifacts"] \
            and not sig["is_empty_husk"]:
        sig["stub"] = True
    else:
        sig["stub"] = False
    urls = sorted({u for _, t in [(p.name, read_text(p)) for p in files] for u in extract_urls(t)})
    domains = sorted({d for d in (canon_domain(url_host(u)) for u in urls) if d})
    lib_fetch = bool(re.search(r"\b(?:import|from)\s+(?:akshare|wbgapi)\b|\bdatacommons",
                               "\n".join(read_text(p) for p in files)))
    api_like = lib_fetch or bool(any(API_LIKE_RE.search(u) for u in urls))
    level, detail = classify(sig, api_like)
    rel = unit_dir.relative_to(repo_root).as_posix()
    u = {
        "path": rel,
        "kind": kind,
        "slug": slug,
        "normalized_slug": norm_slug(slug),
        "level": level,
        "level_reason": detail,
        "register_style": register_style if kind == "adapter" else False,
        "confidence": "high" if (sig["net_call_strong"] or level == "L4"
                                 or sig["has_output_artifacts"]) else "low",
        "signals": sig,
        "api_like": api_like,
        "domains": domains,
        "urls": urls,
        "flags": [],
    }
    return u


def discover_units(repo_root: Path) -> tuple[list[dict], list[str], list[str], list[str]]:
    """Return (units, litter_files, empty_dirs, codeless_spider_dirs), repo-relative."""
    units: list[dict] = []
    spider_dirs = [d for d in walk_dirs(repo_root)
                   if (d / SPIDER_FILENAME).is_file() and d != repo_root]
    for d in spider_dirs:
        units.append(make_unit(d, repo_root, "spider_dir", d.name))

    in_spider_dir = {d for d in spider_dirs}
    for d in walk_dirs(repo_root):
        for f in sorted(d.glob("*_spider.py")):
            if f.parent not in in_spider_dir and f.name != SPIDER_FILENAME:
                units.append(make_unit(f, repo_root, "loose_file", f.stem))

    adapters_dir = repo_root / ADAPTERS_REL
    if adapters_dir.is_dir():
        for f in sorted(adapters_dir.glob("*.py")):
            if f.name != "__init__.py":
                units.append(make_unit(f, repo_root, "adapter", f.stem))

    litter: list[str] = []
    empty_dirs: list[str] = []
    codeless: list[str] = []
    for child in sorted(repo_root.iterdir(), key=lambda p: p.name):
        if child.is_file() and child.suffix in LITTER_SUFFIXES \
                and child.name not in DOC_WHITELIST:
            litter.append(child.relative_to(repo_root).as_posix())
        if child.is_dir() and not is_pruned(child) and child.name not in STANDARD_TOPDIRS:
            if not has_any_files(child) and not child.name.startswith("scraw-"):
                empty_dirs.append(child.relative_to(repo_root).as_posix())

    for child in sorted((repo_root / "spiders").glob("*") if (repo_root / "spiders").is_dir() else []):
        if child.is_file() and child.suffix in LITTER_SUFFIXES and child.name != "README.md":
            litter.append(child.relative_to(repo_root).as_posix())
    spiers = repo_root / "spiers"
    if spiers.is_dir():
        for child in sorted(spiers.iterdir(), key=lambda p: p.name):
            if child.is_file() and (child.suffix in LITTER_SUFFIXES
                                    or child.name.startswith("test_")
                                    or child.name == ".DS_Store"):
                litter.append(child.relative_to(repo_root).as_posix())

    # abandoned scraw-* scaffolds are themselves units (L4 husks)
    for child in sorted(repo_root.glob("scraw-*")):
        if child.is_dir():
            units.append(make_unit(child, repo_root, "scraw_husk", child.name))

    # spider-shaped subdirectories holding no code at all (README-only husks,
    # empty data/output shells) are dead weight, not backfill candidates
    for parent in ("spiders", "spiers"):
        pdir = repo_root / parent
        if not pdir.is_dir():
            continue
        for child in sorted(pdir.iterdir(), key=lambda p: p.name):
            if child.is_dir() and not any(child.glob("*.py")):
                codeless.append(child.relative_to(repo_root).as_posix())

    return sorted(units, key=lambda u: u["path"]), sorted(litter), sorted(empty_dirs), \
        sorted(codeless)


# --- aggregation -------------------------------------------------------------
def manifest_coverage(units: list[dict], repo_root: Path) -> None:
    registry = {}
    mdir = repo_root / "manifests"
    if mdir.is_dir():
        for f in mdir.glob("*.yaml"):
            registry[norm_slug(f.stem)] = f.name
    for u in units:
        if u["kind"] == "spider_dir" and u["path"].startswith("spiders/"):
            u["manifest_present"] = (repo_root / u["path"] / "manifest.yaml").is_file()
            u["registry_present"] = registry.get(u["normalized_slug"])
        else:
            u["manifest_present"] = None
            u["registry_present"] = None


def aggregate(all_units: list[tuple[str, dict]]) -> dict:
    # duplicate detection is slug-global, independent of domain attribution:
    # hardcoded fake adapters often carry no URL literals at all, yet their
    # dash/underscore variants are still duplicates.
    tagged = []
    for repo, u in all_units:
        u = dict(u)
        u["repo"] = repo
        tagged.append(u)
    by_slug: dict[str, list[dict]] = {}
    for u in tagged:
        by_slug.setdefault(u["normalized_slug"], []).append(u)
    for slug, group in by_slug.items():
        if len(group) > 1:
            drift = len({x["slug"] for x in group}) > 1
            flag = "naming_drift" if drift else "location_duplicate"
            for x in group:
                if flag not in x["flags"]:
                    x["flags"].append(flag)

    domains: dict[str, dict] = {}
    unattributed = []
    for u in tagged:
        if not u["domains"]:
            unattributed.append(u)
            continue
        for d in u["domains"]:
            rec = domains.setdefault(d, {"domain": d, "units": [], "flags": []})
            rec["units"].append(u)
    for rec in domains.values():
        rec["units"].sort(key=lambda x: x["path"])
        best = min((x["level"] for x in rec["units"]), key=lambda l: LEVEL_RANK[l])
        rec["level"] = best
        rec["lead_only"] = all(x["level"] == "L4" for x in rec["units"])
        rec["flags"] = sorted({f for x in rec["units"] for f in x["flags"]})
    domains_sorted = {d: domains[d] for d in sorted(domains)}
    unattributed.sort(key=lambda x: x["path"])
    return {"domains": domains_sorted, "unattributed_units": unattributed,
            "slug_groups": {s: g for s, g in sorted(by_slug.items()) if len(g) > 1}}


# --- backlog -----------------------------------------------------------------
def derive_backlog(agg: dict, all_units: list[dict], litter: list[str],
                   empty_dirs: list[str], codeless_dirs: list[str]) -> dict:
    # register-style adapters are excluded from delete candidates: the registry
    # reference is proof of wiring, and liveness is re-judged by retest/re-analysis
    l4_units = [u for u in all_units
                if u["level"] == "L4" and not u.get("register_style")]
    l3_units = [u for u in all_units if u["level"] == "L3"]
    consolidate = [{"normalized_slug": s,
                    "domains": sorted({d for x in g for d in x["domains"]}) or ["(no domain)"],
                    "units": [x["path"] for x in g]}
                   for s, g in agg["slug_groups"].items()]
    backfill = [{"path": u["path"], "registry_present": u["registry_present"]}
                for u in all_units
                if u.get("manifest_present") is False]
    return {
        "archive_delete": {
            "empty_dirs": empty_dirs,
            "codeless_spider_dirs": codeless_dirs,
            "litter_files": litter,
            "l4_units": [u["path"] for u in l4_units],
        },
        "consolidate_duplicates": consolidate,
        "manifest_backfill": backfill,
        "re_analysis": [{"path": u["path"], "domains": u["domains"]} for u in l3_units],
    }


# --- outputs -----------------------------------------------------------------
def write_outputs(agg: dict, backlog: dict, units: list[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    levels = {l: sum(1 for u in units if u["level"] == l) for l in ("L1", "L2", "L3", "L4")}
    summary = {
        "total_units": len(units),
        "units_by_level": levels,
        "distinct_domains": len(agg["domains"]),
        "duplicate_domains": sum(1 for r in agg["domains"].values() if r["flags"]),
        "unattributed_units": len(agg["unattributed_units"]),
    }
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": summary,
        "domains": agg["domains"],
        "slug_duplicate_groups": agg["slug_groups"],
        "unattributed_units": agg["unattributed_units"],
    }
    (out_dir / "triage.json").write_text(json.dumps(payload, indent=2, sort_keys=False,
                                                    ensure_ascii=False) + "\n",
                                          encoding="utf-8")
    (out_dir / "cleanup_backlog.json").write_text(
        json.dumps({"generated_at": payload["generated_at"], "backlog": backlog},
                   indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [f"# Spider knowledge triage report",
             "",
             f"Generated at: {payload['generated_at']}",
             "",
             f"- Total units: **{summary['total_units']}** "
             f"(L1 {levels['L1']} / L2 {levels['L2']} / L3 {levels['L3']} / L4 {levels['L4']})",
             f"- Distinct source domains: **{summary['distinct_domains']}** "
             f"({summary['duplicate_domains']} with duplicates)",
             f"- Unattributed units (no URL literals): {summary['unattributed_units']}",
             "",
             "Levels: L1 verified API (static) | L2 verified HTML (static) | "
             "L3 real domain, unverified parsing | L4 fabricated/husk/stub",
             "",
             "| Domain | Level | Units | Flags | Lead only |",
             "|---|---|---|---|---|"]
    for rec in agg["domains"].values():
        lines.append(f"| {rec['domain']} | {rec['level']} | {len(rec['units'])} "
                     f"| {', '.join(rec['flags']) or '-'} | {'yes' if rec['lead_only'] else '-'} |")
    lines += ["", "## Domain details", ""]
    for rec in agg["domains"].values():
        lines.append(f"### {rec['domain']} — {rec['level']}"
                     f"{' (lead only)' if rec['lead_only'] else ''}")
        for u in rec["units"]:
            man = ""
            if u.get("manifest_present") is not None:
                man = f", manifest={'yes' if u['manifest_present'] else 'NO'}"
            reg = ", register-style" if u.get("register_style") else ""
            lines.append(f"- `{u['path']}` [{u['kind']}, {u['level']}/{u['level_reason']}, "
                         f"confidence {u['confidence']}{man}{reg}"
                         f"{', ' + '/'.join(u['flags']) if u['flags'] else ''}]")
        lines.append("")
    if agg["unattributed_units"]:
        lines += ["## Unattributed units", ""]
        for u in agg["unattributed_units"]:
            lines.append(f"- `{u['path']}` [{u['kind']}, {u['level']}/{u['level_reason']}]")
        lines.append("")
    (out_dir / "triage_report.md").write_text("\n".join(lines), encoding="utf-8")

    bl = ["# Cleanup backlog (plan only — nothing has been deleted)", "",
          f"Generated at: {payload['generated_at']}", ""]
    bl += [f"## Archive/delete candidates", "",
           f"- Empty non-standard directories ({len(backlog['archive_delete']['empty_dirs'])}):"]
    bl += [f"  - `{p}`" for p in backlog["archive_delete"]["empty_dirs"]]
    bl += [f"- Codeless spider-shaped directories "
           f"({len(backlog['archive_delete']['codeless_spider_dirs'])}):"]
    bl += [f"  - `{p}`" for p in backlog["archive_delete"]["codeless_spider_dirs"]]
    bl += [f"- Session litter files ({len(backlog['archive_delete']['litter_files'])}):"]
    bl += [f"  - `{p}`" for p in backlog["archive_delete"]["litter_files"]]
    bl += [f"- L4 units ({len(backlog['archive_delete']['l4_units'])}):"]
    bl += [f"  - `{p}`" for p in backlog["archive_delete"]["l4_units"]]
    bl += ["", f"## Consolidate duplicates ({len(backlog['consolidate_duplicates'])})", ""]
    for c in backlog["consolidate_duplicates"]:
        bl.append(f"- **{c['normalized_slug']}** ({', '.join(c['domains'])}): "
                  + ", ".join(f"`{p}`" for p in c["units"]))
    bl += ["", f"## Manifest backfill ({len(backlog['manifest_backfill'])})", ""]
    bl += [f"- `{b['path']}` (registry: {b['registry_present'] or 'none'})"
           for b in backlog["manifest_backfill"]]
    bl += ["", f"## Re-analysis candidates — L3 ({len(backlog['re_analysis'])})", ""]
    bl += [f"- `{r['path']}` → {', '.join(r['domains'])}" for r in backlog["re_analysis"]]
    (out_dir / "cleanup_backlog.md").write_text("\n".join(bl) + "\n", encoding="utf-8")


# --- self-test ---------------------------------------------------------------
def self_test() -> bool:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "api_unit").mkdir()
        (root / "api_unit" / "spider.py").write_text(
            "import requests\n"
            "r = requests.get('https://api.sample-source.org/v2/data.json')\n")
        (root / "api_unit" / "data").mkdir()
        (root / "api_unit" / "data" / "out.db").write_text("x")
        (root / "html_unit").mkdir()
        (root / "html_unit" / "spider.py").write_text(
            "import requests\n"
            "r = requests.get('https://www.sample-source.org/reports/monthly/')\n")
        (root / "html_unit" / "output").mkdir()
        (root / "html_unit" / "output" / "r.jsonl").write_text("{}")
        (root / "netonly_unit").mkdir()
        (root / "netonly_unit" / "spider.py").write_text(
            "import requests\n"
            "r = requests.get('https://news.sample-source.org/list')\n")
        (root / "fake_unit").mkdir()
        (root / "fake_unit" / "spider.py").write_text(
            "import pandas as pd\n"
            "# supposed to scrape https://www.lead-only-source.org/data\n"
            "df = pd.DataFrame({'date': ['2024-07-31', '2024-07-30']})\n")
        (root / "scraw-husk").mkdir()
        (root / "README.md").write_text("# x\n")
        # register-style adapter fixture: referenced registrar (hardcoded rows would
        # condemn it as L4-fake) vs an unreferenced fake that must stay condemned
        adapters = root / ADAPTERS_REL
        adapters.mkdir(parents=True)
        (adapters / "__init__.py").write_text(
            "from fd_open_data_mcp.adapters import reg_adapter\n")
        (adapters / "reg_adapter.py").write_text(
            "import pandas as pd\n"
            "df = pd.DataFrame({'date': ['2024-07-31']})\n")
        (adapters / "orphan_fake.py").write_text(
            "import pandas as pd\n"
            "df = pd.DataFrame({'date': ['2024-07-31']})\n")

        units, litter, empty, codeless = discover_units(root)
        by_name = {u["path"]: u for u in units}
        cases = [
            ("api_unit", "L1"), ("html_unit", "L2"),
            ("netonly_unit", "L3"), ("fake_unit", "L4"),
        ]
        ok = True
        for name, expect in cases:
            got = by_name.get(name, {}).get("level")
            status = "PASS" if got == expect else "FAIL"
            if got != expect:
                ok = False
            print(f"  [{status}] {name}: expected {expect}, got {got}")
        husk = [u for u in units if u["kind"] == "scraw_husk"]
        hstatus = "PASS" if len(husk) == 1 and husk[0]["level"] == "L4" else "FAIL"
        if hstatus == "FAIL":
            ok = False
        print(f"  [{hstatus}] scraw-husk: expected 1 L4 husk, got {husk}")
        agg = aggregate([("fixture", u) for u in units])
        backlog = derive_backlog(agg, units, [], [], [])
        reg = by_name.get("fd_open_data_mcp/adapters/reg_adapter.py", {})
        orphan = by_name.get("fd_open_data_mcp/adapters/orphan_fake.py", {})
        delete_list = set(backlog["archive_delete"]["l4_units"])
        reg_ok = (reg.get("level_reason") != "fake" and reg.get("register_style") is True
                  and "fd_open_data_mcp/adapters/reg_adapter.py" not in delete_list)
        orphan_ok = (orphan.get("level_reason") == "fake"
                     and not orphan.get("register_style")
                     and "fd_open_data_mcp/adapters/orphan_fake.py" in delete_list)
        rstatus = "PASS" if reg_ok and orphan_ok else "FAIL"
        if not (reg_ok and orphan_ok):
            ok = False
        print(f"  [{rstatus}] register-style: reg_adapter={reg.get('level')}/"
              f"{reg.get('level_reason')} marker={reg.get('register_style')} "
              f"in_delete={'fd_open_data_mcp/adapters/reg_adapter.py' in delete_list}; "
              f"orphan_fake={orphan.get('level')}/{orphan.get('level_reason')} "
              f"in_delete={'fd_open_data_mcp/adapters/orphan_fake.py' in delete_list}")
        rec = agg["domains"].get("lead-only-source.org")
        sample = agg["domains"].get("sample-source.org")
        lead_ok = (rec is not None and rec.get("lead_only") is True
                   and sample is not None and sample.get("lead_only") is False)
        lstatus = "PASS" if lead_ok else "FAIL"
        if not lead_ok:
            ok = False
        print(f"  [{lstatus}] fake_unit keeps real domain as lead: "
              f"lead-only-source.org={rec is not None and rec.get('lead_only')}, "
              f"sample-source.org lead_only={sample.get('lead_only') if sample else None}")
        return ok


# --- main --------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="print inventory counts without writing outputs")
    ap.add_argument("--self-test", action="store_true",
                    help="run fixture classification tests")
    ap.add_argument("--roots", default=None,
                    help="comma-separated repo roots to scan (default: fd-industry-data + fd-open-data-mcp)")
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    args = ap.parse_args()

    if args.self_test:
        print("self-test:")
        return 0 if self_test() else 1

    roots = [Path(p) for p in args.roots.split(",")] if args.roots else DEFAULT_ROOTS
    all_units: list[dict] = []
    litter, empty_dirs, codeless_dirs = [], [], []
    for root in roots:
        units, lt, ed, cd = discover_units(root)
        manifest_coverage(units, root)
        all_units += units
        litter += [f"{root.name}/{p}" for p in lt]
        empty_dirs += [f"{root.name}/{p}" for p in ed]
        codeless_dirs += [f"{root.name}/{p}" for p in cd]
    all_units.sort(key=lambda u: u["path"])

    if args.dry_run:
        kinds = {}
        for u in all_units:
            kinds[u["kind"]] = kinds.get(u["kind"], 0) + 1
        print(f"units total: {len(all_units)} by kind: {json.dumps(kinds, sort_keys=True)}")
        print(f"litter files: {len(litter)}, empty dirs: {len(empty_dirs)}, "
              f"codeless spider dirs: {len(codeless_dirs)}")
        print(f"empty dirs: {empty_dirs}")
        print(f"codeless spider dirs: {codeless_dirs[:10]}{' ...' if len(codeless_dirs) > 10 else ''}")
        for probe in ("spiers/", "scraw-flowers-kifc", "fd_open_data_mcp/adapters/"):
            hits = [u["path"] for u in all_units if probe in u["path"]]
            print(f"  probe '{probe}': {len(hits)} units"
                  + (f" e.g. {hits[:3]}" if hits else ""))
        return 0

    tagged = []
    for u in all_units:
        tagged.append(("fd-industry-data" if not u["path"].startswith("fd_open_data_mcp")
                       else "fd-open-data-mcp", u))
    agg = aggregate(tagged)
    backlog = derive_backlog(agg, all_units, litter, empty_dirs, codeless_dirs)
    out_dir = Path(args.output_dir)
    write_outputs(agg, backlog, all_units, out_dir)
    s = {u["path"] for u in all_units}
    print(f"triage complete: {len(all_units)} units, "
          f"{len(agg['domains'])} domains -> {out_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
