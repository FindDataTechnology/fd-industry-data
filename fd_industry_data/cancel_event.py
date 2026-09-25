"""Cooperative cancel event shared by the runner and the crawl loop.

The runner polls crawl_runs.cancel_requested in its main thread and sets
this event; the shared crawl loop (and any source that opts in) checks it
between fetches and stops early, keeping whatever items it already has.
"""
from __future__ import annotations

import threading

EVENT = threading.Event()


def is_set() -> bool:
    return EVENT.is_set()
