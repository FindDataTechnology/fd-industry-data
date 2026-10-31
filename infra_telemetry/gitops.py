"""① gitops_sync — ArgoCD / In-cluster GitOps 逐 app out-of-sync/degraded 只读状态
（spec: infra-telemetry-surface R2 第①类）。

通道 = ArgoCD REST API 只读 GET ``/api/v2/applications``（io_.http_json 归因路径）：
- base URL：env ``FD_TELEMETRY_ARGOCD_URL``；未设 → ChannelError(channel="argocd",
  reason="not_configured")，绝不发起请求、不猜默认端点。
- token：env ``FD_TELEMETRY_ARGOCD_TOKEN``；在场仅拼 Bearer 请求头（只入请求侧），
  未设不预检——认证失败由 HTTP 401/403 经 io_.http_json 归因 unauthorized。
- 零凭据零端点（R4）：items 只用逻辑名（app 名），返回体不含 URL/host/token；
  自由文本（condition message）出信封前全部过 scrub()。

逐 app 解析（metadata.name / status.conditions / status.sync.status /
status.health.status / status.operationState.phase）：
- 字段缺失 / API 结构不认识 → sync/health 记 "unknown"（小写哨兵），不猜绿。
- anomalous = sync_status != "Synced" 或 health_status ∈ {Degraded, Missing,
  Unknown, unknown}。健康与未知项同样逐条出现，禁只回异常或空集假绿。

summary 计数（机器可数的具名状态口径）：
- apps        条目总数
- out_of_sync sync_status == "OutOfSync"
- degraded    health_status == "Degraded"
- unknown     sync 或 health 为 "Unknown"/"unknown"（含结构缺失归一化）

失败态照 crawl_summary/cost：items=[]、summary 带 note、channels 点名 argocd +
reason + scrub 后 detail、ok=False（R3 禁假绿）。纯 stdlib；register 段仅薄包装。
"""
from __future__ import annotations

import os
from typing import Any, Callable

from .envelope import ChannelError, envelope, scrub, utc_now_iso
from .io_ import http_json, run_cmd_json

CHANNEL = "argocd"
ENDPOINT = "/api/v2/applications"
BASE_URL_ENV = "FD_TELEMETRY_ARGOCD_URL"
TOKEN_ENV = "FD_TELEMETRY_ARGOCD_TOKEN"
MODE_ENV = "FD_TELEMETRY_ARGOCD_MODE"  # rest(default) | kubectl
KUBECTL_ENV = "FD_TELEMETRY_KUBECTL"

UNKNOWN = "unknown"
# ArgoCD 具名状态面（原样透传，不翻译不猜）；unknown 仅指字段缺失的结构未知。
_ANOMALOUS_HEALTH = frozenset({"Degraded", "Missing", "Unknown", UNKNOWN})
_UNKNOWNISH = frozenset({"Unknown", UNKNOWN})


def _status_field(status: dict, *path: str) -> str:
    """status.<a>.<b> 安全取值；任一层缺失/空/非字符串 → "unknown" 哨兵。"""
    node: Any = status
    for key in path:
        if not isinstance(node, dict):
            return UNKNOWN
        node = node.get(key)
    if isinstance(node, str) and node.strip():
        return node.strip()
    return UNKNOWN


def _parse_app(raw: Any, idx: int) -> dict:
    """单 app → 条目；结构不认识只影响本条（记 unknown），不牵连整通道。"""
    app: dict = raw if isinstance(raw, dict) else {}
    meta = app.get("metadata") if isinstance(app.get("metadata"), dict) else {}
    name = meta.get("name")
    name = str(name) if name else f"app-{idx}"

    status = app.get("status") if isinstance(app.get("status"), dict) else None
    if status is None:
        sync_status = health_status = UNKNOWN
        operation_state = None
        conditions: list = []
    else:
        sync_status = _status_field(status, "sync", "status")
        health_status = _status_field(status, "health", "status")
        op_state = status.get("operationState")
        if not isinstance(op_state, dict):
            op_state = None
        phase = op_state.get("phase") if op_state else None
        operation_state = str(phase) if phase else None
        conditions = status.get("conditions") if isinstance(status.get("conditions"), list) else []

    detail_msgs: list[str] = []
    last_transition: str | None = None
    for c in conditions:
        if not isinstance(c, dict):
            continue
        message = c.get("message")
        if message:
            ctype = c.get("type")
            detail_msgs.append(f"{ctype}: {message}" if ctype else str(message))
        ts = c.get("lastTransitionTime")
        if ts and (last_transition is None or str(ts) > last_transition):
            last_transition = str(ts)
    if last_transition is None and isinstance(status, dict):
        op_state = status.get("operationState")
        if isinstance(op_state, dict) and op_state.get("finishedAt"):
            last_transition = str(op_state["finishedAt"])

    return {
        "app": name,
        "sync_status": sync_status,
        "health_status": health_status,
        "operation_state": operation_state,
        "anomalous": sync_status != "Synced" or health_status in _ANOMALOUS_HEALTH,
        "last_transition": last_transition,
        "detail": scrub("; ".join(detail_msgs)) if detail_msgs else None,
    }


def _extract_apps(payload: Any) -> list:
    """GET /api/v2/applications 标准形态：dict 带 items 列表，或直接列表。"""
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return payload["items"]
    if isinstance(payload, list):
        return payload
    raise ChannelError(CHANNEL, "upstream_unreachable", "unrecognized applications response shape")


def _collect_kubectl(*, timeout: int, executor: Callable[..., Any] | None) -> Any:
    """kubectl 模式：读 Application CR（与 REST 同构 status，_parse_app 直接复用）。

    集群内 SA + argocd ns Role(get/list/watch argoproj.io applications)；
    ArgoCD 2.13.2 无离线 API-token 子命令、会话 JWT 24h 过期——常驻遥测
    免造凭据走此路（design D5 修订 2026-10-31）。只读动词由 run_cmd_json 调用点
    与 workloads 白名单同一纪律：仅 get。
    """
    binary = os.environ.get(KUBECTL_ENV, "kubectl")
    argv = [binary, "get", "applications.argoproj.io", "-n", "argocd", "-o", "json"]
    runner = executor or run_cmd_json
    return runner(argv, channel=CHANNEL, timeout=timeout)


def collect(
    *,
    timeout: int = 20,
    base_env: str = BASE_URL_ENV,
    token_env: str = TOKEN_ENV,
    fetcher: Callable[..., Any] | None = None,
    executor: Callable[..., Any] | None = None,
) -> dict:
    """纯函数核心：fetcher/executor 可注入（测试 fake），生产走 io_.http_json/run_cmd_json。"""
    as_of = utc_now_iso()
    errors: dict[str, ChannelError] = {}
    items: list[dict] = []
    mode = os.environ.get(MODE_ENV, "rest").strip().lower() or "rest"
    if mode == "kubectl":
        try:
            payload = _collect_kubectl(timeout=timeout, executor=executor)
            raw_apps = _extract_apps(payload)
        except ChannelError as e:
            errors[e.channel] = e
        else:
            items = [_parse_app(a, i) for i, a in enumerate(raw_apps)]
    else:
        base = os.environ.get(base_env, "").strip()
        if not base:
            # 诚实失败：不猜端点、不发请求。
            errors[CHANNEL] = ChannelError(CHANNEL, "not_configured", f"env {base_env} is unset")
        else:
            url = base.rstrip("/") + ENDPOINT
            headers: dict[str, str] = {}
            token = os.environ.get(token_env, "").strip()
            if token:
                headers["Authorization"] = f"Bearer {token}"  # 只入请求侧，绝不入返回体
            runner = fetcher or http_json
            try:
                payload = runner(url, channel=CHANNEL, headers=headers, timeout=timeout)
                raw_apps = _extract_apps(payload)
            except ChannelError as e:
                errors[e.channel] = e
            else:
                items = [_parse_app(a, i) for i, a in enumerate(raw_apps)]

    if errors:
        counters = {"apps": 0, "out_of_sync": 0, "degraded": 0, "unknown": 0, "note": "channel failed — no data claimed"}
    else:
        counters = {
            "apps": len(items),
            "out_of_sync": sum(1 for i in items if i["sync_status"] == "OutOfSync"),
            "degraded": sum(1 for i in items if i["health_status"] == "Degraded"),
            "unknown": sum(
                1 for i in items
                if i["sync_status"] in _UNKNOWNISH or i["health_status"] in _UNKNOWNISH
            ),
        }
    return envelope(
        "gitops_sync",
        items,
        counters,
        channels=[{"channel": CHANNEL, "status": "ok"}] if not errors else [{"channel": e.channel, "status": "failed", "reason": e.reason, "detail": scrub(e.detail)} for e in errors.values()],
        as_of=as_of,
    )


def register(mcp) -> None:
    @mcp.tool(name="gitops_sync", description="ArgoCD GitOps out-of-sync/degraded 逐 app 状态（只读 REST GET，base=env FD_TELEMETRY_ARGOCD_URL，零凭据零端点）")
    def _tool(timeout: int = 20) -> dict:
        return collect(timeout=timeout)
