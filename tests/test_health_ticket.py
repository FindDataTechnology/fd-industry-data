"""health.ticket：工单 schema、校验规则与生命周期事件测试。"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.health import ticket


def _base(**overrides):
    doc = ticket.new_ticket(
        "fred-data",
        "spiders/fred-data/",
        evidence={
            "window": {"from": "2026-10-01T00:00:00+00:00", "to": "2026-10-03T00:00:00+00:00"},
            "failing_runs": [{"run_id": "r2", "error": "timeout"}],
            "last_success": {"run_id": "r1"},
            "notes": "连续三次超时",
        },
        verify_commands=["python3 -m pytest tests/test_fred.py -q"],
        category="network",
        needs_human=False,
        suggested_action="出口切换/代理复测（不改代码）",
        golden_paths=["golden/fred/latest.json"],
        created_at="2026-10-03T08:00:00+00:00",
    )
    doc.update(overrides)
    return doc


def test_ticket_filename_format():
    assert ticket.ticket_filename("20261003", "fred-data", "a1b2c3d4") == (
        "20261003-fred-data-a1b2c3d4.yaml"
    )


def test_new_ticket_shape_and_validation():
    doc = _base()
    assert ticket.validate_ticket(doc) == []
    assert re.fullmatch(r"20261003-fred-data-[0-9a-f]{8}", doc["ticket_id"])
    assert doc["ticket_id"] == ticket.ticket_filename(
        "20261003", "fred-data", doc["ticket_id"].rsplit("-", 1)[1]
    )[: -len(".yaml")]
    assert doc["source"] == "fred-data"
    assert doc["unit"] == "spiders/fred-data/"
    assert doc["category"] == "network"
    assert doc["needs_human"] is False
    assert doc["suspected_anti_bot"] is False
    assert doc["terminal"] is None
    assert doc["verify"] == {"commands": ["python3 -m pytest tests/test_fred.py -q"]}
    assert doc["golden"] == ["golden/fred/latest.json"]
    assert doc["evidence"]["window"] == {
        "from": "2026-10-01T00:00:00+00:00",
        "to": "2026-10-03T00:00:00+00:00",
    }
    assert doc["evidence"]["notes"] == "连续三次超时"
    assert doc["lifecycle"] == [{"at": "2026-10-03T08:00:00+00:00", "event": "created"}]


def test_new_ticket_evidence_defaults():
    doc = ticket.new_ticket("bls", "spiders/bls/", evidence=None, verify_commands=[])
    assert ticket.validate_ticket(doc) == ["needs_human=False 时 category 不能为空（不得空分类/猜测分类）"]
    assert doc["evidence"] == {
        "window": {"from": None, "to": None},
        "failing_runs": [],
        "last_success": None,
        "notes": "",
    }
    assert doc["golden"] == []
    assert doc["terminal"] is None


def test_new_ticket_default_created_at_is_utc():
    doc = ticket.new_ticket(
        "bls",
        "spiders/bls/",
        evidence={},
        verify_commands=[],
        category="structure",
    )
    created = datetime.fromisoformat(doc["created_at"])
    assert created.tzinfo is not None
    assert created.utcoffset().total_seconds() == 0
    assert doc["ticket_id"].startswith(
        created.astimezone(timezone.utc).strftime("%Y%m%d") + "-bls-"
    )
    assert ticket.validate_ticket(doc) == []


@pytest.mark.parametrize(
    "field",
    ["ticket_id", "source", "unit", "created_at", "evidence", "verify", "lifecycle"],
)
def test_missing_required_field_reported(field):
    doc = _base()
    del doc[field]
    errors = ticket.validate_ticket(doc)
    assert errors, f"缺少 {field} 时应报错"
    assert any(field in message for message in errors), errors


def test_empty_doc_reports_all_required_fields():
    errors = ticket.validate_ticket({})
    for field in ("ticket_id", "source", "unit", "created_at", "evidence", "verify", "lifecycle"):
        assert any(field in message for message in errors), (field, errors)
    assert any("category" in message for message in errors)


def test_invalid_category_rejected():
    errors = ticket.validate_ticket(_base(category="weird"))
    assert any("category" in message and "非法" in message for message in errors)


@pytest.mark.parametrize("category", ticket.CATEGORIES)
def test_all_categories_accepted(category):
    assert ticket.validate_ticket(_base(category=category)) == []


def test_needs_human_allows_missing_category():
    doc = _base(category=None, needs_human=True)
    assert ticket.validate_ticket(doc) == []
    # 有分类 + 转人工同样合法
    doc = _base(category="network", needs_human=True)
    assert ticket.validate_ticket(doc) == []


def test_no_empty_category_when_not_human():
    errors = ticket.validate_ticket(_base(category=None, needs_human=False))
    assert any("category" in message for message in errors), errors


def test_suspected_anti_bot_only_for_contract():
    assert ticket.validate_ticket(
        _base(category="contract", suspected_anti_bot=True)
    ) == []
    errors = ticket.validate_ticket(_base(category="network", suspected_anti_bot=True))
    assert any("suspected_anti_bot" in message for message in errors), errors
    # 证据不足转人工（category=None）也不允许 anti_bot 标记
    errors = ticket.validate_ticket(
        _base(category=None, needs_human=True, suspected_anti_bot=True)
    )
    assert any("suspected_anti_bot" in message for message in errors), errors


def test_type_checks():
    errors = ticket.validate_ticket(_base(needs_human="yes"))
    assert any("needs_human" in message for message in errors)
    errors = ticket.validate_ticket(_base(suspected_anti_bot="yes"))
    assert any("suspected_anti_bot" in message for message in errors)
    errors = ticket.validate_ticket(_base(evidence="not-a-dict", verify="nope", lifecycle="nope"))
    assert any("evidence" in message for message in errors)
    assert any("verify" in message for message in errors)
    assert any("lifecycle" in message for message in errors)
    errors = ticket.validate_ticket(_base(verify={"commands": "pytest"}))
    assert any("verify.commands" in message for message in errors)


def test_validate_non_dict():
    assert ticket.validate_ticket("nope") == ["工单必须是 dict"]


def test_terminal_validation_and_event():
    doc = _base()
    before = [dict(event) for event in doc["lifecycle"]]
    ticket.set_terminal(doc, "manual", note="人工接管")
    assert doc["terminal"] == "manual"
    assert ticket.validate_ticket(doc) == []
    last = doc["lifecycle"][-1]
    assert last["event"] == "terminal"
    assert last["terminal"] == "manual"
    assert last["previous"] is None
    assert last["note"] == "人工接管"
    assert "at" in last
    assert [dict(event) for event in doc["lifecycle"][: len(before)]] == before


def test_set_terminal_rejects_unknown():
    doc = _base()
    with pytest.raises(ValueError) as excinfo:
        ticket.set_terminal(doc, "done")
    assert "terminal" in str(excinfo.value)
    assert doc["terminal"] is None
    assert len(doc["lifecycle"]) == 1


@pytest.mark.parametrize("terminal", ticket.TERMINALS)
def test_all_terminals_accepted(terminal):
    doc = _base()
    ticket.set_terminal(doc, terminal)
    assert doc["terminal"] == terminal
    assert ticket.validate_ticket(doc) == []


def test_terminal_validator_rejects_bad_value():
    errors = ticket.validate_ticket(_base(terminal="done"))
    assert any("terminal" in message for message in errors), errors


def test_append_event_appends_without_touching_history():
    doc = _base()
    ticket.append_event(doc, "triage", category="network", rule_trace="rule5:network")
    ticket.append_event(doc, "verify", command="pytest -q", ok=True)
    events = doc["lifecycle"]
    assert events[0]["event"] == "created"
    assert [event["event"] for event in events] == ["created", "triage", "verify"]
    assert all("at" in event for event in events)
    assert events[1]["category"] == "network"
    assert events[1]["rule_trace"] == "rule5:network"
    assert events[2]["ok"] is True
    assert ticket.validate_ticket(doc) == []


def test_dump_load_roundtrip(tmp_path):
    doc = _base()
    text = ticket.dump_ticket(doc)
    assert "连续三次超时" in text  # allow_unicode：中文原样落盘
    path = tmp_path / ticket.ticket_filename("20261003", "fred-data", "deadbeef")
    path.write_text(text, encoding="utf-8")
    assert ticket.load_ticket(path) == doc
    assert yaml.safe_load(text) == doc