"""Auth pool smoke probe: proves lease -> inject -> use for an
authenticated source without leaving the platform (no external site)."""
from __future__ import annotations

import json
import os


def run_auth_smoke(limit: int = 100) -> list[dict]:
    jar_path = os.environ.get("FD_SESSION_JAR_PATH")
    account = os.environ.get("FD_ACCOUNT", "none")
    if not jar_path:
        raise RuntimeError("auth-smoke: no session jar injected (pool not used?)")
    with open(jar_path) as f:
        jar = json.load(f)
    token = (jar.get("auth") or {}).get("userToken", "")
    if not token.startswith("tok-"):
        raise RuntimeError("auth-smoke: injected session lacks the expected token")
    return [{"account": account, "token_head": token[:12],
             "source": "auth-smoke"}][:max(1, limit)]
