#!/usr/bin/env python3
"""Shared helpers for the agricultural-vertical extractors.

The write discipline (obs_month bucket, additive insert, concept creation,
trend gate) is reused verbatim from ``scripts/restaurant_extractors/_common.py``
— this module only adds the agricultural-specific piece: a **local live
backfill** loader.

Why a local loader is needed here (and was not in restaurant wave-0):

* restaurant wave-0 read stored payloads from the central crawl DB
  (``unit_items``), because those units were already deployed;
* the agriculture W1 units (``agri-casde`` / ``moa-fishery`` / ``moa-hog-price``
  extension / ``cncotton``) are **not deployed yet** — they were verified by
  local real runs (see ``docs/agriculture-series-curation.md`` §0). There is no
  ``crawl_items`` payload to read.

So ``live_items`` imports the unit's ``spider.py`` and calls its entry function
directly. Every series it writes still carries the real ``source_used`` (the
unit name), and each call is reported in ``notes`` as a LOCAL LIVE BACKFILL so
nobody mistakes this for a platform crawl. Once the units are deployed, swapping
``live_items`` for ``unit_items`` is a one-line change per extractor.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
_REPO = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:  # reach restaurant_extractors
    sys.path.insert(0, str(_SCRIPTS))

from restaurant_extractors._common import (  # noqa: E402,F401 — re-export
    COUNTRY_CN,
    COUNTRY_US,
    ENTITY_WORLD,
    dedup_latest,
    ensure_unit_concepts,
    fmt,
    gate_periods,
    insert_rows,
    num,
    obs_month_of,
    unit_items,
)

ENTITY_COUNTRY_CN = COUNTRY_CN  # 1


def _load_unit(slug: str):
    path = _REPO / "spiders" / slug / "spider.py"
    if not path.exists():
        raise FileNotFoundError(f"spider not found: {path}")
    mod_name = "agri_unit_" + slug.replace("-", "_")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def live_items(slug: str, func: str, notes: list[str], **kwargs) -> list[dict]:
    """Run one unit entry function in-process (local live backfill).

    Args:
        slug: unit directory name (== manifest name == ``source_used``).
        func: entry function name, e.g. ``run_agri_casde``.
        notes: caller's note sink; a LOCAL LIVE BACKFILL line is appended.
        **kwargs: passed straight to the entry function.

    Returns:
        The unit's real return value (list of row dicts). No filtering here —
        each extractor owns its own caliber filters.
    """
    mod = _load_unit(slug)
    fn = getattr(mod, func, None)
    if fn is None:
        raise AttributeError(f"{slug}: entry {func} missing in spider.py")
    rows = fn(**kwargs)
    notes.append(
        f"{slug}: LOCAL LIVE BACKFILL via {func}({', '.join(f'{k}={v!r}' for k, v in kwargs.items())})"
        f" -> {len(rows)} raw row(s); unit not yet deployed, no crawl_items payload read"
    )
    return rows


__all__ = [
    "COUNTRY_CN", "COUNTRY_US", "ENTITY_WORLD", "ENTITY_COUNTRY_CN",
    "dedup_latest", "ensure_unit_concepts", "fmt", "gate_periods",
    "insert_rows", "num", "obs_month_of", "unit_items", "live_items",
    "REPO", "SCRIPTS",
]

REPO = _REPO
SCRIPTS = _SCRIPTS
