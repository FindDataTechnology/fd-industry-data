#!/usr/bin/env python3
"""Merge open tickets that describe the same failure (harden-crawl-delivery 2.8).

Identity is ``(source, category, evidence signature)``. A group only merges when
**every** ticket in it carries non-empty error evidence: two tickets whose
evidence is empty hash to the same "empty" signature, and merging them would
turn "we don't know what failed" into "these were the same failure". That is the
one thing this script must never do.

The oldest ticket in a group is kept; the rest become ``superseded`` with a
pointer to the kept ticket. Terminal transitions are appended to lifecycle, the
existing evidence is left untouched, and nothing is deleted.

Dry-run by default; ``--apply`` writes.

Usage:
    python3 scripts/merge_duplicate_tickets.py                     # dry run
    python3 scripts/merge_duplicate_tickets.py --apply
    python3 scripts/merge_duplicate_tickets.py --within-days 3650
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.health import identity, inspector, ticket as ticket_mod  # noqa: E402

DEFAULT_DIR = REPO / "reports" / "health-tickets"


def _error_text(doc: dict) -> str:
    ev = doc.get("evidence") or {}
    failing = ev.get("failing_runs") or []
    if failing:
        return str((failing[-1] or {}).get("error_summary") or "")
    return str(ev.get("error_summary") or "")


def _created(doc: dict) -> str:
    return str(doc.get("created_at") or "")


def plan_merges(tickets: list[tuple[Path, dict]]) -> tuple[list[list[tuple[Path, dict]]], list[dict]]:
    """Return (merge groups, skipped notes).

    A group is a list of (path, doc), oldest first. Skipped notes explain why a
    candidate group was not merged — silence there would look like "nothing to
    do" when the truth is "cannot tell".
    """
    groups: dict[tuple[str, str, str], list[tuple[Path, dict]]] = defaultdict(list)
    skipped: list[dict] = []
    for path, doc in tickets:
        err = _error_text(doc)
        if not err.strip():
            skipped.append({
                "ticket": path.name, "source": doc.get("source"),
                "reason": "evidence has no error text; signature would be the empty one",
            })
            continue
        key = (str(doc.get("source") or ""), str(doc.get("category") or ""),
               identity.signature(err))
        groups[key].append((path, doc))

    mergeable: list[list[tuple[Path, dict]]] = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        mergeable.append(sorted(members, key=lambda m: _created(m[1])))
    return mergeable, skipped


def apply_merges(groups: list[list[tuple[Path, dict]]], now: datetime) -> list[dict]:
    results: list[dict] = []
    for members in groups:
        keep_path, keep_doc = members[0]
        keep_id = str(keep_doc.get("ticket_id") or keep_path.stem)
        for path, doc in members[1:]:
            ticket_mod.set_terminal(
                doc, "superseded",
                note=f"重复单：与 {keep_id} 同源、同类别、同证据签名，已并入该单",
            )
            doc["superseded_by"] = keep_id
            ticket_mod.append_event(doc, "merged", into=keep_id, at=now.isoformat())
            path.write_text(ticket_mod.dump_ticket(doc), encoding="utf-8")
            results.append({"ticket": path.name, "into": keep_id,
                            "source": doc.get("source")})
    return results


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(DEFAULT_DIR))
    ap.add_argument("--within-days", type=int, default=3650)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv[1:])

    tickets = inspector.nonterminal_ticket_paths(Path(args.dir), within_days=args.within_days)
    groups, skipped = plan_merges(tickets)

    print(f"merge_duplicate_tickets: {len(tickets)} open tickets, "
          f"{len(groups)} mergeable groups, {len(skipped)} skipped")
    for members in groups:
        keep = members[0][0].name
        print(f"  keep {keep}  <- {[m[0].name for m in members[1:]]}")
    for s in skipped:
        print(f"  skip {s.get('ticket', s.get('source'))}: {s['reason']}")

    if not args.apply:
        print("(dry run; pass --apply to write)")
        return 0

    now = datetime.now().astimezone()
    for item in apply_merges(groups, now):
        print(f"  merged {item['ticket']} -> {item['into']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
