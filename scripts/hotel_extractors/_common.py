#!/usr/bin/env python3
"""Hotel-vertical extractor helpers — explicit re-export shim (no new logic).

The write discipline shared by every extractor in this repository lives in
exactly ONE place:

    scripts/restaurant_extractors/_common.py

That module owns the rules: additive ``INSERT ... ON CONFLICT ON CONSTRAINT
uq_sem_obs DO NOTHING`` on the natural key (concept_id, entity_type,
entity_id, date, granularity, source_used), never interpolating / zero-filling
/ fabricating periods, keeping the latest ``scraped_at`` payload per series,
writing the ``obs_month`` governance bucket at the write site, creating
concepts with ``verified=false``, and the >= 24-period trend gate.

This module deliberately does NOT copy that code (the restaurant copy is 174
lines and already audited). It only re-exports the helpers under a hotel-side
import path so hotel extractors can read ``from ._common import ...`` without
cross-package reach-through. Merging the two packages into one neutral module
is booked as a cleanup item in the closing wave of the ``add-hotel-vertical``
change; until then the single source of truth stays in
``scripts/restaurant_extractors/_common.py`` and any discipline change must be
made there, never here.

Import path: the batch runner imports extractor packages with ``scripts/`` on
``sys.path`` (see ``scripts/extract_restaurant_wave0.py``). The ``sys.path``
insert below makes that dependency explicit and also keeps ``python -m
hotel_extractors.<module>`` working when the cwd is not ``scripts/``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parents[1])
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from restaurant_extractors._common import (  # noqa: E402
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

__all__ = [
    "COUNTRY_CN",
    "COUNTRY_US",
    "ENTITY_WORLD",
    "dedup_latest",
    "ensure_unit_concepts",
    "fmt",
    "gate_periods",
    "insert_rows",
    "num",
    "obs_month_of",
    "unit_items",
]
