"""Tencent site dispatch probe: proves claim -> execute -> report
without touching the network. Trigger-only twin of dispatch-smoke."""
import socket, time


def run_tencent_smoke(limit: int = 100) -> list[dict]:
    return [{"host": socket.gethostname(),
             "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
             "source": "tencent-smoke"}][:max(1, limit)]
