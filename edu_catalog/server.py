"""edu-catalog FastMCP server — 教育目录查询/订阅面（spec cap2）。

工具面闭集声明处 = TOOL_NAMES（与 registry 在册面/pack allowTools 三方对账锚点，
沿用 infra-telemetry 装配纪律）。

Entry: ``python -m edu_catalog.server``（stdio）
       ``EDU_CATALOG_TRANSPORT=http EDU_CATALOG_PORT=8411 python -m edu_catalog.server``
存储路径走环境（EDU_CATALOG_STORE / EDU_SUBS_STORE），源码零口令。
"""
from __future__ import annotations

import os
from pathlib import Path

from fastmcp import FastMCP

from .store import CatalogStore, SubscriptionStore
from .tools import register

TOOL_NAMES = frozenset({
    "edu_search", "edu_item_detail", "edu_overview",
    "edu_subscribe", "edu_unsubscribe",
})


def build_server(catalog_store=None, subs_store=None, error_hook=None) -> FastMCP:
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
    if catalog_store is None or subs_store is None:
        from .pg_store import stores_from_env  # DSN 在场=PG 权威库，否则文件回落
        catalog_store, subs_store = stores_from_env()
    # 论文实时通道（2026-11-04 晚架构纠偏）：EDU_LIVE_SEARCH=off 显式关（单机/离线形态），
    # 其余装配 OpenAlex facade——key 走 OPENALEX_API_KEY env，故障回落本地目录。
    live = None
    if os.getenv("EDU_LIVE_SEARCH", "on") != "off":
        from .openalex_live import search_openalex

        def live(query: str, limit: int) -> list[dict]:
            return search_openalex(query, limit)
    register(mcp, catalog=lambda: catalog_store, subs=subs_store, error_hook=error_hook, live_search=live)
    return mcp


def main() -> None:
    transport = os.getenv("EDU_CATALOG_TRANSPORT", "stdio")
    if transport == "stdio":
        build_server().run()
        return
    import uvicorn

    mcp = build_server()
    asgi = mcp.http_app(transport="streamable-http")
    token = os.environ.get("MCP_BEARER_TOKEN", "")

    async def app(scope, receive, send):  # noqa: ANN001 - ASGI shim
        # lifespan 必须直通：FastMCP 的会话管理器在 lifespan 里启动，
        # 拦掉即 initialize 500（infra-telemetry 2026-10-31 实探教训，同模复刻）。
        if scope.get("type") == "lifespan":
            await asgi(scope, receive, send)
            return
        if token:
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"").decode()
            if auth != f"Bearer {token}":
                await _send_401(receive, send)
                return
        await asgi(scope, receive, send)

    async def _send_401(receive, send):  # noqa: ANN001
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"unauthorized"})

    uvicorn.run(
        app,
        host=os.environ.get("EDU_CATALOG_HOST", "0.0.0.0"),
        port=int(os.environ.get("EDU_CATALOG_PORT", "8411")),
    )


if __name__ == "__main__":
    main()
