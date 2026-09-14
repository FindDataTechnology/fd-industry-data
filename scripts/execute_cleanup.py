#!/usr/bin/env python3
"""Execute the triage cleanup backlog: archive dead crawler code, consolidate duplicates.

Reads targets exclusively from the pinned triage cleanup backlog (never re-derives),
moves them into per-repo archive trees preserving repo-relative paths, and verifies
post-conditions by re-running the read-only triage audit. Dry-run by default;
--apply executes. Spec: openspec/changes/spider-cleanup-execution/.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent          # fd-industry-data
FINDDATA_ROOT = REPO_ROOT.parent
MCP_ROOT = FINDDATA_ROOT / "fd-open-data-mcp"
DEFAULT_BACKLOG = REPO_ROOT / "output" / "triage" / "cleanup_backlog.json"
DEFAULT_POST_AUDIT = REPO_ROOT / "output" / "triage-post-cleanup"
REF_EXCLUDE_PARTS = {".venv", "__pycache__", ".git", ".idea", ".vscode", "node_modules"}
REF_EXCLUDE_SUFFIX = (".egg-info",)
ADAPTERS_REL = Path("fd_open_data_mcp") / "adapters"
RUNNER_REL = Path("fd_open_data_mcp") / "fetch" / "runner.py"
LEVEL_RANK = {"L1": 1, "L2": 2, "L3": 3, "L4": 4}


def today_tag() -> str:
    return datetime.now().strftime("%Y%m%d")


def repo_for(rel_path: str) -> tuple[Path, str]:
    """Map a backlog path (prefix styles vary across audit sections) to (repo, rel)."""
    if rel_path.startswith("fd-open-data-mcp/"):
        return MCP_ROOT, rel_path[len("fd-open-data-mcp/"):]
    if rel_path.startswith("fd-industry-data/"):
        return REPO_ROOT, rel_path[len("fd-industry-data/"):]
    if rel_path.startswith("fd_open_data_mcp/"):  # raw in-repo package path
        return MCP_ROOT, rel_path
    return REPO_ROOT, rel_path


def backlog_paths(backlog: dict) -> list[tuple[str, str]]:
    """Flatten archive_delete entries into (repo-prefixed path, category) pairs."""
    out = []
    ad = backlog["archive_delete"]
    for category in ("empty_dirs", "codeless_spider_dirs", "litter_files", "l4_units"):
        for p in ad.get(category, []):
            out.append((p, category))
    return out


def is_git_tracked(repo: Path, rel: str) -> bool:
    r = subprocess.run(["git", "ls-files", "--error-unmatch", rel],
                       cwd=repo, capture_output=True)
    return r.returncode == 0


def plan_moves(backlog: dict) -> tuple[list[dict], list[dict]]:
    """Return (moves, skipped). Each move: {src_repo, src_rel, category, tracked}."""
    moves, skipped = [], []
    for path, category in backlog_paths(backlog):
        repo, rel = repo_for(path)
        if not (repo / rel).exists():
            skipped.append({"path": path, "reason": "not found"})
            continue
        moves.append({"src_repo": repo, "src_rel": rel,
                      "category": category,
                      "tracked": is_git_tracked(repo, rel)})
    return moves, skipped


def consolidate_groups(backlog: dict) -> tuple[list[dict], list[dict]]:
    """Apply D3 winner precedence to backlog consolidation groups.

    Members already condemned by a category move (L4/codeless/litter/empty) are not
    retained: a winner is chosen only among surviving members, and a group whose
    members are all condemned is fully archived by those category moves (winner=None).
    Returns (plans, skipped); each plan = {slug, winner|None, losers[]}.
    """
    condemned = {p for p, _ in backlog_paths(backlog)}
    plans, skipped = [], []
    for group in backlog["consolidate_duplicates"]:
        members = []
        for path in group["units"]:
            repo, rel = repo_for(path)
            if not (repo / rel).exists():
                skipped.append({"path": path, "reason": "not found"})
                continue
            members.append({"repo": repo, "rel": rel, "path": path,
                            "condemned": path in condemned})
        if not members:
            continue
        survivors = [m for m in members if not m["condemned"]]
        if not survivors:
            plans.append({"slug": group["normalized_slug"], "winner": None,
                          "losers": [], "all_condemned": True})
            continue
        if len(survivors) == 1:
            plans.append({"slug": group["normalized_slug"], "winner": survivors[0],
                          "losers": [], "all_condemned": False})
            continue

        def key(m):
            loc = 0 if m["rel"].startswith("spiders/") else (1 if m["rel"].startswith("spiers/") else 2)
            manifest = 1 if (m["repo"] / m["rel"]).is_dir() and (m["repo"] / m["rel"] / "manifest.yaml").is_file() else 0
            level = LEVEL_RANK.get(member_level(m), 4)
            return (loc, manifest, level, m["rel"])

        ranked = sorted(survivors, key=key)
        plans.append({"slug": group["normalized_slug"], "winner": ranked[0],
                      "losers": ranked[1:], "all_condemned": False})
    return plans, skipped


_MEMBER_LEVEL = {}


def member_level(m: dict) -> str:
    return _MEMBER_LEVEL.get(m["path"], "L4")


def load_member_levels(triage_json: Path) -> None:
    """Feed unit levels from the audit output so winner ranking can prefer better levels."""
    if not triage_json.is_file():
        return
    data = json.loads(triage_json.read_text(encoding="utf-8"))
    for rec in data.get("domains", {}).values():
        for u in rec["units"]:
            _MEMBER_LEVEL[u["path"]] = u["level"]
    for u in data.get("unattributed_units", []):
        _MEMBER_LEVEL[u["path"]] = u["level"]


def find_references(module: str) -> list[tuple[Path, int]]:
    """References to `module` (e.g. 'cisa_industry') outside venv/self/archive."""
    hits = []
    pat = re.compile(rf"\b{re.escape(module)}\b")
    for repo in (REPO_ROOT, MCP_ROOT):
        if not repo.is_dir():
            continue
        for p in repo.rglob("*.py"):
            parts = set(p.parts)
            if parts & REF_EXCLUDE_PARTS or p.name.endswith(REF_EXCLUDE_SUFFIX):
                continue
            if "archive" in p.parts:
                continue
            if p.name == f"{module}.py":
                continue
            try:
                for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if pat.search(line):
                        hits.append((p, i))
            except OSError:
                continue
    return hits


RUNNER_BRANCH_RE = re.compile(
    r"[ \t]*if\s+source\s*==\s*['\"](?P<src>[\w-]+)['\"]\s*:\s*\n"
    r"[ \t]+from fd_open_data_mcp\.adapters\.(?P<mod>\w+) import \w+\n"
    r"[ \t]+return \w+\(command, params\)\n",
)


def runner_branches_for(modules: list[str]) -> list[tuple[str, str]]:
    """(source_slug, module) lazy-dispatch branches in fetch/runner.py for given modules."""
    runner = MCP_ROOT / RUNNER_REL
    if not runner.is_file():
        return []
    text = runner.read_text(encoding="utf-8")
    wanted = set(modules)
    out = []
    for m in RUNNER_BRANCH_RE.finditer(text):
        if m.group("mod") in wanted:
            out.append((m.group("src"), m.group("mod")))
    return out


def remove_runner_branches(repo: Path, modules: list[str], index: dict) -> list[str]:
    """Remove `if source == "<slug>": from adapters.<mod> import ...` dispatch branches
    for archived modules. Single-pass rebuild (offset-safe), syntax-checked before write."""
    runner = repo / RUNNER_REL
    text = runner.read_text(encoding="utf-8")
    wanted = set(modules)
    out, last, removed = [], 0, []
    for m in RUNNER_BRANCH_RE.finditer(text):
        if m.group("mod") in wanted:
            out.append(text[last:m.start()])
            last = m.end()
            removed.append(m.group("mod"))
    out.append(text[last:])
    candidate = "".join(out)
    import ast  # noqa: PLC0415

    ast.parse(candidate)  # raises SyntaxError before anything is written
    runner.write_text(candidate, encoding="utf-8")
    for mod in removed:
        index["entries"].append({
            "action": "remove_runner_branch", "module": mod,
            "file": str(RUNNER_REL), "category": "reference_safety"})
    return removed


def move_to_archive(repo: Path, rel: str, archive_root: Path, index: dict,
                    category: str, winner: str | None = None) -> None:
    src = repo / rel
    dst = archive_root / rel
    tracked = is_git_tracked(repo, rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    index["entries"].append({
        "action": "archive", "category": category,
        "repo": repo.name, "original_path": rel,
        "archived_to": str(dst.relative_to(repo)),
        "git_tracked": tracked,
        "winner": winner})


def prune_empty_parents(repo: Path, rel: str, archive_root: Path) -> list[str]:
    """Remove now-empty parent dirs of a moved path (never the archive root itself)."""
    removed = []
    parent = (repo / rel).parent
    while True:
        p = repo / parent
        if p == repo or archive_root in p.parents or p == archive_root or not p.exists():
            break
        try:
            p.rmdir()
        except OSError:
            break
        removed.append(str(parent))
        parent = parent.parent
    return removed


def write_index(archive_root: Path, index: dict) -> None:
    # merge with any existing index from a same-day run so history is never lost
    existing = archive_root / "index.json"
    if existing.is_file():
        prev = json.loads(existing.read_text(encoding="utf-8"))
        done = {(e.get("action"), e.get("original_path") or e.get("module"))
                for e in prev.get("entries", [])}
        index["entries"] = [e for e in prev.get("entries", [])] + [
            e for e in index["entries"]
            if (e.get("action"), e.get("original_path") or e.get("module")) not in done]
        index["generated_at"] = prev.get("generated_at", index["generated_at"])
        index["runs"] = prev.get("runs", 0) + 1
    else:
        index["runs"] = 1
    (archive_root / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = ["# Cleanup archive index", "",
             f"Generated at: {index['generated_at']}",
             f"Backlog snapshot: {index['backlog_snapshot']}",
             f"Runs recorded: {index['runs']}", ""]
    for e in index["entries"]:
        if e["action"] == "archive":
            w = f" (winner: {e['winner']})" if e.get("winner") else ""
            lines.append(f"- `{e['original_path']}` -> `{e['archived_to']}` "
                         f"[{e['category']}]{w}")
        elif e["action"] == "restore":
            lines.append(f"- RESTORED `{e['original_path']}` to live tree "
                         f"[{e.get('category', '')}] {e.get('reason', '')}")
        else:
            lines.append(f"- removed runner branch for `{e.get('module')}` "
                         f"in `{e.get('file')}` [{e.get('category', '')}]")
    (archive_root / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_audit(out_dir: Path) -> dict:
    """Run the read-only triage audit into out_dir via its public functions."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import triage_audit  # noqa: PLC0415

    all_units, litter, empty_dirs, codeless = [], [], [], []
    for root in triage_audit.DEFAULT_ROOTS:
        units, lt, ed, cd = triage_audit.discover_units(root)
        triage_audit.manifest_coverage(units, root)
        all_units += units
        litter += [f"{root.name}/{p}" for p in lt]
        empty_dirs += [f"{root.name}/{p}" for p in ed]
        codeless += [f"{root.name}/{p}" for p in cd]
    tagged = [("fd-industry-data" if not u["path"].startswith("fd_open_data_mcp")
               else "fd-open-data-mcp", u) for u in all_units]
    agg = triage_audit.aggregate(tagged)
    backlog = triage_audit.derive_backlog(agg, all_units, litter, empty_dirs, codeless)
    triage_audit.write_outputs(agg, backlog, all_units, out_dir)
    return {"units": all_units, "fresh_backlog": backlog,
            "levels": {l: sum(1 for u in all_units if u["level"] == l)
                       for l in ("L1", "L2", "L3", "L4")}}


def verify_invariants(pre_units: list[dict], post: dict) -> tuple[bool, list[str]]:
    """(ok, violations): no L1-L3 unit lost without an equal-or-better domain survivor,
    and no residual debris in the fresh backlog."""
    post_by_path = {u["path"]: u for u in post["units"]}
    post_domains: dict[str, set[str]] = {}
    for u in post["units"]:
        for d in u.get("domains", []):
            post_domains.setdefault(d, set()).add(u["level"])
    violations = []
    seen = set()
    for u in pre_units:
        if u["path"] in seen:
            continue
        seen.add(u["path"])
        if u["level"] not in ("L1", "L2", "L3"):
            continue
        if u["path"] in post_by_path:
            continue
        # lost unit: a domain must retain an equal-or-better level
        kept = any(any(LEVEL_RANK[l] <= LEVEL_RANK[u["level"]] for l in ls)
                   for d, ls in post_domains.items() if d in u.get("domains", []))
        if not kept:
            violations.append(
                f"lost {u['level']} unit {u['path']} with no equal-or-better domain survivor")
    residue = {k: len(v) for k, v in post["fresh_backlog"]["archive_delete"].items()}
    violations += [f"residual {k}={n}" for k, n in residue.items() if n]
    return not violations, violations


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backlog", default=str(DEFAULT_BACKLOG))
    ap.add_argument("--apply", action="store_true", help="execute (default: dry-run)")
    ap.add_argument("--post-audit-dir", default=str(DEFAULT_POST_AUDIT))
    ap.add_argument("--skip-verify", action="store_true",
                    help="skip the post-cleanup re-audit (not recommended)")
    args = ap.parse_args()

    backlog = json.loads(Path(args.backlog).read_text(encoding="utf-8"))
    if "backlog" in backlog:  # audit emits {"generated_at": ..., "backlog": {...}}
        backlog = backlog["backlog"]
    triage_json = Path(args.backlog).parent / "triage.json"
    load_member_levels(triage_json)
    date_tag = today_tag()

    moves, move_skips = plan_moves(backlog)
    plans, cons_skips = consolidate_groups(backlog)

    # loser moves fold into the main move list
    for plan in plans:
        for loser in plan["losers"]:
            moves.append({"src_repo": loser["repo"], "src_rel": loser["rel"],
                          "category": "consolidation_loser", "tracked": None,
                          "winner": plan["winner"]["path"]})

    # reference safety + knowledge guard: adapters move only when provably safe;
    # consolidation losers never move if their triage level is better than the
    # winner's (that would lose verified knowledge; spec invariant guards this).
    runner_modules = set()
    deferred = list(move_skips) + cons_skips
    executable = []
    winner_by_path = {p["winner"]["path"]: p["winner"] for p in plans if p["winner"]}
    for mv in moves:
        if mv["src_rel"].startswith(str(ADAPTERS_REL)):
            module = Path(mv["src_rel"]).stem.replace("-", "_")
            level = member_level({"path": mv["src_rel"]})
            refs = find_references(module)
            runner_refs = [(p, i) for p, i in refs if p == MCP_ROOT / RUNNER_REL]
            if refs and not runner_refs:
                deferred.append({"path": mv["src_repo"].name + "/" + mv["src_rel"],
                                 "reason": f"referenced at {refs[0][0].name}:{refs[0][1]}"})
                continue
            if runner_refs and level in ("L1", "L2", "L3"):
                deferred.append({"path": mv["src_repo"].name + "/" + mv["src_rel"],
                                 "reason": f"{level} adapter referenced by fetch runner"})
                continue
            if runner_refs:
                runner_modules.add(module)
        if mv["category"] == "consolidation_loser" and mv.get("winner"):
            loser_level = LEVEL_RANK.get(member_level({"path": mv["src_rel"]}), 4)
            winner_mv = winner_by_path.get(mv["winner"])
            if winner_mv:
                winner_level = LEVEL_RANK.get(
                    member_level({"path": mv["winner"]}), 4)
                if loser_level < winner_level:
                    deferred.append({"path": mv["src_repo"].name + "/" + mv["src_rel"],
                                     "reason": f"level L{loser_level} better than winner's; kept in place"})
                    continue
        executable.append(mv)

    branches = runner_branches_for(sorted(runner_modules))
    if not args.apply:
        print(f"DRY-RUN plan: {len(executable)} moves, {len(plans)} consolidation groups, "
              f"{len(branches)} runner branch removals, {len(deferred)} skipped/deferred")
        by_cat = {}
        for mv in executable:
            by_cat[mv["category"]] = by_cat.get(mv["category"], 0) + 1
        print("moves by category:", json.dumps(by_cat, sort_keys=True))
        for d in deferred[:10]:
            print("  deferred:", d["path"], "-", d["reason"])
        for plan in plans:
            if plan["winner"] is None:
                print(f"  group {plan['slug']}: fully condemned by category moves")
            else:
                w = plan["winner"]["path"]
                ls = ", ".join(l["path"] for l in plan["losers"])
                print(f"  group {plan['slug']}: keep {w} | archive: {ls or '(only member)'}")
        return 0

    # ---- apply ----
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    indexes = {}
    for repo in (REPO_ROOT, MCP_ROOT):
        archive_root = repo / "archive" / f"triage-cleanup-{date_tag}"
        archive_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.backlog, archive_root / "cleanup_backlog.snapshot.json")
        indexes[repo] = {"generated_at": stamp, "repo": repo.name, "entries": [],
                         "backlog_snapshot": "cleanup_backlog.snapshot.json"}

    moved = 0
    for mv in executable:
        archive_root = mv["src_repo"] / "archive" / f"triage-cleanup-{date_tag}"
        rel = mv["src_rel"]
        if rel.startswith("archive/"):
            continue
        if (mv["src_repo"] / rel).exists():
            move_to_archive(mv["src_repo"], rel, archive_root, indexes[mv["src_repo"]],
                            mv["category"], mv.get("winner"))
            prune_empty_parents(mv["src_repo"], rel, archive_root)
            moved += 1

    if runner_modules:
        removed = remove_runner_branches(MCP_ROOT, sorted(runner_modules),
                                          indexes[MCP_ROOT])
        print(f"removed runner branches for: {removed}")

    for repo in (REPO_ROOT, MCP_ROOT):
        write_index(repo / "archive" / f"triage-cleanup-{date_tag}", indexes[repo])

    print(f"applied this run: {moved} moves "
          f"(index totals: {sum(1 for e in indexes[REPO_ROOT]['entries'] if e['action'] == 'archive')} in fd-industry-data, "
          f"{sum(1 for e in indexes[MCP_ROOT]['entries'] if e['action'] == 'archive')} in fd-open-data-mcp), "
          f"{len(deferred)} deferred")

    if args.skip_verify:
        return 0

    # capture pre-state from the executed backlog's sibling triage.json
    pre_units = []
    if triage_json.is_file():
        data = json.loads(triage_json.read_text(encoding="utf-8"))
        for rec in data.get("domains", {}).values():
            pre_units += rec["units"]
        pre_units += data.get("unattributed_units", [])
    post = run_audit(Path(args.post_audit_dir))
    ok, violations = verify_invariants(pre_units, post)
    print("post-cleanup levels:", json.dumps(post["levels"]))
    print("fresh backlog residue:", json.dumps(
        {k: len(v) for k, v in post["fresh_backlog"]["archive_delete"].items()}))
    if not ok:
        for v in violations:
            print("VIOLATION:", v)
        return 1
    print("invariants OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
