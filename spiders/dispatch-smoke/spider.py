"""Dispatch smoke probe: proves a site's claim -> execute -> report chain
without touching the network. Trigger-only (no schedule) — used to verify
new sites and as a permanent site liveness check."""
from __future__ import annotations

import socket
import time


def run_dispatch_smoke(limit: int = 100) -> list[dict]:
    return [{"host": socket.gethostname(), "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
             "source": "dispatch-smoke"}][:max(1, limit)]
