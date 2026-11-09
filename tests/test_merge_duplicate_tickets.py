"""Merge tool tests (harden-crawl-delivery 2.8).

The core guarantee: a group is only merged when every ticket in it carries
error evidence. Two tickets with empty evidence hash to the same "empty"
signature, and merging them would turn "we do not know what failed" into
"these were the same failure".
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from fd_industry_data.health import identity, ticket


def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "merge_duplicate_tickets.py"
    spec = importlib.util.spec_from_file_location("merge_dup", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


merge = _load_script()


def _doc(source, category, errors, created):
    doc = ticket.new_ticket(
        source=source, unit=f"spiders/{source}/",
        evidence={"failing_runs": [{"error_summary": e} for e in errors]},
        verify_commands=["echo"], category=category, created_at=created)
    return doc


def test_same_signature_merges():
    a = _doc("gdacs", "structure",
             ["json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)"],
             "2026-11-06T04:46:53+00:00")
    b = _doc("gdacs", "structure",
             ["json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)"],
             "2026-11-07T04:46:53+00:00")
    groups, skipped = merge.plan_merges([(Path("a.yaml"), a), (Path("b.yaml"), b)])
    assert len(groups) == 1
    assert groups[0][0][0].name == "a.yaml"  # oldest kept
    assert not skipped


def test_different_signature_does_not_merge():
    a = _doc("fred-cpi", "structure", ["ValueError: 第 875 行数值 '' 不可解析"],
             "2026-11-07T00:00:00+00:00")
    b = _doc("fred-cpi", "network", ["TimeoutError: The read operation timed out"],
             "2026-11-07T01:00:00+00:00")
    groups, _ = merge.plan_merges([(Path("a.yaml"), a), (Path("b.yaml"), b)])
    assert groups == []


def test_empty_evidence_never_merges():
    a = _doc("fred-cpi", None, [], "2026-11-07T00:00:00+00:00")
    b = _doc("fred-cpi", None, [], "2026-11-07T01:00:00+00:00")
    groups, skipped = merge.plan_merges([(Path("a.yaml"), a), (Path("b.yaml"), b)])
    assert groups == []
    assert len(skipped) == 2
    assert all("no error text" in s["reason"] for s in skipped)


def test_mixed_group_does_not_merge():
    """无证据单在分组前就被排除，所以它既进不了组、也不会拖累有证据的那张。"""
    a = _doc("gdacs", "structure", ["HTTP Error 500"], "2026-11-06T00:00:00+00:00")
    b = _doc("gdacs", "structure", [], "2026-11-07T00:00:00+00:00")
    groups, skipped = merge.plan_merges([(Path("a.yaml"), a), (Path("b.yaml"), b)])
    assert groups == []  # 组里只剩 1 张，够不上合并
    assert any("no error text" in s["reason"] for s in skipped)


def test_apply_marks_superseded_and_keeps_open_ticket(tmp_path):
    a = _doc("gdacs", "structure", ["HTTP Error 500"], "2026-11-06T00:00:00+00:00")
    b = _doc("gdacs", "structure", ["HTTP Error 500"], "2026-11-07T00:00:00+00:00")
    pa, pb = tmp_path / "a.yaml", tmp_path / "b.yaml"
    for p, d in ((pa, a), (pb, b)):
        d["ticket_id"] = p.stem
        p.write_text(ticket.dump_ticket(d), encoding="utf-8")

    groups, _ = merge.plan_merges([(pa, ticket.load_ticket(pa)), (pb, ticket.load_ticket(pb))])
    from datetime import datetime, timezone
    merge.apply_merges(groups, datetime(2026, 11, 9, tzinfo=timezone.utc))

    kept = ticket.load_ticket(pa)
    merged = ticket.load_ticket(pb)
    assert kept["terminal"] is None
    assert merged["terminal"] == "superseded"
    assert merged["superseded_by"] == "a"
    assert merged["lifecycle"][-1]["event"] == "merged"


def test_signature_is_stable_across_volatile_fields():
    a = "JSONDecodeError at 2026-11-06T04:46:53Z took 12.4s rows=0"
    b = "JSONDecodeError at 2026-11-07T09:01:02Z took 30.1s rows=7"
    assert identity.signature(a) == identity.signature(b)
