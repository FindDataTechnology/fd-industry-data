"""edu-catalog FastMCP server — 教育目录查询/订阅面（spec cap2）。

工具面闭集声明处 = TOOL_NAMES（与 registry 在册面/pack allowTools 三方对账锚点，
沿用 infra-telemetry 装配纪律）。

Entry: ``python -m edu_catalog.server``（stdio）
       ``EDU_CATALOG_TRANSPORT=http EDU_CATALOG_PORT=8411 python -m edu_catalog.server``
存储路径走环境（EDU_CATALOG_STORE / EDU_SUBS_STORE），源码零口令。
"""
from __future__ import annotations

import json
import os
import time
from collections import deque
from pathlib import Path

from fastmcp import FastMCP

from .store import CatalogStore, SubscriptionStore
from .tools import register

TOOL_NAMES = frozenset({
    "edu_search", "edu_item_detail", "edu_overview",
    "edu_subscribe", "edu_unsubscribe",
})


def build_server(catalog_store=None, subs_store=None, error_hook=None, read_only=False) -> FastMCP:
    mcp = FastMCP(
        name="edu-catalog",
        instructions=(
            "教育目录查询面：跨论文/播客/课程检索（edu_search）、条目详情"
            "（edu_item_detail）、许可层概况（edu_overview）、订阅盯梢"
            "（edu_subscribe/edu_unsubscribe，唯一写面）。"
            "每条结果带许可层与 as_of；卡片层明示「本目录未取正文」。"
            "空结果（no_match，注明检索域）与通道故障（显式 reason）严格区分。"
        ),
    )
    if catalog_store is None or (subs_store is None and not read_only):
        from .pg_store import stores_from_env  # DSN 在场=PG 权威库，否则文件回落
        catalog_store, subs_store = stores_from_env()
    # 论文实时通道（2026-11-04 晚架构纠偏）：EDU_LIVE_SEARCH=off 显式关（单机/离线形态），
    # 其余装配 OpenAlex facade——key 走 OPENALEX_API_KEY env，故障回落本地目录。
    # read_only 面 MUST 不装实时通道（spec add-education-public-read-tier：公开层只本地目录）。
    live = None
    if not read_only and os.getenv("EDU_LIVE_SEARCH", "on") != "off":
        from .openalex_live import search_openalex

        def live(query: str, limit: int) -> list[dict]:
            return search_openalex(query, limit)
    register(mcp, catalog=lambda: catalog_store, subs=subs_store, error_hook=error_hook,
             live_search=live, read_only=read_only)
    return mcp


def _make_asgi(catalog_store=None, subs_store=None):
    """组两 FastMCP app（只读公开 + 全量鉴权）挂一个 uvicorn，按路径分 auth/CORS。

    catalog_store/subs_store 供测试注入；生产传 None → build_server 走 stores_from_env。

    D1 路径 gate（2026-11-04 实测 StarletteWithLifespan 可组合、两路径 initialize 均 200）：
    - /public/* → 只读 app（3 工具，live=None），匿名放行 + CORS；
    - 其余（/mcp）→ 全量 app（5 工具），仍校验 bearer（在册/网关/pack 现走此路，向后兼容）。
    两子 app 的 lifespan 由父 Starlette 的 combined lifespan 手动进入（stateless 仍需会话
    管理器 task group，否则 initialize 报未初始化——2026-11-04 实测）。
    """
    from contextlib import asynccontextmanager

    from starlette.applications import Starlette
    from starlette.routing import Mount

    full_app = build_server(catalog_store=catalog_store, subs_store=subs_store).http_app(
        transport="streamable-http", path="/mcp")
    read_app = build_server(catalog_store=catalog_store, subs_store=subs_store, read_only=True).http_app(
        transport="streamable-http", path="/mcp")

    @asynccontextmanager
    async def lifespan(_app):
        async with read_app.router.lifespan_context(read_app), \
                   full_app.router.lifespan_context(full_app):
            yield

    parent = Starlette(
        routes=[Mount("/public", app=read_app), Mount("/", app=full_app)],
        lifespan=lifespan,
    )

    token = os.environ.get("MCP_BEARER_TOKEN", "")
    cors_origin = os.environ.get("EDU_PUBLIC_CORS_ORIGIN", "*")
    # 匿名面按 IP 粗限速（#2 5.1，挂账触发条件=官网 /api/mcp 公网接入已成立）：
    # 内存固定窗，超限 429 显式报因 + Retry-After，不静默。表上限 1 万键防内存放大。
    rate_per_min = int(os.environ.get("EDU_PUBLIC_RATE_PER_MIN", "120"))
    rate_table: dict[str, deque] = {}

    def _client_ip(scope) -> str:  # noqa: ANN001
        headers = dict(scope.get("headers") or [])
        xff = headers.get(b"x-forwarded-for", b"").decode()
        return xff.split(",")[0].strip() if xff else (scope.get("client") or ["unknown"])[0]

    def _rate_ok(ip: str, now: float) -> tuple[bool, int]:
        win = rate_table.setdefault(ip, deque())
        while win and now - win[0] >= 60.0:
            win.popleft()
        if len(win) >= rate_per_min:
            return False, max(1, int(60.0 - (now - win[0])) + 1)
        win.append(now)
        if len(rate_table) > 10_000:  # 键数护栏：整体清扫过期项
            for k in [k for k, v in rate_table.items() if not v]:
                rate_table.pop(k, None)
            if len(rate_table) > 10_000:
                rate_table.clear()
        return True, 0

    _CORS = [
        (b"access-control-allow-origin", cors_origin.encode()),
        (b"access-control-allow-methods", b"GET, POST, DELETE, OPTIONS"),
        (b"access-control-allow-headers",
         b"content-type, authorization, mcp-protocol-version, last-event-id, mcp-session-id"),
        (b"access-control-expose-headers", b"mcp-session-id, retry-after"),
        (b"access-control-max-age", b"600"),
    ]
    if cors_origin != "*":
        _CORS.append((b"vary", b"Origin"))

    async def app(scope, receive, send):  # noqa: ANN001 - ASGI dispatcher
        if scope.get("type") == "lifespan":
            await parent(scope, receive, send)
            return
        path = scope.get("path", "")
        if path.startswith("/public"):
            # 匿名只读面：按 IP 限速后放行（不校验 bearer），加 CORS。写工具根本不在此 app 上（D1 结构性红线）。
            if scope.get("method") == "OPTIONS":
                await send({"type": "http.response.start", "status": 204, "headers": list(_CORS)})
                await send({"type": "http.response.body", "body": b""})
                return
            ok, retry_after = _rate_ok(_client_ip(scope), time.monotonic())
            if not ok:
                await _send_429(receive, send, retry_after, rate_per_min)
                return

            async def send_cors(message):  # noqa: ANN001
                if message.get("type") == "http.response.start":
                    message = {**message, "headers": list(message.get("headers") or []) + list(_CORS)}
                await send(message)

            await parent(scope, receive, send_cors)
            return
        # 鉴权面（/mcp）：bearer 校验，行为与既有在册一致。
        if token:
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"").decode()
            if auth != f"Bearer {token}":
                await _send_401(receive, send)
                return
        await parent(scope, receive, send)

    return app


async def _send_401(receive, send):  # noqa: ANN001
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": b"unauthorized"})


async def _send_429(receive, send, retry_after: int, limit: int) -> None:  # noqa: ANN001
    """限速显式报因（#2 5.1：超限不静默），CORS 由 send_cors 包装层照加。"""
    body = json.dumps({
        "status": "rate_limited",
        "reason": f"per-IP limit {limit}/min exceeded; slow down",
        "retry_after_s": retry_after,
    }).encode()
    await send({
        "type": "http.response.start",
        "status": 429,
        "headers": [
            (b"content-type", b"application/json"),
            (b"retry-after", str(retry_after).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})


def main() -> None:
    transport = os.getenv("EDU_CATALOG_TRANSPORT", "stdio")
    if transport == "stdio":
        build_server().run()
        return
    import uvicorn

    uvicorn.run(
        _make_asgi(),
        host=os.environ.get("EDU_CATALOG_HOST", "0.0.0.0"),
        port=int(os.environ.get("EDU_CATALOG_PORT", "8411")),
    )


if __name__ == "__main__":
    main()
