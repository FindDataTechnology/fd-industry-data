#!/usr/bin/env python3
"""Archive dead-classified legacy manifests (report-driven, archive-not-delete).

Reads targets exclusively from the legacy triage report (never re-derives),
moves the `dead`-classified manifests into archive/legacy-manifests-<date>/
preserving filenames, and writes INDEX.md/index.json carrying each entry's
classification evidence (verdict, source_url, domains, matched units) so an
operator can recover a manifest and know why it was condemned. `alive` and
`unknown` manifests are never touched; the script verifies they stay
byte-identical (sha256 before/after) and exits non-zero if not. Idempotent:
names already archived are skipped-and-reported, so a re-run is a no-op.
Dry-run by default; --apply executes. Spec: openspec/changes/estate-hygiene/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent          # fd-industry-data
DEFAULT_REPORT = REPO_ROOT / "output" / "manifest-drafts" / "legacy_triage.json"
MANIFESTS_DIR = REPO_ROOT / "manifests"


def today_tag() -> str:
    return datetime.now().strftime("%Y%m%d")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_git_tracked(rel: str) -> bool:
    r = subprocess.run(["git", "ls-files", "--error-unmatch", rel],
                       cwd=REPO_ROOT, capture_output=True)
    return r.returncode == 0


def load_dead(report_path: Path) -> list[dict]:
    data = json.loads(report_path.read_text(encoding="utf-8"))
    return [m for m in data["manifests"] if m.get("verdict") == "dead"]


def hash_live_manifests() -> dict[str, str]:
    if not MANIFESTS_DIR.is_dir():
        return {}
    return {p.name: sha256(p) for p in sorted(MANIFESTS_DIR.glob("*.yaml"))}


def load_index(archive_root: Path) -> dict:
    existing = archive_root / "index.json"
    if existing.is_file():
        return json.loads(existing.read_text(encoding="utf-8"))
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "repo": REPO_ROOT.name, "entries": [], "report": "", "runs": 0}


def write_index(archive_root: Path, index: dict) -> None:
    (archive_root / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = ["# Legacy dead-manifest archive index", "",
             f"Generated at: {index['generated_at']}",
             f"Triage report: {index['report']}",
             f"Runs recorded: {index['runs']}", ""]
    for e in index["entries"]:
        matched = ", ".join(e["matched_units"]) or "(no units matched)"
        lines.append(
            f"- `{e['original_path']}` -> `{e['archived_to']}` [{e['category']}] "
            f"verdict: {e['verdict']} (source: {e['source_url']}, matched: {matched})")
    (archive_root / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    ap.add_argument("--apply", action="store_true", help="execute (default: dry-run)")
    args = ap.parse_args()

    report_path = Path(args.report)
    dead = load_dead(report_path)
    archive_root = REPO_ROOT / "archive" / f"legacy-manifests-{today_tag()}"
    index = load_index(archive_root)
    archived_names = {Path(e["original_path"]).name for e in index["entries"]
                      if e.get("action") == "archive"}

    before = hash_live_manifests()
    moves, skipped = [], []
    for m in dead:
        name = m["manifest"]
        src = MANIFESTS_DIR / name
        if not src.is_file():
            if name in archived_names:
                skipped.append({"manifest": name, "reason": "already archived (no-op)"})
            else:
                skipped.append({"manifest": name, "reason": "not in manifests/ and not "
                                                            "recorded in archive index"})
            continue
        if name in archived_names:
            skipped.append({"manifest": name, "reason": "recorded in index but still "
                                                        "present in manifests/"})
            continue
        moves.append({"entry": m, "src": src})

    print(f"report: {report_path.relative_to(REPO_ROOT)} -> {len(dead)} dead manifests")
    print(f"{'APPLY' if args.apply else 'DRY-RUN'}: {len(moves)} to move, "
          f"{len(skipped)} skipped")
    for mv in moves:
        print(f"  move: {mv['src'].relative_to(REPO_ROOT)} -> "
              f"{archive_root.relative_to(REPO_ROOT) / mv['src'].name}")
    for s in skipped:
        print(f"  skip: {s['manifest']} ({s['reason']})")
    if not args.apply:
        return 0

    archive_root.mkdir(parents=True, exist_ok=True)
    index["report"] = str(report_path.relative_to(REPO_ROOT))
    for mv in moves:
        m, src = mv["entry"], mv["src"]
        rel = src.relative_to(REPO_ROOT).as_posix()
        dst = archive_root / src.name
        tracked = is_git_tracked(rel)
        shutil.move(str(src), str(dst))
        index["entries"].append({
            "action": "archive", "category": "legacy_manifest_dead",
            "repo": REPO_ROOT.name, "original_path": rel,
            "archived_to": dst.relative_to(REPO_ROOT).as_posix(),
            "git_tracked": tracked,
            "verdict": m.get("verdict"), "source_url": m.get("source_url"),
            "domains": m.get("domains", []), "matched_units": m.get("matched_units", [])})
        print(f"  archived: {rel} -> {dst.relative_to(REPO_ROOT)}")
    index["runs"] += 1
    if moves or not (archive_root / "index.json").exists():
        write_index(archive_root, index)

    after = hash_live_manifests()
    untouched = {name: h for name, h in after.items() if name not in
                 {mv["src"].name for mv in moves}}
    drifted = [name for name, h in untouched.items() if before.get(name) != h]
    lost = [name for name in before if name not in after
            and name not in {mv["src"].name for mv in moves}]
    if drifted or lost:
        print(f"VERIFY FAIL: drifted={drifted} lost={lost}", file=sys.stderr)
        return 1
    print(f"verified: {len(untouched)} remaining manifests byte-identical "
          f"(sha256), {len(moves)} archived")
    return 0


if __name__ == "__main__":
    sys.exit(main())
