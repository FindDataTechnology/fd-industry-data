"""Test login unit (automation=auto): fabricates a session token so the
pool chain can be exercised end-to-end without an external site."""
from __future__ import annotations

import time

automation = "auto"


def login(account_alias: str) -> dict:
    return {
        "version": 1,
        "source": "auth-smoke",
        "account_id": account_alias,
        "mode": "session_jar",
        "auth": {"kind": "test-token",
                 "userToken": f"tok-{account_alias}-{int(time.time())}"},
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
