"""CaaS 契约的 Python 侧一致性测试（openspec: add-caas-delivery-contract）。

两条判据：

1. **语义等价**：Python ``caas`` 与 JS ``caas-envelope`` 对同一批输入给出同结论。
   这里用固定用例钉死两侧共有的判定（接线可见于 scripts/check_caas_conformance.py）。
2. **数据同源**：本包内的 ``caas-forms.json`` 是权威文件的逐字节副本——副本漂移
   必须在这里红，而不是等到生产里两个面各说各话。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from fd_industry_data.alerts_core import caas

REPO = Path(__file__).resolve().parents[1]
AUTHORITY = REPO.parent / "openspec" / "changes" / "add-caas-delivery-contract" / "caas-forms.json"
COPY = Path(caas.__file__).with_name("caas-forms.json")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.mark.skipif(not AUTHORITY.exists(), reason="authority file not present in this checkout")
def test_copy_is_byte_identical_to_authority():
    assert _sha(COPY) == _sha(AUTHORITY), (
        "caas-forms.json 副本与权威文件不一致——改契约必须两边同步"
    )


def test_version_and_forms_are_present():
    assert isinstance(caas.version(), int)
    assert set(caas.forms()) == {"text", "chart", "file", "asset", "link"}


def test_resolve_form_prefers_sender_order_and_recipient_veto():
    assert caas.resolve_form(["chart", "asset"], caas.caps_for("a2a-slug")) == "asset"
    assert caas.resolve_form(["chart"], caas.caps_for("room-seat")) == "chart"
    assert caas.resolve_form(["chart"], caas.caps_for("a2a-slug")) is None


def test_ack_aliases_collapse_onto_consumed():
    assert caas.canonical_ack("read") == "consumed"
    assert caas.canonical_ack("acted") == "consumed"
    assert caas.canonical_ack("bogus") is None
    assert caas.ack_rank("consumed") > caas.ack_rank("delivered") > caas.ack_rank("none")


def test_ack_projection_marks_downgrade():
    p = caas.ack_projection(ack_declared="read", ack_actual="delivered")
    assert p.state == "delivered"
    assert p.downgraded is True
    ok = caas.ack_projection(ack_declared="delivered", ack_actual="consumed")
    assert ok.state == "consumed"
    assert ok.downgraded is False


def test_ack_projection_failure_retryability_is_distinguishable():
    assert caas.ack_projection(failed=True, retryable=True).retryable is True
    assert caas.ack_projection(failed=True, retryable=False).retryable is False


def test_validate_envelope_names_every_missing_field():
    r = caas.validate_envelope({})
    assert r.ok is False
    assert set(r.missing) == {"to", "forms", "ack", "correlation.idem"}


def test_validate_envelope_rejects_unregistered_form():
    r = caas.validate_envelope(
        {"to": {"seatId": "s1"}, "forms": ["chart", "hologram"], "ack": "delivered",
         "correlation": {"idem": "k"}}
    )
    assert r.ok is False
    assert "hologram" in r.reason


def test_validate_envelope_accepts_complete():
    r = caas.validate_envelope(
        {"to": {"seatId": "s1"}, "forms": ["chart", "link"], "ack": "read",
         "correlation": {"idem": "fleet:r1"}}
    )
    assert r.ok is True


def test_recipient_caps_and_ceilings_known_for_all_kinds():
    for kind in ("room-seat", "a2a-slug", "platform-identity", "external-endpoint"):
        assert caas.caps_for(kind), f"{kind} must declare caps"
        assert caas.canonical_ack(caas.max_ack_for(kind)), f"{kind} must declare max_ack"
    assert caas.caps_for("nope") == ()
    assert caas.max_ack_for("nope") is None


def test_json_is_parseable_and_documented():
    data = json.loads(COPY.read_text(encoding="utf-8"))
    assert data.get("version") == caas.version()
    assert "note" in data
