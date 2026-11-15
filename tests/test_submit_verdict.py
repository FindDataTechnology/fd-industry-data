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


def test_unknown_cap_counts_real_events_not_just_ok_true():
    """**现网形态**：unknown 事件是 `ok: false`（ok 只在 accepted/duplicate 时为真）。

    旧循环先 `if ... or e["ok"] is False: continue`，于是 unknown 事件永远走不到
    上限检查 ⇒ docstring 承诺的「unknown 重试有上限」不存在，实际是**无限重投**：
    2026-11-15 实测三张吃过 308 的单（jp-tdnet / cba-wealth-index / fred-cpi）
    全部 `_submit_recorded=False`，一张也不会停手。上限必须按 unknown 条数数。
    """
    unknown = {"event": "submitted", "ok": False, "verdict": "unknown",
               "response": "<html><title>308 Permanent Redirect</title><center>tengine</center>"}
    assert inspector._submit_recorded({"lifecycle": [unknown] * 2}) is False  # 未达上限，仍重投
    assert inspector._submit_recorded({"lifecycle": [unknown] * 3}) is True   # 达上限停手
    # 旧事件没有 verdict 字段：回退重解析存档原文，同样计数
    legacy = {"event": "submitted", "ok": False, "response": "hmm"}
    assert inspector._submit_recorded({"lifecycle": [legacy] * 3}) is True


def test_accepted_receipt_is_recorded():
    doc = {"lifecycle": [{"event": "submitted", "ok": True, "response": "已入队"}]}
    assert inspector._submit_recorded(doc) is True


def test_quote_inside_conclusion_does_not_flip_verdict():
    """结论首段判词，引述历史错误不误判。"""
    msg = "已入队。说明：某次 submit 曾回 invalid request，本次已修复。"
    assert _v(200, msg)["verdict"] in ("accepted", "duplicate")


def test_http_error_carries_the_envelope_code():
    """2026-11-14 实历：门面 402 有三种成因，此前报告里全是 `HTTP 402`。

    `PLAN_EXHAUSTED` 是套餐分钟用完——它要求人去改套餐，而
    `INSUFFICIENT_BALANCE` 要求人去充值，`CALLER_SUSPENDED` 要求人去解停。
    三者处置完全不同，正文里本来就有 code，不该只在报告里留一个状态码。
    """
    body = json.dumps({"error": {"code": "PLAN_EXHAUSTED",
                                 "message": "the caller's serving plan has no minutes left"}})
    v = _v(402, body)
    assert v["verdict"] == "error"
    assert "HTTP 402" in v["reason"]
    assert "PLAN_EXHAUSTED" in v["reason"]


def test_http_error_without_a_json_body_stays_status_only():
    """非 JSON 正文（网关的 HTML 错误页）不许编造错误码。"""
    v = _v(503, "<html>502 Bad Gateway</html>")
    assert v["reason"] == "HTTP 503"


def test_http_error_detail_is_bounded():
    """正文可能是整页错误文档——理由要留得住，不能把报告撑爆。"""
    body = json.dumps({"error": {"code": "X", "message": "y" * 5000}})
    assert len(_v(500, body)["reason"]) < 200
