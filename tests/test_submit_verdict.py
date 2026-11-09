"""SUBMIT verdict parsing tests (harden-crawl-delivery 2.11).

Before this, a receipt that matched none of the known phrases was counted as a
success and its raw text stored as the conclusion. The 2026-11-09 production
`last-run.json` contains an agent's thinking-aloud as a ticket's `response`:

    "The quoting broke. Let me write the python to a file in /tmp ..."

That is not a conclusion. It must read as ``unknown``, allow a bounded retry,
and keep the raw text as evidence only.
"""
from __future__ import annotations

import json

from fd_industry_data.health import inspector


def _v(status, raw="", message=None):
    return inspector.parse_submit_verdict(status, raw, message if message is not None else raw)


def test_explicit_acceptance():
    # 「已入队」属于去重语义（见下一条），明确的受理以状态词表达
    assert _v(200, "queued")["verdict"] == "accepted"
    assert _v(200, "working: 修复中")["state"] == "working"
    assert _v(200, "pr-open")["verdict"] == "accepted"


def test_dedup_receipt_is_accepted_not_new_work():
    v = _v(200, "已见过该工单，不重复执行")
    assert v["verdict"] == "duplicate"


def test_refusal_is_refused():
    assert _v(200, "拒绝入队：工单不可读")["verdict"] == "refused"
    assert _v(200, "不认领该路径")["verdict"] == "refused"
    assert _v(200, "invalid request")["verdict"] == "refused"


def test_http_error_and_jsonrpc_error():
    assert _v(500, "")["verdict"] == "error"
    assert _v(200, json.dumps({"error": {"code": -32032, "message": "unavailable"}}))["verdict"] == "error"


def test_bare_jsonrpc_error_object_is_error():
    """`{"code": -32003, "message": "Connection error."}` 没有 error 外层键。

    2026-11-09 回放 7 条历史回执，其中 2 条是这个形状，此前被判成功。
    """
    v = _v(200, '{"code": -32003, "message": "Connection error."}')
    assert v["verdict"] == "error"
    assert "-32003" in v["reason"]


def test_jsonrpc_result_envelope_is_accepted():
    assert _v(200, json.dumps({"result": {"state": "queued"}}))["verdict"] == "accepted"


def test_agent_chatter_is_unknown_not_success():
    chatter = ("The quoting broke. Let me write the python to a file in /tmp using the write tool, "
               "then run it. That avoids quoting hell.")
    v = _v(200, chatter)
    assert v["verdict"] == "unknown"
    assert v["response_excerpt"].startswith("The quoting broke")
    assert not inspector._looks_failed(200, chatter, chatter)  # unknown ≠ refused/error


def test_unknown_receipt_is_not_recorded_as_accepted():
    doc = {"lifecycle": [{"event": "submitted", "ok": True, "response": "The quoting broke. Let me write"}]}
    assert inspector._submit_recorded(doc) is False  # 允许重投


def test_unknown_receipt_stops_after_retry_cap():
    doc = {"lifecycle": [{"event": "submitted", "ok": True, "response": "hmm"} for _ in range(3)]}
    assert inspector._submit_recorded(doc) is True  # 达上限停手


def test_accepted_receipt_is_recorded():
    doc = {"lifecycle": [{"event": "submitted", "ok": True, "response": "已入队"}]}
    assert inspector._submit_recorded(doc) is True


def test_quote_inside_conclusion_does_not_flip_verdict():
    """结论首段判词，引述历史错误不误判。"""
    msg = "已入队。说明：某次 submit 曾回 invalid request，本次已修复。"
    assert _v(200, msg)["verdict"] in ("accepted", "duplicate")
