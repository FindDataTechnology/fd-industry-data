"""Ticket identity tests (harden-crawl-delivery 2.7)."""
from __future__ import annotations

from fd_industry_data.health.identity import (
    is_same_failure,
    signature,
    ticket_identity,
)


def test_volatile_values_collapse_to_one_signature():
    a = ("RuntimeError: steam-ccu: 全部种子 app 取数失败 — 730: ValueError: "
         "response is not JSON (JSONDecodeError) at 2026-11-03T23:57:01Z")
    b = ("RuntimeError: steam-ccu: 全部种子 app 取数失败 — 730: ValueError: "
         "response is not JSON (JSONDecodeError) at 2026-11-07T23:56:56Z")
    assert signature(a) == signature(b)


def test_row_counts_and_durations_do_not_split_identity():
    a = "RuntimeError: gdacs: empty feature set for 2026-10-10..2026-11-09 (took 60.4s, rows=0)"
    b = "RuntimeError: gdacs: empty feature set for 2026-10-10..2026-11-09 (took 61.2s, rows=0)"
    assert signature(a) == signature(b)


def test_different_exception_type_is_a_different_failure():
    a = "ValueError: response is not JSON"
    b = "urllib.error.URLError: <urlopen error timed out>"
    assert signature(a) != signature(b)


def test_different_http_status_is_a_different_failure():
    assert signature("HTTP Error 412: blocked") != signature("HTTP Error 503: unavailable")


def test_different_endpoint_is_a_different_failure():
    a = "RuntimeError: 请求失败 https://api.a.example/data: HTTPError 500"
    b = "RuntimeError: 请求失败 https://api.b.example/data: HTTPError 500"
    assert signature(a) != signature(b)


def test_category_participates_in_identity():
    err = "HTTP Error 500"
    k1, s1 = ticket_identity("src", "network", err)
    k2, _ = ticket_identity("src", "structure", err)
    assert k1 != k2
    assert s1 == signature(err)


def test_legacy_ticket_without_signature_still_matches():
    # 老单没有签名，分不清故障形状 → 退回按源去重（上线首日不爆发新单）
    legacy = {"source": "fred-cpi", "category": "structure"}
    assert is_same_failure(legacy, "fred-cpi", "structure", "deadbeef")
    assert is_same_failure(legacy, "fred-cpi", "network", "deadbeef")
    assert not is_same_failure(legacy, "other", "structure", "deadbeef")


def test_ticket_with_signature_requires_signature_match():
    doc = {"source": "s", "category": "contract", "evidence_signature": "abc"}
    assert is_same_failure(doc, "s", "contract", "abc")
    assert not is_same_failure(doc, "s", "contract", "def")


def test_empty_error_is_stable():
    assert signature("") == signature(None)
