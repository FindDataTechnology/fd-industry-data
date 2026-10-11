"""wire-finddatatech-cloud — 平台 wire 健康端点取数单元（demand-orchestration 演练单）。

来源：https://wire.finddatatech.cloud/health —— fd-wire-api 的健康端点，返回单个
JSON 对象（本单实测：{"status": "ok", "service": "fd-wire-api", "version": "0.1.0"}）。

字段口径（brief.expectations 点名 status/service/version）：
- status / service / version：端点原样返回，不改写、不映射、不补默认值；
- url：读取的端点地址（常量，便于下游溯源）；
- scraped_at：取数时刻 UTC ISO8601。

坑位与容错：
- 只用标准库（urllib）：端点返回纯 JSON，无需 scrapling/httpx，既不加新依赖，
  也少一层解析漂移；镜像预装依赖不受影响。
- 网络层：15s 超时 + 至多 3 次尝试（退避 2s/4s），只做容错，不做频次对抗。
- **取数失败绝不伪造行**：端点不可达/返回非 JSON 对象时抛 RuntimeError，交给验证链
  判红并转人工（drill 单可随时关闭，关闭时应显式失败而不是吐假数据）。
- 未写 schedule（静默合入，点亮由人工补写）、未写 site（走默认执行位）。

入口：run_wire_finddatatech_cloud(limit=100) -> list[dict]
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone

SOURCE_URL = "https://wire.finddatatech.cloud/health"
TIMEOUT_SECONDS = 15
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2
USER_AGENT = "finddata-spider/1.0 (+https://finddatatech.cloud)"


def _fetch_health(url: str = SOURCE_URL, attempts: int = MAX_ATTEMPTS) -> dict:
    """取 health 端点 JSON 对象；有限重试后仍失败则抛 RuntimeError（不返回假数据）。"""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(
                url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
            )
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(f"expected JSON object, got {type(payload).__name__}")
            return payload
        except Exception as exc:  # noqa: BLE001 — 收敛为有限重试
            last_error = exc
            if attempt < attempts:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(
        f"health endpoint unreachable after {attempts} attempts: "
        f"{type(last_error).__name__}: {last_error}"
    )


def run_wire_finddatatech_cloud(limit: int = 100) -> list[dict]:
    """Entry point for fd-open-data-protocol dispatch."""
    if limit is not None and int(limit) <= 0:
        return []
    payload = _fetch_health()
    return [{
        "service": str(payload.get("service", "")),
        "status": str(payload.get("status", "")),
        "version": str(payload.get("version", "")),
        "url": SOURCE_URL,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }]
