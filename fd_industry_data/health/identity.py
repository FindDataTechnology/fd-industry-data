"""Ticket identity: (source, category, evidence signature).

Change harden-crawl-delivery 2.7. Before this, a ticket's identity was
``(source, unit, created_at)`` — so the same failure produced a brand-new
ticket every run. Measured on the 2026-11-09 estate: fred-cpi ×3, steam-ccu ×2
(one of them 15 days old), nasa-firms ×2, gdacs ×2, nra-rpi ×2, all describing
the same root cause, all open at once.

The signature deliberately excludes volatile parts of an error string —
timestamps, durations, row counts, ids — because those change every run and
would make every signature unique (the current ``_fingerprint`` folds digits to
'#' but keeps vendored file paths and memory addresses, which still drift).

Category is part of the identity on purpose: a source can have a network
problem and, once that clears, a structure problem. Those are two different
repairs and MUST NOT be merged into one ticket.
"""
from __future__ import annotations

import hashlib
import re

# Volatile fragments stripped before hashing.
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_HEX_RE = re.compile(r"\b0x[0-9a-f]+\b", re.I)
_LONG_HEX_RE = re.compile(r"\b[0-9a-f]{16,}\b", re.I)
_ISO_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?\b")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_PATH_RE = re.compile(r"(?:[A-Za-z]:)?(?:/[\w.\-]+){2,}/")
_WS_RE = re.compile(r"\s+")

# Fragments that carry the identity of the failure surface.
_ENDPOINT_RE = re.compile(r"https?://[^\s'\"<>]+")
_HTTP_RE = re.compile(r"\bHTTP(?: Error)?[ :]*([45]\d\d)\b", re.I)
_EXC_RE = re.compile(r"\b([A-Z][A-Za-z]*(?:Error|Exception))\b")


def signature(error_text: str) -> str:
    """Stable hash of an error's identity, ignoring volatile decoration.

    Two failures that differ only in timestamps, durations, row counts or ids
    collapse to the same signature; a different exception type, endpoint or
    HTTP status produces a different one.
    """
    text = str(error_text or "")
    http = _HTTP_RE.search(text)
    endpoint = ""
    if (m := _ENDPOINT_RE.search(text)):
        endpoint = m.group(0).split("?")[0].rstrip("/")
    exc = ""
    if (m := _EXC_RE.search(text)):
        exc = m.group(1)
    # The normalized remainder keeps the shape of the message (what failed)
    # while dropping values that vary per run.
    norm = _UUID_RE.sub("<id>", text)
    norm = _LONG_HEX_RE.sub("<hex>", norm)
    norm = _HEX_RE.sub("<hex>", norm)
    norm = _ISO_RE.sub("<ts>", norm)
    norm = _PATH_RE.sub("<path>/", norm)
    norm = _NUM_RE.sub("#", norm)
    norm = _WS_RE.sub(" ", norm).strip().lower()[:400]
    basis = f"{exc}|{http.group(1) if http else ''}|{endpoint}|{norm}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


def ticket_identity(source: str, category: str | None, error_text: str) -> tuple[str, str]:
    """(dedupe_key, evidence_signature) for one ticket.

    ``dedupe_key`` is what the inspector compares against open tickets;
    ``evidence_signature`` is stored on the ticket so a human can see why two
    failures were judged the same (or different).
    """
    sig = signature(error_text)
    return f"{source}|{category or 'unknown'}|{sig}", sig


def is_same_failure(ticket: dict, source: str, category: str | None, sig: str) -> bool:
    """Whether an existing ticket already covers this failure.

    A ticket written before this change has no signature, so it cannot tell one
    failure shape from another; for those we keep the old behaviour (one open
    ticket per source). Without this fallback the first inspection after deploy
    would open a second ticket for every source that already has one.
    """
    if str(ticket.get("source") or "") != source:
        return False
    stored = ticket.get("evidence_signature")
    if stored is None:
        return True
    if (ticket.get("category") or None) != (category or None):
        return False
    return stored == sig
